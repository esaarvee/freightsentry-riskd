"""Integration coverage for the 5B in-process tenant-config cache under
real DB load. The unit tests in `tests/unit/test_tenant_config_cache.py`
mock the loader; this file exercises the production code path
end-to-end with a real asyncpg connection per coroutine.

Contracts:
- 10 concurrent requests for the same tenant_id result in exactly one
  DB SELECT against `tenants` (the underlying loader fires once, the
  remaining 9 coroutines hit the inner cache re-check inside the
  per-tenant Lock).
- 10 concurrent requests for 10 distinct tenant_ids result in exactly
  10 DB SELECTs (per-tenant locks don't over-serialize). The seeding
  + cleanup connections are explicitly released before/after the
  10-way gather so the pool (max_size=10) is not starved.
"""

from __future__ import annotations

import asyncio
import secrets

import asyncpg
import pytest

from app import tenant_config, tenant_config_cache
from app.tenant_config_cache import load_tenant_config_cached


@pytest.fixture(autouse=True)
def _explicit_cache_reset() -> None:
    """Belt-and-suspenders alongside `tests/conftest.py::_reset_tenant_config_cache`;
    retained so this file's contract is verifiable in isolation if the
    parent conftest fixture ever moves or loses autouse."""
    tenant_config_cache._reset_for_tests()


async def _fetch_via_pool(pool: asyncpg.Pool, tenant_id: int) -> tenant_config.TenantConfig:
    """Acquire a fresh connection per coroutine — mirrors the per-request
    pool acquisition in production endpoints."""
    async with pool.acquire() as conn:
        return await load_tenant_config_cached(conn, tenant_id)


async def test_ten_concurrent_same_tenant_produces_one_db_load(
    seeded_tenant: int,
    _pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end concurrent-load proof: 10 coroutines for the same
    tenant_id race the cache miss; the underlying `load_tenant_config`
    fires exactly once. The remaining 9 coroutines hit the inner DCL
    re-check inside the per-tenant Lock and return the cached value.

    Test 1 tolerates the `seeded_tenant` fixture holding a pool slot
    during the gather (pool max=10, fixture holds 1, 9 free) because
    only the first coroutine actually needs a connection — the other
    9 short-circuit on the inner cache re-check. Test 2 below cannot
    tolerate the same shape because all 10 coroutines must execute
    the loader, so it explicitly releases its seed connection before
    fan-out."""
    load_count = 0
    real_loader = tenant_config.load_tenant_config

    async def counting_loader(
        conn: asyncpg.Connection, tenant_id: int
    ) -> tenant_config.TenantConfig:
        nonlocal load_count
        load_count += 1
        return await real_loader(conn, tenant_id)

    monkeypatch.setattr(tenant_config_cache, "load_tenant_config", counting_loader)

    results = await asyncio.gather(*(_fetch_via_pool(_pool, seeded_tenant) for _ in range(10)))

    assert load_count == 1, (
        f"expected exactly 1 DB load for 10 concurrent same-tenant requests; got {load_count}"
    )
    assert len(results) == 10
    assert all(r.tenant_id == seeded_tenant for r in results)


async def test_ten_concurrent_distinct_tenants_produce_ten_db_loads(
    _pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """10 concurrent requests for 10 distinct tenant_ids must each hit
    the loader (per-tenant locks don't serialize across tenants).
    Seeding + cleanup connections are explicitly released before/after
    the 10-way gather so the pool (max_size=10) has all slots free
    during fan-out."""
    extra_tenant_ids: list[int] = []
    name_suffix = secrets.token_hex(4)

    async with _pool.acquire() as seed_conn:
        for i in range(10):
            tid: int = await seed_conn.fetchval(
                "INSERT INTO tenants (name) VALUES ($1) RETURNING id",
                f"concurrency-test-{name_suffix}-{i}",
            )
            extra_tenant_ids.append(tid)

    try:
        load_count = 0
        real_loader = tenant_config.load_tenant_config

        async def counting_loader(
            conn: asyncpg.Connection, tenant_id: int
        ) -> tenant_config.TenantConfig:
            nonlocal load_count
            load_count += 1
            return await real_loader(conn, tenant_id)

        monkeypatch.setattr(tenant_config_cache, "load_tenant_config", counting_loader)

        results = await asyncio.gather(*(_fetch_via_pool(_pool, tid) for tid in extra_tenant_ids))

        assert load_count == 10, (
            f"expected exactly 10 DB loads for 10 distinct concurrent tenants; got {load_count}"
        )
        assert sorted(r.tenant_id for r in results) == sorted(extra_tenant_ids)
    finally:
        async with _pool.acquire() as cleanup_conn:
            await cleanup_conn.execute("DELETE FROM tenants WHERE id = ANY($1)", extra_tenant_ids)
