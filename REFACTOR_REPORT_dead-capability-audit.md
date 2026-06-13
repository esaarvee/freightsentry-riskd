# REFACTOR_REPORT — Dead-Capability Audit

Pass: dead-capability-audit · branch `feat/refactor` · base HEAD `6dbe642` (PBL D6)
Date: 2026-06-13 · Outcome: **Part-A only (documentation); zero code changes.**

## Commits landed

| # | SHA | Type | Review route | Summary |
|---|---|---|---|---|
| A1 | `7859cbf` | doc | doc-reviewer + senior-engineer | `docs/audits/dead-capability-audit.md` — the classification deliverable |
| A2 | `7885cc1` | ledger | triage-gate-trivial | `.claude/BUGS.md` — phone_prefix/email_domain population-gap tangential |

Working artifacts (not committed): `REFACTOR_PLAN_dead-capability-audit.md`,
`/tmp/dead-capability-findings-01.md`, this report.

## Counts

**Fields**
- Audited (ALLOWED_CONTEXT_FIELDS): **77** — all computed, 0 constant.
- Consumed directly by rules: **63**.
- Dead-to-direct-rules: **14** → after two-layer resolution:
  - value-consumed transitively or by scoring: **6** (`account_age_days`, `total_shipments`, `fraud_confirmed_count`, `cadence_zscore_hours`, `days_since_last_booking`, `customer_registered_country`)
  - field-redundant exposures (value reaches rules via a sibling field): **3** (`ip_country`, `ip_familiarity_tier`, `shipment_currency`)
  - **genuinely dead capability: 5** — of which:
    - keep-and-wire, **cost-bearing** (DB round-trip): **2** (`customer_distinct_ips_30d`, `velocity_user_30d`)
    - keep-and-wire / latent (cheap): **1** (`is_new_route`)
    - keep-as-latent (cheap): **2** (`booking_hour_utc`, `booking_weekday`)
  - discard-candidates: **0**

**Rules**
- Audited: **81**.
- Inert-but-latent (zero-fire by design in replay): 8 `modification_*` + 4 previously-rejected + `cold_start_population_baseline_rare_with_carrier_dropoff` + `case_3_compound` (+ `ip2p_threat_botnet_block` UNDETERMINED). All keep-as-latent.
- Fires-but-never-decides (Layer-1 shadowing): **0** fully decision-inert.
- discard-candidates: **0**.
- Empirical fire-count axis: **degraded to static + partial-aggregate** (per-record replay JSON scrubbed in Phase 7A.0; surviving aggregates are post-7C.12, not stale). Rules without a surviving share marked UNDETERMINED.

**Structural bugs (fix-eligible category)**
- Found: **0** · Fixed: **0** · Deferred-ambiguous: **0**.
- The one suppressing-default candidate (IP2Proxy enrichment) is documented-intended + already observable → classified-not-fixed, not deferred-ambiguous.

## Per-fix disposition

None — zero structural fixes. The deliverable is the classification document.

## Reviewer-caught corrections

| Reviewer | Verdict | Correction applied |
|---|---|---|
| senior-engineer | SHIP IT | None — every technical claim (consumption graph, transitive chains, 5-dead set, §5 structural-bug negatives, Layer-1 shadowing) independently verified against source. |
| doc-reviewer | MINOR TWEAKS | Symmetric-triangle deletion phase corrected: doc said "7C.3"; `docs/history.md` records the deletion under 7C.2 (7C.2/7C.3 landed as one atomic commit). Reworded to "Phase 7C.2–7C.3 catalogue churn" with the history.md note. Applied before commit A1. |

Merge gate satisfied on cycle 1 (one MINOR TWEAKS fix + one SHIP IT); no second cycle required.

## Hand-off

**To the documentation-staleness audit (runs next — this doc is its required reading):**
- Stale transitive example corrected here (symmetric triangle → asymmetric outbound chain); verify no other doc still references the symmetric triangle as live.
- `ip_country` / `ip_familiarity_tier` / `shipment_currency` are field-redundant exposures — correct any doc that calls them rule-consumed.
- IP2Proxy suppressing default is graceful degradation + observable, not detection coverage — ensure prose reflects that.

**To Phase 9 / post-launch calibration:**
- keep-and-wire seed (prioritize the 2 cost-bearing): `customer_distinct_ips_30d`, `velocity_user_30d`, then `is_new_route`; `booking_hour_utc` / `booking_weekday` keep-as-latent.
- Cost-bearing decision for Phase 9: wire a consuming rule (validated) or remove the two 30d velocity DB round-trips from `build_context`.
- Inert-but-latent rules to revisit once representative corpora exist (8 modification, 4 previously-rejected, `case_3_compound`, population-baseline cold-start).
- Discard-candidates: none.

**To BUGS.md (logged, commit A2):** `phone_prefix_stats` never populated; `email_domain_stats` booking-path-only — latent baseline-dimension gap, Phase 9 follow-up.

## Constraints honored

No rule or field removed · no weight/threshold/maturity/band changed · PBL D-series
migrate-automation surface untouched · historical ledgers undisturbed · keep-and-wire
fields not wired (post-launch, out of scope).
