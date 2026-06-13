"""In-process TTL cache around `load_tenant_config`.

Replaces the per-request DB fetch with a 60-second cache keyed by
`tenant_id`. The cache is a singleton at module level; multi-worker
uvicorn deployments each have their own copy (acceptable because the
TTL bounds divergence to 60s).

Concurrency contract: reads are lock-free dict lookups; concurrent
cache misses for the SAME `tenant_id` serialize on a per-tenant
`asyncio.Lock` so the underlying `load_tenant_config` DB fetch fires
exactly once per miss. Misses for different `tenant_id`s do NOT
serialize against each other.

Invalidation: TTL-only for v1. A config write via
`scripts/tenant_onboard.py` (or future admin write endpoints) takes
up to 60s to propagate to all workers. Documented in
`.ai/decisions.md` (cache section); surface to operators in the
onboarding script output.

LookupError on missing tenant is NOT cached — the caller's next
attempt retries the DB.
"""

from __future__ import annotations

import asyncio
import time

import asyncpg
import structlog

from app.tenant_config import TenantConfig, load_tenant_config


def _now() -> float:
    """Module-level seam so tests can replace it without monkeypatching
    the global `time` module (which would poison asyncio's internal
    timeouts that also call `time.monotonic`)."""
    return time.monotonic()


_log = structlog.get_logger(__name__)

TTL_SECONDS: float = 60.0

_entries: dict[int, tuple[TenantConfig, float]] = {}
_locks: dict[int, asyncio.Lock] = {}


def _get_per_tenant_lock(tenant_id: int) -> asyncio.Lock:
    """Return the per-tenant Lock, creating it via `dict.setdefault` if
    absent. `dict.setdefault` is atomic under CPython's GIL: two
    concurrent coroutines may each construct a new Lock(), but only
    the one stored in the dict is returned to both; the other is GC'd.
    Both coroutines then synchronize on the same Lock instance.
    Locks are never evicted — bounded by tenant count (<<1000)."""
    return _locks.setdefault(tenant_id, asyncio.Lock())


async def load_tenant_config_cached(
    conn: asyncpg.Connection,
    tenant_id: int,
) -> TenantConfig:
    """Cache-fronted load_tenant_config. Hit returns the cached value
    if within TTL; miss serializes per-tenant on the miss path so 10
    concurrent requests for the same tenant_id result in 1 DB load."""
    cached = _entries.get(tenant_id)
    if cached is not None:
        config, loaded_at = cached
        if (_now() - loaded_at) < TTL_SECONDS:
            _log.info(
                "tenant_config.cache.hit",
                tenant_id=tenant_id,
                metric=True,
            )
            return config

    lock = _get_per_tenant_lock(tenant_id)
    async with lock:
        cached = _entries.get(tenant_id)
        if cached is not None:
            config, loaded_at = cached
            if (_now() - loaded_at) < TTL_SECONDS:
                _log.info(
                    "tenant_config.cache.hit",
                    tenant_id=tenant_id,
                    metric=True,
                )
                return config

        config = await load_tenant_config(conn, tenant_id)
        _entries[tenant_id] = (config, _now())
        _log.info(
            "tenant_config.cache.miss",
            tenant_id=tenant_id,
            cache_size=len(_entries),
            metric=True,
        )
        return config


def _reset_for_tests() -> None:
    """Drop all cached entries + per-tenant locks. Tests-only; not
    referenced by production code paths."""
    _entries.clear()
    _locks.clear()
