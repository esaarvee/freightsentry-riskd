# replay-validation.md — Staging-replay validation

Staging-replay measurements taken across Phase 6C, Phase 7B, and Phase
7D against three corpora exported from the sibling freight_risk SQLite
database. This doc retains the methodology, the final post-7C.12
Phase 7D measurement, and the acceptance gates that closed Phase 7.

For the full Phase 6/7 measurement narrative — Phase 6C per-corpus
detection breakdowns, Phase 6E calibration-backlog seeding, Phase 7B
five-variant exploration, and the structural-bound argument that
retired single-variant FPR tuning — see `docs/history.md`.

**No tuning was performed in response to Phase 6C measurements.** Per
the build-phase discipline, rule weights, thresholds, maturity
parameters, and rule definitions were NOT changed in response to the
6C findings. Phase 7C.12 did perform measured calibration of four
geo rules; that work is documented in the Phase 7D section below and
in `docs/history.md`.

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

`scripts/replay_validation.py` with `concurrency = 50` for Phase 6C
(later tightened to 20 from Phase 7B onward after observed
`RemoteProtocolError` disconnects under accumulated DB state).
httpx.AsyncClient, deterministic `request_id = "replay-{corpus}-{idx}"`
for idempotency-replay on re-runs. POSTs to
`/api/v1/shipments/booking/evaluate`.

### Raw results

Per-transaction JSON output was retained at
`docs/replay-results/{approved,case2,case3}.json` for the 6C run.

> **Note (Phase 7A.0)**: the `docs/replay-results/` directory + the
> entire `scripts/replay/` tree (NDJSON corpora) were scrubbed from
> repository history via `git filter-repo`. Phase 7 operates under
> a strict aggregate-only output policy; per-record content lives
> only in `/tmp/` and is never committed. References above to
> per-record `docs/replay-results/*.json` files describe historical
> Phase 6C state; the files no longer exist in any commit reachable
> from current HEAD.

---

## Phase 6 measurement summary

Phase 6C baseline measurement against the three corpora (full detail
in `docs/history.md`):

| Corpus | BLOCK | REVIEW | ALLOW | Combined |
|---|---|---|---|---|
| case-3b (95, Roulottes Lupien census) | 0% | 0% | 100% | 0% (target ≥85%) |
| case-2 (500, gobolt-non-34x-api) | 13.2% | 84.8% | 2.0% | 98% (target ≥85%) |
| approved (10,000, Jan-Mar 2026) | 0.18% | 40.83% | 58.99% | FPR reading |

case-3b detection 0% because the symmetric
`cold_start_country_triangle_with_carrier_dropoff` did not match the
asymmetric attack shape (CA-registered customer ships CA→US;
triangle mismatch returns False) and
`cold_start_population_baseline_rare_with_carrier_dropoff` did not
fire on an empty `tenant_route_baselines`. Phase 7C.2 added
`cold_start_outbound_carrier_dropoff` to close the gap.

case-2 recall 98% — the 4-rule compound (`api_non_cloud_ip` +
`non_cloud_established_account` + `unknown_destination_address` +
`unfamiliar_ip_country_for_origin`) carries the majority of records.
10 false negatives logged to calibration backlog.

Approved-corpus FPR — 18 BLOCK records all fire the same 4-rule
compound (unintentional overlap with the case-2 compound). 41%
REVIEW driven by `unfamiliar_ip_country_for_origin` (72% fire) and
`unknown_destination_address` (65%).

case-3a empirical validation DEFERRED to post-launch (requires
production case-3a fraud + structured signals from platform
integration). Phase 6E synthesized 6C findings into
`docs/calibration-backlog.md` items 1, 2, 6, and ancillary items.
Item 6 (case-3b) resolved by 7C.2; items 1 and 2 deferred to
post-launch per the Phase 7B structural-bound finding.

---

## Phase 7B/7C calibration summary

Phase 7B (2026-06-04) measured five rule-file variants against all
three corpora to inform 7C calibration. Variants spanned (A)
gate-tightening to `customer_observations >= 30` on IPC + DEST,
(B) weight halving, (C) both, (D) compound secondary-signal
conditions that zero-fire most approved records, and (E) an
asymmetric split (D-style on IPC + A-style on DEST). Targets:
approved BLOCK <0.05%, REVIEW <15%, case-2 recall ≥95%, case-3b
detection ≥85%, IPC fire <15%, DEST fire <20%.

**No variant met both the REVIEW target and the case-2 recall
floor.** A/B/C cut IPC/DEST fire rates ~14pp but REVIEW barely
moved — `api_non_cloud_ip` (0.40) and
`non_cloud_established_account` (0.20) fire on ~41% of approved
on their own, and the noisy-OR exceeds 0.60 even when IPC/DEST
are partially suppressed. D met REVIEW (4.28%) but collapsed
case-2 recall to 43.2% (the case-2 signature depends on the same
IPC/DEST signals). E confirmed the structural bound: REVIEW
34.67%, but the api+non_cloud compound keeps the score above 0.60.
The <15% REVIEW target is not reachable through IPC + DEST tuning
alone.

Phase 7C decision (2026-06-04): apply no FPR-rule variant; land
the structural case-3b fix only. **7C.1 SKIPPED** (baseline IPC +
DEST retained; backlog items 1 + 2 deferred to post-launch).
**7C.2-3 PROCEEDED**: deleted symmetric
`cold_start_country_triangle_with_carrier_dropoff`; added
asymmetric `cold_start_outbound_carrier_dropoff` (weight 0.65,
cold-start-gated in-condition). New derivation
`_outbound_destination_mismatch` in `app/context.py`. Net
`ALLOWED_CONTEXT_FIELDS` count unchanged at 76.
`cold_start_population_baseline_rare_with_carrier_dropoff` retained
unchanged (different signal class). **7C.7-12** added the case-2
ASN-deviation rule, 7C.11 ALLOW-only baseline-gating semantics, and
7C.12 geo-rule weight calibration — measured in the Phase 7D
section below.

The structured-field architectural pattern was preserved across
the 7C catalogue churn: the riskd app consumes
`payload.customer.registered_country` directly and never parses
address strings in production. Full variant tables, per-rule fire
rates, and the structural-bound argument are in `docs/history.md`.

---

## Phase 7D measurement — final, post-7C.12 (2026-06-04)

Final 7D measurement under the Phase 7C.11 baseline-gating
semantics + 7C.12 calibrated geo-rule weights. Methodology shifts
across the Phase 7 arc are documented in `docs/history.md`. The
Phase 7B variant comparison numbers are NOT directly comparable
to Phase 7D measurements — different architectural states.

### Replay environment

- App: post-7C.11 catalogue (81 rules; baseline gated on ALLOW).
- MaxMind GeoLite2 ASN + City mounted (bind-mount, 7D-prep
  commit `d11a534`).
- Replay tenant: id 15622 (`replay-tenant`); full state
  truncated pre-run (customer_baselines, decisions, shipments,
  etc.).
- Concurrency: 20 (Phase 5D verified-good).
- Corpora: deterministic export (seed=42; same record counts as
  the pre-7C.11 7D run).
  - approved: 10000 measurement + 10662 warmup
  - case2: 500 measurement + 100 warmup
  - case3: 95 measurement + 0 warmup

### Targets vs actuals (final, post-7C.12)

| Metric | Baseline (6C) | Pre-MaxMind | Post-7C.11 | **Post-7C.12 (final)** | Target | Verdict |
|---|---|---|---|---|---|---|
| Approved BLOCK | 0.18% | 0.10% | 3.46% | **1.31%** | <0.05% original; **<0.5% retired-target** | CLOSE-BUT-OVER (2.6x retired-target) |
| Approved REVIEW | 41% | 38.83% | 6.17% | **4.58%** | <15% (stretch <10%) | **PASS** (stretch) |
| Case-2 recall (combined) | 98% | 97.6% | 84.0% | **80.0%** | ≥95% | FAIL combined |
| **Case-2 recall (gPYG, legit-history customer)** | — | — | 100% | **100%** | ≥95% | **PASS** |
| Case-2 recall (nD7, fraud-only customer) | — | — | 40.7% | **25.9%** | n/a (no baseline) | DEFERRED to item 20 |
| Case-3b detection | 0% | 0% | 100% | **100%** | ≥85% | **PASS** |

### Per-customer case-2 finding (critical for closeout reading)

Per-customer breakdown of the 500 case-2 measurement records
(both pre-7C.12 and post-7C.12):

| Customer | Records | Has legit history? | Recall |
|---|---|---|---|
| **gPYG** (701 pre-Mar-31 approved records in freight_risk) | 365 | YES | **100%** (365/365 BLOCK) |
| **nD7** (zero approved feedback in entire freight_risk dataset) | 135 | NO | 25.9% post-7C.12; 40.7% pre-7C.12 |

**Customer nD7's complete shipment history in freight_risk is
6,684 records, ALL labeled `reject` with notes `gobolt-non-34x-api`.
Zero `approve`. Zero no-feedback. The customer's entire dataset
presence is fraudulent.**

Earliest nD7 booking: 2026-03-17 14:03 (same day the attack window
starts). No pre-attack legitimate history exists in the dataset.

Implication: the case-2 ASN-deviation rule (7C.7) correctly fires
0% on nD7 because the customer's baseline can't accumulate — they
have no operator-confirmed ALLOW bookings to fold. This is the
DESIGNED behavior: ASN-deviation requires a baseline to deviate
from. nD7-class fraud (purely fraudulent customer with no
legitimate history) needs a different signal class — a "brand-new
+ API + residential ASN" compound. Sketched as calibration-backlog
item 20; deferred to post-launch.

The "84% recall ceiling" framing from the pre-7C.12 analysis was
incorrect. It's not a structural ceiling — it's a customer-mix
artifact: one of two case-2 customers is fraud-only. Customers
with legitimate history (gPYG-class) achieve 100% recall.

### Phase 7C.11 impact analysis (post-7C.11 / pre-7C.12 snapshot — partially superseded)

> **Superseded for case-2 and BLOCK numbers.** This subsection
> was drafted at the post-7C.11 checkpoint before 7C.12 geo-rule
> calibration landed. Items below cite the post-7C.11 / pre-7C.12
> intermediate state. Items 1 (case-3b 0% → 100%) and 3 (REVIEW
> 41% → 6.17%) hold post-7C.12 unchanged. Item 2 (case-2 40.2%
> → 84%) and Losses items 1 (BLOCK 1.45% → 3.46%) and 2 (case-2
> "84% structural ceiling") are SUPERSEDED — see the
> "Phase 7C.12 calibration impact" subsection below for the final
> post-7C.12 numbers, and `docs/history.md` Phase 7 closeout for
> the per-customer-class reframe that retires the "structural
> ceiling" framing. Retained here for empirical-audit continuity.

**Wins**:

1. **Case-3b detection 0% → 100%**. The 7C.2 outbound rule's
   cold-start gate (`customer_observations < 10`) now stays True
   for the entire 95-record corpus because the Roulottes Lupien
   customer's baseline never accumulates (all 95 records BLOCK,
   none fold). Pre-7C.11, the first ~10 records folded and the
   gate closed for records 11-95. Post-7C.11, the gate stays
   open. Rule fires on every case-3 record.

2. **Case-2 recall 40.2% → 84%**. Customer baselines no longer
   polluted by attack records. `api_booking_from_unfamiliar_asn`
   fires 78.4% on case-2 (392/500), up from ~0% pre-7C.11
   (baseline pollution = always-familiar ASNs).

3. **Approved REVIEW 41% → 6.17%**. Beats the <15% target with
   margin; beats the <10% stretch. Combined effect of 7C.7 rule
   replacement + 7C.8 weight reductions + 7C.11 cold-start
   bypassing maturity-gated pair-novelty rules.

**Losses**:

1. **Approved BLOCK rose 1.45% → 3.46%**. Predicted cold-start
   ramp side effect. With baselines smaller (warmup ALLOW rate
   89.7%, not 100%), more legitimate customer bookings appear
   as "novel" along multiple signal classes. Top contributors:
   `unknown_destination_address` (52.4% fire), `impossible_travel
   _geo` (5.8%), `web_booking_from_cloud_ip` (4.7%),
   `ip_fully_new_for_customer` (3.0%), `unfamiliar_ip_country_for
   _origin` (3.0%). Compound noisy-OR pushes 346 records past
   the 0.80 BLOCK threshold.

2. **Case-2 recall ceiling at ~84%**. The 16% ALLOWed attacks
   (80/500) come from ASNs that ARE in the customer's
   warmup-confirmed baseline. Customer gPYG used 32+ different
   ASNs legitimately (per V-14 finding); the 100 warmup records
   sampled those ASNs broadly enough that attack ASNs overlap
   with familiar-ASN history. Reaching 95% recall would require
   an additional signal class (e.g., IP frequency or velocity
   within the established ASN); structural ceiling on
   ASN-deviation alone given diverse legitimate-ASN history.

### Phase 7C.12 calibration impact

The four MaxMind-enabled geo rules (impossible_travel_geo,
ip_intercontinental_jump, ip_country_change, ip_long_distance_new_ip)
had Phase 1-2 intuition-based weights set BEFORE MaxMind was
provisioned in the test/dev stack. 7C.12 calibrated them against
the Jan-Mar 2026 measured FPR. The "wait for production traffic"
framing under the Phase 1-2 `no_weight_tuning_phase2` decision
assumed production was the only data source for FPR-driven
tuning; the 7D-prep MaxMind mount made historical-data
calibration possible.

Calibrated weights (conditions unchanged):
- impossible_travel_geo: 0.65 → 0.30
- ip_intercontinental_jump: 0.35 → 0.20
- ip_country_change: 0.25 → 0.15
- ip_long_distance_new_ip: 0.25 → 0.15

Empirical impact (post-7C.12 vs post-7C.11):
- Approved BLOCK: 3.46% → 1.31% (2.6x reduction)
- Approved REVIEW: 6.17% → 4.58% (~26% reduction)
- gPYG case-2 recall: 100% → 100% (preserved — the operator's gate)
- nD7 case-2 catch: 40.7% → 25.9% (geo rules contributed to nD7 catches; cuts trade away some nD7 detection)
- Case-3b detection: 100% → 100% (unchanged; uses different rule class)

The case-2 gate per operator was "If the calibration moves case-2
below 95% on gPYG, back off." gPYG holds at 100% → no back-off
required.

### BLOCK target retirement (operator decision 2026-06-04)

The Phase 7 BLOCK target (<0.05%) was set against under-enriched
measurements; the production catalogue with MaxMind active exposes
geo-signal contributions that the original target did not
anticipate. Phase 7C.12 calibrates those signals' weights against
the Jan-Mar 2026 measured FPR. Post-calibration result: 1.31%
BLOCK. Operator-stated post-calibration achievable: <0.5%; actual
1.31% is 2.6x over but constitutes a 7.4x reduction from the
6C-baseline-with-MaxMind state (would have been ~9-10% without
the case-2 + case-3b + 7C.11 + 7C.12 cumulative work).

The original <0.05% target is RETIRED. The new operational
acceptance is documented in `docs/history.md` Phase 7 closeout:
production monitoring per `docs/production-launch-checklist.md`
Phase E will track operator-approved-as-legit rate on BLOCKs;
if Day 1-30 production data shows the BLOCKed records are real
fraud catches that were ALLOWed pre-MaxMind, the 1.31% is a real
detection win. If they're predominantly operator-approved false
positives, post-launch calibration iterates further on these
geo-rule weights or compounds.

### Empirical findings worth Phase 7 closeout discussion (pre-7C.12 snapshot — superseded)

> **Superseded by 7C.12 calibration above.** The text below was
> drafted at the post-7C.11 / pre-7C.12 checkpoint when the
> 3.46% BLOCK rate triggered the operator's two-paths question.
> Path (b) was taken (geo-rule weight calibration), 7C.12 landed,
> and the BLOCK rate dropped to 1.31%. The <0.05% target was
> retired in the same operator decision. The "structural
> ceiling" framing for case-2 was reframed to per-customer-class
> semantics in `docs/history.md` Phase 7 closeout. Retained
> here for empirical-audit continuity; do not act on the
> verdicts in this subsection.

1. **Approved BLOCK exceeds target by 69x** (3.46% vs <0.05%).
   The cold-start ramp from 7C.11 produces enough "novel" pair-
   notes on legitimate customer history to compound through
   noisy-OR. Two paths forward (operator decision required):

   - (a) Accept the BLOCK rate as a Phase 7 known limitation;
     document Phase 9+ work on the cold-start ramp signal
     calibration. Production launch monitors per-tenant BLOCK
     rate vs operator-approved-as-legit feedback to confirm the
     pattern is harmless under real-traffic operator review.
   - (b) Iterate further: tighten `unknown_destination_address`
     weight (0.10 → 0.05) and/or add a cold-start grace
     multiplier that downweights maturity-gated rules for
     customers under a tightened observation threshold. Adds
     1-2 commits to Phase 7C.

2. **Case-2 recall structural ceiling at ~84%**. The 7C.7 ASN-
   deviation design has an empirical ceiling that depends on
   the diversity of the customer's legitimate ASN history.
   gobolt-tenant customers using 30+ residential ISPs hit the
   ceiling; gobolt-only-Google-Cloud customers would hit higher
   recall. Phase 9+ architectural workstream (additional signal
   classes for case-2: frequency, velocity, within-ASN /24
   deviation) carries the remaining 11 percentage points to
   the 95% target.

3. **Case-3b 100% is the structural ceiling for this corpus**.
   The single-customer cluster (95 records from Roulottes
   Lupien) is now fully detected. Generalization to diverse
   case-3b-class fraud actors awaits post-launch traffic.

### Phase 7D acceptance gate (pre-7C.12 — resolved)

The plan's iteration policy specified all targets must hit
before Phase 7 closes. Strict reading at the post-7C.11 / pre-7C.12
checkpoint: 7D FAILS (BLOCK and case-2 miss). Operator-decision
2026-06-04 resolved both misses: (a) BLOCK target retired with
rationale (see "BLOCK target retirement" above), (b) case-2
"structural ceiling" reframed to per-customer-class semantics —
gPYG-class hits 100%, nD7-class deferred to backlog item 20.
Post-7C.12 verdict: Phase 7 closes. Surfaced and resolved via
AskUserQuestion at the Phase 7D mid-pass checkpoint.

### Raw aggregate result files

Files live at `/tmp/phase-7d-results/{approved,case2,case3}.json`
on the operator's machine. NOT committed. Aggregate-only per
Phase 7 policy.

---

## Acceptance gates (post-Phase 7E close)

The Phase 7 close decision (operator, 2026-06-04) updated the
acceptance gates as follows. These are the gates that closed Phase 7
and that future calibration cycles measure against.

| Gate | Original target | Post-7E status | Closeout verdict |
|---|---|---|---|
| Approved BLOCK | <0.05% | RETIRED; operational acceptance via production monitoring | CLOSE-BUT-OVER at 1.31% (2.6x retired-target proxy <0.5%); production Phase E monitors operator-approved-as-legit rate |
| Approved REVIEW | <15% (stretch <10%) | RETAINED | **PASS** at 4.58% (stretch) |
| Case-2 recall (per-customer) | ≥95% | REFRAMED to per-customer-class | **PASS** on gPYG (legit-history class) at 100%; nD7 (fraud-only class) DEFERRED to backlog item 20 |
| Case-3b detection | ≥85% | RETAINED | **PASS** at 100% on the Roulottes Lupien census |

**Per-customer framing for case-2.** The pre-7C.12 "84% combined
recall structural ceiling" framing was retired during Phase 7E
close. The combined-corpus number conflates two customer classes:
gPYG (701 pre-Mar-31 approved records) achieves 100% recall; nD7
(zero approved records, entire freight_risk presence fraudulent)
needs a different signal class because ASN-deviation requires a
baseline to deviate from. Backlog item 20 sketches the "brand-new
+ API + residential ASN" compound; deferred to post-launch.

**Replay environment baseline for future calibration.** Future
cycles measure against the Phase 7D environment: post-7C.11
catalogue (81 rules; baseline gated on ALLOW); MaxMind GeoLite2
ASN + City mounted; replay-tenant id 15622 truncated pre-run;
concurrency 20; deterministic export at seed=42 with 10000+10662
approved, 500+100 case-2, 95+0 case-3 records.

**Production monitoring (Phase E launch checklist).** The retired
BLOCK target is replaced with operator-approved-as-legit rate
tracking on production BLOCKs over Day 1-30. If BLOCKs are real
fraud catches that were ALLOWed pre-MaxMind, the 1.31% replay rate
is a detection win; if BLOCKs are predominantly operator-approved
false positives, post-launch calibration iterates on the geo-rule
weights or compounds.

---

## Latency observations (not load-test conditions)

| Corpus | p50 | p95 | p99 |
|---|---|---|---|
| approved | 131.5 ms | 246.4 ms | 395.4 ms |
| case2 | 223.6 ms | 346.5 ms | 472.5 ms |
| case3 | 309.8 ms | 454.3 ms | 538.1 ms |

These latencies are measurement-condition observations from the 6C
run under the local docker-compose stack with concurrency=50; they
are NOT load-test results. Phase 5D's load test established the
booking-endpoint ~12ms p95 baseline under load-test-tuned
conditions (per `docs/load-test-phase-5.md`). The replay-condition
latencies above are inflated by local stack resource constraints,
IP-enrichment cache miss penalties on first encounter of every
source_ip in the corpus, and the 6A.7 + 6A.8 case-3b subsystem
overhead. The replay-condition p95 should NOT be used to
extrapolate production latency.

---

## Raw results location

| Run | Location | Status |
|---|---|---|
| Phase 6C per-transaction JSON | `docs/replay-results/{approved,case2,case3}.json` | Scrubbed from history via `git filter-repo` (Phase 7A.0) |
| Phase 7B variant aggregates | `/tmp/phase-7b-results/{a,b,c,d,e}-{approved,case2,case3}.json` | Operator machine; NOT committed |
| Phase 7D final aggregates | `/tmp/phase-7d-results/{approved,case2,case3}.json` | Operator machine; NOT committed |

Phase 7 operates under a strict aggregate-only output policy;
per-record content lives only in `/tmp/` and is never committed.
Reproducibility contract: re-run
`scripts/calibration/export_from_freight_risk.py` (deterministic
under seed=42 against the same freight_risk DB snapshot) + re-run
the replay orchestrator.

---

## Non-tuning statement

Phase 6C findings did NOT drive rule weight or condition changes.
Per Phase 6 discipline, weights, thresholds, maturity parameters,
and rule definitions were unchanged in response to the 6C
measurements; the calibration backlog at
`docs/calibration-backlog.md` enumerates items for post-launch
real-data observation.

Phase 7C.12 DID perform measured calibration of four
MaxMind-enabled geo rules (impossible_travel_geo,
ip_intercontinental_jump, ip_country_change,
ip_long_distance_new_ip) against the Jan-Mar 2026 measured FPR —
the documented exception to the no-weight-tuning posture. The
original `no_weight_tuning_phase2` decision assumed production was
the only data source for FPR-driven tuning; the 7D-prep MaxMind
mount made historical-data calibration possible without waiting
for production. See `docs/history.md` Phase 7 closeout for full
rationale.

Replay-measurement limitations (apply across all phases):
synthetic-customer-history bias (per-customer baseline forms
during replay); operator-feedback label noise; single-customer
case-3 cluster (95 records, one customer — not a population
sample); empty population baseline for replay tenant (operator
default option (b) per 6C.4 amendment); IP enrichment data
freshness (cached enrichment tables, some
`unknown_origin_address` / `unfamiliar_ip_country_for_origin`
fires may be enrichment gaps rather than true signal).
