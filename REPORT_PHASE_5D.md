# REPORT_PHASE_5D — RLS role transition + load test + audit + Phase 5 wrap

Batch 5D of Phase 5. 7 implementation commits including the retro fix.

## Commit list with reviewer-panel verdicts

| Commit | Title | Routing | Reviewer verdicts |
|---|---|---|---|
| d435008 | 5D.1: Migration 0008 — riskd_app_login role | Never Skip (role + RLS-adjacent) | senior: SHIP IT · security: LOW RISK · code-flow: CLEAN · db: SHIP IT |
| bb231cc | 5D.2 prereq: migration 0009 drops RLS on api_tokens + app_users | Never Skip + db-reviewer | (rolled into 5D.2 panel below) |
| 79c7d39 | 5D.2: DATABASE_URL switch to riskd_app_login + RLS-aware fixtures | HIGHEST-RISK — Full panel | senior cycle-1: NEEDS MINOR FIXES → cycle-2 retro applied · security cycle-1: MEDIUM RISK (api_tokens RLS-drop gap documented in 5D.5) · code-flow: MINOR ISSUES → cycle-2 retro applied · db: SHIP IT |
| 8437ba7 | 5D.2 retro fix: ALEMBIC_DATABASE_URL in docker-compose + docstring drift + count_b symmetry | Cycle-2 fix-up | (resolves cycle-1 NEEDS MINOR FIXES) |
| e08f6fb | 5D.3: scripts/load_test.py locust harness | Standard panel | senior: SHIP IT · security: LOW RISK / CLEAN · code-flow: CLEAN |
| 8fd55b4 | 5D.4: execute load test + docs/load-test-phase-5.md (GREEN) | Doc-only post-execution | doc-reviewer: PUBLISH (results captured inline) |
| eca7dc0 | 5D.5: docs/security-audit-rls-phase-5.md | Doc-only | doc-reviewer: PUBLISH (results captured inline) |

## 5D.2 panel deep-dive

Highest-risk commit of Phase 5. First-attempt validation surfaced 59
failed + 335 errored tests because RLS policies on the 9 tenant-scoped
tables enforce WITH CHECK by default in Postgres, and test fixtures
predated RLS by doing raw `INSERT INTO customers/users/shipments/...`
without first calling `set_tenant_id`. STOPPED per autonomous-execution
rule; STATUS.md row appended for operator direction.

Operator chose option (a): refactor every test fixture and helper
that touches a tenant-scoped table. Three components landed:

1. **Migration 0009** (commit bb231cc): drops RLS on api_tokens +
   app_users. Auth chicken-and-egg (auth lookup happens BEFORE
   set_tenant_id can fire). Justification: token_hash IS the
   credential; RLS was vestigial under the pre-5D superuser bypass
   anyway.

2. **app/db.py pool-init callback**: sets `app.tenant_id='0'` (safe
   sentinel — no real tenant has id 0) on every new pooled connection
   via asyncpg's `setup=` parameter. Fail-closed default: RLS-protected
   reads return empty until the per-request `set_tenant_id` (with
   `is_local=true`) overrides the session sentinel inside the
   transaction.

3. **tests/conftest.py helpers + 18 integration test files patched**:
   `set_test_tenant_id`, `reset_test_tenant_id`, `with_test_tenant_context`
   (auto-restoring CM), `create_extra_tenant`. Sub-agent worked through
   the 18 files mechanically; 5 files needed direct edits to use the
   helpers correctly.

Reviewer panel cycle 1: senior NEEDS MINOR FIXES (missing
ALEMBIC_DATABASE_URL in docker-compose app.environment), security
MEDIUM RISK (api_tokens RLS-drop creates defense-in-depth gap;
documented in 5D.5), code-flow MINOR ISSUES (docstring drift + asymmetry
in test_tenant_isolation.py), db SHIP IT.

Cycle 2 retro commit 8437ba7 applied: ALEMBIC_DATABASE_URL added to
docker-compose; reset_test_tenant_id docstring corrected; count_b read
wrapped in with_test_tenant_context for symmetry with count_a.
Deferred items captured in the retro commit message; the api_tokens
RLS-drop rationale and defense-in-depth gap captured in 5D.5 audit doc.

## Test count delta

- Start of 5D: 919 (end of 5C).
- After 5D.1: 919 (migration only, no test changes).
- After 5D.2: 918 (−1: api_tokens param case dropped from canary per
  migration 0009; gained 0 new tests).
- After 5D.3: 918 (locust harness, not a test).
- After 5D.4: 918 (doc only).
- After 5D.5: 918 (doc only).
- **End of batch 5D: 918 tests pass. mypy strict + pre-commit clean.**

Case-1 + case-2 regression continue to pass under `riskd_app_login`.

## Load test results (5D.4)

GREEN status:

| Endpoint | p95 (ms) | p99 (ms) | Headroom to 200ms |
|---|---|---|---|
| booking | 12 | 16 | 188ms |
| modification | 13 | 16 | 187ms |
| feedback | 7 | 10 | 193ms |

10,970 requests over 60 seconds at 20 users (183 RPS aggregate). 0
errors. Cache hit ratio > 99% within steady-state. Saturation test at
100 users showed pool-max-10 bottleneck (Phase 6 tuning).

## BUGS.md state

Phase 5 entries reviewed:
- `2C.6 rule-count` (pre-Phase-5 RESOLVED)
- `ux_decisions_tenant_request UNIQUE flat` (RESOLVED in 5A.7)
- `ruff version drift` (RESOLVED in 5A.1)
- `docker-compose `.env` localhost mismatch` (DEFERRED to Phase 6 — production uses Secrets Manager; dev-host workaround documented)
- `Dockerfile pytricia build` (RESOLVED in 5A.4; Phase 6 multi-stage cleanup)
- `Redundant ix_api_tokens_tenant` (DEFERRED — low severity write amplification)
- `409 catch unreachable in serial tests` (DEFERRED — future asyncio.gather race test)
- `_assert_decisions_equivalent duplicated` (DEFERRED — future cleanup)

3 RESOLVED, 5 DEFERRED. No new BUGS.md entries from 5D itself.

## Plan-vs-delivery notes

- **5D.2 scope expansion**: the plan didn't anticipate the migration 0009 RLS-drop on auth tables. Surfaced during first-attempt validation; operator confirmed via AskUserQuestion. Plan section 5D.2's "expected failures and their resolution" called out missing tenant filter under RLS but not the auth chicken-and-egg specifically. The auth.py docstring DID call it out (lines 15-18 referencing Phase 5 follow-up). Audit doc 5D.5 documents the rationale and defense-in-depth gap.
- **5D.4 saturation test**: the plan called for "100 RPS sustained for 60+ seconds". Run 1 used `-u 100` and overdrove the system (pool max=10 bottleneck). Run 2 at `-u 20` sustained 183 RPS aggregate (above plan target) with p95=12ms (GREEN). Both runs documented for completeness.
- **5D.4 baseline comparison**: 5D.4 numbers BEAT 5C.4 baseline (booking p95 12 vs 47.9; modification 13 vs 144.9; feedback 7 vs 148.7). Explanation: cache warmth dominates the 60s steady-state window vs 5C.4's 1000-request cold-start. The RLS overhead projection from REPORT_PHASE_5B (5-15ms) is lost in the cache-warmth signal.
- **5D.2 sub-agent delegation**: the fixture refactor work across 18 files was delegated to a sub-agent that worked through the patches mechanically. Sub-agent reported 5 files needing direct edits + the operator-rejected pattern of api_tokens RLS testing in the canary. All cycle-2 reviewer feedback was applied directly by the main agent.

## Operator checkpoint

Batch 5D complete. The Phase 5 wrap (aggregate REPORT_PHASE_5.md)
follows in commit 5D.6.
