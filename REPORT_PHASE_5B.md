# REPORT_PHASE_5B — Tenant-config in-process cache

Batch 5B of Phase 5. 4 implementation commits.

## Commit list with reviewer-panel verdicts

| Commit | Title | Routing | Reviewers (verdict at land) |
|---|---|---|---|
| e161cfb | 5B.1: `app/tenant_config_cache.py` — 60s TTL cache with per-tenant asyncio.Lock | Never Skip (new `.py` under app/) — full panel + test-reviewer | senior: SHIP IT · security: LOW RISK / CLEAN · code-flow: CLEAN · test cycle-1: ACCEPTABLE → cycle-2: ACTUALLY GOOD |
| ef1fe44 | 5B.2: wire `load_tenant_config_cached` into 5 endpoint call sites | Standard panel | senior: SHIP IT · security: LOW RISK / CLEAN · code-flow: CLEAN |
| acb936f | 5B.3: integration test for concurrent tenant-config cache loads | Test-only (test + senior + code-flow) | test: ACTUALLY GOOD · senior cycle-1: APPROVED WITH RESERVATIONS → cycle-2: SHIP IT · code-flow cycle-1: MINOR ISSUES → cycle-2: CLEAN |
| a9ee711 | 5B.4: `.ai/decisions.md` caching section + onboard script staleness output | Doc + senior cross-check | doc-reviewer: MINOR TWEAKS → applied |

**Reviewer-panel discipline.** Every commit invoked the panel per CLAUDE.md routing. 2 of 4 went to cycle-2 fixes (5B.1 test-reviewer found three coverage gaps including DCL inner re-check + TTL boundary; 5B.3 senior + code-flow found pool-exhaustion shadowing in the integration test). Both resolved cleanly with cycle-2 cleanest verdicts.

## Per-commit corrections

- **5B.1** test-reviewer cycle 1 (ACCEPTABLE): three coverage gaps fixed in cycle 2:
  - Test 5 (different-tenant non-serialization) was tautological — assertion would pass under a single global Lock too. Rewrote with an `asyncio.Event` barrier so all 10 loaders must be in flight concurrently before any can release. Genuinely proves per-tenant locking.
  - No TTL boundary tests. Added two: elapsed=59.999s hits (strict less-than), elapsed=60.0s misses.
  - No direct DCL inner-re-check test. Added an Event-gated test that holds the first loader open while the second coroutine queues on the lock, then asserts `load_count == 1` after both complete (proves inner re-check returns cached without reloading).
  - Plus: surfaced and fixed a real correctness issue during initial debugging — monkeypatching `tenant_config_cache.time.monotonic` actually mutated the global `time` module, poisoning asyncio's internal timeouts. Introduced a `_now()` module-level seam so tests can mock cleanly. This is a real-world correctness fix the unit tests would otherwise have masked.
- **5B.3** senior + code-flow cycle 1 (APPROVED WITH RESERVATIONS / MINOR ISSUES): pool-exhaustion was silently degrading test 2's "10 concurrent" claim because `db_conn` held 1 of 10 slots during the gather. Restructured test 2 to acquire seed + cleanup connections via explicit `async with _pool.acquire()` blocks that release before/after the gather. Also renamed `_pool` parameter to `pool` in the helper (was shadowing fixture name), dropped unused `db_conn` from test 1, replaced fragile `current_task().get_name()` disambiguator with `secrets.token_hex(4)`.

## Test count delta

- Start of 5B: 863 (end of 5A).
- After 5B.1: 871 (+8 unit tests: 5 initial + 3 from cycle-2 coverage additions).
- After 5B.2: 871 (no test count change; test harness updated to bypass cache via spy + autouse cache reset + helper resets in 2 currency test files). Mild discovery delta noted.
- After 5B.3: 873 (+2 integration tests).
- After 5B.4: 873 (no test count change — doc + onboard script print).
- **End of batch 5B: 873 tests pass. mypy strict + pre-commit clean.**

Case-1 + case-2 regression tests: continue to pass under the cache.

## BUGS.md state

No new BUGS.md entries from 5B. The 5A-deferred items remain:
- docker-compose `app` localhost mismatch (deferred to 5D.2 or Phase 6)
- Phase 6 multi-stage Dockerfile (hard prerequisite for production deploy)
- Redundant `ix_api_tokens_tenant` index (future cleanup)
- 409 catch unreachable in serial tests (future concurrent race test)
- `_assert_decisions_equivalent` helper duplicated (future cleanup)

## Plan-vs-delivery notes

- **5B.1 plan-spec deviation**: Plan specified a `_locks_lock: asyncio.Lock` meta-lock to guard `_locks` dict mutation. Implementation uses `dict.setdefault(tenant_id, asyncio.Lock())` which is GIL-atomic under CPython — no meta-lock needed. Senior + code-flow reviewers explicitly endorsed this as a defensible simplification (the meta-lock would have added a serialization point on the lock-creation path without atomicity benefit). Module docstring documents the rationale.
- **5B.2 test changes**: Plan said "Test changes: None directly." In practice, integration tests that patched `app.api.<module>.load_tenant_config` had to be updated to the new symbol name `load_tenant_config_cached`. The `_set_tenant_config`/`_set_allowed_currencies` test helpers in two files gained explicit `tenant_config_cache._reset_for_tests()` calls (cache invalidation after a mid-test config write, because the cache would otherwise hide the new state for up to 60s within a single test). New autouse fixture `_reset_tenant_config_cache` in `tests/conftest.py` ensures pre-test cache cleanliness. These are mechanical maintenance, not new design.
- **5B.3 scope**: Plan suggested ~3-4 integration tests including a TTL-expiry scenario. Shipped 2 integration tests (10-concurrent-same-tenant, 10-concurrent-distinct-tenants). TTL expiry is covered at the unit-test tier in `tests/unit/test_tenant_config_cache.py` with `_now()` monkeypatching, so duplicating at the integration tier was deemed redundant. Reviewer-endorsed.

## Operator checkpoint

Batch 5B complete. Operator decisions before 5C begins:

1. **5C approval**: `PLAN_PHASE_5C.md` (observability backend — CloudWatch EMF processor) ready to execute. Decisions absorbed: EMF namespace `FreightSentry/RiskD`, processor inserted before `JSONRenderer()` in structlog chain, non-metric logs pass through unchanged, baseline measurement via httpx (not locust).
2. **Open from 5A's STATUS.md row** (docker-compose `.env` localhost mismatch): still deferred. Could land alongside 5D.2's `ALEMBIC_DATABASE_URL` split, or be deferred to Phase 6.
3. **5C.4 headroom yellow flag**: when baseline is captured at end of 5C, if any endpoint shows p95 ≥ 170ms, operator notification required before 5D.3 (per the 5C.4 headroom gate added during plan production).
