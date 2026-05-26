"""Shared test fixtures.

Pool initialised once per session; tests share the same asyncpg pool the
running app would use. Per-test seed cleanup is explicit via the
`seeded_tenant` / `seeded_api_token` fixtures (commit + delete rather
than per-test rollback, because the auth dependency in app/auth.py
acquires a SEPARATE connection from the same pool and won't see
uncommitted transactional data).
"""

import secrets
from collections.abc import AsyncIterator

import asyncpg
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth import AuthContext, _hash_token, require_api_token
from app.config import get_settings
from app.db import close_pool, init_pool
from app.main import app


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
    yield pool
    await close_pool()


@pytest_asyncio.fixture
async def db_conn(_pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Connection]:
    """Single connection from the shared pool. NOT auto-transactional —
    callers manage their own transactions for seed/cleanup."""
    async with _pool.acquire() as conn:
        yield conn


@pytest_asyncio.fixture
async def seeded_tenant(db_conn: asyncpg.Connection) -> AsyncIterator[int]:
    """Insert a tenant; cleanup all dependent rows on teardown.

    FKs are non-CASCADE in the migration (deliberate — prevents accidental
    bulk deletes in production). The fixture compensates by deleting in
    reverse-FK order so tests don't have to.
    """
    tenant_id: int = await db_conn.fetchval(
        "INSERT INTO tenants (name) VALUES ($1) RETURNING id",
        f"test-tenant-{secrets.token_hex(4)}",
    )
    yield tenant_id
    for table in (
        "feedback",
        "decisions",
        "customer_baselines",
        "shipments",
        "users",
        "customers",
        "enterprises",
        "api_tokens",
        "app_users",
    ):
        await db_conn.execute(f"DELETE FROM {table} WHERE tenant_id = $1", tenant_id)
    await db_conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)


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
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=1, role="tenant"
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        # Scoped removal (not clear()) so sibling fixtures that layer their
        # own overrides don't get wiped.
        app.dependency_overrides.pop(require_api_token, None)


@pytest_asyncio.fixture
async def unauth_client(_pool: asyncpg.Pool) -> AsyncIterator[AsyncClient]:
    """HTTP client without dependency overrides — exercises real auth."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
