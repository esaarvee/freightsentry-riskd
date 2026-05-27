# Phase 3 — Batch 3D Plan — Currency decision + integration validation + wrap

> **Status (2026-05-27)**: Pending operator approval. Operator may defer until after 3C execution reports.

Batch 3D closes Phase 3 by (a) documenting the currency-implicit-USD decision and the Phase-4 deferral to per-tenant `value_caps`, (b) adding integration tests that demonstrate the cross-batch chain (booking → modification → feedback → next-booking-triggers-rule) — the canonical end-to-end demonstration that 3A and 3B compose correctly, (c) adding a maturity-aware modification scoring integration test that proves Layer 2 + Phase-3 modification rules compose as expected, and (d) producing the per-batch report `REPORT_PHASE_3D.md` plus the aggregate `REPORT_PHASE_3.md` Phase wrap.

Target: 4 commits.

---

## Decisions absorbed (batch-relevant subset)

| Decision | Value | Source |
|---|---|---|
| Currency normalization | Document the implicit-USD assumption in `.ai/decisions.md`. **Defer per-currency normalization to Phase 4** via `TenantConfig.value_caps: dict[str, float]`. Tenants whose currency is not USD will need Phase 4's per-tenant `value_caps` to calibrate. | Phase 3 bootstrap recommendation |
| Currency decision section placement | After existing § "Per-tenant configuration" in `.ai/decisions.md` (verification §1-2: existing section structure includes Per-tenant config at ~L213-L230). New section "Currency normalization (Phase 3D — deferred to Phase 4)" slots after Per-tenant configuration, before Cold start. | Verification §2 |
| Value-implicit rules corpus | 7 rules in `app/rules.yaml` use implicit-USD thresholds (verification §3): `vpn_high_value` (1000), `low_trust_high_value` (1000), `flags_with_value` (2000), `threat_intel_high_value` (2000), `ip2p_threat_high_value` (2000), `high_value_new_user` (5000), `absolute_high_value` (10000). Plus modification rule 1 `modification_within_30_min_value_increase` is currency-implicit at the magnitude-fraction level (no absolute threshold; `magnitude > 0.2`). | Verification §3 + 3A.7 |
| 3D modification rules consideration | Modification rule weights set in 3A.7 are NOT currency-affected (they condition on `modification_magnitude` as a fraction, not on absolute value). Document this explicitly — modification path inherits the same implicit-USD ASSUMPTION for absolute-value compares, but introduces NO NEW currency complexity. | Phase 3 bootstrap + 3A.7 |
| TenantConfig presence | NOT present in `app/` today (verification §4). Defer creation to Phase 4. | Verification §4 |
| Integration validation focus | Cross-batch chains that cannot be tested within a single batch's scope: booking → modification → feedback → next booking. Single-batch integration is already tested in 3A.8 (modification flow), 3B.6 (feedback chain), 3C.2 (cross-tenant), so 3D does NOT duplicate. | Phase 3 bootstrap |
| Phase 3 report aggregate | Follow Phase 2 shape: `REPORT_PHASE_3.md` summarizes 4 batches' aggregate stats, per-batch disposition, plan deviations, reviewer-caught corrections (with file:line refs), explicitly deferred items, Phase 4 inheritance. | Phase 3 bootstrap |
| If operator wants Phase-3 currency normalization | Surface as **separate AskUserQuestion** at 3D.1 planning — substantive scope decision. **Default**: documentation only (defer per-tenant work to Phase 4). | Phase 3 bootstrap |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md.
- Reviewer routing per CLAUDE.md triage gate:
  - 3D.1 (`.ai/decisions.md` amendment): borderline rule — `.ai/decisions.md` edits are **always standard path with doc-reviewer at minimum**. Standard panel + doc-reviewer.
  - 3D.2 (cross-batch integration test): test-only — test-reviewer + senior-engineer + code-flow.
  - 3D.3 (maturity + modification integration test): test-only — test-reviewer + senior-engineer + code-flow.
  - 3D.4 (`REPORT_PHASE_3D.md` + `REPORT_PHASE_3.md`): doc-only — doc-reviewer.

- Reviewer-invocation slice template:
  > `Plan file: PLAN_PHASE_3D.md, current commit: 3D.N (<title>), upcoming commits: 3D.{N+1} through 3D.4 sections. Read only those sections.`

---

## Cross-batch dependencies

- **Consumes from Phase 1**: `.ai/decisions.md` structure (Per-tenant config section).
- **Consumes from Phase 2**: 7 value-implicit rules in `app/rules.yaml`; Layer 2 maturity scoring at `app/scoring.py`.
- **Consumes from 3A**: modification endpoint at `app/api/modification.py`; `decisions.request_type` discriminator; `build_modification_context`; 8 modification rules; modification velocity SQL.
- **Consumes from 3B**: feedback endpoint at `app/api/feedback.py`; feedback table at post-3B.1 shape; 4 previously-rejected rules + Context derivations; `add_rejected_observation` wiring.
- **Consumes from 3C**: 2-tenant fixture pattern + audit doc structure (referenced by 3D.4 report aggregate).
- **Consumed by Phase 4 prompt**: aggregate report drives Phase 4 scope; `TenantConfig` + `value_caps` is the first Phase 4 deliverable per .ai/decisions.md amendment.

---

## 3D.1 — Currency-implicit-USD decision documented in `.ai/decisions.md`

**Theme**: Append a new section to `.ai/decisions.md` documenting that all absolute-value thresholds in `app/rules.yaml` are implicitly USD; that modification-magnitude thresholds are currency-independent (fractions); that per-tenant currency normalization is deferred to Phase 4 via `TenantConfig.value_caps`. Enumerate the 7 value-implicit Phase 2 rules + the 1 modification rule for traceability.

**Files**:
- `.ai/decisions.md` (EDIT — insert new section between "Per-tenant configuration" and "Cold start" per verification §2)

**Specifics**:

New section body (~80 lines markdown):

```markdown
## Currency normalization (Phase 3D — deferred to Phase 4)

**Decision (2026-05-27)**: All absolute-value thresholds in `app/rules.yaml` carry an implicit-USD assumption. Per-currency normalization is deferred to Phase 4 via `TenantConfig.value_caps: dict[str, float]`.

### Scope of the implicit-USD assumption

| Rule | Threshold | Currency assumption |
|---|---|---|
| `vpn_high_value` | `shipment_value > 1000` | USD |
| `low_trust_high_value` | `shipment_value > 1000` | USD |
| `flags_with_value` | `shipment_value > 2000` | USD |
| `threat_intel_high_value` | `shipment_value > 2000` | USD |
| `ip2p_threat_high_value` | `shipment_value > 2000` | USD |
| `high_value_new_user` | `shipment_value > 5000` | USD |
| `absolute_high_value` | `shipment_value > 10000` | USD |

The `shipment_value` Context field is set directly from `BookingRequest.shipment.value` (a `Decimal` per the Pydantic model), with no transformation. The booking request schema does not include a `currency` field today — it is presumed USD at the application boundary. Tenants whose business operates in CAD, EUR, GBP, etc. cannot use these rules accurately without per-tenant calibration.

### Modification-specific note

The Phase 3A modification rule `modification_within_30_min_value_increase` uses `modification_magnitude > 0.2` — a currency-independent fraction (`abs(new_value - old_value) / old_value`). This rule does not inherit the implicit-USD assumption.

The other 7 modification rules (3A.7) condition on categorical fields (type, time bucket, direction, velocity) and do not introduce currency complexity.

### Deferral to Phase 4

Phase 4 will introduce `TenantConfig.value_caps: dict[str, float]` (e.g. `{"USD": 10000, "CAD": 12500, "EUR": 9000}`) and:

1. Add an optional `currency: Literal["USD", "CAD", "EUR", ...]` field to `BookingRequest.shipment`.
2. Rewrite the 7 absolute-value rule conditions to consult tenant config: e.g. `shipment_value > tenant.value_caps.get(currency, tenant.value_caps['USD'])`.
3. Provide a Phase 4 migration helper to populate the default `value_caps` for existing tenants (all `{"USD": <existing thresholds>}` — no behavior change for USD-implicit tenants).

This deferral is intentional: Phase 3's scope is endpoint additions, not configuration model expansion. Mixing the two would conflate two different change axes.

### What this means today

- USD-implicit tenants are calibrated correctly out of the box.
- Non-USD tenants will see rule thresholds that don't match their currency. They have two options:
  1. **Wait for Phase 4** (recommended for production launch).
  2. **Provide values pre-converted to USD** at the integration boundary (operator-side conversion). Not a long-term solution but adequate for staging.

### Auditing

The 7 + 1 rule corpus is enumerated above. If a Phase 4+ change adds another absolute-value rule, this section must be updated. If a Phase 4+ change rewrites these rules to use `TenantConfig.value_caps`, this section should be marked `## Currency normalization (RESOLVED in Phase 4)` with the resolving migration referenced.
```

**Validation**:
- `markdownlint .ai/decisions.md` (visual confirmation if linter unavailable)
- Re-grep `app/rules.yaml` for `shipment_value >` — confirm 7 matches; no rule missed in the enumeration above.
- Doc-reviewer verifies the deferral rationale is clear and the Phase 4 hand-off is concrete.

**Risk**: **Low**. Doc-only.

**Reversibility**: Easy — revert removes the section.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: None. (A test that re-counts the value-implicit rules and asserts they all appear in the decision doc would be defensible — but is over-engineering at this stage. Skip per `Don't add features beyond what the task requires`.)

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: standard + doc-reviewer (per CLAUDE.md borderline rule "A `.ai/decisions.md` amendment: ALWAYS standard path with doc-reviewer at minimum").

---

## 3D.2 — Cross-batch integration test: booking → modification → feedback → next-booking chain

**Theme**: The canonical "all three Phase 3-touching endpoints in sequence" integration test. Demonstrates that 3A, 3B, and Phase 2's existing scoring compose correctly across batches.

**Files**:
- `tests/integration/test_phase3_cross_batch_chain.py` (NEW)

**Specifics**:

Test scenarios (each is one test):

1. **Full chain happy path**:
   a. Customer with mature baseline (~30 historical bookings to familiar destinations).
   b. POST booking → ALLOW.
   c. POST modification (small value bump, familiar destination, > 24h after booking) → ALLOW.
   d. POST feedback (label=approved) → applied=True, baseline `r_n` unchanged.
   e. POST next booking by same customer → ALLOW; `triggered_rules` contains no previously-rejected rule.

2. **Full chain fraud confirmation cascade**:
   a. Customer with thin baseline.
   b. POST booking → REVIEW.
   c. POST modification (destination change to unfamiliar address within 30 min) → BLOCK.
   d. POST feedback on the MODIFICATION's request_id (label=fraud_confirmed) → applied=True; baseline rejected dimensions incremented; flagged_count += 1, fraud_confirmed_count += 1.
   e. POST next booking by same customer from same source_ip + same email → `triggered_rules` contains `email_previously_rejected_for_customer`, `ip_previously_rejected_for_customer`, `origin_previously_rejected_for_customer`. Final decision: BLOCK.

3. **Modification feedback persistence across booking + modification**:
   a. POST booking → REVIEW.
   b. POST feedback on the booking (label=rejected) → applied.
   c. POST modification on the same booking — Context inherits `email_previously_rejected=True`, etc. → BLOCK.
   d. POST feedback on the MODIFICATION (label=fraud_confirmed, upgrade) → applied; counter delta: `flagged_count += 0` (already counted from rejected); `fraud_confirmed_count += 1`.
   e. Assert customer's final flagged_count == 1, fraud_confirmed_count == 1 (idempotent under monotonicity upgrade).

4. **Approved-then-rejected does NOT double-flag**:
   a. POST booking → ALLOW.
   b. POST feedback approved → applied; no counters incremented.
   c. POST feedback rejected (new request_id, same target) → applied (upgrade); flagged_count += 1.
   d. Assert customer flagged_count == 1.

5. **Modification of modification rejected, then feedback on original booking still works**:
   a. POST booking → REVIEW.
   b. POST modification → BLOCK.
   c. POST another modification targeting the previous MODIFICATION's request_id → 422 (per Phase 3 scope "Modification of modifications — explicitly out").
   d. POST feedback on the ORIGINAL booking's request_id → applied, baseline updated.
   e. Sanity: original booking and the modification are both findable via the feedback's `target_request_id` matching their respective request_ids.

6. **Cross-tenant chain isolation**:
   a. Tenant A: POST booking → POST feedback rejected.
   b. Tenant B: POST booking by a customer using the SAME email (HMAC computes to same value, but it's tenant B's own customer baseline).
   c. Assert Tenant B's booking does NOT trigger `email_previously_rejected_for_customer` (rejected dict is per-customer per-tenant; tenant A's rejections do not leak to tenant B).

**Validation**:
- `pytest tests/integration/test_phase3_cross_batch_chain.py -v` — 6 tests pass.
- `pytest tests/ --asyncio-mode=auto -q` — full suite green.
- Each test runs in <10s (cross-tenant fixture setup is the dominant cost).

**Risk**: **Medium**. Long chains amplify any flakiness in fixtures or shared baseline state. Each test must clean up customer baseline state to prevent test pollution. Use the test-DB fixture pattern that wipes per-test.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 6 integration tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: test-only — test-reviewer + senior-engineer + code-flow.

---

## 3D.3 — Maturity + modification integration test: Layer 2 downweight composes correctly

**Theme**: Integration test that exercises Phase 2's Layer 2 + maturity downweight in combination with Phase 3A's modification rules. Confirms that maturity-sensitive modification rules (`modification_destination_change_pre_pickup`, `modification_dormant_customer`, etc.) downweight correctly for thin-baseline customers AND fire at full weight for mature customers — and that the noisy-OR composition matches expectations.

**Files**:
- `tests/integration/test_modification_maturity_composition.py` (NEW)

**Specifics**:

Scenarios:

1. **Thin baseline modification scoring**:
   - Customer with 3 historical bookings (immature, maturity < 0.3).
   - POST modification (destination change to unfamiliar address within 24h).
   - Assert: `modification_destination_change_pre_pickup` (maturity_sensitive=True) contributes a downweighted weight to the final score per `app/scoring.py` Layer 2 logic.
   - Assert: final score lower than the same modification against a mature customer would produce.

2. **Mature baseline modification scoring**:
   - Customer with 50 historical bookings (mature, maturity ≥ 0.7).
   - Same modification as above.
   - Assert: `modification_destination_change_pre_pickup` fires at full weight (no downweight).
   - Assert: final score higher than the thin-baseline case.

3. **Modification dormancy compound**:
   - Customer with 30 historical bookings BUT no booking for 90 days (dormant per `is_abnormally_dormant`).
   - POST modification (destination change).
   - Assert: `modification_dormant_customer` fires + `modification_destination_change_pre_pickup` fires. Final score reflects compound (noisy-OR).
   - Compare to non-dormant baseline: dormant case has higher score.

4. **Non-maturity-sensitive modification rules unaffected**:
   - `modification_high_velocity_1h` (maturity_sensitive=False, per 3A.7).
   - Same customer, 5 modifications within 1h.
   - Assert: rule fires at full weight regardless of customer maturity (no downweight).

5. **Modification + Layer 2 account_prior interaction**:
   - Customer with high `flagged_count` (low trust_score).
   - POST modification (small risk).
   - Assert: `account_prior` field on response reflects the trust deficit. Final score includes both modification rule contributions and the account_prior shift.

6. **Phase 2 case-2 regression with Phase 3 rules present**:
   - Re-run case-2 fixture (Phase 2 ATO case) with the 8 modification rules + 4 previously-rejected rules registered (loaded from rules.yaml).
   - Assert: case-2 booking still reaches BLOCK (no regression).
   - Phase 3 rules do NOT fire on case-2 (it's a booking, not a modification; rejected dimensions are empty in baseline).
   - This is the Phase 2 invariant guard.

**Validation**:
- `pytest tests/integration/test_modification_maturity_composition.py -v` — 6 tests pass.
- `pytest tests/integration/test_case_2.py -v` — Phase 2's case-2 BLOCK test still passes (regression guard).
- `pytest tests/ --asyncio-mode=auto -q` — full suite green.

**Risk**: **Medium**. Layer 2 maturity math is non-trivial; assertions on numeric thresholds must allow for floating-point tolerance. Use `pytest.approx` for score comparisons.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 6 integration tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: test-only — test-reviewer + senior-engineer + code-flow.

---

## 3D.4 — Phase 3 wrap reports

**Theme**: Produce `REPORT_PHASE_3D.md` (Batch 3D's per-batch report) AND `REPORT_PHASE_3.md` (aggregate Phase 3 report). Both follow Phase 2 report shape: aggregate stats, per-batch disposition, plan deviations, reviewer-caught corrections with file:line refs, explicitly deferred items, Phase 4 inheritance.

**Files**:
- `REPORT_PHASE_3D.md` (NEW)
- `REPORT_PHASE_3.md` (NEW)

**Specifics**:

`REPORT_PHASE_3D.md` shape (~150 lines):

```markdown
# Phase 3 — Batch 3D Report

**Batch**: 3D — Currency normalization decision + integration validation + Phase 3 wrap
**Commits**: 3D.1 through 3D.4 (4 commits)
**Date range**: 2026-05-27 to 2026-05-XX
**Status**: COMPLETE

## Aggregate stats

| Metric | Pre-3D | Post-3D |
|---|---|---|
| Rule count | 79 | 79 (unchanged) |
| Test count | 571 | 583 (+12 from 3D.2 + 3D.3) |
| ALLOWED_CONTEXT_FIELDS | 66 | 66 (unchanged) |
| Migrations | 4 | 4 (unchanged) |
| `.ai/decisions.md` sections | N | N+1 (Currency normalization) |

## Per-commit disposition

[Table: commit hash, theme, lines added/removed, tests added, reviewer panel, verdicts, cycles]

## Plan deviations

[Any deviations from PLAN_PHASE_3D.md, with explanation]

## Reviewer-caught corrections

[With file:line refs]

## Currency decision summary

[1-paragraph summary of what was decided + Phase 4 hand-off]

## Integration validation outcomes

[Did the cross-batch tests pass first time? Any flakiness? Any surprising score values?]

## Carry-forward to Phase 4

[Items 3D surfaced that Phase 4 must address]
```

`REPORT_PHASE_3.md` shape (~250 lines):

```markdown
# Phase 3 — Aggregate Report

**Phase**: 3 of N (Week 3)
**Batches**: 3A, 3B, 3C, 3D
**Commits**: ~22-24 across all batches (final count TBD post-execution)
**Date range**: 2026-05-27 to 2026-05-XX
**Status**: COMPLETE

## Phase 3 invariants achieved

- Modification endpoint live: `POST /api/v1/shipments/modification/evaluate` (ALLOW/REVIEW/BLOCK)
- Feedback endpoint live: `POST /api/v1/shipments/feedback` with two-tier idempotency
- 12 new rules added (8 modification + 4 previously-rejected); total 79 rules
- 10 new Context fields added (6 modification + 4 previously-rejected); total 66 ALLOWED_CONTEXT_FIELDS
- 4 migrations total (0001, 0002, 0003 [request_type], 0004 [feedback shape])
- RLS audit doc published; structural readiness for Phase 5 role transition confirmed
- Currency-implicit-USD assumption documented; per-currency normalization deferred to Phase 4
- ~150 new tests across 4 batches; total ~583

## Aggregate stats

| Metric | Pre-Phase-3 | Post-Phase-3 |
|---|---|---|
| Rule count | 67 | 79 (+12) |
| Test count | 432 | ~565 (+133) |
| ALLOWED_CONTEXT_FIELDS | 56 | 66 (+10) |
| Migrations | 2 | 4 (+2) |
| New endpoints | 2 (booking, health) | 4 (+ modification, feedback) |
| `.ai/decisions.md` new sections | — | +3 (modification design, feedback design, currency) |

## Per-batch summary

### Batch 3A — Modification endpoint stack (8 commits)
[Brief disposition; commit hashes; reviewer verdicts summary; any plan deviations]

### Batch 3B — Feedback endpoint stack (7 commits)
[Brief disposition; commit hashes; reviewer verdicts summary; any plan deviations; note the schema migration adaption from operator's pre-batch choice]

### Batch 3C — Multi-tenant scoping audit (3 commits)
[Brief disposition; commit hashes; reviewer verdicts summary; audit findings = zero gaps]

### Batch 3D — Integration validation + wrap (4 commits)
[Brief disposition; commit hashes; reviewer verdicts summary]

## Plan deviations across Phase 3

[Aggregate list with file:line refs; cross-reference STATUS.md entries]

## Reviewer-caught corrections

[File:line refs for every correction during Phase 3]

## Tangential issues logged to BUGS.md

[Snapshot of new BUGS.md entries opened during Phase 3, with disposition]

## Explicitly deferred items

| Item | Original scope | Deferred to | Reason |
|---|---|---|---|
| Per-currency `value_caps` normalization | Phase 3 (recommended documentation-only) | Phase 4 | Operator-confirmed deferral; Phase 4 introduces `TenantConfig` |
| Extended modification decision states (REVERT, CANCEL) | v1+ | v2+ | Phase 3 scope decision |
| Modification of modifications | v1+ | (out) | Explicit Phase 3 scope exclusion |
| `riskd_app_login` runtime RLS activation | Phase 3 (structural confirmation only) | Phase 5 | Pre-existing STATUS row 1B.2 |

## Phase 4 inheritance

Phase 4 (Week 4) starts with:

1. **`TenantConfig` Pydantic model** + tenant onboarding script — first deliverable
2. **Cold-start window enforcement** — Phase 4 scope
3. **Two read-only admin endpoints** — Phase 4 scope
4. **Currency normalization implementation** — Phase 4 wires `value_caps` into the 7 absolute-value rules (and any others added 3D+); rewrites conditions to consult `tenant.value_caps.get(currency, default)`
5. **Modification + feedback endpoints remain unchanged** — Phase 4 layers config on top
6. **Audit doc inheritance**: Phase 4 admin endpoints MUST be added to `docs/security-audit-rls-phase-3.md` (or a new Phase-4 successor doc). Triage gate requires standard panel + db-reviewer for admin endpoints.

## Performance note

Modification endpoint's `build_modification_context` runs **11 sequential awaits** on the txn connection (9 from base `build_context` + 2 modification-velocity SQL queries). Phase 5 latency-budget review may require splitting into parallel pool connections — out of scope for Phase 3 per watch-points.

Feedback endpoint's transaction holds `SELECT FOR UPDATE` on `customer_baselines` and writes baseline + customers + feedback in one shot. Concurrent booking + feedback on the same customer serialize correctly per 3B.7 test.

## Recommended Phase 4 pre-flight

Before Phase 4 starts, operator should:
- Drain `.claude/BUGS.md` from Phase 3 (triage per CLAUDE.md "phase boundaries")
- Confirm `REPORT_PHASE_3.md` matches the operator's understanding of what landed
- Approve Phase 4 scope (which will reference this report)
```

**Validation**:
- `markdownlint REPORT_PHASE_3D.md REPORT_PHASE_3.md` (visual if linter unavailable)
- Doc-reviewer confirms both reports cover all required sections.
- All numeric counts in the reports cross-checked against actual repo state at HEAD: rule count via `grep -c "^  - name:" app/rules.yaml`, test count via `pytest --collect-only -q | tail -1`, migration count via `ls alembic/versions/ | grep -c .py$`, ALLOWED_CONTEXT_FIELDS count via grep at `app/rules.py`.

**Risk**: **Low**. Doc-only. Risk is numeric mismatch — cross-check before commit.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green (no Python touched).

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: doc-only — doc-reviewer.

---

## Batch 3D summary table

| Commit | Theme | Files | Tests added | Risk | Reviewer panel |
|---|---|---|---|---|---|
| 3D.1 | Currency-implicit-USD decision | `.ai/decisions.md` | 0 | Low | Standard + doc-reviewer |
| 3D.2 | Cross-batch chain integration test | 1 new test file | 6 | Medium | test-reviewer + senior + code-flow |
| 3D.3 | Maturity + modification composition test | 1 new test file | 6 | Medium | test-reviewer + senior + code-flow |
| 3D.4 | Phase 3 wrap reports | `REPORT_PHASE_3D.md`, `REPORT_PHASE_3.md` | 0 | Low | doc-reviewer |
| **Total** | | | **12 new tests** | | |

Expected test count at end of Batch 3D: **571 + 12 = 583 tests**.

Rule count at end of Batch 3D: **79 rules** (unchanged from 3B).

ALLOWED_CONTEXT_FIELDS count at end of Batch 3D: **66 fields** (unchanged from 3B).

Migrations count at end of Batch 3D: **4** (unchanged from 3B).

---

## Phase 3 aggregate (predicted post-execution)

| Metric | Pre-Phase-3 | Post-Phase-3 (predicted) | Delta |
|---|---|---|---|
| Rule count | 67 | 79 | +12 |
| Test count | 432 | ~583 | +133 |
| ALLOWED_CONTEXT_FIELDS | 56 | 66 | +10 |
| Migrations | 2 | 4 | +2 |
| New endpoints | 2 | 4 | +2 (modification, feedback) |
| `.ai/decisions.md` sections | N | N+3 | modification + feedback + currency |
| Commits | 432 baseline | +22 (8+7+3+4) | 22 Phase-3 commits |
