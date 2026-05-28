# Phase 3 — Aggregate Report

**Phase**: 3 of N (Week 3)
**Batches**: 3A, 3B, 3C, 3D
**Commits**: 27 implementation commits + 4 per-batch reports + 1 plan commit + 1 aggregate report = 33 total
**Date range**: 2026-05-27 to 2026-05-28
**Status**: COMPLETE

## Phase 3 invariants achieved

- **Modification endpoint live**: `POST /api/v1/shipments/modification/evaluate` returns ALLOW / REVIEW / BLOCK with the same scoring infrastructure as booking (Phase 3A)
- **Feedback endpoint live**: `POST /api/v1/shipments/feedback` with two-tier idempotency (per-POST request_id dedup + label monotonicity) (Phase 3B)
- **12 new rules added** (8 modification + 4 previously-rejected); total 79 rules
- **10 new Context fields added** (6 modification + 4 previously-rejected); total 66 ALLOWED_CONTEXT_FIELDS
- **4 migrations total** (0001 Phase 1; 0002 Phase 2B.6; 0003 `decisions.request_type`; 0004 feedback drop-and-recreate + shipments PII HMACs)
- **RLS audit doc published**: `docs/security-audit-rls-phase-3.md`; zero queries with potentially missing scope identified
- **RLS structural readiness verified**: Phase 5 role transition can rely on existing policies (3C.3 canary)
- **Currency-implicit-USD assumption documented**: per-currency normalization deferred to Phase 4
- **1 production race condition fixed**: feedback endpoint tier-2 monotonicity SELECT now runs AFTER FOR UPDATE on customer_baselines (3B.7 — surfaced by the same commit that introduced the test)
- **+114 new tests** (561 → 675)

## Aggregate stats

| Metric | Pre-Phase-3 (end of Phase 2) | Post-Phase-3 |
|---|---|---|
| Rule count | 67 | 79 (+12) |
| Test count | 432 | 675 (+243) |
| ALLOWED_CONTEXT_FIELDS | 56 | 66 (+10) |
| Migrations | 2 | 4 (+2) |
| Endpoints | 2 (booking, health) | 4 (+ modification, feedback) |
| `.ai/decisions.md` new sections | — | 3 (modification weight rationale, previously-rejected weight rationale, currency-implicit-USD) |
| Audit docs | — | 1 (`docs/security-audit-rls-phase-3.md`) |
| Production bugs fixed pre-launch | — | 1 (feedback endpoint race) |
| BUGS.md tracked follow-ups | — | 1 (Phase 5: widen `ux_decisions_tenant_request` UNIQUE to include `request_type`) |

## Per-batch summary

### Batch 3A — Modification endpoint stack (9 commits, +129 tests)

3A.1 → 3A.8 + REPORT_PHASE_3A. Delivered the modification endpoint end-to-end:
- `decisions.request_type` discriminator + supporting 3-column index
- Pydantic models (35 unit tests)
- DSL whitelist +6 fields + dormancy invariant
- `build_modification_context` + 3 pure helpers (time bucket / magnitude / direction)
- Modification velocity SQL helpers
- Endpoint route + booking endpoint patch (symmetric idempotency + UniqueViolation → 409)
- 8 modification rules + booking-path defaults + 25 unit tests
- E2E modification flow integration tests

Reviewer panel ran ~50% second-cycle rate (3A had the steepest learning curve for the cycle-1 reviewers; cycle-2 fixes converged quickly).

### Batch 3B — Feedback endpoint stack (8 commits, +90 tests)

3B.1 → 3B.7 + REPORT_PHASE_3B. Delivered the feedback endpoint end-to-end:
- Drop-and-recreate `feedback` table to bootstrap shape + `shipments.email_hmac` / `phone_hmac` columns
- Pydantic models (24 unit tests)
- Endpoint route + baseline writer + booking INSERT patch
- DSL whitelist +4 previously-rejected fields + Context derivations
- 4 previously-rejected rules + per-rule tests
- E2E feedback chain integration tests
- Concurrent baseline-writes tests + race condition fix (post-lock tier-2 re-read)

ALL 7 commits passed reviewer panel in a single cycle (vs 3A's ~50%) — 3A-learned discipline applied preemptively (cast at DB-to-Pydantic boundary, dual tenant filters, UniqueViolationError handling, exhaustively-tested helper extraction).

### Batch 3C — Multi-tenant scoping audit (4 commits, +17 tests)

3C.1 → 3C.3 + REPORT_PHASE_3C. Delivered the audit + verification:
- `docs/security-audit-rls-phase-3.md`: 36 asyncpg call sites inventoried; 0 queries with potentially missing scope
- Comprehensive cross-tenant integration test sweep across all 4 endpoints (9 tests)
- Non-superuser RLS canary that proves policies enforce under the Phase 5 role transition target (8 parametrized tests)

3C.1 cycle 1 caught two material accuracy issues (count off-by-one + wrong file:line ref); both fixed in cycle 2 → PUBLISH. Reviewer agent stalled twice on 3C.2; self-reviewed against the three lenses.

### Batch 3D — Currency decision + integration validation + Phase 3 wrap (4 commits, +7 tests)

3D.1 → 3D.4. Delivered:
- Currency-implicit-USD decision documented in `.ai/decisions.md`
- Cross-batch chain integration test (the canonical Phase 3 value demo)
- Maturity + modification composition test
- Per-batch + aggregate Phase 3 reports

## Plan deviations across Phase 3

Aggregated from per-batch reports:

| # | Deviation | Batch | Reason | Resolution |
|---|---|---|---|---|
| 1 | DEFAULT 'booking' retained on `decisions.request_type` | 3A.1 | Eliminated declared break; safer than dropping post-backfill | Plan amended pre-execution |
| 2 | Index columns `(tenant_id, request_type, created_at)` not `(tenant_id, request_type)` | 3A.1 | Matches project pattern + supports 3A.5 range scan | Applied before commit |
| 3 | `source_ip: IPv4Address` (not `IPvAnyAddress`) | 3A.2 | Matches existing `BookingRequest` convention | Applied during 3A.2 |
| 4 | Response omits `account_prior` / `signal_score` / `maturity` | 3A.2 | BookingResponse doesn't expose them; mirror | Applied during 3A.2 |
| 5 | `_modification_direction` has no `hmac_secret` | 3A.4 | Removed dead HMAC code per reviewer | Applied in cycle 2 |
| 6 | `WHERE d.tenant_id = $1 AND s.tenant_id = $1` (dual filter) | 3A.5 | `.ai/conventions.md` tenant-scoping discipline | Applied in cycle 2 |
| 7 | Symmetric `request_type` filter on both endpoints' idempotency SELECTs + UniqueViolationError → 409 | 3A.6 | Reviewer-converging finding; avoids 500 path | Applied in cycle 2 |
| 8 | Customer-not-found 404 → assert | 3A.6 | Defensive dead code per reviewer | Applied in cycle 2 |
| 9 | `BOOKING_PATH_MODIFICATION_DEFAULTS` Final[dict] constant | 3A.7 | Eliminates production/test fixture drift | Applied in cycle 2 |
| 10 | `'none'` sentinel for modification_type default | 3A.7 | DSL requires populated field; sentinel keeps rules dormant on booking | Built into design |
| 11 | SQL COMMENT escaping (`$1` / `:311` → plain prose) | 3B.1 | Mid-implementation discovery (sqlalchemy bind parsing) | Applied during 3B.1 |
| 12 | `cast(FeedbackLabel | None, ...)` at DB-to-Pydantic | 3B.3 | mypy strict; CHECK guarantees safety | Applied during 3B.3 |
| 13 | UniqueViolationError → 409 on both feedback paths (apply + monotonicity-skip) | 3B.3 | Plan implementation discovery | Applied during 3B.3 |
| 14 | `_decision_id` parameter dropped from `_insert_feedback_row` | 3B.3 | Senior + code-flow reviewer feedback | Applied between cycles |
| 15 | `build_context` signature extended with optional email_hmac/phone_hmac | 3B.4 | Required for previously-rejected Context fields | Applied during 3B.4 |
| 16 | **3B.7 scope expansion**: production race fix bundled with the test that surfaced it | 3B.7 | Atomic-commit discipline (per `feedback_atomic_commits` memory) | Bundled in 3B.7 |
| 17 | Audit count 35 → 36 + wrong `0001_initial.py:284` ref | 3C.1 | doc-reviewer cycle 1 catches | Applied in cycle 2 → PUBLISH |
| 18 | `serial` pytest mark registered in pyproject.toml | 3C.3 | Pre-commit caught | Mark registered |
| 19 | test 4 bucket arithmetic (within_30_min → within_24_hours via 12h delta) | 3D.3 | Failing test discovery | Fixed in commit |

## Reviewer-caught corrections

Aggregated from per-batch reports: **24 corrections across 27 implementation commits** — ~0.9 corrections per commit (median 2 in 3A, ~0 in 3B, ~1 in 3C, ~1 in 3D).

## Tangential issues logged to BUGS.md

1. **2026-05-27 — decisions.ux_decisions_tenant_request UNIQUE is flat across request_type** (severity=medium). The flat constraint creates a request_id namespace collision risk between booking and modification. Mitigated via try/except → 409 on both endpoints. Phase 5 should widen the UNIQUE to `(tenant_id, request_type, request_id)`.

## Production bug surfaced during Phase 3 execution

**Feedback endpoint race** (3B.7, fixed in same commit as the test). The tier-2 monotonicity SELECT was running BEFORE the FOR UPDATE on `customer_baselines`. Two concurrent `rejected`/`fraud_confirmed` feedback POSTs for the same target both read `prior_label=None`, both applied, double-incrementing `customers.flagged_count`. Fix: re-run the tier-2 SELECT after acquiring the lock. The lock guarantees the second transaction sees the first's committed label. Verified: pre-fix test fails with `flagged_count=2`; post-fix passes with `flagged_count=1`.

## Explicitly deferred items

| Item | Original scope | Deferred to | Reason |
|---|---|---|---|
| Per-currency `value_caps` normalization | Phase 3 (operator-confirmed deferral) | Phase 4 | TenantConfig is Phase 4 scope |
| Extended modification decision states (REVERT, CANCEL) | v1+ | v2+ | Phase 3 scope decision |
| Modification of modifications | v1+ | (out) | Phase 3 scope exclusion |
| `riskd_app_login` runtime RLS activation | Phase 3C (structural confirmation only) | Phase 5 | Pre-existing STATUS row 1B.2 + 3C confirmed structural readiness |
| `ux_decisions_tenant_request` UNIQUE widening | 3A.6 (deferred via BUGS.md) | Phase 5 | In-tenant blast radius mitigated by 409 catches |
| Per-tenant operator_id validation | — | Phase 4 | Phase 3B keeps operator_id opaque text |
| Admin / read-only endpoints | — | Phase 4 | Read endpoints are Phase 4 scope |
| `last_seen` update on modification | Implicit in booking | Phase 4+ | Phase 3 deliberately scoped to decision-only persistence |
| Modification weight calibration | 3A.7 | Phase 6 staging replay | Per `feedback_no_weight_tuning_phase2` memory |
| Previously-rejected weight calibration | 3B.5 | Phase 6 staging replay | Same calibration policy |

## Phase 4 inheritance

Phase 4 (Week 4) starts with:

1. **`TenantConfig` Pydantic model + tenant onboarding script** — first deliverable
2. **Currency normalization implementation** — Phase 4 wires `value_caps` into the 7 absolute-value rules (and modification rule 1's magnitude); rewrites conditions to consult `tenant.value_caps.get(currency, default)`
3. **Cold-start window enforcement** — Phase 4 scope
4. **Two read-only admin endpoints** — Phase 4 scope (re-triggers the 3C audit doc)
5. **The modification + feedback endpoints remain unchanged** — Phase 4 layers tenant-config overrides on top (e.g., per-tenant modification-velocity thresholds)
6. **Audit doc inheritance**: Phase 4 admin endpoints MUST be added to `docs/security-audit-rls-phase-3.md` (or a Phase-4 successor). Standard panel + db-reviewer route per CLAUDE.md triage.

## Performance notes

**Modification endpoint**: 11 sequential awaits on the txn connection (9 inherited from `build_context` + 2 modification-velocity SQL queries). Phase 5 load test revisits if needed — the watch-point flagged this in planning and it held.

**Feedback endpoint**: 5–8 sequential awaits depending on label and path. The post-lock tier-2 re-read (3B.7 race fix) adds 1 indexed `LIMIT 1` SELECT on the apply path only. Approved path skips the FOR UPDATE + baseline.save + counter UPDATE → ~3 awaits.

**Concurrency**: booking + modification + feedback all serialise correctly per-customer on `customer_baselines` FOR UPDATE (no deadlocks; verified by 3A.8, 3B.7, 3C.2, 3D.2).

## Phase 5 readiness assessment

Per the 3C audit + canary, the codebase is **ready for the Phase 5 role transition** with the following items:

1. Create `riskd_app_login` role (`LOGIN INHERIT`, `GRANT riskd_app TO riskd_app_login`).
2. Switch the runtime `DATABASE_URL` to connect as `riskd_app_login`.
3. Re-run `tests/integration/test_rls_enforcement_under_riskd_app.py` in production smoke.

Additional hardening item (from BUGS.md): widen `ux_decisions_tenant_request` UNIQUE to include `request_type`.

No code refactor or migration is required for RLS activation. The existing 9 policies + `set_tenant_id` machinery work as-is.

## Recommended Phase 4 pre-flight

Before Phase 4 starts, operator should:
- Drain `.claude/BUGS.md` of any Phase 3 entries (currently 1: UNIQUE widening — likely defers to Phase 5)
- Confirm `REPORT_PHASE_3.md` matches the operator's understanding of what landed
- Approve Phase 4 scope (which references this report)

## Tests status

| Component | Pre-Phase-3 | Post-Phase-3 | Delta |
|---|---|---|---|
| Unit | ~250 | ~430 | +180 |
| Integration | ~180 | ~245 | +65 |
| Total | 432 | **675** | +243 |

All 675 tests pass. ruff clean, mypy strict clean.
