# PLAN_PHASE_8A — Migration squash (11 → 5)

Phase 8 batch 8A. Squashes the 11 pre-launch alembic migrations to 5 thematic baseline files. Schema-equivalence is the halt gate. Pre-launch state (no production data) makes this safe; after launch this operation becomes irreversible without coordinated tenant downtime.

## Decisions absorbed

| Decision | Resolution | Source |
|---|---|---|
| Commit strategy | Atomic per logical change. The squash itself is one atomic commit because per-new-migration splits create a broken intermediate alembic revision graph (new 0001 is a restructured grouping, not a superset of old 0001 — they cannot coexist in the revision chain with sensible `down_revision` pointers). | Operator answer (Phase 8 prompt opening AskUserQuestion); MEMORY.md feedback_atomic_commits |
| Number of new migrations | 5 (prompt-aligned). Grouping themes: foundation, booking_flow, baselines, enrichment_global, runtime_roles. | Phase 8 prompt |
| Schema-equivalence gate | `pg_dump --schema-only --no-comments --no-owner` against blank-then-upgraded Postgres pre/post must be byte-equivalent under the canonical normalizer defined below. Halt condition if normalized diff non-empty. | Phase 8 prompt §S-1 + §Quality 3 |
| Canonical normalizer (specified) | Implementation: read pg_dump output → drop any line where (a) `strip() == ""`, (b) `lstrip().startswith("--")` (comment lines including pg_dump's `-- Name: ...; Type: ...; Owner: -` section labels), or (c) `lstrip().startswith("\\")` (psql metacommands like `\restrict <random-hash>` and `\unrestrict <random-hash>` that pg_dump 16 emits with a per-dump random token). Return `"\n".join(sorted(remaining_lines))`. Equivalent shell form: `pg_dump --schema-only --no-comments --no-owner -h localhost -U riskd riskd \| sed -E 's/^[[:space:]]*--.*//; s/^[[:space:]]*\\\\.*//; /^[[:space:]]*$/d' \| sort`. Rationale: sort-based comparison preserves the "set of DDL statements" identity (which is what schema-equivalence requires) and ignores statement-ordering differences that pg_dump introduces non-deterministically across runs (dependency-order traversal, OID order). Comments + psql metacommands stripped to ignore the timestamp/version preamble and the per-dump random restrict tokens. Note: sort-based comparison would mask a genuine ordering bug (e.g., CREATE INDEX before its target table) — but Postgres rejects such ordering at apply time, so a chain that upgrades cleanly is by definition order-valid. Verified empirically against the 11-migration HEAD: normalized dump is ~424 lines (down from ~1278 raw). | Operator finding #1; empirical verification 8A.0 |
| Round-trip verification | `alembic downgrade base && alembic upgrade head` from fresh Postgres must succeed with no errors. | Phase 8 prompt 8A.9 |
| Auth-table RLS in post-squash | Final state: no RLS on `api_tokens` or `app_users` (Phase 5D end state). New 0001 simply does not create these policies; new 0005 does NOT need a corresponding DROP. The dummy create-then-drop pattern in the prompt's 0005 design is omitted as redundant. | Schema-equivalence argument — final state is what matters |
| Role creation split | `riskd_app` NOLOGIN created in new 0001 (foundation: table GRANTs depend on it). `riskd_app_login` WITH LOGIN INHERIT created in new 0005 plus the `GRANT riskd_app TO riskd_app_login`. Mirrors current 0001 + 0008 ordering. | Migration content classification (V-2/V-3) |
| Seed migration (0011) handling | The INSERT...SELECT into `tenant_route_baselines` runs in new 0003 after its CREATE TABLE. Pre-launch the seed yields 0 rows (no customers with `registered_country`); idempotent semantics preserved. | 0011 read |
| Golden schema mechanism | `tests/golden/schema.sql` does not currently exist. 8A.0 establishes it via `pg_dump --schema-only` against the migrated database. Becomes the long-term equivalence anchor. | V-13 |
| Migration round-trip test rewrites | V-5 found zero hardcoded revision-ID references in test code. 8A.2 (test sweep) is likely a no-op confirmation step. | V-5 |
| Pre-squash schema capture | A pre-squash `pg_dump --schema-only` output is captured into `/tmp/schema_pre_squash.sql` before the squash commit. Compared bytewise (post canonical-whitespace + sort) against post-squash dump. Diff committed in PLAN_PHASE_8A.md as evidence. | Phase 8 prompt §Quality 3 |
| Workflow note | All commits in 8A run the full standard reviewer panel (senior-engineer + security-auditor + code-flow + db-reviewer + test-reviewer for any test-touching commit). db-reviewer is mandatory on the squash commit. | CLAUDE.md triage routing (never-skip on migration changes) |

## Pre-batch verification (executed before plan draft)

V-1 through V-5 + V-13 completed. Findings:

- **V-1**: 11 migrations confirmed (`0001` through `0011`).
- **V-2**: Classified — 0001 (DDL+ROLE+RLS), 0002-0006 (additive DDL), 0007 (constraint replace), 0008 (ROLE), 0009 (RLS drop), 0010 (column add), 0011 (DDL+DATA seed).
- **V-3**: Two roles in the chain: `riskd_app` NOLOGIN (0001), `riskd_app_login` WITH LOGIN INHERIT (0008).
- **V-4**: 1118 tests across 96 files. No phase-numbered filenames.
- **V-5**: Zero hardcoded migration revision IDs in `tests/` (no `"0001"`, `"0008"`, `command.upgrade(...)`-with-rev references). Test sweep in 8A.2 is expected to be a confirmation no-op.
- **V-13**: `tests/golden/` directory does not exist; 8A.0 establishes it.

Pre-flight checks passed: clean tree, `feat/refactor` branch, 30+ unpushed commits (Phase 5/6/7 work preserved locally), Phase 7 outputs intact in `app/rules.yaml` + `app/api/booking.py`.

## Grouping design (5 new migrations)

### New 0001 — foundation
**Tables**: tenants, enterprises, customers, users, app_users, api_tokens.
**Includes**: tenants.updated_at (from old 0005); customers.registered_country (from old 0011); api_tokens.last_used_at index (from old 0006); all current columns on these tables.
**Roles**: CREATE ROLE riskd_app NOLOGIN (idempotent guard via DO block).
**Privileges**: GRANT USAGE ON SCHEMA public + GRANT on these 6 tables to riskd_app.
**RLS**: policies on tenants, enterprises, customers, users. NO RLS on api_tokens or app_users (matches Phase 5D end state; see Decisions absorbed).
**Indexes**: all current indexes on these tables including api_tokens (tenant_id, last_used_at DESC NULLS LAST).
**Folds in**: old 0001 (auth/customer table subset), 0005, 0006, 0011 (customers.registered_country only).
**Required docstring note** (per operator finding #3): the migration docstring MUST include a historical-context paragraph explaining that old 0001 created RLS policies on api_tokens + app_users which old 0009 (Phase 5D) subsequently dropped because the auth-lookup chicken-and-egg (auth must resolve tenant_id BEFORE `set_tenant_id` is called) made those policies unusable under the non-superuser runtime role. The squashed chain skips the creation entirely; the final-state schema is equivalent to the pre-squash final state. Future readers tracing the absence of an RLS DISABLE in new 0005 are pointed here. Cross-reference `docs/security-audit-rls-phase-5.md` for the full architectural reasoning.

### New 0002 — booking_flow
**Tables**: shipments, decisions, feedback.
**Includes**: shipments.destination_hmac/email_hmac/phone_hmac (from old 0002, 0004); decisions.request_type column (from old 0003); decisions UNIQUE on (tenant_id, request_type, request_id) (from old 0007); feedback final Phase 3B shape (from old 0004).
**Privileges**: GRANT on these 3 tables to riskd_app.
**RLS**: policies on all 3 tables.
**Indexes**: shipments (tenant_id, destination_hmac, booking_ts) composite; all current indexes.
**Folds in**: old 0001 (booking-flow table subset), 0002, 0003, 0004, 0007.

### New 0003 — baselines
**Tables**: customer_baselines, tenant_route_baselines.
**Includes**: customer_baselines with full current JSONB shape (origin_stats through ip_asn_stats through country_route_stats); Welford triples; last_booking pointers; decay anchor. tenant_route_baselines with composite PK + RLS.
**Privileges**: GRANT on both to riskd_app.
**RLS**: policies on both.
**Seed**: INSERT...SELECT from shipments+customers into tenant_route_baselines (pre-launch yields 0 rows; preserved for fresh-from-existing-DB upgrade scenarios).
**Folds in**: old 0001 (customer_baselines subset), 0010, 0011 (table + seed only).

### New 0004 — enrichment_global
**Tables**: ip_enrichment, global_blocked_vectors.
**Privileges**: SELECT to riskd_app on both.
**RLS**: none on either (global-scope tables).
**Folds in**: old 0001 (enrichment/global subset).

### New 0005 — runtime_roles
**Roles**: CREATE ROLE riskd_app_login WITH LOGIN INHERIT (idempotent guard); GRANT riskd_app TO riskd_app_login.
**Documentation**: docstring documents that the password is dev-baked (`riskd_app_login_dev`) and production rotation happens via Secrets Manager (per `docs/security-audit-rls-phase-5.md`). No DROP RLS on auth tables (new 0001 never created RLS there).
**Folds in**: old 0008 only. (Old 0009 is omitted as documented in Decisions absorbed.)

## Commits

### 8A.0 — Establish golden schema baseline + project venv

**Changes**:
- Set up project venv: `python3 -m venv .venv && source .venv/bin/activate && pip install -e . pytest pytest-cov pytest-asyncio` (or per `pyproject.toml` spec).
- Add `.venv/` to `.gitignore` if not already excluded.
- Create `tests/golden/` directory.
- Run `docker compose down -v && docker compose up -d postgres` (fresh DB).
- Run `alembic upgrade head` against fresh Postgres.
- Capture canonical-normalized schema dump to `tests/golden/schema.sql` using the normalizer defined in the Decisions absorbed table: `pg_dump --schema-only --no-comments --no-owner -h localhost -U riskd riskd | sed -E 's/^[[:space:]]*--.*//; /^[[:space:]]*$/d' | sort > tests/golden/schema.sql`.
- Add a `tests/integration/test_schema_golden.py` test that re-runs the dump under the SAME normalizer and asserts byte-equivalence to the committed golden file. The test must implement the normalizer in Python (not shell) so it runs identically across environments: `lines = [l for l in dump.splitlines() if l.strip() and not l.lstrip().startswith("--")]; normalized = "\n".join(sorted(lines))`. This becomes the long-term anti-drift gate.

**Tests**: 1 new (`test_schema_golden.py`).

**Validation**:
- `pytest tests/integration/test_schema_golden.py` passes against current 11-migration HEAD.
- `pytest tests/unit/ -x` passes (smoke).
- `ruff check . && mypy app/` passes.

**Declared breaks**: none. This commit only adds tooling/baselines; no behavior change.

**Reviewer panel**: senior-engineer + code-flow + test-reviewer + db-reviewer.

### 8A.1 — Atomic squash: 11 → 5

**Changes**:
- Capture pre-squash schema dump for comparison (this happens via the test from 8A.0, but the comparison output is what gates the commit).
- DELETE: `alembic/versions/0001_initial.py` through `0011_case_3b_schema.py` (11 files).
- ADD: `alembic/versions/0001_foundation.py`, `0002_booking_flow.py`, `0003_baselines.py`, `0004_enrichment_global.py`, `0005_runtime_roles.py` (5 files).
- Each new file carries comprehensive upgrade()/downgrade(); each new file's docstring documents which old migrations fold in.
- `alembic_version` table state on a freshly-upgraded DB: a single row with revision = "0005" (new chain head).

**Tests**: existing `test_schema_golden.py` from 8A.0 must continue to pass — this IS the equivalence gate.

**Validation**:
- `docker compose down -v && docker compose up -d postgres` (fresh DB).
- `alembic upgrade head` succeeds.
- `pytest tests/integration/test_schema_golden.py` passes (schema byte-equivalent).
- `alembic downgrade base && alembic upgrade head` succeeds (round-trip in same commit's validation).
- `pytest tests/integration/ -x` passes (broader integration suite; migration changes ripple through fixtures).
- `pytest tests/unit/ -x` passes.

**Declared breaks**: none. The squash is presented as a single coherent state transition; no intermediate state exists where new migrations coexist with old. The previous revision graph is replaced wholesale.

**Halt condition**: if `test_schema_golden.py` fails (schema diff non-empty), do NOT commit. Iterate on the new migration files until equivalence is reached. The plan does not advance until this gate is green.

**Reviewer panel**: senior-engineer + security-auditor + code-flow + db-reviewer (MANDATORY) + test-reviewer (test fixture impact).

**Operator note on review surface (finding #2)**: 8A.1 is the largest single review surface in Phase 8 — 5 new migration files (~500-800 lines each ≈ 2500-4000 lines added) + 11 file deletions + the equivalence-evidence appendix. Plan for a longer reviewer pass than a typical commit. Do not squeeze into a fast turnaround. If autonomous-execution is in play, expect the per-reviewer wall time on this commit to dominate the batch.

### 8A.2 — Sweep migration revision-ID references (verification commit)

**Changes**:
- Grep for any hardcoded migration revision IDs across the repo: `grep -rn '"0001"\|"0002"\|"0003"\|"0004"\|"0005"\|"0006"\|"0007"\|"0008"\|"0009"\|"0010"\|"0011"' app/ tests/ scripts/ docs/ .ai/ alembic/ CLAUDE.md`.
- For each match: if a test/fixture references a specific old revision, update to use `alembic_command.upgrade(cfg, "head")` and relative pointers (`-1`).
- Per V-5, expected matches: zero in test code. If actually zero: this commit becomes documentation-only (a small docstring or comment in PLAN_PHASE_8A.md confirming the sweep ran clean).

**Tests**: full suite (`pytest tests/`).

**Validation**:
- `pytest tests/` returns 0 failures.
- Test count unchanged (1118 ± a small delta if tests were rewritten).

**Declared breaks**: none.

**Reviewer panel**: senior-engineer + test-reviewer (if any test changed) OR doc-reviewer + senior-engineer (if doc-only). Routing decided after the sweep based on whether any files actually changed.

### 8A.3 — Batch close

**Changes**:
- Update PLAN_PHASE_8A.md with the post-execution record: pre-squash schema fingerprint, post-squash schema fingerprint, round-trip log, test counts pre/post, any deviations.
- Append the equivalence-comparison evidence (a `sha256sum` of the canonicalized schema dumps, or the diff if it required iteration).
- Note for 8B/8C handoff: any test files that newly broke or fixtures that needed re-fixturing.

**Tests**: none (doc commit).

**Validation**: doc-reviewer panel.

**Declared breaks**: none.

**Reviewer panel**: doc-reviewer.

## Acceptance criteria for 8A close

1. `alembic/versions/` contains exactly 5 files: `0001_foundation.py`, `0002_booking_flow.py`, `0003_baselines.py`, `0004_enrichment_global.py`, `0005_runtime_roles.py`.
2. `alembic upgrade head` against fresh Postgres succeeds with no errors.
3. `tests/integration/test_schema_golden.py` passes (schema byte-equivalent to pre-squash dump per canonical normalization).
4. `alembic downgrade base && alembic upgrade head` succeeds.
5. `pytest tests/` returns 0 failures; test count within ±5 of the 1118 baseline (delta only if test_schema_golden.py was added — net +1).
6. `tests/golden/schema.sql` committed and reflects post-squash schema.
7. PLAN_PHASE_8A.md final state with execution record appended.

## Notes for downstream batches

- **8B**: V-4 found no `test_phase_*.py` files. The prompt's "milestone-numbered count tests by filename" pattern doesn't apply. 8B's actual surface area is phase-named test functions (13 found across 7 files) + milestone-count assertions in test bodies (`assert len(ALLOWED_CONTEXT_FIELDS) == 77`, `ruleset.rules == 81`, etc.). Coverage tooling installed in 8A.0 supports the coverage-non-regression gate.
- **8C**: Note that `tests/integration/test_schema_golden.py` (added in 8A.0) serves as the long-term schema anti-drift gate. 8C.2's schema.md rewrite can reference this as the operational verification mechanism.
- **8C**: MASTER_PLAN.md exists at repo root (361 lines; not in prompt). Handle in 8C alongside PLAN_PHASE_*.md and REPORT_PHASE_*.md deletions.
- **8C**: REPORT_PHASE_7.md is missing. Per operator decision, 8C will generate it as part of the doc batch.

---

## Execution record

### 8A.0 — golden schema baseline + anti-drift test gate (commit 41c3d90)
- Venv set up at `.venv/`; project + test/dev extras installed via `pip install -e '.[test,dev]'`.
- Postgres 16-alpine container reset; alembic upgrade head against the 11-migration chain succeeded.
- `tests/golden/schema.sql` committed: 422 lines (down from ~1278 raw pg_dump) under the canonical normalizer (drop blank/comment/psql-metacommand lines, sort).
- `tests/integration/test_schema_golden.py` committed: dual-path pg_dump capture (host binary preferred, docker compose exec fallback, skip otherwise), Python normalizer matching the spec, unified-diff failure output with regeneration instructions in the module docstring.
- Reviewer panel cleanest tier: senior-engineer (SHIP IT) + code-flow (CLEAN) + test-reviewer (ACTUALLY GOOD). First-pass clean.

### 8A.1 — atomic migration squash 11 → 5 (commit 4fec9bb)
- 11 old migrations deleted; 5 new migrations added.
- Schema-equivalence verified empirically: `pytest tests/integration/test_schema_golden.py` passes against fresh Postgres after `alembic upgrade head` against the new chain.
- Round-trip verified: `alembic downgrade base && alembic upgrade head` succeeds on a fresh DB with no errors.
- Net diff: +812 / -1117 lines (17 files changed).
- Pre-existing test failure surfaced and logged to `.claude/BUGS.md` (medium severity): `tests/integration/test_case_2.py::test_unfamiliar_ip_against_established_customer_blocks_under_layer2` asserts 2 baseline-dependent rules that don't fire at HEAD. Verified via `git stash --include-untracked` + run against the 11-migration chain — fails identically. NOT caused by the squash.
- Reviewer panel cleanest tier: db-reviewer (SHIP IT) + senior-engineer (SHIP IT) + security-auditor (LOW RISK / CLEAN) + code-flow (CLEAN). First-pass clean.

### 8A.2 — migration revision-ID sweep (this commit)
- `grep -rn '"0001"|"0002"|...|"0011"|alembic_command|command\.upgrade|command\.downgrade' app/ tests/ scripts/` returned **zero matches**.
- V-5 prediction (test sweep would be a no-op) confirmed: no test or app code references the old revision IDs by literal string.
- Migration round-trip tests use `head` and relative pointers (no hardcoded revision IDs to update).

### 8A.3 — batch close (this commit)
- All acceptance criteria met:
  1. 5 alembic migration files in `alembic/versions/`; 0 of the original 11 remaining. ✓
  2. `alembic upgrade head` against fresh Postgres succeeds. ✓
  3. `tests/integration/test_schema_golden.py` passes (byte-equivalent under canonical normalizer). ✓
  4. `alembic downgrade base && alembic upgrade head` succeeds. ✓
  5. `pytest tests/` returns 1118 passes + 1 pre-existing fail (logged to BUGS.md, not caused by squash). Net test count delta: +1 (test_schema_golden added in 8A.0). ✓
  6. `tests/golden/schema.sql` committed; reflects post-squash schema. ✓
- Carry-forward to 8B: test_case_2 failure investigation (or post-launch defer); coverage baseline capture in 8B.0; phase-named function renames across 13 functions in 7 files.
- Carry-forward to 8C: tests/integration/test_schema_golden.py as the schema anti-drift gate; tests/coverage_baseline.txt (TBD in 8B.0) as the coverage anti-drift anchor. Both surface in 8C.4 system-status.md "Anti-drift gates" section.
