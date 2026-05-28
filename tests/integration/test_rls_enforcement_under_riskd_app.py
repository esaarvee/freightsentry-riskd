"""Non-superuser RLS enforcement verification (3C.3).

The 3C.2 sweep proves the app-layer WHERE tenant_id = $N filter works
under the bootstrap-superuser connection (which BYPASSes RLS). This
file proves the OTHER half: the tenant_isolation policies actually
enforce when the connection is a NON-superuser role with `app.tenant_id`
set.

Without this, a Phase 5 role transition could ship with broken RLS
(e.g. a missing policy, a wrong policy expression) and we'd only
discover at runtime that tenant isolation depends on app-layer
filtering alone. This test is the canary.

Mechanism: the `riskd_app` role exists (NOLOGIN) per Phase 1
0001_initial.py:33 with GRANTs on all tables in 0001_initial.py:324-326.
The fixture grants LOGIN temporarily for the test, opens a fresh
connection as that role, exercises queries with `SET LOCAL
app.tenant_id`, then revokes LOGIN on teardown.

Marked @pytest.mark.serial because it mutates a role privilege —
xdist-incompatible. Teardown discipline: LOGIN must be revoked even on
test failure (try/finally), otherwise the test DB role is left in a
LOGIN state.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from app.auth import _hash_token
from app.config import get_settings
from tests.conftest import _cleanup_tenant


@pytest_asyncio.fixture
async def riskd_app_conn(
    db_conn: asyncpg.Connection,
) -> AsyncIterator[asyncpg.Connection]:
    """Grant LOGIN on the `riskd_app` role, open a fresh connection as
    that role, yield it, then revoke LOGIN on teardown.

    Uses a fresh random password per test to avoid persisting a known
    credential. teardown via try/finally ensures the role returns to
    NOLOGIN even if the test body raises.
    """
    password = secrets.token_urlsafe(16)
    await db_conn.execute(f"ALTER ROLE riskd_app WITH LOGIN PASSWORD '{password}'")
    riskd_conn: asyncpg.Connection | None = None
    try:
        settings = get_settings()
        # Construct a DSN for riskd_app. settings.database_url is the
        # superuser DSN; swap the userinfo for riskd_app:<password>.
        # asyncpg.connect accepts host/port/user/password kwargs as an
        # alternative to a DSN string — use those to avoid DSN parsing
        # gymnastics across drivers.
        original_dsn = settings.database_url
        # Parse host/port/database out of the canonical DSN
        # postgres://user:pass@host:port/dbname
        from urllib.parse import urlparse

        parsed = urlparse(original_dsn.replace("postgresql+asyncpg", "postgresql"))
        riskd_conn = await asyncpg.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user="riskd_app",
            password=password,
            database=(parsed.path or "/riskd").lstrip("/"),
        )
        yield riskd_conn
    finally:
        if riskd_conn is not None:
            await riskd_conn.close()
        # Revoke LOGIN even if the connection / yield raised
        await db_conn.execute("ALTER ROLE riskd_app WITH NOLOGIN PASSWORD NULL")


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
                        tenant_id, customer_id, user_id, request_id, source_ip,
                        origin, destination, value, channel, booking_ts,
                        destination_hmac
                    )
                    VALUES (
                        $1, $2, $3, $4, $5::inet,
                        '{"address":"10 Origin"}'::jsonb,
                        '{"address":"20 Destination"}'::jsonb,
                        100, 'web', now(),
                        'rls-test-hmac'
                    )
                    """,
                    tenant_id,
                    cust,
                    user,
                    f"rls-ship-{tenant_id}-{i}",
                    f"203.0.113.{200 + i}",
                )
            # Also seed a decision per tenant for the decisions check
            ship_id: int = await db_conn.fetchval(
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
        await _cleanup_tenant(db_conn, tenant_a)
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
        "api_tokens",
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
            f"RLS leak: as tenant_a viewing {table}, " f"saw {leaked} tenant_b rows (expected 0)"
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
