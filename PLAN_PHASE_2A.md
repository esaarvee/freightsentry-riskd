# Phase 2 — Batch 2A Plan — Layer 2 scoring infrastructure

Batch 2A wires Layer 2 (account prior + trust contribution + flag prior) into `app/scoring.py` between the existing Layer 1 (hard-block short-circuit) and Layer 3 (signal noisy-OR), and adds maturity downweighting on Layer 3 rules with `maturity_sensitive: true`. Phase 1 preserved `maturity_sensitive` as a YAML field but did not apply it; this batch wires the math.

Target: 4 commits.

---

## Decisions absorbed (batch-relevant subset)

| Decision | Value | Source |
|---|---|---|
| Layer 2 enabled | Yes — `final = noisyOR(account_prior, signal_score)` | Phase 2 bootstrap |
| Account-prior constants | `MaxNewAccount=0.10`, `TrustFactor=0.25`, `flag_weights=[0.00, 0.15, 0.25, 0.35]`, `maturity_age_days=180`, `maturity_shipments=50`, `MaturityK=0.30` | Design Context (locked) |
| `flag_weights` interpretation | **4-tier direct lookup** by `flagged_count_tier` (0→0.00, 1-2→0.15, 3-5→0.25, 6+→0.35). NOT noisy-OR over independent tier activations (which is FreightSentry's `scorer.go:476-488` choice). | Design Context (locked) — verification §3.3 |
| Maturity formula | `maturity = clamp(age_days / maturity_age_days, 0, 1) * clamp(shipments / maturity_shipments, 0, 1)` — multiplicative product of clamped linear fractions | Design Context (locked) — diverges from scorer.go's `min(age_frac, ship_frac)` with `log1p`-scaled shipments |
| Maturity downweight target | Per-rule, only when `rule.maturity_sensitive == true`: `effective_weight = weight * (1 - MaturityK * (1 - maturity))` | Bootstrap "Scoring infrastructure" |
| Trust contribution | `trust_risk = max(0, (0.5 - trust_score) / 0.5)`; `trust_contribution = trust_risk * TrustFactor` | Design Context |
| Customer inheritance | NOT implemented — single-customer maturity only (FreightSentry's customer-aggregate inheritance is out of scope) | Design Context simplification |
| Tenant overridability | Constants are Design-Context-fixed in Phase 2; tenant overrides land Phase 4 | Bootstrap "Watch points" |
| Phase 1 constants in YAML | NOT applicable — account-prior constants land as Python module constants in `app/scoring.py`, not in `app/rules.yaml`. `rules.yaml` carries thresholds (`allow_max`, `block_min`) only. | Verification §2.3 — no Pydantic/dataclass default drift |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md; pre-commit hooks fire on every commit (now installed locally — `pre-commit install` ran during Phase 2 precondition resolution). **Reviewer-panel quota is available for Phase 2** — every code-path commit runs the full panel at commit time; no retro-panel fallback pattern is planned.
- Reviewer routing per CLAUDE.md triage gate:
  - Every commit in this batch touches `app/scoring.py` (the scoring formula) → **standard path** (senior-engineer + security-auditor + code-flow-reviewer). `app/scoring.py` is **never-skip** per CLAUDE.md.
  - Commits that change tests also run test-reviewer.
- Reviewer-invocation slice template:
  > `Plan file: PLAN_PHASE_2A.md, current commit: 2A.N (<title>), upcoming commits: 2A.{N+1} through 2A.4 sections. Read only those sections.`

---

## Cross-batch dependencies

- **Consumes from Phase 1**: `app/trust.py::compute_trust_score` (already attached to Context as `trust_score`); `app/rules.yaml` rules with `maturity_sensitive: true` (5 rules carry the flag in Phase 1); `customer_baselines.value_n` (exposed as `customer_observations`); `customers.first_seen` / `total_shipments` / `flagged_count`.
- **Consumed by later batches**: 2B extends Context but does not change scoring; 2C adds rules that may be marked `maturity_sensitive`; 2D applies tuned thresholds and re-asserts case-2 reaches BLOCK with Layer 2 active.

---

## 2A.1 — Constants module + maturity helper

**Theme**: Introduce `app/scoring_constants.py` with the locked account-prior constants and the `maturity()` helper. Keep `app/scoring.py` shape unchanged; new module is pure data + one pure function.

**Files**:
- `app/scoring_constants.py` (NEW)
- `tests/unit/test_scoring_constants.py` (NEW)

**Specifics**:

`scoring_constants.py`:
```python
"""Layer 2 account-prior constants and the maturity helper.

Values are Design-Context-fixed for Phase 2; Phase 4 introduces
per-tenant override via tenants.config. These constants are not in
app/rules.yaml because they're scoring-formula machinery, not rule
parameters; rules.yaml continues to own allow_max / block_min only.

See .ai/decisions.md § Scoring architecture for the formula's
derivation and the divergences from FreightSentry's scorer.go (which
uses min-of-fractions with log1p-scaled shipments and 2-tier
noisy-OR flag weights — neither of which we adopt).
"""

MAX_NEW_ACCOUNT: float = 0.10
TRUST_FACTOR: float = 0.25
MATURITY_AGE_DAYS: int = 180
MATURITY_SHIPMENTS: int = 50
MATURITY_K: float = 0.30

# 4-tier direct-lookup table indexed by flagged_count_tier:
#   0 flagged shipments        → tier 0 → 0.00
#   1-2 flagged shipments      → tier 1 → 0.15
#   3-5 flagged shipments      → tier 2 → 0.25
#   6+ flagged shipments       → tier 3 → 0.35
FLAG_WEIGHTS: tuple[float, ...] = (0.00, 0.15, 0.25, 0.35)


def flagged_count_tier(flagged_count: int) -> int:
    if flagged_count <= 0:
        return 0
    if flagged_count <= 2:
        return 1
    if flagged_count <= 5:
        return 2
    return 3


def maturity(age_days: int, total_shipments: int) -> float:
    age_frac = min(max(age_days, 0) / MATURITY_AGE_DAYS, 1.0)
    ship_frac = min(max(total_shipments, 0) / MATURITY_SHIPMENTS, 1.0)
    return age_frac * ship_frac
```

`tests/unit/test_scoring_constants.py` — pure math, no DB:
- `test_maturity_zero_when_brand_new`: age=0, shipments=0 → 0.0
- `test_maturity_one_when_saturated`: age=180, shipments=50 → 1.0
- `test_maturity_one_when_over_saturated`: age=365, shipments=200 → 1.0 (clamps)
- `test_maturity_clamps_negative_inputs`: age=-10, shipments=-5 → 0.0
- `test_maturity_multiplicative_form`: age=90 days (frac=0.5), shipments=25 (frac=0.5) → 0.25 — the multiplicative product is more conservative than `min`, which would return 0.5
- `test_maturity_dominated_by_lesser_factor`: age=180 (frac=1.0), shipments=10 (frac=0.2) → 0.2 (effectively the smaller factor when one saturates)
- `test_flagged_count_tier_boundaries`: 0→0, 1→1, 2→1, 3→2, 5→2, 6→3, 1000→3
- `test_flagged_count_tier_negative_clamps_to_zero`: -5 → 0
- `test_flag_weights_table_length_matches_tier_count`: assert `len(FLAG_WEIGHTS) == 4`
- `test_constants_immutable`: assert `FLAG_WEIGHTS` is `tuple` (cannot be mutated in-place)

**Validation**:
- `pytest tests/unit/test_scoring_constants.py -v` — all 9 tests pass
- `ruff check app/scoring_constants.py tests/unit/test_scoring_constants.py` clean
- `mypy app/scoring_constants.py` clean

**Risk**: **Low**. Pure constants + one pure helper; no I/O, no rule wiring yet.

**Reversibility**: Easy — `git revert` removes the module; no consumers yet.

**Pre-commit verification**: ruff, ruff-format, mypy on `app/`, pytest unit (these constants only land in unit tests for this commit). All gates green.

**Observability**: N/A (no runtime behavior added).

**Test changes**: 9 unit tests added; no existing tests touched.

**Rollback plan**: `git revert <hash>`. The module has no callers in this commit.

**Declared breaks**:
- Scope: `app/scoring_constants.py` and its `maturity()` helper exist but no production code calls them. The first caller arrives in 2A.3.
- Resolved in: 2A.3 (scorer wires in Layer 2 + maturity downweight).

**Reviewer routing**: Standard path. `app/scoring.py` is not touched in this commit, but the constants are scoring-formula machinery — security-auditor reviews against the "no scoring shortcut" dimension; senior-engineer reviews against the divergence-from-scorer.go note in `.ai/decisions.md`; code-flow reviewer checks clamps + multiplicative semantics; test-reviewer reviews 9 boundary tests.

---

## 2A.2 — `.ai/decisions.md` amendment recording formula divergences

**Theme**: Amend `.ai/decisions.md` § Scoring architecture with the explicit Layer 2 formula, the four documented divergences from FreightSentry's `scorer.go`, and the Phase 2 wiring intent. This is a doc-only commit so reviewers can audit the diff alongside the constants module before any executable wiring lands.

**Files**:
- `.ai/decisions.md` (EDIT — amend § Scoring architecture > Layer 2 subsection)

**Specifics** — add an "Amendment 2026-05-26 (Phase 2A planning)" sub-section under the existing Layer 2 block containing:

1. **The exact formula** (verbatim from this batch plan).
2. **Four documented divergences from scorer.go:300-415**:
   - Maturity formula is multiplicative `clamp(age_frac) * clamp(ship_frac)`, not `min(age_frac, ship_frac)`. The product is more conservative when both factors are moderate (e.g. 0.5 × 0.5 = 0.25 vs `min` = 0.5).
   - Shipments fraction is **linear**: `total_shipments / 50` clamped to [0, 1]. Not the `log1p(shipments) / log1p(50)` form scorer.go uses.
   - Flag prior is **4-tier direct-lookup** indexed by `flagged_count_tier`, not 2-tier noisy-OR over independent tier activations.
   - **No customer-inheritance term.** Single-customer maturity only; FreightSentry's customer-aggregate `customer_inheritance_factor=0.50` is out of scope (the new project has no enterprise-level aggregate to inherit from at Phase 2).
3. **Why the divergences are intentional**: per verification §3.3, the Design Context picks the foundation-default values; FreightSentry's tuning was a response to its rule mix, not ours. Phase 6 staging replay measures FPR at the resulting operating point.
4. **Where `MaturityK` and other constants live**: `app/scoring_constants.py` (Phase 2A). NOT in `rules.yaml` (which owns only `allow_max` / `block_min` per verification §2.3). NOT in pydantic-settings. Single source of truth; rebinding requires a code change reviewed under the never-skip rule.

**Validation**:
- Manual read — formula and divergences match the bootstrap-prompt's "Scoring infrastructure" section verbatim.
- Grep `.ai/decisions.md` for `MaturityK = 0.30` and `flag_weights = [0.00, 0.15, 0.25, 0.35]` (must appear).
- No other files touched.

**Risk**: **Low**. Doc-only.

**Reversibility**: Easy.

**Pre-commit verification**: trailing-whitespace, end-of-file-fixer, check-yaml pass (`.ai/decisions.md` is markdown, not YAML, but pre-commit's yaml check skips non-yaml files).

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**: None (self-contained).

**Reviewer routing**: Per CLAUDE.md "borderline rule" — any `.ai/decisions.md` edit is ALWAYS standard path with doc-reviewer at minimum. Doc-reviewer reviews against the four-divergence shape; senior-engineer reviews that the formula in the amendment matches what 2A.3 will implement.

---

## 2A.3 — Wire Layer 2 + Layer 3 maturity downweight + booking endpoint call-site update

**Theme**: This is the load-bearing commit of the batch. Atomic change spanning (a) the `score()` formula extension and (b) the single call-site update in `app/api/booking.py`. Splitting these into two commits would leave 2A.3-alone as a broken intermediate state (the booking endpoint wouldn't construct `CustomerState`, Phase 1 integration tests would fail) — so they land together. Reviewer panel sees both dimensions on one diff.

**Files**:
- `app/scoring.py` (EDIT)
- `app/api/booking.py` (EDIT — single call site updated to construct `CustomerState` and pass it)
- `tests/unit/test_scoring_layer2.py` (NEW)

**Specifics**:

Signature change of `score()`:
```python
def score(
    ruleset: RuleSet,
    ctx: Mapping[str, Any],
    *,
    customer_state: CustomerState,  # NEW required keyword arg
) -> ScoringResult: ...
```

Where `CustomerState` is a new lightweight frozen dataclass in `app/scoring.py`:
```python
@dataclass(frozen=True)
class CustomerState:
    """Subset of Context needed for Layer 2 + maturity-downweight on Layer 3.

    Passed explicitly rather than re-reading from ctx so the scoring
    function has typed access without the DSL whitelist's `Mapping[str, Any]`
    shape. Callers (booking endpoint, integration tests) build this from
    the same fields they put into ctx.
    """
    trust_score: float
    account_age_days: int
    total_shipments: int
    flagged_count: int
```

Layer-by-layer:

1. Layer 1 short-circuit unchanged. Hard-block still returns `score=1.0, decision=BLOCK` and **bypasses Layer 2 entirely**. (FreightSentry does the same per scorer.go:305-328.) This means Layer 2 is irrelevant for hard-blocks; do not compute it before the Layer 1 loop.

2. Between the Layer 1 loop and the Layer 3 loop, compute Layer 2:
   ```python
   m = maturity(customer_state.account_age_days, customer_state.total_shipments)
   base_prior = MAX_NEW_ACCOUNT * (1 - m)
   trust_risk = max(0.0, (0.5 - customer_state.trust_score) / 0.5)
   trust_contribution = trust_risk * TRUST_FACTOR
   flag_prior = FLAG_WEIGHTS[flagged_count_tier(customer_state.flagged_count)]
   account_prior = _noisy_or([base_prior, trust_contribution, flag_prior])
   ```

3. Layer 3 loop: for each fired non-BLOCK rule, compute effective weight:
   ```python
   if rule.maturity_sensitive:
       effective_weight = rule.weight * (1.0 - MATURITY_K * (1.0 - m))
   else:
       effective_weight = rule.weight
   weights.append(effective_weight)
   ```
   (Important: when `maturity` is 1.0 — fully-established customer — the multiplier `(1 - MaturityK * (1 - 1)) = 1.0`, so a fully-mature customer gets the unmodified weight. When `maturity` is 0.0 — brand-new customer — the multiplier is `(1 - 0.30 * 1) = 0.70`, so the rule fires at 70% strength. The downweight monotonically REDUCES the weight for cold-start customers; it never amplifies.)

4. Final score: `signal_score = _noisy_or(effective_weights)`; `final = _noisy_or([account_prior, signal_score])`.

5. `_decide(final, ruleset.thresholds)` unchanged.

6. Persist `account_prior` + `signal_score` + `maturity` as new fields on `ScoringResult` (frozen dataclass extended):
   ```python
   @dataclass(frozen=True)
   class ScoringResult:
       score: float                          # = final
       account_prior: float                  # NEW — Layer 2 contribution
       signal_score: float                   # NEW — Layer 3 contribution (pre-noisy-OR-with-prior)
       maturity: float                       # NEW — for observability and case-2/case-1 explain
       decision: Literal["ALLOW", "REVIEW", "BLOCK"]
       classification: Literal["GREEN", "YELLOW", "RED"]
       risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
       triggered_rules: tuple[str, ...]
       risk_factors: tuple[RiskFactor, ...]
   ```

   Adding fields (vs renaming or removing) is purely additive; the booking endpoint reads `result.score` / `result.decision` / `result.triggered_rules` and can continue to ignore the new fields until 2A.4 wires the observability emission. The new fields are still callable from tests.

`tests/unit/test_scoring_layer2.py` — unit tests against the scoring function with a stub `RuleSet` (no DB):

- `test_layer1_short_circuit_skips_layer2`: BLOCK rule fires → `account_prior == 0.0`, `score == 1.0` (Layer 2 must NOT be computed for hard-blocks; the test asserts `account_prior` is 0 in the result to confirm).
- `test_brand_new_customer_account_prior_above_zero`: `account_age_days=0, total_shipments=0, trust_score=0.5, flagged_count=0` → `m=0`, `base_prior=0.10`, `trust_risk=0`, `flag_prior=0` → `account_prior = noisyOR(0.10, 0, 0) = 0.10`.
- `test_established_customer_account_prior_collapses`: `account_age_days=365, total_shipments=100, trust_score=0.9, flagged_count=0` → `m=1`, `base_prior=0`, `trust_risk=0`, `flag_prior=0` → `account_prior == 0.0`.
- `test_low_trust_drives_trust_contribution`: brand-new customer + `trust_score=0.1` → `trust_risk = (0.5 - 0.1) / 0.5 = 0.8`, `trust_contribution = 0.8 * 0.25 = 0.20`. Then `account_prior = noisyOR(0.10, 0.20, 0.00) ≈ 0.28`.
- `test_high_trust_zeros_trust_contribution`: `trust_score=0.95` → `trust_risk = max(0, (0.5 - 0.95)/0.5) = max(0, -0.9) = 0` → `trust_contribution = 0`.
- `test_flag_tier_lookup`: 0→0.00, 1→0.15, 3→0.25, 6→0.35 (each across a fixed customer otherwise zero-flag). Asserts the table lookup matches `FLAG_WEIGHTS`.
- `test_maturity_downweight_on_sensitive_rule_brand_new`: brand-new customer (`m=0`), one fired rule weight 0.40 marked `maturity_sensitive=True` → effective weight = `0.40 * (1 - 0.30) = 0.28`. Signal_score equals 0.28.
- `test_maturity_downweight_on_sensitive_rule_mature`: fully-mature customer (`m=1`), same rule → effective weight = `0.40 * 1.0 = 0.40`. Signal_score equals 0.40.
- `test_maturity_NOT_applied_when_flag_false`: brand-new customer, one fired rule weight 0.40 with `maturity_sensitive=False` → effective weight stays at 0.40.
- `test_layer2_layer3_compose_via_noisy_or`: brand-new customer with `account_prior=0.10` AND one fired Layer 3 rule weight 0.40 (non-maturity-sensitive) → `final = 1 - (1 - 0.10) * (1 - 0.40) = 1 - 0.54 = 0.46`.
- `test_no_layer3_rules_fired_uses_account_prior_only`: customer state forces `account_prior=0.10`, no rules fire → `signal_score=0.0`, `final = noisyOR(0.10, 0) = 0.10`.
- `test_no_account_prior_and_no_rules_returns_zero`: established customer with no fires → `account_prior=0, signal_score=0, final=0`. Decision is ALLOW with risk_level LOW.
- `test_account_prior_alone_crosses_REVIEW_band`: heavily-flagged brand-new customer with low trust — confirm we don't accidentally push to REVIEW from Layer 2 alone unless flags + trust are at extremes. Calibration check.

**Validation**:
- `pytest tests/unit/test_scoring_layer2.py -v` — all 13 tests pass
- `pytest tests/unit/ -q --asyncio-mode=auto` — entire unit suite passes (existing Layer 3 tests must still work; we will have changed their `score()` calls to pass `customer_state=CustomerState(...)` — see Test changes)
- `pytest tests/integration/ -q --asyncio-mode=auto` — Phase 1 integration tests pass with the new `customer_state` parameter (call sites updated in this same commit)
- `ruff check app/scoring.py tests/unit/test_scoring_layer2.py` clean
- `mypy app/` clean (strict mode)

**Risk**: **HIGH**. This is the central scoring change. Double-application of `account_prior`, mis-applied maturity downweight (wrong sign, wrong sensitivity check), Layer 1 short-circuit accidentally including Layer 2 — every one of these would silently miscalibrate scores. The unit-test boundary cases above plus reviewer attention are non-negotiable.

**Reversibility**: Moderate. The booking endpoint's call site changes signature (must pass `customer_state`). A revert restores Phase 1 scoring with no Layer 2; integration tests would re-pass under that revert. Phase 2C would need to re-build maturity wiring.

**Pre-commit verification**: ruff, ruff-format, mypy strict, pytest unit, check-yaml. All green.

**Observability**:
- Booking endpoint's structured log adds `account_prior`, `signal_score`, `maturity` fields tagged `metric: true` for Phase 5 sink. The log existed in Phase 1 with `score, decision, triggered_rules` — we extend, not replace.
- 2A.4 expands on this with a dedicated `risk.evaluation` log shape; this commit lands minimum observability so reviewers can audit Layer 2 contributions in case-2 fixture runs.

**Test changes**:
- 13 new unit tests in `test_scoring_layer2.py` (above)
- Existing `tests/unit/test_scoring.py` tests updated to pass `customer_state=CustomerState(trust_score=..., account_age_days=..., total_shipments=..., flagged_count=...)` to `score()`. Existing assertions on `result.score / result.decision / result.triggered_rules` unchanged. (test_reviewer will check that Phase 1 boundary cases still produce the same final scores, since they all use established customers + non-sensitive rules where Layer 2 contributes 0.)
- Integration tests in `tests/integration/test_booking_endpoint.py` (case-2 etc.): updated where `score()` is called. The booking endpoint itself constructs `CustomerState` from the same fields it puts in Context. The case-2 pipeline test (`test_unfamiliar_ip_against_established_customer_triggers_signals`) is expected to still see `score > 0.0` because the signal_score path is unchanged for the fired rules; the assertion in this commit STAYS at `> 0.0`. The BLOCK assertion lands in 2D.

**Rollback plan**: `git revert <hash>`. Revert restores the pre-2A.3 signature; existing tests pass against the reverted code (because they were green before this commit too).

**Declared breaks**:
- Scope: `ScoringResult` gains `account_prior`, `signal_score`, `maturity` fields, additive only. No existing field renamed or removed. Existing readers continue to work.
- Resolved in: 2A.4 (observability emission consumes the new fields).

**Reviewer routing**: Standard path — senior-engineer + security-auditor + code-flow-reviewer + test-reviewer. NEVER-SKIP per CLAUDE.md (scoring formula change). Specifically:
- senior-engineer verifies (i) the formula matches `.ai/decisions.md` § Scoring architecture amendment (from 2A.2) and the bootstrap prompt's "Scoring infrastructure" section, and (ii) the booking endpoint correctly threads `CustomerState` from existing Context fields.
- security-auditor verifies Layer 2 cannot be triggered for hard-blocks (the bypass invariant), `CustomerState` carries no PII (all integers + one float), and the endpoint call-site doesn't accidentally widen the data that flows into scoring.
- code-flow-reviewer verifies the noisy-OR composition order: `account_prior` and `signal_score` are independent inputs to a final noisy-OR — not nested, not added. Reviewer also checks the single endpoint call-site is the only call-site changed (no broader scoring API drift).
- test-reviewer verifies the 13 boundary cases cover the four divergences from scorer.go's formula, and that the integration suite continues to pass against the updated call site.

---

## 2A.4 — Observability + run case-2 pipeline test under Layer 2

**Theme**: Refine the booking endpoint's structured log to emit a clear `risk.evaluation` event with all Layer 2 + Layer 3 components, and assert that the Phase 1 case-2 pipeline test still passes under Layer 2 wiring. NO new tests for case-1 yet (that's 2D); this commit confirms the existing case-2 verification path is undisturbed by Layer 2 additions.

**Files**:
- `app/api/booking.py` (EDIT — refine log statement)
- `tests/integration/test_booking_endpoint.py` (EDIT — strengthen the existing case-2 assertion to also assert `account_prior > 0.0` for a brand-new customer attached to a new tenant; existing `score > 0.0` assertion preserved)

**Specifics**:

Log shape:
```python
_log.info(
    "risk.evaluation",
    metric=True,
    tenant_id=auth.tenant_id,
    request_id=payload.request_id,
    decision=result.decision,
    score=result.score,
    account_prior=result.account_prior,
    signal_score=result.signal_score,
    maturity=result.maturity,
    triggered_rules=list(result.triggered_rules),
    trust_score=ctx["trust_score"],
    flagged_count=ctx["flagged_count"],
)
```

`metric: true` tag lets Phase 5 CloudWatch EMF sink pick this up as a measurement-shaped record. The fields land as structured-log keys, not free-text; downstream parsers (Phase 5) consume them directly.

Existing case-2 assertion (`test_unfamiliar_ip_against_established_customer_triggers_signals`) is strengthened. Currently asserts `result["score"] > 0.0` and certain rules fire. We extend to also assert:
- `result["account_prior"] >= 0.0` (sanity — established customers may have near-zero account_prior; we only assert non-negative)
- The log emission carries `metric=true` and the new fields (use `caplog` fixture)

We do NOT yet assert case-2 reaches BLOCK; that calibration lands in 2D once tuned thresholds are in.

**Validation**:
- `pytest tests/integration/test_booking_endpoint.py -v -k case_2 -q --asyncio-mode=auto` — case-2 test passes with the strengthened assertion
- `pytest tests/ -q --asyncio-mode=auto` — full suite green
- Manual: examine a sample structured-log emission in test output to confirm `metric=True` and the field set match the spec above

**Risk**: **Low**. Observability + assertion-strengthening only.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: This commit IS the observability. Emits `risk.evaluation` with `metric: true` and all Layer-2/Layer-3 components.

**Test changes**: Strengthens existing case-2 test; adds `caplog`-based assertion on the log emission.

**Rollback plan**: `git revert <hash>`. Endpoint reverts to a simpler log; case-2 test continues to pass against the reverted log shape (the `caplog` assertion is the only thing that would fail; rollback restores the previous assertion set).

**Declared breaks**: None.

**Reviewer routing**: Standard path. test-reviewer specifically checks that the `caplog`-based assertion is robust (not flaky against log-order changes) and that `metric: true` is correctly attached.

---

## Batch 2A summary

4 commits:
- 2A.1 — Constants module + maturity helper (with 9 unit tests)
- 2A.2 — `.ai/decisions.md` Layer 2 formula amendment + 4 divergence notes
- 2A.3 — Wire Layer 2 + maturity downweight into `app/scoring.py` + booking endpoint call-site update (atomic; with 13 unit tests, integration-suite sweep)
- 2A.4 — Structured-log refinement + strengthened case-2 assertion

At the end of Batch 2A, the scoring formula is the full 3-layer Design Context shape. Phase 2C will add rules that consume `trust_score` and `maturity_sensitive`; Batch 2D will apply tuned thresholds and assert case-2 reaches BLOCK.

**Expected test count after 2A**: 274 (Phase 1 baseline) + 9 (2A.1) + 13 (2A.3) + 0-1 from caplog strengthening (2A.4) ≈ **296-297 tests**.

**No new Phase 1 schema migration. No DSL whitelist change. No `app/rules.yaml` edits.** All changes are in `app/scoring.py` + `app/scoring_constants.py` + `app/api/booking.py` + tests + `.ai/decisions.md`.
