# REPORT_PHASE_2A.md

Batch 2A execution disposition. Operator checkpoint per
`PLAN_PHASE_2A.md` and the Phase 2 bootstrap-prompt mandatory-stop list.
Waiting on operator approval before Batch 2B scope is opened.

---

## Aggregate stats

| Metric | Value |
|---|---|
| Commits in Batch 2A | 5 (`713f1d4` 2A.1 → `b8ede67` 2A.4) |
| Production source files touched | 3 (1 NEW: `app/scoring_constants.py`; 2 EDITED: `app/scoring.py`, `app/api/booking.py`) |
| Tests passing | 302 / 302 (1.91 s wall-time) |
| New tests in 2A | 28 (11 in `test_scoring_constants.py`, 17 in `test_scoring_layer2.py`; existing-test edits not counted) |
| Plan-expected test count | 296-297 — exceeded by ~5 due to additive tier-coverage and constants-immutability checks |
| Validation tooling | `ruff check` clean · `mypy --strict` clean · `pytest --asyncio-mode=auto` 302/302 · pre-commit hooks green on every commit |
| Net diff vs pre-2A | +633 / −105 across 11 files |
| Scoring formula | 3-layer noisy-OR shape (Layer 1 hard-block → Layer 2 account_prior → Layer 3 signal_score → noisyOR-compose) |

---

## Per-commit disposition

| Commit | Theme | Reviewer panel | Outcome |
|---|---|---|---|
| `713f1d4` | 2A.1 — `app/scoring_constants.py` + `maturity()` helper + 11 unit tests | senior-engineer + security-auditor + code-flow + test-reviewer | SHIP IT / LOW RISK / CLEAN / ACTUALLY GOOD |
| `1549ea4` | 2A.2 — `.ai/decisions.md` Layer 2 amendment + 4 divergences from `scorer.go` | doc-reviewer + senior-engineer | PUBLISH / SHIP IT |
| `78c112a` | Pre-commit hook fix (add `structlog`; drop `asyncpg-stubs`) | triage-gate trivial (config) | committed without panel; rationale in commit body |
| `39bf49d` | 2A.3 — Layer 2 wiring + maturity downweight + booking endpoint call-site update + 17 unit tests | full standard panel | SHIP IT / LOW RISK / CLEAN / ACTUALLY GOOD |
| `b8ede67` | 2A.4 — `risk.evaluation` log + strengthened case-2 `structlog.testing.capture_logs()` assertion | full standard panel | SHIP IT / LOW RISK / CLEAN / ACTUALLY GOOD (1 constructive tightening applied pre-commit) |

---

## Plan deviations

Three deviations from `PLAN_PHASE_2A.md`, none material to scoring correctness:

| Commit | Plan said | Actual | Why |
|---|---|---|---|
| 2A.1 | 9 unit tests in `test_scoring_constants.py` | 11 | Boundary discipline: added `test_flag_weights_table_length_matches_tier_count` and `test_constants_immutable` as cheap additive checks. |
| 2A.3 | 13 unit tests in `test_scoring_layer2.py` | 17 | Implementer split the planned `test_flag_tier_lookup` parameterized check into 4 explicit cases (tier 0/1/2/3) and added `test_maturity_field_exposed_on_result` sanity check. More coverage, not less. |
| 2A.4 | Use `caplog` fixture for log assertion | `structlog.testing.capture_logs()` | `caplog` only captures stdlib `logging` records; structlog's `PrintLoggerFactory` writes directly to stdout, bypassing stdlib. `capture_logs()` intercepts events at the BoundLogger boundary — the canonical structlog test helper. Reviewer panel called this out as a better choice. |

A separate infrastructure precondition (`78c112a`) was committed mid-batch — not in the plan but necessary to unblock 2A.3's mypy hook (the first 2A commit to touch a file that imports `structlog`). Recorded as a precondition commit rather than amended onto 2A.3 to preserve atomic commit semantics.

---

## Reviewer-caught corrections

Material findings turned into code changes within the batch:

- **2A.3 (idempotency precision)**: DB NUMERIC round-trip of the brand-new-customer `account_prior` (0.099999... → 0.1) surfaced a fragility in `test_duplicate_request_id_returns_idempotent`. Split into per-field exact equality (decision / classification / risk_level / triggered_rules / risk_factors) plus `pytest.approx(abs=1e-9)` on score only. Phase-1's `0.0` baseline never reached this precision boundary; Phase-2's `0.10` does.
- **2A.4 (constructive tightening, pre-commit)**: test-reviewer noted `account_prior >= 0.0` is tautological (noisy-OR cannot go negative). Applied `> 0.0` on `account_prior` and `0.0 < maturity < 1.0` (strict bounds) — the case-2 customer (age=90d, shipments=20) makes both bounds provable, and the change catches the Layer 2 short-circuit regression class.

No reviewer-flagged finding required a follow-up commit.

---

## Layer 2 formula — locked

Constants live in `app/scoring_constants.py` (single source of truth; not in `rules.yaml`, not in pydantic-settings):

```
MAX_NEW_ACCOUNT     = 0.10
TRUST_FACTOR        = 0.25
MATURITY_AGE_DAYS   = 180
MATURITY_SHIPMENTS  = 50
MATURITY_K          = 0.30
FLAG_WEIGHTS        = (0.00, 0.15, 0.25, 0.35)   # 4-tier direct lookup
```

Formula (applied in `app/scoring.py::score` between the Layer 1 BLOCK loop and the Layer 3 signal loop):

```
maturity         = clamp(age_days / 180) * clamp(total_shipments / 50)
base_prior       = MAX_NEW_ACCOUNT * (1 - maturity)
trust_risk       = max(0, (0.5 - trust_score) / 0.5)
trust_contrib    = trust_risk * TRUST_FACTOR
flag_prior       = FLAG_WEIGHTS[flagged_count_tier(flagged_count)]
account_prior    = noisyOR(base_prior, trust_contrib, flag_prior)
```

Maturity downweight on per-rule basis (only when `rule.maturity_sensitive`):

```
effective_weight = weight * (1 - MATURITY_K * (1 - maturity))
```

Final score: `noisyOR(account_prior, signal_score)`. Layer 1 BLOCK bypasses Layer 2 entirely (sentinel zeros on `ScoringResult.{account_prior, signal_score, maturity}`).

Four documented divergences from FreightSentry's `scorer.go` (recorded in `.ai/decisions.md` 2A.2 amendment): multiplicative vs. min-of-fractions maturity; linear vs. log1p shipments fraction; 4-tier direct-lookup vs. 2-tier noisy-OR flag prior; no customer-inheritance term.

---

## Observability

Booking endpoint emits `risk.evaluation` with `metric=True` and the full Layer 2 + Layer 3 component set:

```
event=risk.evaluation
metric=True
tenant_id, request_id, decision, score
account_prior, signal_score, maturity         # NEW Layer 2 + composition
triggered_rules, trust_score, flagged_count   # NEW context-derived
```

Phase 5 CloudWatch EMF sink ingests this directly. `caplog`-style assertion (`structlog.testing.capture_logs()`) on the case-2 integration test verifies the event shape end-to-end.

---

## Explicitly deferred from Batch 2A

Per `PLAN_PHASE_2A.md` declared-breaks and `MASTER_PLAN.md` Phase 2 scope:

- **11 trust-conditioned + dormancy + lock-in + residential-asn rules.** Phase 2B + 2C — Batch 2A wires the formula machinery only.
- **DSL whitelist extension** (currently 45 fields; grows to 56 in 2B).
- **`destination_hmac` migration.** Baked into 2B.6 plan upfront (column confirmed missing from Phase 1 schema via grep during planning).
- **Per-tenant constants override.** Phase 4 — Layer 2 constants are Design-Context-fixed for Phase 2.
- **Customer-inheritance term.** Out of scope (no enterprise-level aggregate to inherit from at Phase 2).
- **Case-1 fixture replay** (dashboard ATO). Phase 2D — Batch 2A's case-2 sweep only confirms Layer 2 wiring is undisturbed.
- **BLOCK-threshold calibration on case-2.** Phase 2D once tuned thresholds land. 2A asserts `score > 0` only; the BLOCK assertion is in 2D.

---

## Quality measurements

- **Layer 1 bypass invariant** (`test_layer1_short_circuit_skips_layer2`): a BLOCK rule firing returns `score=1.0` with `account_prior=0.0`, `signal_score=0.0`, `maturity=0.0` — three orthogonal sentinel checks confirm Layer 2 was never computed.
- **Account prior calibration ceiling** (`test_highly_flagged_low_trust_brand_new_pushes_account_prior_high`): worst-case Layer-2-only customer (trust=0.1, flagged_count=10, brand-new) yields `account_prior = 0.532 < allow_max 0.60` — confirms account_prior alone never tips to REVIEW.
- **Maturity downweight direction** (`test_maturity_downweight_on_sensitive_rule_brand_new` + `_mature`): brand-new customer downweights 0.40 → 0.28; fully-mature customer preserves 0.40. Multiplier `(1 - MATURITY_K * (1 - maturity))` monotonically reduces, never amplifies.
- **Layer 2 + Layer 3 composition** (`test_layer2_layer3_compose_via_noisy_or`): `account_prior=0.10` + one fired rule weight 0.40 → final `1 - 0.9 * 0.6 = 0.46`. Independent noisy-OR inputs, not nested.
- **Case-2 observability** (`test_unfamiliar_ip_against_established_customer_triggers_signals`): single `risk.evaluation` event captured with `metric=True`, partially-mature customer (maturity 0.20) yields `account_prior > 0` — Layer 2 short-circuit regression class is now under test.
- **Pre-commit hook coverage**: `ruff`, `ruff-format`, `mypy --strict app/`, `pytest tests/unit/ -x` fire on every commit; structlog dependency now present in mypy hook venv (precondition fix `78c112a`).

---

## Open items for Batch 2B (and the next operator action)

1. **Operator approval to open Batch 2B scope.** Per `PLAN_PHASE_2B.md`:
   - DSL whitelist extension (+11 fields → 56 total)
   - 6 trust-conditioned rules (`very_low_trust`, `low_trust_*`, etc.) wired against `trust_score` on Context
   - `destination_hmac` column migration (confirmed missing from Phase 1 schema)
   - 2 dormancy rules (`customer_dormant_then_active`, `dormant_then_high_value`) wired against `days_since_last_booking`

2. **No carry-forward to STATUS.md.** Batch 2A completed without checkpointing — all commits cleared the merge gate on first pass.

3. **Tracked-for-later** (not blocking 2B):
   - Reviewer-noted suggestion-tier item from 2A.3 senior-engineer: idempotent-replay test's `risk_factors` field uses exact equality on dataclass-roundtripped floats — after Phase 2C adds risk-factor weight serialization for trust-conditioned rules, this could surface a precision boundary. Defer to whoever next touches the idempotency test path.
   - Implicit-USD currency assumption carried forward to Phase 2D plan per memory (no per-currency normalization in 2A).

---

End of Batch 2A. Working tree clean. `feat/refactor` branch ready for Batch 2B operator approval.
