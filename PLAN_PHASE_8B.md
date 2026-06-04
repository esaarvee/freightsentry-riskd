# PLAN_PHASE_8B — Test suite audit

Phase 8 batch 8B. Collapses phase-named test functions and milestone count assertions into current-state equivalents. Coverage-non-regression is the halt gate. Sweep for shared-fixture-prop-up patterns; refactors deferred if non-trivial.

## Decisions absorbed

| Decision | Resolution | Source |
|---|---|---|
| Audit surface | Content-based (function names + `assert len` patterns in test bodies), not filename-based. The prompt's `test_phase_*.py` filename pattern doesn't apply — well-named files; phase markers live in function names. | Operator AskUserQuestion + V-4 grep |
| Phase-named functions found (V-4 follow-up) | 13 functions across 7 files: `test_rules_whitelist.py` (6), `test_rules_modification.py` (1), `test_rules_modification_whitelist.py` (2), `test_rules_previously_rejected.py` (1), `test_value_caps_resolution.py` (1), `test_per_tenant_maturity_overrides.py` (1), `test_context.py` (1). | grep follow-up |
| Milestone count assertions found | `ALLOWED_CONTEXT_FIELDS == 77` (2 sites), `ruleset.rules == 81` (2 sites), `_PHASE_2B_ADDITIONS == 11`, `_PHASE_3A_MODIFICATION_FIELDS == 6`, `modification_rules == 8`, `FLAG_WEIGHTS == 4`. | grep follow-up |
| Underscore-prefixed phase-frozen constants | Keep (`_PHASE_2B_ADDITIONS`, `_PHASE_3A_MODIFICATION_FIELDS`). These are subset-membership probes — they assert "these specific fields exist in the current whitelist", which is more specific than `len() == N` and serves as anti-regression. Removing them is a refactor, not redundancy elimination. | Discipline: "default to keep when uncertain" |
| Coverage non-regression gate | Required. pytest-cov installed in 8A.0; 8B.0 captures baseline. Line coverage ≥ pre-audit. | Phase 8 prompt §Quality 4 |
| Coverage measurement scope | `pytest --cov=app tests/` (production code only; not test code). Branch coverage as a secondary check. | Phase 8 prompt |
| Test count delta tolerance | Net reduction expected: ~10-15 functions collapsed. Final count is what coverage validates; the count itself is informational. | Phase 8 prompt |
| Shared-fixture-prop-up surface | Survey-only in 8B. Refactor deferred if non-trivial (each individual refactor risks coverage churn). Any findings logged to `.claude/BUGS.md` for post-launch triage. | Phase 8 prompt §S-2 + §8B.6 |
| Migration revision-ID test sweep | Already handled by 8A.2; no overlap in 8B. | 8A handoff |
| Atomic commit cadence | Each collapse cluster (one test file's phase-named functions) is one commit, since multiple commits per file would create transient broken-import states (the `_PHASE_*_ADDITIONS` constants are imported by multiple tests). | MEMORY.md feedback_atomic_commits |

## Pre-batch verification

Completed during the 8A→8B handoff. Findings recorded in the Decisions absorbed table above. Specifically:

- 1118 tests in 96 files (V-4 baseline, pre-8A).
- Coverage baseline TBD — captured in 8B.0 after pytest-cov install in 8A.0.
- 13 phase-named test functions identified.
- 6 distinct milestone-count assertion patterns identified.

## Commits

### 8B.0 — Coverage baseline capture

**Changes**:
- Activate venv from 8A.0.
- Run `pytest --cov=app --cov-report=term-missing --cov-report=json:/tmp/coverage_pre.json tests/`.
- Capture line coverage percentage to `tests/coverage_baseline.txt` (committed as the regression-gate anchor).
- Brief docstring/comment block in the file documents the baseline and links to PLAN_PHASE_8B.md.
- Add a `tests/integration/test_coverage_baseline.py` if appropriate (operational note only — coverage drift typically caught in CI, not unit tests; this is a one-time anchor, not a recurring gate).

**Tests**: 0 new.

**Validation**:
- `pytest tests/` returns 0 failures (broad sanity).
- `tests/coverage_baseline.txt` file exists with a real number.

**Declared breaks**: none.

**Reviewer panel**: senior-engineer + test-reviewer.

### 8B.1 — Collapse phase-named functions in `test_rules_whitelist.py`

**Changes**:
- File contains 6 phase-named functions: `test_whitelist_size_matches_phase_7c_total`, `test_whitelist_contains_phase_6a_2_additions`, `test_whitelist_contains_phase_6a_5_and_7c_case3b_fields`, `test_whitelist_contains_phase_6a_8_addition`, `test_whitelist_contains_every_phase_2b_addition`, `test_whitelist_phase_2b_additions_count_is_eleven`.
- Collapse strategy:
  - Single `test_whitelist_size_matches_current` (replaces `_phase_7c_total` — asserts `len(ALLOWED_CONTEXT_FIELDS) == 77` against the current count).
  - Single `test_whitelist_contains_phase_2b_additions` (renamed to keep the subset-membership probe but drop the phase-anchoring framing in the body docstring).
  - Single `test_whitelist_contains_phase_6_and_7_additions` (merges the 3 separate Phase 6/7 subset probes into one — they all do `for field in subset: assert field in ALLOWED_CONTEXT_FIELDS`).
  - Net: 6 functions → 3 functions.
- Underscore-prefixed `_PHASE_2B_ADDITIONS` constant: keep (subset-probe data).
- Existing assertions about specific field presence (e.g., `assert "customer_country_triangle_mismatch" not in ALLOWED_CONTEXT_FIELDS`) preserved.

**Tests**: 3 functions remain in this file (down from 6 + non-phase tests).

**Validation**:
- `pytest tests/unit/test_rules_whitelist.py -v` passes.
- `pytest tests/unit/ -x` passes (no cross-file regression).
- Coverage on `app/rules.py` does not decrease (spot-check via `pytest --cov=app.rules tests/unit/test_rules_whitelist.py`).

**Declared breaks**: none.

**Reviewer panel**: senior-engineer + test-reviewer.

### 8B.2 — Collapse phase-named functions in `test_rules_modification.py` + `test_rules_modification_whitelist.py`

**Changes**:
- `test_rules_modification.py:test_phase_3a_modification_rule_count` → rename to `test_modification_rule_count_current` (asserts `len(modification_rules) == 8`).
- `test_rules_modification_whitelist.py:test_phase_3a_additions_count_is_six` → rename to `test_modification_additions_current_count`.
- `test_rules_modification_whitelist.py:test_whitelist_contains_every_phase_3a_modification_field` → rename to `test_whitelist_contains_modification_fields`.
- Underscore-prefixed `_PHASE_3A_MODIFICATION_FIELDS` constant: keep (subset-probe data).
- Net: 3 functions renamed (counts preserved).

**Tests**: same count, descriptive renames.

**Validation**:
- `pytest tests/unit/test_rules_modification.py tests/unit/test_rules_modification_whitelist.py -v` passes.
- Coverage on `app/rules.py` (modification rule paths) unchanged.

**Declared breaks**: none.

**Reviewer panel**: senior-engineer + test-reviewer.

### 8B.3 — Collapse remaining phase-named functions

**Changes**:
- `test_rules_previously_rejected.py:test_phase_3b_previously_rejected_rule_count` → `test_previously_rejected_rule_count_current`.
- `test_value_caps_resolution.py:test_default_value_caps_match_phase_2_thresholds` → `test_default_value_caps_current_thresholds`.
- `test_per_tenant_maturity_overrides.py:test_empty_config_tenant_unchanged_from_phase3` → `test_empty_config_tenant_uses_defaults`.
- `test_context.py:test_build_context_returns_all_phase2_fields` → `test_build_context_returns_all_expected_fields` (with docstring noting "expected = currently-defined ALLOWED_CONTEXT_FIELDS").
- Net: 4 functions renamed (counts preserved).

**Tests**: same count, descriptive renames across 4 files.

**Validation**:
- Affected tests pass.
- `pytest tests/` returns 0 failures.

**Declared breaks**: none.

**Reviewer panel**: senior-engineer + test-reviewer.

### 8B.4 — Shared-fixture-prop-up survey

**Changes**:
- Install `pytest-randomly` in the venv from 8A.0: `pip install pytest-randomly`.
- Run `pytest tests/unit/ --randomly-seed=12345` and `--randomly-seed=54321` — record any failures that don't occur in deterministic order.
- For each fixture-order-dependent failure: document the dependency in PLAN_PHASE_8B.md AND append to `.claude/BUGS.md` for post-launch triage.
- DO NOT refactor any individual fixture-order dependency in this batch (Phase 8 prompt §8B.6: defer if non-trivial). Refactor only if a single one-liner fix exists (e.g., an obviously-missing `@pytest.fixture(autouse=True)` reset).
- Remove pytest-randomly from venv install if it's persistently destabilizing other tests; log to BUGS.md instead.

**Tests**: 0 new; existing tests may be reordered.

**Validation**:
- `pytest tests/unit/` (default order) passes.
- Findings (if any) committed to `.claude/BUGS.md`.

**Declared breaks**: none. Survey is read-only.

**Reviewer panel**: senior-engineer + test-reviewer.

### 8B.5 — Coverage non-regression verification

**Changes**:
- Re-run `pytest --cov=app --cov-report=term-missing --cov-report=json:/tmp/coverage_post.json tests/`.
- Diff `/tmp/coverage_post.json` against `tests/coverage_baseline.txt` (or the JSON from 8B.0).
- Assert: line coverage post ≥ pre. Branch coverage post ≥ pre.
- Update `tests/coverage_baseline.txt` to the post-audit number (forward-looking anchor for post-Phase-8 work).
- If regression detected, halt and identify the lost test; restore or rewrite before continuing.

**Tests**: 0 new.

**Validation**:
- Coverage delta ≥ 0%.
- `pytest tests/` returns 0 failures.

**Declared breaks**: none.

**Reviewer panel**: senior-engineer + test-reviewer.

### 8B.6 — Batch close

**Changes**:
- PLAN_PHASE_8B.md final state with execution record: test count delta, coverage delta, shared-fixture findings.
- Note any test changes for 8C.5 (if any test docs reference deleted PLAN_PHASE_* files).

**Tests**: 0 new (doc commit).

**Validation**: doc-reviewer panel.

**Declared breaks**: none.

**Reviewer panel**: doc-reviewer.

## Acceptance criteria for 8B close

1. All 13 phase-named test functions renamed to current-state names (no `phase_\d` in function names anywhere in `tests/`).
2. Coverage delta ≥ 0% (line + branch).
3. `pytest tests/` returns 0 failures.
4. `tests/coverage_baseline.txt` reflects post-audit coverage.
5. Shared-fixture-prop-up findings (if any) logged to `.claude/BUGS.md`.
6. PLAN_PHASE_8B.md final state with execution record appended.

## Notes for downstream batches

- **8C**: PLAN_PHASE_8B.md does NOT need to be deleted at 8C.13 — it joins history.md absorption like PLAN_PHASE_8A.md / 8C.md / 8D.md. The whole Phase 8 plan family gets absorbed at production-launch time (or kept as the canonical record per the prompt's operator-preference note in 8D.1).
- **8C.4**: After 8B closes, `system-status.md` should note that the coverage baseline and the schema golden test now exist as anti-drift gates.
