"""Case-1 dashboard ATO integration test.

Replays the case_1_dashboard_ato.json fixture: an established cloud-IP
+ web-channel customer (38 prior Cloudflare bookings) is taken over and
the attacker bursts 30 web-channel bookings from a single VPN IP in the
185.220.101.0/24 range over ~60 minutes (single-IP burst is the more
realistic ATO pattern — attackers don't rotate per booking).

The assertions use band-level tolerances — we
do NOT pin a specific shipment index for each band transition because:
(a) the score progression depends on noisy-OR composition of many
rules whose weights are calibration targets, and
(b) the plan explicitly forbids weight tuning to make this pass.

Assertions:
- ip_fully_new_for_customer fires from the first burst shipment
  (the IP / /24 / ASN are all absent from the seeded baseline)
- ip_velocity_high_ui fires by ~shipment 11 (single-IP web-channel
  burst crosses >10/hour)
- At least one BLOCK occurs by the end of the 30-shipment burst
- Compound-evidence guard: when BLOCK fires, the triggered_rules list
  must contain at least 2 distinct rules — catches a future regression
  where a single rule's weight inflates past the BLOCK threshold
  alone (e.g., someone accidentally promotes a Layer-3 rule's weight
  to >= 0.80)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
from httpx import AsyncClient

from tests.conftest import seed_customer_with_baseline

_BOOKING_PATH = "/api/v1/shipments/booking/evaluate"
_FIXTURE = Path(__file__).parent / "fixtures" / "case_1_dashboard_ato.json"


def _load_fixture() -> dict[str, Any]:
    with _FIXTURE.open() as f:
        return json.load(f)


async def _seed_case_1_customer(
    db_conn: asyncpg.Connection, tenant_id: int, fixture: dict[str, Any]
) -> int:
    """Seed the case-1 customer + baseline via the shared conftest helper.
    Translates the fixture's relative timestamps (hours_ago) into the
    absolute datetimes the seed helper expects."""
    cust = fixture["customer"]
    seed = dict(fixture["seed_baseline"])  # copy — we mutate the ts field
    seed["last_booking_ts"] = datetime.now(tz=UTC) - timedelta(
        hours=seed.pop("last_booking_ts_hours_ago")
    )
    return await seed_customer_with_baseline(
        db_conn,
        tenant_id,
        external_id=cust["external_id"],
        first_seen_days_ago=cust["first_seen_days_ago"],
        total_shipments=cust["total_shipments"],
        baseline_kwargs=seed,
    )


async def _seed_vpn_enrichment(db_conn: asyncpg.Connection, ips: list[str]) -> None:
    """Seed ip_enrichment with the burst IPs marked is_vpn=True. The
    booking endpoint's Enricher reads this cache before falling back to
    the source files; with this seed, every burst IP is identified as
    a VPN exit from Germany via the Tor Project ASN.

    ip_enrichment is intentionally global (no RLS) per the schema
    comment in 0001_initial.py — cleanup is the caller's job via
    _cleanup_vpn_enrichment in the test's try/finally.
    """
    for ip in ips:
        await db_conn.execute(
            """
            INSERT INTO ip_enrichment (
                ip, country, asn_org, is_vpn, fh_level2, is_proxy, is_cloud,
                lat, lon
            )
            VALUES ($1::inet, 'DE', 'Tor Project', true, true, true, false,
                    52.52, 13.41)
            ON CONFLICT (ip) DO UPDATE SET
                is_vpn = EXCLUDED.is_vpn,
                fh_level2 = EXCLUDED.fh_level2,
                country = EXCLUDED.country,
                asn_org = EXCLUDED.asn_org,
                is_proxy = EXCLUDED.is_proxy,
                lat = EXCLUDED.lat,
                lon = EXCLUDED.lon,
                updated_at = now()
            """,
            ip,
        )


async def _cleanup_vpn_enrichment(db_conn: asyncpg.Connection, ips: list[str]) -> None:
    """Tear down the rows seeded by _seed_vpn_enrichment. ip_enrichment
    is global and not in conftest's _TENANT_SCOPED_TABLES cleanup, so
    seeded rows would otherwise leak across test sessions and pollute
    any future test that enriches an IP in 185.220.101.0/24."""
    for ip in ips:
        await db_conn.execute("DELETE FROM ip_enrichment WHERE ip = $1::inet", ip)


def _build_burst_payload(fixture: dict[str, Any], index: int, start_ts: datetime) -> dict[str, Any]:
    interval = fixture["burst_interval_seconds"]
    booking_ts = start_ts + timedelta(seconds=interval * index)
    ip = fixture["burst_ips"][index % len(fixture["burst_ips"])]
    value = fixture["burst_values_progression"][index]
    return {
        "request_id": f"case1-burst-{index:03d}",
        "shipment_id": f"ship-case1-burst-{index:03d}",
        "transaction_number": f"txn-case1-burst-{index:03d}",
        "customer": {"external_id": fixture["customer"]["external_id"]},
        "user": {"external_id": "case1-user-ato"},
        "source_ip": ip,
        "shipment": {
            "origin": {"address": "100 Bay Street"},
            "destination": {"address": "500 5th Avenue"},
            "value": value,
            "channel": "web",
        },
        "booking_ts": booking_ts.isoformat().replace("+00:00", "Z"),
    }


async def test_case_1_dashboard_ato_progression(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Replay the case-1 burst and assert (a) the new-IP signal fires
    from shipment 1, (b) the web-velocity signal fires by ~shipment 11,
    (c) at least one BLOCK occurs by the end of the burst, (d) when
    BLOCK fires, the triggered_rules list contains compound evidence
    (>=2 rules) — guards against a single-rule-cliff regression."""
    token, tenant_id = seeded_api_token
    headers = {"Authorization": f"Bearer {token}"}
    fixture = _load_fixture()
    burst_ips: list[str] = fixture["burst_ips"]

    await _seed_case_1_customer(db_conn, tenant_id, fixture)
    await _seed_vpn_enrichment(db_conn, burst_ips)

    try:
        burst_size: int = fixture["burst_size"]
        start_ts = datetime.now(tz=UTC)
        scores: list[float] = []
        decisions: list[str] = []
        triggered_per_shipment: list[list[str]] = []

        for i in range(burst_size):
            payload = _build_burst_payload(fixture, i, start_ts)
            resp = await unauth_client.post(_BOOKING_PATH, json=payload, headers=headers)
            assert resp.status_code == 200, f"shipment {i} returned {resp.status_code}: {resp.text}"
            body = resp.json()
            scores.append(body["score"])
            decisions.append(body["decision"])
            triggered_per_shipment.append(body["triggered_rules"])

        # (a) ip_fully_new_for_customer fires from shipment 0 — the burst
        # IPs / /24s / ASN are absent from the seeded baseline.
        assert "ip_fully_new_for_customer" in triggered_per_shipment[0], (
            f"shipment 0 did not fire ip_fully_new_for_customer; "
            f"triggered_rules={triggered_per_shipment[0]}"
        )

        # (b) Single-IP web-channel burst > 10/hour trips ip_velocity_high_ui.
        # With 2-min spacing and single-IP source, shipment 11 sees 11 prior
        # in the last hour — strict > 10 fires from shipment 11 onward.
        velocity_idx = next(
            (i for i, t in enumerate(triggered_per_shipment) if "ip_velocity_high_ui" in t),
            None,
        )
        assert velocity_idx is not None, (
            "ip_velocity_high_ui never fired across the 30-shipment burst — "
            "single-IP web velocity detection appears broken"
        )
        assert velocity_idx <= 14, (
            f"ip_velocity_high_ui fired only at shipment {velocity_idx}; "
            f"expected by ~shipment 11 given the >10/hour threshold and "
            f"2-min single-IP spacing"
        )

        # (c) End-of-burst: at least one BLOCK should have occurred.
        assert any(s >= 0.80 for s in scores), (
            f"case-1 burst never crossed BLOCK band (>= 0.80). Max score "
            f"was {max(scores):.3f}. Decisions: {decisions}. This is a "
            f"calibration signal, not a code bug — surface to operator "
            f"per the bootstrap 'no weight tuning in Phase 2' rule."
        )

        # (d) Compound-evidence guard: every BLOCK decision must be backed
        # by >= 2 distinct rules. Catches a future regression where a
        # single rule's weight inflates past the 0.80 BLOCK threshold
        # alone (e.g., Layer 3 weight accidentally bumped to 0.85).
        # Holds at shipment 0 too, unlike the original index-ordering
        # check which silently vacated when first-BLOCK was at index 0.
        block_indices = [i for i, d in enumerate(decisions) if d == "BLOCK"]
        assert block_indices, "no BLOCK fired — but assertion (c) should have caught this"
        for i in block_indices:
            rules = triggered_per_shipment[i]
            assert len(rules) >= 2, (
                f"shipment {i} reached BLOCK on only {len(rules)} rule(s): "
                f"{rules}. A single rule producing BLOCK is a weight-cliff "
                f"regression — Layer 3 rules should be sub-BLOCK individually "
                f"and only compose to BLOCK via noisy-OR of multiple signals."
            )
    finally:
        await _cleanup_vpn_enrichment(db_conn, burst_ips)
