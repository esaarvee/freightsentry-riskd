"""Unit tests for `app.tenant_config_cache.load_tenant_config_cached`.

Covers:
- Hit returns the cached value on the second call within TTL (one DB load).
- Miss after TTL expiry re-fetches (two DB loads).
- LookupError propagates and is NOT cached (next call retries the DB).
- Concurrent misses for the same tenant_id serialize via the per-tenant
  Lock — 10 concurrent gather() calls produce exactly 1 DB load.
- Concurrent misses for DIFFERENT tenant_ids are genuinely concurrent
  inside the loader (barrier-based proof that a global lock would
  deadlock; per-tenant locks let all 10 enter their own miss paths).
- TTL boundary at elapsed=59.999s hits (strict less-than).
- TTL boundary at elapsed=60.0s misses (strict less-than fails).
- Double-checked-locking inner re-check returns cached value without
  re-loading when another coroutine populated `_entries` while we
  awaited the per-tenant lock.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import pytest

from app import tenant_config_cache
from app.tenant_config import TenantConfig
from app.tenant_config_cache import load_tenant_config_cached


def _config(tenant_id: int = 1) -> TenantConfig:
    return TenantConfig(
        tenant_id=tenant_id,
        config_version=0,
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        updated_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    tenant_config_cache._reset_for_tests()
    yield
    tenant_config_cache._reset_for_tests()


async def test_hit_returns_cached_without_second_db_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = AsyncMock(return_value=_config(tenant_id=42))
    monkeypatch.setattr(tenant_config_cache, "load_tenant_config", loader)
    conn = AsyncMock(spec=asyncpg.Connection)

    first = await load_tenant_config_cached(conn, 42)
    second = await load_tenant_config_cached(conn, 42)

    assert first is second
    assert loader.await_count == 1


async def test_miss_after_ttl_expiry_refetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = AsyncMock(side_effect=[_config(tenant_id=42), _config(tenant_id=42)])
    monkeypatch.setattr(tenant_config_cache, "load_tenant_config", loader)
    conn = AsyncMock(spec=asyncpg.Connection)

    # First call: cache empty → no hit-checks; one _now() call at store
    # time (writes (config, 100.0)).
    # Second call: hit-check (outer + inner under lock) sees t=200.0;
    # 200-100=100s > TTL → miss; one _now() call at store time (writes
    # (config, 200.0)). Four _now() consumptions total.
    monotonic_values = iter([100.0, 200.0, 200.0, 200.0])
    monkeypatch.setattr(
        tenant_config_cache,
        "_now",
        lambda: next(monotonic_values),
    )

    await load_tenant_config_cached(conn, 42)
    await load_tenant_config_cached(conn, 42)

    assert loader.await_count == 2


async def test_lookup_error_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = AsyncMock(side_effect=[LookupError("nope"), _config(tenant_id=42)])
    monkeypatch.setattr(tenant_config_cache, "load_tenant_config", loader)
    conn = AsyncMock(spec=asyncpg.Connection)

    with pytest.raises(LookupError):
        await load_tenant_config_cached(conn, 42)

    config = await load_tenant_config_cached(conn, 42)
    assert config.tenant_id == 42
    assert loader.await_count == 2


async def test_concurrent_misses_same_tenant_serialize_to_one_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """10 concurrent gather() calls for the same tenant_id must result in
    exactly 1 underlying DB load. The per-tenant Lock around the miss
    path is what enforces this."""
    load_count = 0

    async def counting_loader(_conn: Any, tenant_id: int) -> TenantConfig:
        nonlocal load_count
        load_count += 1
        await asyncio.sleep(0)
        return _config(tenant_id=tenant_id)

    monkeypatch.setattr(tenant_config_cache, "load_tenant_config", counting_loader)
    conn = AsyncMock(spec=asyncpg.Connection)

    results = await asyncio.gather(*(load_tenant_config_cached(conn, 7) for _ in range(10)))

    assert load_count == 1
    assert len(results) == 10
    assert all(r.tenant_id == 7 for r in results)


async def test_concurrent_misses_different_tenants_do_not_serialize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """10 concurrent calls for 10 distinct tenant_ids should ALL be in
    flight inside the loader at the same time — proving the per-tenant
    Lock is genuinely per-tenant (a global Lock would deadlock on the
    barrier; per-tenant locks let all 10 enter their own miss paths
    concurrently).

    Without the barrier, the assertion `load_count == 10` would also pass
    under a single global Lock (each distinct cache key still produces a
    miss). The barrier is what distinguishes "10 distinct loads" from
    "10 concurrent loads"."""
    barrier_count = 0
    barrier_event = asyncio.Event()
    release_event = asyncio.Event()
    load_count = 0

    async def barrier_loader(_conn: asyncpg.Connection, tenant_id: int) -> TenantConfig:
        nonlocal barrier_count, load_count
        load_count += 1
        barrier_count += 1
        if barrier_count == 10:
            barrier_event.set()
        await barrier_event.wait()
        await release_event.wait()
        return _config(tenant_id=tenant_id)

    monkeypatch.setattr(tenant_config_cache, "load_tenant_config", barrier_loader)
    conn = AsyncMock(spec=asyncpg.Connection)

    gather_task = asyncio.gather(*(load_tenant_config_cached(conn, tid) for tid in range(100, 110)))
    await barrier_event.wait()
    assert barrier_count == 10, "all 10 misses must be in flight concurrently"
    release_event.set()
    results = await gather_task

    assert load_count == 10
    assert sorted(r.tenant_id for r in results) == list(range(100, 110))


async def test_ttl_boundary_just_below_ttl_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At elapsed = 59.999s (< 60s strict less-than), the cache hits."""
    loader = AsyncMock(side_effect=[_config(tenant_id=42), _config(tenant_id=42)])
    monkeypatch.setattr(tenant_config_cache, "load_tenant_config", loader)
    conn = AsyncMock(spec=asyncpg.Connection)

    monotonic_values = iter([100.0, 159.999])
    monkeypatch.setattr(
        tenant_config_cache,
        "_now",
        lambda: next(monotonic_values),
    )

    await load_tenant_config_cached(conn, 42)
    await load_tenant_config_cached(conn, 42)

    assert loader.await_count == 1


async def test_ttl_boundary_at_exact_ttl_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At elapsed = 60.0s exactly, the strict less-than comparison
    `(_now() - loaded_at) < TTL_SECONDS` fails → cache misses, refetch."""
    loader = AsyncMock(side_effect=[_config(tenant_id=42), _config(tenant_id=42)])
    monkeypatch.setattr(tenant_config_cache, "load_tenant_config", loader)
    conn = AsyncMock(spec=asyncpg.Connection)

    monotonic_values = iter([100.0, 160.0, 160.0, 160.0])
    monkeypatch.setattr(
        tenant_config_cache,
        "_now",
        lambda: next(monotonic_values),
    )

    await load_tenant_config_cached(conn, 42)
    await load_tenant_config_cached(conn, 42)

    assert loader.await_count == 2


async def test_double_checked_lock_inner_recheck_returns_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DCL inner re-check (inside the per-tenant lock) MUST return
    the cached value when another coroutine populated `_entries` while
    we were waiting on the lock — not call the loader again. Engineered
    by holding the first loader call open via Event, letting the second
    coroutine attempt and block on the lock; once the first releases,
    the second observes the populated cache and returns without
    reloading."""
    load_count = 0
    first_started = asyncio.Event()
    first_release = asyncio.Event()

    async def gated_loader(_conn: asyncpg.Connection, tenant_id: int) -> TenantConfig:
        nonlocal load_count
        load_count += 1
        first_started.set()
        await first_release.wait()
        return _config(tenant_id=tenant_id)

    monkeypatch.setattr(tenant_config_cache, "load_tenant_config", gated_loader)
    conn = AsyncMock(spec=asyncpg.Connection)

    first_task = asyncio.create_task(load_tenant_config_cached(conn, 7))
    await first_started.wait()

    second_task = asyncio.create_task(load_tenant_config_cached(conn, 7))
    await asyncio.sleep(0)

    assert load_count == 1, "second coroutine must NOT have entered the loader yet"

    first_release.set()
    first_result = await first_task
    second_result = await second_task

    assert load_count == 1, "DCL inner re-check failed: loader was called twice instead of once"
    assert first_result is second_result
