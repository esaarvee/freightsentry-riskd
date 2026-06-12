# Dead-Capability Audit — Classification

> **Status:** identify-and-recommend deliverable. No rule or field was removed; no
> weight, threshold, maturity parameter, or band cutoff was changed. Disposition
> recommendations are documented for Phase 9 / post-launch, **not** executed here.
>
> **Provenance:** branch `feat/refactor`, HEAD `6dbe642` (PBL D6). Produced by the
> dead-capability audit pass, which runs after the PBL D-series migrate-automation
> work and before the documentation-staleness audit. This document is **required
> reading** for the doc-staleness audit and the **seed** for the Phase 9 deep audit.

## What this audits

Capability that costs something but does nothing: context fields computed every
request but consumed by no rule, rules that never fire or never change a decision,
and enrichment stages that are transitively dead. The analysis layers are
raw enrichment/baseline → derived field (`build_context`) → rule condition (`rules.yaml`)
→ scoring (`scoring.py`).

## Constraints honored

1. Not a calibration pass — no weights/thresholds/maturity/bands changed.
2. A zero-fire rule or unconsumed field is **not** proof of uselessness; the default
   verdict is keep-as-latent or keep-and-wire. `customer_distinct_ips_30d` is the
   canonical latent (zero consumers, textbook account-takeover signal).
3. No rule or field deleted.
4. The only fix-eligible category is actively-broken structural items. **None were
   found** (see §5) — this pass is documentation-only.

---

## 1. Field partition — computed vs constant

`ALLOWED_CONTEXT_FIELDS` (`app/rules.py`) holds **77 tokens. All 77 are computed
fields; zero are bare comparison-constant tokens.**

Two independent confirmations:

- Every member is assigned in `build_context` / `build_modification_context`
  (static key-extraction over `app/context.py` matched all 63 rule-referenced
  members; the remaining members are assigned but unconsumed — see §2).
- String comparison RHS values in conditions — `"value"`, `"destination"`,
  `"recipient"`, `"within_30_min"`, `"within_24_hours"`, `"unfamiliar"`, `"none"`,
  etc. — are quoted `ast.Constant` nodes, **not** `ast.Name` tokens. `collect_names()`
  (`app/dsl.py`) never surfaces them, so they were never added to the whitelist.
  The only Names used as comparison RHS are the four `shipment_value_threshold_*`
  fields, which are genuine computed fields (assigned from `resolve_value_caps`).

No token is excluded from the dead-field candidate set on "it's really a constant"
grounds. The partition is {77 computed, 0 constant}.

---

## 2. Two-layer consumption graph

81 rules; **63 distinct fields referenced directly** by rule conditions;
**14 fields dead-to-direct-rules** (in the whitelist, in no `collect_names()` set):

```
account_age_days        booking_hour_utc       booking_weekday
cadence_zscore_hours    customer_distinct_ips_30d   customer_registered_country
days_since_last_booking fraud_confirmed_count  ip_country
ip_familiarity_tier     is_new_route           shipment_currency
total_shipments         velocity_user_30d
```

Resolving the second layer — does the field's **value** reach a rule transitively
(via a derived field), or reach the **scoring** layer?

| Field | Transitive / scoring consumer | Verdict |
|---|---|---|
| `cadence_zscore_hours` | → `is_abnormally_dormant` (`cadence_zscore > 6`) → `dormant_vpn`, `dormant_new_ip`, `ip_distance_dormant`, `modification_dormant_customer` | value-consumed (transitive) |
| `days_since_last_booking` | → `impossible_travel` (`days==0 ∧ dist>500`) → `impossible_travel_geo` | value-consumed (transitive) |
| `customer_registered_country` | → `customer_destination_country_mismatch_outbound` → `cold_start_outbound_carrier_dropoff`; **and** → `derive_route_rarity` → `shipment_route_rare_for_tenant` → `cold_start_population_baseline_rare_with_carrier_dropoff` | value-consumed (transitive) |
| `fraud_confirmed_count` | → `trust_score` (`compute_trust_score`) → `very_low_trust`, `low_trust_*`, … | value-consumed (transitive) |
| `account_age_days` | → `trust_score`; **and** scoring Layer-2 maturity (`customer_state.account_age_days`) | value-consumed (transitive + scoring) |
| `total_shipments` | scoring Layer-2 maturity (`customer_state.total_shipments`) | value-consumed (scoring) |
| `ip_country` | `enrichment.country` value → `ip_country_changed`, `origin_ip_country_familiar` (read from `enrichment.country` directly, **not** the ctx field) | field-redundant |
| `ip_familiarity_tier` | `familiarity` value → `is_new_ip`, `ip_fully_new`, `ip_new_known_asn`, `ip_family_familiar` | field-redundant |
| `shipment_currency` | `currency` value → `resolve_value_caps` → 4 threshold fields | field-redundant |

- **value-consumed (transitive / scoring):** the field's value reaches a rule or the
  scorer through a derived intermediate. Not dead capability.
- **field-redundant:** the value reaches rules via a *sibling* ctx field; the named
  exposure itself is unconsumed but cheap (no extra cost). Candidate for the
  doc-staleness audit to correct if any doc describes the field as rule-consumed.

### Stale-doc correction (hand-off to the doc-staleness audit)

The historically-documented transitive chain
`customer_registered_country → customer_country_triangle_mismatch → cold_start_country_triangle_*`
describes the **symmetric triangle** rule **deleted in the Phase 7C.2–7C.3 catalogue
churn** (`docs/history.md` records the deletion under 7C.2; 7C.2 and 7C.3 landed as one
atomic commit). The current live
chain is the **asymmetric** `customer_destination_country_mismatch_outbound →
cold_start_outbound_carrier_dropoff` (plus the population-baseline chain). Same source
field; renamed derived intermediate and consuming rule.

### Genuinely dead capability — 5 fields

Value reaches no rule (directly or transitively) and no scoring path:

| Field | Source | Per-request cost |
|---|---|---|
| `customer_distinct_ips_30d` | `count_user_distinct_ips_30d` (`app/velocity.py`) | **DB round-trip** — `COUNT(DISTINCT source_ip)` over 30d |
| `velocity_user_30d` | `count_user_30d` | **DB round-trip** — `count(*)` over 30d |
| `is_new_route` | `f"{origin}\|\|{destination}" not in lane_stats` | dict membership (cheap, baseline already loaded) |
| `booking_hour_utc` | `payload.booking_ts.hour` | int attribute (free) |
| `booking_weekday` | `payload.booking_ts.weekday()` | int attribute (free) |

---

## 3. Per-field classification & dispositions

| Field | Layer-2 verdict | Disposition | Rationale |
|---|---|---|---|
| `customer_distinct_ips_30d` | dead capability | **keep-and-wire** | Textbook account-takeover signal (constraint-#2 canonical). Cost-bearing — prioritize. |
| `velocity_user_30d` | dead capability | **keep-and-wire** | 30d volume baseline / sustained-abuse signal; complements existing hourly/daily velocity rules. Cost-bearing — prioritize. |
| `is_new_route` | dead capability | **keep-and-wire / latent** | Lane-novelty (new origin→destination). Cheap; partially covered by `*_address_familiar` rules. Lower priority. |
| `booking_hour_utc` | dead capability | **keep-as-latent** | Off-hours signal; cheap; `hour_hist` baseline supports a future z-score rule. |
| `booking_weekday` | dead capability | **keep-as-latent** | Weekday/weekend anomaly; cheap; `weekday_hist` baseline exists. |
| `account_age_days` | transitive + scoring | live (no action) | Feeds `trust_score` and Layer-2 maturity. |
| `total_shipments` | scoring | live (no action) | Feeds Layer-2 maturity. |
| `fraud_confirmed_count` | transitive | live (no action) | Feeds `trust_score`. |
| `cadence_zscore_hours` | transitive | live (no action) | Feeds `is_abnormally_dormant`. |
| `days_since_last_booking` | transitive | live (no action) | Feeds `impossible_travel`. |
| `customer_registered_country` | transitive | live (no action) | Feeds outbound-mismatch + route-rarity chains. |
| `ip_country` | field-redundant | latent exposure | Value reaches rules via `enrichment.country`; the ctx field itself is unconsumed. |
| `ip_familiarity_tier` | field-redundant | latent exposure | Boolean derivatives consumed; the tier string field itself unconsumed. |
| `shipment_currency` | field-redundant | latent exposure | Value feeds threshold derivations; the ctx field itself unconsumed. |

**Discard-candidates: none.** No field clears the constraint-#2 bar (unconsumed AND
no plausible threat model AND not historical-by-design). Every dead-capability field
has a plausible threat model.

### Cost-bearing subset (priority for Phase 9)

`customer_distinct_ips_30d` and `velocity_user_30d` each add a sequential `await` DB
round-trip inside `build_context` for a value no rule reads. They are the only
dead-to-rules fields carrying real per-request cost. **Disposition: keep-and-wire,
with the wasted query flagged as a cost note** — Phase 9 should resolve them by wiring
a consuming rule (validated, post-launch) or, if they remain unwired, weigh removing
the two velocity counts from `build_context` to reclaim the round-trips. No change in
this pass.

---

## 4. Inert-rule analysis

### Empirical degradation (declared)

There are **no** `docs/replay-results-*.json` on disk. Per-record replay output was
scrubbed from git history in Phase 7A.0 (`git filter-repo`); only aggregate
fire-shares in `docs/replay-validation.md` survive (plus `/tmp/phase-7d-results/*` on
the operator machine). The surviving aggregates reflect the **post-7C.12 calibrated**
rule set (final Phase 7D run) — they are **not stale** — but they are partial.
Therefore the never-fires axis runs **static + partial-aggregate only**; rules with no
surviving share are marked **UNDETERMINED**, never guessed.

### Axis A — never-fires

Surviving aggregate fire-shares (post-7C.12, the only empirical ground truth):
`impossible_travel_geo` 71.7% (approved-BLOCK), `ip_country_change` 58.4%,
`ip_long_distance_new_ip` 57.0%, `ip_intercontinental_jump` 46.0%,
`unknown_destination_address` 52.4%, `web_booking_from_cloud_ip` 4.7%,
`ip_fully_new_for_customer` 3.0%, `unfamiliar_ip_country_for_origin` 3.0% BLOCK /
~72% REVIEW-fire, `api_booking_from_unfamiliar_asn` 78.4% (case-2). All other rules:
no surviving per-rule share → **UNDETERMINED on disk.**

**Structurally latent in the replay corpora** (zero-fire by design — keep-as-latent,
not discard, per constraint #2):

- **8 `modification_*` rules** — all three corpora POST `booking/evaluate`; the
  booking path sets `modification_type = "none"` (`BOOKING_PATH_MODIFICATION_DEFAULTS`),
  matching no enum literal these rules condition on. Zero-fire is structural.
- **4 previously-rejected rules** (`email/phone/origin/ip_previously_rejected_for_customer`)
  — require accumulated operator-rejection state (`rejected_*_hmacs`, `r_n > 0`); the
  replay tenant is truncated pre-run → no rejection history → expected zero-fire. Core
  repeat-offender detectors.
- **`cold_start_population_baseline_rare_with_carrier_dropoff`** — requires tenant
  population baseline ≥100 obs; the replay tenant has an empty `tenant_route_baselines`
  → returns False → zero-fire in replay; fires on mature production tenants.
- **`case_3_compound`** (case-3a established-customer compromise) — no case-3a corpus
  exists; case-3a validation is explicitly deferred to post-launch. UNDETERMINED.
- **`ip2p_threat_botnet_block`** (Layer-1) — botnet-tag rarity; UNDETERMINED.

### Axis B — fires-but-never-decides (Layer-1 shadowing)

The only Layer-1 BLOCK conditions are `ip_in_level1` (→ `blacklisted_ip`) and
`ip2p_threat_botnet` (→ `ip2p_threat_botnet_block`). **No Layer-3 rule is conditioned
solely on either** — `threat_intel_level1` was deliberately not ported precisely to
avoid this (Phase 2C.6 triage note). The two rules referencing `ip_in_threat_list`
(`= level1 OR level2`) — `ip_velocity_threat`, `threat_intel_high_value` — are only
*partially* shadowed on the `level1` branch (when level1 fires, Layer-1 short-circuits
before Layer-3) but still decide on the `level2` branch. **No fully decision-inert
rule found.** The catalogue already avoids the trap.

**Discard-candidates among rules: none.**

---

## 5. Structural-bug detection (the only fix-eligible category)

| Sub-class | Result |
|---|---|
| (a) Rule references a name `build_context` never sets (unsatisfiable / request-time `NameError`) | **NONE.** `collect_names()` ⊆ `ALLOWED_CONTEXT_FIELDS` (the loader fails fast at startup) **and** all 63 referenced fields are assigned in ctx (verified — zero missing). Modification fields are populated on the booking path via `BOOKING_PATH_MODIFICATION_DEFAULTS`, so no NameError path exists. |
| (b) Derived field logically dead (always None/False/0 from a bug) | **NONE.** Spot-checked `impossible_travel`, `ip_distance_km`, `customer_destination_country_mismatch_outbound`, `unfamiliar_asn_for_customer`, `_derive_route_unfamiliar`, `is_abnormally_dormant`, `value_zscore` — all have non-degenerate firing paths. The "address-country class": `origin_ip_country_stats` writer key `f"{origin}\|\|{ip_country}"` matches reader `f"{origin}\|\|{enrichment.country or ''}"` (same `enrichment.country` source passed at the booking call site); `country_route_stats` writer/reader both `f"{oc}\|\|{dc}"` with **both** call sites (booking + feedback) passing `shipment_origin_country` / `shipment_destination_country`. No writer/reader key drift; no call-site omission. |
| (c) Enrichment stage with a suppressing default | **EXISTS but DORMANT-by-design — classified, not fixed.** `enrich._lookup` wraps the IP2Proxy lookup in `except Exception → rec={}`, leaving `is_vpn/is_proxy/is_tor=False`; missing source files likewise leave enrichment booleans False. This is **documented intended behavior** (`app/enrich.py:6` docstring; `.ai/enrichment.md` "Default behaviours" — "rules conditioned on enrichment booleans get False — no spurious positives") **and already observable**: `enrich.ip2proxy_lookup_failed` + `enrich.{maxmind_*,firehol,ip2proxy,cloud_cidr}_missing` WARNING logs, and `/health` `enrichment: degraded`. A valid intended reading exists → fails the bug-vs-dormant test → classify-and-note, do not fix. |

**Conclusion: zero unambiguous structural bugs. This pass produced no code change.**
This is the expected "few or none" outcome for the fix-eligible category.

---

## 6. Hand-off

**To the documentation-staleness audit (runs next):**

- Correct the stale transitive example (symmetric triangle → asymmetric outbound chain; §2).
- `ip_country`, `ip_familiarity_tier`, `shipment_currency` are **field-redundant
  exposures** — if any doc describes them as rule-consumed, correct it.
- The IP2Proxy suppressing default is intended + observable, not a defect — ensure docs
  describe it as graceful degradation, not detection coverage.

**To Phase 9 / post-launch calibration:**

- keep-and-wire seed: `customer_distinct_ips_30d`, `velocity_user_30d` (both
  cost-bearing — prioritize wire-vs-remove), `is_new_route`; plus `booking_hour_utc`
  and `booking_weekday` as keep-as-latent.
- Discard-candidates: **none**.
- Inert-but-latent rules to revisit once representative corpora exist: the 8
  `modification_*` rules, the 4 previously-rejected rules, `case_3_compound`,
  `cold_start_population_baseline_rare_with_carrier_dropoff`.

**Tangential (logged to `.claude/BUGS.md`):** `phone_prefix_stats` is never populated
and `email_domain_stats` is populated on the booking path but not the feedback path;
neither feeds an `ALLOWED_CONTEXT_FIELDS` field. Latent baseline-dimension gap, not a
dead rule-field. Low severity; Phase 9 follow-up.
