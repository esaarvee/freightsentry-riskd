"""asyncpg connection pool + per-request connection helpers.

Pool is created at app lifespan via `init_pool`, drained on shutdown via
`close_pool`. Request handlers acquire a connection via `get_conn()` and
set the RLS tenant context via `set_tenant_id` inside a transaction.

`set_tenant_id` uses `set_config(name, value, is_local=true)` — the SQL
function equivalent of `SET LOCAL`, which accepts parameterised values
and is transaction-scoped (clears automatically on commit/rollback).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.config import Settings

_pool: asyncpg.Pool | None = None


async def init_pool(settings: Settings) -> asyncpg.Pool:
    """Create the global asyncpg pool. Idempotent guard against double-init."""
    global _pool
    if _pool is not None:
        msg = "asyncpg pool already initialised"
        raise RuntimeError(msg)
    _pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=10,
    )
    return _pool


async def close_pool() -> None:
    """Drain the global pool. Safe to call when already closed."""
    global _pool
    if _pool is None:
        return
    await _pool.close()
    _pool = None


def get_pool() -> asyncpg.Pool:
    """Return the live pool. Raises if lifespan setup hasn't run."""
    if _pool is None:
        msg = "asyncpg pool not initialised — is the app lifespan running?"
        raise RuntimeError(msg)
    return _pool


@asynccontextmanager
async def get_conn() -> AsyncIterator[asyncpg.Connection]:
    """Acquire a connection from the pool for the duration of the context."""
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn


async def set_tenant_id(conn: asyncpg.Connection, tenant_id: int) -> None:
    """Set the RLS session variable for this transaction.

    Must be called inside an open transaction; `set_config(..., true)` is
    transaction-scoped and clears on commit/rollback.
    """
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
