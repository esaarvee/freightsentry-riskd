# Phase 6C replay-validation measurement

Replay of the freightsentry-riskd booking endpoint against three
corpora exported from the sibling freight_risk SQLite database, run
2026-06-03 on the local docker-compose stack.

**No tuning was performed in response to these measurements.** Per
the project-wide build-phase discipline, rule weights, thresholds,
maturity parameters, and rule definitions were NOT changed in
response to the findings here. Phase 6E synthesizes the findings
into a calibration backlog for post-launch real-data observation.

## Methodology

### Tenant

Single dedicated tenant `replay-tenant` (id 15622) with
`allowed_currencies = ["USD", "CAD"]` and matching CAD + USD
value_caps. Created via:

```bash
python3 scripts/tenant_onboard.py \
    --external-id replay-tenant \
    --display-name "Phase 6C Replay Tenant" \
    --config-json /tmp/replay-tenant-config.json \
    --rotate-token
```

where the config JSON carries the multi-currency setup. No
historical booking data; empty `tenant_route_baselines` (operator
default — option (b) per the plan's 6C.4 amendment).

### Corpora

Produced by the sibling-repo export script at
`/Users/drshott/PycharmProjects/miscProj/freight_risk/scripts/export_for_riskd.py`
with `seed=42`. See `scripts/replay/README.md` for the schema mapping
and SQL queries.

| Corpus | Records | Source filter |
|---|---|---|
| `approved_jan_mar.ndjson` | 10,000 | `feedback='approve' AND target_date BETWEEN '2026-01-01' AND '2026-03-31'`, random sample |
| `case2_sample.ndjson` | 500 | `feedback='reject' AND notes='gobolt-non-34x-api'`, random sample |
| `case3_census.ndjson` | 95 | `feedback='reject' AND notes='Roulottes Lupien — entire customer history fraud (user-confirmed)'`, full census |

Phase 6A structured fields hardcoded per corpus (export-side):
- case-3 records: `customer.registered_country = "CA"`,
  `shipment.origin_via_carrier_dropoff = true` (operator-supplied
  ground truth from the fraud investigation)
- case-2 + approved records: `customer.registered_country = null`,
  `shipment.origin_via_carrier_dropoff = false` (no per-record
  source data; case-2 was API ATO automation, not carrier-dropoff)

Currency: all corpora `shipment.currency = "CAD"` (Phase 6B project
default).

### Orchestrator

`scripts/replay_validation.py` with `concurrency = 50` (Phase 5
load-test cadence; 5x pool max 10). httpx.AsyncClient, deterministic
`request_id = "replay-{corpus}-{idx}"` for idempotency-replay on
re-runs. POSTs to `/api/v1/shipments/booking/evaluate`.

### Raw results

JSON output retained at `docs/replay-results/{approved,case2,case3}.json`
with per-transaction triggered_rules + score + latency for
operator-side enumeration.

---

## case-3a empirical validation — DEFERRED

`case_3_compound` (the case-3a established-customer-compromise rule
shipped in Phase 6A.3) is **not expected to fire on the case-3b
census** by design:

- Maturity gate `customer_observations >= 10` excludes brand-new
  customers; records 1-9 fail the gate regardless of other signals.
- For records 10+, the customer's own route baseline is contaminated
  by the prior fraud records (CA→US becomes "familiar" because the
  prior fraud bookings established it), so
  `shipment_route_unfamiliar_for_customer` returns False.

The rule is in production for the case-3a threat class. Empirical
validation defers to post-launch when (a) the platform integration
supplies `customer.registered_country` + `origin_via_carrier_dropoff`
in production booking payloads AND (b) case-3a-style fraud
(established-customer compromise) is observed in production
traffic. The 6C replay against the case-3b census exists to
measure case-3b detection capability, not case-3a.

---

## case-3b detection on the Roulottes Lupien census (95-record cluster)

| Decision | Count | Share |
|---|---|---|
| BLOCK | 0 | 0% |
| REVIEW | 0 | 0% |
| ALLOW | 95 | 100% |

**Combined REVIEW + BLOCK detection: 0/95 = 0%.** Far below the
plan's ≥85% target.

Per-rule fire counts (top of the table):

| Rule | Fires |
|---|---|
| `unfamiliar_ip_country_for_origin` | 85/95 |
| `unknown_destination_address` | 82/95 |
| `extreme_value` | 2/95 |
| `ip_fully_new_for_customer` | 1/95 |

Note that the rule fires above do not compound to push score into
REVIEW (≥0.60) or BLOCK (≥0.80) bands at the cold-start customer
priors and maturity-sensitive weights in effect.

### Why the case-3b compound rules did not fire

**`cold_start_country_triangle_with_carrier_dropoff` (case-3b
simple, Phase 6A.5)** — condition:
```
customer_country_triangle_mismatch
AND origin_via_carrier_dropoff
AND customer_observations < 10
```

`customer_country_triangle_mismatch` requires
`customer_country != origin_country AND customer_country != destination_country`
— customer ships outside their declared country in BOTH origin and
destination.

The Roulottes Lupien attack pattern is: CA-registered customer with
home address `2700 route 122, SAINT-CYRILLE-DE-WENDOVER, QC, CA`,
booking ships from that same CA address to a US destination. So:
- `customer_registered_country = "CA"`
- `origin_country = "CA"` (origin address parsed by the export
  script's last-token regex)
- `destination_country = "US"`

The customer-country equals the origin-country → triangle mismatch
returns False → the simple compound does not fire.

This is consistent with the rule's design intent: a CA-registered
business sometimes shipping CA→US is legitimate cross-border
behavior, not by-itself fraud-shaped. The rule targets the
brand-new-customer fraud pattern where the customer ships entirely
outside their declared country (e.g., a CA-registered customer
shipping US→US repeatedly, which would be unusual for a Canadian
business). The Roulottes Lupien attack is a different sub-class of
case-3b: domestic-origin + cross-border-destination + carrier
dropoff, which the current rule catalogue does not specifically
target.

**`cold_start_population_baseline_rare_with_carrier_dropoff`
(case-3b sophisticated, Phase 6A.9)** — condition:
```
shipment_route_rare_for_tenant
AND origin_via_carrier_dropoff
AND customer_observations < 10
```

`shipment_route_rare_for_tenant` requires the tenant's
`tenant_route_baselines` to contain ≥100 observations across all
triples (`RARITY_MIN_OBSERVATIONS = 100`). The replay tenant was
created immediately before the replay with no historical bookings;
the table is empty. The cold-start gate inside the derivation
returns False, so the sophisticated compound does not fire.

This is consistent with the rule's design intent: tenant-population-
derived rarity is meaningful only when the tenant has accumulated
sufficient population data; firing on insufficient data would
produce noise on freshly-onboarded tenants. The 6C.4 plan amendment
documented this as "option (b) — empty baseline" (operator default;
more conservative; relies less on synthetic data).

**`case_3_compound` (case-3a, Phase 6A.3)** — not expected to fire
on the case-3b census (see "case-3a empirical validation" section
above).

### Single-customer cluster caveat

All 95 case-3 records are from a single customer (Roulottes Lupien
2000 inc., CA-registered, all bookings May 2026). The replay
measures detection on this specific attack pattern, not the
population of case-3b-class fraud. Generalization across diverse
case-3b fraud actors (e.g., a CA-registered customer shipping
US→US, or US-registered shipping outside-US country pairs) awaits
post-launch traffic with a broader fraud sample.

### Items carried to the calibration backlog (Phase 6E)

The case-3b detection gap on the Roulottes Lupien pattern surfaces
the following items for the post-launch calibration backlog:

1. **Domestic-origin + carrier-dropoff + cross-border-destination
   sub-pattern.** The Roulottes Lupien attack shape (customer
   ships from their declared country to outside-country with
   carrier dropoff) is not currently covered by either case-3b
   compound. A future calibration cycle may evaluate either (a)
   relaxing `customer_country_triangle_mismatch` to fire when
   customer ≠ destination only (origin can match), or (b) adding
   a separate compound `cold_start_outbound_carrier_dropoff` that
   targets the asymmetric pattern. Decision deferred until
   post-launch data shows whether this pattern recurs across
   diverse fraud actors or is specific to the Roulottes Lupien
   cluster.

2. **Population baseline seeding for new tenants.** Production
   tenants will start with empty `tenant_route_baselines` (Phase
   6D launch checklist documents this cold-start behavior).
   Sophisticated compound detection ramps up as the tenant
   accumulates ≥100 observations. Calibration may revisit whether
   the 100-observation minimum is the right threshold, or whether
   sub-100 tenants should default to a configurable static rarity
   list.

3. **`case_3_compound` empirical validation.** Deferred until
   case-3a fraud (established-customer compromise) is observed in
   production with the structured signals supplied.

---

## case-2 recall on gobolt-non-34x-api fraud (500 records)

| Decision | Count | Share |
|---|---|---|
| BLOCK | 66 | 13.2% |
| REVIEW | 424 | 84.8% |
| ALLOW | 10 | 2.0% |

**Combined REVIEW + BLOCK recall: 490/500 = 98%.** Above the ≥85%
target.

Per-rule fire counts (top of the table):

| Rule | Fires |
|---|---|
| `api_non_cloud_ip` | 500/500 |
| `non_cloud_established_account` | 490/500 |
| `unknown_destination_address` | 480/500 |
| `unfamiliar_ip_country_for_origin` | 480/500 |
| `ip_fully_new_for_customer` | 138/500 |
| `unknown_origin_address` | 88/500 |
| `new_user_api_non_cloud` | 10/500 |
| `value_novelty_compound` | 10/500 |

`api_non_cloud_ip` fires on every case-2 record — case-2 is the API
ATO pattern (`source = 'api'`) and the fraud-shipment IPs are
residential / non-cloud. The compound with
`non_cloud_established_account` (490/500), `unknown_destination_address`
(480/500), and `unfamiliar_ip_country_for_origin` (480/500) puts
the score firmly into REVIEW or BLOCK bands for the majority of
records.

### Items carried to the calibration backlog (Phase 6E)

1. **10 records ALLOW'd (false negatives on case-2)** —
   `ip_fully_new_for_customer` fires on only 138/500 and
   `unknown_origin_address` on 88/500, suggesting some case-2
   records have IP-familiar customers AND known origin addresses,
   which weakens the compound. Post-launch calibration may
   evaluate whether the rule weights compound correctly on this
   subset.

---

## Approved-corpus enumeration (10,000-record FPR reading)

| Decision | Count | Share |
|---|---|---|
| BLOCK | 18 | 0.18% |
| REVIEW | 4,083 | 40.83% |
| ALLOW | 5,899 | 58.99% |

Strict-reading enumeration per the Phase 6 prompt's methodology:
both BLOCK and REVIEW on operator-approved transactions are
documented with contributing rules.

### BLOCK records (18/10000)

The 18 BLOCK records on operator-approved transactions are the most
load-bearing FPR finding from this replay. All 18 fire the same
4-rule compound at the top:

| Rule | BLOCK records firing |
|---|---|
| `unknown_destination_address` | 18/18 |
| `unfamiliar_ip_country_for_origin` | 18/18 |
| `api_non_cloud_ip` | 18/18 |
| `non_cloud_established_account` | 18/18 |
| `ip_fully_new_for_customer` | 17/18 |
| `value_novelty_compound` | 13/18 |
| `extreme_value` | 6/18 |
| `unknown_origin_address` | 4/18 |
| `dormant_new_ip` | 2/18 |
| `above_normal_value` | 1/18 |

The 4-rule top compound fires on every BLOCK record. The
`value_novelty_compound` and `extreme_value` additions push 13 and
6 of the 18 BLOCK records over the 0.80 threshold.

Pattern read: high-value bookings from API-non-cloud-IP customers
with unknown destination addresses and unfamiliar
origin-IP-country pairs. These shapes can be legitimate (large
established customers shipping to new partners) but also overlap
with the case-2 fraud surface area. The 18 records cluster
suggests an unintentional overlap between legitimate large-customer
behavior and the case-2 compound — the calibration backlog should
revisit whether the compound's weights are correctly tuned against
real-data once production traffic provides a comparable baseline.

### REVIEW records (4083/10000)

The 41% REVIEW rate on operator-approved transactions is high but
expected given the cold-start customer prior contribution and the
broad-firing of `unfamiliar_ip_country_for_origin` (7183/10000 of
the approved corpus). REVIEW is operationally a "human-reviewed,
not auto-blocked" band — not a false-positive in the BLOCK sense
but worth post-launch observation.

### Per-rule fire counts on approved corpus (top of the table)

| Rule | Fires |
|---|---|
| `unfamiliar_ip_country_for_origin` | 7,183 |
| `unknown_destination_address` | 6,482 |
| `api_non_cloud_ip` | 4,128 |
| `non_cloud_established_account` | 3,986 |
| `ip_fully_new_for_customer` | 267 |
| `unknown_origin_address` | 203 |
| `above_normal_value` | 162 |
| `extreme_value` | 155 |
| `new_user_api_non_cloud` | 142 |
| `ip_family_familiar_residential` | 105 |

### Items carried to the calibration backlog (Phase 6E)

1. **`unfamiliar_ip_country_for_origin` fires on 72% of the approved
   corpus.** This is a high baseline fire rate that contributes to
   the 41% REVIEW share. Post-launch calibration may evaluate
   whether the rule's weight is correctly tuned against the
   broader-than-expected legitimate-customer cross-border-IP
   pattern, or whether the rule needs a tighter trigger condition
   (e.g., compound with route deviation rather than IP-origin pair
   novelty alone).

2. **`unknown_destination_address` fires on 65% of the approved
   corpus.** Similar baseline-fire-rate observation. The rule
   exists to flag novel destination addresses that should be
   reviewed, but at 65% on operator-approved traffic the rule
   alone is not discriminating. Post-launch calibration may
   evaluate whether the weight is correctly tuned or whether the
   rule should compound with other signals before contributing.

3. **`api_non_cloud_ip` + `non_cloud_established_account`
   co-fire on >40% of the approved corpus.** These are case-2-
   targeting rules and their high baseline fire rate on
   operator-approved traffic suggests partial overlap with
   legitimate behavior. Post-launch calibration may revisit the
   compound's weights.

4. **The 18 BLOCK records.** Per-record `request_id` enumeration
   retained in `docs/replay-results/approved.json` per_transaction
   array (decision=='BLOCK'). Post-launch operators can use this
   list to triage whether the BLOCK pattern persists on real
   traffic of similar shape.

---

## Latency observations (not load-test conditions)

| Corpus | p50 | p95 | p99 |
|---|---|---|---|
| approved | 131.5 ms | 246.4 ms | 395.4 ms |
| case2 | 223.6 ms | 346.5 ms | 472.5 ms |
| case3 | 309.8 ms | 454.3 ms | 538.1 ms |

These latencies are measurement-condition observations under the
local docker-compose stack with concurrency=50; they are NOT
load-test results. Phase 5D's load test established the booking-
endpoint ~12ms p95 baseline under load-test-tuned conditions
(per `docs/load-test-phase-5.md`; the same baseline showed
modification and feedback endpoints at different p95 values). The replay-condition
latencies above are inflated by:
- Local stack resource constraints
- IP-enrichment cache miss penalties on first encounter of every
  source_ip in the corpus
- The 6A.7 + 6A.8 case-3b subsystem overhead (~4ms p95 expected;
  validated to be within budget at Phase 5 cadence — see Phase
  6A.10 latency budget section in `.ai/decisions.md`)

Phase 6D's smoke test + Phase 6E's launch checklist Day 1-7
monitoring will measure latency under production-shaped conditions.
The replay-condition p95 should NOT be used to extrapolate
production latency.

---

## Explicit non-tuning statement

Findings documented per Phase 6 discipline. **No rule weight,
threshold, maturity parameter, or rule definition was changed in
response to these measurements.** The calibration backlog at
`docs/calibration-backlog.md` (created in Phase 6E) enumerates
items for the post-launch real-data observation window.

Limitations:
- Synthetic-customer-history bias: the replay tenant has no prior
  bookings; the per-customer baseline FORMS during the replay
  itself, so within-corpus repeated-customer dynamics (e.g., a
  customer's 2nd booking in the corpus seeing the 1st as
  "familiar") affect downstream records.
- Label-noise tolerance: the operator-supplied feedback labels
  in freight_risk are subject to operator-side classification
  noise. The 0.18% BLOCK rate on "approved" includes some records
  that may be operator-mislabeled-approved rather than truly
  legitimate.
- Single-customer case-3 cluster: 95 records from one customer
  is not a population case-3b sample. See "Single-customer cluster
  caveat" above.
- Empty population baseline for replay tenant: per the operator
  default (option (b)) in the 6C.4 amendment, the replay tenant
  has no seeded historical data. This is more conservative
  (relies less on synthetic data) but means the sophisticated
  case-3b compound has no opportunity to fire.
- IP enrichment data freshness: the replay was conducted against
  the locally-cached IP enrichment tables. Some
  `unknown_origin_address` / `unfamiliar_ip_country_for_origin`
  fires may be explained by enrichment gaps rather than true
  signal.

---

## Raw results location

| File | Records | Contents |
|---|---|---|
| `docs/replay-results/approved.json` | 10,000 transactions | Per-transaction triggered_rules, score, latency for FPR enumeration |
| `docs/replay-results/case2.json` | 500 transactions | Per-transaction details for case-2 recall measurement |
| `docs/replay-results/case3.json` | 95 transactions | Per-transaction details for case-3b detection measurement |

Each file's `per_transaction` array is enumerable for downstream
analysis. Operator can re-derive any of the aggregate counts in
this doc from the per_transaction array.
