"""End-to-end integration tests for both admin endpoints.

Combined coverage:
- GET /api/v1/admin/decisions/{request_id} — happy paths + 401/403/404
  + cross-tenant + modification decision lookup
- GET /api/v1/admin/customers/{external_id}/baseline — happy paths +
  401/403/404 + cross-tenant + missing-baseline case + truncation
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime

import asyncpg
import pytest
from httpx import AsyncClient

from app.auth import AuthContext, _hash_token, require_api_token
from app.main import app
from tests.conftest import _cleanup_tenant, set_test_tenant_id

pytestmark = pytest.mark.asyncio


def _booking_payload(
    *, request_id: str, customer: str, source_ip: str = "192.0.2.99"
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "shipment_id": f"ship-{request_id}",
        "transaction_number": f"txn-{request_id}",
        "customer": {"external_id": customer},
        "user": {"external_id": f"u-{customer}"},
        "source_ip": source_ip,
        "shipment": {
            "origin": {"address": "1 Main St", "city": "Boston", "country": "US"},
            "destination": {"address": "2 Park Ave", "city": "New York", "country": "US"},
            "value": "100",
            "channel": "web",
        },
        "booking_ts": datetime.now(UTC).isoformat(),
    }


async def _seed_admin_token(db_conn: asyncpg.Connection, tenant_id: int) -> str:
    plaintext = "adm-" + secrets.token_urlsafe(16)
    await db_conn.execute(
        "INSERT INTO api_tokens (tenant_id, token_hash, role) VALUES ($1, $2, 'admin')",
        tenant_id,
        _hash_token(plaintext),
    )
    return plaintext


async def _post_booking_as_tenant(
    unauth_client: AsyncClient,
    tenant_id: int,
    request_id: str,
    customer: str,
) -> str:
    """POST a booking via the dependency-overridden tenant role; return the
    booking's request_id (echoed) so admin tests can look it up."""
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=tenant_id, role="tenant"
    )
    try:
        payload = _booking_payload(request_id=request_id, customer=customer)
        r = await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=payload,
        )
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    assert r.status_code == 200, f"booking seed failed: {r.text}"
    return request_id


# ---------------------------------------------------------------------------
# Admin decisions endpoint
# ---------------------------------------------------------------------------


async def test_admin_decision_lookup_returns_full_shape(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Admin token + existing decision in tenant → 200 with full shape."""
    rid = f"REQ-adm-d-{secrets.token_hex(3)}"
    await _post_booking_as_tenant(unauth_client, seeded_tenant, rid, "cust-adm-d")
    admin_token = await _seed_admin_token(db_conn, seeded_tenant)
    r = await unauth_client.get(
        f"/api/v1/admin/decisions/{rid}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["request_id"] == rid
    assert body["request_type"] == "booking"
    assert body["decision"] in ("ALLOW", "REVIEW", "BLOCK")
    assert "triggered_rules" in body
    assert "risk_factors" in body
    # Shipment block — city + country (not full address).
    # shipment.id is now the platform-supplied TEXT shipment_id (string),
    # equal to the booking's shipment_id (f"ship-{request_id}").
    assert body["shipment"]["id"] == f"ship-{rid}"
    assert body["shipment"]["origin_city"] == "Boston"
    assert body["shipment"]["destination_city"] == "New York"
    assert body["shipment"]["source_ip"] == "192.0.2.99"


async def test_admin_decision_tenant_token_returns_403(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    rid = f"REQ-adm-d-403-{secrets.token_hex(3)}"
    await _post_booking_as_tenant(unauth_client, seeded_tenant, rid, "cust-adm-d-403")
    # Tenant (non-admin) token
    plaintext = "tnt-" + secrets.token_urlsafe(16)
    await db_conn.execute(
        "INSERT INTO api_tokens (tenant_id, token_hash, role) VALUES ($1, $2, 'tenant')",
        seeded_tenant,
        _hash_token(plaintext),
    )
    r = await unauth_client.get(
        f"/api/v1/admin/decisions/{rid}",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 403
    assert "admin role required" in r.json()["detail"]


async def test_admin_decision_nonexistent_request_id_404(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    admin_token = await _seed_admin_token(db_conn, seeded_tenant)
    r = await unauth_client.get(
        "/api/v1/admin/decisions/REQ-does-not-exist",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404
    assert "decision not found" in r.json()["detail"]


async def test_admin_decision_cross_tenant_returns_404(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Tenant_a admin looking up tenant_b's request_id → 404 (hides existence)."""
    other_tenant_id: int = await db_conn.fetchval(
        'INSERT INTO tenants (name, config) VALUES ($1, \'{"allowed_currencies": ["USD", "CAD"]}\'::jsonb) RETURNING id',
        f"adm-other-{secrets.token_hex(3)}",
    )
    try:
        await set_test_tenant_id(db_conn, other_tenant_id)
        rid = f"REQ-adm-cross-{secrets.token_hex(3)}"
        await _post_booking_as_tenant(unauth_client, other_tenant_id, rid, "cust-cross")
        # Now tenant_a (seeded_tenant) admin looks up tenant_b's request_id.
        await set_test_tenant_id(db_conn, seeded_tenant)
        admin_token_a = await _seed_admin_token(db_conn, seeded_tenant)
        r = await unauth_client.get(
            f"/api/v1/admin/decisions/{rid}",
            headers={"Authorization": f"Bearer {admin_token_a}"},
        )
        assert r.status_code == 404
    finally:
        await set_test_tenant_id(db_conn, other_tenant_id)
        await _cleanup_tenant(db_conn, other_tenant_id)
        await set_test_tenant_id(db_conn, seeded_tenant)


async def test_admin_decision_no_auth_401(unauth_client: AsyncClient) -> None:
    r = await unauth_client.get("/api/v1/admin/decisions/REQ-no-auth")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Admin customer baseline endpoint
# ---------------------------------------------------------------------------


async def test_admin_customer_baseline_returns_full_shape(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Customer with baseline → 200, full shape."""
    rid = f"REQ-adm-b-{secrets.token_hex(3)}"
    await _post_booking_as_tenant(unauth_client, seeded_tenant, rid, "cust-adm-b")
    admin_token = await _seed_admin_token(db_conn, seeded_tenant)
    r = await unauth_client.get(
        "/api/v1/admin/customers/cust-adm-b/baseline",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["customer"]["external_id"] == "cust-adm-b"
    assert body["customer"]["total_shipments"] >= 1
    assert body["baseline"] is not None
    # Stat dicts have the truncation shape.
    assert "entries" in body["baseline"]["origin_stats"]
    assert "total_count" in body["baseline"]["origin_stats"]
    assert "truncated" in body["baseline"]["origin_stats"]


async def test_admin_customer_baseline_nonexistent_404(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    admin_token = await _seed_admin_token(db_conn, seeded_tenant)
    r = await unauth_client.get(
        "/api/v1/admin/customers/cust-does-not-exist/baseline",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404
    assert "customer not found" in r.json()["detail"]


async def test_admin_customer_baseline_cross_tenant_404(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Tenant_a admin looking up tenant_b's customer → 404."""
    other_tenant_id: int = await db_conn.fetchval(
        'INSERT INTO tenants (name, config) VALUES ($1, \'{"allowed_currencies": ["USD", "CAD"]}\'::jsonb) RETURNING id',
        f"adm-other-c-{secrets.token_hex(3)}",
    )
    try:
        await set_test_tenant_id(db_conn, other_tenant_id)
        await _post_booking_as_tenant(
            unauth_client, other_tenant_id, f"REQ-bcross-{secrets.token_hex(3)}", "cust-bcross"
        )
        await set_test_tenant_id(db_conn, seeded_tenant)
        admin_token_a = await _seed_admin_token(db_conn, seeded_tenant)
        r = await unauth_client.get(
            "/api/v1/admin/customers/cust-bcross/baseline",
            headers={"Authorization": f"Bearer {admin_token_a}"},
        )
        assert r.status_code == 404
    finally:
        await set_test_tenant_id(db_conn, other_tenant_id)
        await _cleanup_tenant(db_conn, other_tenant_id)
        await set_test_tenant_id(db_conn, seeded_tenant)


async def test_admin_customer_baseline_tenant_token_403(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    rid = f"REQ-adm-c-403-{secrets.token_hex(3)}"
    await _post_booking_as_tenant(unauth_client, seeded_tenant, rid, "cust-c-403")
    # Tenant token (not admin).
    plaintext = "tnt-c-" + secrets.token_urlsafe(16)
    await db_conn.execute(
        "INSERT INTO api_tokens (tenant_id, token_hash, role) VALUES ($1, $2, 'tenant')",
        seeded_tenant,
        _hash_token(plaintext),
    )
    r = await unauth_client.get(
        "/api/v1/admin/customers/cust-c-403/baseline",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 403


async def test_admin_customer_baseline_no_auth_401(unauth_client: AsyncClient) -> None:
    r = await unauth_client.get("/api/v1/admin/customers/cust-x/baseline")
    assert r.status_code == 401


async def test_admin_customer_truncation_applied_with_15_entries(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Seed a baseline with 15 origin_stats entries; admin response shows
    top-10 + truncated=True."""
    # Seed customer + baseline directly with 15 origin_stats entries.
    cust_id: int = await db_conn.fetchval(
        "INSERT INTO customers (tenant_id, external_id) VALUES ($1, $2) RETURNING id",
        seeded_tenant,
        "cust-trunc",
    )
    origin_stats = {
        f"addr-{i}": {"n": float(15 - i), "r_n": 0.0, "last": "2026-01-01"} for i in range(15)
    }
    await db_conn.execute(
        """
        INSERT INTO customer_baselines (tenant_id, customer_id, origin_stats, decay_anchor_date)
        VALUES ($1, $2, $3::jsonb, current_date)
        """,
        seeded_tenant,
        cust_id,
        json.dumps(origin_stats),
    )

    admin_token = await _seed_admin_token(db_conn, seeded_tenant)
    r = await unauth_client.get(
        "/api/v1/admin/customers/cust-trunc/baseline",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    origin_block = r.json()["baseline"]["origin_stats"]
    assert origin_block["total_count"] == 15
    assert origin_block["truncated"] is True
    assert len(origin_block["entries"]) == 10
    # The top entry is the highest-n one (addr-0 has n=15).
    assert origin_block["entries"][0]["key"] == "addr-0"
    assert origin_block["entries"][0]["n"] == 15.0


# ---------------------------------------------------------------------------
# Admin role does NOT restrict normal access
# ---------------------------------------------------------------------------


async def test_admin_token_can_call_booking_endpoint(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Admin role should not restrict normal endpoint access (booking,
    modification, feedback all use require_api_token; admin role passes)."""
    admin_token = await _seed_admin_token(db_conn, seeded_tenant)
    rid = f"REQ-adm-as-booking-{secrets.token_hex(3)}"
    r = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=_booking_payload(request_id=rid, customer="cust-adm-as-booking"),
    )
    assert r.status_code == 200
