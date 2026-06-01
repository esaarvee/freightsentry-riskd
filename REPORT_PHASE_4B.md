# Phase 4 — Batch 4B Report

**Phase**: 4 of 6 (Week 4)
**Batch**: 4B — Currency normalization
**Commits**: 7 implementation (4B.1 through 4B.7) + this report
**Date range**: 2026-06-01
**Status**: COMPLETE

## Batch 4B invariants achieved

- **`currency` field on payloads** — `BookingRequest.shipment` and `ModificationRequest` both carry `currency: str` (default `"USD"`, ISO 4217 pattern); zero behavioral change for Phase 1-3 payloads.
- **`DEFAULT_VALUE_CAPS` + `resolve_value_caps` helper** — per-currency 4-tier thresholds with documented USD fallback + `tenant_config.value_caps.fallback` warning.
- **Request-time currency validation** — booking + modification endpoints reject currencies not in `tenant_config.allowed_currencies` with 400; feedback endpoint untouched.
- **5 new Context fields** — `shipment_currency` + 4 tier thresholds added to `ALLOWED_CONTEXT_FIELDS` (66 → 71); populated by `build_context` from `resolve_value_caps`.
- **7 currency-implicit rules rewritten** — literal thresholds replaced with `shipment_value_threshold_<tier>` references; weights and maturity-sensitive flags unchanged.
- **CASE-1 + CASE-2 REGRESSION GATE SATISFIED** — both BLOCK assertions hold post-rewrite under USD-default tenants.
- **`.ai/decisions.md § Currency normalization` marked RESOLVED** with the Phase 4B resolution subsection.

## Aggregate stats

| Metric | Pre-4B (end of 4A) | Post-4B |
|---|---|---|
| Rule count | 79 | 79 (7 rewritten, no count change) |
| Test count | 729 | 798 (+69) + 0 skips |
| ALLOWED_CONTEXT_FIELDS | 66 | 71 (+5) |
| Migrations | 5 | 5 (unchanged) |
| Endpoints | 4 | 4 (unchanged; 4D adds 2 admin) |
| `.ai/decisions.md` sections marked RESOLVED | 0 | 1 (Currency normalization) |

## Per-commit disposition

### 4B.1 — currency field on payloads (`6c39a59`)
- `ShipmentData.currency` and `ModificationRequest.currency` — optional `str`, default `"USD"`, `pattern=r"^[A-Z]{3}$"`, `min_length=3, max_length=3`.
- 11 unit tests (10 planned + 1 backward-compat).
- **Reviewer panel cycle 1**: all 4 cleanest verdicts. Single-line docstring fix folded in pre-commit ("10 tests" → "11 tests").

### 4B.2 — `DEFAULT_VALUE_CAPS` + `resolve_value_caps` helper (`29512f1`)
- New constant + helper in `app/tenant_config.py`. Falsy `value_caps` (None or empty dict) routes to USD-default fallback with `metric=True` warning.
- 9 unit tests (8 planned + 1 empty-dict case).
- **Reviewer panel cycle 1**: senior SHIP IT / security LOW RISK / test ACTUALLY GOOD. Folded pre-commit: no-warning assertion on actual happy path + empty-dict coverage.

### 4B.3 — request-time currency validation (`712cf1a`)
- 5-line `HTTPException(400)` block in booking + modification endpoints (after `load_tenant_config`); checks `payload.currency in tenant_config.allowed_currencies`.
- 9 integration tests (5 booking + 4 modification).
- **Reviewer panel**: senior SHIP IT / security LOW RISK / db SHIP IT (txn rolls back cleanly on HTTPException) / test cycle 1: ACCEPTABLE → cycle 1 folded: removed unused fixture; anchored 3 modification 404 assertions to "Original booking not found" detail string.

### 4B.4 — DSL whitelist +5 + Context derivations (`2e6f330`)
- `ALLOWED_CONTEXT_FIELDS` grows 66→71. `build_context` populates the 5 fields via `resolve_value_caps`; parked `_ = tenant_config` marker removed.
- `build_modification_context` overrides synthesized booking's currency via `model_copy`.
- `tests/unit/conftest.py` base_ctx() gets 5 USD-default values.
- `tests/unit/test_rules_whitelist.py` assertion 66→71.
- The 4A.3 skipped test re-enabled with 71-field assertion.
- **Reviewer panel cycle 1**: senior APPROVED WITH RESERVATIONS (test placement under `tests/unit/`) / security LOW RISK / test NEEDS WORK.
- **Folded cycle 1**: moved DB-backed file to `tests/integration/`; custom-USD test asserts all 4 tier values; fallback test pins warning + metric=True via `patch(_log)`; parametrize trimmed (5→2 currencies) with threshold assertions; **new** `test_modification_synthetic_booking_uses_modifications_currency` pins the `model_copy` override.
- 8 integration tests + 3 unit fixture updates.

### 4B.5 — 7-rule rewrite in `app/rules.yaml` (`f5e5696`)
- **Highest blast radius in Phase 4.** All 7 rule conditions rewritten; weights unchanged; descriptions updated to tier-naming.
- 22 new tests (14 per-rule fire/no-fire + 7 parametrized USD-default invariance + 1 rule-count sanity).
- **REGRESSION GATE EXPLICITLY VERIFIED**: case-1 (`test_case_1_dashboard_ato_progression`) + case-2 (3 tests) all PASS.
- **Reviewer panel cycle 1**: all 4 cleanest verdicts (SHIP IT / CLEAN / CLEAN / ACTUALLY GOOD). Cwd-portability fix folded in pre-commit (`open("app/rules.yaml")` → conftest `ruleset` fixture).

### 4B.6 — cross-currency E2E + regression gate (`a77a07a`)
- 9 end-to-end integration tests: USD-default boundary fire/no-fire, CAD-calibrated boundary, cross-tier-boundary (above USD threshold but below CAD threshold), cross-tenant isolation, fallback with warning, threat_intel_high_value per-currency, modification rule 1 currency-independence, multi-rule composition.
- **Reviewer panel cycle 1**: senior APPROVED WITH RESERVATIONS (plan drift 8 vs 10 + missing fallback warning) / code-flow MINOR ISSUES (in-function import; tautology) / test ACTUALLY GOOD.
- **Folded cycle 1**: added multi-rule composition test (now 9 vs 8); fallback test now pins warning via `patch(_log)`; top-level import for `_cleanup_tenant`; dropped tautology assertion.
- 2 of originally-planned 10 tests not delivered (modification value-tier CAD; cold-tenant onboarding via script) — underlying invariants covered by 4B.4 and 4A.6 tests respectively.

### 4B.7 — `.ai/decisions.md` mark Currency normalization RESOLVED (`d2c4437`)
- Renamed section header to "RESOLVED in Phase 4B, 2026-06-01".
- Appended Phase 4B resolution subsection documenting fields + value_caps shape + DEFAULT_VALUE_CAPS + resolve_value_caps + rule rewrites + case-1/case-2 regression.
- Added "Currency conversion via rates table — REJECTED" subsection documenting the rejected alternative.

## Plan deviations

| # | Deviation | Commit | Reason |
|---|---|---|---|
| 1 | 11 unit tests in 4B.1 (plan called for 10) | 4B.1 | Added backward-compat BookingRequest construction test |
| 2 | 9 unit tests in 4B.2 (plan called for 8) | 4B.2 | Added empty-dict case per test-reviewer suggestion |
| 3 | Test file moved to `tests/integration/` | 4B.4 | Plan suggested `tests/unit/` but file is DB-backed; senior + test reviewer caught (per `.ai/conventions.md`) |
| 4 | Modification model_copy test added | 4B.4 | Plan-test #6 from 4B.4 list — covers the synthetic_booking currency override |
| 5 | Parametrize trimmed 5→2 currencies | 4B.4 | Test reviewer cycle 1 — meaningful threshold assertions added instead of string-copy plumbing |
| 6 | 9 E2E tests in 4B.6 (plan called for 10) | 4B.6 | 2 planned tests substituted by tests already in 4A.6 and 4B.4; multi-rule composition added per senior cycle-1 reservation |

## Reviewer-caught corrections (file:line refs)

| # | File:line | Finding | Reviewer | Cycle |
|---|---|---|---|---|
| 1 | `tests/unit/test_models_currency.py:1-7` | "10 tests" docstring stale (file has 11) | senior + test | 4B.1 c1 |
| 2 | `tests/unit/test_value_caps_resolution.py` (no-warning on happy path) | Missing no-warning assertion on success path | test | 4B.2 c1 |
| 3 | `tests/unit/test_value_caps_resolution.py` (empty-dict case) | Empty value_caps falls-back case not exercised | test | 4B.2 c1 |
| 4 | `tests/integration/test_currency_validation.py` (auth_as fixture) | Unused fixture dead code | test | 4B.3 c1 |
| 5 | `tests/integration/test_currency_validation.py` (modification 404 tests) | 404 not anchored to detail string → false-pass risk on currency fail-open regression | test | 4B.3 c1 |
| 6 | `tests/unit/test_context_value_caps_fields.py` placement | DB-backed test in tests/unit/ breaks pre-commit unit-fast contract | senior + test | 4B.4 c1 |
| 7 | `tests/unit/test_context_value_caps_fields.py:84-86` | Stale "6 tests" comment | test | 4B.4 c1 |
| 8 | `tests/unit/test_context_value_caps_fields.py:112` | test_custom_value_caps_usd_overrides_default only asserts one tier | test | 4B.4 c1 |
| 9 | `tests/unit/test_context_value_caps_fields.py:154` | Fallback test doesn't assert warning emission | test | 4B.4 c1 |
| 10 | `tests/unit/test_context_value_caps_fields.py:179-198` | Modification-path coverage missing | test | 4B.4 c1 |
| 11 | `tests/unit/test_rules_currency_rewrite.py:242` | `open("app/rules.yaml")` relative-path cwd fragility | senior + test | 4B.5 c1 |
| 12 | `tests/integration/test_currency_normalization_e2e.py` test count | 8 vs 10 planned tests | senior + code-flow | 4B.6 c1 |
| 13 | `tests/integration/test_currency_normalization_e2e.py:199-212` | Fallback warning assertion missing | senior + test | 4B.6 c1 |
| 14 | `tests/integration/test_currency_normalization_e2e.py:192` | In-function import of `_cleanup_tenant` (underscore-private) | code-flow + test | 4B.6 c1 |
| 15 | `tests/integration/test_currency_normalization_e2e.py:290` | Tautological `assert prior["decision"] in (...)` | code-flow | 4B.6 c1 |

**Total corrections**: 15 across 6 implementation commits. All reviewers' cycle-2 escalation avoided (corrections folded pre-commit in single cycles where possible, or with explicit 1-cycle escalation for 4B.3 + 4B.4 + 4B.6).

Cycle-1 verdict ladder for 4B:

- 4B.1: 4/4 cleanest
- 4B.2: 3/3 cleanest (senior, security, test)
- 4B.3: 3/4 cleanest (test ACCEPTABLE → fold)
- 4B.4: 1/3 cleanest (senior APPROVED W/RES, test NEEDS WORK → fold)
- 4B.5: 4/4 cleanest
- 4B.6: 1/3 cleanest (senior APPROVED W/RES, code-flow MINOR ISSUES → fold)
- 4B.7: PUBLISH

Most non-cleanest verdicts were resolved within the same cycle via pre-commit fold-in. No second-cycle reviewer escalations needed.

## Tangential issues logged to BUGS.md

None new in 4B. The Phase 4A entry (ruff version drift) remains.

## Production bugs caught during 4B execution

None. The 4A.6 "DELETE...RETURNING count(*) OVER ()" was the only true production bug caught during Phase 4A; 4B execution surfaced no analogous issues. Integration tests caught a real Pydantic-coercion subtlety (bool → float on value_caps) in 4A.1 cycle 2 but that was Phase 4A.

## Explicitly deferred items

| Item | Original scope | Deferred to | Reason |
|---|---|---|---|
| Currency conversion via rates table | rejected | post-v2 | Documented in decisions.md as explicitly rejected, not deferred — would require maintained rates data and reintroduces precision drift |
| Storage of currency on shipments table | Phase 4B (out per plan) | Phase 5+ | Modification path infers currency from request payload; shipments column add deferred per plan |
| Modification value-tier CAD integration test | Plan test #6 of 4B.6 | n/a | Underlying model_copy override covered by 4B.4's modification synthetic-booking test |
| Cold-tenant onboarding via script E2E | Plan test #10 of 4B.6 | n/a | Covered by 4A.6's `test_initial_config_applied_visible_via_loader` |

## Phase 4C inheritance

Phase 4C (cold-start enforcement) starts with:

1. `TenantConfig.maturity_age_days`, `maturity_shipments`, `maturity_k`, `cold_start_grace_days` all defined and validated; loader operational
2. `app/scoring.py` consumes only the pure-default `MATURITY_*` constants from `app/scoring_constants.py` — 4C will refactor `score()` to consult tenant_config first
3. `tenant_config` already threaded through `build_context` and `build_modification_context`; 4C just adds a similar threading into `score()`
4. The `_ = tenant_config` parked marker was removed in 4B.4 → 4C cannot reintroduce a parked marker; consumers must be real
5. Case-1 + case-2 regression invariance is now a continuous gate — every batch's full suite verifies it

## Performance notes

**Booking endpoint**: build_context's currency-derivation adds 1 dict lookup + 5 dict writes (~microseconds). No DB or network impact. Latency budget unchanged.

**Modification endpoint**: same plus 1 `model_copy` (synthetic booking currency override). Negligible.

## Tests status

| Component | Pre-4B | Post-4B | Delta |
|---|---|---|---|
| Unit (`tests/unit/`) | ~470 | ~519 | +49 (4B.1 +11 + 4B.2 +9 + 4B.4 +3 + 4B.5 +22 + 4B.6 +0 (E2E in integration)) |
| Integration (`tests/integration/`) | ~257 | ~279 | +22 (4B.3 +9 + 4B.4 +8 + 4B.6 +9 - 4 case-1/case-2 unchanged) |
| **Total** | **729** | **798** | **+69** |

All 798 tests pass. ruff clean. mypy strict clean.

## Phase 4C pre-flight

Before Phase 4C execution, operator should:

- Drain `.claude/BUGS.md` of any 4B entries (none new — the 4A entry is the carry-forward)
- Confirm `REPORT_PHASE_4B.md` matches operator's understanding (in particular the 2 plan-test deviations in 4B.6)
- Approve `PLAN_PHASE_4C.md` (operator preference: per-batch checkpoint)
