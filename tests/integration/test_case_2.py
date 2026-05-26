"""Case-2 ATO fixture — Phase 1 pipeline replay.

The full case-2 scenario (cloud customer → residential proxy burst over
1 hour) requires Phase 6 staging replay against real enrichment data
(MaxMind GeoLite2 + IP2Proxy LITE PX11) to evaluate cumulative behavior
over time. Phase 1's job here is narrower:

1. Verify the pipeline wires correctly: a booking from an unfamiliar IP
   against a customer with established baseline triggers the
   ip_fully_new_for_customer + unfamiliar_ip_country_for_origin signals.
2. Verify the booking endpoint returns a non-stub decision (the 1C.1
   ALLOW 0.0 stub has been replaced by real scoring).
3. Verify a velocity burst (12 bookings from one IP in an hour) trips
   ip_velocity_high_ui on the web channel.

Real-world calibration (FPR measurement, threshold tuning) lands in
Phase 6.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
import structlog
from httpx import AsyncClient

_BOOKING_PATH = "/api/v1/shipments/booking/evaluate"


def _seed_payload(
    request_id: str,
    source_ip: str,
    *,
    channel: str = "web",
    customer_id: str = "case2-cust",
    booking_ts: datetime | None = None,
) -> dict[str, Any]:
    ts = booking_ts or datetime.now(tz=UTC)
    return {
        "request_id": request_id,
        "customer": {"external_id": customer_id},
        "user": {"external_id": "case2-user"},
        "source_ip": source_ip,
        "shipment": {
            "origin": {"address": "100 Bay Street"},
            "destination": {"address": "500 5th Avenue"},
            "value": 250.00,
            "channel": channel,
        },
        "booking_ts": ts.isoformat().replace("+00:00", "Z"),
    }


async def _seed_established_customer(
    db_conn: asyncpg.Connection,
    tenant_id: int,
    external_id: str = "case2-cust",
) -> int:
    """Seed a customer with first_seen 90 days ago + baseline with 20
    cloud-IP observations from a single /24 (typical case-2 victim
    profile)."""
    customer_id = await db_conn.fetchval(
        """
        INSERT INTO customers (tenant_id, external_id, first_seen, total_shipments)
        VALUES ($1, $2, now() - interval '90 days', 20)
        RETURNING id
        """,
        tenant_id,
        external_id,
    )
    await db_conn.execute(
        """
        INSERT INTO customer_baselines (
            tenant_id, customer_id,
            ip_stats, ip_netblock_stats, ip_asn_stats,
            country_stats, origin_ip_country_stats,
            origin_stats, dest_stats, lane_stats,
            ip_type_hist,
            value_n, value_mean, value_m2,
            last_booking_ts, last_booking_country,
            decay_anchor_date
        )
        VALUES (
            $1, $2,
            '{"35.190.0.1": {"n": 20, "r_n": 0, "last": "2026-05-20", "type": "cloud"}}'::jsonb,
            '{"35.190.0.0": {"n": 20, "r_n": 0, "last": "2026-05-20"}}'::jsonb,
            '{"GOOGLE": {"n": 20, "r_n": 0, "last": "2026-05-20"}}'::jsonb,
            '{"US": {"n": 20, "r_n": 0, "last": "2026-05-20"}}'::jsonb,
            '{"100 Bay Street||US": {"n": 20, "r_n": 0, "last": "2026-05-20"}}'::jsonb,
            '{"100 Bay Street": {"n": 20, "r_n": 0, "last": "2026-05-20"}}'::jsonb,
            '{"500 5th Avenue": {"n": 20, "r_n": 0, "last": "2026-05-20"}}'::jsonb,
            '{"100 Bay Street||500 5th Avenue": {"n": 20, "r_n": 0, "last": "2026-05-20"}}'::jsonb,
            '{"cloud": 20}'::jsonb,
            20, 250, 2000,
            now() - interval '6 days',
            'US',
            current_date
        )
        """,
        tenant_id,
        customer_id,
    )
    return customer_id


async def test_unfamiliar_ip_against_established_customer_triggers_signals(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """A booking from a brand-new residential /24 against a customer
    with established cloud-only baseline triggers the ip_fully_new
    signal. The customer_observations >= 10 guard is satisfied
    (value_n = 20 from the seed)."""
    token, tenant_id = seeded_api_token
    await _seed_established_customer(db_conn, tenant_id)

    payload = _seed_payload(request_id="case2-first-residential", source_ip="198.51.100.42")
    with structlog.testing.capture_logs() as captured:
        response = await unauth_client.post(
            _BOOKING_PATH, json=payload, headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200
    body = response.json()

    # ip_fully_new fires because the new IP / /24 / ASN are all absent
    # from baseline.{ip_stats, ip_netblock_stats, ip_asn_stats}.
    assert "ip_fully_new_for_customer" in body["triggered_rules"], (
        f"Expected ip_fully_new_for_customer in triggered_rules; got " f"{body['triggered_rules']}"
    )

    # Real scoring ran — the 1C.1 stub ALLOW 0.0 is gone.
    assert body["score"] > 0.0, "Phase 1 stub ALLOW 0.0 should be replaced by real scoring"

    # Observability sanity: risk.evaluation log emitted with the full
    # Layer 2 + Layer 3 component set tagged metric=True for the Phase 5
    # CloudWatch sink. Locate by event name; the booking endpoint emits
    # exactly one per request.
    risk_events = [e for e in captured if e.get("event") == "risk.evaluation"]
    assert (
        len(risk_events) == 1
    ), f"Expected exactly one risk.evaluation event; got {len(risk_events)}"
    event = risk_events[0]
    assert event["metric"] is True
    assert event["request_id"] == "case2-first-residential"
    # Case-2 customer: age=90d (age_frac=0.5), shipments=20 (ship_frac=0.4)
    # → maturity = 0.20. base_prior = 0.10 * (1 - 0.20) = 0.08 — strict
    # `> 0` catches the Layer 2 short-circuit regression class. The 0.0
    # lower bound on the others is structural (noisy-OR cannot go negative).
    assert event["account_prior"] > 0.0
    assert event["signal_score"] >= 0.0
    assert 0.0 < event["maturity"] < 1.0
    assert event["score"] == pytest.approx(body["score"])


async def test_velocity_burst_from_one_ip_trips_ip_velocity_high_ui(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """12 bookings from one IP within an hour, web channel → the 12th
    booking sees velocity_ip_hourly > 10 and trips ip_velocity_high_ui."""
    token, tenant_id = seeded_api_token
    headers = {"Authorization": f"Bearer {token}"}
    await _seed_established_customer(db_conn, tenant_id, external_id="vel-cust")

    burst_ip = "198.51.100.55"

    # Send 11 bookings; the 12th will see >10 prior in the last hour.
    now = datetime.now(tz=UTC)
    for i in range(11):
        payload = _seed_payload(
            request_id=f"vel-burst-{i}",
            source_ip=burst_ip,
            customer_id="vel-cust",
            booking_ts=now - timedelta(minutes=30 - i),
        )
        r = await unauth_client.post(_BOOKING_PATH, json=payload, headers=headers)
        assert r.status_code == 200

    # 12th booking — now sees > 10 prior from this IP in the last hour.
    payload12 = _seed_payload(request_id="vel-burst-12", source_ip=burst_ip, customer_id="vel-cust")
    response = await unauth_client.post(_BOOKING_PATH, json=payload12, headers=headers)
    assert response.status_code == 200
    assert "ip_velocity_high_ui" in response.json()["triggered_rules"], (
        f"Expected ip_velocity_high_ui in triggered rules after 11-burst "
        f"prelude; got {response.json()['triggered_rules']}"
    )


async def test_clean_baseline_no_rules_fire(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Sanity: a clean booking (no enrichment data → no threat signals;
    new customer → maturity-guarded rules inert) returns ALLOW 0.0
    with no rules firing. Confirms the pipeline doesn't false-positive
    on the simplest case."""
    token, _ = seeded_api_token
    payload = _seed_payload(
        request_id="clean-1", source_ip="198.51.100.100", customer_id="clean-cust"
    )
    response = await unauth_client.post(
        _BOOKING_PATH, json=payload, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "ALLOW"
    # Phase 2: brand-new customer + no Layer 3 rules firing → score equals
    # the base account_prior of 0.10 (MAX_NEW_ACCOUNT). Pipeline doesn't
    # false-positive: triggered_rules empty, decision ALLOW.
    assert body["score"] == pytest.approx(0.10)
    assert body["triggered_rules"] == []
