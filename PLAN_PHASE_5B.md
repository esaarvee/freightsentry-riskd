# PLAN_PHASE_5B — Tenant-config in-process cache

Batch 5B of Phase 5. Replaces the per-request `load_tenant_config` DB call with a TTL-bounded in-process cache. Reduces query load ahead of 5D's role transition.

## Pre-plan verification findings

- **`load_tenant_config` signature confirmed**: `async def load_tenant_config(conn: asyncpg.Connection, tenant_id: int) -> TenantConfig` at `app/tenant_config.py:204-264`. Raises `LookupError` on missing tenant (line 240).
- **Five call sites** all at request entry: `app/api/booking.py:54`, `app/api/modification.py:60`, `app/api/feedback.py:115`, `app/api/admin.py:98` and `:185`. The admin and feedback handlers load it for "shape consistency" (intentionally unused) — they MUST continue to call the cached version to keep cache stats accurate and to keep the consistency contract.
- **`config_version` lives INSIDE the JSONB** (`tenants.config`), not as a separate column. Bootstrap proposed `config_version` as an invalidation signal; verification confirms the chosen v1 contract (**TTL-only invalidation**) sidesteps this entirely — the cache doesn't need to read `config_version` to decide hit/miss.
- **`tenants.updated_at` exists as a separate column** (`alembic/versions/0005_tenants_updated_at.py`). Also not used by v1 cache (TTL-only). Available as a future invalidation signal if Phase 5+ revisits.
- **Only existing cache in `app/`**: `@lru_cache` on `get_settings()` in `app/config.py:33`. No conflicting per-domain caches.
- **No existing `asyncio.Lock` usage in app modules**. The 5B cache will be the first. Pattern reference: `pg_advisory_xact_lock(hashtext($1))` in `scripts/tenant_onboard.py:136-139` (per-key DB-side serialization), but the in-process cache uses asyncio.Lock instead.
- **Connection acquisition is `async with pool.acquire() as conn` via `get_conn()` context manager** (`app/db.py:54-58`). The cache is queried OUTSIDE the connection-acquire window in some call sites and INSIDE in others — verify call-site-by-call-site that the cache wrap point matches.

## Decisions absorbed (5B-specific)

| Decision | Value | Source |
|---|---|---|
| Cache TTL | 60 seconds | Phase 1 carry-forward; bootstrap |
| Cache invalidation strategy | TTL-only for v1. 60s max staleness window. No `config_version` or `updated_at` check. | Bootstrap |
| Cache thread safety | `asyncio.Lock` keyed per `tenant_id` around miss path. Reads lock-free (atomic dict access). | Bootstrap |
| Time source | `time.monotonic()` for TTL math (not `datetime.now()` — robust to wall-clock drift) | Standard pattern |
| Cache scope | Per-process (module-level singleton). Multi-worker uvicorn deployments each have their own cache — acceptable since TTL bounds divergence to 60s. | Bootstrap (implicit) |
| Cache module location | `app/tenant_config_cache.py`, sibling to `app/tenant_config.py` | Verification finding |
| Cache miss = log + DB load | Single DB load per tenant_id even under concurrent misses (the per-tenant-id Lock enforces this) | Bootstrap |
| Cache hit = no DB call | Lock-free dict lookup, then TTL check, then return | Bootstrap |
| Cache eviction | Implicit — TTL expiry on next read. No explicit eviction loop. | v1 simplicity |
| Cache cleanup on tenant deletion | Out of scope. Tenants aren't deleted in v1; stale entries expire via TTL. | Bootstrap |
| Metrics | `tenant_config.cache.hit` / `tenant_config.cache.miss` structured logs with `metric=True`. Both at INFO level. | Bootstrap |
| Call sites updated | All 5 — booking, modification, feedback, admin (×2). Even shape-consistency loads. | Verification finding |
| Error propagation | `LookupError` on missing tenant propagates through cache unchanged (cache does NOT cache the error). | Bootstrap (implicit) |
| `tenant_onboard.py` script output | Update to mention "config changes take effect within 60 seconds" so operators know the staleness window | Bootstrap |

## Workflow context

**Per-commit reviewer panel is MANDATORY in Phase 5.** Each commit lists its triage routing and reviewer assignment. Per CLAUDE.md, the panel runs at commit time; operator batch checkpoint is separate. This batch introduces a NEW `.py` file under `app/`, which is in the Never Skip list — full panel applies.

Plan-file slicing for reviewer invocations: `Plan file: PLAN_PHASE_5B.md, current commit: 5B.N (<title>), upcoming commits: 5B.(N+1) through 5B.4 sections.`

## Cross-batch dependencies

- **Depends on 5A**: lockfile + format-sync cleanliness so 5B diffs are reviewable.
- **Feeds 5C**: the `tenant_config.cache.hit` / `cache.miss` events are consumed by 5C's EMF processor. 5C wires the formatter; 5B emits the events.
- **Feeds 5D**: cache reduces per-request DB load, materially helping the load test target (p95 < 200ms at 100 RPS). Without 5B, every booking/modification/feedback/admin request would issue an extra `SELECT FROM tenants` — at 100 RPS that's 100 extra SELECTs/sec.

## Commits

### 5B.1 — `app/tenant_config_cache.py` module

**Theme.** New module: in-memory cache class with per-tenant-id asyncio.Lock around the miss path. Module-level singleton instance. Public API: `async def load_tenant_config_cached(conn, tenant_id) -> TenantConfig`.

**Files changed.**
- `app/tenant_config_cache.py` — new file.

**Specifics.**
- Module exposes a singleton `_cache` (private) and a top-level `load_tenant_config_cached(conn, tenant_id)` async function that callers use.
- Internal state:
  - `_entries: dict[int, tuple[TenantConfig, float]]` — keyed by `tenant_id`, value is `(config, loaded_at_monotonic)`.
  - `_locks: dict[int, asyncio.Lock]` — per-tenant-id lock for serializing concurrent misses. Locks are created on demand and never evicted (acceptable: bounded by tenant count, which is small in v1).
  - `_locks_lock: asyncio.Lock` — guards `_locks` dict mutation (creating a new per-tenant Lock).
- `TTL_SECONDS: float = 60.0` module-level constant.
- Algorithm in `load_tenant_config_cached(conn, tenant_id)`:
  1. Read `_entries.get(tenant_id)`. If present, check `monotonic() - loaded_at < TTL_SECONDS`. If yes → emit `tenant_config.cache.hit`, return cached config.
  2. Cache miss. Acquire per-tenant lock (creating it under `_locks_lock` if absent).
  3. Inside the per-tenant lock: re-check `_entries` (another coroutine may have populated it while we waited). If now-present and within TTL → emit hit, return.
  4. Still miss. Call `load_tenant_config(conn, tenant_id)`. On success: store `(config, monotonic())` in `_entries`; emit `tenant_config.cache.miss`. Return.
  5. On `LookupError`: do NOT cache the error; re-raise. (Subsequent calls retry the DB.)
- Use `time.monotonic()` for TTL math, NOT `datetime.now()`.
- Type hints strict (mypy-clean). Use `from __future__ import annotations` if other modules in `app/` do; check `app/tenant_config.py` for convention.
- Public API only: `load_tenant_config_cached`. Private state not exported.

**Validation.**
- New unit test file: `tests/unit/test_tenant_config_cache.py` covers:
  - Hit returns same object reference (or same content) on second call within TTL.
  - Miss after TTL expiry re-loads.
  - `LookupError` propagates and is not cached.
  - Time mocking via `time.monotonic` patch (pytest monkeypatch).
- New unit test for concurrent-miss serialization (mock the underlying `load_tenant_config` to count invocations; gather 10 coroutines for same tenant_id; assert load_tenant_config called exactly once).
- `pre-commit run --all-files` clean.
- `mypy app/` strict clean.
- `pytest tests/unit/test_tenant_config_cache.py -v` passes.

**Risk level.** Medium. New concurrency primitive in the codebase. The double-checked-locking pattern is a known correctness pitfall — careful review needed.

**Reversibility.** High. `git revert` removes the module; nothing imports it yet (call sites wire in 5B.2). If 5B.2 lands first, revert order is 5B.2 then 5B.1.

**Pre-commit verification.** Hooks pass.

**Observability.** Two new structured-log events at INFO level: `tenant_config.cache.hit` and `tenant_config.cache.miss`. Both include `tenant_id` and `metric=True`. The miss event additionally includes `cache_size` (number of entries after the load) for observability.

**Test changes.** New file `tests/unit/test_tenant_config_cache.py` (~5-7 test functions).

**Rollback plan.** `git revert`. Cache is not yet wired (5B.2 wires it); revert removes dead code.

**Declared breaks.**
- Scope: `load_tenant_config_cached` is added but no production code calls it yet — 5B.2 wires call sites.
- Resolved in: 5B.2.

**Reviewer routing.** Never Skip per CLAUDE.md: "Any commit that introduces a new `.py` file under `app/`." → **Full standard panel: senior-engineer + security-auditor + code-flow-reviewer + test-reviewer** (tests change). Security-auditor specifically validates: (a) double-checked-locking is correct (re-check inside the lock), (b) `_locks` dict mutation is itself serialized, (c) error-path doesn't pollute the cache, (d) no tenant_id confusion (cache key matches request tenant_id, not auth.role).

---

### 5B.2 — Wire cache into call sites

**Theme.** Replace direct `load_tenant_config(conn, tenant_id)` calls with `load_tenant_config_cached(conn, tenant_id)` at all 5 call sites.

**Files changed.**
- `app/api/booking.py:54` — change import + call.
- `app/api/modification.py:60` — change import + call.
- `app/api/feedback.py:115` — change import + call.
- `app/api/admin.py:98` — change import + call.
- `app/api/admin.py:185` — change import + call (same import line).

**Specifics.**
- Replace `from app.tenant_config import load_tenant_config` (or similar) with `from app.tenant_config_cache import load_tenant_config_cached`.
- Replace the call expression. Signature is identical (`async def f(conn, tenant_id) -> TenantConfig`), so no other changes needed.
- The shape-consistency call sites in feedback.py + admin.py (lines 110-115, 98-99, 185-186 per verification) continue to load — they now hit the cache and contribute to hit-rate metrics. This is intentional.
- Verify: no call site bypasses the cache. (A future-discovered bypass would mean the cache is partially effective.)
- The original `load_tenant_config` remains in `app/tenant_config.py` — it's the cache miss path. NOT removed in this commit. (Other code paths may exist in tests; verify before removal in a future commit.)

**Validation.**
- All 852+ existing integration tests pass — they exercise the cache transparently (cache miss on first request, cache hit on second).
- Verify hit/miss balance: run booking endpoint test twice for same tenant; capture structured logs; assert one miss + one hit log.
- Latency benchmark (informal): time a single endpoint hit before-and-after. Expected: ~1-2ms p95 reduction per request (the load_tenant_config DB roundtrip is small but measurable).
- `pre-commit run --all-files` clean. `mypy app/` strict clean.

**Risk level.** Low. Substitution at 5 sites; signatures identical.

**Reversibility.** High. `git revert` switches back to direct call.

**Pre-commit verification.** Hooks pass.

**Observability.** The hit/miss events from 5B.1 begin firing in production traffic paths.

**Test changes.** None directly (the integration tests already cover; new explicit hit/miss assertion test lives in 5B.3).

**Rollback plan.** `git revert`. The cache module remains as dead code; can stay or be removed in a follow-up.

**Declared breaks.** None.

**Reviewer routing.** Standard panel: 5 call sites across 4 files; touches auth-adjacent code (tenant_config gates currency validation, which affects request acceptance). **senior-engineer + security-auditor + code-flow-reviewer.**

---

### 5B.3 — Concurrent-load integration test

**Theme.** Explicit integration test asserting that 10 concurrent requests for the same tenant_id under a cache miss result in exactly 1 DB load.

**Files changed.**
- `tests/integration/test_tenant_config_cache_concurrency.py` — new file.

**Specifics.**
- Test setup: clean cache state (the module-level singleton may have entries from prior tests). Reset by patching `_entries` and `_locks` to empty dicts via pytest fixture.
- Test body:
  1. Patch (monkeypatch) `app.tenant_config.load_tenant_config` to a counting wrapper that increments a counter on each call AND delegates to the real implementation.
  2. Issue 10 concurrent calls to `load_tenant_config_cached(conn, tenant_id)` via `asyncio.gather`.
  3. Assert counter == 1.
  4. Assert all 10 returned configs are equal (same content; reference may or may not be identical depending on Pydantic immutability).
- Additional test: after TTL expiry (mock `time.monotonic`), another gather of 10 → counter increments by 1 only (total 2).
- Additional test: 10 concurrent calls across 10 DIFFERENT tenant_ids → counter == 10 (per-tenant lock doesn't over-serialize).

**Validation.**
- New file tests pass. ~3-4 test functions.
- Full integration suite remains green.

**Risk level.** Low (test-only).

**Reversibility.** High.

**Pre-commit verification.** Hooks pass.

**Observability.** None (tests don't emit production metrics).

**Test changes.** New file. ~3-4 test functions.

**Rollback plan.** `git revert`.

**Declared breaks.** None.

**Reviewer routing.** Test-only per CLAUDE.md: "ONLY test file additions or changes, no production code → **test-reviewer + senior-engineer + code-flow-reviewer**." Test-reviewer specifically verifies: (a) counter wrapper actually delegates (no inline re-impl), (b) `asyncio.gather` not `asyncio.wait` (different semantics), (c) the 10-different-tenant case actually creates 10 different tenant_ids (not all the same), (d) no false-pass shape (assertions actually fire).

---

### 5B.4 — Documentation: `.ai/decisions.md` + tenant_onboard.py output + BUGS.md staleness note

**Theme.** Document the cache TTL + staleness window in `.ai/decisions.md`. Update `scripts/tenant_onboard.py` script output to surface the 60s window to operators. No code-path changes.

**Files changed.**
- `.ai/decisions.md` — new section "Tenant config cache (Phase 5B)" documenting: TTL=60s, TTL-only invalidation, per-tenant asyncio.Lock around miss, no config_version check, per-process scope, future considerations (multi-worker divergence, explicit invalidation).
- `scripts/tenant_onboard.py` — add a `print()` statement at the end of the upsert path: "Config changes take effect within 60 seconds (cache TTL)."
- `.ai/conventions.md` — short pointer to `.ai/decisions.md` Phase 5B section under a "Caching" subsection.

**Specifics.**
- `.ai/decisions.md` section is ~40-60 lines. Includes: rationale (per-request DB load was a measurable hotspot ahead of load test), trade-off acknowledgment (60s staleness vs. simpler implementation), explicit non-goal (sub-60s invalidation not in scope), future revisit triggers.
- `tenant_onboard.py` change: ~1 line of new code (a print after the existing success print). No logic change.
- `.ai/conventions.md` change: a 3-5 line subsection pointing to the decision doc.

**Validation.**
- `pre-commit run --all-files` clean.
- Manual: run `scripts/tenant_onboard.py` against the dev DB; confirm the new line appears in stdout.

**Risk level.** Trivial-to-low. Docs + print statement.

**Reversibility.** High.

**Pre-commit verification.** Hooks pass.

**Observability.** None.

**Test changes.** None.

**Rollback plan.** `git revert`.

**Declared breaks.** None.

**Reviewer routing.** Mixed: `.ai/decisions.md` amendment is per CLAUDE.md "ALWAYS standard path with doc-reviewer at minimum." Plus the tenant_onboard.py print change is trivial-but-bundled. → **senior-engineer + doc-reviewer.** Doc-reviewer validates the decision narrative; senior-engineer validates the print statement doesn't accidentally leak secrets or break the script's exit code.

---

## Batch 5B summary

- 4 commits.
- New module: `app/tenant_config_cache.py` (~80-100 lines).
- New tests: `tests/unit/test_tenant_config_cache.py` (~5-7 functions); `tests/integration/test_tenant_config_cache_concurrency.py` (~3-4 functions).
- Wire-up at 5 call sites.
- `.ai/decisions.md` gains a Phase 5B section.
- `scripts/tenant_onboard.py` surfaces the staleness window to operators.
- Cumulative test count target: ~865-867.

End of batch: REPORT_PHASE_5B.md. Cache hit/miss metrics now flowing; 5C wires them into EMF.
