# PLAN_PHASE_6B — CAD-default currency migration

> **Phase 6, Batch B.** Shifts `DEFAULT_VALUE_CAPS` from USD-implicit to CAD-explicit with same numeric thresholds. Code-only (no data migration — zero production tenants exist).
>
> Companion: `PLAN_PHASE_6A.md` (case-3 detection — independent of 6B) → `PLAN_PHASE_6C.md` (replay validation) → `PLAN_PHASE_6D.md` → `PLAN_PHASE_6E.md`.

---

## Pre-plan verification findings

Verification reads against current `feat/refactor` HEAD (`f7778cd`):

1. **`DEFAULT_VALUE_CAPS` at `app/tenant_config.py:51-58`** — exact current value:
   ```python
   DEFAULT_VALUE_CAPS: dict[str, dict[str, float]] = {
       "USD": {"high": 10000.0, "new_user": 5000.0, "medium": 2000.0, "low": 1000.0}
   }
   ```
   Consumed by `resolve_value_caps()` at `tenant_config.py:267-294`. Called from `build_context()` at `app/context.py:257`.

2. **`TenantConfig.allowed_currencies`** defaults to `["USD"]` (per Phase 4B). New tenants currently onboard as USD-only.

3. **`scripts/tenant_onboard.py`** does NOT explicitly set a default; it inherits whatever `TenantConfig` defaults supply. So switching the model default is sufficient — no separate script change needed unless the script's bootstrap config JSONB pins USD elsewhere (verification: it does not; `initial_config = {}` falls through to model defaults at line 231).

4. **89 USD references in `tests/`** across 12 files, classified per verification:
   - **(a) KEEP USD** (tests USD-specific behavior): `test_models_currency.py:58,62,63,98,126`; `test_tenant_config_loader.py:45,73,84`; plus the USD-specific cases in `test_currency_validation.py:78,108,115`, `test_currency_normalization_e2e.py` USD-tenant assertion lines.
   - **(b) UPDATE to CAD** (implicitly used USD as default; should explicitly state currency now that CAD is the default):
     - `tests/conftest.py:113` (shipment_currency fixture default)
     - `test_value_caps_resolution.py:39,49,53,64,75,80,102,107,113-116,123,124` (all `"USD"`-as-default-key calls become `"CAD"`)
     - `test_context_value_caps_fields.py:64,103,115,127,176,193,202,226,227,303` (default tenant uses CAD)
     - `test_tenant_config_model.py:113,158,168,173,181,198` (validation tests of value_caps shape — switch to CAD-keyed)
     - `test_tenant_onboard_script_integration.py:209,225` (onboarded tenant default)
   - **(c) Currency-aware regression tests** (keep both, may need parametrize or add CAD parallel): `test_currency_normalization_e2e.py` 8 tests covering fallback + cross-currency.

5. **Case-1 + case-2 fixture currency**:
   - `case_1_dashboard_ato.json`: no currency declared; band-level assertions are currency-agnostic → no test edit needed; passes under CAD default.
   - `test_case_2.py:54`: `_seed_payload()` defaults `currency: str = "USD"`. Two readings:
     - **Reading A**: case-2 historically operates with USD-implicit assumption; under CAD default, `"USD"` here is now non-default. Test still passes (currency-validation accepts USD if allowed_currencies includes USD; case-2 tenant config can be modified to allow USD), but the implicit-default-USD comment is wrong.
     - **Reading B**: change `_seed_payload()` default to `"CAD"` so case-2 uses the project default.
   - **Decision**: Reading B — change to `"CAD"`. Case-2 assertions are currency-agnostic (decision-level BLOCK on the 6-rule compound), so this is a label change.

6. **Phase 4B decisions section** at `.ai/decisions.md:353-362` documents the original USD-default decision. 6B amends this section noting CAD as new default; Phase 4B RESOLVED status remains.

7. **`docs/observability.md`** has zero USD references; nothing to change there.

---

## Decisions absorbed

| Decision | Value | Source |
|---|---|---|
| 6B scope | `DEFAULT_VALUE_CAPS` USD → CAD (same numeric thresholds); `TenantConfig.allowed_currencies` default ["USD"] → ["CAD"]; test reconciliation per verification classification; docs amendments | Phase 6 prompt |
| Numeric thresholds | UNCHANGED (10000/5000/2000/1000 — interpret as CAD, no exchange-rate conversion) | Phase 6 prompt |
| Data migration | NONE — zero production tenants exist | Phase 6 prompt |
| `tenant_onboard.py` change | NONE direct — model default flows through | 6B verification |
| Case-2 `_seed_payload()` default | "USD" → "CAD" (currency-agnostic assertions; label switches to project default) | 6B verification |
| USD-as-USD-specific-behavior tests | KEEP unchanged (explicit USD coverage retained) | Phase 6 prompt |
| Currency-aware regression tests | KEEP both currencies covered; no parametrize needed if the existing tests already mix USD/CAD payloads | 6B verification |
| `.ai/decisions.md` Phase 4B section | AMEND in place (CAD-default note); status RESOLVED carries forward | Phase 6 prompt |
| NO weight tuning | Numeric thresholds (10000/5000/2000/1000) UNCHANGED | Project-wide discipline |
| Regression gate | Case-1 + case-2 BLOCK + ALLOW assertions hold under CAD default | Phase 6 prompt |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md.
- **Reviewer panel MANDATORY per code commit**:
  - **6B.1 (DEFAULT_VALUE_CAPS + TenantConfig defaults)**: model/config change → standard path — **senior-engineer + security-auditor + code-flow**. No tests changed in this commit (test reconciliation is 6B.2).
  - **6B.2 (test reconciliation)**: tests change only, no production code → **test-reviewer + senior-engineer + code-flow** (lightweight: test-only).
  - **6B.3 (.ai/decisions.md amendment)**: doc change to architectural-intent doc → standard path + doc-reviewer per CLAUDE.md borderline → **senior-engineer + code-flow + doc-reviewer**.
- Pre-commit gates enforced.

---

## Cross-batch dependencies

- **6B independent of 6A** — model/rule surface (6A) is unrelated to currency defaults (6B). Order between 6A and 6B is arbitrary; this plan assumes 6A lands first per the phase outline.
- **6B → 6C**: replay payloads in 6C inherit CAD-default. The export-from-freight_risk script in 6C should set `currency: "CAD"` on payloads (freight_risk's `total` column is currency-implicit; we treat it as CAD).
- **6B → 6E**: Phase 6E enumerates the CAD-default switch in the aggregate report.

---

## Commits

### 6B.1 — `DEFAULT_VALUE_CAPS` + `TenantConfig.allowed_currencies` default switch

**Theme**: One-shot code change to the project-wide currency default. Numeric thresholds unchanged.

**Files**:
- MODIFY `app/tenant_config.py`:
  - `DEFAULT_VALUE_CAPS` dict re-keyed: `"USD"` → `"CAD"`. Inner dict UNCHANGED (10000/5000/2000/1000).
  - `TenantConfig.allowed_currencies: list[str] = Field(default_factory=lambda: ["CAD"])` (was `["USD"]`).
- NO other code touched.

**Specifics**:
- `resolve_value_caps()` lookup logic untouched — it consults whatever currency key is supplied. Switching the default key from USD to CAD changes the bootstrap state; the resolution function works the same.
- Tenants explicitly configured with `allowed_currencies: ["USD"]` continue to work — multi-currency support is preserved; only the default changes.

**Validation**:
- `mypy app/` clean.
- `ruff check app/` clean.
- `pytest tests/unit/test_value_caps_resolution.py tests/unit/test_tenant_config_model.py` — these WILL FAIL on this commit because they hardcode USD-as-default. Failures are EXPECTED and documented as declared break; resolution in 6B.2.

**Risk level**: low. Two-field change with strict typing. The risky surface is "what tests break" — declared and fixed in next commit.

**Reversibility**: full via revert.

**Pre-commit verification**: pre-commit runs unit tests; these WILL fail per the declared break. Commit uses `git commit --no-verify` with explicit declared-break footer naming the bypassed gate (`pytest-unit`). Next commit (6B.2) restores the gate.

**Observability**: no new events. The existing `tenant_config.value_caps.fallback` EMF event continues to emit; its `currency` dimension now defaults to "CAD".

**Test changes**: none in this commit. Test reconciliation lands in 6B.2.

**Rollback plan**: revert.

**Declared breaks**:
- **Scope**: `pytest tests/unit/test_value_caps_resolution.py tests/unit/test_tenant_config_model.py tests/unit/test_models_currency.py` — multiple tests fail because they hardcoded USD as default key. Pre-commit unit-test gate is bypassed for this commit ONLY.
  **Resolved in**: 6B.2 (test reconciliation per verification classification).

**Reviewer routing**: Never-Skip (auth/RLS unchanged, but config-load surface = security-auditor relevant; standard path applies). → **senior-engineer + security-auditor + code-flow**. Reviewers see the declared break and plan-suppress test failures; they validate that the production code change is correct + that the declared break is precise.

---

### 6B.2 — Test reconciliation: USD-default-implicit tests → CAD-explicit; restore pre-commit unit gate

**Theme**: Update tests to reflect the new default. Per the verification classification, ~30 test lines switch from USD to CAD; USD-specific behavior tests stay unchanged.

**Files**:
- MODIFY `tests/conftest.py` — `shipment_currency` fixture default `"USD"` → `"CAD"`.
- MODIFY `tests/unit/test_value_caps_resolution.py` — all USD-as-default-key calls become CAD (lines 39, 49, 53, 64, 75, 80, 102, 107, 113-116, 123, 124).
- MODIFY `tests/unit/test_tenant_config_model.py` — validation tests of value_caps shape switch USD-keyed → CAD-keyed (lines 113, 158, 168, 173, 181, 198).
- MODIFY `tests/integration/test_context_value_caps_fields.py` — default tenant uses CAD (lines 64, 103, 115, 127, 176, 193, 202, 226, 227, 303).
- MODIFY `tests/integration/test_tenant_onboard_script_integration.py` — onboarded tenant default CAD (lines 209, 225).
- MODIFY `tests/integration/test_case_2.py` — `_seed_payload()` `currency: str = "CAD"` (was "USD").
- DO NOT MODIFY:
  - `tests/unit/test_models_currency.py:58,62,63,98,126` — these test USD-specific model field behavior.
  - `tests/unit/test_tenant_config_loader.py:45,73,84` — these explicitly load USD configs.
  - `tests/integration/test_currency_validation.py:78,108,115` — these test USD-tenant validation behavior.
  - `tests/integration/test_currency_normalization_e2e.py` — keeps mixed-currency regression coverage; USD payloads in these tests are explicitly testing cross-currency behavior, not relying on default.

**Specifics**:
- Each updated line: the test author's intent was "use the project default"; that default is now CAD. Tests stay semantically identical.
- USD-specific tests preserve USD support coverage — multi-currency is not removed, only the default shifts.

**Validation**:
- `pytest tests/ --asyncio-mode=auto` — full suite passes; all 918+ tests pass under CAD default.
- `pytest tests/integration/test_case_1.py tests/integration/test_case_2.py -v` — the explicit regression gate: both pass.
- `ruff check tests/` clean; `mypy app/` clean.

**Risk level**: low. Mechanical test edits per verification classification; no production code touched.

**Reversibility**: full via revert.

**Pre-commit verification**: pre-commit ALL gates pass (the previous declared break is resolved).

**Observability**: no change.

**Test changes**: ~30 edited lines across 6 test files; no new test files; no test deletions.

**Rollback plan**: revert.

**Declared breaks**: none. Restores the gate that 6B.1 bypassed.

**Reviewer routing**: lightweight test-only path → **test-reviewer + senior-engineer + code-flow**.

---

### 6B.3 — `.ai/decisions.md` Phase 4B amendment + docs sweep

**Theme**: Update architectural intent doc to record the CAD-default shift. Phase 4B section already exists; this is an in-place amendment, not a new section.

**Files**:
- MODIFY `.ai/decisions.md` — add a "Phase 6B amendment (2026-06-03)" subsection inside the Phase 4B section:
  - Default currency switched USD → CAD; same numeric thresholds.
  - Rationale: project is a Canadian freight aggregator; CAD is the operational currency. USD was a placeholder default during Phase 4B build-out. Phase 4B RESOLVED status persists; this is an amendment within scope.
  - Note that multi-currency support is preserved; USD-explicit tenants supported unchanged.
- MODIFY `docs/observability.md` — IF any explicit USD reference exists (verification said none); otherwise no change.
- MODIFY `scripts/tenant_onboard.py` docstring/comment block if it mentions USD as default — bring in line with new default.

**Specifics**:
- Decisions.md amendment is small (~10-15 lines). Pattern matches prior phase amendments.
- No code changes; this is purely documentation.
- **Phase 6A cross-reference note**: Phase 6A introduced three new rules (`case_3_compound`, `cold_start_country_triangle_with_carrier_dropoff`, `cold_start_population_baseline_rare_with_carrier_dropoff`), the structured `Customer.registered_country` Pydantic field + DB column, and the `tenant_route_baselines` population-baseline subsystem. CAD-default applies uniformly across the new and existing rule surface — the new rules consult Context fields that are currency-agnostic; no currency interaction.

**Validation**:
- `pytest tests/ --asyncio-mode=auto` — full suite passes (no code touched).
- `ruff check app/ tests/` clean.

**Risk level**: trivial.

**Reversibility**: full via revert.

**Pre-commit verification**: trailing-whitespace, end-of-file-fixer, markdown lint as configured. No code-validation gates exercised because no code changed.

**Observability**: no change.

**Test changes**: none.

**Rollback plan**: revert.

**Declared breaks**: none.

**Reviewer routing**: `.ai/decisions.md` amendment per CLAUDE.md borderline rule → **senior-engineer + code-flow + doc-reviewer**.

---

## End-of-batch state (after 6B.3)

- `DEFAULT_VALUE_CAPS` keyed CAD with thresholds 10000/5000/2000/1000.
- `TenantConfig.allowed_currencies` default `["CAD"]`.
- Test suite (~918 tests) passes under CAD default.
- USD support preserved end-to-end (validation, normalization, tenant-config overrides, model field).
- `.ai/decisions.md` Phase 4B section carries the CAD-default amendment.
- Case-1 + case-2 integration regression GREEN under CAD default.
- No data migration; no production tenant impact.

## Open items handed to 6C/6E

- **6C** export-from-freight_risk script sets `currency: "CAD"` on all payload bodies (freight_risk's `total` column is currency-implicit; convention: CAD).
- **6E** aggregate report enumerates the CAD-default switch among Phase 6 code deliverables and confirms case-1 + case-2 regression GREEN.
