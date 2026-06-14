"""Non-superuser RLS enforcement verification.

This file is the canary that proves `tenant_isolation` policies
actually enforce when the connection is non-superuser with
`app.tenant_id` set.

Mechanism: the `riskd_app_login` role exists per migration 0008
(LOGIN INHERIT, GRANT riskd_app) and is the runtime DATABASE_URL
identity for the app. Tests open a fresh asyncpg connection as that
role using the same parse-from-settings approach production endpoints
use — no temporary-grant dance, no role-state mutation, xdist-safe
(no @pytest.mark.serial needed).

The fixture yields a `riskd_app_login` connection.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import asyncpg
import pytest
import pytest_asyncio

from app.auth import _hash_token
from app.config import get_settings
from tests.conftest import _cleanup_tenant, set_test_tenant_id


@pytest_asyncio.fixture
async def riskd_app_conn() -> AsyncIterator[asyncpg.Connection]:
    """Yield a fresh asyncpg connection as `riskd_app_login`.

    Connection parameters: parse host/port/database from
    `settings.database_url` (which already points at
    `riskd_app_login`), so the test connects under the exact runtime
    identity. Password is the local-dev convention from migration
    0008 (`riskd_app_login_dev`). The connection is closed on
    teardown; no role state is mutated.
    """
    settings = get_settings()
    parsed = urlparse(settings.database_url.replace("postgresql+asyncpg", "postgresql"))
    riskd_conn = await asyncpg.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        user="riskd_app_login",
        password="riskd_app_login_dev",
        database=(parsed.path or "/riskd").lstrip("/"),
    )
    try:
        yield riskd_conn
    finally:
        await riskd_conn.close()


@pytest_asyncio.fixture
async def two_tenants_with_shipments(
    db_conn: asyncpg.Connection,
) -> AsyncIterator[tuple[int, int]]:
    """Seed two tenants each with 3 shipments. Yields (tenant_a, tenant_b)
    integer ids. Teardown cascades both tenants' rows."""
    tenant_a: int = await db_conn.fetchval(
        "INSERT INTO tenants (name) VALUES ($1) RETURNING id",
        f"rls-test-a-{secrets.token_hex(4)}",
    )
    tenant_b: int = await db_conn.fetchval(
        "INSERT INTO tenants (name) VALUES ($1) RETURNING id",
        f"rls-test-b-{secrets.token_hex(4)}",
    )
    try:
        for tenant_id in (tenant_a, tenant_b):
            # Switch session app.tenant_id BEFORE the dependent
            # inserts so RLS WITH CHECK accepts them. The fixture
            # connection (db_conn) carries state across iterations,
            # so we explicitly switch each loop.
            await set_test_tenant_id(db_conn, tenant_id)
            cust = await db_conn.fetchval(
                "INSERT INTO customers (tenant_id, external_id) VALUES ($1, 'rls-cust') RETURNING id",
                tenant_id,
            )
            user = await db_conn.fetchval(
                "INSERT INTO users (tenant_id, customer_id, external_id) VALUES ($1, $2, 'rls-user') RETURNING id",
                tenant_id,
                cust,
            )
            for i in range(3):
                await db_conn.execute(
                    """
                    INSERT INTO shipments (
                        id, tenant_id, customer_id, user_id, request_id, source_ip,
                        origin, destination, value, channel, booking_ts,
                        destination_hmac, transaction_number
                    )
                    VALUES (
                        $4, $1, $2, $3, $4, $5::inet,
                        '{"address":"10 Origin"}'::jsonb,
                        '{"address":"20 Destination"}'::jsonb,
                        100, 'web', now(),
                        'rls-test-hmac', 'tx-' || $4
                    )
                    """,
                    tenant_id,
                    cust,
                    user,
                    f"rls-ship-{tenant_id}-{i}",
                    f"203.0.113.{200 + i}",
                )
            # Also seed a decision per tenant for the decisions check
            ship_id: str = await db_conn.fetchval(
                "SELECT id FROM shipments WHERE tenant_id = $1 ORDER BY id LIMIT 1",
                tenant_id,
            )
            await db_conn.execute(
                """
                INSERT INTO decisions (
                    tenant_id, shipment_id, request_id, request_type,
                    score, decision, classification, risk_level,
                    triggered_rules, risk_factors
                )
                VALUES ($1, $2, 'rls-dec', 'booking', 0.5, 'REVIEW',
                        'YELLOW', 'MEDIUM', '{}'::text[], '[]'::jsonb)
                """,
                tenant_id,
                ship_id,
            )
            # And a feedback row
            await db_conn.execute(
                """
                INSERT INTO feedback (
                    tenant_id, request_id, target_request_id, label,
                    feedback_ts
                )
                VALUES ($1, $2, 'rls-dec', 'rejected', now())
                """,
                tenant_id,
                f"rls-fb-{tenant_id}",
            )
            # And a baseline row
            await db_conn.execute(
                "INSERT INTO customer_baselines (tenant_id, customer_id) VALUES ($1, $2)",
                tenant_id,
                cust,
            )
            # And an api_token (for the api_tokens table)
            await db_conn.execute(
                "INSERT INTO api_tokens (tenant_id, token_hash, role) VALUES ($1, $2, 'tenant')",
                tenant_id,
                _hash_token(f"rls-token-{tenant_id}"),
            )
        yield tenant_a, tenant_b
    finally:
        # Cleanup DELETEs must run under the tenant's RLS view.
        await set_test_tenant_id(db_conn, tenant_a)
        await _cleanup_tenant(db_conn, tenant_a)
        await set_test_tenant_id(db_conn, tenant_b)
        await _cleanup_tenant(db_conn, tenant_b)


@pytest.mark.serial
async def test_rls_shipments_scoped_by_app_tenant_id(
    riskd_app_conn: asyncpg.Connection,
    two_tenants_with_shipments: tuple[int, int],
) -> None:
    """As riskd_app (non-superuser) with app.tenant_id = tenant_a, a
    SELECT FROM shipments returns ONLY tenant_a's 3 rows — even though
    tenant_b's 3 rows exist in the table. If the RLS policy were broken
    (missing, wrong expression, or ENABLE RLS dropped), this returns
    6 and the test fails."""
    tenant_a, tenant_b = two_tenants_with_shipments
    async with riskd_app_conn.transaction():
        await riskd_app_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_a))
        count_a = await riskd_app_conn.fetchval("SELECT count(*) FROM shipments")
        assert count_a == 3, f"expected 3 tenant_a shipments, got {count_a}"

    async with riskd_app_conn.transaction():
        await riskd_app_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_b))
        count_b = await riskd_app_conn.fetchval("SELECT count(*) FROM shipments")
        assert count_b == 3, f"expected 3 tenant_b shipments, got {count_b}"


@pytest.mark.serial
@pytest.mark.parametrize(
    "table",
    [
        "customers",
        "users",
        "decisions",
        "feedback",
        "customer_baselines",
        # api_tokens RLS dropped in migration 0009 (auth-lookup chicken-and-egg);
        # app_users likewise. See docs/security-audit-rls-phase-5.md for the
        # auth-table RLS rationale.
    ],
)
async def test_rls_table_scoped_by_app_tenant_id(
    table: str,
    riskd_app_conn: asyncpg.Connection,
    two_tenants_with_shipments: tuple[int, int],
) -> None:
    """Same canary applied to every tenant-scoped table that the fixture
    seeds. If any policy is missing or misconfigured, this test
    parametrize cell fails."""
    tenant_a, tenant_b = two_tenants_with_shipments
    async with riskd_app_conn.transaction():
        await riskd_app_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_a))
        count_a: int = await riskd_app_conn.fetchval(f"SELECT count(*) FROM {table}")

    async with riskd_app_conn.transaction():
        await riskd_app_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_b))
        count_b: int = await riskd_app_conn.fetchval(f"SELECT count(*) FROM {table}")

    # Each tenant has at least 1 row of every seeded type; tenant_a's
    # query returns ONLY tenant_a's rows, not tenant_b's.
    assert count_a >= 1, f"expected at least 1 tenant_a row in {table}, got {count_a}"
    assert count_b >= 1, f"expected at least 1 tenant_b row in {table}, got {count_b}"

    # The cross-tenant invariant: viewing one tenant cannot see the other's.
    # Confirmed structurally by the per-tenant count below (the seeds inserted
    # the same number per tenant, so a leak would inflate both counts).
    async with riskd_app_conn.transaction():
        await riskd_app_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_a))
        # Try to fetch a row with tenant_id = tenant_b — should return None
        # since RLS filters it out
        leaked: Any = await riskd_app_conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE tenant_id = $1",
            tenant_b,
        )
        assert leaked == 0, (
            f"RLS leak: as tenant_a viewing {table}, saw {leaked} tenant_b rows (expected 0)"
        )


@pytest.mark.serial
async def test_rls_blocks_unset_tenant_context(
    riskd_app_conn: asyncpg.Connection,
    two_tenants_with_shipments: tuple[int, int],
) -> None:
    """When app.tenant_id is unset (the policy's
    current_setting('app.tenant_id') returns ''), the ::int cast raises
    or returns 0 rows. Either is acceptable — the load-bearing
    assertion is that an unset tenant context does NOT see any rows."""
    async with riskd_app_conn.transaction():
        try:
            count = await riskd_app_conn.fetchval("SELECT count(*) FROM shipments")
            assert count == 0, f"RLS leak: with unset app.tenant_id, expected 0 rows, got {count}"
        except asyncpg.PostgresError:
            # Policy refused unset context (e.g. ::int cast failure on '')
            pass


# ---------------------------------------------------------------------------
# Admin endpoint extension — RLS enforces against the SQL patterns
# the admin endpoints execute. The canary role is `riskd_app_login`, the
# runtime identity, and these tests pin tenant-isolation invariants under
# it. The FastAPI-layer enforcement matrix lives in
# tests/integration/test_admin_endpoints.py (which uses dependency-override).
# ---------------------------------------------------------------------------


@pytest.mark.serial
async def test_rls_admin_decisions_join_scoped_by_tenant(
    riskd_app_conn: asyncpg.Connection,
    two_tenants_with_shipments: tuple[int, int],
) -> None:
    """Admin endpoint's `SELECT FROM decisions JOIN shipments WHERE
    d.tenant_id = $1` runs under riskd_app session. As tenant_a, the JOIN
    returns ONLY tenant_a's row even when querying tenant_b's request_id."""
    tenant_a, tenant_b = two_tenants_with_shipments
    async with riskd_app_conn.transaction():
        await riskd_app_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_a))
        # As tenant_a, query tenant_b's request_id — RLS hides tenant_b's row,
        # so the WHERE clause matches nothing.
        row = await riskd_app_conn.fetchrow(
            """
            SELECT d.request_id
              FROM decisions d
              JOIN shipments s ON s.id = d.shipment_id AND s.tenant_id = d.tenant_id
             WHERE d.tenant_id = $1 AND d.request_id = $2
            """,
            tenant_b,
            "rls-dec",
        )
        assert row is None, f"RLS leak: tenant_a queried tenant_b's decision and got {row}"

        # Same query against tenant_a's own request_id returns the row.
        row_a = await riskd_app_conn.fetchrow(
            """
            SELECT d.request_id
              FROM decisions d
              JOIN shipments s ON s.id = d.shipment_id AND s.tenant_id = d.tenant_id
             WHERE d.tenant_id = $1 AND d.request_id = $2
            """,
            tenant_a,
            "rls-dec",
        )
        assert row_a is not None


@pytest.mark.serial
async def test_rls_admin_customer_lookup_scoped_by_tenant(
    riskd_app_conn: asyncpg.Connection,
    two_tenants_with_shipments: tuple[int, int],
) -> None:
    """Admin customer lookup: `SELECT FROM customers WHERE tenant_id = $1
    AND external_id = $2`. tenant_a cannot see tenant_b's customer even
    though both use external_id='rls-cust'."""
    tenant_a, tenant_b = two_tenants_with_shipments
    async with riskd_app_conn.transaction():
        await riskd_app_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_a))
        # As tenant_a, look up by tenant_b's id — RLS hides; WHERE matches nothing.
        row = await riskd_app_conn.fetchrow(
            "SELECT id FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_b,
            "rls-cust",
        )
        assert row is None
        # Same query for tenant_a returns its own customer.
        row_a = await riskd_app_conn.fetchrow(
            "SELECT id FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_a,
            "rls-cust",
        )
        assert row_a is not None


@pytest.mark.serial
async def test_rls_admin_baseline_lookup_scoped_by_tenant(
    db_conn: asyncpg.Connection,
    riskd_app_conn: asyncpg.Connection,
    two_tenants_with_shipments: tuple[int, int],
) -> None:
    """Admin customer-baseline endpoint runs `SELECT FROM customer_baselines
    WHERE tenant_id = $1 AND customer_id = $2` (admin.py:202-222). This test
    exercises THAT EXACT pattern — both predicates — under the riskd_app
    session for both cross-tenant (negative) and same-tenant (positive)
    cases, distinguishing it from the parametrized table-level RLS canary
    above (which only tests the single-predicate `tenant_id = $1`).
    """
    tenant_a, tenant_b = two_tenants_with_shipments
    # Resolve both tenants' customer_id values under the db_conn (which
    # is also under RLS). Switch app.tenant_id per lookup so
    # each tenant's row is visible.
    await set_test_tenant_id(db_conn, tenant_b)
    tenant_b_customer_id: int = await db_conn.fetchval(
        "SELECT customer_id FROM customer_baselines WHERE tenant_id = $1 LIMIT 1",
        tenant_b,
    )
    assert tenant_b_customer_id is not None, "fixture seed failed for tenant_b"
    await set_test_tenant_id(db_conn, tenant_a)
    tenant_a_customer_id: int = await db_conn.fetchval(
        "SELECT customer_id FROM customer_baselines WHERE tenant_id = $1 LIMIT 1",
        tenant_a,
    )
    assert tenant_a_customer_id is not None, "fixture seed failed for tenant_a"

    async with riskd_app_conn.transaction():
        await riskd_app_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_a))
        # Negative: as tenant_a, query tenant_b's (tenant_id, customer_id)
        # pair — the EXACT shape admin.py runs. RLS hides tenant_b's
        # baselines, so the lookup returns None.
        row = await riskd_app_conn.fetchrow(
            "SELECT customer_id FROM customer_baselines WHERE tenant_id = $1 AND customer_id = $2",
            tenant_b,
            tenant_b_customer_id,
        )
        assert row is None, "RLS leak: tenant_a saw tenant_b's baseline via dual-key lookup"
        # Positive: tenant_a's own (tenant_id, customer_id) pair returns
        # its own baseline. Guards against an over-restrictive RLS policy
        # that would silently deny everything (all 3 admin-RLS tests'
        # negative assertions would otherwise pass under such a policy).
        row_a = await riskd_app_conn.fetchrow(
            "SELECT customer_id FROM customer_baselines WHERE tenant_id = $1 AND customer_id = $2",
            tenant_a,
            tenant_a_customer_id,
        )
        assert row_a is not None
        assert row_a["customer_id"] == tenant_a_customer_id
