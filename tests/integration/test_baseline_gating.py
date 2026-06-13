"""Customer baseline accumulation gated on ALLOW band.

Five tests pinning the deferred-observation semantics:

1. non-ALLOW-no-fold: REVIEW or BLOCK booking does NOT update baseline
   (the `decision != "ALLOW"` gate at booking.py covers both bands;
   the test's `decision in ("REVIEW", "BLOCK")` assertion captures
   whichever the customer's signal combination produces).
2. approve-then-fold: REVIEW booking + later `approved` feedback DOES fold.
3. ALLOW-no-double-add: ALLOW booking + later `approved` feedback does
   NOT double-add (the elif's `decision_band != ALLOW` guard).
4. monotonicity-skip: REVIEW booking + `rejected` feedback + later
   `approved` feedback → monotonicity skips the late approved; baseline
   carries r_n=1 from the rejected, n=0 (never folded positively).
5. rejected-on-non-folded (concern #4): REVIEW booking + `rejected`
   feedback → `add_rejected_observation` creates fresh entries with
   r_n=1, n=0 even though those stat-dict keys did NOT exist before
   feedback. Pins the missing-key fresh-entry contract.

All tests seed the booking shape to deliberately produce the target
decision band. Baseline state is read via SQL query post-request to
verify the gating.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import asyncpg
from httpx import AsyncClient

from tests.conftest import seeded_ip_enrichment
from tests.ips import CLEAN_IP, CLEAN_IP_2, CLEAN_IP_3, CLEAN_IP_4

_BOOKING_PATH = "/api/v1/shipments/booking/evaluate"
_FEEDBACK_PATH = "/api/v1/shipments/feedback"


def _booking_payload(
    request_id: str,
    *,
    source_ip: str = CLEAN_IP,
    customer_id: str = "gating-cust",
    channel: str = "web",
    value: float = 250.00,
    booking_ts: datetime | None = None,
) -> dict[str, Any]:
    ts = booking_ts or datetime.now(tz=UTC)
    return {
        "request_id": request_id,
        "customer": {"external_id": customer_id},
        "user": {"external_id": "gating-user"},
        "source_ip": source_ip,
        "shipment": {
            "origin": {"address": "100 Bay Street"},
            "destination": {"address": "500 5th Avenue"},
            "value": value,
            "channel": channel,
        },
        "booking_ts": ts.isoformat().replace("+00:00", "Z"),
    }


def _feedback_payload(
    request_id: str,
    target_request_id: str,
    label: str,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "target_request_id": target_request_id,
        "label": label,
        "feedback_ts": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    }


async def _seed_established_customer_with_tight_baseline(
    db_conn: asyncpg.Connection,
    tenant_id: int,
    external_id: str = "gating-cust",
) -> int:
    """Seed an established customer with a TIGHT baseline: 20 cloud-API
    observations from a single ASN (Google LLC). The gating tests then
    send bookings from a DIFFERENT residential ASN to deliberately trip
    REVIEW/BLOCK band so the deferred-observation semantics can be
    asserted."""
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
            '{"Google LLC": {"n": 20, "r_n": 0, "last": "2026-05-20"}}'::jsonb,
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


async def _load_ip_asn_stats(
    db_conn: asyncpg.Connection, tenant_id: int, customer_db_id: int
) -> dict[str, Any]:
    row = await db_conn.fetchrow(
        "SELECT ip_asn_stats FROM customer_baselines WHERE tenant_id=$1 AND customer_id=$2",
        tenant_id,
        customer_db_id,
    )
    if row is None:
        return {}
    return (
        json.loads(row["ip_asn_stats"])
        if isinstance(row["ip_asn_stats"], str)
        else dict(row["ip_asn_stats"])
    )


async def _load_ip_stats(
    db_conn: asyncpg.Connection, tenant_id: int, customer_db_id: int
) -> dict[str, Any]:
    row = await db_conn.fetchrow(
        "SELECT ip_stats FROM customer_baselines WHERE tenant_id=$1 AND customer_id=$2",
        tenant_id,
        customer_db_id,
    )
    if row is None:
        return {}
    return (
        json.loads(row["ip_stats"]) if isinstance(row["ip_stats"], str) else dict(row["ip_stats"])
    )


async def _load_value_n(db_conn: asyncpg.Connection, tenant_id: int, customer_db_id: int) -> float:
    return float(
        await db_conn.fetchval(
            "SELECT value_n FROM customer_baselines WHERE tenant_id=$1 AND customer_id=$2",
            tenant_id,
            customer_db_id,
        )
    )


# ---------------------------------------------------------------------------
# Test 1: REVIEW booking does NOT update baseline
# ---------------------------------------------------------------------------


async def test_review_band_booking_does_not_fold_to_baseline(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """REVIEW-band booking holds in pending state.
    The baseline's ip_asn_stats / ip_stats / value_n stay at their
    pre-booking values."""
    token, tenant_id = seeded_api_token
    customer_db_id = await _seed_established_customer_with_tight_baseline(db_conn, tenant_id)

    pre_asn = await _load_ip_asn_stats(db_conn, tenant_id, customer_db_id)
    pre_ips = await _load_ip_stats(db_conn, tenant_id, customer_db_id)
    pre_value_n = await _load_value_n(db_conn, tenant_id, customer_db_id)

    payload = _booking_payload(request_id="gating-test1-review", source_ip=CLEAN_IP, channel="api")
    async with seeded_ip_enrichment(
        db_conn,
        CLEAN_IP,
        asn_org="Comcast",
        country="US",
        is_cloud=False,
        is_datacenter=False,
    ):
        response = await unauth_client.post(
            _BOOKING_PATH, json=payload, headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200
    decision = response.json()["decision"]
    # Locked cloud-API customer hitting api+residential triggers many
    # rules; expect REVIEW or BLOCK. Either lands in the gated band.
    assert decision in ("REVIEW", "BLOCK"), f"unexpected decision: {decision}"

    post_asn = await _load_ip_asn_stats(db_conn, tenant_id, customer_db_id)
    post_ips = await _load_ip_stats(db_conn, tenant_id, customer_db_id)
    post_value_n = await _load_value_n(db_conn, tenant_id, customer_db_id)

    # Baseline UNCHANGED — the booking is held in pending state.
    assert post_asn == pre_asn, "REVIEW/BLOCK booking must NOT add to ip_asn_stats"
    assert post_ips == pre_ips, "REVIEW/BLOCK booking must NOT add to ip_stats"
    assert post_value_n == pre_value_n, "REVIEW/BLOCK booking must NOT update value_n"
    assert "Comcast" not in post_asn
    assert CLEAN_IP not in post_ips


# ---------------------------------------------------------------------------
# Test 2: BLOCK band — covered by Test 1's `in ("REVIEW", "BLOCK")` assertion.
# A dedicated BLOCK fixture would need extreme signal compounding; the
# REVIEW path proves the gate works since both bands share the
# `decision != "ALLOW"` branch.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 3: approved feedback on REVIEW/BLOCK booking FOLDS to baseline
# ---------------------------------------------------------------------------


async def test_approved_feedback_on_review_booking_folds_to_baseline(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Operator-confirmed legitimate booking that landed
    in REVIEW band gets folded into the customer baseline at feedback
    time. The new ASN/IP appears in ip_asn_stats/ip_stats post-feedback."""
    token, tenant_id = seeded_api_token
    customer_db_id = await _seed_established_customer_with_tight_baseline(db_conn, tenant_id)

    payload = _booking_payload(
        request_id="gating-test3-review", source_ip=CLEAN_IP_2, channel="api"
    )
    async with seeded_ip_enrichment(
        db_conn,
        CLEAN_IP_2,
        asn_org="Comcast",
        country="US",
        is_cloud=False,
        is_datacenter=False,
    ):
        booking_response = await unauth_client.post(
            _BOOKING_PATH, json=payload, headers={"Authorization": f"Bearer {token}"}
        )
        assert booking_response.status_code == 200
        assert booking_response.json()["decision"] in ("REVIEW", "BLOCK")

        # Pre-feedback baseline check: ASN/IP NOT present.
        pre_asn = await _load_ip_asn_stats(db_conn, tenant_id, customer_db_id)
        assert "Comcast" not in pre_asn

        feedback = _feedback_payload("gating-test3-feedback", "gating-test3-review", "approved")
        feedback_response = await unauth_client.post(
            _FEEDBACK_PATH, json=feedback, headers={"Authorization": f"Bearer {token}"}
        )
        assert feedback_response.status_code == 200
        assert feedback_response.json()["applied"] is True

    # Post-feedback baseline check: ASN/IP ARE present.
    post_asn = await _load_ip_asn_stats(db_conn, tenant_id, customer_db_id)
    post_ips = await _load_ip_stats(db_conn, tenant_id, customer_db_id)
    assert "Comcast" in post_asn, "approve feedback must fold ASN into baseline"
    assert post_asn["Comcast"]["n"] == 1.0
    assert CLEAN_IP_2 in post_ips


# ---------------------------------------------------------------------------
# Test 4: ALLOW booking + later approved feedback does NOT double-add
# ---------------------------------------------------------------------------


async def test_allow_booking_then_approved_feedback_does_not_double_add(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """When the booking already landed in ALLOW band,
    the baseline was folded at booking time. A later `approved`
    feedback against the same booking must NOT re-add (would double-
    count). The new branch's `decision_band != "ALLOW"` guard
    short-circuits the fold."""
    token, tenant_id = seeded_api_token
    # Brand-new customer + clean payload — should land in ALLOW.
    payload = _booking_payload(
        request_id="gating-test4-allow",
        source_ip=CLEAN_IP,
        customer_id="clean-allow-cust",
        channel="web",
    )
    booking_response = await unauth_client.post(
        _BOOKING_PATH, json=payload, headers={"Authorization": f"Bearer {token}"}
    )
    assert booking_response.status_code == 200
    assert booking_response.json()["decision"] == "ALLOW"

    customer_db_id = await db_conn.fetchval(
        "SELECT id FROM customers WHERE tenant_id=$1 AND external_id=$2",
        tenant_id,
        "clean-allow-cust",
    )
    pre_value_n = await _load_value_n(db_conn, tenant_id, customer_db_id)
    # Booking-time fold: value_n must be exactly 1.0 after the ALLOW.
    assert pre_value_n == 1.0

    feedback = _feedback_payload("gating-test4-feedback", "gating-test4-allow", "approved")
    feedback_response = await unauth_client.post(
        _FEEDBACK_PATH, json=feedback, headers={"Authorization": f"Bearer {token}"}
    )
    assert feedback_response.status_code == 200

    post_value_n = await _load_value_n(db_conn, tenant_id, customer_db_id)
    # No double-add: value_n still 1.0, NOT 2.0.
    assert post_value_n == 1.0, "approve feedback on ALLOW booking must NOT re-fold"


# ---------------------------------------------------------------------------
# Test 5: monotonicity-skip — rejected first, then approved attempted later
# ---------------------------------------------------------------------------


async def test_rejected_then_approved_does_not_fold(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Once a booking is rejected, subsequent `approved`
    feedback is NOT label-stronger and the monotonicity gate
    short-circuits before the fold ever runs. The baseline reflects
    the rejected state (r_n=1, n=0 on the relevant stat-dict entries
    from add_rejected_observation), never folded positively."""
    token, tenant_id = seeded_api_token
    customer_db_id = await _seed_established_customer_with_tight_baseline(
        db_conn, tenant_id, external_id="gating-cust-test5"
    )

    payload = _booking_payload(
        request_id="gating-test5-review",
        source_ip=CLEAN_IP_3,
        customer_id="gating-cust-test5",
        channel="api",
    )
    async with seeded_ip_enrichment(
        db_conn,
        CLEAN_IP_3,
        asn_org="Comcast",
        country="US",
        is_cloud=False,
        is_datacenter=False,
    ):
        booking_response = await unauth_client.post(
            _BOOKING_PATH, json=payload, headers={"Authorization": f"Bearer {token}"}
        )
        assert booking_response.status_code == 200

        # First feedback: rejected.
        rejected_feedback = _feedback_payload(
            "gating-test5-rejected", "gating-test5-review", "rejected"
        )
        r1 = await unauth_client.post(
            _FEEDBACK_PATH,
            json=rejected_feedback,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r1.status_code == 200
        assert r1.json()["applied"] is True

        # Second feedback: approved — must be skipped per monotonicity.
        approved_feedback = _feedback_payload(
            "gating-test5-approved", "gating-test5-review", "approved"
        )
        r2 = await unauth_client.post(
            _FEEDBACK_PATH,
            json=approved_feedback,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 200
        assert r2.json()["applied"] is False  # monotonicity skipped

    post_asn = await _load_ip_asn_stats(db_conn, tenant_id, customer_db_id)
    post_ips = await _load_ip_stats(db_conn, tenant_id, customer_db_id)
    post_value_n = await _load_value_n(db_conn, tenant_id, customer_db_id)

    # ip_asn_stats is NOT in _apply_baseline_writes (only ip_stats,
    # origin_stats, dest_stats, email/phone HMACs get r_n bumps).
    # The Comcast ASN should NEVER appear in the baseline because the
    # booking was held (REVIEW/BLOCK; no fold) AND the approve fold
    # was monotonicity-skipped (rejected wins).
    assert "Comcast" not in post_asn, (
        "ASN should never enter baseline — non-ALLOW booking + "
        "monotonicity-skipped approve = no fold"
    )
    # ip_stats does get r_n bumped by the rejected feedback's
    # add_rejected_observation (test 6 covers the fresh-entry shape).
    # Here we pin that the approved feedback (skipped) did NOT bump n.
    assert CLEAN_IP_3 in post_ips, "rejected feedback should have created fresh ip_stats entry"
    assert post_ips[CLEAN_IP_3]["n"] == 0.0, (
        "approved was monotonicity-skipped; n must NOT have been bumped"
    )
    assert post_ips[CLEAN_IP_3]["r_n"] == 1.0, "rejected feedback should leave r_n=1"
    # value_n unchanged from the seeded baseline (20).
    assert post_value_n == 20.0, "approved was skipped; value_n unchanged"


# ---------------------------------------------------------------------------
# Test 6: rejected feedback on non-folded booking creates fresh entries
# ---------------------------------------------------------------------------


async def test_rejected_on_non_folded_booking_creates_fresh_entries(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """When `rejected` feedback comes in for a
    booking whose baseline state is empty (gated REVIEW/BLOCK at
    booking time), `add_rejected_observation` correctly creates fresh
    entries with r_n=1, n=0. Pins the missing-key fresh-entry contract."""
    token, tenant_id = seeded_api_token
    customer_db_id = await _seed_established_customer_with_tight_baseline(
        db_conn, tenant_id, external_id="gating-cust-test6"
    )

    payload = _booking_payload(
        request_id="gating-test6-review",
        source_ip=CLEAN_IP_4,
        customer_id="gating-cust-test6",
        channel="api",
    )
    async with seeded_ip_enrichment(
        db_conn,
        CLEAN_IP_4,
        asn_org="Verizon",
        country="US",
        is_cloud=False,
        is_datacenter=False,
    ):
        booking_response = await unauth_client.post(
            _BOOKING_PATH, json=payload, headers={"Authorization": f"Bearer {token}"}
        )
        assert booking_response.status_code == 200
        assert booking_response.json()["decision"] in ("REVIEW", "BLOCK")

        # Pre-feedback: the IP is NOT in ip_stats (gated booking).
        pre_ips = await _load_ip_stats(db_conn, tenant_id, customer_db_id)
        assert CLEAN_IP_4 not in pre_ips

        rejected_feedback = _feedback_payload(
            "gating-test6-rejected", "gating-test6-review", "rejected"
        )
        r = await unauth_client.post(
            _FEEDBACK_PATH,
            json=rejected_feedback,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["applied"] is True

    # Post-feedback: fresh entry created with r_n=1, n=0.
    post_ips = await _load_ip_stats(db_conn, tenant_id, customer_db_id)
    assert CLEAN_IP_4 in post_ips, (
        "add_rejected_observation must create a fresh entry even when "
        "the key was missing pre-feedback"
    )
    entry = post_ips[CLEAN_IP_4]
    assert entry["r_n"] == 1.0, "rejected feedback must set r_n=1 on fresh entry"
    assert entry["n"] == 0.0, "fresh-entry n must be 0.0 (never folded as positive)"
