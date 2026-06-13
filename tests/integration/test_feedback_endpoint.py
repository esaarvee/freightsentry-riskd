"""End-to-end integration tests for POST /api/v1/shipments/feedback.

Covers endpoint contract surfaces — per-POST idempotency, label
monotonicity, baseline + counter writes, 404 on unknown target,
cross-tenant 404, modification-decision feedback. Chain semantics
(feedback → next booking triggers previously-rejected rule) lives in
tests/integration/test_feedback_chain_e2e.py.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from httpx import AsyncClient

from tests.conftest import create_tenant_with_token, seeded_ip_enrichment, set_test_tenant_id

_BOOKING_PATH = "/api/v1/shipments/booking/evaluate"
_FEEDBACK_PATH = "/api/v1/shipments/feedback"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _booking_payload(
    *,
    request_id: str = "book-fb-001",
    customer_external_id: str = "fb-cust-1",
    user_external_id: str = "fb-user-1",
    source_ip: str = "203.0.113.50",
    origin_email: str | None = "alice@example.com",
    origin_phone: str | None = "+15551234567",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": request_id,
        "customer": {"external_id": customer_external_id},
        "user": {"external_id": user_external_id},
        "source_ip": source_ip,
        "shipment": {
            "origin": {"address": "10 Origin Lane"},
            "destination": {"address": "20 Destination Ave"},
            "value": 1000.00,
            "channel": "api",
        },
        "booking_ts": "2026-05-27T08:00:00Z",
    }
    if origin_email or origin_phone:
        contact: dict[str, str] = {}
        if origin_email:
            contact["origin_email"] = origin_email
        if origin_phone:
            contact["origin_phone"] = origin_phone
        payload["contact"] = contact
    return payload


def _feedback_payload(
    *,
    request_id: str,
    target_request_id: str = "book-fb-001",
    label: str = "rejected",
    feedback_ts: str = "2026-05-27T10:00:00Z",
    note: str | None = None,
    operator_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": request_id,
        "target_request_id": target_request_id,
        "label": label,
        "feedback_ts": feedback_ts,
    }
    if note is not None:
        payload["note"] = note
    if operator_id is not None:
        payload["operator_id"] = operator_id
    return payload


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_first_feedback_approved_applies_no_counter_change(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """approved label: applied=True, but no counter increments and no
    baseline r_n writes (approved is a positive signal)."""
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.50", asn_org="Comcast"):
        booking = await unauth_client.post(
            _BOOKING_PATH, json=_booking_payload(), headers=_headers(token)
        )
        assert booking.status_code == 200, booking.text
        # baseline counter pre-state
        flagged_pre = await db_conn.fetchval(
            "SELECT flagged_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "fb-cust-1",
        )

        resp = await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(request_id="fb-app-001", label="approved"),
            headers=_headers(token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {
            "applied": True,
            "previous_label": None,
            "target_request_id": "book-fb-001",
        }

        flagged_post = await db_conn.fetchval(
            "SELECT flagged_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "fb-cust-1",
        )
        assert flagged_post == flagged_pre


async def test_first_feedback_rejected_increments_flagged(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """rejected label: applied=True; customers.flagged_count += 1; baseline
    rejected_email_hmacs / rejected_phone_hmacs / ip_stats.r_n incremented."""
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.51", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(source_ip="203.0.113.51"),
            headers=_headers(token),
        )
        flagged_pre = await db_conn.fetchval(
            "SELECT flagged_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "fb-cust-1",
        )

        resp = await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(request_id="fb-rej-001", label="rejected"),
            headers=_headers(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied"] is True

        flagged_post = await db_conn.fetchval(
            "SELECT flagged_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "fb-cust-1",
        )
        assert flagged_post == flagged_pre + 1

        # Baseline rejected_email_hmacs should have one entry with r_n=1.0
        baseline_row = await db_conn.fetchrow(
            "SELECT rejected_email_hmacs, ip_stats FROM customer_baselines WHERE tenant_id = $1",
            tenant_id,
        )
        import json as _json

        rejected_email = _json.loads(baseline_row["rejected_email_hmacs"])
        assert len(rejected_email) == 1
        entry = next(iter(rejected_email.values()))
        assert entry["r_n"] == 1.0

        ip_stats = _json.loads(baseline_row["ip_stats"])
        assert ip_stats["203.0.113.51"]["r_n"] == 1.0


async def test_first_feedback_fraud_confirmed_increments_both_counters(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.52", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(source_ip="203.0.113.52"),
            headers=_headers(token),
        )
        resp = await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(request_id="fb-fraud-001", label="fraud_confirmed"),
            headers=_headers(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied"] is True

        counts = await db_conn.fetchrow(
            "SELECT flagged_count, fraud_confirmed_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "fb-cust-1",
        )
        assert counts["flagged_count"] == 1
        assert counts["fraud_confirmed_count"] == 1


# ---------------------------------------------------------------------------
# Idempotency tier 1: POST replay
# ---------------------------------------------------------------------------


async def test_replay_same_request_id_returns_prior_no_double_increment(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.53", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(source_ip="203.0.113.53"),
            headers=_headers(token),
        )
        first = await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(request_id="fb-replay-001", label="rejected"),
            headers=_headers(token),
        )
        second = await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(request_id="fb-replay-001", label="rejected"),
            headers=_headers(token),
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["applied"] is True
        assert second.json()["applied"] is False
        assert second.json()["previous_label"] == "rejected"

        # Counter incremented once, not twice
        flagged = await db_conn.fetchval(
            "SELECT flagged_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "fb-cust-1",
        )
        assert flagged == 1


# ---------------------------------------------------------------------------
# Idempotency tier 2: label monotonicity
# ---------------------------------------------------------------------------


async def test_label_upgrade_rejected_to_fraud_confirmed_applies(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Different request_id, same target, stronger label → upgrade applies;
    counter delta only for the NEW signal (fraud_confirmed_count += 1;
    flagged_count unchanged since the prior rejected already incremented)."""
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.54", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(source_ip="203.0.113.54"),
            headers=_headers(token),
        )
        # First: rejected
        await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(request_id="fb-up-001", label="rejected"),
            headers=_headers(token),
        )
        # Upgrade: fraud_confirmed
        upgrade = await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(
                request_id="fb-up-002",
                label="fraud_confirmed",
                feedback_ts="2026-05-27T11:00:00Z",
            ),
            headers=_headers(token),
        )
        assert upgrade.status_code == 200
        assert upgrade.json()["applied"] is True
        assert upgrade.json()["previous_label"] == "rejected"

        counts = await db_conn.fetchrow(
            "SELECT flagged_count, fraud_confirmed_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "fb-cust-1",
        )
        # flagged_count stays at 1 (already counted from rejected);
        # fraud_confirmed_count moves from 0 → 1.
        assert counts["flagged_count"] == 1
        assert counts["fraud_confirmed_count"] == 1


async def test_label_downgrade_blocked_by_monotonicity(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Different request_id, same target, weaker label → applied=False;
    counters unchanged. The audit row IS persisted (operator action
    visibility) but no baseline or counter writes happen."""
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.55", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(source_ip="203.0.113.55"),
            headers=_headers(token),
        )
        await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(request_id="fb-dn-001", label="fraud_confirmed"),
            headers=_headers(token),
        )
        downgrade = await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(
                request_id="fb-dn-002",
                label="rejected",
                feedback_ts="2026-05-27T11:00:00Z",
            ),
            headers=_headers(token),
        )
        assert downgrade.status_code == 200
        assert downgrade.json()["applied"] is False
        assert downgrade.json()["previous_label"] == "fraud_confirmed"

        # Counters unchanged from the fraud_confirmed application
        counts = await db_conn.fetchrow(
            "SELECT flagged_count, fraud_confirmed_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "fb-cust-1",
        )
        assert counts["flagged_count"] == 1
        assert counts["fraud_confirmed_count"] == 1

        # Audit row IS persisted (both feedback rows present)
        audit_count = await db_conn.fetchval(
            "SELECT count(*) FROM feedback WHERE tenant_id = $1 AND target_request_id = $2",
            tenant_id,
            "book-fb-001",
        )
        assert audit_count == 2


# ---------------------------------------------------------------------------
# Negative paths
# ---------------------------------------------------------------------------


async def test_unknown_target_request_id_returns_404(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    token, _ = seeded_api_token
    resp = await unauth_client.post(
        _FEEDBACK_PATH,
        json=_feedback_payload(
            request_id="fb-orphan-001",
            target_request_id="never-booked",
            label="rejected",
        ),
        headers=_headers(token),
    )
    assert resp.status_code == 404
    assert "target_request_id not found" in resp.json()["detail"]


async def test_cross_tenant_target_returns_404(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Tenant B token attempting feedback on Tenant A's request_id → 404
    (invisible, not 403)."""
    token_a, tenant_a = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.56", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(source_ip="203.0.113.56"),
            headers=_headers(token_a),
        )
        async with create_tenant_with_token(db_conn) as (token_b, _tenant_b):
            resp = await unauth_client.post(
                _FEEDBACK_PATH,
                json=_feedback_payload(
                    request_id="fb-cross-001",
                    target_request_id="book-fb-001",  # tenant A's target
                    label="rejected",
                ),
                headers=_headers(token_b),
            )
            assert resp.status_code == 404
    await set_test_tenant_id(db_conn, tenant_a)


async def test_feedback_for_modification_decision(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """A modification's request_id resolves the same way a booking's
    does — feedback can target either. (The decisions SELECT carries
    no request_type filter on this path; both kinds are valid targets.)
    """
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.57", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(source_ip="203.0.113.57"),
            headers=_headers(token),
        )
        mod_resp = await unauth_client.post(
            "/api/v1/shipments/modification/evaluate",
            json={
                "request_id": "mod-fb-001",
                "original_request_id": "book-fb-001",
                "modification_ts": "2026-05-27T09:00:00Z",
                "modification_type": "value",
                "new_value": {"value": 1100},
            },
            headers=_headers(token),
        )
        assert mod_resp.status_code == 200

        # Feedback targeting the MODIFICATION's request_id
        fb_resp = await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(
                request_id="fb-on-mod-001",
                target_request_id="mod-fb-001",
                label="rejected",
            ),
            headers=_headers(token),
        )
        assert fb_resp.status_code == 200
        assert fb_resp.json()["applied"] is True

        # flagged_count incremented as for any rejection
        flagged = await db_conn.fetchval(
            "SELECT flagged_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "fb-cust-1",
        )
        assert flagged == 1
