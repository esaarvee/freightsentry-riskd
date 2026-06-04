# Calibration backlog — post-launch tuning checklist

> **Audience**: operators and post-launch development phases driving rule
> weight, threshold, and compound-condition tuning against real production
> traffic. **NOT acted on during the build phases.** This document is the
> canonical record of deferred items synthesized from the Phase 6C replay
> validation + amendments accumulated across Phases 6A and 6D.
>
> Tuning happens during the 5-month post-launch observation window per
> `docs/production-launch-checklist.md` (Phase G — Month 2-3 first tuning
> pass; Phase H — Month 4-5 second pass).
>
> Why no build-phase tuning: weights calibrated against synthetic-history
> data risk fitting the artifact of the synthesis rather than the
> production reality. Ship the rules as-shipped; observe under real
> traffic; tune against this backlog with confidence.

---

## 1. Approved-corpus FPR — `unfamiliar_ip_country_for_origin` (72% fire rate)

**Observation (from `docs/replay-validation.md`)**: the rule fires on
7,183 / 10,000 operator-approved records — a high baseline that
contributes meaningfully to the 41% REVIEW share on the approved corpus.

**Deferred action**: observe ≥4 weeks of production fire rate. If the
pattern persists on real traffic, evaluate (a) weight reduction, or
(b) compound the trigger with route deviation rather than IP-origin pair
novelty alone (e.g. require a second signal before the rule contributes).

**PARTIAL** (Phase 7C.8, 2026-06-04): weight reduced 0.30 → 0.15.
The rule's fire rate is intentionally preserved (Phase 7 amendment
clarified the rule is pair-novelty on (origin, ip_country) per
customer; legitimate freight customers' origin expansion is the
dominant pattern). Its contribution to scoring drops via the
weight reduction; case-2 detection load shifted to
`api_booking_from_unfamiliar_asn` (7C.7). Outstanding post-launch
work: the rule's pair-novelty SEMANTICS may need additional
refinement (e.g., decouple origin from IP-country; require a
second signal). Not RESOLVED — semantic refinement still
deferred to post-launch real-data observation.

---

## 2. Approved-corpus FPR — `unknown_destination_address` (65% fire rate)

**Observation**: fires on 6,482 / 10,000 approved records. Similar
high-baseline behavior; rule alone is not discriminating between
fraud and legitimate novel destinations.

**Deferred action**: observe ≥4 weeks. If persistent on real traffic,
evaluate weight reduction OR compounding with other signals before
contributing (e.g. only contribute when paired with a value or IP
anomaly).

**PARTIAL** (Phase 7C.8, 2026-06-04): weight reduced 0.20 → 0.10.
Same shape as item 1: fire rate preserved; contribution to scoring
reduced; case-2 detection load shifted to the ASN-deviation rule.
Semantic refinement (compound with value or IP anomaly) remains
post-launch work.

---

## 3. Approved-corpus FPR — `api_non_cloud_ip` + `non_cloud_established_account` co-fire

**Observation**: each fires on >40% of the approved corpus
(4,128 / 10,000 and 3,986 / 10,000). They are case-2-targeting rules
but the high baseline indicates partial overlap with legitimate
behavior shapes.

**Deferred action**: observe ≥4 weeks. Evaluate compound weight or
condition tightening if the false-positive cluster persists.

---

## 4. Approved-corpus BLOCK cluster — 18 records / 0.18%

**Observation**: 18 / 10,000 approved records reached BLOCK on the
4-rule compound (`unknown_destination_address` +
`unfamiliar_ip_country_for_origin` + `api_non_cloud_ip` +
`non_cloud_established_account`). All 18 records' `request_id` retained
in `docs/replay-results/approved.json` per-transaction array.

**Caveat**: per Phase 6C limitations, "approved" labels carry label noise
(operator-approved ≠ truly legitimate). Some fraction of the 18 may be
fraud that slipped through the manual approval workflow.

**Deferred action**: post-launch operators triage whether the BLOCK
pattern persists on real traffic of similar shape. If the pattern
recurs on confirmed-legitimate transactions, revisit the 4-rule
compound's weight tuning.

---

## 5. Case-2 false negatives — 10 / 500 ALLOW'd

**Observation**: 10 case-2 records bypassed BLOCK + REVIEW. The
fire-count distribution shows `ip_fully_new_for_customer` (138/500)
and `unknown_origin_address` (88/500) — these records had
IP-familiar customers AND known origin addresses, softening the
compound.

**Deferred action**: post-launch evaluation of whether weight
adjustments would improve case-2 recall toward 100% without
inflating approved-corpus FPR. Rationale for deferral: 98% recall
is acceptable for v1 launch; tuning without diverse
production-traffic counter-examples risks regression on the
approved-corpus pattern.

---

## 6. Case-3b detection on Roulottes Lupien census — 0 / 95 (0%)

**Observation**: combined REVIEW + BLOCK detection on the 95-record
census was 0% (target ≥85%). Two structural reasons (full diagnosis
in `docs/replay-validation.md`):

- **`cold_start_country_triangle_with_carrier_dropoff`** did not fire:
  the Roulottes Lupien attack is CA-registered customer shipping CA→US,
  which is asymmetric (origin matches customer country, only
  destination differs). The triangle-mismatch condition requires both
  origin AND destination to differ from customer country.
- **`cold_start_population_baseline_rare_with_carrier_dropoff`** did not
  fire: the replay tenant had an empty `tenant_route_baselines` table
  (cold-start). The rule's 100-observation minimum (`RARITY_MIN_OBSERVATIONS`)
  is intentionally conservative for cold-start tenants.

**Single-customer cluster caveat**: all 95 records are from one
customer. Cluster recall ≠ population recall. Generalization across
diverse case-3b fraud actors awaits post-launch traffic.

**Deferred actions (post-launch)**:

1. **Domestic-origin + carrier-dropoff + cross-border-destination
   sub-pattern.** Either (a) relax `customer_country_triangle_mismatch`
   to fire when customer ≠ destination only (origin can match), or
   (b) add `cold_start_outbound_carrier_dropoff` targeting the
   asymmetric pattern. Decide after post-launch data shows whether
   this pattern recurs across diverse fraud actors or is specific
   to the Roulottes Lupien cluster.

2. **Population baseline seeding for new tenants.** Sophisticated
   compound detection ramps up as tenants accumulate ≥100 observations.
   Revisit whether 100 is the right minimum or whether sub-100 tenants
   should default to a configurable static rarity list.

3. **`case_3_compound` empirical validation.** `case_3_compound`
   targets case-3a (established-customer compromise) and is not
   expected to fire on the case-3b census by design (maturity gate +
   contaminated customer baseline). Validation defers to post-launch
   when (a) platform integration supplies the structured signals AND
   (b) case-3a-style fraud is observed in production.

---

## 7. Trust-suppression on mature accounts — Phase 7+ architectural workstream

**Pattern**: a mature legitimate customer has a low `account_prior`
(established trust). If that account is compromised, the case-3a /
case-3b signals fire, but the combined score may not reach BLOCK
because the trust contribution offsets the fraud contribution.

**Classification**: architectural, NOT parameter tuning. Deferred to
Phase 7+. Candidate designs to evaluate:

- Capability-based trust (per-dimension trust: shipping behavior,
  payment history, geographic pattern — compromise one, lose one).
- Session-anomaly signals (device fingerprint change, geographic jump
  indicators) feeding a separate suppression layer.
- Asymmetric trust freeze (rapid trust erosion on first anomaly,
  slow trust rebuild).

Documented in `.ai/decisions.md` Phase 6A "Case-3 detection
capability" → "Phase 7+ architectural concerns".

---

## 8. Population baseline thresholds (Phase 6A amendment)

**Current values** in `app/tenant_route_baselines.py`:
- `RARITY_MIN_OBSERVATIONS = 100`
- `RARITY_THRESHOLD = 0.02` (2%)

**Deferred action**: tune post-launch with real production traffic data
once tenant baselines accumulate diverse legitimate routes. The 6C
replay had no signal on this (empty baseline).

---

## 9. Modification weight calibration

**Status**: deferred to post-launch (no real modification feedback
data available in Phase 6).

**Deferred action**: with production-traffic modification events
accumulated, evaluate whether the per-modification weight contributions
reflect real-world re-modification-fraud frequency.

---

## 10. Previously-rejected weight calibration

**Status**: deferred to post-launch.

**Deferred action**: tune previously-rejected-customer weight against
real-data observation of repeat-fraud-attempt frequency.

---

## 11. Cold-start grace multiplier (0.5)

**Status**: hardcoded; FPR impact unmeasured against real traffic.

**Deferred action**: post-launch FPR-on-new-tenant evidence will inform
whether 0.5 is the right multiplier or whether the value should be
dynamic (e.g. proportional to observation count).

---

## 12. Pool-max scaling

**Status**: asyncpg pool max = 10. Phase 5D Run 2 (20-user steady state)
sustained 10,970 aggregate requests at 183 RPS / ~12ms p95 against this
ceiling without saturating the pool.

**Deferred action**: re-evaluate against production load profile. If
sustained-throughput plateau hits the pool ceiling, raise to match
real concurrency.

---

## 13. Sub-60s tenant config cache invalidation

**Status**: current TTL acceptable for Phase 6 scope (60s).

**Deferred action**: only revisit if a specific production requirement
emerges (e.g. immediate-effect tenant config changes for incident
response).

---

## 14. Case-1 replay — deferred indefinitely

**Status**: no enrichment data from the case-1 training window
(historical fraud pre-dating MaxMind + IP2Proxy database states
available to the replay environment).

**Deferred action**: no current path to validation. Re-evaluate if
historical enrichment snapshots become available.

---

## 15. Latency budget watch (Phase 6A amendment)

**Baseline shift**: Phase 5D measured ~12ms p95 on the booking endpoint.
Phase 6A.7 + 6A.8 added a synchronous UPSERT (`tenant_route_baselines`
maintenance on every booking) + a SELECT for rarity derivation, adding
~4ms p95. Post-amendment baseline: ~16ms p95.

**Monitoring thresholds** (per `docs/production-launch-checklist.md`
Phase E):
- **Yellow flag (≥50ms p95)**: investigate query performance,
  evaluate in-process cache on `tenant_route_baselines` reads.
- **Red flag (≥195ms p95)**: calibration backlog action before the
  200ms ceiling breach.

**Deferred action**: only intervenes if monitoring thresholds trigger.

---

## 16. Customer baseline cold-start ramp at production launch (Phase 7C.10)

**Status**: documented; ongoing post-launch observation.

**Observation**: At production launch all `customer_baselines` rows
start empty (`ip_asn_stats == '{}'` etc.). The new
`api_booking_from_unfamiliar_asn` rule (Phase 7C.7) has a cold-start
gate `customer_observations >= 10` inside its derivation; the rule
cannot fire until each customer has accumulated ≥10 bookings. The
new case-3b asymmetric compound (Phase 7C.2) has the inverse
relationship (cold-start gate `< 10` inside the derivation) and IS
expected to fire on brand-new-customer fraud at launch.

Detection ramp for case-2:
- Day 1: 0% case-2 detection by the new ASN rule.
- Weeks: partial detection (customers cross the 10-observation
  gate).
- Months: full detection (per-customer ASN baselines stable).

**Deferred action**: monitor `customer_baselines` population rate
during Day 1-30 (production-launch checklist Phase E). If the ramp
takes longer than expected (e.g., low-volume tenants struggle to
cross the gate), revisit either (a) lowering the gate to >= 5,
(b) seeding baselines from prior production observations (no
freight_risk data — only post-launch production observations), or
(c) adding a complementary rule that catches case-2-style attacks
on cold-start customers via different signals.

---

## 17. Tenant-bulk-import of historical bookings into customer_baselines

**Status**: deferred to post-launch architectural workstream;
explicit operator decision required.

**Context**: Phase 7's no-freight-risk-data-in-the-repo policy
prohibits embedding historical bookings into the riskd repo. A
production-tenant onboarding flow that bulk-imports the tenant's
own historical bookings (from their own systems, not from
freight_risk) would give Day-1 detection capability — case-2 rule
fires immediately on established customers — but introduces
architectural complexity (idempotency, schema mapping, replay-vs-
real distinction, audit trail).

**Deferred action**: post-launch decision. Without bulk-import,
case-2 detection capability ramps per item 16 above. With
bulk-import, the architecture needs design for: tenant data
ingest format, replay-record vs production-record discrimination
in audit logs, schema-mapping per-tenant ETL, transactional
guarantees during bulk-load, and decision-cache implications
(should bulk-imported bookings populate the decisions table?).

---

## Phase-by-phase post-launch tuning timeline

Cross-reference: `docs/production-launch-checklist.md`.

| Window | Activity |
|---|---|
| Week 1 | Observation only — Day 1-7 verification per launch checklist Phase E. |
| Week 1-4 | Observation; calibration-backlog items accumulate production-frequency data per launch checklist Phase F. No tuning yet. |
| Month 2-3 | First tuning pass per launch checklist Phase G. Per-item: confirm pattern, design intervention (weight reduction, condition tightening), staged replay if a current corpus is available, plan-mode the tuning commit. |
| Month 4-5 | Second tuning pass per launch checklist Phase H. Modification weights + previously-rejected weights become tuneable with real feedback latency. Re-evaluate cold-start grace multiplier with FPR-on-new-tenant evidence. |
| Month 5+ | Ongoing calibration; Phase 7+ scope opens. |

Tuning commits follow the same CLAUDE.md 6-step commit cycle as Phase 6 —
reviewer panel mandatory; declared breaks if any; per-commit validation.
