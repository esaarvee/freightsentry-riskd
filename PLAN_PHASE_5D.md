# PLAN_PHASE_5D — RLS role transition + load test + audit refresh + Phase 5 wrap

Batch 5D of Phase 5. The highest-risk batch: switches the runtime DB connection from a superuser to `riskd_app_login`. RLS actually enforces for the first time. Then runs the sustained load test, refreshes the security audit doc, and produces the Phase 5 wrap report.

## Pre-plan verification findings

- **Existing role**: `CREATE ROLE riskd_app NOLOGIN;` at `alembic/versions/0001_initial.py:33`. NOLOGIN means it can't connect directly — needed a `GRANT riskd_app TO <login-role>` path until 5D.
- **Grants to `riskd_app`** (`0001_initial.py:324-326`): `USAGE` on schema, `SELECT/INSERT/UPDATE/DELETE` on all tables in public, `USAGE/SELECT` on all sequences. Plus explicit feedback grant in 0004 (line 76). These propagate to `riskd_app_login` via `GRANT riskd_app TO riskd_app_login` (the standard inheritance pattern).
- **RLS policies** on 9 tables — `enterprises, customers, users, shipments, decisions, feedback, customer_baselines, api_tokens, app_users` — all `USING (tenant_id = current_setting('app.tenant_id')::int)`. Set per-connection via `app/db.py::set_tenant_id` (lines 61-67).
- **Current `DATABASE_URL` user**: `riskd:riskd@postgres:5432/riskd` (superuser bootstrap user). Both alembic (sync, via psycopg) and runtime (async, via asyncpg) use this URL.
- **Alembic env.py** (`alembic/env.py:24-40`) builds the URL from `DATABASE_URL` and rewrites the driver prefix. Migrations and runtime share the env var; the proposed Phase 5D split (alembic = superuser, runtime = `riskd_app_login`) requires either two env vars or an explicit migration-mode override.
- **Existing 3C.3 RLS canary** (`tests/integration/test_rls_enforcement_under_riskd_app.py`): grants `riskd_app` LOGIN + random password in a fixture, opens fresh connection, runs tests, revokes LOGIN on teardown. Covers all 6 endpoint patterns (per 4D.4 retro fix). With 5D's `riskd_app_login` permanently granted LOGIN, the temporary-grant dance is no longer necessary — the canary now connects directly. Plan covers the refactor.
- **Integration test pool** (`tests/conftest.py:118`) inits a session-scoped asyncpg pool from a single DATABASE_URL. Switching the full suite to `riskd_app_login` means changing this URL (or introducing a separate test-time env var) and accepting that every test runs under RLS-enforcement.
- **Case-1 + case-2 regression tests**: `tests/integration/test_case_1_detection.py` and `tests/integration/test_case_2.py`. Both must pass under the new role.
- **`locust>=2.32` is in the `[load]` optional extras** of `pyproject.toml`. Phase 5D uses locust for sustained load testing (5C's baseline measurement used httpx — different tools for different scales).
- **`data/enrichment/` directory does not exist** (Phase 6 loads real data). Phase 5D's load test runs against synthetic enrichment — measurements are optimistic by 1-5ms per request vs. real-data Phase 6 deploy. Documented as a watch point.
- **`docs/security-audit-rls-phase-3.md`** is 174 lines; **`docs/security-audit-rls-phase-4.md`** is 117 lines (delta over phase 3). Phase 5D adds `docs/security-audit-rls-phase-5.md` as delta over phase 4 (~100-150 lines expected).

## Decisions absorbed (5D-specific)

| Decision | Value | Source |
|---|---|---|
| New role | `riskd_app_login` WITH LOGIN INHERIT; `GRANT riskd_app TO riskd_app_login`. Inherits all riskd_app grants. | Bootstrap |
| Role password management | For local dev: hardcoded in docker-compose secrets (development-only). For Phase 6 production: AWS Secrets Manager (out of 5D scope; 5D delivers the role + local-dev creds). | Bootstrap implicit |
| Migration role split | `DATABASE_URL` continues to be the runtime connection. Alembic uses a separate `ALEMBIC_DATABASE_URL` env var (falls back to `DATABASE_URL` if unset, for backward compat). Local docker-compose sets both: `DATABASE_URL` to `riskd_app_login`, `ALEMBIC_DATABASE_URL` to superuser. | Verification finding (cleanest split with minimal churn) |
| Migration role default behavior | If `ALEMBIC_DATABASE_URL` unset, alembic uses `DATABASE_URL` — this preserves the current local-dev "no env vars set, just docker-compose defaults" flow until 5D commits the docker-compose update. | Verification |
| Canary test refactor | 3C.3 canary's temporary-LOGIN dance becomes legacy. Refactored to either (a) connect as `riskd_app_login` (now permanently LOGIN), or (b) deleted in favor of "the entire integration suite is the canary." Plan keeps option (a) for explicit RLS surface coverage during operator review; option (b) is a future cleanup. | Bootstrap implicit |
| Test suite role | Full integration suite runs under `riskd_app_login`. `tests/conftest.py::_pool` is updated. | Bootstrap |
| Load test framework | **locust** (Python-native; matches stack; `[load]` extra in pyproject). NOT wrk. | Verification + bootstrap |
| Load test target | <200ms p95 across booking + modification + feedback under 100 RPS sustained for 60+ seconds | MASTER_PLAN |
| Load test traffic mix | ~5% fraud (case-1/case-2 shapes), ~95% legitimate; randomized request_id per call; spread across 3-5 synthetic tenants | Bootstrap |
| Audit doc | `docs/security-audit-rls-phase-5.md` as DELTA over phase 4 (not full re-audit). ~100-150 lines. | Bootstrap |
| Audit doc covers | (a) role transition mechanics, (b) load-test findings + any hardening that resulted, (c) `last_used_at` auth signal addition (from 5A.5) | Bootstrap |
| BUGS.md drain | Final sweep at 5D.6. Confirm: ruff drift RESOLVED in 5A; UNIQUE widening RESOLVED in 5A; 2C.6 rule count RESOLVED pre-Phase-5 (verify and mark if not). | Bootstrap |
| Phase 6 readiness assessment | Section in `REPORT_PHASE_5.md` aggregate. Lists Phase 6 prerequisites that Phase 5 satisfies, and unblocked Phase 6 work items. | Bootstrap |
| Phase 5D failure mode handling | If full integration suite breaks under `riskd_app_login`, stop and surface to `.claude/STATUS.md`. Do NOT add `BYPASSRLS` to the role as a workaround. | Bootstrap watch point |

## Workflow context

**Per-commit reviewer panel is MANDATORY in Phase 5.** This batch has the highest reviewer-panel stakes in the entire phase: RLS now actually enforces, and a missing tenant filter in any query — even one the Phase 3+4 audits didn't surface — manifests here as a silent data leak (empty result that looks like "no data" rather than "permission denied"). Reviewers MUST attend to dual-tenant-predicate patterns in every query.

Plan-file slicing: `Plan file: PLAN_PHASE_5D.md, current commit: 5D.N (<title>), upcoming commits: 5D.(N+1) through 5D.6 sections.`

## Cross-batch dependencies

- **Depends on 5A.5**: the `last_used_at` writer in `auth.py` issues an UPDATE on `api_tokens` under the auth-context tenant_id. Under `riskd_app_login`, this UPDATE must succeed. The grants to `riskd_app` include UPDATE on all tables — inherited by `riskd_app_login` — so it should succeed; 5D.2 validates.
- **Depends on 5A.6 + 5A.7**: the `last_used_at` index and the `ux_decisions_tenant_request_type` index must exist before 5D's full-suite run.
- **Depends on 5B**: cache reduces per-request DB load, materially helping the 100 RPS load test target.
- **Depends on 5C**: baseline from 5C.4 is the reference 5D's load test compares against. EMF formatter captures load-test metrics.

## Commits

### 5D.1 — Migration 0008: `riskd_app_login` role creation

**Theme.** Create the `riskd_app_login` role with LOGIN INHERIT; grant `riskd_app` to it. No runtime config change yet.

**Files changed.**
- `alembic/versions/0008_riskd_app_login.py` — new migration. Revises 0007.

**Specifics.**
- Migration upgrade SQL:
  ```sql
  -- Local-dev password; production overrides via AWS Secrets Manager (Phase 6).
  CREATE ROLE riskd_app_login WITH LOGIN INHERIT PASSWORD 'riskd_app_login_dev';
  GRANT riskd_app TO riskd_app_login;
  ```
- Migration downgrade SQL:
  ```sql
  REVOKE riskd_app FROM riskd_app_login;
  DROP ROLE riskd_app_login;
  ```
- Critical: this migration runs as superuser via alembic. After this commit, the role EXISTS but nothing connects as it yet (5D.2 wires the connection).
- Plain-text password in the migration is acceptable for local dev — production runs a separate "set password from secret" step in Phase 6 deploy. Document this in the migration comment and in 5D.5 audit doc.
- Critical: the password is intentionally bound to local dev only. Phase 6 either rotates the password from a secret or recreates the role. The migration comment makes this explicit.

**Validation.**
- `docker compose exec app alembic upgrade head` succeeds.
- `docker compose exec app alembic downgrade base && docker compose exec app alembic upgrade head` round-trips.
- `psql ... '\du'` shows the new role with LOGIN attribute and `riskd_app` membership.
- All 850+ tests still pass — nothing changed at the runtime layer yet.

**Risk level.** Low. Additive role; no runtime impact until 5D.2.

**Reversibility.** High. `alembic downgrade -1`.

**Pre-commit verification.** Hooks pass.

**Observability.** None.

**Test changes.** None directly. 5D.2 adds the full-suite run under the new role.

**Rollback plan.** `alembic downgrade 0007`.

**Declared breaks.** None.

**Reviewer routing.** Never Skip (migration + RLS-adjacent role). **Full panel: senior-engineer + security-auditor + code-flow-reviewer + db-reviewer.** Security-auditor specifically validates: (a) the role has INHERIT not NOINHERIT (otherwise grants don't propagate), (b) no BYPASSRLS attribute, (c) no SUPERUSER attribute, (d) password is local-dev-only and the migration comment makes this explicit, (e) `GRANT riskd_app TO riskd_app_login` correctly transfers all 9-table RLS policies' applicability.

---

### 5D.2 — Runtime `DATABASE_URL` switch + full integration suite re-run

**Theme.** Switch local-dev runtime `DATABASE_URL` to `riskd_app_login`. Split alembic via `ALEMBIC_DATABASE_URL`. Re-run the full integration suite. Any test failure surfaces as fix-up work in the same batch.

**Files changed.**
- `docker-compose.yml` — change `DATABASE_URL` to `postgresql://riskd_app_login:riskd_app_login_dev@postgres:5432/riskd`; add `ALEMBIC_DATABASE_URL: ${ALEMBIC_DATABASE_URL:-postgresql://riskd:riskd@postgres:5432/riskd}` (superuser for migrations).
- `.env.example` — same shape (DATABASE_URL=`riskd_app_login`...; ALEMBIC_DATABASE_URL=`riskd:riskd`...).
- `alembic/env.py` — modify `_build_url()` to prefer `ALEMBIC_DATABASE_URL` over `DATABASE_URL` if set; fall back to `DATABASE_URL` for backward compat.
- `tests/conftest.py` — update the `_pool` fixture to use `DATABASE_URL` (which now points to `riskd_app_login`). No code change if the fixture already reads from settings.
- `app/config.py` — no change expected; `DATABASE_URL` is already the runtime URL.
- `tests/integration/test_rls_enforcement_under_riskd_app.py` — **refactor to connect as `riskd_app_login` directly** (no temporary LOGIN grant). Operator decision: the temporary-grant dance was designed for the dormant-RLS world; with `riskd_app_login` permanently granted LOGIN, the dance is obsolete. Refactor removes the LOGIN-grant fixture and connects via the same connection mechanism as production runtime. Explicit canary surface coverage is preserved (option (a) in plan terminology) — option (b) ("entire integration suite is the canary") is deferred as a future cleanup.

**Specifics.**
- The transition is atomic: docker-compose env vars + alembic env.py + .env.example + canary refactor land together. After this commit, `docker compose up -d` brings up the stack with the new runtime role, and the canary connects via the same mechanism as production.
- Before merging this commit, run locally:
  1. `docker compose down -v && docker compose up -d` (clean DB).
  2. `docker compose exec app alembic upgrade head` (using ALEMBIC_DATABASE_URL via the override env or the docker-compose-set value).
  3. `pytest tests/ --asyncio-mode=auto` — the FULL integration suite under the new role.
- Expected failures and their resolution:
  - Any test that depends on superuser-only operations (TRUNCATE, ALTER ROLE) will fail. Fix: use DELETE instead, or add explicit GRANT in a follow-up migration. Document any such test in `.claude/STATUS.md`.
  - Any query missing tenant filter under RLS will return empty rows where it expected non-empty — surface as test failure. Fix: add the missing filter to the production code AND extend the relevant test to assert positive-control behavior.
- If 5+ tests fail or any case-1/case-2 regression breaks: STOP. Append to `.claude/STATUS.md` `Unforeseen / checkpoints`. Do not paper over.
- The 3C.3 canary test's temporary-LOGIN dance is unconditionally refactored out in this commit (per operator decision in Decisions absorbed). The canary connects as `riskd_app_login` directly via the runtime pool, matching production. The temporary LOGIN-grant fixture is deleted.

**Validation.**
- `docker compose down -v && docker compose up -d` succeeds.
- `docker compose exec app alembic upgrade head` succeeds (alembic still uses superuser via ALEMBIC_DATABASE_URL).
- `docker compose exec app whoami` returns `app` (UID 1000, from 5A.4).
- `docker compose exec postgres psql -U postgres -d riskd -c "SELECT current_user"` returns `postgres` (manual check).
- App connects as `riskd_app_login` — verify via `docker compose logs app | grep <connection log>` or via app-side log of connection identity.
- `pytest tests/ --asyncio-mode=auto` — full suite passes. **THIS IS THE CRITICAL GATE.**
- `pytest tests/integration/test_case_1_detection.py tests/integration/test_case_2.py -v` — case-1 + case-2 specifically pass under new role (regression gate; per CLAUDE.md these must BLOCK).
- 5C's `tenant_config.cache.hit/miss` metrics still fire correctly under new role.
- `last_used_at` writer in 5A.5 still succeeds under new role (validated by 5A.5's test running green under 5D.2).
- `pre-commit run --all-files` clean.

**Risk level.** **HIGH.** This is the moment-of-truth. A single missing tenant filter that the Phase 3+4 audits missed will surface here as either a test failure (explicit) or silent test pass with reduced result set (subtle — reviewers must look for empty/unexpected-shape assertions).

**Reversibility.** High at the env-var level (`git revert` restores `riskd:riskd`). But: if any fix-up code commits land in this batch to fix tenant-filter gaps, those are NOT reverted by the env switch — they're correct in either configuration.

**Pre-commit verification.** Hooks pass.

**Observability.** Existing structured logs continue to fire. EMF baseline from 5C may shift slightly under the new role (no functional reason; included for due diligence).

**Test changes.** 3C.3 canary refactored to connect as `riskd_app_login` directly (temporary LOGIN-grant fixture removed). Possibly: fix-up tests for any tenant-filter gap surfaced by suite re-run. Document each in commit message.

**Rollback plan.** `git revert` restores `riskd:riskd`. If a test failure was actually a real tenant-filter bug, KEEP the test/code fix; only revert the env-var swap. The fix is correct regardless of which role the runtime uses.

**Declared breaks.** None.

**Reviewer routing.** **HIGHEST-RISK COMMIT IN PHASE 5.** Never Skip everywhere: auth + RLS + migration-adjacent + config that touches identity. **Full panel: senior-engineer + security-auditor + code-flow-reviewer + db-reviewer + test-reviewer** (if any test changed). Security-auditor takes lead on this commit. Specifically validates: (a) ALEMBIC_DATABASE_URL fallback to DATABASE_URL preserves backward compat for environments that don't set it, (b) no test uses superuser-only operations silently, (c) the local-dev password is not echoed in any log or container env that ships to production, (d) `GRANT riskd_app TO riskd_app_login` correctly transfers RLS applicability (the policies are on the *table*, not the *role*, so they apply automatically when riskd_app_login queries — but db-reviewer must confirm), (e) the integration test suite materially exercises each table's RLS policy.

---

### 5D.3 — `scripts/load_test.py` (locust harness)

**Theme.** New locust load test harness targeting booking + modification + feedback endpoints. Generates synthetic traffic at configurable RPS.

**Files changed.**
- `scripts/load_test.py` — new file. Locust file with task classes for each endpoint.
- `scripts/load_test_fixtures/` — new directory with synthetic payload templates (one per endpoint).
- `pyproject.toml` — no change (locust already in `[load]` extras).

**Specifics.**
- Locust user class `RiskdUser(HttpUser)` with `wait_time = between(0.05, 0.15)` (target ~100 RPS at default user count).
- Tasks:
  - `task_booking_legitimate` (weight=60) — case-2-shape payload (legitimate API-channel cloud IP)
  - `task_booking_fraud` (weight=3) — case-1-shape payload (high-risk fraud)
  - `task_modification` (weight=20) — random modification payload
  - `task_feedback` (weight=15) — feedback for a recently-booked request
  - Idempotent_replay tasks (weight=2 each) — re-POST same `request_id`
- Mix target: ~5% fraud, ~95% legitimate per bootstrap.
- 3-5 synthetic tenants seeded via `scripts/tenant_onboard.py` before the load test. Locust users randomly pick a tenant per request to spread load across tenants.
- Per-request `request_id` is fresh UUID. Per-request `customer_external_id` is random from a seeded pool of ~50 customers per tenant (to exercise customer_baselines warm path, not always cold-start).
- Locust outputs latency stats to stdout + CSV via `--csv` flag.
- Script is invoked as `locust -f scripts/load_test.py --host=http://localhost:8000 -u 100 -r 10 -t 60s --csv=load_test_5d` (100 users, ramp-up 10/s, run 60s, write CSV).

**Validation.**
- `pre-commit run --all-files` clean.
- Manual smoke: run locust with `-u 1 -r 1 -t 5s` (1 user, 5 seconds) against a fresh local stack; confirm script launches, hits endpoints, reports stats.
- No automated test for the locust file (it's a harness; running it IS the validation, in 5D.4).
- `mypy app/` strict clean (script under scripts/ may or may not be in mypy scope; verify pyproject mypy config).

**Risk level.** Low. New harness; doesn't touch app code.

**Reversibility.** High.

**Pre-commit verification.** Hooks pass.

**Observability.** Script generates load that EXERCISES observability; doesn't add emit points.

**Test changes.** None automated. Optional smoke test `tests/integration/test_load_test_script_smoke.py` to import and verify the locust file is well-formed.

**Rollback plan.** `git revert`.

**Declared breaks.** None.

**Reviewer routing.** Standard panel for the new `.py` file under `scripts/` (not `app/`, but substantial new code). **senior-engineer + security-auditor + code-flow-reviewer.** Security-auditor verifies: (a) no auth tokens hardcoded in the harness (tokens come from tenant_onboard.py outputs at runtime), (b) the harness doesn't bypass any production safeguard (e.g., disabling RLS for speed), (c) the fraud-traffic payloads don't leak real customer data into commits.

---

### 5D.4 — Execute load test + capture results + `docs/load-test-phase-5.md`

**Theme.** Run the 5D.3 harness against the local Docker stack with the `riskd_app_login` role. Capture p50/p95/p99 per endpoint. Compare against 5C baseline. Document findings.

**Files changed.**
- `docs/load-test-phase-5.md` — new file with methodology + results + comparison + recommendations.
- Possibly: hardening commits if any latency-regression issue surfaces (deferred to follow-up commits in 5D if material).

**Specifics.**
- Run sequence:
  1. `docker compose down -v && docker compose up -d` (clean state).
  2. Migrate to head: `docker compose exec app alembic upgrade head`.
  3. Seed 3-5 tenants + 50 customers each: `python scripts/tenant_onboard.py ...` (loop).
  4. Run locust: `locust -f scripts/load_test.py --host=http://localhost:8000 -u 100 -r 10 -t 60s --csv=load_test_5d --headless`.
  5. Capture stats CSV + run summary.
  6. Generate p50/p95/p99 per endpoint from the CSV.
- Validation criteria:
  - p95 < 200ms for booking + modification + feedback (HARD gate per MASTER_PLAN).
  - No HTTP 500s (errors indicate RLS gap or other defect).
  - No memory leak (locust runs 60s; if app memory > 1GB at end, flag — but no automated assertion).
  - Cache hit rate > 80% (most requests reuse tenant_config; emit counts visible in app structured logs).
- If p95 > 200ms: STOP. Append to `.claude/STATUS.md`. Investigate before continuing.
- If p95 < 200ms but with material headroom degradation vs. 5C baseline (>50% increase): flag in the doc and BUGS.md, don't stop.

**Cache hit/miss tail analysis (per operator feedback).** Load test runs 60+ seconds with traffic spread across 3-5 synthetic tenants; tenant-config cache TTL is 60s. Expected behavior: every tenant sees a cache miss at the minute boundary (~3-5 misses total during a 60s test, plus the cold-start misses at second 0). The miss path adds a DB round-trip; under load that's where p99 tails come from. Document explicitly:
- **Cache hit rate** per the load-test window — count `tenant_config.cache.hit` vs `tenant_config.cache.miss` events from app logs. Expected: ≥95% hit rate (3-5 misses out of ~6000 requests).
- **p95 vs p99 latency gap** per endpoint. If p99 is materially worse than p95 (e.g., p99 = p95 + 100ms), inspect timestamps of high-latency requests against `tenant_config.cache.miss` event timestamps. Correlation = cache-miss tail. Acceptable if so; document.
- If p99 spikes are NOT correlated with cache misses, that's a separate phenomenon — investigate before signing off.
- The `docs/load-test-phase-5.md` results table includes both p95 and p99 columns AND a "miss-correlated p99 outliers" qualitative note per endpoint.

**`docs/load-test-phase-5.md` contents** (~80-120 lines):
- Methodology section (locust config, traffic mix, tenant/customer setup).
- Results table: per-endpoint **p50, p95, AND p99** columns + RPS achieved + error rate. Plus a "miss-correlated p99 outliers" qualitative column per endpoint (per cache hit/miss tail analysis above).
- Cache hit/miss summary: total hits, total misses, hit rate %, and miss-vs-p99-tail correlation note.
- Comparison vs. 5C baseline: per-endpoint delta column.
- Synthetic-vs-real-enrichment caveat: Phase 6 runs the same harness against real data; expected latency increase 1-5ms p95.
- Recommendations for Phase 6: any tunings the load test surfaced (connection pool sizing, async concurrency limits, etc.).

**Validation.**
- p95 < 200ms validated by the CSV.
- `pre-commit run --all-files` clean (doc commit).
- Doc reviewer reads the results in context.

**Risk level.** Medium. The risk is "load test surfaces a real defect" — which is fine because we'd want to know — but it may delay the batch. Mitigation: BUGS.md captures non-blocking findings; STATUS.md surfaces blocking ones.

**Reversibility.** N/A (doc + measurement; no code change unless a hardening fix-up commits).

**Pre-commit verification.** Hooks pass.

**Observability.** Load test exercises observability; metrics flow through 5C's EMF.

**Test changes.** None.

**Rollback plan.** N/A.

**Declared breaks.** None.

**Reviewer routing.** Doc-only per CLAUDE.md (doc + measurement data). **doc-reviewer**. If hardening fix-up commits accompany (which would be code), each gets its own standard-panel review.

---

### 5D.5 — `docs/security-audit-rls-phase-5.md` as delta over phase 4

**Theme.** New audit doc documenting Phase 5 security-relevant changes: role transition, `last_used_at` writer, load test findings, and the production-deploy implications.

**Files changed.**
- `docs/security-audit-rls-phase-5.md` — new file.

**Specifics.**
- ~100-150 lines, delta over phase 4. Sections:
  - "Phase 5 security-relevant deltas" — bulleted summary of changes.
  - "Role transition: `riskd_app_login`" — what changed, what now enforces, what the local-dev password contract is, what Phase 6 production must do (Secrets Manager rotation).
  - "`last_used_at` auth signal" — what's captured, where it's written, what it enables (Phase 6+ stale-token reporting), what it does NOT expose (no plaintext token, no PII).
  - "Tenant config cache" — security implications of the 60s staleness window. Specifically: a config change that REVOKES a permission (e.g., narrows `allowed_currencies`) takes up to 60s to enforce across all workers. Acceptable trade-off; documented.
  - "Load test findings" — any RLS gap or security-relevant performance issue surfaced.
  - "Container hardening" — non-root UID 1000 implications. What's protected; what's not (no namespace isolation; still single-container).
  - "UNIQUE widening" — security-relevant only insofar as it removes a constraint-collision attack surface (a previously-spurious 409 path no longer exists; legitimate cross-type request_id reuse now succeeds). No new risk.
  - "What Phase 5 does NOT cover" — explicit out-of-scope: production deploy hardening, real-enrichment-data risks, multi-region deploy.
- The doc closes with a "Open items for Phase 6 audit" list.

**Validation.**
- `pre-commit run --all-files` clean.
- Doc reviewer reads end-to-end.
- Cross-check against `docs/security-audit-rls-phase-4.md` — Phase 5 doc should not silently re-litigate phase-4 conclusions; it should ADD.

**Risk level.** Low (doc).

**Reversibility.** High.

**Pre-commit verification.** Hooks pass.

**Observability.** None.

**Test changes.** None.

**Rollback plan.** `git revert`.

**Declared breaks.** None.

**Reviewer routing.** Doc-only per CLAUDE.md. **doc-reviewer.** Optionally **security-auditor** for cross-check; if the security-auditor was already invoked on prior commits in this batch (5D.1 + 5D.2), they have full context.

---

### 5D.6 — Phase 5 wrap: REPORT_PHASE_5D.md + aggregate REPORT_PHASE_5.md + BUGS.md drain + `.ai/decisions.md` final updates

**Theme.** Closing commit for Phase 5. Per-batch report 5D, aggregate Phase 5 report, BUGS.md drain sweep, final decisions doc updates, Phase 6 readiness assessment.

**Files changed.**
- `REPORT_PHASE_5D.md` — new file.
- `REPORT_PHASE_5.md` — new file (aggregate).
- `.claude/BUGS.md` — sweep: confirm ruff drift (5A) + UNIQUE widening (5A) RESOLVED; confirm 2C.6 rule count RESOLVED (per bootstrap precondition); append any new entries surfaced during Phase 5.
- `.ai/decisions.md` — final Phase 5 sweep — make sure 5A/5B/5C/5D decisions are all captured. Likely already done in 5B.4 + 5C.4 + 5D.5; this commit verifies.

**Specifics for REPORT_PHASE_5D.md** (per Phase 4 report shape):
- Commit list with reviewer-panel verdicts per commit (the Phase 5 discipline check).
- Per-commit corrections counted.
- Test count delta: starting at 852+, target at ~885-895 after 5C, after 5D add load-test-script-smoke if added → ~885-900.
- BUGS.md additions and resolutions for 5D specifically.
- Load test pass/fail summary.
- Role transition summary.

**Specifics for REPORT_PHASE_5.md aggregate**:
- Phase 5 totals: ~20-27 commits across 4 batches.
- Reviewer panel verdict distribution.
- Production bugs caught (if any) — listed.
- Phase 6 readiness assessment:
  - Prerequisites satisfied: RLS enforces at runtime, observability backend ready, cache reducing load, foundational hardening (lockfile + container + last_used_at + UNIQUE) complete, load test methodology + baseline available, audit doc current.
  - Phase 6 unblocked items: real enrichment data load, ECS Fargate deploy, RDS provisioning, Secrets Manager wire-up, case-1/case-2 replay against real data, FPR/recall measurement, modification + previously-rejected weight calibration, production launch runbook.
- Decision trail summary.
- Test count: start of phase ~852, end of phase ~885-900.

**Specifics for BUGS.md drain**:
- Confirm `## 2026-05-27 — PLAN_PHASE_2C.md 2C.6 rule-count arithmetic error` has `RESOLVED:` annotation; if not, add one (pre-Phase-5 should have done this; verify).
- Confirm `## 2026-05-27 — decisions.ux_decisions_tenant_request UNIQUE is flat across request_type` has `RESOLVED: 5A.7 (migration 0007)`.
- Confirm `## 2026-06-01 — ruff version drift between pre-commit pin and local install` has `RESOLVED: 5A.1 + 5A.2 (lockfile + format-sync)`.
- Append any new Phase-5-surfaced entries that the operator needs to triage for Phase 6.

**Validation.**
- `pre-commit run --all-files` clean.
- Doc reviewer reads end-to-end.
- Aggregate report's test count + reviewer verdict counts match per-batch sums.

**Risk level.** Trivial-to-low (docs).

**Reversibility.** High.

**Pre-commit verification.** Hooks pass.

**Observability.** None.

**Test changes.** None.

**Rollback plan.** `git revert`.

**Declared breaks.** None.

**Reviewer routing.** Doc-only per CLAUDE.md. **doc-reviewer.**

---

## Batch 5D summary

- 6 commits.
- New migration: 0008 (riskd_app_login role).
- Runtime DB connection switched to `riskd_app_login`. RLS now actually enforces.
- New script: `scripts/load_test.py` (locust harness).
- Load test executed; p95 < 200ms target validated under 100 RPS sustained for 60s.
- New docs: `docs/load-test-phase-5.md`, `docs/security-audit-rls-phase-5.md`.
- Phase 5 wrap reports.
- BUGS.md drained: 3 entries RESOLVED.
- Cumulative test count target: ~885-900.
- Phase 6 unblocked.

End of phase: Phase 5 complete. Operator reviews `REPORT_PHASE_5.md` aggregate; Phase 6 prompt awaits.

---

## Phase 5 watch points (consolidated)

1. **Reviewer panel discipline.** Every code commit invokes the panel per CLAUDE.md triage routing. NO per-batch-mode exception. The Phase 4 retro made this binding.
2. **5D.2 is the highest-risk commit in the phase.** A single missing tenant filter that Phase 3+4 audits missed will surface here — potentially as silent empty-result rather than loud permission-denied. Reviewers attend to dual-tenant-predicate patterns.
3. **`last_used_at` rollback semantics.** 5A.5's writer is inside the auth transaction; if a request rolls back, `last_used_at` reverts. Documented in test; reviewers confirm the contract.
4. **Cache staleness is user-facing.** 60s window documented in `.ai/decisions.md`, `docs/observability.md`, and `tenant_onboard.py` output. Operators told.
5. **Cache concurrent-load behavior tested explicitly.** 10 concurrent requests for same tenant_id → 1 DB load. asyncio.Lock per-tenant-id enforces.
6. **EMF formatter preserves non-metric log shape.** Backward compatibility for existing log consumers.
7. **Load test runs against synthetic enrichment.** Real-data Phase 6 deploy will show +1-5ms p95. Documented.
8. **Container hardening preserves alembic-as-superuser pattern.** ALEMBIC_DATABASE_URL split + Dockerfile USER directive coexist.
9. **UNIQUE widening migration downgrade is conditional.** Cannot revert after any cross-type request_id reuse exists in the table. Documented in migration comment.
10. **`riskd_app_login` password is local-dev only.** Production Phase 6 rotates from Secrets Manager. Documented in migration + audit doc.
