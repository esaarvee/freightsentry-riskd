# Phase 4 — Batch 4A Report

**Phase**: 4 of 6 (Week 4)
**Batch**: 4A — TenantConfig foundation
**Commits**: 7 implementation (4A.1 through 4A.7) + 1 plans commit + 1 BUGS amendment + this report
**Date range**: 2026-06-01
**Status**: COMPLETE

## Batch 4A invariants achieved

- **TenantConfig Pydantic v2 model** in `app/tenant_config.py` — validation boundary between the JSONB stored in `tenants.config` and the runtime scoring/rule paths.
- **Per-request loader** `load_tenant_config(conn, tenant_id)` wired into all 3 endpoints (booking, modification, feedback) inside their existing transactions, after `set_tenant_id`.
- **Migration 0005** adds `tenants.updated_at timestamptz NOT NULL DEFAULT now()` for staleness tracking.
- **Tenant onboarding CLI** `scripts/tenant_onboard.py` — idempotent UPSERT-by-name with `pg_advisory_xact_lock` serialization, RLS session-var hardening, and `--rotate-token` that actually revokes prior tokens.
- **build_context signature extension** — `tenant_config: TenantConfig` is now a required keyword arg on both `build_context` and `build_modification_context`. 4A.3 declared break / 4A.4 resolution.
- **12 new integration tests** verifying cross-tenant isolation, per-request fresh load, modification + feedback endpoints load the config, and the onboarding script E2E (including the real production bug it surfaced).

## Aggregate stats

| Metric | Pre-Phase-4 (end of Phase 3) | Post-4A |
|---|---|---|
| Rule count | 79 | 79 (unchanged; 4B rewrites 7) |
| Test count | 675 | 729 (+54) + 1 intentional skip |
| ALLOWED_CONTEXT_FIELDS | 66 | 66 (unchanged; 4B adds 5) |
| Migrations | 4 | 5 (+1: `tenants.updated_at`) |
| Endpoints | 4 | 4 (unchanged; 4D adds 2 admin) |
| `.ai/decisions.md` sections | — | +1 (TenantConfig design subsection under existing Per-tenant configuration) |
| New modules under `app/` | — | +1 (`app/tenant_config.py`) |
| New scripts | — | +1 (`scripts/tenant_onboard.py`) |
| BUGS.md entries | — | +1 (ruff version drift) |

## Per-commit disposition

### 4A.1 — TenantConfig Pydantic v2 model (`63628eb`)
- New `app/tenant_config.py` with `TenantConfig` model + `parse_config_jsonb` helper
- `extra="forbid"`, `frozen=True`, ISO 4217 currency validation, 4-tier value_caps strict matching, `mode="before"` validator on `value_caps` so bool thresholds are rejected before Pydantic coerces `True → 1.0`
- 25 unit tests (plan called for 20; +5 from reviewer-fold: 2 maturity_shipments bounds + 1 bool-threshold rejection + 2 value_caps tier-shape edge cases)
- **Reviewer panel cycle 1**: senior SHIP IT / security LOW RISK / code-flow CLEAN / test ACTUALLY GOOD — all cleanest verdicts on first cycle

### 4A.2 — `load_tenant_config` + tenants.updated_at migration (`d02daea`)
- `alembic/versions/0005_tenants_updated_at.py` (additive column)
- `load_tenant_config(conn, tenant_id) -> TenantConfig` appended to `app/tenant_config.py` with cast-at-boundary JSONB handling (handles both str and dict asyncpg codec paths)
- 8 DB-backed unit tests
- **Reviewer panel**: 4 cleanest cycle-1; test-reviewer NEEDS WORK cycle-1 → ACTUALLY GOOD cycle-2. Material catch: both "codec path" tests originally hit the same `str` branch (false-pass coverage); dict test now registers a real JSONB codec via `set_type_codec` to genuinely exercise the else branch. Bonus tzinfo assertion + `match=` scoping added.

### 4A.3 — Extend build_context signatures (declared break) (`a986a5d`)
- Added `tenant_config: TenantConfig` as required keyword arg to `build_context` and `build_modification_context`. Marker `_ = tenant_config` keeps mypy quiet until 4B/4C consume.
- 3 unit tests (2 pass, 1 skipped — re-enabled at 4B.4 when whitelist grows 66→71)
- Pre-commit hooks bypassed via `--no-verify` per CLAUDE.md declared-break policy. Specifically bypassed: pytest tests/unit/ (94 expected failures) + mypy app/ (2 errors at endpoint call sites). ruff/format still passed.
- **No reviewer panel** (declared-break commit; resolved in 4A.4 which gets the panel)

### 4A.4 — Wire load_tenant_config into 3 endpoints + restore test fixtures (`d87b382`)
- Loader call site added to booking + modification + feedback endpoints (feedback parks as `_tenant_config` for future 4B+ consumer)
- `make_default_tenant_config()` helper added to `tests/conftest.py`
- 13 call sites in `tests/integration/test_context.py` updated uniformly
- Operator watch point satisfied: every `build_context` / `build_modification_context` call site (16 total: 3 production + 13 test) carries `tenant_config=`
- **Reviewer panel**: 4 cleanest cycle-1; senior-engineer NEEDS MINOR FIXES cycle-1 → SHIP IT cycle-2. Material catch: ruff version drift between pre-commit pin (0.6.0) and local install (0.15.7) caused `ruff format app/ tests/` to reformat 22 unrelated files, inflating diff from planned 5 files to 27. Reverted via `git checkout HEAD -- <files>`; in-scope 5-file diff restored. Logged to BUGS.md as a workflow follow-up.

### 4A.5 — scripts/tenant_onboard.py (`68cd6ff`)
- Operator CLI: idempotent UPSERT-by-name + initial config write + API token issuance
- 7 helper unit tests (full E2E in 4A.6)
- **Reviewer panel cycle 1**: senior SHIP IT, but security MEDIUM RISK / code-flow MINOR ISSUES / db NEEDS MINOR FIXES / test ACCEPTABLE
- **Cycle 2 fixes**:
  - DB-critical: `set_config('app.tenant_id', ..., true)` added before any `api_tokens` query (without this the script would fail under the production non-superuser `riskd_app` role with "unrecognized configuration parameter")
  - Security-high: `--rotate-token` now actually revokes prior tokens (in-transaction `DELETE FROM api_tokens` before INSERT) — operator using `--rotate-token` after compromise gets prior token invalidated
  - DB/Security-medium: TOCTOU race on `tenants.name` (no UNIQUE constraint) closed via `pg_advisory_xact_lock(hashtext(external_id))` at top of transaction
  - Test: malformed-JSON branch coverage added
  - Code-flow: clarity comment on `_validate_initial_config`'s `tenant_id=1` placeholder pattern
- **Cycle 2 verdicts**: senior SHIP IT (already) / security LOW RISK / code-flow CLEAN / db SHIP IT / test ACTUALLY GOOD

### 4A.6 — Integration tests + bundled production fix (`1e5f4e2`)
- 12 integration tests across 2 new files (test_tenant_config_integration.py: 8 tests; test_tenant_onboard_script_integration.py: 4 tests)
- **Notable real-bug catch**: writing `test_rotate_token_revokes_prior_token` surfaced that 4A.5's `DELETE FROM api_tokens ... RETURNING count(*) OVER ()` was invalid Postgres ("window functions are not allowed in RETURNING"). Cycle-2 reviewers in 4A.5 had flagged this as a clarity suggestion only; the integration test caught it as a hard SQL error. Following the 3B.7 precedent, the script fix is bundled into 4A.6 alongside the test that surfaced it.
- Stored-JSONB corruption test asserts `ValidationError` propagation (4A intentionally has no try/except; Phase 4D/5 may translate to 500 with structured log)
- **Reviewer panel**: senior SHIP IT / code-flow CLEAN / test-reviewer ACTUALLY GOOD — all cleanest verdicts on first cycle. Dead-assertion + status-check hardening folded in pre-commit.

### 4A.7 — `.ai/decisions.md` TenantConfig subsection (`6bbe3c2`)
- 54-line subsection appended under existing "Per-tenant configuration"
- Documents column-reuse decision, final field set (which differs from the historical sketch above), value_caps shape, loading semantics, validation timing, migration 0005, onboarding-script highlights, non-consumer status in 4A, carry-forward items
- **Reviewer**: doc-reviewer PUBLISH. Off-by-one line citation (`0001_initial.py:37-38` → `:36-37`) folded in pre-commit.

## Plan deviations

| # | Deviation | Commit | Reason | Resolution |
|---|---|---|---|---|
| 1 | TenantConfig stored in `tenants.config` (not `config_json`) | 4A.1 | Operator-approved verification finding — column already exists | Plan amended at pre-execution checkpoint |
| 2 | 25 unit tests in 4A.1 (plan called for 20) | 4A.1 | Reviewer-fold: bool-threshold rejection + 2 maturity_shipments bounds + 2 value_caps tier-shape edge cases | Applied during cycle-1 |
| 3 | `value_caps` validator uses `mode="before"` | 4A.1 | Required to reject `True` before Pydantic coerces to `1.0` | Applied during cycle-1 |
| 4 | Loader logs at DEBUG (not INFO) | 4A.2 | Loader fires once per request; INFO would inflate log volume; DEBUG with `metric=True` retains Phase 5 EMF eligibility | Applied during 4A.2 |
| 5 | Real JSONB codec registration in test 7 | 4A.2 | Test-reviewer cycle-1: original dict-codec test silently hit same str branch | Applied in cycle 2 |
| 6 | Pre-commit `--no-verify` on 4A.3 | 4A.3 | Declared-break commit per CLAUDE.md bypass policy | Documented in commit message |
| 7 | Wide 5-file scope on 4A.4 after format revert | 4A.4 | Cycle-1: ruff format reformatted 22 unrelated files; reverted to keep diff focused | Applied in cycle 2 |
| 8 | Feedback endpoint uses `_tenant_config` (underscore prefix) | 4A.4 | Single-underscore signals intentionally-unused; cleaner than `_ = tenant_config` dummy rebind per code-flow | Applied during 4A.4 |
| 9 | `pg_advisory_xact_lock` added to onboarding script | 4A.5 | Security + DB cycle-1 race-window finding | Applied in cycle 2 |
| 10 | `set_config('app.tenant_id', ..., true)` in onboarding script | 4A.5 | DB cycle-1: required for production RLS under riskd_app role | Applied in cycle 2 |
| 11 | `--rotate-token` actually revokes prior tokens | 4A.5 | Security cycle-1 HIGH: semantic mismatch between flag name and behavior | Applied in cycle 2 |
| 12 | `DELETE` without `RETURNING count(*) OVER ()` | 4A.5 / 4A.6 | Cycle-2 clarity suggestion in 4A.5 → 4A.6 integration test surfaced as hard SQL error (window funcs not allowed in RETURNING) | Bundled fix in 4A.6 per 3B.7 precedent |
| 13 | Stored-JSONB corruption test asserts ValidationError (not 500) | 4A.6 | 4A intentionally has no try/except around loader; propagation IS the documented failure mode | Plan-consistent — Phase 4D/5 may revisit |

## Reviewer-caught corrections (file:line refs)

| # | File:line | Finding | Reviewer | Cycle |
|---|---|---|---|---|
| 1 | `app/tenant_config.py:126` | `isinstance(threshold, (int, float))` admits Python bool | security-auditor | 4A.1 c1 |
| 2 | `tests/unit/test_tenant_config_model.py` (model bounds) | `maturity_shipments` validator had no test | test-reviewer | 4A.1 c1 |
| 3 | `app/tenant_config.py` (value_caps validator) | Default mode="after" runs AFTER coercion; bool→1.0 slipped through | (security + test) | 4A.1 c1 |
| 4 | `tests/unit/test_tenant_config_loader.py:102-119` | Both codec tests hit the same str branch (false-pass coverage) | test-reviewer | 4A.2 c1 |
| 5 | `tests/unit/test_tenant_config_loader.py:26` | Redundant `pytestmark = pytest.mark.asyncio` (auto mode covers it) | test-reviewer | 4A.2 c1 |
| 6 | `tests/unit/test_tenant_config_loader.py:97-100` | `pytest.raises(ValidationError)` without `match=` allows false-pass on unrelated field errors | test-reviewer | 4A.2 c1 |
| 7 | 22 unrelated files | ruff version drift caused scope creep in 4A.4 | senior-engineer | 4A.4 c1 |
| 8 | `app/api/feedback.py:114-115` | Redundant `_ = _tenant_config` rebind | code-flow | 4A.4 c1 |
| 9 | `scripts/tenant_onboard.py` | No `set_config('app.tenant_id', ...)` before api_tokens queries; would fail under riskd_app role | db-reviewer | 4A.5 c1 |
| 10 | `scripts/tenant_onboard.py:163-176` | `--rotate-token` did not actually revoke prior tokens | security-auditor | 4A.5 c1 |
| 11 | `scripts/tenant_onboard.py:121-159` | TOCTOU race on tenants.name (no UNIQUE) | security + db-reviewer | 4A.5 c1 |
| 12 | `scripts/tenant_onboard.py:99-109` | `tenant_id=1` magic number in `_validate_initial_config` — no comment explaining placeholder semantics | code-flow | 4A.5 c1 |
| 13 | `tests/unit/test_tenant_onboard_script.py` | `_load_initial_config`'s JSONDecodeError branch was uncovered | test-reviewer | 4A.5 c1 |
| 14 | `scripts/tenant_onboard.py:203-209` | `DELETE...RETURNING count(*) OVER ()` is invalid Postgres (window funcs not allowed in RETURNING) | integration test | 4A.6 (4A.5 cycle-2 reviewers flagged only as clarity) |
| 15 | `tests/integration/test_tenant_onboard_script_integration.py:132` | Dead assertion (`api_token=existing` literal not produced) | test-reviewer | 4A.6 c1 |
| 16 | `tests/integration/test_tenant_config_integration.py:138-145` | Test could silently pass on loader-correct + endpoint-broken; status==200 assertions added | senior-engineer | 4A.6 c1 |
| 17 | `.ai/decisions.md:267` | `0001_initial.py:37-38` line range off-by-one | doc-reviewer | 4A.7 |

**Total corrections**: 17 across 7 implementation commits + 4A.6's production-bug catch (a noteworthy "tests caught what reviewers missed"). Cycle-1 verdict ladder for 4A:

- 4A.1: 4/4 cleanest
- 4A.2: 4/5 cleanest (test NEEDS WORK)
- 4A.4: 4/5 cleanest (senior NEEDS MINOR FIXES)
- 4A.5: 1/5 cleanest (4 non-cleanest)
- 4A.6: 3/3 cleanest
- 4A.7: PUBLISH

Cycle-2 panels for 4A.2, 4A.4, 4A.5 all reached cleanest verdicts within the 3-cycle cap. No deadlocks.

## Tangential issues logged to BUGS.md

1. **2026-06-01 — ruff version drift between pre-commit pin and local install** (severity=low / workflow). Pre-commit ruff is pinned to 0.6.0 while the local install is 0.15.7. `ruff format app/ tests/` over the whole tree reformats ~22 files unrelated to the current task, inflating the review surface. Caught in 4A.4 cycle-1 review and reverted; in-scope diff restored. Suggested action: bump the ruff pin in `.pre-commit-config.yaml` to match current ecosystem version, run `ruff format` across the codebase once in a dedicated formatting-sync commit, and land that BEFORE the next phase to avoid re-running into the same scope-creep risk.

## Production bug caught during 4A execution

**Onboarding script `DELETE ... RETURNING count(*) OVER ()`** (4A.5 → 4A.6). The cycle-2 reviewers in 4A.5 flagged the `RETURNING count(*) OVER ()` pattern as a clarity suggestion ("could be simpler"). 4A.6's integration test `test_rotate_token_revokes_prior_token` exercised it for the first time and surfaced `asyncpg.exceptions.WindowingError: window functions are not allowed in RETURNING`. The fix (revert to `conn.execute` with the already-known `token_count`) was bundled into 4A.6 per the 3B.7 precedent. Lesson: clarity suggestions for unfamiliar SQL idioms warrant a quick correctness sanity check, not just a suggestion-tier note.

## Explicitly deferred items (to Phase 5 or post-launch)

| Item | Original scope | Deferred to | Reason |
|---|---|---|---|
| In-process tenant-config cache (60s TTL) | Phase 5 | Phase 5 | Pre-existing carry-forward; consistent with plan |
| `UNIQUE (name)` on `tenants` | Phase 5 BUGS.md candidate | Phase 5+ | Would replace advisory-lock pattern with `INSERT ON CONFLICT (name)`; out of 4A scope |
| Promote `_hash_token` to public `hash_api_token` helper | Phase 5+ | Phase 5+ | Cross-module coupling concern raised by reviewers; defer to follow-up |
| `--overwrite-config` flag for onboarding script | post-4A | Phase 5+ | Currently re-runs silently overwrite tenants.config (documented in docstring); explicit flag would be more conservative |
| try/except around `load_tenant_config` → 500 response | Phase 4D admin / Phase 5 hardening | Phase 5 | Phase 4A intentionally lets ValidationError propagate; Phase 4D admin endpoints may add the translation layer |
| Stored-config write endpoint | v2+ | v2+ | Admin endpoints in Phase 4D are read-only |

## Phase 4B inheritance

Phase 4B (currency normalization) starts with:

1. `TenantConfig.value_caps` field shape (4-tier per-currency) defined and validated
2. `TenantConfig.allowed_currencies` defined; `DEFAULT_ALLOWED_CURRENCIES = ["USD"]`
3. `load_tenant_config` wired into all 3 endpoints; per-request fresh load operational
4. `build_context` / `build_modification_context` accept `tenant_config` and have `_ = tenant_config` marker awaiting consumers (4B.4 removes the marker and populates 5 currency-derived ctx fields)
5. The skipped test `tests/unit/test_context_tenant_config_passthrough.py::test_ctx_shape_unchanged_in_4a` is re-enabled at 4B.4 with the 71-field whitelist assertion
6. Currency-implicit-USD assumption documented in `.ai/decisions.md § Currency normalization` — Phase 4B implements the resolution and the section becomes "RESOLVED in Phase 4B"

## Performance notes

**Booking endpoint**: 9 sequential awaits + 1 (load_tenant_config) = 10. ~1ms added by the indexed PK lookup. Phase 5 cache eliminates.
**Modification endpoint**: 11 + 1 = 12.
**Feedback endpoint**: 5-8 + 1 = 6-9.

Latency budget impact within the documented <200ms p95 target. Phase 5 load test revisits.

## Tests status

| Component | Pre-4A | Post-4A | Delta |
|---|---|---|---|
| Unit (`tests/unit/`) | ~430 | ~470 | +40 (4A.1 model + 4A.2 loader + 4A.3 signature + 4A.5 helper) |
| Integration (`tests/integration/`) | ~245 | ~257 | +12 (4A.6) |
| Security (`tests/security/`) | unchanged | unchanged | 0 |
| **Total** | **675** | **729** | **+54** + 1 intentional skip |

All 729 tests pass. ruff clean. mypy strict clean (26 source files). Migration round-trip verified.

## Phase 4B pre-flight

Before Phase 4B execution, operator should:

- Drain `.claude/BUGS.md` of any 4A entries (1: ruff version drift — Phase 5 candidate)
- Confirm `REPORT_PHASE_4A.md` matches the operator's understanding
- Approve `PLAN_PHASE_4B.md` (operator preference: deferred per-batch checkpoint)

Phase 4B is the highest-blast-radius batch in Phase 4 — the 7-rule rewrite must preserve case-1 + case-2 BLOCK outcomes under USD-default tenants. The plan's regression gate is explicit.
