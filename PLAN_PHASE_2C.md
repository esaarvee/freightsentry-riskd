# Phase 2 — Batch 2C Plan — Rule additions

> **Status (2026-05-26)**: Pending operator approval after 2B execution. Re-review this plan against post-2B field availability (DSL whitelist final shape, recipient overlap derivation form) before approving.

Batch 2C grows `app/rules.yaml` from 14 rules to ~78-82 rules by porting:
- ~12 FreightSentry-exclusive rules (trust-conditioned + dormancy + lock-in + residential)
- ~52-56 freight_risk catalog rules (velocity, value, IP-class, geographic, identity-novelty, cadence, recipient-overlap)

Total Phase 2C target: ~64-68 net-new rules. End-of-2C `app/rules.yaml` total: **~78-82 rules**. (Below the bootstrap's ~95-100 target because we explicitly defer rules referencing fields not in 2B's whitelist — see Decisions absorbed and "Rules deferred" section.)

Target: 7 commits.

---

## Decisions absorbed (batch-relevant subset)

| Decision | Value | Source |
|---|---|---|
| Rule sourcing | Conditions + weights from freight_risk's catalog (84 rules) + FreightSentry's ~13 port rules. Where both catalogs have the same rule, use freight_risk's tuned weight (verification §2.2). | Phase 2 bootstrap |
| Recipient-overlap origin | freight_risk's catalog (NOT FreightSentry port) | Verification §1.2 |
| Tuned thresholds | `velocity_spike_daily_api = 50`, `residential_asn_high_velocity = 15`. `cadence_anomaly z > 6` is read from `is_abnormally_dormant` Context field (which Phase 1 already derives at z > 6 per `app/context.py:165`). | Verification §2.2 — applied in 2D for boundary verification |
| Field references | Every new rule's condition uses only fields in `ALLOWED_CONTEXT_FIELDS` post-2B (56 fields). Rules referencing deferred fields are documented in "Rules deferred" and NOT added. | Phase 2 bootstrap |
| `maturity_sensitive` flag | Applied per freight_risk's source-of-truth. Maturity-sensitive rules downweight at brand-new customers (per 2A.3 wiring). | Phase 2 bootstrap |
| Weight calibration | Use freight_risk-source weights. Adjustments DURING Phase 2 are out of scope; Phase 6 staging replay measures actual recall + FPR and tunes per tenant. | Phase 2 bootstrap "Watch points" |
| Rule-loader validation | The Phase 1 rule loader (`app/rules.py::load_rules`) validates every condition at lifespan startup: every Name token must be in `ALLOWED_CONTEXT_FIELDS`. New rules fail loud at startup if a typo or unknown field slips in. | Phase 1 existing |
| Per-rule observability | The `risk.evaluation` log from 2A.5 already lists `triggered_rules`; no per-rule observability code needed for Phase 2C. | 2A.5 |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md. **Reviewer-panel quota is available for the full Phase 2 batch sequence** — the per-commit full panel runs on every code-path commit at commit time. No retrospective-panel fallback is planned; if a reviewer is unavailable, the commit blocks until the panel completes.
- Reviewer routing per CLAUDE.md triage gate:
  - Each rule-addition commit touches only `app/rules.yaml` + tests. Per CLAUDE.md "Never Skip" — "Any change to `app/rules.yaml` weights, thresholds, or conditions that adds or removes a rule" → standard path full panel.
  - test-reviewer runs on each commit (new tests for every rule).
  - **Panel density expectation**: 2C runs the full panel 7 times in one batch (one per commit). This is the densest reviewer-load batch in Phase 2; the granularity (one rule family per commit) is intentional for reviewer focus.
- Reviewer-invocation slice template:
  > `Plan file: PLAN_PHASE_2C.md, current commit: 2C.N (<title>), upcoming commits: 2C.{N+1} through 2C.7 sections. Read only those sections.`

---

## Cross-batch dependencies

- **Consumes from 2A**: Layer 2 + maturity-downweight wiring is active; `maturity_sensitive: true` rules now downweight for cold-start customers.
- **Consumes from 2B**: 11 new Context fields + DSL whitelist additions. Every rule below references only fields in the post-2B 56-field whitelist.
- **Consumed by 2D**: Tuned thresholds are applied to existing rules; case-1 fixture exercises the full ruleset; case-2 BLOCK assertion lands after the full ruleset is loaded.

---

## Rules deferred from Phase 2 (documented here so 2C reviewers don't flag as gaps)

These freight_risk + FreightSentry rules will NOT be ported in Phase 2C. Each has a documented reason. Reviewer-panel reviewers can confirm rules below are deferred-by-design rather than missed.

**Deferred to Phase 3 (feedback endpoint required)**:
- `email_previously_rejected_for_customer` (freight_risk 485)
- `phone_previously_rejected_for_customer` (freight_risk 490)
- `origin_previously_rejected_for_customer` (freight_risk 475)
- `ip_previously_rejected_for_customer` (freight_risk 480)

**Deferred to Phase 6+ (global blocked vectors stub)**:
- `blocked_user` / `confirmed_fraud_block` (FreightSentry) — fraud_confirmed_count check; Phase 2 has the column but no rule currently fires on it
- `globally_blocked_ip` / `globally_blocked_device` / `globally_blocked_email` / `globally_blocked_phone` (FreightSentry) — depend on `global_blocked_vectors` table consumers (capability stubbed, sharing disabled in v1 per `.ai/decisions.md`)
- `ip_globally_rejected` (freight_risk 496)
- `recipient_globally_rejected` (freight_risk 534)

**Deferred due to missing-derivation gap (would require additional fields beyond 2B scope)**:
- `low_trust_new_route` (FreightSentry) — references `customer_dest_diversity`
- `mid_trust_new_route_value` (FreightSentry) — references `customer_dest_diversity`
- `customer_novelty_compound` / `customer_novelty_compound_strong` (freight_risk 359, 364) — reference `customer_novelty_signals`
- `value_novelty_compound` (freight_risk 511) — references combined novelty signals (we could port this by re-expressing — see 2C.6 note)
- `out_of_pattern_hour` / `out_of_pattern_weekday` (freight_risk 449, 454) — reference rarity p-values
- `unusual_channel` (freight_risk 459) — references `channel_share_p`
- `cadence_anomaly` (freight_risk 464) — references `cadence_gap_zscore` (we have `cadence_zscore_hours` from Phase 1; could rename for compatibility — see 2C.7 note)
- `booking_burst` (freight_risk 546) — references `cadence_gap_zscore`
- `customer_daily_volume_extreme` (freight_risk 120) — references `daily_volume_zscore`
- `unusual_ip_country_for_origin` (freight_risk 552) — references `origin_ip_country_rarity_p` and `origin_rarity_p`
- `origin_not_matching_registered` (freight_risk 321) — references `origin_mismatches_registered`

**Deferred — out of scope per `.ai/decisions.md`**:
- All device-fingerprint rules (`device_blacklisted`, `is_known_device`, etc.)
- All user-agent rules (`is_bot`, `ua_*`, etc.)
- `email_matches_customer_name` rules (constraint #14)

**Total rules deferred from Phase 2**: ~20 rules. Combined with the 64-68 ported, the rule count lands at 78-82 — below the ~95-100 bootstrap target but the deferrals are principled (data-availability and feedback-loop gaps). Phase 3-6 absorbs the deferred rules as their dependencies land.

---

## 2C.1 — Trust-conditioned rules

**Theme**: Add the trust-score-conditioned rules from FreightSentry's catalog. These are the rules whose firing depends on `trust_score` — which Layer 2 in 2A.3 made meaningful (low trust now contributes to `account_prior` AND drives these rules to fire).

**Files**:
- `app/rules.yaml` (EDIT — add 7 rules)
- `tests/unit/test_rules_trust_conditioned.py` (NEW)

**Rules added** (7):

| Name | Condition | Weight | maturity_sensitive | Source |
|---|---|---|---|---|
| `very_low_trust` | `trust_score < 0.2` | 0.15 | true | FreightSentry 301-306 |
| `low_trust_high_value` | `trust_score < 0.3 AND shipment_value > 1000` | 0.20 | true | FreightSentry 308-313 |
| `low_trust_vpn` | `trust_score < 0.3 AND is_vpn` | 0.20 | true | FreightSentry 293-299 |
| `very_low_trust_velocity` | `trust_score < 0.2 AND velocity_user_hourly > 3` | 0.35 | true | FreightSentry 339-345 |
| `threat_score_moderate` | `ip_threat_score > 0.5` | 0.25 | false | FreightSentry 672-677 |
| `flags_with_value` | `flagged_count > 3 AND shipment_value > 2000` | 0.40 | false | FreightSentry 684-688 |
| `vpn_known_user` | `is_vpn AND NOT is_new_user` | 0.25 | false | freight_risk 86 |

`tests/unit/test_rules_trust_conditioned.py` — one boundary test per rule, using `app/rules.py::load_rules` against the full rules.yaml:
- `test_very_low_trust_fires_below_threshold`: `trust_score=0.15` → fires.
- `test_very_low_trust_does_not_fire_at_threshold`: `trust_score=0.2` → does not fire (strict `<`).
- `test_low_trust_high_value_requires_both`: `trust_score=0.25, shipment_value=500` → no; `trust_score=0.25, shipment_value=1001` → fires.
- `test_low_trust_vpn_compound`: matrix on (trust_score, is_vpn).
- `test_very_low_trust_velocity_compound`: `trust_score=0.1, velocity_user_hourly=4` → fires.
- `test_threat_score_moderate_above_50pct`: `ip_threat_score=0.5` no, `0.51` yes.
- `test_flags_with_value`: `flagged_count=4, shipment_value=2001` → fires.
- `test_vpn_known_user_excludes_new`: `is_vpn=True, is_new_user=False` → fires; `is_vpn=True, is_new_user=True` → no.
- `test_all_trust_conditioned_rules_parse`: rule-loader runs without DSL errors against the new YAML.
- `test_rule_count_increased_by_seven`: assert `len(ruleset.rules) == 14 + 7 == 21`.

**Validation**:
- `pytest tests/unit/test_rules_trust_conditioned.py -v` — all 10 tests pass
- `pytest tests/ -q --asyncio-mode=auto` — full suite green
- `python -c "from app.rules import load_rules; from pathlib import Path; load_rules(Path('app/rules.yaml'))"` — no startup errors
- `ruff check tests/unit/test_rules_trust_conditioned.py` clean

**Risk**: **Medium**. Rule conditions are short — typo risk on the threshold value or comparison direction is the main concern. Mitigation: each rule has at least one boundary test; the rule-loader validates every Name resolves to a known field at startup.

**Reversibility**: Easy — remove the YAML entries.

**Pre-commit verification**: All gates green.

**Observability**: New rules show up in `risk.evaluation`'s `triggered_rules` list when fired. No new emission needed.

**Test changes**: 10 unit tests added.

**Rollback plan**: `git revert <hash>`.

**Declared breaks**: None.

**Reviewer routing**: Standard path full panel + test-reviewer.

---

## 2C.2 — Dormancy + customer lock-in rules

**Theme**: Account-takeover-pattern rules. Dormancy rules (sudden activity after silence) and customer-lock-in rules (a customer with a strong API+cloud baseline suddenly comes in from a different infrastructure). These are the case-1 (dashboard ATO) + case-2 (API ATO) primary detectors.

**Files**:
- `app/rules.yaml` (EDIT — add 5 rules)
- `tests/unit/test_rules_dormancy_lockin.py` (NEW)

**Rules added** (5):

| Name | Condition | Weight | maturity_sensitive | Source |
|---|---|---|---|---|
| `dormant_vpn` | `is_abnormally_dormant AND is_vpn` | 0.55 | false | FreightSentry 428-433 |
| `dormant_new_ip` | `is_abnormally_dormant AND ip_fully_new` | 0.35 | true | FreightSentry 435-441 |
| `ip_distance_dormant` | `ip_distance_km > 1000 AND is_abnormally_dormant` | 0.40 | true | FreightSentry 418-424 |
| `cloud_api_customer_deviation_iptype` | `customer_locked_cloud_api AND is_api_booking AND NOT is_cloud_ip AND NOT is_datacenter_ip AND customer_observations >= 20` | 0.55 | false | freight_risk 402 + FreightSentry 773-778 (same rule, freight_risk weight authoritative) |
| `locked_customer_unfamiliar_ip` | `customer_locked_cloud_api AND is_api_booking AND ip_fully_new AND customer_observations >= 20` | 0.45 | false | freight_risk 430 + FreightSentry 780-785 |

`tests/unit/test_rules_dormancy_lockin.py` — boundary tests:
- `test_dormant_vpn_requires_both`: 3 combinations (dormant only, vpn only, both).
- `test_dormant_new_ip_compound`: same.
- `test_ip_distance_dormant_kilometre_threshold`: 999 → no, 1001 → yes (with dormancy true).
- `test_cloud_api_deviation_full_conditions`: full 5-clause boolean truth table on `(customer_locked_cloud_api, is_api_booking, is_cloud_ip, is_datacenter_ip, customer_observations)`.
- `test_locked_customer_unfamiliar_ip_compound`: same shape, simpler clauses.
- `test_dormancy_lockin_rule_count`: assert YAML adds 5 rules; total = 21 + 5 = 26.

**Validation**: Same as 2C.1. Total expected test count after this commit: 328 (post-2B) + 10 (2C.1) + 6 (2C.2) = 344.

**Risk**: **High**. These are the case-1 + case-2 primary detectors. A typo in `cloud_api_customer_deviation_iptype`'s condition is the kind of bug that produces silent miscalibration of the highest-stakes rule in the catalog. Mitigation: full truth table in tests; reviewer panel emphasizes the boolean composition.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 6 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Standard full panel + test-reviewer. NEVER-SKIP per CLAUDE.md (any add of a rule).

---

## 2C.3 — Residential ASN + IP-class diversity rules

**Theme**: Rules around residential-ASN abuse (proxy farms, distributed bookings) and IP-class diversity within a customer (one customer rotating across many non-cloud IPs).

**Files**:
- `app/rules.yaml` (EDIT — add 6 rules)
- `tests/unit/test_rules_ip_class.py` (NEW)

**Rules added** (6):

| Name | Condition | Weight | maturity_sensitive | Source |
|---|---|---|---|---|
| `residential_asn_high_velocity` | `is_residential_asn AND velocity_ip_hourly > 15` | 0.40 | true | FreightSentry 544-550 (tuned `velocity_ip_hourly > 15` per verification §2.2; original was 5) |
| `api_non_cloud_ip` | `is_api_booking AND NOT is_cloud_ip AND NOT is_datacenter_ip` | 0.40 | false | freight_risk 187 |
| `new_user_api_non_cloud` | `is_new_user AND is_api_booking AND NOT is_cloud_ip AND NOT is_datacenter_ip` | 0.40 | false | freight_risk 195 |
| `non_cloud_established_account` | `NOT is_cloud_ip AND NOT is_datacenter_ip AND NOT is_new_user AND is_api_booking` | 0.20 | false | freight_risk 204 |
| `web_booking_from_cloud_ip` | `is_platform_booking AND (is_cloud_ip OR is_datacenter_ip)` | 0.45 | false | freight_risk 414 |
| `web_only_customer_using_api` | `customer_locked_web_only AND is_api_booking AND customer_observations >= 20` | 0.50 | false | freight_risk 419 |

**Important**: `web_booking_from_cloud_ip` requires the DSL evaluator to handle `OR` correctly in a parenthesized sub-expression. Phase 1's DSL evaluator supports this (verified in `app/dsl.py`); the test below explicitly exercises the precedence.

`tests/unit/test_rules_ip_class.py`:
- `test_residential_asn_high_velocity_threshold_15`: matrix on (`is_residential_asn`, `velocity_ip_hourly`) at 15 / 16.
- `test_api_non_cloud_ip`: 3-clause boolean truth table.
- `test_new_user_api_non_cloud_compound`: requires is_new_user too.
- `test_non_cloud_established_account_excludes_new_users`: 4-clause truth table.
- `test_web_booking_from_cloud_ip_OR_precedence`: `is_platform_booking=True, is_cloud_ip=False, is_datacenter_ip=True` → fires (the OR sub-expression); the parentheses test.
- `test_web_only_customer_using_api`: requires customer_locked_web_only AND api booking AND ≥20 observations.

**Validation**: As 2C.1. Total tests after: 344 + 6 = 350.

**Risk**: **Medium**. `web_booking_from_cloud_ip` is the rule whose condition has an `OR` inside parentheses; need to confirm the DSL parses it (it does — `app/dsl.py::parse_condition` handles arbitrary boolean trees within the whitelist).

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 6 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Standard full panel + test-reviewer.

---

## 2C.4 — Recipient-overlap rules

**Theme**: Cross-customer destination-hmac overlap detection. These rules use `recipient_cross_customer_count` from 2B's tenant-scoped SQL.

**Files**:
- `app/rules.yaml` (EDIT — add 2 rules)
- `tests/unit/test_rules_recipient_overlap.py` (NEW)

**Rules added** (2):

| Name | Condition | Weight | maturity_sensitive | Source |
|---|---|---|---|---|
| `recipient_used_by_many_customers` | `recipient_cross_customer_count > 3 AND recipient_cross_customer_count <= 10 AND customer_observations >= 10` | 0.40 | true | freight_risk 524 |
| `recipient_used_by_very_many_customers` | `recipient_cross_customer_count > 10` | 0.60 | false | freight_risk 529 |

**Important**: the upper bound `<= 10` on `recipient_used_by_many_customers` is intentional — the higher-stakes `recipient_used_by_very_many_customers` covers counts > 10. Without the upper bound the lower-weight rule would also fire on > 10, and noisy-OR composition would slightly raise the combined contribution (the rules are not designed to compose this way; reading freight_risk's catalogue confirms they're tier-disjoint).

`tests/unit/test_rules_recipient_overlap.py`:
- `test_many_customers_within_3_10_range_AND_observations`: count=5, observations=10 → fires.
- `test_many_customers_below_lower_bound`: count=3 → no (strict `>`).
- `test_many_customers_above_upper_bound`: count=11 → no (`<= 10` excludes; the very-many rule covers > 10).
- `test_very_many_customers_above_10`: count=11 → fires.
- `test_very_many_customers_at_10`: count=10 → no (strict `>`).
- `test_recipient_overlap_requires_observations`: count=5, observations=9 → no (the lower-weight rule requires observations).

**Validation**: As 2C.1. Total tests after: 350 + 6 = 356.

**Risk**: **High**. This is the cross-customer query. The Phase 2B integration test already asserts tenant scoping at the SQL level; this commit adds the rule layer on top. The rule conditions are simple, but the operational outcome (a tenant_b customer's overlap influencing a tenant_a decision) is the worst-case security failure mode if any boundary leaks. Mitigation: 2B.6 + this commit's tests + reviewer attention.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 6 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Standard full panel + test-reviewer + security-auditor priority.

---

## 2C.5 — Velocity (additional) + identity-novelty rules

**Theme**: Velocity rules that complement Phase 1's basic `velocity_user_daily > 20` rule, plus simple identity-novelty rules from freight_risk's catalog. Channel + user-state compound conditions.

**Files**:
- `app/rules.yaml` (EDIT — add 11 rules)
- `tests/unit/test_rules_velocity_novelty.py` (NEW)

**Rules added** (11):

**Velocity (~5)**:
| Name | Condition | Weight | maturity_sensitive | Source |
|---|---|---|---|---|
| `velocity_spike_hourly_ui` | `is_platform_booking AND velocity_user_hourly > 60` | 0.45 | true | freight_risk 102 |
| `velocity_spike_hourly_api` | `is_api_booking AND velocity_user_hourly > 500` | 0.25 | true | freight_risk 107 |
| `velocity_spike_daily_ui` | `is_platform_booking AND velocity_user_daily > 300 AND customer_observations < 30` | 0.35 | false | freight_risk 125 |
| `velocity_spike_daily_api` | `is_api_booking AND velocity_user_daily > 50 AND customer_observations < 30` | 0.25 | false | freight_risk 131 (tuned from 5000) |
| `ip_velocity_threat` | `velocity_ip_daily > 5 AND ip_in_threat_list` | 0.35 | false | freight_risk 150 |
| `user_velocity_vpn` | `velocity_user_daily > 3 AND is_vpn` | 0.30 | false | freight_risk 155 |
| `user_velocity_new_user` | `velocity_user_daily > 3 AND is_new_user` | 0.35 | false | freight_risk 160 |

**Identity-novelty (~4)**:
| Name | Condition | Weight | maturity_sensitive | Source |
|---|---|---|---|---|
| `dummy_email_blocklisted` | `is_email_blocklisted` | 0.55 | false | freight_risk 264 |
| `dummy_email_suspicious_pattern` | `is_email_suspicious_pattern` | 0.30 | false | freight_risk 274 |
| `vpn_new_user` | `is_vpn AND is_new_user` | 0.40 | false | freight_risk 91 |
| `high_value_new_user` | `shipment_value > 5000 AND is_new_user` | 0.35 | false | freight_risk 258 |

`tests/unit/test_rules_velocity_novelty.py` — boundary tests per rule (11 tests).

**Validation**: As 2C.1. Total tests after: 356 + 11 = 367.

**Risk**: **Medium**. `velocity_spike_daily_api > 50` is the tuned value from verification §2.2; if reviewers flag the change-from-Phase-1's previous default (the Phase 1 rules.yaml already has `velocity_user_daily > 20` for `customer_daily_volume_spike` — different rule with different intent), confirm via re-reading verification §2.2.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 11 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Standard full panel + test-reviewer.

---

## 2C.6 — Value-anomaly + geographic + threat-intel composite rules

**Theme**: Value-z-score, geographic-distance, country-change, and threat-intel composite rules from freight_risk. Higher-weight detectors gated on absolute thresholds.

**Files**:
- `app/rules.yaml` (EDIT — add 15 rules)
- `tests/unit/test_rules_value_geo_threat.py` (NEW)

**Rules added** (15):

**Value-anomaly (~6)**:
| Name | Condition | Weight | maturity_sensitive | Source |
|---|---|---|---|---|
| `extreme_value` | `value_zscore > 3.0 AND customer_observations >= 10` | 0.40 | true | freight_risk 237 |
| `above_normal_value` | `value_zscore > 2.0 AND value_zscore <= 3.0 AND customer_observations >= 10` | 0.15 | true | freight_risk 243 |
| `above_normal_value_vpn` | `value_zscore > 2.0 AND is_vpn` | 0.25 | false | freight_risk 248 |
| `absolute_high_value` | `shipment_value > 10000` | 0.20 | false | freight_risk 253 |
| `threat_intel_high_value` | `ip_in_threat_list AND shipment_value > 2000` | 0.30 | false | freight_risk 39 |
| `ip2p_threat_high_value` | `ip2p_threat_any AND shipment_value > 2000` | 0.40 | false | freight_risk 60 |

**Geographic (~5)**:
| Name | Condition | Weight | maturity_sensitive | Source |
|---|---|---|---|---|
| `ip_intercontinental_jump` | `ip_distance_km > 5000` | 0.35 | false | freight_risk 166 |
| `ip_long_distance_new_ip` | `ip_distance_km > 2000 AND is_new_ip` | 0.25 | false | freight_risk 171 |
| `ip_country_change` | `ip_country_changed AND is_new_ip` | 0.25 | false | freight_risk 176 |
| `api_country_change_unfamiliar` | `is_api_booking AND ip_country_changed AND is_new_ip` | 0.55 | false | freight_risk 330 |
| `impossible_travel_geo` | `impossible_travel` | 0.65 | false | freight_risk 540 |

**Threat-intel composites (~4)**:
| Name | Condition | Weight | maturity_sensitive | Source |
|---|---|---|---|---|
| `threat_intel_level1` | `ip_in_level1` | 0.45 | false | freight_risk 29 — **DUPLICATES** Phase 1's hard-block. Skip if Phase 1 already covers via `blacklisted_ip` action: BLOCK. |
| `threat_level2_vpn` | `ip_in_level2 AND is_vpn` | 0.20 | false | freight_risk 44 |
| `ip2p_threat_scanner` | `ip2p_threat_scanner` | 0.50 | false | freight_risk 50 |
| `ip2p_threat_spam` | `ip2p_threat_spam` | 0.35 | false | freight_risk 55 |
| `ip2p_threat_new_user` | `ip2p_threat_any AND is_new_user` | 0.40 | false | freight_risk 65 |
| `ip2p_threat_api` | `ip2p_threat_any AND is_api_booking` | 0.45 | false | freight_risk 70 |
| `open_proxy` | `is_proxy AND NOT is_vpn AND NOT is_tor` | 0.30 | false | freight_risk 96 |
| `outside_allowed_country` | `ip_outside_allowed_country` | 0.20 | false | freight_risk 181 |

**IMPORTANT — pre-implementation triage**:
- `threat_intel_level1` — REJECT (Phase 1's `blacklisted_ip` BLOCK rule already covers; adding it as a Layer 3 contribution post-BLOCK is redundant — Layer 1 short-circuit means Layer 3 never runs when BLOCK fires).
- `outside_allowed_country` — references `ip_outside_allowed_country`, which is NOT in 2B's whitelist. Either (a) add the field in this commit (extending whitelist) requiring tenant-level config for `country_allowlist`/`country_blocklist`, or (b) defer. **Recommendation: defer to Phase 4 tenant-config landing.** Mark as deferred for now.

After triage, this commit lands **13 rules**, not 15.

`tests/unit/test_rules_value_geo_threat.py` — boundary tests per rule (13 tests).

**Validation**: As 2C.1. Total tests after: 367 + 13 = 380.

**Risk**: **Medium**. The triage step is the load-bearing decision; a missed redundancy (`threat_intel_level1`) would noisy-OR a never-firing rule (Layer 1 short-circuits first) into the catalog — harmless but ugly. The `outside_allowed_country` defer surfaces a Phase-4 dependency we'd want explicit operator awareness on.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 13 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Standard full panel + test-reviewer. Senior-engineer flags the triage decisions.

---

## 2C.7 — IP-familiarity tier + email/phone novelty + remaining freight_risk catalog

**Theme**: Final round of freight_risk catalog rules: IP-familiarity tier-conditioned rules, simple identity-novelty conditioned-on-customer-state rules, plus IP-rotation + netblock-diversity. Each rule references only post-2B Context fields.

**Files**:
- `app/rules.yaml` (EDIT — add ~14 rules; final count ≈ 26 (post-2C.1-2C.6 batch) + 14 = ~80)
- `tests/unit/test_rules_familiarity_and_diversity.py` (NEW)

**Rules added** (~14):

**IP-familiarity tier-conditioned (~3)**:
| Name | Condition | Weight | maturity_sensitive | Source |
|---|---|---|---|---|
| `ip_family_familiar_cloud` | `ip_family_familiar AND is_cloud_ip AND customer_observations >= 10` | 0.05 | true | freight_risk 370 |
| `ip_family_familiar_residential` | `ip_family_familiar AND NOT is_cloud_ip AND customer_observations >= 10` | 0.15 | true | freight_risk 375 |
| `ip_new_known_asn` | `ip_new_known_asn AND customer_observations >= 20` | 0.30 | true | freight_risk 380 |

**Email/phone novelty for established customer (~4)**:
| Name | Condition | Weight | maturity_sensitive | Source / note |
|---|---|---|---|---|
| `unknown_email_for_customer` | `NOT is_email_disposable AND NOT origin_address_familiar AND customer_observations >= 10` | 0.15 | true | Adapted: freight_risk's original references `is_new_email AND email_domain_known_for_customer` (not in our whitelist); we use a proxy condition that's derivable. Document the adaptation in `.ai/decisions.md`. |
| `unknown_phone_for_customer` | similar adaptation | 0.15 | true | Same adaptation. |

**ACTUALLY — defer the two adapted rules.** Adapting a rule's condition to use proxy fields is the kind of weight-calibration guesswork the bootstrap prompt explicitly forbids ("Do not adjust weights during Phase 2 except as the tuned-threshold application in Batch 2D specifies — adjustments here are guesswork, not engineering."). The same logic applies to condition adaptations. Defer to Phase 3+ when `is_new_email`/`is_new_phone` fields land.

**IP rotation / netblock diversity (~4)**:
| Name | Condition | Weight | maturity_sensitive | Source |
|---|---|---|---|---|
| `user_ip_rotation_elevated` | `customer_distinct_ips_30d > 3 AND customer_distinct_ips_30d <= 8` | 0.30 | true | freight_risk 216 (adapted: `user_unique_non_cloud_ips_daily` → `customer_distinct_ips_30d`; this is a structural rename, not a calibration change; same window-size proxy) |
| `user_ip_rotation_high` | `customer_distinct_ips_30d > 8` | 0.50 | false | freight_risk 221 |

**ALSO defer** — `user_unique_non_cloud_ips_daily` and `customer_distinct_ips_30d` are different metrics (one is non-cloud-IPs-daily, the other is all-IPs-30-day). The semantic mismatch is not safe to paper over. Defer the rotation rules to Phase 3+ when a proper distinct-non-cloud-IP counter lands.

**After deferrals, only 3 rules land in 2C.7**: the IP-familiarity-tier rules. Plus rules added as part of compound additions in earlier commits, the batch total stabilizes.

**Theme revision**: 2C.7 is the closing commit — it adds the 3 IP-familiarity-tier rules + runs a final-pass rule-count verification + applies a small set of compound conditions that re-use existing fields without adaptation.

**Rules added** (final 5 in 2C.7):

| Name | Condition | Weight | maturity_sensitive | Source |
|---|---|---|---|---|
| `ip_family_familiar_cloud` | `ip_family_familiar AND is_cloud_ip AND customer_observations >= 10` | 0.05 | true | freight_risk 370 |
| `ip_family_familiar_residential` | `ip_family_familiar AND NOT is_cloud_ip AND customer_observations >= 10` | 0.15 | true | freight_risk 375 |
| `ip_new_known_asn_rule` | `ip_new_known_asn AND customer_observations >= 20` | 0.30 | true | freight_risk 380 (renamed from `ip_new_known_asn` to avoid collision with the Context field name) |
| `value_novelty_compound` | `value_zscore > 1.5 AND ip_fully_new AND customer_observations >= 20` | 0.35 | true | freight_risk 511 |
| `locked_customer_new_ip_family` | `customer_locked_cloud_api AND is_api_booking AND ip_new_known_asn AND customer_observations >= 20` | 0.30 | false | freight_risk 439 |

`tests/unit/test_rules_familiarity_and_diversity.py` — boundary tests per rule (5 tests + 1 final rule-count assertion).

**Validation**:
- `pytest tests/unit/test_rules_familiarity_and_diversity.py -v` — 6 tests pass
- `pytest tests/ -q --asyncio-mode=auto` — full suite green
- Manual: `wc -l app/rules.yaml` should show ≥ 480 lines (78-82 rules at ~5-6 lines each + header)
- `python -c "from app.rules import load_rules; from pathlib import Path; rs = load_rules(Path('app/rules.yaml')); print(len(rs.rules))"` — prints 78-82
- Final rule count check: assert `len(ruleset.rules) >= 78`

**Total tests after 2C.7**: 380 + 6 = 386.

**Risk**: **Medium**. Closing commit; ensure rule count target met and DSL whitelist used correctly.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 6 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None — but document the deferrals (adapted-condition rules, rotation rules) clearly in this commit's message and in `.ai/decisions.md` if not already covered by the "Rules deferred" section above.

**Reviewer routing**: Standard full panel + test-reviewer.

---

## Batch 2C summary

7 commits adding net ~64 new rules to `app/rules.yaml`:
- 2C.1 — Trust-conditioned (7 rules; +10 tests)
- 2C.2 — Dormancy + customer lock-in (5 rules; +6 tests)
- 2C.3 — Residential ASN + IP-class diversity (6 rules; +6 tests)
- 2C.4 — Recipient overlap (2 rules; +6 tests; security-priority)
- 2C.5 — Velocity (additional) + simple novelty (11 rules; +11 tests)
- 2C.6 — Value-anomaly + geographic + threat composites (13 rules after triage; +13 tests)
- 2C.7 — IP-familiarity tier + closing pieces (5 rules; +6 tests)

**Total**: 14 (Phase 1) + 49 (2C net, after deferrals) = **~63 rules** in `app/rules.yaml` end of 2C. Below the 78-82 target — let me double-check.

Rule count audit (re-totalling):
- Phase 1: 14
- 2C.1: 7 → 21
- 2C.2: 5 → 26
- 2C.3: 6 → 32
- 2C.4: 2 → 34
- 2C.5: 11 → 45
- 2C.6: 13 → 58
- 2C.7: 5 → **63**

Final rule count: **63**. Below the 78-82 estimate stated above; correcting: end-of-2C is ~63 rules, with ~32 freight_risk + FreightSentry rules deferred (per "Rules deferred" section).

**The plan target rule count of 78-82 was aspirational based on the bootstrap's ~95-100 figure. The principled deferrals (data-availability gaps, feedback-loop gaps, scope deferrals) bring the realistic Phase 2 end-state to ~63 rules.** This is documented for the operator-approval checkpoint — surface the gap as a question if the operator wants to expand 2C's scope.

If the operator wants closer to the original target, options are:
1. Land the feedback endpoint dependencies (Phase 3 work pulled into Phase 2 — adds ~4 `_previously_rejected` rules + ~4 `_globally_blocked` rules)
2. Land the rarity p-value derivations in 2B (substantive 2B scope expansion — adds ~5-7 rules)
3. Land the additional aggregations (customer_novelty_signals, customer_dest_diversity) in 2B (substantive — adds ~4 rules)

Recommendation: accept the ~63 figure for Phase 2; address the gaps in Phase 3 + 6 as their dependencies land naturally.

**Expected test count after 2C**: 328 (post-2B) + 10+6+6+6+11+13+6 = 386 tests.

**No new migration. No new module files**. All changes are in `app/rules.yaml` + tests.
