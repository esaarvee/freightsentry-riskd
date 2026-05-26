# REPORT_PHASE_2C.md

Batch 2C execution disposition. Operator checkpoint per `PLAN_PHASE_2C.md`.
Waiting on operator approval before Batch 2D scope is opened.

---

## Aggregate stats

| Metric | Value |
|---|---|
| Commits in Batch 2C | 8 (`2f1f2d9` 2C.1 → `3e87158` 2C.7, plus `90b21fd` conftest extraction) |
| Production source files touched | 1 (`app/rules.yaml` only — no Python under `app/` modified) |
| Tests passing | 419 / 419 |
| New tests in 2C | 70 (10 + 6 + 7 + 8 + 12 + 20 + 7 + 8 in the seven per-batch test files; the extraction commit refactored 2C.1 to use shared helpers, no net test change) |
| Plan-expected test count | 386 — exceeded by 33 (test additions reflect plan-spec gaps closed during execution and reviewer-driven boundary tightening) |
| Validation tooling | `ruff check` clean · `mypy --strict` clean · `pytest --asyncio-mode=auto` 419/419 |
| Net diff vs pre-2C | +1846 / −9 across 12 files |
| Migrations added | 0 (rules-only batch) |
| Rules.yaml shape | 14 (Phase 1) → 67 (end-of-2C); **+53 net additions** |
| DSL whitelist | unchanged (56 fields — Phase 2B's whitelist already covers every condition added in 2C) |

---

## Per-commit disposition

| Commit | Theme | Rules added | Tests added | Reviewer panel | Outcome |
|---|---|---|---|---|---|
| `2f1f2d9` | 2C.1 — trust-conditioned | 7 | 10 | full | SHIP IT / LOW RISK / CLEAN / ACTUALLY GOOD (boundary tightening applied pre-commit) |
| `90b21fd` | conftest extraction (proactive D3 prevention before 2C.2 lands) | 0 | 0 (refactor) | none — triage-gate trivial | committed without panel; rationale in commit body |
| `042f820` | 2C.2 — dormancy + customer lock-in | 5 | 6 | full | SHIP IT / LOW RISK / CLEAN / ACTUALLY GOOD |
| `0c7dcbb` | 2C.3 — residential ASN + IP-class diversity | 6 | 7 | full | SHIP IT / LOW RISK / CLEAN / ACCEPTABLE (with substantive feedback applied — see "Reviewer-caught corrections") |
| `11b5011` | 2C.4 — recipient overlap (SECURITY-PRIORITY) | 2 | 8 | full + security-priority | SHIP IT / LOW RISK / CLEAN / ACTUALLY GOOD |
| `e3fb114` | 2C.5 — velocity + identity-novelty | 11 | 12 | full | SHIP IT / LOW RISK / CLEAN / ACTUALLY GOOD |
| `a894f29` | 2C.6 — value + geographic + threat composites (after triage) | 17 (plan summary said 13 — arithmetic error in the plan) | 20 | full | SHIP IT / LOW RISK / CLEAN / ACTUALLY GOOD (cosmetic fixes applied pre-commit; plan arithmetic discrepancy logged to BUGS.md) |
| `3e87158` | 2C.7 — IP-familiarity tier + closing pieces | 5 | 8 (incl. total-count + duplicate-name audits) | full | SHIP IT / LOW RISK / CLEAN / ACTUALLY GOOD |

---

## Rule-count ledger

| Stage | Adds | Cumulative |
|---|---:|---:|
| Phase 1 baseline | 14 | 14 |
| 2C.1 trust-conditioned | +7 | 21 |
| 2C.2 dormancy + customer lock-in | +5 | 26 |
| 2C.3 residential ASN + IP-class diversity | +6 | 32 |
| 2C.4 recipient overlap | +2 | 34 |
| 2C.5 velocity + identity-novelty | +11 | 45 |
| 2C.6 value-anomaly + geographic + threat composites | +17 | 62 |
| 2C.7 familiarity-tier + closing pieces | +5 | **67** |

Canonical total-count assertion lives in `tests/unit/test_rules_familiarity_and_diversity.py::test_phase2_end_total_rule_count`. A companion `test_phase2_end_no_duplicate_rule_names` audits that all 67 names are unique.

---

## Plan deviations

| Commit | Plan said | Actual | Why |
|---|---|---|---|
| 2C.1 | 10 unit tests | 10 (then 9 — count assertion removed in 2C.2 cycle 2) | Per-commit `len(ruleset.rules) == N` assertions force churn on every subsequent commit; per-batch set-membership preserved, canonical total-count moved to 2C.7. |
| 2C.6 | "13 rules" (plan body) / "(15 rules)" (header) / 19 in table | 17 after triage of 2 | Plan summary text had an arithmetic error. The section table enumerated 19 rules (6 value + 5 geo + 8 threat-intel), 2 are explicitly triaged (`threat_intel_level1`, `outside_allowed_country`), so 17 lands. Logged to `.claude/BUGS.md`. |
| 2C.7 | "13 rules" / final state estimate `~63` | 67 final | +4 from the 2C.6 arithmetic correction. Documented in the test docstring. |
| 2C.3 integration follow-up | not in plan | dropped `decision == "ALLOW"` assertions in test_booking_e2e (2 tests) | Phase 2C.3 rules legitimately trip the `booking_minimal` fixture (channel=api + non-cloud IP + brand-new user). Test-reviewer cycle 1 caught my initial `decision in ("ALLOW", "REVIEW")` relaxation as a false-pass shape; final fix drops the decision assertion entirely since these tests verify persistence rows, not decision outcomes. |
| 2C.3 test infra | not in plan | aligned `_seed_baseline` decay_anchor_date with Python's `date.today()` | PG `current_date` (UTC) drifted 1 day from Python `date.today()` (local TZ ahead of UTC), silently triggering 1-day decay in build_context and flipping the strict-boundary value_n=5.0 case. Same time-bomb class as 2B.6's hardcoded booking_ts. |

---

## Reviewer-caught corrections

Material findings turned into code changes within the batch:

- **2C.1 (test reviewer cycle 1)** — `low_trust_high_value` + `flags_with_value` tested strict-inequality on only one of two clauses. Tightened to test both clauses' boundaries.
- **2C.3 (test reviewer cycle 1)** — substantive: `decision in ("ALLOW", "REVIEW")` integration-test relaxation was a false-pass shape (passes both when rules fire AND when they regress to not-firing). Dropped the decision assertion entirely; the tests verify persistence rows / value=0 acceptance.
- **2C.3 (implicit cross-TZ bug)** — `_seed_baseline` PG `current_date` vs Python `date.today()` drift broke `test_is_new_user_strict_boundary_via_build_context` on 2026-05-27. Switched the seed to use `date.today()` from Python so both sides of the date comparison share a source.
- **2C.6 (senior eng + code flow)** — section-comment off-by-one ("7 rules" → "6 rules"); rename-rationale comment for `ip2p_threat_scanner_signal` / `ip2p_threat_spam_signal` (`_signal` suffix avoids collision with the homonymous Context boolean fields).
- **conftest extraction (proactive)** — code-flow reviewer suggested at 2C.1 review that `_base_ctx` / `_find` should move to `tests/unit/conftest.py` before 2C.2 copies them. Extracted in a small commit (`90b21fd`) between 2C.1 and 2C.2 — D3 prevention before duplicate accumulated.

No reviewer-flagged finding required a follow-up commit (all addressed pre-commit or within the same commit's review cycle).

---

## Triage decisions

Rules deferred at the commit level (each documented in YAML comments + test audits that catch silent re-introduction):

**2C.6 triage:**
- `threat_intel_level1` (freight_risk 29) — REJECTED. Phase 1's `blacklisted_ip` BLOCK rule already covers `ip_in_level1`; Layer 1 short-circuits Layer 3, so a duplicate would never fire.
- `outside_allowed_country` (freight_risk 181) — DEFERRED to Phase 4. The condition needs `ip_outside_allowed_country` which depends on tenant-level country-allowlist config (Phase 4 scope).

**2C.7 triage:**
- `unknown_email_for_customer` / `unknown_phone_for_customer` (freight_risk) — DEFERRED to Phase 3+. Requires `is_new_email` / `is_new_phone` fields not in the 2B whitelist; adapting via proxy conditions would be weight-calibration guesswork (forbidden by the bootstrap rule).
- `user_ip_rotation_elevated` / `user_ip_rotation_high` (freight_risk 216/221) — DEFERRED. Semantic mismatch between `customer_distinct_ips_30d` (all IPs, 30d window) and freight_risk's source field `user_unique_non_cloud_ips_daily` (non-cloud-only, daily). Not safe to paper over with a structural rename.

Both 2C.6 and 2C.7 triage decisions are pinned by `test_triaged_rules_not_present` / test docstrings — they cannot silently sneak back into the catalog.

---

## Maturity-sensitive coverage

Rules with `maturity_sensitive: true` (Layer 3 downweight applied to cold-start customers per the 2A.3 wiring):

| Family | Count | Examples |
|---|---:|---|
| Phase 1 baseline (carried forward) | 5 | customer_daily_volume_spike, ip_velocity_high_ui/api, unknown_origin/dest_address, ip_fully_new, unfamiliar_ip_country_for_origin |
| 2C.1 trust-conditioned | 4 | very_low_trust, low_trust_high_value, low_trust_vpn, very_low_trust_velocity |
| 2C.2 dormancy + lock-in | 2 | dormant_new_ip, ip_distance_dormant |
| 2C.3 residential / IP-class | 1 | residential_asn_high_velocity |
| 2C.4 recipient overlap | 1 | recipient_used_by_many_customers (lower tier — high tier is fraud-strength evidence regardless of maturity) |
| 2C.5 velocity additional | 2 | velocity_spike_hourly_ui, velocity_spike_hourly_api |
| 2C.6 value + geo + threat | 2 | extreme_value, above_normal_value |
| 2C.7 familiarity tier + closing | 4 | ip_family_familiar_cloud/residential, ip_new_known_asn_rule, value_novelty_compound |
| **Total maturity_sensitive after 2C** | **21** | |

The downweight (per 2A.3) is `effective_weight = weight × (1 - 0.30 × (1 - maturity))`, so cold-start customers see these rules contributing at 70% of their nominal weight, easing the false-positive rate for legitimate new accounts.

---

## Tier-disjoint rule pairs

Two rule pairs are intentionally tier-disjoint, with the lower-weight rule carrying an upper bound to prevent simultaneous firing (avoiding noisy-OR double-counting). Each pair has a dedicated sweep test:

| Family | Lower-tier rule | Upper-tier rule | Disjointness test |
|---|---|---|---|
| Recipient overlap | `recipient_used_by_many_customers` (3 < count ≤ 10) | `recipient_used_by_very_many_customers` (count > 10) | `test_recipient_overlap_rules_are_tier_disjoint` (sweep [0,20]) |
| Value-anomaly | `above_normal_value` (2.0 < z ≤ 3.0) | `extreme_value` (z > 3.0) | `test_value_zscore_rules_are_tier_disjoint` (sweep [0.0, 4.9]) |

---

## Quality measurements

- **DSL evaluator security boundary**: untouched. The whitelist (`ALLOWED_CONTEXT_FIELDS`) and the AST evaluator (`app/dsl.py`) are unchanged. All 53 new rules use only post-2B-whitelist fields.
- **Per-rule boundary discipline**: every strict-inequality threshold (`> 3.0`, `< 5.0`, `> 1.5`, `> 500`, `> 15`, `> 1000`, `> 5000`, `> 2000`, `> 10000`, `> 60`, `> 50`, `> 300`, `> 3`, etc.) is exercised at the boundary value (must-not-fire side) AND boundary+ε (must-fire side). A `>` → `>=` weakening anywhere in `app/rules.yaml` would fail at least one boundary test.
- **Compound-AND coverage**: every multi-clause AND condition (5-clause `cloud_api_customer_deviation_iptype`, 4-clause `locked_customer_*`, 3-clause `extreme_value` / `velocity_spike_daily_*`, etc.) has a "positive case + each clause individually flipped to non-firing" test using the `fires(**overrides)` helper pattern. Functionally equivalent to a full 2^N truth table for AND-only compositions with NOTs.
- **OR-precedence**: `web_booking_from_cloud_ip`'s `is_platform_booking AND (is_cloud_ip OR is_datacenter_ip)` parenthesized sub-expression has a dedicated 4-cell coverage test ensuring the DSL evaluator correctly handles nested `BoolOp(Or)` inside `BoolOp(And)`.
- **No new `.py` file under `app/`**: per CLAUDE.md never-skip rule (any new `app/` file triggers full review). All edits land in existing modules / new YAML / new test files.
- **Pre-commit hook coverage**: every commit cleared ruff + ruff-format + mypy strict + pytest unit; no `--no-verify` was used.

---

## Open items for Batch 2D (and the next operator action)

1. **Operator approval to open Batch 2D scope.** Per `PLAN_PHASE_2D.md`:
   - Apply tuned thresholds (`allow_max`, `block_min` reconsideration based on the 67-rule contribution profile)
   - Case-1 fixture replay (dashboard ATO ~50 shipments)
   - Case-2 BLOCK assertion (verify the full ruleset reaches BLOCK on the case-2 scenario)
   - End-to-end pipeline regression sweep

2. **Drain `.claude/BUGS.md`** at the 2C → 2D boundary:
   - Single entry: PLAN_PHASE_2C.md 2C.6 rule-count arithmetic error (plan said "13 rules", actual is 17 after triage of 19). Suggested resolution: refresh `PLAN_PHASE_2C.md` line 380 + batch-summary "~63" to reflect actual 67.

3. **Tracked-for-later** (not blocking 2D):
   - Test-reviewer noted 4 minor coverage-asymmetry suggestions in 2C.5 (channel-guard symmetry, customer_observations boundary on `velocity_spike_daily_api`, etc.). Suggestion-tier; can fold in if a future PR touches that file.
   - Senior-engineer noted that `absolute_high_value` (single-condition rule, w=0.20) has the largest false-positive surface in 2C.6 — could fire on legitimate B2B high-ticket freight. Tracked for Phase 6 calibration; do NOT weight-tune in mid-build phases (per memory).
   - 4 freight_risk catalog rules deferred to Phase 3+ (proxy-field adaptation: unknown_email/phone_for_customer; semantic mismatch: user_ip_rotation_*). Each pinned by audit tests so they cannot silently re-appear.

4. **No carry-forward to STATUS.md.** All 8 commits cleared the merge gate within their review cycles; no checkpoints required.

---

End of Batch 2C. Working tree clean. `feat/refactor` branch ready for Batch 2D operator approval.
