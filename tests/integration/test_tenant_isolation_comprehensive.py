"""Comprehensive cross-tenant integration test sweep.

Verifies app-layer tenant scoping at every endpoint. The runtime app
connects as the postgres superuser (RLS dormant per .claude/STATUS.md),
so this file exercises the explicit WHERE tenant_id = $N filters that
are the ACTIVE isolation today. The non-superuser-role test
(test_rls_enforcement_under_riskd_app.py) exercises RLS enforcement
under the runtime role.

Scenarios are organized by endpoint and dimension. Each test seeds two
tenants via create_tenant_with_token, exercises a cross-tenant query
shape from tenant B against tenant A's data, and asserts isolation
(404 / empty result / unaffected counter / unmodified baseline).
"""

from __future__ import annotations

from typing import Any

import asyncpg
from httpx import AsyncClient

from tests.conftest import (
    create_tenant_with_token,
    seeded_ip_enrichment,
    set_test_tenant_id,
    with_test_tenant_context,
)

_BOOKING_PATH = "/api/v1/shipments/booking/evaluate"
_MOD_PATH = "/api/v1/shipments/modification/evaluate"
_FB_PATH = "/api/v1/shipments/feedback"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _booking_payload(
    *,
    request_id: str,
    customer_external_id: str = "iso-cust",
    user_external_id: str = "iso-user",
    source_ip: str = "203.0.113.80",
    booking_ts: str = "2026-05-27T08:00:00Z",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "shipment_id": f"ship-{request_id}",
        "transaction_number": f"txn-{request_id}",
        "customer": {"external_id": customer_external_id},
        "user": {"external_id": user_external_id},
        "source_ip": source_ip,
        "shipment": {
            "origin": {"address": "10 Origin Lane"},
            "destination": {"address": "20 Destination Ave"},
            "value": 1000.00,
            "channel": "api",
        },
        "booking_ts": booking_ts,
        "contact": {"origin_email": "iso@example.com"},
    }


# ---------------------------------------------------------------------------
# Booking endpoint — tenant scoping
# ---------------------------------------------------------------------------


async def test_booking_request_id_namespace_is_per_tenant(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Two tenants can both use request_id='shared-id' independently —
    UNIQUE(tenant_id, request_id) namespaces them per tenant."""
    token_a, tenant_a = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.80", asn_org="Comcast"):
        a_resp = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="shared-id"),
            headers=_headers(token_a),
        )
        assert a_resp.status_code == 200

        async with create_tenant_with_token(db_conn) as (token_b, tenant_b):
            b_resp = await unauth_client.post(
                _BOOKING_PATH,
                json=_booking_payload(
                    request_id="shared-id",  # same id, different tenant
                    customer_external_id="tenant-b-cust",
                    user_external_id="tenant-b-user",
                ),
                headers=_headers(token_b),
            )
            assert b_resp.status_code == 200, b_resp.text

            # Each tenant has exactly 1 decision; cross-tenant counts match
            async with with_test_tenant_context(db_conn, tenant_a):
                a_count = await db_conn.fetchval(
                    "SELECT count(*) FROM decisions WHERE tenant_id = $1", tenant_a
                )
            async with with_test_tenant_context(db_conn, tenant_b):
                b_count = await db_conn.fetchval(
                    "SELECT count(*) FROM decisions WHERE tenant_id = $1", tenant_b
                )
            assert a_count == 1
            assert b_count == 1
    # Restore tenant_a context so seeded_tenant teardown can cascade-delete.
    await set_test_tenant_id(db_conn, tenant_a)


async def test_booking_customer_external_id_namespace_is_per_tenant(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Same customer external_id in two tenants creates two distinct
    customer rows; neither sees the other's first_seen / total_shipments."""
    token_a, tenant_a = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.81", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="ten-a-book",
                customer_external_id="shared-cust",
                source_ip="203.0.113.81",
            ),
            headers=_headers(token_a),
        )

        async with create_tenant_with_token(db_conn) as (token_b, tenant_b):
            await unauth_client.post(
                _BOOKING_PATH,
                json=_booking_payload(
                    request_id="ten-b-book",
                    customer_external_id="shared-cust",  # same id, different tenant
                    source_ip="203.0.113.81",
                ),
                headers=_headers(token_b),
            )

            # Each customer has total_shipments == 1, not 2
            async with with_test_tenant_context(db_conn, tenant_a):
                a_total = await db_conn.fetchval(
                    "SELECT total_shipments FROM customers WHERE tenant_id = $1 AND external_id = $2",
                    tenant_a,
                    "shared-cust",
                )
                a_count = await db_conn.fetchval(
                    "SELECT count(*) FROM customers WHERE external_id = 'shared-cust'"
                )
            async with with_test_tenant_context(db_conn, tenant_b):
                b_total = await db_conn.fetchval(
                    "SELECT total_shipments FROM customers WHERE tenant_id = $1 AND external_id = $2",
                    tenant_b,
                    "shared-cust",
                )
                b_count = await db_conn.fetchval(
                    "SELECT count(*) FROM customers WHERE external_id = 'shared-cust'"
                )
            # Each tenant sees exactly 1 customer with that external_id under RLS
            assert a_count == 1
            assert b_count == 1
            assert a_total == 1
            assert b_total == 1
    await set_test_tenant_id(db_conn, tenant_a)


# ---------------------------------------------------------------------------
# Modification endpoint — tenant scoping
# ---------------------------------------------------------------------------


async def test_modification_cross_tenant_original_returns_404(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Tenant B token attempting to modify Tenant A's booking → 404
    (the WHERE tenant_id filter scopes the lookup; invisible to B)."""
    token_a, tenant_a = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.82", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="ten-a-orig", source_ip="203.0.113.82"),
            headers=_headers(token_a),
        )
        async with create_tenant_with_token(db_conn) as (token_b, _tenant_b):
            resp = await unauth_client.post(
                _MOD_PATH,
                json={
                    "request_id": "ten-b-mod",
                    "original_request_id": "ten-a-orig",  # tenant A's id
                    "shipment_id": "ship-ten-a-orig",
                    "transaction_number": "txn-ten-a-orig",
                    "modification_ts": "2026-05-27T08:30:00Z",
                    "modification_type": "value",
                    "new_value": {"value": 1500},
                },
                headers=_headers(token_b),
            )
            assert resp.status_code == 404
    await set_test_tenant_id(db_conn, tenant_a)


async def test_modification_velocity_isolated_by_tenant(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Tenant B modifications do NOT count toward tenant A's
    modification_velocity_1h. count_user_modifications_1h has explicit
    `d.tenant_id = $1 AND s.tenant_id = $1` (dual filter)."""
    token_a, tenant_a = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.83", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="ten-a-mvbook", source_ip="203.0.113.83"),
            headers=_headers(token_a),
        )

        async with create_tenant_with_token(db_conn) as (token_b, _tenant_b):
            await unauth_client.post(
                _BOOKING_PATH,
                json=_booking_payload(
                    request_id="ten-b-mvbook",
                    customer_external_id="tenant-b-cust",
                    user_external_id="tenant-b-user",
                    source_ip="203.0.113.83",
                ),
                headers=_headers(token_b),
            )
            # 5 modifications by tenant B
            for i in range(5):
                await unauth_client.post(
                    _MOD_PATH,
                    json={
                        "request_id": f"ten-b-mod-{i}",
                        "original_request_id": "ten-b-mvbook",
                        "shipment_id": "ship-ten-b-mvbook",
                        "transaction_number": "txn-ten-b-mvbook",
                        "modification_ts": f"2026-05-27T08:{10 + i}:00Z",
                        "modification_type": "value",
                        "new_value": {"value": 1010 + i},
                    },
                    headers=_headers(token_b),
                )

            # Tenant A's first modification sees velocity=0 (not 5)
            a_mod = await unauth_client.post(
                _MOD_PATH,
                json={
                    "request_id": "ten-a-mod-1",
                    "original_request_id": "ten-a-mvbook",
                    "shipment_id": "ship-ten-a-mvbook",
                    "transaction_number": "txn-ten-a-mvbook",
                    "modification_ts": "2026-05-27T08:30:00Z",
                    "modification_type": "value",
                    "new_value": {"value": 1020},
                },
                headers=_headers(token_a),
            )
            assert a_mod.status_code == 200
            triggered = set(a_mod.json()["triggered_rules"])
            assert "modification_high_velocity_1h" not in triggered, triggered

            # Confirm tenant A's modification count is still 1
            async with with_test_tenant_context(db_conn, tenant_a):
                a_mod_count = await db_conn.fetchval(
                    "SELECT count(*) FROM decisions WHERE tenant_id = $1 AND request_type = 'modification'",
                    tenant_a,
                )
            assert a_mod_count == 1
    await set_test_tenant_id(db_conn, tenant_a)


# ---------------------------------------------------------------------------
# Feedback endpoint — tenant scoping
# ---------------------------------------------------------------------------


async def test_feedback_cross_tenant_target_returns_404(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Tenant B token attempting feedback on Tenant A's target → 404."""
    token_a, tenant_a = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.84", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="ten-a-fbtarget", source_ip="203.0.113.84"),
            headers=_headers(token_a),
        )
        async with create_tenant_with_token(db_conn) as (token_b, _tenant_b):
            resp = await unauth_client.post(
                _FB_PATH,
                json={
                    "request_id": "ten-b-fb",
                    "target_request_id": "ten-a-fbtarget",
                    "label": "rejected",
                    "feedback_ts": "2026-05-27T09:00:00Z",
                },
                headers=_headers(token_b),
            )
            assert resp.status_code == 404
    await set_test_tenant_id(db_conn, tenant_a)


async def test_feedback_does_not_mutate_other_tenants_customer_counter(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """When tenant A rejects a target, tenant B's customers (even with
    matching external_id) MUST NOT have their flagged_count modified."""
    token_a, tenant_a = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.85", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="ten-a-ctr-book", source_ip="203.0.113.85"),
            headers=_headers(token_a),
        )

        async with create_tenant_with_token(db_conn) as (token_b, tenant_b):
            # Tenant B has a customer with same external_id
            await unauth_client.post(
                _BOOKING_PATH,
                json=_booking_payload(
                    request_id="ten-b-ctr-book",
                    customer_external_id="iso-cust",  # same as tenant A
                    source_ip="203.0.113.85",
                ),
                headers=_headers(token_b),
            )

            # Tenant A rejects its own booking
            await unauth_client.post(
                _FB_PATH,
                json={
                    "request_id": "ten-a-ctr-fb",
                    "target_request_id": "ten-a-ctr-book",
                    "label": "rejected",
                    "feedback_ts": "2026-05-27T09:00:00Z",
                },
                headers=_headers(token_a),
            )

            # Tenant A's customer flagged_count = 1; Tenant B's same-name customer = 0
            async with with_test_tenant_context(db_conn, tenant_a):
                a_flag = await db_conn.fetchval(
                    "SELECT flagged_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
                    tenant_a,
                    "iso-cust",
                )
            async with with_test_tenant_context(db_conn, tenant_b):
                b_flag = await db_conn.fetchval(
                    "SELECT flagged_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
                    tenant_b,
                    "iso-cust",
                )
            assert a_flag == 1
            assert b_flag == 0
    await set_test_tenant_id(db_conn, tenant_a)


async def test_feedback_monotonicity_isolated_by_tenant(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Tenant B's prior feedback labels do NOT influence tenant A's
    monotonicity gate for the same nominal target_request_id. Two
    feedbacks with same external request_id namespace in different
    tenants are independent."""
    token_a, tenant_a = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.86", asn_org="Comcast"):
        # Tenant A booking
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="shared-fb-target", source_ip="203.0.113.86"),
            headers=_headers(token_a),
        )

        async with create_tenant_with_token(db_conn) as (token_b, _tenant_b):
            # Tenant B booking with same request_id (per-tenant namespace)
            await unauth_client.post(
                _BOOKING_PATH,
                json=_booking_payload(
                    request_id="shared-fb-target",
                    customer_external_id="tenant-b-cust",
                    source_ip="203.0.113.86",
                ),
                headers=_headers(token_b),
            )
            # Tenant B applies fraud_confirmed
            b_fb = await unauth_client.post(
                _FB_PATH,
                json={
                    "request_id": "tenant-b-fraud-fb",
                    "target_request_id": "shared-fb-target",
                    "label": "fraud_confirmed",
                    "feedback_ts": "2026-05-27T09:00:00Z",
                },
                headers=_headers(token_b),
            )
            assert b_fb.status_code == 200
            assert b_fb.json()["applied"] is True

            # Tenant A's first feedback (rejected) MUST apply — not blocked
            # by tenant B's stronger label on a same-named target
            a_fb = await unauth_client.post(
                _FB_PATH,
                json={
                    "request_id": "tenant-a-rej-fb",
                    "target_request_id": "shared-fb-target",
                    "label": "rejected",
                    "feedback_ts": "2026-05-27T09:00:00Z",
                },
                headers=_headers(token_a),
            )
            assert a_fb.status_code == 200
            assert a_fb.json()["applied"] is True
            assert a_fb.json()["previous_label"] is None
    await set_test_tenant_id(db_conn, tenant_a)


async def test_previously_rejected_baseline_isolated_by_tenant(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Tenant A's rejection of email/IP/origin must NOT leak into
    tenant B's build_context derivations (baseline.rejected_*_hmacs and
    .r_n counters are per-customer-per-tenant)."""
    token_a, tenant_a = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.87", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="ten-a-pr-book", source_ip="203.0.113.87"),
            headers=_headers(token_a),
        )
        await unauth_client.post(
            _FB_PATH,
            json={
                "request_id": "ten-a-pr-fb",
                "target_request_id": "ten-a-pr-book",
                "label": "rejected",
                "feedback_ts": "2026-05-27T09:00:00Z",
            },
            headers=_headers(token_a),
        )

        async with create_tenant_with_token(db_conn) as (token_b, _tenant_b):
            # Tenant B booking with same IP + email — must NOT trip
            # previously-rejected rules
            b_resp = await unauth_client.post(
                _BOOKING_PATH,
                json=_booking_payload(
                    request_id="ten-b-pr-book",
                    customer_external_id="tenant-b-cust",
                    source_ip="203.0.113.87",
                ),
                headers=_headers(token_b),
            )
            assert b_resp.status_code == 200
            triggered = set(b_resp.json()["triggered_rules"])
            previously_rejected = {r for r in triggered if "previously_rejected" in r}
            assert not previously_rejected, previously_rejected
    await set_test_tenant_id(db_conn, tenant_a)


# ---------------------------------------------------------------------------
# Auth boundary
# ---------------------------------------------------------------------------


async def test_tenant_a_token_cannot_act_as_tenant_b(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Auth boundary sanity: a request signed with tenant A's token only
    ever attaches tenant A's tenant_id to the AuthContext. There's no
    way for a tenant A token to write into tenant B's namespace."""
    token_a, tenant_a = seeded_api_token
    async with (
        seeded_ip_enrichment(db_conn, "203.0.113.88", asn_org="Comcast"),
        create_tenant_with_token(db_conn) as (_token_b, tenant_b),
    ):
        # POST a booking with tenant A's token
        a_resp = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="auth-boundary-book",
                source_ip="203.0.113.88",
            ),
            headers=_headers(token_a),
        )
        assert a_resp.status_code == 200

        # Tenant A's row count went up; tenant B's didn't
        async with with_test_tenant_context(db_conn, tenant_a):
            a_count = await db_conn.fetchval(
                "SELECT count(*) FROM shipments WHERE tenant_id = $1", tenant_a
            )
        async with with_test_tenant_context(db_conn, tenant_b):
            b_count = await db_conn.fetchval(
                "SELECT count(*) FROM shipments WHERE tenant_id = $1", tenant_b
            )
        assert a_count == 1
        assert b_count == 0
    await set_test_tenant_id(db_conn, tenant_a)
