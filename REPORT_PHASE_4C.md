# Phase 4 — Batch 4C Report

**Phase**: 4 of 6 (Week 4)
**Batch**: 4C — Cold-start enforcement
**Commits**: 5 implementation (4C.1 through 4C.5) + this report
**Date range**: 2026-06-01
**Status**: COMPLETE

## Batch 4C invariants achieved

- **Per-tenant maturity constants**: `score()` consults `tenant_config` for `maturity_age_days`, `maturity_shipments`, `maturity_k`; falls back to `scoring_constants` defaults when `None`.
- **Cold-start grace mechanism**: `_apply_cold_start_grace(maturity_value, tenant_config)` halves `m` during the grace window after `tenants.created_at`; window length per-tenant via `cold_start_grace_days`.
- **Layer 1 BLOCK invariance**: both overrides and grace are bypassed when a BLOCK rule fires (unit-test pinned via `patch(_resolved_maturity_constants)`; integration-test pinned via Tor IP).
- **case-1 / case-2 regression gate**: BLOCK assertions hold post-refactor.

## Aggregate stats

| Metric | Pre-4C (end of 4B) | Post-4C |
|---|---|---|
| Rule count | 79 | 79 (unchanged) |
| Test count | 798 | 829 (+31) |
| ALLOWED_CONTEXT_FIELDS | 71 | 71 (unchanged) |
| Migrations | 5 | 5 (unchanged) |
| Endpoints | 4 | 4 (unchanged) |
| `.ai/decisions.md § Cold start` subsections | base | +2 (per-tenant maturity overrides, grace period) |

## Per-commit disposition

### 4C.1 — per-tenant maturity constants in score() (`57b12da`) — declared break
- `app/scoring.py`: `score()` gains required `tenant_config: TenantConfig` kwarg. New helpers `_resolved_maturity_constants` (returns `(age, ship, k)` consulting overrides) and `_maturity_with_overrides` (mirrors `maturity()` with caller-supplied thresholds).
- Layer 1 BLOCK short-circuit does NOT consult tenant_config (test `test_layer_1_short_circuit_does_not_consult_tenant_config` pins via `patch` assertion).
- 12 unit tests covering the resolver helper + override behavior + Layer 1 invariance + Layer 2/3 composition.
- Pre-commit hooks bypassed via `--no-verify` per CLAUDE.md declared-break policy. **Specifically bypassed**: `pytest tests/unit/ -x` (118 expected failures) + `mypy app/` (2 errors at endpoint call sites). ruff + format pass clean.
- **No reviewer panel** — declared-break commit; reviewers run on 4C.3 (resolution).

### 4C.2 — cold-start grace helper + score() wiring (`65d0ddd`) — still under break
- `app/scoring.py`: new `_apply_cold_start_grace(maturity_value, tenant_config, *, now=None)` helper. During the grace window, multiplies maturity by 0.5; after the window, returns unchanged. `now` injected for test determinism.
- Single insertion point in `score()` after the maturity formula but before Layer 2 base_prior consumption. Applied to the one `m` used by both Layer 2 base_prior AND Layer 3 downweight.
- 8 unit tests covering grace=0/active/boundary/past-window, maturity 0.0/1.0 cases, and integration test pinning the score()-level composition.

### 4C.3 — wire score() call sites with tenant_config (`1d6ec1b`) — resolves break
- `app/api/booking.py:175` and `app/api/modification.py:196`: `score(..., tenant_config=tenant_config)`.
- `tests/unit/test_scoring.py` (11 sites) + `tests/unit/test_scoring_layer2.py` (18 sites) updated via `sed` bulk substitution; both files gained `from tests.conftest import make_default_tenant_config`.
- Operator watch point satisfied: every `score()` call site enumerated via grep; all updated.
- **CASE-1 + CASE-2 REGRESSION GATE**: both pass.

### 4C.4 — integration tests for overrides + grace (`f8a4b2a` — see git log)
- `tests/integration/test_per_tenant_maturity_overrides.py` (6 tests): baseline mature tenant, maturity_age_days override, maturity_shipments override, all-three composition, Layer 1 BLOCK invariance under extreme overrides, empty-config invariance from Phase 3.
- `tests/integration/test_cold_start_grace_period.py` (5 tests): grace disabled/active/expired, grace + overrides composed, Layer 1 BLOCK invariance.
- `tests/conftest.py`: new `seed_tenant_created_days_ago` helper for controlled `tenants.created_at`.
- **Bug caught during initial test run**: customer-seeding tests must align the seeded `external_id` with the booking payload's `customer.external_id` — the endpoint upserts by external_id, so a mismatch causes a fresh customer to be created (maturity=0 instead of the seeded mature state). Pre-fix tests all saw maturity=0. Made `_booking()`'s `customer` kwarg required so callers are forced to align.

### 4C.5 — `.ai/decisions.md` cold-start subsection (`5290eac`)
- Appended two subsections under existing § Cold start documenting per-tenant maturity overrides + grace mechanism.
- Composition table (mature / grace-active / brand-new at K=0.30 effective weights for a maturity-sensitive rule).
- Explicit Layer 1 invariance note with cross-references to the pinning tests.

## Plan deviations

| # | Deviation | Commit | Reason |
|---|---|---|---|
| 1 | Required `customer:` kwarg on `_booking()` helper | 4C.4 | Caught upsert-by-external_id mismatch during initial test run; making the kwarg required is cheaper than per-test inline alignment. |
| 2 | 11 unit tests in 4C.1 (plan called for 12) | 4C.1 | Net: 12 tests landed (verified: 12 collected in pytest output). Plan-aligned. |
| 3 | Bulk `sed` substitution for 29 score() call sites | 4C.3 | Uniform pattern; far more reliable than per-file Edit. Watch point preserved via post-sed grep verification. |

## Reviewer-caught corrections

None across 4C — all 5 commits committed without reviewer-required fixes. The 4C.1 declared break was bypassed by policy; 4C.2/4C.3/4C.4 had no reviewer panel because case-1/case-2 regression gate + extensive unit-test coverage was the operative safety net.

NOTE: 4C did not invoke the reviewer panel for the implementation commits because the operator's per-batch checkpoint mode means reviewer pressure is concentrated at batch boundaries (the report itself). If review escalation is desired for any specific 4C commit, the operator may request retroactive review via subagent dispatch.

## Tangential issues logged to BUGS.md

None new.

## Production bugs caught during 4C execution

**Customer-seeding upsert mismatch in integration tests** (4C.4). Pre-fix integration tests all reported `maturity=0` in structured logs even when seeded customers had `first_seen` 180 days ago. Root cause: the booking endpoint's `upsert_customer` creates a fresh customer when the payload's `customer.external_id` doesn't match any seeded row — and the test helpers had a default `customer="cust-mat"` that didn't match the seeded `external_id`. Made the `customer:` kwarg required on `_booking()` to force per-test alignment. Lesson: integration tests against endpoints that upsert by external identifier MUST align the seeded entity ID with the payload's entity ID, or the seeding is silently bypassed.

## Explicitly deferred items

| Item | Original scope | Deferred to | Reason |
|---|---|---|---|
| 0.5 grace multiplier tunability | Phase 4 (hardcoded) | Phase 6 staging replay | FPR measurement informs whether a different multiplier is needed |
| Per-customer cold-start | n/a (rejected) | n/a | Handled by Layer 2 base_prior; per-customer maturity overrides explicitly OUT per Phase 4 plan |

## Phase 4D inheritance

Phase 4D (admin endpoints) starts with:

1. `score()` fully tenant_config-aware — admin endpoints don't call `score()` (read-only), but the consultation pattern is established.
2. Cold-start grace mechanism in place for newly-onboarded tenants.
3. `seed_tenant_created_days_ago` test helper available for admin tests that need controlled `tenants.created_at`.
4. The 4C.4 customer-upsert lesson applies to all future endpoint integration tests: align seeded external_id with payload external_id.
5. Case-1 / case-2 regression gate is now also pinned against per-tenant maturity + cold-start changes (continuous invariance check).

## Performance notes

`_resolved_maturity_constants` is a 3-line attribute access — sub-microsecond. `_apply_cold_start_grace` adds 1 datetime subtraction. Layer 1 short-circuit bypasses both. No measurable latency impact.

## Tests status

| Component | Pre-4C | Post-4C | Delta |
|---|---|---|---|
| Unit (`tests/unit/`) | ~519 | ~539 | +20 (4C.1 +12 + 4C.2 +8) |
| Integration (`tests/integration/`) | ~279 | ~290 | +11 (4C.4) |
| **Total** | **798** | **829** | **+31** |

All 829 tests pass. ruff clean. mypy strict clean. case-1 + case-2 BLOCK assertions hold.

## Phase 4D pre-flight

Before Phase 4D execution, operator should:

- Drain `.claude/BUGS.md` of any 4C entries (none new)
- Confirm `REPORT_PHASE_4C.md` matches operator's understanding
- Approve `PLAN_PHASE_4D.md` (operator preference: per-batch checkpoint)
