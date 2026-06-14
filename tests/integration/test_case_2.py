"""Case-2 ATO integration tests.

Three tests covering progressively stronger assertions:

1. test_unfamiliar_ip_against_established_customer_blocks_under_layer2
   — canonical success criterion. API booking from an
   unfamiliar residential IP against a cloud-API-locked customer
   crosses BLOCK end-to-end. Asserts the full 6-rule compound
   (ip_fully_new + unfamiliar_country + lock-in pair + api-non-cloud
   pair) fires — protects against a future regression where one
   compound rule silently breaks but the remaining rules push score
   past BLOCK anyway.
2. test_velocity_burst_from_one_ip_trips_ip_velocity_high_ui —
   velocity-burst smoke (12 bookings/hour/IP, web channel).
3. test_clean_baseline_no_rules_fire — false-positive check
   (brand-new customer + clean payload → no rules, ALLOW).

Real-world calibration (FPR measurement, threshold tuning) is deferred
work. Per .ai/decisions.md and the bootstrap no-weight-tuning rule,
BLOCK failures on case-2 escalate to operator via .claude/STATUS.md —
not to a weight tune.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
import structlog
from httpx import AsyncClient

from tests.conftest import seeded_ip_enrichment
from tests.ips import CLEAN_IP

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
        "shipment_id": f"ship-{request_id}",
        "transaction_number": f"txn-{request_id}",
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
    cloud-IP API-channel observations from a single /24 (typical case-2
    victim profile: an established API-integrating customer that posts
    exclusively from cloud infrastructure).

    channel_hist seeded with 20 api observations
    so api_share=1.0 — required so the
    customer_locked_cloud_api gate fires on a customer who SHOULD be locked.
    """
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
            ip_type_hist, channel_hist,
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
            '{"api": 20}'::jsonb,
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


async def test_unfamiliar_ip_against_established_customer_blocks_under_layer2(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """The canonical case-2 ATO detection test. An API
    booking from an unfamiliar residential /24 against a customer with
    an established cloud-API baseline crosses BLOCK end-to-end under
    Layer 2 + lock-in rules. The score>=0.80 assertion
    is the canonical success criterion.

    The seeded baseline (20 cloud + 20 API observations, value_n=20)
    satisfies the customer_locked_cloud_api gate (cloud_share=1.0 +
    api_share=1.0 + value_n>=20). The booking uses channel=api from
    a residential IP, so the 6-rule compound below all fires. The
    test asserts each one explicitly — protects against a future
    regression where one rule silently breaks but the remaining rules
    push score past BLOCK anyway.

    Per .ai/decisions.md's no-weight-tuning rule, if this test
    fails the response is operator escalation via .claude/STATUS.md —
    NOT a weight tune to force the pass.
    """
    token, tenant_id = seeded_api_token
    await _seed_established_customer(db_conn, tenant_id)

    payload = _seed_payload(
        request_id="case2-block-residential",
        source_ip="198.51.100.42",
        channel="api",
    )
    # api_booking_from_unfamiliar_asn requires a non-None
    # asn_org from enrichment. Seed Comcast (residential) so the rule
    # can compare against the customer's GOOGLE-only baseline (set
    # by _seed_established_customer above). Without this seed,
    # enrichment returns asn_org=None and the new rule's defensive
    # guard returns False, breaking case-2 detection in tests.
    #
    # country="RU" preserves unfamiliar_ip_country_for_origin firing:
    # baseline carries "100 Bay Street||US"; the attack IP from a
    # different country produces a novel origin/IP-country pair.
    async with seeded_ip_enrichment(
        db_conn,
        "198.51.100.42",
        asn_org="Comcast",
        country="RU",
        is_cloud=False,
        is_datacenter=False,
    ):
        with structlog.testing.capture_logs() as captured:
            response = await unauth_client.post(
                _BOOKING_PATH, json=payload, headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 200
        body = response.json()

    # The canonical success criterion: case-2 reaches BLOCK.
    assert body["decision"] == "BLOCK", (
        f"case-2 expected BLOCK with Layer 2 + Phase 2C rules active; "
        f"got {body['decision']} at score {body['score']:.3f}. "
        f"triggered_rules={body['triggered_rules']}"
    )
    assert body["score"] >= 0.80, (
        f"case-2 score {body['score']:.3f} below the BLOCK band (>= 0.80). "
        f"This is a calibration signal, not a code bug — surface to operator "
        f"per the bootstrap 'no weight tuning in Phase 2' rule."
    )

    # 5-rule compound check (api_non_cloud_ip +
    # non_cloud_established_account replaced by single
    # api_booking_from_unfamiliar_asn rule). Each rule pins a specific
    # failure mode. If any silently stops firing, the remaining rules
    # can still push score past BLOCK; this assertion locks the
    # compound itself, not just the outcome.
    expected_rules = {
        # maturity-gated familiarity rules (obs >= 10).
        "ip_fully_new_for_customer",  # new IP/netblock/ASN, m_s
        "unfamiliar_ip_country_for_origin",  # origin paired with new country
        # lock-in rules — pin the customer_locked_cloud_api gate.
        "cloud_api_customer_deviation_iptype",  # 5-clause locked detector
        "locked_customer_unfamiliar_ip",  # 4-clause locked + new IP
        # case-2 learning-based detection. Replaces deleted
        # api_non_cloud_ip + non_cloud_established_account.
        "api_booking_from_unfamiliar_asn",
    }
    actual_rules = set(body["triggered_rules"])
    missing = expected_rules - actual_rules
    assert not missing, (
        f"case-2 5-rule compound is incomplete; missing: {missing}. "
        f"Each missing rule indicates a regression in a specific Phase 2C "
        f"or 7C derivation. triggered_rules={body['triggered_rules']}"
    )
    # Pin: the deleted rules must not appear (catches
    # accidental rule-name revival).
    assert "api_non_cloud_ip" not in actual_rules
    assert "non_cloud_established_account" not in actual_rules

    # Observability sanity check.
    risk_events = [e for e in captured if e.get("event") == "risk.evaluation"]
    assert len(risk_events) == 1
    event = risk_events[0]
    assert event["metric"] is True
    assert event["request_id"] == "case2-block-residential"
    assert event["account_prior"] > 0.0
    assert event["signal_score"] >= 0.80  # Layer 3 alone reaches BLOCK
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

    burst_ip = CLEAN_IP

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
    payload = _seed_payload(request_id="clean-1", source_ip=CLEAN_IP, customer_id="clean-cust")
    response = await unauth_client.post(
        _BOOKING_PATH, json=payload, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "ALLOW"
    # Brand-new customer + no Layer 3 rules firing → score equals
    # the base account_prior of 0.10 (MAX_NEW_ACCOUNT). Pipeline doesn't
    # false-positive: triggered_rules empty, decision ALLOW.
    assert body["score"] == pytest.approx(0.10)
    assert body["triggered_rules"] == []
