# REPORT_PHASE_2D.md

Batch 2D execution disposition. End-of-Phase-2 operator checkpoint.

---

## Aggregate stats

| Metric | Value |
|---|---|
| Commits in Batch 2D | 5 (4 commits + this report = `eb00a5b` 2D.1 → `148788f` 2D.4 + REPORT) |
| Production source files touched | 0 (Batch 2D is test-only — no Python under `app/` modified; no migrations) |
| Tests passing | 432 / 432 |
| New tests in 2D | 13 (4 in 2D.1, 1 in 2D.2, 8 in 2D.3, 0 in 2D.4 — strengthens existing) |
| Plan-expected count after 2D | 399 — exceeded by 33 due to 2D.3's broader Layer-2 matrix and 2C cycle-2 corrections that carried forward |
| Validation tooling | `ruff check` clean · `mypy --strict` clean · `pytest --asyncio-mode=auto` 432/432 |
| Net diff vs pre-2D | +1163 / -54 across 6 files |
| Conftest additions | `seed_customer_with_baseline` + `seeded_ip_enrichment` shared helpers |

---

## Per-commit disposition

| Commit | Theme | Tests added | Reviewer panel | Outcome |
|---|---|---|---|---|
| `eb00a5b` | 2D.1 — Tuned-threshold audit (4 pins) | 4 unit | lightweight (test-only): senior-engineer + test-reviewer | SHIP IT / ACCEPTABLE (boundary parity guard added per test-reviewer) |
| `06595dc` | 2D.2 — Case-1 dashboard ATO fixture + integration test | 1 integration + 2 JSON fixtures + `seed_customer_with_baseline` conftest helper | full panel (test, senior, code-flow) | cycle 1 NEEDS WORK / NEEDS MINOR FIXES / MINOR ISSUES → cycle 2 ACTUALLY GOOD / SHIP IT / CLEAN |
| `a9344e2` | 2D.3 — Layer 2 + maturity integration test matrix | 8 integration + `seeded_ip_enrichment` conftest helper | full panel | cycle 1 MINOR ISSUES + SHIP IT → cycle 2 CLEAN / ACTUALLY GOOD / SHIP IT (D3 extraction + lock-in gate isolation + maturity test renaming + tautological smoke replaced) |
| `148788f` | 2D.4 — Case-2 BLOCK assertion (canonical Phase 2 success criterion) | 0 (strengthens existing) | full panel | SHIP IT / LOW RISK / CLEAN / ACTUALLY GOOD (6-rule compound assertion added per test-reviewer's "BLOCK reached for the right reasons" feedback) |

---

## Plan deviations

Three deviations from `PLAN_PHASE_2D.md`, all non-material to the canonical Phase 2 success criterion:

| Commit | Plan said | Actual | Why |
|---|---|---|---|
| 2D.1 | "Apply any threshold that's still on a Phase 1 default" + 4 unit tests | 4 unit tests, no YAML change | 2C applied every tuned threshold in-place during execution. The audit found zero drift. |
| 2D.2 | 4-IP rotated burst + plan-listed assertions including "ALLOW for shipments 0-3" | 1-IP burst + compound-evidence assertion (not index-ordering) | Cycle-1 test-reviewer caught (a) the 4-IP rotation made `ip_velocity_high_ui` impossible to fire (each IP gets ~8/hour, below the >10 threshold) and (b) the index-ordering escalation check vacuously skipped when shipment 0 was already BLOCK. Cycle-2 switched to single-IP burst (more realistic ATO anyway) and replaced (d) with a compound-evidence check that holds at shipment 0. |
| 2D.3 | 8 tests including `test_recipient_overlap_count_does_not_cross_tenants` + plan-listed maturity-downweight assertion | 8 tests but the recipient-overlap one replaced by `test_customer_locked_cloud_api_flips_at_observation_threshold` (the tautological smoke from cycle 1 was deleted) + maturity test renamed to `..._collapses_account_prior_for_mature_customer` (the original name promised a Layer-3 downweight check the test couldn't deliver because is_new_user differs between brand-new and mature customers). | Recipient overlap is already integration-tested in 2B.6 + Context-tested in 2B.4 — duplicating at Layer-2-integration layer was low-value. |

---

## Reviewer-caught corrections

Material findings turned into code changes within the batch:

**2D.1 (test reviewer cycle 1)**: residential_asn_high_velocity threshold pin was missing the symmetric "did not drift back to 5" guard that the daily-API test had. Added `assert "velocity_ip_hourly > 5 " not in condition` for parity.

**2D.2 (test reviewer cycle 1 — substantive)**: Two findings: (a) the 4-IP rotated burst made `ip_velocity_high_ui` impossible to fire (max ~8 per IP under the >10 threshold) yet the docstring promised it would; (b) assertion (d) "progressive escalation" was vacuous when first_block_idx=0 (which the empirical regime always reaches). Cycle 2: switched fixture to single-IP burst (realistic for ATO), replaced (d) with compound-evidence check that holds at shipment 0.

**2D.2 (senior eng cycle 1)**: ip_enrichment seeded but not cleaned up — global table, not in conftest's `_TENANT_SCOPED_TABLES`. Rows leak across test sessions and pollute any future test that enriches an IP in `185.220.101.0/24`. Cycle 2: try/finally with explicit DELETE.

**2D.2 (code flow cycle 1)**: D3 duplicate helper — `_seed_case_1_customer` was the third customer+baseline seeding helper. Cycle 2: extracted `seed_customer_with_baseline` to `tests/conftest.py` accepting customer kwargs + baseline_kwargs dict.

**2D.3 (code flow cycle 1)**: D3 duplicate again — `_seed_clean_residential_enrichment` + `_cleanup_enrichment` repeated the case-1 pattern at a third site. Cycle 2: extracted `seeded_ip_enrichment` async context-manager to conftest. Resets all boolean flags on UPSERT so a crashed prior test can't leave stale `is_vpn`/`is_tor`/`threat` state.

**2D.3 (code flow cycle 1)**: Lock-in negative test had two non-firing reasons (`value_n=15` AND not-locked) — couldn't isolate the gate. Cycle 2: bumped obs to 25 (above >=20 gate) with mixed channel (api_share=0.52 below 0.95) so only the lock-in flag is False.

**2D.3 (code flow cycle 1)**: Maturity downweight test name overpromised — promised Layer-3 signal_score check but only verified Layer-2 account_prior collapse. Cycle 2: renamed `..._softens_score_for_brand_new` → `..._collapses_account_prior_for_mature_customer` with docstring rewritten to scope to Layer-2.

**2D.3 (code flow cycle 1)**: `test_layer2_integration_module_loads` was tautological padding. Cycle 2: replaced with `test_customer_locked_cloud_api_flips_at_observation_threshold` — exercises the strict-equality boundary at value_n=19 vs value_n=20 through the booking endpoint.

**2D.4 (test reviewer cycle 1 — substantive)**: docstring enumerated 6 expected rules but only 2 were asserted. The remaining 4 (`unfamiliar_ip_country_for_origin`, `locked_customer_unfamiliar_ip`, `api_non_cloud_ip`, `non_cloud_established_account`) were documented but not enforced — a future regression where one of them silently breaks could still pass the BLOCK assertion via the remaining rules. Reviewer's option (a) applied: assertion now uses set-membership over the full 6-rule compound. Catches "BLOCK reached for the right reasons" not just "BLOCK reached".

**2D.4 (senior eng + code flow cycle 1)**: docstring typo "50 API + cloud observations" should be "20 cloud + 20 API". Stale module docstring still framed test as Phase 1. Both fixed.

---

## End-of-Phase-2 system state

- **Scoring**: full 3-layer (Layer 1 BLOCK short-circuit + Layer 2 account_prior + Layer 3 noisy-OR with maturity downweight). Constants in `app/scoring_constants.py`; formula in `app/scoring.py`; CustomerState carries trust + age + shipments + flagged_count typed.
- **Context**: 56 fields in `ALLOWED_CONTEXT_FIELDS` (Phase 1 baseline 45 + 11 Phase 2B). `build_context` produces every field Phase 2C rules consume.
- **Rules**: 67 rules in `app/rules.yaml` (Phase 1 14 + 53 net Phase 2C additions). Total `--asyncio-mode=auto` test count: 432.
- **Migrations**: 2 (`0001_initial.py` from Phase 1 + `0002_shipments_destination_hmac.py` from 2B.6). Schema stable since 2B.6.
- **Observability**: booking endpoint emits `risk.evaluation` structured log with Layer 2 + Layer 3 component fields tagged `metric=True` for Phase 5 CloudWatch sink.
- **Case-2 ATO end-to-end**: API booking from unfamiliar residential IP against cloud-API-locked customer reaches BLOCK with 6 compound rules firing (`ip_fully_new_for_customer`, `unfamiliar_ip_country_for_origin`, `cloud_api_customer_deviation_iptype`, `locked_customer_unfamiliar_ip`, `api_non_cloud_ip`, `non_cloud_established_account`). The canonical Phase 2 success criterion is met.
- **Case-1 dashboard ATO**: synthesized 30-shipment burst from single VPN IP. At least one BLOCK occurs across the burst; ip_fully_new fires from shipment 0; ip_velocity_high_ui fires by shipment 11.

---

## Quality measurements

- **Tuned thresholds pinned**: cadence z>6, velocity_spike_daily_api>50, residential_asn_high_velocity>15, ip_familiarity_tier /24-only. Each catches drift back to the untuned freight_risk source values via source-grep or YAML lookup.
- **Cross-tenant boundary**: recipient_cross_customer_count tested at SQL level (2B.2), helper level (2B.2), Context-wiring level (2B.4), AND rule-firing level (2C.4). 4-layer defense-in-depth.
- **Tier-disjoint rule pairs verified**: `extreme_value` vs `above_normal_value` (z-score sweep [0.0, 4.9]), `recipient_used_by_many_customers` vs `_very_many_customers` (count sweep [0, 20]). Both load-bearing.
- **Pre-commit hook coverage**: every commit cleared ruff + ruff-format + mypy strict + pytest unit. No `--no-verify` used.

---

## Currency-handling carry-forward (Phase 3 planning input)

Phase 2C ships rules with absolute-value thresholds — `absolute_high_value > 10000`, `threat_intel_high_value > 2000`, `flags_with_value > 2000`, `vpn_high_value > 1000`, `high_value_new_user > 5000`, `extreme_value`-related (z-score, indirectly). These thresholds are **implicitly USD**.

Phase 4's `TenantConfig` defines `value_caps: dict[str, float]` per currency. Phase 3 planning should consider:
- Mid-Phase-3: introduce a `value_in_usd` normalization (using `payload.shipment.currency` + a static rates table) so per-tenant `value_caps` and the absolute-value rule thresholds compose correctly.
- Alternative: keep the rules as-is (USD-implicit) and document for operators that non-USD tenants need separate rule configuration.

Not a Phase 2 blocker. Flagged here for Phase 3 visibility (carry-forward from the Phase 2 bootstrap).

---

## Open items for Phase 3 (and the next operator action)

1. **Operator approval to open Phase 3 scope.** Per `MASTER_PLAN.md`:
   - Modification endpoint (reuses existing scoring + Layer 2 + 3 infrastructure)
   - Feedback endpoint (writes `r_n` increments to baseline; uses existing `add_rejected_observation` helper)
   - ~12 deferred rules waiting on feedback + global-blocked-vectors

2. **Drain `.claude/BUGS.md`** at the 2C → 2D boundary:
   - `PLAN_PHASE_2C.md` 2C.6 rule-count arithmetic error (plan said "13 rules", actual is 17 after triage of 19). 2D.5 absorbed the larger consequence (67 total rules, not the plan's ~63 estimate) in the per-batch reports.

3. **Tracked-for-later** (not blocking Phase 3):
   - Phase 2D.3 test reviewer's structlog cross-test aliasing nit (filter by request_id in addition to event name). Low priority — pytest is sequential, no xdist.
   - Phase 2D.4 senior eng's `signal_score >= 0.80` could be tightened to a narrower band (currently >= 0.80; actual is ~0.94). Drift-protection nit; Phase 6 calibration will revisit anyway.
   - `test_case_2._seed_established_customer` could now use the shared `seed_customer_with_baseline` helper but is left as-is to minimize cycle churn. Future refactor scope.

---

End of Batch 2D. End of Phase 2.
