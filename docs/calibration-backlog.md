# Calibration backlog — post-launch tuning checklist

> **Audience**: operators and post-launch development phases driving rule
> weight, threshold, and compound-condition tuning against real production
> traffic. **NOT acted on during build phases.**
>
> Tuning happens during the 5-month post-launch observation window per
> `docs/production-launch-checklist.md` (Phase G — Month 2-3 first pass;
> Phase H — Month 4-5 second pass).
>
> Why no build-phase tuning: weights calibrated against synthetic-history
> data risk fitting the artifact of the synthesis rather than the
> production reality. Ship the rules as-shipped; observe under real
> traffic; tune against this backlog with confidence.
>
> **Status framing**: items below carry pre-launch status as of Phase 8C
> close (2026-06-05). The operator triages each item at the 5-month
> observation window mark against real production traffic, NOT against
> the synthetic-history replay corpus the backlog originated from.
>
> **Item numbering** is stable for cross-reference continuity from
> `docs/history.md` and `.ai/decisions.md`. Items appear by section
> grouping (active / parameter-tuning / architectural / observation /
> deferred / resolved), not by number.

---

## Active items

Known post-launch action paths. Re-measure against production traffic
during the 5-month observation window; design intervention if the
pattern persists.

### Item 1 — `unfamiliar_ip_country_for_origin` semantic refinement

**Status**: PARTIAL (Phase 7C.8); semantic refinement deferred.

**Observation (Phase 6C replay)**: rule fires on 7,183 / 10,000
operator-approved records — high baseline contributing to the 41%
REVIEW share on the approved corpus.

**Action landed (Phase 7C.8)**: weight reduced 0.30 → 0.15. Fire rate
intentionally preserved (the rule's pair-novelty semantics on
`(origin, ip_country)` per customer remain — legitimate freight
customers' origin expansion is the dominant pattern). Case-2
detection load shifted to `api_booking_from_unfamiliar_asn` (Item 16
context).

**Open post-launch work**: pair-novelty semantic refinement. Candidate
designs: (a) decouple origin from IP-country; (b) require a second
signal before the rule contributes. Re-measure FPR after ≥4 weeks of
production traffic; design intervention at the 5-month tuning
checkpoint if pattern persists.

---

### Item 2 — `unknown_destination_address` semantic refinement

**Status**: PARTIAL (Phase 7C.8); semantic refinement deferred.

**Observation (Phase 6C replay)**: fires on 6,482 / 10,000 approved
records. Same shape as Item 1 — high baseline; rule alone not
discriminating between fraud and legitimate novel destinations.

**Action landed (Phase 7C.8)**: weight reduced 0.20 → 0.10. Fire rate
preserved; contribution to scoring reduced.

**Open post-launch work**: compound with a value or IP anomaly before
contributing. Re-measure FPR after ≥4 weeks of production traffic;
design intervention at the 5-month tuning checkpoint if persistent.

---

### Item 6 — Case-3b detection generalization

**Status**: PARTIAL — architectural sub-pattern resolved in Phase
7C.2-3; population-baseline + case_3_compound validation deferred.

**Context**: Phase 6C measured 0/95 detection on the Roulottes Lupien
case-3b census. Phase 7C.2 added `cold_start_outbound_carrier_dropoff`
(asymmetric: customer ≠ destination; origin can match) and deleted the
symmetric `cold_start_country_triangle_with_carrier_dropoff`. Phase 7D
re-measurement confirmed 100% detection on the census post-7C.2.

**Single-customer cluster caveat carries forward**: all 95 census
records are from one customer. Cluster recall ≠ population recall.
Generalization across diverse case-3b fraud actors awaits post-launch
traffic.

**Open post-launch work**:

1. **Population baseline seeding for new tenants.** Sophisticated
   compound detection ramps up as tenants accumulate ≥100 observations.
   Revisit whether 100 is the right minimum or whether sub-100 tenants
   should default to a configurable static rarity list.

2. **`case_3_compound` empirical validation.** Targets case-3a
   (established-customer compromise); not expected to fire on case-3b
   by design (maturity gate + contaminated customer baseline).
   Validation defers to post-launch when (a) platform integration
   supplies the structured signals AND (b) case-3a-style fraud is
   observed in production.

---

### Item 16 — Customer baseline cold-start ramp (Phase 7C.10)

**Status**: ACTIVE — post-launch monitoring required.

**Context**: at production launch all `customer_baselines` rows start
empty (`ip_asn_stats == '{}'` etc.). The `api_booking_from_unfamiliar_asn`
rule (Phase 7C.7) has a cold-start gate `customer_observations >= 10`
inside its derivation; the rule cannot fire until each customer has
accumulated ≥10 bookings. The case-3b asymmetric compound (Phase 7C.2)
has the inverse relationship (`< 10` inside the derivation) and IS
expected to fire on brand-new-customer fraud at launch.

**Detection ramp**:
- Day 1: 0% case-2 detection by the new ASN rule (by design).
- Weeks: partial detection (customers cross the 10-observation gate).
- Months: full detection (per-customer ASN baselines stable).

**Open post-launch work**: monitor `customer_baselines` population rate
during Day 1-30 (production-launch checklist Phase E). If the ramp
takes longer than expected (low-volume tenants stall before the gate),
revisit: (a) lower the gate to ≥5, (b) seed baselines from
post-launch production observations, or (c) add a complementary rule
catching case-2-style attacks on cold-start customers via different
signals.

---

### Item 18 — Cold-start ramp lengthening under 7C.11 baseline gating

**Status**: DEFERRED — post-launch monitoring; intervention
trigger-based.

**Context**: Phase 7C.11 gates `customer_baselines` accumulation on
ALLOW band — REVIEW/BLOCK bookings no longer contribute to
`ip_asn_stats`, `ip_stats`, Welford accumulators. Cold-start customers
reach the ≥10 maturity gate (Item 16) slower than under pre-7C.11
behavior; delta is approximately the per-customer REVIEW-band rate.

**Expected impact**:
- Clean-traffic tenants (most bookings → ALLOW): ramp lengthening
  <5%.
- High-REVIEW-rate tenants (mature anti-fraud platforms catching
  real attacks, OR high-FPR tenants needing calibration): empirical
  5-15% range.

**Open post-launch work**: monitor `customer_baselines.value_n` growth
trajectory per tenant during Day 1-30. If a tenant's customers
consistently take >2x longer than expected to cross ≥10 observations,
that's a calibration signal — either (a) the tenant's REVIEW rate
needs upstream rule-catalogue review, or (b) the cold-start gate
threshold should be relaxed for that tenant.

Cross-references Items 1, 2 indirectly: 7C.11 reduces those rules'
fire rates because cold-start customers (below the ≥10 maturity gate)
bypass them entirely. The 7D re-measurement after 7C.11 reflects the
combined 7C.7 + 7C.8 + 7C.11 impact on FPR.

---

## Threshold and parameter tuning

Operational parameters with no current calibration evidence. Tune
during Month 2-3 (Phase G) and Month 4-5 (Phase H) tuning passes per
launch checklist.

### Item 8 — Population baseline thresholds (Phase 6A amendment)

**Current values** in `app/tenant_route_baselines.py`:
- `RARITY_MIN_OBSERVATIONS = 100`
- `RARITY_THRESHOLD = 0.02` (2%)

**Status**: DEFERRED. Phase 6C replay had no signal (empty baseline).
Tune once tenant baselines accumulate diverse legitimate routes over
the 5-month observation window.

---

### Item 9 — Modification weight calibration

**Status**: DEFERRED — no real modification-feedback data available
in Phase 6.

**Post-launch action**: with production modification events
accumulated, evaluate whether per-modification weight contributions
reflect real-world re-modification-fraud frequency. Month 4-5 second
tuning pass (Phase H).

---

### Item 10 — Previously-rejected weight calibration

**Status**: DEFERRED. Tune against real-data observation of
repeat-fraud-attempt frequency. Month 4-5 second tuning pass (Phase H).

---

### Item 11 — Cold-start grace multiplier (0.5)

**Status**: DEFERRED — hardcoded; FPR impact unmeasured against real
traffic.

**Post-launch action**: FPR-on-new-tenant evidence will inform whether
0.5 is right or whether the value should be dynamic (e.g.,
proportional to observation count).

---

### Item 12 — Pool-max scaling

**Status**: DEFERRED — re-evaluation triggers on observed pool-
saturation events; no preemptive change.

**Baseline**: asyncpg pool max = 10. Phase 5D Run 2 (20-user steady
state) sustained 10,970 aggregate requests at 183 RPS / ~12ms p95
without saturating. Raise to match real concurrency if sustained-
throughput plateau hits the ceiling.

---

### Item 13 — Sub-60s tenant config cache invalidation

**Status**: DEFERRED — current TTL (60s) acceptable.

**Post-launch action**: revisit only if a specific production
requirement emerges (e.g., immediate-effect tenant config changes
for incident response).

---

### Item 15 — Latency budget watch (Phase 6A amendment)

**Status**: DEFERRED — monitoring-driven; no scheduled tuning pass.

**Baseline shift**: Phase 5D measured ~12ms p95 on booking. Phase
6A.7 + 6A.8 added synchronous `tenant_route_baselines` UPSERT + a
SELECT for rarity derivation, adding ~4ms. Post-amendment baseline:
~16ms p95.

**Monitoring thresholds** (per launch-checklist Phase E):
- **Yellow (≥50ms p95)**: investigate query performance; evaluate
  in-process cache on `tenant_route_baselines` reads.
- **Red (≥195ms p95)**: calibration backlog action before the 200ms
  ceiling breach.

---

## Architectural workstreams

Design discussion required before implementation. Phase 9+ scope;
deferred unless launch evidence demands.

### Item 7 — Trust-suppression on mature accounts

**Pattern**: a mature legitimate customer has a low `account_prior`
(established trust). If that account is compromised, case-3a / case-3b
signals fire, but the combined score may not reach BLOCK because the
trust contribution offsets the fraud contribution.

**Classification**: architectural, NOT parameter tuning. Candidate
designs to evaluate:
- Capability-based trust (per-dimension trust: shipping behavior,
  payment history, geographic pattern — compromise one, lose one).
- Session-anomaly signals (device fingerprint change, geographic
  jump indicators) feeding a separate suppression layer.
- Asymmetric trust freeze (rapid erosion on first anomaly, slow
  rebuild).

Background context in [`docs/history.md`](history.md) (Phase 6A
case-3 detection capability discussion).

---

### Item 17 — Tenant-bulk-import of historical bookings

**Status**: ARCHITECTURAL WORKSTREAM — explicit operator decision
required before design starts.

**Context**: Phase 7's no-freight-risk-data-in-the-repo policy
prohibits embedding historical bookings in the riskd repo. A
production-tenant onboarding flow bulk-importing the tenant's own
historical bookings (from their systems, not from freight_risk) would
give Day-1 detection capability — case-2 rule fires immediately on
established customers — but introduces architectural complexity.

**Design considerations if pursued**:
- Tenant data ingest format.
- Replay-record vs production-record discrimination in audit logs.
- Per-tenant schema-mapping ETL.
- Transactional guarantees during bulk-load.
- Decision-cache implications (should bulk-imported bookings populate
  the `decisions` table?).

Without bulk-import, case-2 detection capability ramps per Item 16.

---

### Item 19 — Force-fold admin endpoint (Phase 7C.11 edge case)

**Status**: DEFERRED — intervention triggers only on observed backlog
growth.

**Context**: Phase 7C.11 holds REVIEW/BLOCK bookings in pending state
until operator feedback arrives. Edge case: feedback never comes
(operator forgot, workflow outage, team rotation losing context).
Booking stays held indefinitely, never folding to the customer
baseline.

Acceptable for v1 — operators typically catch up via normal workflow.
Held-booking population is queryable (launch-checklist Phase F
runbook entry).

**Candidate designs if backlog accumulates persistently**:
1. `POST /admin/feedback/force-fold` taking a `target_request_id` and
   applying an `approved` feedback server-side.
2. Auto-approve grace period (e.g., 90 days post-booking without
   feedback → automatic fold).
3. Keep the held state indefinitely; add an operational alert when
   the per-tenant backlog exceeds a threshold.

Decision deferred — operator preference depends on observed
production patterns.

---

## Observation-dependent

Decision pending production-traffic observation; intervention design
sketched but not committed.

### Item 20 — nD7-class fraud detection (brand-new + API + residential ASN)

**Status**: DEFERRED — post-launch tuning roadmap. Sketch below is a
candidate design, NOT a committed implementation.

**Context**: the Phase 7D case-2 measurement identified two structurally
different fraud classes within the freight_risk `gobolt-non-34x-api`
corpus:

1. **gPYG-class** (compromised established customer): pre-attack
   legitimate booking history exists. Customer baseline accumulates
   from operator-approved ALLOW bookings during the warmup window.
   The Phase 7C.7 `api_booking_from_unfamiliar_asn` rule fires
   correctly on attack records (attack ASN absent from the
   confirmed-legit baseline). **100% recall** measured post-7C.12.

2. **nD7-class** (brand-new fraud-only customer): no legitimate
   history exists. nD7's complete freight_risk presence is 6,684
   records, all labeled `reject`, all attack records, earliest
   booking same day as the attack window starts. Customer baseline
   stays empty; cold-start gate on `api_booking_from_unfamiliar_asn`
   (≥10 observations inside the derivation) prevents the rule
   from firing. **25.9% catch** measured post-7C.12 (from
   baseline-agnostic rules; reduced from 40.7% pre-7C.12 due to
   the 7C.12 geo-rule weight cuts).

The nD7-class shape is structurally different from gPYG-class:
nD7-class customers have no operator-confirmed legitimate behavior
to deviate from. The case-2 ASN-deviation rule is correctly silent
on them (it would be wrong to flag a brand-new customer's first
booking as "unfamiliar ASN" — they have no familiar ASNs by
definition). A separate signal class is needed.

**Candidate design**:
```yaml
- name: brand_new_customer_api_residential_asn
  description: |
    Brand-new customer (<10 observations) booking via API from a
    residential ASN. Targets the nD7-class case-2 fraud shape:
    fraud-only customer with no legitimate history; first
    transaction is already fraudulent; baseline-deviation
    signals cannot fire.
  condition: "is_api_booking AND customer_observations < 10 AND is_residential_asn"
  weight: 0.50
  maturity_sensitive: false
```

**Concerns before implementation**:
- Legitimate new-customer onboarding from residential ASNs exists
  (small businesses, individual operators). Weight 0.50 alone may
  cause FPR on legit cold-start traffic.
- Compound conditions (require additional corroborating signals)
  may be appropriate.
- Architectural pattern follows the case-3b cold-start design
  (Phase 7C.2): use the cold-start gate INSIDE the derivation as a
  positive signal for the fraud shape (brand-new), rather than as
  a maturity gate (mature).

**Acceptance gate**: production observation needed. The current
operator confirmation that "case-2 attacks compromise established
customers" doesn't necessarily generalize to "all case-2-class
attacks have pre-attack legitimate history." Post-launch real-data
observation will inform whether nD7-class fraud occurs in production
at meaningful volume.

---

## Deferred indefinitely

### Item 14 — Case-1 replay

**Status**: DEFERRED indefinitely. No known path to validation. No
enrichment data from the case-1 training window (historical fraud
pre-dating MaxMind + IP2Proxy database states available to the replay
environment). Re-evaluate if historical enrichment snapshots become
available.

---

## Resolved / superseded

Listed for audit-trail continuity; not actionable post-launch. Full
Phase 7C close narrative in [`docs/history.md`](history.md).

- **Item 3** — Approved-corpus FPR on `api_non_cloud_ip` +
  `non_cloud_established_account`. **RESOLVED in Phase 7C.2/7C.7**:
  both rules DELETED; replaced by per-customer
  `api_booking_from_unfamiliar_asn`. Replacement rule's FPR
  re-measurement folds into Item 16.

- **Item 4** — Approved-corpus BLOCK cluster (18/10,000 records on
  the 4-rule compound). **RESOLVED via Phase 7C.2/7C.7 rule deletions**:
  two of the four compound contributors (`api_non_cloud_ip`,
  `non_cloud_established_account`) no longer exist. Remaining-rule
  weight reductions in 7C.8 fold into Items 1, 2 monitoring.

- **Item 5** — Case-2 false negatives (10/500 ALLOW'd against the
  Phase 6C corpus). **SUPERSEDED by Phase 7D per-customer-class
  reframing**: the 98% recall measurement is from the old framing.
  Current case-2 detection measurement is in
  `docs/replay-validation.md` Phase 7D section. Item 20 covers the
  nD7-class detection gap that the new framing exposed.

- **Item 6 bullet 1** — Domestic-origin + carrier-dropoff +
  cross-border-destination sub-pattern. **RESOLVED in Phase 7C.2-3**:
  symmetric `cold_start_country_triangle_with_carrier_dropoff` DELETED;
  asymmetric `cold_start_outbound_carrier_dropoff` added. Bullets 2-3
  carry forward in Item 6 above.

---

## Timeline

Tuning happens during the 5-month post-launch observation window. See
`docs/production-launch-checklist.md` Phase G (Month 2-3 first pass)
and Phase H (Month 4-5 second pass) for the operational schedule.

Tuning commits follow the same CLAUDE.md 6-step commit cycle as
build-phase work — reviewer panel mandatory; declared breaks if any;
per-commit validation.
