"""End-to-end booking tests against fixture payloads.

These tests exercise the full request → upsert → persist flow against
the JSON payload fixtures in tests/fixtures/payloads/. They cover the
gaps test-reviewer flagged on 1C.1 (repeat-booking COALESCE,
total_shipments increment beyond 1, value=0 boundary) plus cross-tenant
isolation and RLS-policy structural checks.
"""

from collections.abc import Callable
from typing import Any

import asyncpg
from httpx import AsyncClient

from tests.conftest import create_tenant_with_token

_BOOKING_PATH = "/api/v1/shipments/booking/evaluate"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Minimal payload — required fields only.
# ---------------------------------------------------------------------------


async def test_minimal_payload_persists_required_rows(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    token, tenant_id = seeded_api_token
    payload = load_payload("booking_minimal")

    response = await unauth_client.post(_BOOKING_PATH, json=payload, headers=_headers(token))
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "ALLOW"
    assert body["score"] == 0.0

    # Customer + user + shipment + decision rows all persisted.
    customer = await db_conn.fetchrow(
        "SELECT id, registered_address, business_name FROM customers "
        "WHERE tenant_id = $1 AND external_id = $2",
        tenant_id,
        payload["customer"]["external_id"],
    )
    assert customer is not None
    assert customer["registered_address"] is None  # Not provided in minimal payload
    assert customer["business_name"] is None

    user_count = await db_conn.fetchval(
        "SELECT count(*) FROM users WHERE tenant_id = $1 AND customer_id = $2",
        tenant_id,
        customer["id"],
    )
    assert user_count == 1

    shipment_count = await db_conn.fetchval(
        "SELECT count(*) FROM shipments WHERE tenant_id = $1 AND request_id = $2",
        tenant_id,
        payload["request_id"],
    )
    assert shipment_count == 1

    decision_count = await db_conn.fetchval(
        "SELECT count(*) FROM decisions WHERE tenant_id = $1 AND request_id = $2",
        tenant_id,
        payload["request_id"],
    )
    assert decision_count == 1


# ---------------------------------------------------------------------------
# Full payload — all optional metadata.
# ---------------------------------------------------------------------------


async def test_full_payload_persists_all_optional_metadata(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    token, tenant_id = seeded_api_token
    payload = load_payload("booking_full")

    response = await unauth_client.post(_BOOKING_PATH, json=payload, headers=_headers(token))
    assert response.status_code == 200

    customer = await db_conn.fetchrow(
        """
        SELECT id, enterprise_id, registered_address, business_name, is_api_partner
        FROM customers WHERE tenant_id = $1 AND external_id = $2
        """,
        tenant_id,
        payload["customer"]["external_id"],
    )
    assert customer is not None
    assert customer["registered_address"] == "100 Corporate Plaza, Suite 4"
    assert customer["business_name"] == "Acme Logistics Inc"
    assert customer["is_api_partner"] is True
    assert customer["enterprise_id"] is not None

    enterprise_external = await db_conn.fetchval(
        "SELECT external_id FROM enterprises WHERE id = $1",
        customer["enterprise_id"],
    )
    assert enterprise_external == payload["enterprise"]["external_id"]


async def test_full_payload_does_not_persist_contact_fields(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """Phase 1: contact fields (all 4 — origin/destination email + phone)
    accepted by Pydantic but neither HMAC'd nor persisted anywhere.
    HMAC-at-ingress + baseline writes land in 1D.1 + 1D.3.

    This is defense-in-depth against future PII leaks — scans every
    text/JSONB column on tenant-scoped tables that touched this request,
    not just shipments.
    """
    token, tenant_id = seeded_api_token
    payload = load_payload("booking_full")

    response = await unauth_client.post(_BOOKING_PATH, json=payload, headers=_headers(token))
    assert response.status_code == 200

    # All 4 PII plaintexts must not appear anywhere on the touched rows.
    contacts: list[str] = [
        payload["contact"]["origin_email"],
        payload["contact"]["origin_phone"],
        payload["contact"]["destination_email"],
        payload["contact"]["destination_phone"],
    ]

    # Build a row-set covering every text/JSONB column the booking endpoint
    # writes for this request: shipments (origin, destination), decisions
    # (risk_factors), customers (registered_address, business_name).
    row_query = """
        SELECT origin::text AS blob FROM shipments
          WHERE tenant_id = $1 AND request_id = $2
        UNION ALL
        SELECT destination::text FROM shipments
          WHERE tenant_id = $1 AND request_id = $2
        UNION ALL
        SELECT risk_factors::text FROM decisions
          WHERE tenant_id = $1 AND request_id = $2
        UNION ALL
        SELECT registered_address FROM customers
          WHERE tenant_id = $1 AND external_id = $3
        UNION ALL
        SELECT business_name FROM customers
          WHERE tenant_id = $1 AND external_id = $3
    """
    rows = await db_conn.fetch(
        row_query, tenant_id, payload["request_id"], payload["customer"]["external_id"]
    )
    blobs = [r["blob"] for r in rows if r["blob"] is not None]
    for contact in contacts:
        for blob in blobs:
            assert contact not in blob, f"PII leak: {contact!r} found in stored row"


# ---------------------------------------------------------------------------
# Repeat booking — COALESCE semantics + total_shipments increment.
# ---------------------------------------------------------------------------


async def test_repeat_booking_increments_total_shipments(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    token, tenant_id = seeded_api_token
    payload_a = load_payload("booking_minimal")
    payload_a["request_id"] = "repeat-a"
    payload_b = load_payload("booking_minimal")
    payload_b["request_id"] = "repeat-b"
    # Both bookings target the SAME customer.external_id.

    for p in (payload_a, payload_b):
        r = await unauth_client.post(_BOOKING_PATH, json=p, headers=_headers(token))
        assert r.status_code == 200

    total = await db_conn.fetchval(
        "SELECT total_shipments FROM customers WHERE tenant_id = $1 AND external_id = $2",
        tenant_id,
        payload_a["customer"]["external_id"],
    )
    assert total == 2


async def test_repeat_booking_with_none_metadata_preserves_existing(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """First booking sets registered_address; second booking omits it
    (payload field is None). The COALESCE in upsert_customer must
    preserve the existing DB value."""
    token, tenant_id = seeded_api_token

    p1 = load_payload("booking_minimal")
    p1["request_id"] = "preserve-1"
    p1["customer"]["registered_address"] = "Original Address"

    r1 = await unauth_client.post(_BOOKING_PATH, json=p1, headers=_headers(token))
    assert r1.status_code == 200

    p2 = load_payload("booking_minimal")
    p2["request_id"] = "preserve-2"
    # Same customer.external_id; explicitly assert + pop registered_address
    # so a future edit to booking_minimal.json that adds the field doesn't
    # silently break this test (the second payload would re-supply the
    # original value and the COALESCE branch would not be exercised).
    p2["customer"].pop("registered_address", None)
    assert "registered_address" not in p2["customer"]

    r2 = await unauth_client.post(_BOOKING_PATH, json=p2, headers=_headers(token))
    assert r2.status_code == 200

    address = await db_conn.fetchval(
        "SELECT registered_address FROM customers WHERE tenant_id = $1 AND external_id = $2",
        tenant_id,
        p1["customer"]["external_id"],
    )
    assert address == "Original Address"


# ---------------------------------------------------------------------------
# Boundary case — value=0 (free shipment).
# ---------------------------------------------------------------------------


async def test_zero_value_shipment_accepted(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """`Field(ge=Decimal("0"))` accepts 0 (free shipment). Boundary
    completion against the negative-rejection test."""
    token, _ = seeded_api_token
    payload = load_payload("booking_minimal")
    payload["request_id"] = "zero-value-1"
    payload["shipment"]["value"] = 0

    response = await unauth_client.post(_BOOKING_PATH, json=payload, headers=_headers(token))
    assert response.status_code == 200
    assert response.json()["decision"] == "ALLOW"


# ---------------------------------------------------------------------------
# Cross-tenant — same external_id under different tenants → two customer rows.
# ---------------------------------------------------------------------------


async def test_cross_tenant_external_id_collision(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """UNIQUE(tenant_id, external_id) on customers/users — the same
    external_id can exist under different tenants without conflict."""
    token_a, tenant_a = seeded_api_token
    shared_external = "shared-cust-id"

    payload_a = load_payload("booking_minimal")
    payload_a["request_id"] = "xtenant-a"
    payload_a["customer"]["external_id"] = shared_external

    r_a = await unauth_client.post(_BOOKING_PATH, json=payload_a, headers=_headers(token_a))
    assert r_a.status_code == 200

    async with create_tenant_with_token(db_conn) as (token_b, tenant_b):
        payload_b = load_payload("booking_minimal")
        payload_b["request_id"] = "xtenant-b"
        payload_b["customer"]["external_id"] = shared_external

        r_b = await unauth_client.post(_BOOKING_PATH, json=payload_b, headers=_headers(token_b))
        assert r_b.status_code == 200

        # Two distinct customer rows — one per tenant.
        count = await db_conn.fetchval(
            "SELECT count(*) FROM customers WHERE external_id = $1", shared_external
        )
        assert count == 2

        # Each tenant sees only its own customer (verify ids differ).
        a_id = await db_conn.fetchval(
            "SELECT id FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_a,
            shared_external,
        )
        b_id = await db_conn.fetchval(
            "SELECT id FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_b,
            shared_external,
        )
        assert a_id != b_id


# ---------------------------------------------------------------------------
# RLS policy structural check (enforcement is dormant under Phase 1 superuser
# per .claude/STATUS.md; Phase 5 role transition activates it).
# ---------------------------------------------------------------------------


async def test_rls_policies_exist_on_tenant_scoped_tables(
    db_conn: asyncpg.Connection,
) -> None:
    """Pinning snapshot — any future tenant-scoped table addition MUST
    update this expected set AND get a tenant_isolation policy in the
    migration. The assertion is intentionally strict (set equality, not
    superset)."""
    expected = {
        "enterprises",
        "customers",
        "users",
        "shipments",
        "decisions",
        "feedback",
        "customer_baselines",
        "api_tokens",
        "app_users",
    }
    rows = await db_conn.fetch(
        """
        SELECT tablename FROM pg_policies
        WHERE schemaname = 'public' AND policyname = 'tenant_isolation'
        """
    )
    actual = {r["tablename"] for r in rows}
    assert actual == expected


async def test_global_tables_have_no_rls(db_conn: asyncpg.Connection) -> None:
    """ip_enrichment / global_blocked_vectors / tenants are intentionally
    global. Assert against an exact expected set so a missing table
    (dropped/renamed by a future migration) doesn't silently pass."""
    expected = {"ip_enrichment", "global_blocked_vectors", "tenants"}
    rows = await db_conn.fetch(
        """
        SELECT tablename, rowsecurity FROM pg_tables
        WHERE schemaname = 'public' AND tablename = ANY($1)
        """,
        list(expected),
    )
    found = {r["tablename"]: r["rowsecurity"] for r in rows}
    assert set(found.keys()) == expected, f"missing tables: {expected - set(found.keys())}"
    for tablename, rls in found.items():
        assert rls is False, f"{tablename} should NOT have RLS"
