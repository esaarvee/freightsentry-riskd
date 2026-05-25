# Python gotchas

## `asyncio.gather` only surfaces the first exception

`gather` collects results from concurrent coroutines, but if multiple coroutines raise, only the first exception bubbles up — the others are silently discarded (or never awaited). Acceptable for parallel reads where partial failure should fast-fail the whole request. **Never** use `gather` for fire-and-forget writes or background telemetry.

## `@lru_cache` + secret rotation = stale hashes

`hmac_hex(value, secret)` decorated with `@lru_cache` caches results keyed on the secret bytes. On secret rotation, old cache entries stay alive until eviction or process restart. Drop the decorator entirely — per-request cost is negligible at 100 TPS.

## `AsyncMock` vs `MagicMock` for async targets

`MagicMock()` returns a non-awaitable when called. Awaiting it produces an opaque `TypeError` or — worse — silently passes a broken test. Use `AsyncMock` for any `async def` target. Use `spec=Class` to make missing-method-stubs raise instead of returning a default mock.

## FastAPI dependency overrides only persist within the same `TestClient` / `app` instance

Tests sharing a module-scoped `client` fixture share the same `app.dependency_overrides` dict. A test that registers an override and doesn't clean up leaks into sibling tests. Either function-scope the client fixture or pair every override with explicit removal in teardown.

## Pydantic v2: `model_validate` not `parse_obj`

Pydantic v2 renamed `parse_obj` → `model_validate`, `dict()` → `model_dump`, `json()` → `model_dump_json`. The v1 names raise `DeprecationWarning` at import time but otherwise work — they will be removed in v3. Use the v2 names.

## `from __future__ import annotations` + Pydantic v2 ForwardRef resolution

With `from __future__ import annotations`, all annotations become string-form `ForwardRef`s resolved lazily. Pydantic v2 resolves them at model-build time, but fails if the referenced types aren't in scope yet (circular references, types defined later in the module, types from later imports). Either drop the future import for Pydantic-model modules, or call `Model.model_rebuild()` after the referenced names are defined.

## `datetime.now(tz=UTC)` not `datetime.utcnow()`

`utcnow()` returns a naive datetime; comparisons against `timestamptz` Postgres columns mix tz-aware and tz-naive, producing wrong results silently. Always tz-aware: `datetime.now(tz=UTC)` (Python 3.11+: `from datetime import UTC`).
