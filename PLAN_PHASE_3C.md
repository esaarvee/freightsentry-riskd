# Phase 3 — Batch 3C Plan — Multi-tenant scoping audit

> **Status (2026-05-27)**: Pending operator approval. Operator may defer approval until after 3B execution reports.
>
> **Verification finding absorbed**: 3C verification (subagent report 2026-05-27) catalogued all 24 asyncpg call-sites in `app/`. Result: **20 queries explicit tenant filter, 2 intentionally global (`ip_enrichment` cache), 1 RLS machinery (`set_tenant_id`), 1 health check. Zero queries with potentially missing scope.** Cross-referenced: 9 tenant-scoped tables all have `ENABLE ROW LEVEL SECURITY` + `tenant_isolation` policy at `alembic/versions/0001_initial.py:291-318`. **No policy gaps; no completion migration needed in 3C.** This shrinks 3C from bootstrap's 3-5 commit target to **3 commits** (audit doc + comprehensive cross-tenant test sweep + non-superuser-role RLS verification test).

Batch 3C produces the authoritative multi-tenant scoping audit document, extends test coverage to assert cross-tenant isolation at every endpoint (booking, modification, feedback), and adds ONE test that actually proves RLS enforces under a non-superuser connection (since app-layer connect-as-superuser dormancy means existing cross-tenant tests prove WHERE-clause filtering, not RLS).

Target: 3 commits.

---

## Decisions absorbed (batch-relevant subset)

| Decision | Value | Source |
|---|---|---|
| Audit deliverable location | `docs/security-audit-rls-phase-3.md` | Phase 3 bootstrap |
| Audit shape | (a) Query inventory: every `conn.fetch*` / `conn.execute` / `conn.copy_*` in `app/` categorized as `explicit-tenant-filter` / `RLS-dependent` / `PK-scoped` / `intentionally-global` / `MISSING SCOPE`. (b) RLS coverage: every tenant-scoped table cross-referenced against `ENABLE RLS` + `tenant_isolation` policy. | Phase 3 bootstrap |
| Pre-3C state | 9 tenant-scoped tables (`enterprises`, `customers`, `users`, `shipments`, `decisions`, `feedback`, `customer_baselines`, `api_tokens`, `app_users`) all have RLS + policy at `alembic/versions/0001_initial.py:291-318`. Zero gaps. | Verification §2 |
| RLS dormancy understanding | App connects as `postgres` superuser (per STATUS row 1B.2); superuser BYPASSES RLS. Active scoping today is app-layer `WHERE tenant_id` filtering. RLS policies are STRUCTURALLY in place for Phase 5 to activate via role transition. 3C audits structure, NOT runtime. | Phase 3 bootstrap + STATUS row 1B.2 |
| Non-superuser test approach | ONE test (3C.3) explicitly connects as the existing `riskd_app` non-superuser role (created in `alembic/versions/0001_initial.py:~270` per migration; verify exact line during execution) to prove RLS enforces. Test fixture: connect as `riskd_app`, `SET LOCAL app.tenant_id = $tenant_a`, attempt `SELECT * FROM shipments` and verify only tenant_a rows visible despite seeded tenant_b rows. This is the test that catches RLS misconfiguration in a way the app-layer-filter tests cannot. | Watch points |
| Comprehensive cross-tenant test matrix | Table-driven `tests/integration/test_tenant_isolation_comprehensive.py`. For each endpoint × each cross-tenant scenario, assert isolation behavior. Endpoints: booking (POST), modification (POST), feedback (POST). Scenarios: tenant_b token attempts to read/modify tenant_a data → expect 404 or empty. | Phase 3 bootstrap |
| No migration in 3C | Verification confirmed zero policy gaps. Bootstrap's `0003_rls_policy_completion.py` is NOT needed. 3C closes with documentation + tests only. | Verification §2 |
| Latest existing tenant isolation test baseline | `tests/integration/test_tenant_isolation.py` covers recipient overlap query isolation (Phase 2B.6). 3C extends with comprehensive endpoint sweep. | Verification §4 |
| Test fixture pattern | `tests/conftest.py::create_tenant_with_token` async context manager already exists for second-tenant setup (verification §5). Reuse, don't reinvent. | Verification §5 |
| Phase 4+ deferral | RLS runtime activation (`riskd_app_login` role with `LOGIN INHERIT`, DATABASE_URL switch) is **Phase 5**. 3C only confirms structural readiness. | Phase 3 OOS list |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md.
- Reviewer routing per CLAUDE.md triage gate:
  - 3C.1 (audit doc under `docs/`): doc-only — doc-reviewer.
  - 3C.2 (comprehensive cross-tenant integration test sweep): test-only — test-reviewer + senior-engineer + code-flow.
  - 3C.3 (non-superuser RLS verification test): test-only with **database-role nuance** — test-reviewer + senior-engineer + code-flow + db-reviewer (the non-superuser connection pattern is novel for the test suite; db-reviewer confirms the role-switching mechanism is safe for shared-DB test fixtures and doesn't introduce cross-test pollution).

- Reviewer-invocation slice template:
  > `Plan file: PLAN_PHASE_3C.md, current commit: 3C.N (<title>), upcoming commits: 3C.{N+1} through 3C.3 sections. Read only those sections.`

---

## Cross-batch dependencies

- **Consumes from Phase 1**: existing RLS policy structure at `alembic/versions/0001_initial.py:291-318`; `riskd_app` role (per STATUS row 1B.2); `tests/conftest.py::create_tenant_with_token` (verification §5).
- **Consumes from 3A**: modification endpoint at `app/api/modification.py`; updated `app/api/booking.py` with `request_type` column writes.
- **Consumes from 3B**: feedback endpoint at `app/api/feedback.py`; feedback table at the post-3B.1 shape.
- **Consumed by 3D**: 3C's audit + tests are gates for the Phase 3 wrap report's security posture statement.
- **Consumed by Phase 5**: audit doc is the input for the `riskd_app_login` role transition planning; if any gap surfaces post-3C, Phase 5 closes it.

---

## 3C.1 — Multi-tenant scoping audit doc

**Theme**: Produce `docs/security-audit-rls-phase-3.md`. Two tables: query inventory (with file:line) and RLS policy coverage. Plus a dormancy section explaining why current tests prove app-layer filtering and what 3C.3's non-superuser test adds.

**Files**:
- `docs/security-audit-rls-phase-3.md` (NEW)

**Specifics**:

Doc outline (~400 lines markdown):

```markdown
# Multi-tenant scoping audit — Phase 3

**Audit date**: 2026-05-27
**Audited commit range**: post-3B execution (after 0004 migration)
**Auditor**: Claude Code via verification-phase grep (results in PLAN_PHASE_3C.md)

## Executive summary

freightsentry-riskd has TWO layers of multi-tenant defense:
1. **App-layer WHERE-clause filtering** (active today)
2. **PostgreSQL Row-Level Security policies** (structurally complete; runtime-dormant until Phase 5)

This audit confirms both layers are structurally complete for all 9 tenant-scoped tables and identifies zero queries with potentially missing scope.

## Query inventory

All `asyncpg.Connection` method calls (`fetch`, `fetchrow`, `fetchval`, `execute`, `executemany`, `copy_*`) in `app/` are catalogued below.

### Explicit tenant filter (safe under both layers)

| File | Line | Method | Table | Filter expression |
|---|---|---|---|---|
| app/baseline.py | 215 | fetchrow | customer_baselines | `WHERE tenant_id = $1 AND customer_id = $2` |
| app/baseline.py | 227 | execute | customer_baselines | INSERT explicit tenant_id |
| app/baseline.py | 236 | fetchrow | customer_baselines | `WHERE tenant_id = $1 AND customer_id = $2 FOR UPDATE` |
| app/baseline.py | 490 | execute | customer_baselines | UPSERT with `ON CONFLICT (tenant_id, customer_id)` |
| app/api/booking.py | 50 | fetchrow | decisions | `WHERE tenant_id = $1 AND request_id = $2` (idempotency) |
| app/api/booking.py | 91 | fetchrow | customers | `WHERE id = $1 AND tenant_id = $2` |
| app/api/booking.py | 165 | fetchval | shipments | INSERT explicit tenant_id |
| app/api/booking.py | 194 | execute | decisions | INSERT explicit tenant_id |
| app/api/booking.py | 214 | execute | customers | `UPDATE customers SET ... WHERE id = $1 AND tenant_id = $2` |
| app/velocity.py | 18 | fetchval | shipments | `WHERE tenant_id = $1 AND customer_id = $2 AND booking_ts > $3` |
| app/velocity.py | 31 | fetchval | shipments | (same shape, hourly window) |
| app/velocity.py | 44 | fetchval | shipments | (same shape, 30d window) |
| app/velocity.py | 57 | fetchval | shipments | `WHERE tenant_id = $1 AND source_ip = $2 AND booking_ts > $3` |
| app/velocity.py | 70 | fetchval | shipments | (same shape, 1h window) |
| app/velocity.py | 92 | fetchval | shipments | `SELECT COUNT(DISTINCT source_ip) FROM shipments WHERE tenant_id = $1 AND customer_id = $2` |
| app/velocity.py | 117 | fetchval | shipments | `SELECT COUNT(DISTINCT customer_id) FROM shipments WHERE tenant_id = $1 AND destination_hmac = $2` (security-load-bearing per Phase 2B.6 test) |
| app/services/entity_upsert.py | 32 | fetchrow | enterprises | `INSERT ... ON CONFLICT (tenant_id, external_id) DO UPDATE` |
| app/services/entity_upsert.py | 68 | fetchrow | customers | (same shape) |
| app/services/entity_upsert.py | 108 | fetchrow | users | (same shape) |
| app/api/modification.py | <TBD post-3A> | fetchrow | decisions JOIN shipments | `WHERE d.tenant_id = $1 AND d.request_id = $2 AND d.request_type = 'booking'` (lookup of original) |
| app/api/modification.py | <TBD post-3A> | execute | decisions | INSERT with `request_type='modification'`, explicit tenant_id |
| app/api/feedback.py | <TBD post-3B> | fetchrow | feedback | `WHERE tenant_id = $1 AND request_id = $2` (first-tier idempotency) |
| app/api/feedback.py | <TBD post-3B> | fetchrow | decisions JOIN shipments JOIN customers | `WHERE d.tenant_id = $1 AND d.request_id = $2` (resolve target — feedback table has no decision_id FK post-3B.1 drop-and-recreate, so resolution goes through decisions's UNIQUE(tenant_id, request_id)) |
| app/api/feedback.py | <TBD post-3B> | fetchrow | feedback | `WHERE tenant_id = $1 AND target_request_id = $2 ORDER BY feedback_ts DESC LIMIT 1` (monotonicity check) |
| app/api/feedback.py | <TBD post-3B> | execute | feedback, customers | INSERT + counter UPDATE, both explicit tenant_id |
| app/velocity.py | <TBD post-3A> | fetchval | decisions JOIN shipments | modification velocity 1h |
| app/velocity.py | <TBD post-3A> | fetchval | decisions JOIN shipments | modification velocity 24h |

[Auditor populates line numbers from post-3B HEAD during 3C.1 execution.]

### Intentionally global (RLS deliberately not applied)

| File | Line | Method | Table | Rationale |
|---|---|---|---|---|
| app/enrich.py | 189 | fetchrow | ip_enrichment | IP enrichment is a shared cache keyed by IP; no tenant scope |
| app/enrich.py | 285 | execute | ip_enrichment | (writes to same shared cache) |

### Auth machinery (cross-tenant by design)

| File | Line | Method | Table | Rationale |
|---|---|---|---|---|
| app/auth.py | 75 | fetchrow | api_tokens | Token lookup is identity-bearing; returns tenant_id for caller. Pre-resolution, the request has no tenant context to scope by. |

### RLS machinery

| File | Line | Method | Purpose |
|---|---|---|---|
| app/db.py | 67 | execute | `SELECT set_config('app.tenant_id', $1, true)` per-transaction tenant context for RLS policies |

### Health checks

| File | Line | Method | Table |
|---|---|---|---|
| app/api/health.py | 35 | fetchval | (no table — `SELECT 1`) |

### Potentially missing scope

**None.** All queries fall in one of the above categories.

## RLS policy coverage

| Table | tenant_id column | ENABLE RLS | tenant_isolation policy |
|---|---|---|---|
| enterprises | ✓ | `0001_initial.py:291` | `0001_initial.py:301-302` |
| customers | ✓ | `0001_initial.py:292` | `0001_initial.py:303-304` |
| users | ✓ | `0001_initial.py:293` | `0001_initial.py:305-306` |
| shipments | ✓ | `0001_initial.py:294` | `0001_initial.py:307-308` |
| decisions | ✓ | `0001_initial.py:295` | `0001_initial.py:309-310` |
| feedback | ✓ | `0001_initial.py:296` | `0001_initial.py:311-312` |
| customer_baselines | ✓ | `0001_initial.py:297` | `0001_initial.py:313-314` |
| api_tokens | ✓ | `0001_initial.py:298` | `0001_initial.py:315-316` |
| app_users | ✓ | `0001_initial.py:299` | `0001_initial.py:317-318` |
| ip_enrichment | — (global) | N/A | N/A |
| global_blocked_vectors | — (global) | N/A | N/A |
| tenants | — (parent) | N/A | N/A |

All 9 tenant-scoped tables have both ENABLE RLS and a `tenant_isolation` policy of the form:
```sql
CREATE POLICY tenant_isolation ON <table>
    USING (tenant_id = current_setting('app.tenant_id')::int);
```

## Runtime dormancy

The app connects to PostgreSQL as the bootstrap `postgres` superuser (per STATUS row 1B.2). Superuser BYPASSES RLS — policies are evaluated but always pass for superuser. Therefore:

- **Today**, app-layer `WHERE tenant_id = $N` is the ONLY active isolation. The 20 explicit-filter queries above are correct and necessary.
- **Phase 5** will introduce `riskd_app_login` (a `LOGIN INHERIT` role granted `riskd_app`) and switch DATABASE_URL to connect as that role. At that point RLS activates: even a query forgetting `WHERE tenant_id` would be invisibly scoped by the `tenant_isolation` policy.

Until Phase 5, RLS is **defense-in-depth structure** that is not yet structurally enforced at the connection layer.

## Tests proving each layer

### App-layer filtering (active today)

`tests/integration/test_tenant_isolation.py` (Phase 2B.6) — proves recipient overlap query filters by tenant_id.

`tests/integration/test_tenant_isolation_comprehensive.py` (3C.2) — extends to all 3 endpoints (booking, modification, feedback) and several cross-tenant scenarios per endpoint.

### RLS runtime enforcement (proven structurally in 3C.3)

`tests/integration/test_rls_enforcement_under_riskd_app.py` (3C.3) — connects as `riskd_app` (no superuser BYPASS), sets `app.tenant_id` for tenant_a, asserts a query against `shipments` returns only tenant_a rows even with seeded tenant_b data. This is the canary test for Phase 5 readiness.

## Recommendations for Phase 4 / 5

1. **Phase 4 (admin endpoints)**: any new admin endpoint MUST add to the query inventory and re-run the audit. The triage gate routes admin endpoints to standard panel + db-reviewer; reviewer must verify.
2. **Phase 5 (RLS runtime)**: create `riskd_app_login`, switch DATABASE_URL, re-run 3C.3's test to confirm. The 20 explicit-filter queries remain correct (defense in depth); the audit's primary value at that point is verifying no new query introduced post-3C bypasses RLS.
3. **Audit cadence**: re-run grep at each phase boundary and refresh this doc.

## Appendix A — grep command for repeatable audit

```bash
grep -rnE 'conn\.(fetch|fetchrow|fetchval|execute|executemany|copy_)' app/
```

Categorize each match against the matrix above.
```

**Validation**:
- `markdownlint docs/security-audit-rls-phase-3.md` (if available; otherwise visual)
- Doc-reviewer panel confirms the doc covers all required sections and the matrix is accurate against current HEAD.
- Cross-check: line numbers for 3A and 3B additions are populated correctly (placeholder `<TBD post-3A>` / `<TBD post-3B>` replaced with real line numbers at commit time).

**Risk**: **Low**. Doc-only commit. The risk is stale line numbers if a later commit shifts them — but this is a known limitation of point-in-time audit docs.

**Reversibility**: Easy — delete the file.

**Pre-commit verification**: ruff/mypy/tests all green (no Python touched).

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: doc-only — doc-reviewer.

---

## 3C.2 — Comprehensive cross-tenant integration test sweep

**Theme**: Table-driven parametrized integration test that exercises cross-tenant isolation at every endpoint. Uses existing `create_tenant_with_token` fixture to seed two tenants; for each scenario asserts the expected 404 / empty / 403 behavior.

**Files**:
- `tests/integration/test_tenant_isolation_comprehensive.py` (NEW)

**Specifics**:

Test matrix:

| Endpoint | Scenario | Expected |
|---|---|---|
| booking | tenant_b token POSTs with tenant_a's existing request_id (cross-tenant idempotency check) | 200 NEW decision created (request_id unique per tenant, so this is allowed; assert decision row count for tenant_a unchanged) |
| booking | tenant_b token POSTs with customer external_id that exists in tenant_a only | 200 NEW customer created under tenant_b (per-tenant external_id namespace) |
| modification | tenant_b token POSTs modification with tenant_a's `original_request_id` | 404 — "Original booking not found" (the WHERE tenant_id filter scopes the lookup) |
| modification | tenant_b token POSTs modification with `original_request_id` that exists in BOTH tenants (independently) → operates on tenant_b's instance only | 200; assert decisions count for tenant_a unchanged |
| feedback | tenant_b token POSTs feedback for tenant_a's `target_request_id` | 404 — "target_request_id not found" |
| feedback | tenant_b token POSTs feedback for `target_request_id` that exists in BOTH tenants → operates on tenant_b's instance only | 200, applied=True; assert tenant_a's customer_baselines unchanged (no r_n increment on tenant_a's customer) |
| feedback | tenant_b token POSTs feedback expecting to update tenant_a's `customers.flagged_count` | 404 (target not found in tenant_b); flagged_count for tenant_a customer unchanged |

Plus regression cases:

| Endpoint | Scenario | Expected |
|---|---|---|
| modification velocity SQL | tenant_a's modifications do not count toward tenant_b's `modification_velocity_1h` | velocity returns 0 for tenant_b's customer despite tenant_a having many modifications |
| feedback monotonicity | tenant_b feedback labels do not affect tenant_a's monotonicity state for the same nominal `target_request_id` | each tenant has independent monotonicity |
| baseline counters | tenant_a's customer baseline `rejected_email_hmacs` does not leak into tenant_b's Context for the same email | tenant_b's `email_previously_rejected` Context field = False even if tenant_a's same-email customer has rejection |
| auth | api_token belonging to tenant_a cannot be presented as Bearer to scope to tenant_b | 401/403 per existing auth; sanity test |

Total: ~12 test scenarios parametrized.

Implementation uses pytest.mark.parametrize for the table-driven shape. Fixture pattern:
```python
@pytest.fixture
async def two_tenants(db_conn):
    async with create_tenant_with_token(db_conn) as (token_a, tenant_a):
        async with create_tenant_with_token(db_conn) as (token_b, tenant_b):
            yield (token_a, tenant_a, token_b, tenant_b)
```

**Validation**:
- `pytest tests/integration/test_tenant_isolation_comprehensive.py -v` — 12 tests pass.
- `pytest tests/ --asyncio-mode=auto -q` — full suite green.
- Re-run audit doc cross-check: every scenario asserted here maps to a row in the audit's query inventory.

**Risk**: **Low-medium**. Test-only. Risk is fixture composition complexity (two tenants in nested context managers).

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 12 integration tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: test-only — test-reviewer + senior-engineer + code-flow.

---

## 3C.3 — Non-superuser RLS enforcement verification test

**Theme**: ONE integration test that connects as the `riskd_app` non-superuser role (created in `0001_initial.py`), sets `SET LOCAL app.tenant_id = <tenant_a>`, and verifies that SELECT queries against tenant-scoped tables return only tenant_a's rows despite tenant_b's data being present. This is the canary test that catches RLS policy misconfigurations — something the comprehensive sweep above CANNOT do because the app's superuser connection bypasses RLS.

**Files**:
- `tests/integration/test_rls_enforcement_under_riskd_app.py` (NEW)
- `tests/conftest.py` (EDIT — add a `riskd_app_conn` fixture that connects to the test DB as `riskd_app` for the duration of one test; cleaned up afterward)

**Specifics**:

Fixture in conftest.py:
```python
@pytest_asyncio.fixture
async def riskd_app_conn(test_database_url):
    """Connect to the test DB as the riskd_app non-superuser role.

    Used only by Phase 3C.3 to prove RLS policies enforce. The role exists
    from migration 0001_initial.py; this fixture grants it LOGIN
    temporarily, connects, then revokes.

    NOTE: this temporarily mutates a role privilege. Tests using this
    fixture MUST run serially (xdist-incompatible) and the fixture MUST
    revoke LOGIN on teardown even on test failure.
    """
    # Connect as superuser to grant LOGIN
    super_conn = await asyncpg.connect(test_database_url)
    try:
        await super_conn.execute("ALTER ROLE riskd_app WITH LOGIN PASSWORD 'rls_test_only';")

        # Build a riskd_app DSN
        riskd_url = test_database_url.replace(<superuser_creds>, "riskd_app:rls_test_only")
        riskd_conn = await asyncpg.connect(riskd_url)
        try:
            yield riskd_conn
        finally:
            await riskd_conn.close()
    finally:
        await super_conn.execute("ALTER ROLE riskd_app WITH NOLOGIN PASSWORD NULL;")
        await super_conn.close()
```

Test:
```python
@pytest.mark.serial  # not parallelizable due to role state mutation
async def test_rls_enforces_tenant_isolation_under_riskd_app(
    db_conn, riskd_app_conn, two_tenants_with_data,
):
    """Connect as non-superuser, set tenant_a context, assert tenant_b rows invisible.

    Setup: db_conn (as superuser) seeds 3 shipments for tenant_a and
    3 shipments for tenant_b.

    Test: riskd_app_conn (as non-superuser) does:
        SET LOCAL app.tenant_id = '<tenant_a_id>';
        SELECT count(*) FROM shipments;

    Assert: count = 3 (tenant_a's only), NOT 6 (combined).

    This proves the tenant_isolation policy on shipments is active under
    a non-superuser role. If RLS were misconfigured (e.g. wrong policy
    expression, USING NOT ENABLE'd), count would be 6.
    """
    token_a, tenant_a, token_b, tenant_b = two_tenants_with_data
    # Seed data already exists via fixture; verify shape:
    # superuser sees 6 shipments total
    total = await db_conn.fetchval("SELECT count(*) FROM shipments WHERE tenant_id IN ($1, $2)", tenant_a, tenant_b)
    assert total == 6

    # non-superuser with tenant_a context sees 3
    async with riskd_app_conn.transaction():
        await riskd_app_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_a))
        count_a = await riskd_app_conn.fetchval("SELECT count(*) FROM shipments")
        assert count_a == 3

    # non-superuser with tenant_b context sees 3
    async with riskd_app_conn.transaction():
        await riskd_app_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_b))
        count_b = await riskd_app_conn.fetchval("SELECT count(*) FROM shipments")
        assert count_b == 3

    # non-superuser with NO tenant context: depends on policy default (likely 0 rows; document outcome)
    async with riskd_app_conn.transaction():
        # current_setting('app.tenant_id') raises if unset and policy uses ::int cast
        # we expect either 0 rows or an error — assert one of these
        try:
            count_none = await riskd_app_conn.fetchval("SELECT count(*) FROM shipments")
            assert count_none == 0
        except asyncpg.PostgresError:
            pass  # acceptable: policy refuses unset tenant context
```

Additional tests (in same file):
1. Same shape for `customers`.
2. Same shape for `decisions`.
3. Same shape for `feedback`.
4. Same shape for `customer_baselines`.
5. Negative test: with `app.tenant_id` set to a tenant that doesn't exist (random int), SELECT returns 0 (not error, not all rows).

Total: 6 tests in this file.

**Validation**:
- `pytest tests/integration/test_rls_enforcement_under_riskd_app.py -v -m serial` — 6 tests pass.
- `pytest tests/ --asyncio-mode=auto -q -m 'not serial'` — non-serial suite passes.
- `pytest tests/ --asyncio-mode=auto -q` — full suite (including serial) green.

**Risk**: **Medium**. Test mutates database role state (`ALTER ROLE riskd_app WITH LOGIN`). If the fixture crashes between grant and revoke, the role is left in a `LOGIN` state which is a security regression in test DB (and a potential leak if test DB credentials are shared elsewhere). Fixture MUST revoke in `finally` even on failure. Reviewer must scrutinize the fixture cleanup discipline.

**Reversibility**: Easy — delete the test file and the fixture; role returns to NOLOGIN.

**Pre-commit verification**: Pre-commit unit tests skip integration; pytest with `-m serial` runs the new test once. All gates green.

**Observability**: N/A.

**Test changes**: 6 integration tests + 1 fixture.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: test-only with database-role nuance — test-reviewer + senior-engineer + code-flow + db-reviewer.

---

## Batch 3C summary table

| Commit | Theme | Files | Tests added | Risk | Reviewer panel |
|---|---|---|---|---|---|
| 3C.1 | RLS audit doc | `docs/security-audit-rls-phase-3.md` | 0 | Low | doc-reviewer |
| 3C.2 | Comprehensive cross-tenant test sweep | 1 new test file | 12 | Low-medium | test-reviewer + senior + code-flow |
| 3C.3 | Non-superuser RLS verification test | 1 new test file, `tests/conftest.py` | 6 | Medium | test-reviewer + senior + code-flow + db-reviewer |
| **Total** | | | **18 new tests** | | |

Expected test count at end of Batch 3C: **553 + 18 = 571 tests**.

Rule count at end of Batch 3C: **79 rules** (unchanged).

ALLOWED_CONTEXT_FIELDS count at end of Batch 3C: **66 fields** (unchanged).

Migrations count at end of Batch 3C: **4** (unchanged — no policy completion migration needed per verification).
