"""Shared test fixtures.

Pool initialised once per session; tests share the same asyncpg pool the
running app would use. Per-test seed cleanup is explicit via the
`seeded_tenant` / `seeded_api_token` fixtures (commit + delete rather
than per-test rollback, because the auth dependency in app/auth.py
acquires a SEPARATE connection from the same pool and won't see
uncommitted transactional data).
"""

import json
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth import AuthContext, _hash_token, require_api_token
from app.config import get_settings
from app.db import close_pool, init_pool
from app.main import app
from app.runtime import init_runtime
from app.tenant_config import TenantConfig


def make_default_tenant_config(tenant_id: int = 1) -> TenantConfig:
    """Synthetic TenantConfig for tests calling build_context / score directly.

    All overrides None, defaults applied — matches a freshly-onboarded
    tenant with empty `tenants.config` JSONB. Used by unit tests that
    construct contexts without going through the endpoint (which would
    load the config from DB).
    """
    now = datetime.now(UTC)
    return TenantConfig(
        tenant_id=tenant_id,
        config_version=0,
        created_at=now,
        updated_at=now,
    )


async def seed_tenant_created_days_ago(
    db_conn: asyncpg.Connection,
    *,
    days_ago: int,
    config: dict[str, Any] | None = None,
) -> int:
    """Insert a tenant whose created_at is exactly `days_ago` days ago.

    Used by Phase 4C integration tests for the cold-start grace mechanism
    which measures the grace window from `tenants.created_at`. Returns
    the new tenant_id. Caller is responsible for cleanup (typically via
    _cleanup_tenant).

    Phase 6B: auto-injects allowed_currencies = ["USD", "CAD"] unless
    the caller's config explicitly overrides — matches the
    seeded_tenant fixture default so integration tests POSTing USD
    payloads continue to work post-6B without per-test edits.
    """
    merged = {"allowed_currencies": ["USD", "CAD"]}
    if config:
        merged.update(config)
    tenant_id: int = await db_conn.fetchval(
        """
        INSERT INTO tenants (name, config, created_at, updated_at)
        VALUES (
            $1,
            $2::jsonb,
            now() - make_interval(days => $3),
            now() - make_interval(days => $3)
        )
        RETURNING id
        """,
        f"test-tenant-grace-{secrets.token_hex(4)}",
        json.dumps(merged),
        days_ago,
    )
    return tenant_id


_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Single source of truth for cascade-cleanup. Reverse-FK order so children
# delete before parents. Add new tenant-scoped tables here, NOT inline in
# each fixture — duplicating this list across fixtures is how cleanup
# drifts (e.g., 1D.1+ may add new tenant-scoped tables).
_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "feedback",
    "decisions",
    "customer_baselines",
    "shipments",
    # Phase 6A.6 — RLS-enabled, no FKs back to other tenant-scoped
    # tables, so position is arbitrary among the leaves; placed before
    # users/customers for explicit ordering.
    "tenant_route_baselines",
    "users",
    "customers",
    "enterprises",
    "api_tokens",
    "app_users",
)


async def _cleanup_tenant(conn: asyncpg.Connection, tenant_id: int) -> None:
    for table in _TENANT_SCOPED_TABLES:
        await conn.execute(f"DELETE FROM {table} WHERE tenant_id = $1", tenant_id)
    await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)


@pytest.fixture
def load_payload() -> Callable[[str], dict[str, Any]]:
    """Return a loader for JSON payload fixtures under tests/fixtures/payloads/."""

    def _load(name: str) -> dict[str, Any]:
        path = _FIXTURES_DIR / "payloads" / f"{name}.json"
        with path.open(encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        return data

    return _load


@pytest.fixture(autouse=True)
def _reset_tenant_config_cache() -> None:
    """5B introduces an in-process 60s TTL cache fronting load_tenant_config.
    Many integration tests mutate `tenants.config` mid-test and expect the
    next endpoint call to observe the new value. In production the
    staleness window is operator-acceptable; in tests it would surface
    as flaky cross-test bleed. Reset the cache before each test so any
    UPDATE on `tenants.config` is immediately visible."""
    from app import tenant_config_cache

    tenant_config_cache._reset_for_tests()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _pool() -> AsyncIterator[asyncpg.Pool]:
    """Initialise the app's asyncpg pool once for the test session.

    Event loop is session-scoped (see pyproject.toml `asyncio_default_*`).
    `autouse=True` ensures every test has the pool ready even if it
    doesn't request the fixture explicitly (e.g. direct
    `require_api_token` calls that hit `get_pool()` internally).
    """
    settings = get_settings()
    pool = await init_pool(settings)
    # Tests use httpx ASGITransport which does NOT trigger app lifespan,
    # so we replicate the lifespan's app.state setup here.
    ruleset, enricher = init_runtime(settings)
    app.state.ruleset = ruleset
    app.state.enricher = enricher
    yield pool
    await close_pool()


async def set_test_tenant_id(conn: asyncpg.Connection, tenant_id: int) -> None:
    """Test helper: set `app.tenant_id` session-scoped on this connection.

    Phase 5D.2: with the runtime role switched to `riskd_app_login`,
    every INSERT into a tenant-scoped table is subject to RLS WITH CHECK
    (the policy USING clause acts as WITH CHECK by default in Postgres).
    Test fixtures that issue raw `INSERT INTO customers/users/...` must
    set `app.tenant_id` BEFORE the INSERT or the policy rejects.

    Production endpoints use `set_tenant_id` (in app/db.py) with
    is_local=True inside a request transaction. Tests typically run in
    asyncpg autocommit, so we use is_local=False — the parameter
    persists for the rest of the session (which is the connection's
    lifetime in the pool). Subsequent test queries on the same db_conn
    see the same app.tenant_id; tests that need to switch tenants
    mid-flow call this helper again.
    """
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_id))


@asynccontextmanager
async def with_test_tenant_context(conn: asyncpg.Connection, tenant_id: int) -> AsyncIterator[None]:
    """Phase 5D.2 helper: temporarily switch `app.tenant_id` on the
    given connection, then restore the prior context on exit. Use for
    cross-tenant verification reads in tests that need to count or
    inspect rows belonging to a different tenant than the one the
    fixture session is currently scoped to."""
    prev_raw = await conn.fetchval("SELECT current_setting('app.tenant_id', true)")
    await set_test_tenant_id(conn, tenant_id)
    try:
        yield
    finally:
        prev_int = int(prev_raw) if prev_raw else 0
        await set_test_tenant_id(conn, prev_int)


async def reset_test_tenant_id(conn: asyncpg.Connection) -> None:
    """Test helper: reset `app.tenant_id` to the sentinel '0' on this
    connection. Matches `app/db.py:_pool_setup`, which initialises the
    same sentinel on every fresh pooled connection.

    Sentinel '0' is safe: no tenant has id 0, so RLS-protected reads
    return empty and INSERTs fail WITH CHECK. Prevents bleed-through
    to subsequent tests that share the same pooled connection."""
    await conn.execute("SELECT set_config('app.tenant_id', '0', false)")


@pytest_asyncio.fixture
async def db_conn(_pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Connection]:
    """Single connection from the shared pool. NOT auto-transactional —
    callers manage their own transactions for seed/cleanup.

    Phase 5D.2: `app.tenant_id` is reset to '0' on acquire so a
    stale value from a prior pool tenant doesn't leak in. Tests that
    insert into tenant-scoped tables must call `set_test_tenant_id`
    before INSERT (or wrap in `async with conn.transaction(): set_tenant_id`)."""
    async with _pool.acquire() as conn:
        await reset_test_tenant_id(conn)
        try:
            yield conn
        finally:
            await reset_test_tenant_id(conn)


@pytest_asyncio.fixture
async def seeded_tenant(db_conn: asyncpg.Connection) -> AsyncIterator[int]:
    """Insert a tenant; cleanup all dependent rows on teardown.

    Phase 5D.2: after inserting `tenants` (which is NOT RLS-enabled),
    set `app.tenant_id` session-scoped to the new id so subsequent
    INSERTs on tenant-scoped tables succeed under RLS WITH CHECK.

    Phase 6B: seeds `allowed_currencies = ["USD", "CAD"]` so the
    project-default-CAD switch (6B.1) does NOT break the ~20 test
    files that POST USD payloads against a default-configured
    tenant. Tests that explicitly want a single-currency tenant
    override via `_set_allowed_currencies` (test_currency_validation
    pattern). Value-caps are not seeded — `resolve_value_caps`
    falls back to `DEFAULT_VALUE_CAPS["CAD"]` for both currencies,
    which is correct for tests that don't exercise currency-specific
    thresholds. Tests that DO exercise currency-specific value_caps
    seed `tenants.config` explicitly.

    FKs are non-CASCADE in the migration (deliberate — prevents
    accidental bulk deletes in production). The fixture compensates
    by deleting in reverse-FK order so tests don't have to.
    """
    tenant_id: int = await db_conn.fetchval(
        """
        INSERT INTO tenants (name, config)
        VALUES ($1, $2::jsonb)
        RETURNING id
        """,
        f"test-tenant-{secrets.token_hex(4)}",
        '{"allowed_currencies": ["USD", "CAD"]}',
    )
    await set_test_tenant_id(db_conn, tenant_id)
    yield tenant_id
    # Cleanup runs as superuser-equivalent role only because session has
    # app.tenant_id set; under riskd_app_login DELETE statements on
    # tenant-scoped tables must execute under that tenant's RLS view.
    await _cleanup_tenant(db_conn, tenant_id)


@pytest_asyncio.fixture
async def seeded_api_token(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> AsyncIterator[tuple[str, int]]:
    """Insert a tenant-role API token; yield (plaintext_token, tenant_id).

    Explicit cleanup (DELETE on api_tokens) runs before the tenant teardown
    deletes the parent row. Don't rely on FK CASCADE — be explicit so test
    isolation doesn't depend on schema-level cascade behaviour.
    """
    plaintext = secrets.token_urlsafe(24)
    token_hash = _hash_token(plaintext)
    await db_conn.execute(
        "INSERT INTO api_tokens (tenant_id, token_hash, role) VALUES ($1, $2, $3)",
        seeded_tenant,
        token_hash,
        "tenant",
    )
    yield plaintext, seeded_tenant
    await db_conn.execute("DELETE FROM api_tokens WHERE token_hash = $1", token_hash)


@pytest_asyncio.fixture
async def seeded_admin_token(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> AsyncIterator[tuple[str, int]]:
    """Insert an admin-role API token; yield (plaintext_token, tenant_id)."""
    plaintext = secrets.token_urlsafe(24)
    token_hash = _hash_token(plaintext)
    await db_conn.execute(
        "INSERT INTO api_tokens (tenant_id, token_hash, role) VALUES ($1, $2, $3)",
        seeded_tenant,
        token_hash,
        "admin",
    )
    yield plaintext, seeded_tenant
    await db_conn.execute("DELETE FROM api_tokens WHERE token_hash = $1", token_hash)


@pytest_asyncio.fixture
async def client(_pool: asyncpg.Pool) -> AsyncIterator[AsyncClient]:
    """HTTP client with auth dependency overridden to a synthetic AuthContext.
    Route tests don't need to think about auth."""
    app.dependency_overrides[require_api_token] = lambda: AuthContext(tenant_id=1, role="tenant")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        # Scoped removal (not clear()) so sibling fixtures that layer their
        # own overrides don't get wiped.
        app.dependency_overrides.pop(require_api_token, None)


@pytest_asyncio.fixture
async def unauth_client(_pool: asyncpg.Pool) -> AsyncIterator[AsyncClient]:
    """HTTP client without dependency overrides — exercises real auth."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@asynccontextmanager
async def seeded_ip_enrichment(
    conn: asyncpg.Connection,
    ip: str,
    *,
    country: str = "US",
    asn_org: str = "Comcast",
    is_cloud: bool = False,
    is_datacenter: bool = False,
    is_vpn: bool = False,
    is_proxy: bool = False,
    is_tor: bool = False,
    fh_level1: bool = False,
    fh_level2: bool = False,
    threat: str | None = None,
    lat: float | None = 38.0,
    lon: float | None = -77.0,
) -> AsyncIterator[str]:
    """Async context-manager that seeds an `ip_enrichment` row with the
    given flags and DELETEs it on exit.

    `ip_enrichment` is intentionally global (no RLS) per the schema
    comment in 0001_initial.py, so cleanup is the caller's
    responsibility — using this helper removes the per-test try/finally
    boilerplate and the cross-test pollution risk.

    Defaults match a clean residential US IP (Comcast, non-cloud,
    non-datacenter, no threat flags). Override only the flags relevant
    to the test scenario.
    """
    await conn.execute(
        """
        INSERT INTO ip_enrichment (
            ip, country, asn_org, is_cloud, is_datacenter,
            is_vpn, is_proxy, is_tor, fh_level1, fh_level2,
            threat, lat, lon
        )
        VALUES (
            $1::inet, $2, $3, $4, $5,
            $6, $7, $8, $9, $10,
            $11, $12, $13
        )
        ON CONFLICT (ip) DO UPDATE SET
            country = EXCLUDED.country,
            asn_org = EXCLUDED.asn_org,
            is_cloud = EXCLUDED.is_cloud,
            is_datacenter = EXCLUDED.is_datacenter,
            is_vpn = EXCLUDED.is_vpn,
            is_proxy = EXCLUDED.is_proxy,
            is_tor = EXCLUDED.is_tor,
            fh_level1 = EXCLUDED.fh_level1,
            fh_level2 = EXCLUDED.fh_level2,
            threat = EXCLUDED.threat,
            lat = EXCLUDED.lat,
            lon = EXCLUDED.lon,
            updated_at = now()
        """,
        ip,
        country,
        asn_org,
        is_cloud,
        is_datacenter,
        is_vpn,
        is_proxy,
        is_tor,
        fh_level1,
        fh_level2,
        threat,
        lat,
        lon,
    )
    try:
        yield ip
    finally:
        await conn.execute("DELETE FROM ip_enrichment WHERE ip = $1::inet", ip)


async def seed_customer_with_baseline(
    conn: asyncpg.Connection,
    tenant_id: int,
    *,
    external_id: str,
    first_seen_days_ago: int = 90,
    total_shipments: int = 0,
    flagged_count: int = 0,
    fraud_confirmed_count: int = 0,
    baseline_kwargs: dict[str, Any] | None = None,
) -> int:
    """Seed a customer row plus its customer_baselines row.

    Shared across integration tests that need a customer with a
    pre-existing baseline state (case-1, case-2, Layer 2 integration
    tests). `baseline_kwargs` accepts every customer_baselines column
    as a Python value; JSONB columns accept dict; date/timestamp accept
    None and default to today / NULL. `decay_anchor_date` defaults to
    Python's `date.today()` so it matches `build_context`'s default
    `as_of` (avoids the cross-TZ decay drift surfaced in 2C.3).
    """
    from datetime import date

    customer_id: int = await conn.fetchval(
        """
        INSERT INTO customers (
            tenant_id, external_id, first_seen, total_shipments,
            flagged_count, fraud_confirmed_count
        )
        VALUES (
            $1, $2, now() - make_interval(days => $3), $4, $5, $6
        )
        RETURNING id
        """,
        tenant_id,
        external_id,
        first_seen_days_ago,
        total_shipments,
        flagged_count,
        fraud_confirmed_count,
    )

    bk = baseline_kwargs or {}
    await conn.execute(
        """
        INSERT INTO customer_baselines (
            tenant_id, customer_id,
            ip_stats, ip_netblock_stats, ip_asn_stats,
            country_stats, origin_ip_country_stats,
            origin_stats, dest_stats, lane_stats,
            ip_type_hist, hour_hist, weekday_hist, channel_hist,
            value_n, value_mean, value_m2,
            cadence_n, cadence_mean_h, cadence_m2_h,
            last_booking_ts, last_booking_lat, last_booking_lon,
            last_booking_country, decay_anchor_date
        )
        VALUES (
            $1, $2,
            $3::jsonb, $4::jsonb, $5::jsonb,
            $6::jsonb, $7::jsonb,
            $8::jsonb, $9::jsonb, $10::jsonb,
            $11::jsonb, $12::jsonb, $13::jsonb, $14::jsonb,
            $15, $16, $17,
            $18, $19, $20,
            $21, $22, $23,
            $24, $25
        )
        """,
        tenant_id,
        customer_id,
        json.dumps(bk.get("ip_stats", {})),
        json.dumps(bk.get("ip_netblock_stats", {})),
        json.dumps(bk.get("ip_asn_stats", {})),
        json.dumps(bk.get("country_stats", {})),
        json.dumps(bk.get("origin_ip_country_stats", {})),
        json.dumps(bk.get("origin_stats", {})),
        json.dumps(bk.get("dest_stats", {})),
        json.dumps(bk.get("lane_stats", {})),
        json.dumps(bk.get("ip_type_hist", {})),
        json.dumps(bk.get("hour_hist", {})),
        json.dumps(bk.get("weekday_hist", {})),
        json.dumps(bk.get("channel_hist", {})),
        float(bk.get("value_n", 0.0)),
        float(bk.get("value_mean", 0.0)),
        float(bk.get("value_m2", 0.0)),
        float(bk.get("cadence_n", 0.0)),
        float(bk.get("cadence_mean_h", 0.0)),
        float(bk.get("cadence_m2_h", 0.0)),
        bk.get("last_booking_ts"),
        bk.get("last_booking_lat"),
        bk.get("last_booking_lon"),
        bk.get("last_booking_country"),
        bk.get("decay_anchor_date") or date.today(),
    )
    return customer_id


@asynccontextmanager
async def create_tenant_with_token(
    db_conn: asyncpg.Connection,
) -> AsyncIterator[tuple[str, int]]:
    """Context-manager helper that creates a second tenant + api_token and
    cascade-cleans on exit. Use inside tests that need >1 tenant (e.g.
    cross-tenant isolation checks).

    Phase 5D.2: sets `app.tenant_id` to the new tenant's id while the
    block is open (so dependent inserts succeed under RLS WITH CHECK);
    on exit, restores the prior `app.tenant_id` after cleanup so the
    outer fixture's teardown sees its own tenant's rows.
    """
    prev_raw = await db_conn.fetchval("SELECT current_setting('app.tenant_id', true)")
    prev_int = int(prev_raw) if prev_raw else 0
    # Phase 6B: seed allowed_currencies = ["USD", "CAD"] to match the
    # seeded_tenant fixture default; cross-tenant tests don't care
    # about currency, just isolation.
    tenant_id: int = await db_conn.fetchval(
        """
        INSERT INTO tenants (name, config)
        VALUES ($1, $2::jsonb)
        RETURNING id
        """,
        f"test-tenant-{secrets.token_hex(4)}",
        '{"allowed_currencies": ["USD", "CAD"]}',
    )
    await set_test_tenant_id(db_conn, tenant_id)
    plaintext = secrets.token_urlsafe(24)
    token_hash = _hash_token(plaintext)
    await db_conn.execute(
        "INSERT INTO api_tokens (tenant_id, token_hash, role) VALUES ($1, $2, $3)",
        tenant_id,
        token_hash,
        "tenant",
    )
    try:
        yield plaintext, tenant_id
    finally:
        # Cleanup needs RLS visibility into this tenant's rows.
        await set_test_tenant_id(db_conn, tenant_id)
        await _cleanup_tenant(db_conn, tenant_id)
        # Restore prior tenant context so the outer fixture's teardown
        # can DELETE its own rows.
        await set_test_tenant_id(db_conn, prev_int)


@asynccontextmanager
async def create_extra_tenant(
    db_conn: asyncpg.Connection, name_prefix: str = "extra"
) -> AsyncIterator[int]:
    """Phase 5D.2 helper: create an extra tenant + set `app.tenant_id`
    session-scoped to it. Use inside tests that need to seed dependent
    rows under a second tenant alongside `seeded_tenant`. On exit
    cascades all tenant-scoped rows AND restores the prior tenant
    context so the outer fixture's teardown can see its own rows.
    """
    prev_raw = await db_conn.fetchval("SELECT current_setting('app.tenant_id', true)")
    prev_int = int(prev_raw) if prev_raw else 0
    # Phase 6B: seed allowed_currencies = ["USD", "CAD"] to match the
    # seeded_tenant fixture default; multi-tenant tests don't care
    # about currency.
    tenant_id: int = await db_conn.fetchval(
        """
        INSERT INTO tenants (name, config)
        VALUES ($1, $2::jsonb)
        RETURNING id
        """,
        f"{name_prefix}-{secrets.token_hex(4)}",
        '{"allowed_currencies": ["USD", "CAD"]}',
    )
    await set_test_tenant_id(db_conn, tenant_id)
    try:
        yield tenant_id
    finally:
        await set_test_tenant_id(db_conn, tenant_id)
        await _cleanup_tenant(db_conn, tenant_id)
        await set_test_tenant_id(db_conn, prev_int)
