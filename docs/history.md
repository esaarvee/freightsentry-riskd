# history.md — freightsentry-riskd phase-by-phase history

This document is the consolidated narrative record of the seven-phase build
that produced freightsentry-riskd from greenfield to pre-launch (Phases 1
through 7), plus the pre-launch consolidation pass (Phase 8) that the
present commit lives in. It absorbs the per-phase planning files, per-batch
and per-phase reports, the original master plan, the topic-organized
decision archive, and the replay-validation measurement narrative — all of
which were deleted in commit 8C.13 once their content landed here. The
document is appendix-style: chronological, report-voice, oriented toward
"why" rather than "what" at each phase boundary.

For the current architecture see [.ai/decisions.md](../.ai/decisions.md).
For the schema as it ships see [.ai/schema.md](../.ai/schema.md).
For the live rule catalogue see [.ai/rules.md](../.ai/rules.md).
For runtime state see [.ai/system-status.md](../.ai/system-status.md).
For the enrichment subsystem see [.ai/enrichment.md](../.ai/enrichment.md).
For replay measurement details see [docs/replay-validation.md](replay-validation.md).
For the post-launch tuning workstream see [docs/calibration-backlog.md](calibration-backlog.md).
For the launch sequence see [docs/production-launch-checklist.md](production-launch-checklist.md).

History here is read for context: how the rule catalogue grew from 14 to 81
rules, why the CAD default replaced USD at Phase 6B, what the case-3b
detection redesign in Phase 7C swapped, why the BLOCK target was retired in
Phase 7C.13, and similar archaeological questions. Day-to-day operational
questions belong against the live docs above, not against this file.

## Project ambition and cross-phase invariants

freightsentry-riskd is a real-time fraud detection SaaS for freight
aggregation platforms. The build was scoped at six weeks plus a calibration
pass, single Python service, PostgreSQL-only storage, multi-tenant from day
one via RLS and `tenant_id` columns, target ceiling 100 TPS with p95
latency under 200ms, cost ceiling CAD 1000 per month at single-tenant
production volumes. The technology stack was fixed at greenfield: Python
3.13, FastAPI, asyncpg, Pydantic v2, Alembic, structlog with CloudWatch EMF
as the production observability sink, ECS Fargate as the production
runtime. The original master plan committed to no second process, no
second language, no second storage engine, no LLM in the decision path.

A small number of invariants held across every phase from 1 through 7:

- **Single service.** FastAPI + asyncpg + Pydantic v2. No worker process,
  no message bus, no second language.
- **Multi-tenant from day one.** Every business-data table has
  `tenant_id`; every query scopes by it; Postgres row-level security is
  configured as a defensive backstop. RLS started dormant in Phase 1
  (the app connected as the Postgres bootstrap superuser, which bypasses
  RLS) and activated in Phase 5D once a non-superuser login role was
  introduced.
- **Six-step commit cycle.** Implement, validate, review, iterate,
  commit, proceed — codified in CLAUDE.md and respected throughout.
  Reviewer-panel discipline (senior + security + code-flow plus
  test/db/doc as routing demanded) was the structural defense against
  quality drift over a multi-month build.
- **Declared breaks.** Any commit that introduced a transitional state
  (a function defined before its caller, a schema column added before
  the writer wired up, a test deleted before its replacement) carried a
  `Declared breaks` subsection naming the scope and the resolving
  commit. Reviewers plan-suppressed findings inside declared scope.
- **Tests land in the same commit as the code they cover.** No
  "tests next commit" pattern.
- **Latency budget.** Under 200ms p95 for every evaluation. Phase 5
  load test enforced; Phase 6A documented the ~16ms baseline after the
  case-3 detection subsystem added ~4ms p95 to the booking commit path.
- **No weight tuning in mid-build phases.** The discipline was
  established in Phase 2 and held through Phase 6. Phase 7 explicitly
  overrode it for the pre-launch calibration pass, with documented
  rationale — the override was a controlled exception, not a drift.

The "Design Context" section of the bootstrap prompt fixed the
architectural choices: three-layer scoring (Layer 1 hard-block + Layer 2
account-prior + Layer 3 signal noisy-OR with maturity downweight), per-IP-
type decay half-lives, identity HMAC at egress, FOR UPDATE on the
read-modify-write baseline window. Phase 1 verification compared this
against two reference codebases (the sibling freight_risk SQLite-backed
research project and the FreightSentry Go service) and accepted four
substantive divergences in the scoring formula as Design-Context
authoritative. The decisions absorbed during build appear in the per-phase
sections below; the current synthesis lives in `.ai/decisions.md`.

---

## Phase 1 (Week 1) — Foundation + signal/baseline core

Phase 1 stood up the skeleton service from a pre-staged FreightSentry-
calibrated document set, wired the Layer 1 hard-block + Layer 3 signal
noisy-OR scoring path end-to-end, and shipped an initial 14-rule
catalogue. Layer 2 (account-prior + trust-score consumption) was
explicitly deferred to Phase 2; the `app/trust.py` module shipped in
Phase 1 with a callable `compute_trust_score` but no Phase 1 rule
condition read its output. Twenty-two commits landed across four
batches between 2026-05-25 and 2026-05-26.

### Batch 1A — Foundation adaptation

Batch 1A trimmed the pre-staged documentation (CLAUDE.md, the `.ai/`
family, the six reviewer-agent prompts, README, pyproject) of stale
references to Go, gRPC, Redis, and AI components that the new
single-Python-service architecture excluded. Seven commits, all doc-only,
all routed through doc-reviewer. The pre-staged docs were created during
project bootstrapping under a different architectural plan; the trim was
genuine reframing, not cosmetic search-replace. Operator amendment
`429d9f2` dropped the `FG_` env-var prefix project-wide; pydantic-settings
field names match environment variable names verbatim (`DATABASE_URL`,
`HMAC_SECRET`, etc.) and source from `.env` in dev, platform secret
manager in production. The amendment touched nine docs and re-stated the
single-source-of-truth convention.

`app/signals.py` was originally planned as a helper module containing pure
functions consumed by per-signal classes in `app/signals/`. The Python
import system would have collided with the module-as-directory pattern;
during 1A.2 the helpers module was renamed `app/signal_helpers.py` with
the STATUS row as authoritative reference. Later PLAN_PHASE_1.md text
referencing the old name was acknowledged as superseded.

### Batch 1B — Skeleton

Batch 1B brought up the skeleton: `docker-compose.yml`, Alembic with the
initial 12-table migration, FastAPI lifespan with asyncpg pool init,
structured logging via structlog, `/health` endpoint with DB ping, and
the first integration tests. Five commits including the retrospective-fix
commit (`b80fe66`) that addressed reviewer findings deferred from 1B.3
and 1B.4 when reviewer subagents returned platform-quota errors mid-
cycle.

The initial migration (`0001_initial.py`) provisioned 12 tables
(`tenants`, `enterprises`, `customers`, `users`, `shipments`,
`ip_enrichment`, `customer_baselines`, `feedback`, `api_tokens`,
`app_users`, `decisions`, `audit_log`), nine RLS policies on the
tenant-scoped tables, the `riskd_app` role with `CONNECT` and table-level
grants, nine `ux_*` unique indexes, and four `ix_*` covering indexes.
RLS policies were configured but dormant under the postgres-superuser
runtime — the gap was documented in the migration with the resolution
deferred to Phase 5D. Application-layer `tenant_id` filtering was the
active control until then.

### Batch 1C — Stub booking endpoint

Two commits delivered the stub booking endpoint at
`POST /api/v1/shipments/booking/evaluate`: Pydantic models for the
booking payload, the entity-upsert path for
`enterprises / customers / users`, decision persistence returning ALLOW
with score 0.0, and the first end-to-end integration tests including
multi-tenant payload isolation and cross-tenant PII-leak scans across
four PII fields and five columns. Test-reviewer feedback in cycle 1
extracted the `_TENANT_SCOPED_TABLES` constant and the `_cleanup_tenant`
helper into `tests/conftest.py` to lock the tenant-scoped table set
against future drift.

### Batch 1D — Signal + baseline core

Batch 1D was the substantive Phase 1 work: signal helpers (`app/signal_helpers.py`,
77 unit tests), the IP enrichment pipeline plus offline refresh script
(`app/enrich.py` + `scripts/fetch_enrichment.py`), the baseline
read-modify-write helper with FOR UPDATE concurrency control
(`app/baseline.py`, 31 tests including a concurrent-update integration
test), the trust score function (`app/trust.py`), the DSL evaluator
(`app/dsl.py`, 78 tests including the lockdown matrix), the Context
builder (`app/context.py`), the rule loader and scorer (`app/rules.py` +
`app/scoring.py`), and the end-to-end pipeline wiring with the initial
14 rules and the case-2 fixture. Eight commits.

The 14 initial rules in `app/rules.yaml` comprised two Layer 1 BLOCK rules
(`blacklisted_ip` conditioned on FireHOL Level 1 threat intel; `tor_exit`)
and 12 Layer 3 score-only rules wiring 10 initial signals — IP-class
deviation, velocity burst, dormancy-then-activity, new route, value
outlier, disposable email, dummy phone, unfamiliar origin, unfamiliar IP,
IP geolocation country.

The DSL evaluator was the highest-stakes commit in 1D. The Python AST
whitelist (`BoolOp`, `UnaryOp(Not)`, `Compare` over a fixed comparator
set, `Name` for env lookup only, `Constant` for int/float/str/bool/None,
`Load`, `And`, `Or`, `Not`) was specified ahead of implementation; any
other node raised `DSLError`. `eval` ran with `{"__builtins__": {}}` and
a frozen env dict wrapped via `MappingProxyType`. Security-auditor
explicitly verified completeness; the lockdown-matrix tests covered every
attempted bypass shape the operator could imagine.

Reviewer panel discipline broke down across batch 1D: all eight 1D commits
proceeded without reviewer-panel feedback at commit time because
subagent quota saturated, and the retrospective panel pass was the highest-
priority open item at the Phase 1/2 boundary. The pattern recurred at
the Phase 4C/4D boundary and prompted the Phase 4 retro pass
(REPORT_PHASE_4_RETRO.md, absorbed in the Phase 4 section below).

### Phase 1 outcomes

Phase 1 closed with 12 tables, 9 dormant RLS policies, the `riskd_app`
role, 9 `ux_*` and 4 `ix_*` indexes, two live endpoints (`GET /health`
and `POST /api/v1/shipments/booking/evaluate`), 14 rules, 45 fields in
the DSL `ALLOWED_CONTEXT_FIELDS` whitelist, and 267 tests passing in
1.64 seconds wall-time. `ruff check`, `mypy --strict`, and the round-trip
Alembic upgrade-then-downgrade all came back clean.

### Phase 1 amendments

`build_context` loads sequentially on the transaction connection rather
than via `asyncio.gather`. asyncpg does not multiplex operations on a
single connection; the gather attempt raised `InterfaceError`. The
baseline FOR UPDATE lock must hold across the read-modify-write window,
so the lock-holding connection cannot be split. Phase 5 load test was
flagged to revisit if parallel reads on separate pool connections were
needed; the load test in Phase 5D verified the sequential pattern at
the 100 RPS target.

Operator amendment 2026-05-25 reframed persistence as **synchronous
within the same transaction as the baseline update**, superseding the
bootstrap prompt's "background, non-blocking" framing. The original
phrase was meant to forbid a separate worker process (vs FreightSentry's
async-worker), NOT to make decision writes fire-and-forget after the
response. The amendment also dropped the persisted `shipment_volume_30d`
column from scope — 30-day window counts compute on demand via
`COUNT(*) FROM shipments WHERE booking_ts > now() - interval '30 days'`.
Rules wanting decay-weighted activity read `customer_baselines.value_n`.

The `value_n` Context field amendment (2026-05-25) exposed the
decay-weighted activity proxy through `customer_observations` for rule
consumption in lieu of the dropped 30-day count.

---

## Phase 2 (Week 2) — Trust score, account-prior, full rule library

Phase 2 wired Layer 2 (account-prior + trust-contribution + flag-prior +
maturity downweight) into the scorer, ported the FreightSentry-exclusive
rule set, added the freight_risk rules deferred from Phase 1, and grew
the catalogue from 14 to 67 rules across four batches and 27 commits
between 2026-05-26 and 2026-05-27.

### Batch 2A — Layer 2 scoring infrastructure

Five commits wired Layer 2 into `app/scoring.py`. The final-score formula
became `noisyOR(account_prior, signal_score)` with a Layer 1 BLOCK
short-circuit bypassing Layer 2 entirely (sentinel zeros on
`ScoringResult.{account_prior, signal_score, maturity}`). The locked
constants (`MAX_NEW_ACCOUNT=0.10`, `TRUST_FACTOR=0.25`, `MATURITY_K=0.30`,
`FLAG_WEIGHTS=(0.00, 0.15, 0.25, 0.35)`, `MATURITY_AGE_DAYS=180`,
`MATURITY_SHIPMENTS=50`) landed in `app/scoring_constants.py`. The choice
to make these Python module constants (not rule-YAML, not
pydantic-settings) was deliberate single-source-of-truth: rebinding
required a code change reviewed under the never-skip rule.

Four substantive divergences from FreightSentry's `scorer.go:300-415`
were documented as Design-Context authoritative:

1. **Maturity is multiplicative, not `min` of fractions.** The Design
   Context specified `maturity = clamp(age_frac, 0, 1) * clamp(ship_frac, 0, 1)`;
   FreightSentry's `accountMaturity()` returns the min. The
   multiplicative product is conservative when both factors are moderate
   — `(0.5, 0.5)` produces `0.25` vs `0.5` in FreightSentry — and
   maturity-downweight kicks in slower.

2. **Shipments fraction is linear, not log-scaled.** Design Context:
   `total_shipments / maturity_shipments` clamped to `[0, 1]`.
   FreightSentry: `log1p(shipments) / log1p(maturity_shipments)`. The
   linear form penalizes new customers harder for the first ~50
   shipments — appropriate for a per-tenant ~45K shipments/day target
   where the first 50 are a small fraction.

3. **Flag prior is a 4-tier direct lookup, not 2-tier noisy-OR.** Design
   Context: `flag_prior = FLAG_WEIGHTS[flagged_count_tier]` over four
   tiers (0 / 1-2 / 3-5 / 6+) mapping to `(0.00, 0.15, 0.25, 0.35)`.
   FreightSentry's `flagContribution()` evaluates two thresholds and
   noisy-ORs them. The 4-tier direct-lookup table is simpler and gives
   finer-grained behavior at the low-flag boundary.

4. **No customer-inheritance term.** FreightSentry optionally folds
   enterprise-level customer maturity into the formula via
   `CustomerMaturityAgeDays / CustomerMaturityShipments / CustomerInheritanceFactor`.
   The Design Context formula uses single-customer maturity only.

A `CustomerState` frozen dataclass (PII-free) carried the layer inputs.
The `risk.evaluation` structured-log event landed with Layer 2 + Layer 3
components (`account_prior`, `signal_score`, `maturity`, `trust_score`,
`flagged_count`). Twenty-eight new tests across the constants module, the
Layer 2 unit suite, and observability strengthening.

### Batch 2B — Context derivations + recipient overlap + destination_hmac

Seven commits extended `build_context` with eleven Phase 2 fields the
Phase 2C rules would consume: `customer_locked_cloud_api`,
`customer_locked_web_only`, `days_since_last_booking`, `is_new_user`,
`ip_familiarity_tier`, `ip_new_known_asn`, `is_residential_asn`,
`ip2p_threat_any`, `recipient_cross_customer_count`,
`customer_distinct_ips_30d`, `impossible_travel`. The DSL whitelist grew
45 → 56. Migration `0002` added `shipments.destination_hmac text NOT NULL`
plus an `ix_shipments_tenant_dest_hmac_booking_ts` covering index.

Three independent tests pinned tenant isolation: the SQL helper directly,
the helper via `build_context`, and the full integration through the
booking endpoint. 2C.3 hit a cross-TZ time bomb where `_seed_baseline`
used PG `current_date` against Python `date.today()` — a class of bug
that recurred in 2B.6 (hardcoded `booking_ts` "2026-05-26T10:00:00Z") and
was internalized as a Phase 2 lesson applied proactively in 2D.

A substantive in-batch correction in 2B.4 deleted false-pass unit tests
(re-implementing threshold checks inline rather than calling production)
and rewrote 21 integration tests against `build_context` output. False-
pass shapes detected by reviewers became a recurring Phase 2 pattern,
caught five times across 2B.4 / 2C.3 / 2D.2 / 2D.3 / 2D.4 and resolved
each time in test-reviewer cycle 2.

### Batch 2C — Rule additions

Nine commits grew `app/rules.yaml` from 14 to 67 rules. All conditions
referenced only post-2B whitelist fields; no production-Python changes —
a rules-only batch. The per-commit breakdown: trust-conditioned (7), then
dormancy + customer lock-in (5, including the case-1 / case-2 ATO
primary detectors), residential ASN + IP-class diversity (6),
recipient overlap (2, tier-disjoint via upper bound), velocity + identity-
novelty (11), value-anomaly + geographic + threat composites (17 after
triage, where the plan summary had said 13 — arithmetic error logged to
BUGS.md), and IP-familiarity tier + closing pieces (5). Conftest
extraction of shared rule-test helpers landed proactively before a second
copy would have surfaced as a D3 finding.

Four candidates triaged out of Phase 2C scope and documented + audit-
tested: `threat_intel_level1` (Phase 1 BLOCK already covers),
`outside_allowed_country` (Phase 4 tenant-config dependency),
`unknown_email/phone_for_customer` (Phase 3 feedback endpoint), and
`user_ip_rotation_*` (semantic mismatch between Context fields and
source).

Tuned thresholds carried from freight_risk and pinned in 2D.1 unit tests:
`cadence_anomaly` z > 6 (not z > 4; weekend false-positive avoidance),
`velocity_spike_daily_api` > 50 (not 5000), `residential_asn_high_velocity`
> 15 (not 5), `ip_familiarity_tier` /24-only family-familiar (no
"cloud + ASN" shortcut).

### Batch 2D — Threshold audit + case fixtures + canonical BLOCK assertion

Five test-only commits locked the tuned-threshold values, synthesized
case-1 (dashboard ATO), built the Layer 2 integration matrix, and lifted
the case-2 BLOCK assertion to canonical Phase 2 success criterion.

The case-1 fixture (30-shipment burst, single VPN IP, established cloud
customer) produced BLOCK from shipment 0 with `signal_score ~0.93` —
VPN + threat_level2 + intercontinental + ip_fully_new compound. A
compound-evidence guard ensured every BLOCK had at least 2 fired rules.
The case-2 fixture (API booking from unfamiliar residential IP against a
cloud-API-locked customer) BLOCKed end-to-end with six compound rules
firing — the **canonical Phase 2 success criterion**.

A 6-rule compound assertion in 2D.4 (replacing a 2-rule-only earlier
version) caught "BLOCK reached for the right reasons" rather than just
"BLOCK reached." 2D.2 dropped a vacuous assertion (d) that passed at
`first_block_idx=0` regardless of evidence; switched a 4-IP velocity
fixture to single-IP to make `ip_velocity_high_ui` reachable; added a
try/finally cleanup for the global `ip_enrichment` table not covered by
`_TENANT_SCOPED_TABLES`.

### Phase 2 outcomes

Phase 2 closed with 67 rules, 56 DSL whitelist fields, 432 tests passing
(+158 net), and one migration added (0002 destination_hmac). The case-1
and case-2 BLOCK assertions both held. The HMAC-at-egress invariant was
preserved: `destination_hmac` computed via `signal_helpers.hmac_hex` at
the same point as `email_hmac` / `phone_hmac` in `app/api/booking.py`.

A few architectural commitments landed during Phase 2: no weight tuning
in mid-build phases (calibration deferred to Phase 6 staging replay),
test fixtures extend rather than tune (when 2D.4 found the case-2
customer wasn't satisfying the lock-in gate, the fix was to extend the
seeded baseline by adding `channel_hist`, not to tune the lock-in rule's
threshold), and the implicit-USD currency assumption was documented but
deferred to Phase 4 for proper resolution.

The full rule progression was: Phase 1 14 rules → Phase 2A no change →
Phase 2C 2C.1=7 / 2C.2=5 / 2C.3=6 / 2C.4=2 / 2C.5=11 / 2C.6=17 /
2C.7=5 = 53 additions = 67 total. The FreightSentry-port set
contributed 11 trust-conditioned rules + 3 dormancy + 2 customer-lock-in
+ 1 residential proxy farm. The freight_risk base contributed recipient-
overlap, additional novelty / lock-in / dormancy rules, and the
above-mentioned tuned thresholds.

---

## Phase 3 (Week 3) — Modification + feedback + tenant scoping

Phase 3 added the modification endpoint, the feedback endpoint, a
multi-tenant scoping audit, and the currency-deferral decision that
Phase 4B would resolve. Four batches and 27 implementation commits across
2026-05-27 and 2026-05-28. Phase 3 also absorbs the Phase 4 retroactive
reviewer-panel pass (REPORT_PHASE_4_RETRO.md) lessons that the original
retro doc captured.

### Batch 3A — Modification endpoint stack

Nine commits (`5d26f8d` through `dbcff67`) delivered the modification
endpoint at `POST /api/v1/shipments/modification/evaluate`. The work
split: migration `0003` added `decisions.request_type` as a discriminator
with `DEFAULT 'booking'` retained (eliminated declared break; safer than
dropping post-backfill) and the supporting 3-column index
`(tenant_id, request_type, created_at)`; Pydantic models for
`ModificationRequest` and `ModificationResponse`; DSL whitelist extended
by 6 fields + dormancy invariant; `build_modification_context` plus three
pure helpers (time bucket, magnitude, direction); modification velocity
SQL helpers; the endpoint route plus the booking endpoint patch for
symmetric idempotency and `UniqueViolationError` → 409 collision safety;
eight modification rules plus booking-path defaults and 25 unit tests;
end-to-end modification flow integration tests.

The eight modification rules added to `app/rules.yaml` brought the count
67 → 75. Weights were operator-judgment-based (no reference codebase has
modification-specific rules — freight_risk and freightcom-risk both lack
the surface) and anchored to Phase 2 weight bands for similar-severity
rules:

| Rule | Weight | Maturity-sensitive | Rationale band |
|---|---|---|---|
| `modification_within_30_min_value_increase` | 0.65 | no | hard signal — value-jacking immediately after booking |
| `modification_destination_change_pre_pickup` | 0.55 | yes | re-routing pre-pickup is classic re-shipping fraud |
| `modification_high_velocity_1h` | 0.70 | no | sustained-rate signal regardless of customer age |
| `modification_high_velocity_24h` | 0.45 | yes | softer band; some operators batch-edit |
| `modification_low_trust_customer` | 0.55 | no | compound: low trust × destination change |
| `modification_dormant_customer` | 0.60 | yes | case-1 ATO pattern applied to modification |
| `modification_recipient_change_to_unfamiliar` | 0.40 | yes | soft signal; recipient changes normal at low rate |
| `modification_destination_change_residential_asn` | 0.35 | yes | compound destination + ASN signal |

Calibration commitments: every weight was a candidate for Phase 6 staging
replay adjustment based on observed precision/recall.

Booking-path safety: `build_context` populated the six modification
fields with neutral defaults (`modification_type='none'`, magnitudes and
velocities zero, time bucket widest, direction unknown) so the DSL
evaluator could resolve every Name reference at evaluation time. The
`'none'` literal matched no enum value the modification rules conditioned
on, so the rules were structurally dormant on the booking path.
`test_modification_rules_dormant_under_booking_path_defaults` pinned the
invariant. The `BOOKING_PATH_MODIFICATION_DEFAULTS` Final[dict] constant
eliminated production/test fixture drift.

Reviewer panel ran roughly 50% second-cycle rate across 3A — the steepest
learning curve in Phase 3. Lessons converged into 3B-applied discipline
(cast at DB-to-Pydantic boundary, dual tenant filters, UniqueViolationError
handling, exhaustively-tested helper extraction).

### Batch 3B — Feedback endpoint stack

Eight commits (`5ac1eaf` through `20f6bee`) delivered the feedback
endpoint at `POST /api/v1/shipments/feedback`. Migration `0004` dropped
and recreated the feedback table to the bootstrap shape and added
`shipments.email_hmac` and `phone_hmac` columns to enforce the HMAC-at-
egress invariant for the new PII surface. The endpoint shipped with a
two-tier idempotency model: per-POST `request_id` dedup + label
monotonicity. ALL seven implementation commits passed reviewer panel in a
single cycle — the 3A-learned discipline applied preemptively.

Four previously-rejected rules landed in 3B.5 (`065aff6`), bringing the
catalogue 75 → 79. Weights were ported from freight_risk:

| Rule | Weight | Maturity-sensitive | Source |
|---|---|---|---|
| `email_previously_rejected_for_customer` | 0.60 | yes | freight_risk catalogue |
| `phone_previously_rejected_for_customer` | 0.60 | yes | freight_risk catalogue |
| `origin_previously_rejected_for_customer` | 0.70 | yes | freight_risk catalogue |
| `ip_previously_rejected_for_customer` | 0.70 | yes | freight_risk catalogue |

Origin and IP carried higher weight than email/phone — physical-address
and source-IP reuse after a prior rejection is a stronger fraud signal
than contact-info reuse (which can be a legitimate operator typo or a new
use of the same person's email).

Booking-path dormancy: `build_context` populated the four fields as
`False` for any customer whose baseline had no prior rejections — pure
dict lookups via 3B.4 (no SQL). The rules were structurally dormant for
clean baselines; `test_previously_rejected_rules_dormant_under_clean_baseline`
pinned the invariant.

3B.7 caught and fixed a feedback endpoint race condition: the tier-2
monotonicity SELECT had been running BEFORE `FOR UPDATE` on
`customer_baselines`. The fix moved the SELECT after the lock acquisition.
Surfaced by the same commit that introduced the test — a one-commit
discover-and-fix cycle that became a template for 4A.6 and 4D.4.

### Batch 3C — Multi-tenant scoping audit

Four commits (`e94ddc8` through `26325b6`) delivered the audit and
verification work. `docs/security-audit-rls-phase-3.md` inventoried 36
asyncpg call sites; zero queries with potentially missing scope. A
comprehensive cross-tenant integration test sweep covered all four
endpoints (9 tests). 3C.3 added a non-superuser RLS canary that proved
policies would enforce under the Phase 5 role transition target — eight
parametrized tests verifying that policies fire when the runtime role
lacks `BYPASSRLS`. The canary became the foundation for Phase 5D's
runtime role transition.

3C.1 cycle 1 caught two material accuracy issues (count off-by-one and
wrong file:line reference); both fixed in cycle 2 to PUBLISH.

### Batch 3D — Currency decision + Phase 3 wrap

Four commits delivered the currency-implicit-USD decision documented in
`.ai/decisions.md` (deferred per-currency normalization to Phase 4), a
cross-batch chain integration test as the canonical Phase 3 value demo,
a maturity + modification composition test, and the per-batch + aggregate
Phase 3 reports.

### Phase 3 outcomes

Phase 3 closed with 79 rules (+12), 675 tests (+243), 66 DSL
ALLOWED_CONTEXT_FIELDS (+10), 4 migrations (+2), 6 endpoints (+2 from
admin), one production race fix (feedback endpoint), and one BUGS.md
tracked follow-up (Phase 5 widening of `ux_decisions_tenant_request`
UNIQUE to include `request_type`, which landed as migration 0007 in
Phase 5A).

### Phase 4 retro absorption

The Phase 4 retroactive reviewer pass (REPORT_PHASE_4_RETRO.md, dated
2026-06-01 same-day after Phase 4 wrap) was triggered by the operator's
request for per-commit retroactive review of Phase 4C and Phase 4D
commits, which had shipped under per-batch checkpoint mode without
reviewer panels. Six retro-fix commits landed: `2a716b5` (4C.4 test
hardening — `_maturity_via_endpoint` dead helper removed; vacuous
assertions tightened; "Tor exit IP" misnomer corrected to
`blacklisted_ip`; loose upper bounds tightened from `< 0.3` to `< 0.05`;
imports hoisted out of function bodies); `c097df3` (4D.1 tautological
composition test); `e31ffbd` (4D.2+3 HMAC truncation contract
divergence); `9d206d0` (4D.4 single-predicate vs dual-predicate test
mismatch); `af79295` (4D.5 stale line refs); `557962f` (4D.6 test-count
reconciliation).

The retro generalized into the rule that **per-commit reviewer panels
run regardless of per-batch checkpoint mode**, codified in CLAUDE.md and
respected through Phase 5/6/7. No subsequent batch shipped reviewer-skip
commits outside the explicit triage-gate trivial / lightweight ladder.

The retro also surfaced the recurring "false-pass" shape: an assertion
that passes when production code is broken because the assertion is
weaker than the contract. Test-reviewer cycle 1 became the standard
defense. By Phase 6 the pattern was largely absent — implementers
internalized the lesson during 3A → 3B handoff and re-applied it
throughout.

---

## Phase 4 (Week 4) — Per-tenant config + cold-start + admin reads

Phase 4 introduced `TenantConfig` as the per-tenant override surface,
resolved the currency-implicit-USD debt from Phase 3D into per-currency
`value_caps`, added the cold-start grace mechanism, and shipped two
read-only admin endpoints. Four batches and roughly 29 implementation
commits on 2026-06-01.

### Batch 4A — TenantConfig foundation

Seven commits (`63628eb` through `ab77d76`) delivered:

- `TenantConfig` Pydantic v2 model in `app/tenant_config.py` (the
  Phase 4 prompt path; superseding the earlier `app/config_tenant.py`
  reference) with `model_config = ConfigDict(extra="forbid", frozen=True)`.
- `parse_config_jsonb` helper (25 unit tests).
- `load_tenant_config(conn, tenant_id)` + migration `0005` adding
  `tenants.updated_at timestamptz NOT NULL DEFAULT now()` for staleness
  tracking (8 tests).
- `build_context` and `build_modification_context` signature extension
  with required `tenant_config` (declared break, resolved in 4A.4).
- Loader wired into the three endpoints + 13+ test-fixture call sites
  updated.
- `scripts/tenant_onboard.py`: idempotent UPSERT-by-name with
  `pg_advisory_xact_lock(hashtext(external_id))` to serialize concurrent
  runs (since `tenants.name` had no UNIQUE constraint at the time).
  `set_config('app.tenant_id', tenant_id, true)` set before any
  `api_tokens` query so the script worked under the production non-
  superuser `riskd_app` role. `--rotate-token` REVOKED prior tokens via
  in-transaction DELETE before issuing a new one.
- 12 integration tests bundled with the 4A.6 production bug fix:
  `DELETE...RETURNING count(*) OVER ()` was invalid PostgreSQL syntax,
  caught by the integration test (the 3B.7 precedent: bundle the
  one-commit discover-and-fix cycle).

Architectural choices recorded in `.ai/decisions.md`:

- **Column reuse, not addition.** `tenants.config` (already in
  `0001_initial.py:42` as `jsonb NOT NULL DEFAULT '{}'`) was the storage
  column. The Phase 4 prompt initially referenced `tenants.config_json`
  as a new column — that was a drafting inconsistency with the pre-
  existing schema. 4A reused the existing column.
- **Per-request fresh load.** No caching in Phase 4; Phase 5B added the
  60s in-process TTL cache as carry-forward.
- **JSONB codec discipline.** asyncpg returns JSONB as `str` by default;
  the loader's cast-at-boundary pattern handled both codec paths so a
  future codec registration would be non-breaking.
- **Carry-forwards** noted in 4A: `UNIQUE (name)` on `tenants` would
  replace the advisory-lock pattern (BUGS.md candidate); destructive
  `tenants.config` overwrite on re-run is intentional; private
  `_hash_token` import suggested promoting to a public helper.

The 17 reviewer corrections across 4A were largely small alignment
issues — none load-bearing — and one ruff version drift in 4A.4 caused a
22-file scope creep, reverted via `git checkout HEAD --` with a BUGS.md
entry filed.

### Batch 4B — Currency normalization

Seven commits (`6c39a59` through `d2c4437`) resolved the Phase 3 currency
deferral. The seven Phase 2 rules with implicit-USD thresholds
(`vpn_high_value` > 1000, `low_trust_high_value` > 1000, `flags_with_value`
> 2000, `threat_intel_high_value` > 2000, `ip2p_threat_high_value`
> 2000, `high_value_new_user` > 5000, `absolute_high_value` > 10000)
were rewritten to consult per-currency-per-tier thresholds from
`tenant_config.value_caps`.

The implementation:

1. `BookingRequest.shipment.currency` and `ModificationRequest.currency`
   added as optional `str` fields with `"USD"` default. Validation:
   3-letter uppercase ISO 4217 shape at the Pydantic layer; allowed-list
   check at request time against `tenant_config.allowed_currencies` (400
   if not in list).
2. `TenantConfig.value_caps: dict[str, dict[str, float]] | None` carried
   per-currency-per-tier thresholds in a 4-tier scheme:
   `high / new_user / medium / low` matching the 4 distinct thresholds in
   the 7 rewritten rules.
3. `DEFAULT_VALUE_CAPS = {"USD": {"high": 10000, "new_user": 5000,
   "medium": 2000, "low": 1000}}` matched Phase 2 hardcoded thresholds.
   USD-default tenants saw zero behavioral change.
4. `resolve_value_caps(tenant_config, currency)` resolved per-request,
   falling back to USD defaults with a `tenant_config.value_caps.fallback`
   structured warning (`metric=True` for Phase 5C EMF) if the tenant had
   an allowed currency without a matching `value_caps` entry.
5. The 7 rules were rewritten to consult
   `shipment_value_threshold_<tier>` Context fields populated in
   `build_context`. The modification rule
   `modification_within_30_min_value_increase` was NOT rewritten — its
   `modification_magnitude > 0.2` is a fraction, currency-independent.

**Currency conversion via a rates table was explicitly rejected**:
requires maintained rates data with refresh cadence; float arithmetic
against decay-weighted Welford accumulators introduces compounding
precision drift; per-currency thresholds are operator-tunable per tenant
via `value_caps` and require no daily upkeep. Cross-currency risk
aggregation can be revisited in v2 if demanded; out of scope for v1.

Case-1 and case-2 BLOCK assertions held post-rewrite — the regression
gate (4B.6) confirmed USD-default tenants saw zero behavioral change.
The 15 reviewer corrections across 4B were the highest-blast-radius batch
in Phase 4.

A note on the later CAD pivot: Phase 6B re-keyed `DEFAULT_VALUE_CAPS`
from `"USD"` to `"CAD"` with the same numeric thresholds, and
`DEFAULT_ALLOWED_CURRENCIES` from `["USD"]` to `["CAD"]`, as a CAD-
default switch for the Canadian operator default. The numeric thresholds
were unchanged — the switch was a default-currency pivot, not a tuning.
Phase 4B's RESOLVED status persisted; the 6B amendment was within scope.

### Batch 4C — Cold-start enforcement

Five commits (`57b12da` through `5290eac`) added per-tenant maturity
overrides and the cold-start grace mechanism. `app/scoring.py::score`
consulted `tenant_config` for three Layer 2 + Layer 3 maturity constants:

| Constant | Override field | Project default |
|---|---|---|
| `maturity_age_days` | `tenant_config.maturity_age_days` | 180 |
| `maturity_shipments` | `tenant_config.maturity_shipments` | 50 |
| `maturity_k` | `tenant_config.maturity_k` | 0.30 |

`None` on a TenantConfig override meant "use project default from
`app/scoring_constants.py`". The constants module remained source of
truth; TenantConfig overrides on top.

The cold-start grace mechanism: `tenant_config.cold_start_grace_days`
(default 0; disabled) — during the grace window after tenant onboarding
(measured from `tenants.created_at`), the maturity formula multiplied its
computed value by 0.5. After the window, no multiplier. Rationale: a
newly-onboarded tenant has no accumulated baselines, so maturity-sensitive
rules might fire too aggressively on legitimate first customers; the 0.5
multiplier softened scoring during the grace window, biasing toward
REVIEW rather than BLOCK while the tenant built baselines.

The 0.5 multiplier was hardcoded (not tenant-configurable); Phase 6
staging replay was flagged to measure FPR impact and revise. Per-customer
cold-start (a customer new to a mature tenant) was unaffected — handled
by Layer 2 base_prior already.

Composition table (maturity-sensitive rule weight 0.6):

| Maturity state | m | K=0.30 effective weight |
|---|---|---|
| Mature (post-grace, ≥180 days, ≥50 shipments) | 1.0 | 0.60 |
| Grace-active, mature customer (m_raw=1.0) | 0.5 | 0.51 |
| Brand-new at default tenant (m_raw=0.0) | 0.0 | 0.42 |

Layer 1 invariance: both per-tenant maturity overrides and cold-start
grace were bypassed when a Layer 1 BLOCK rule fired. Pinned by
`test_layer_1_short_circuit_does_not_consult_tenant_config` (unit) and
`test_overrides_do_not_affect_layer_1_block` /
`test_grace_does_not_affect_layer_1_block` (integration).

A production-impact bug surfaced during 4C.4 integration tests: the
customer-upsert `external_id` mismatch silently bypassed seeded mature
customers, fixed by making `_booking()`'s `customer` kwarg required. The
retro pass surfaced six additional test-quality findings addressed in
`2a716b5`.

### Batch 4D — Admin endpoints + audit + wrap

Five commits (`b4ac3d5` through `3bdbe87`) delivered the read-only admin
surface:

- `require_admin_role` dependency in `app/auth.py` (checks
  `auth.role == "admin"`, returns 403 otherwise).
- `GET /api/v1/admin/decisions/{request_id}` — full decision detail +
  linked shipment data (city + country only; full address NOT surfaced).
- `GET /api/v1/admin/customers/{external_id}/baseline` — customer record
  + truncated baseline (stat-dicts top-10 by `n` desc + `total_count` +
  `truncated` flag).
- 3C.3 RLS canary extended with three admin endpoint SQL-pattern tests.
- `docs/security-audit-rls-phase-4.md` published (later superseded by
  `docs/security-audit-rls-phase-5.md`).

Authorization sourced `auth.role` from `api_tokens.role` (Phase 1 schema).
`app_users.role` existed but was not wired to auth in Phase 4 (Phase 5+
was flagged for multi-user admin model). Cross-tenant lookups returned
404 — hides existence per security-by-default convention. Admin write
endpoints were out of scope for v1; v2+ may introduce a separate admin
write surface with workflow approvals.

Implicit entity registration: customer / enterprise / user records auto-
upserted from the first booking payload that referenced them. Booking
payload carried optional metadata (registered_address, business_name,
enterprise_id, etc.) populating the records on first sight and updating
on subsequent bookings.

### Phase 4 outcomes

Phase 4 closed with rule count unchanged at 79 (7 rules rewritten in 4B,
no count change); 852 tests (+177); 71 ALLOWED_CONTEXT_FIELDS (+5 in
4B.4); 5 migrations (+1: `tenants.updated_at` in 4A.2); 6 endpoints
(+ admin × 2); one production bug fixed pre-launch (the
`DELETE...RETURNING count(*) OVER ()` syntax error). The Phase 4D audit
doc had zero queries with potentially missing scope.

USD-default tenants saw zero behavioral change from Phase 3. Case-1 and
case-2 BLOCK assertions held post all four batches — the Phase 4
regression gate.

---

## Phase 5 (Week 5) — Observability + security hardening + load test + deploy infra

Phase 5 hardened the runtime for production: non-root container, `uv.lock`
as dependency lockfile, `last_used_at` writer on `api_tokens`, widened
`decisions` UNIQUE to include `request_type`, the 60s in-process TTL
cache fronting `load_tenant_config`, CloudWatch EMF observability backend,
runtime role transition from postgres-superuser to `riskd_app_login`
(making RLS actually enforce at runtime), and the load test that validated
the 100 RPS / p95<200ms target. Four batches, roughly 28 commits, started
2026-06-02 and wrapped 2026-06-03.

### Batch 5A — Foundational hardening

Six commits delivered the multi-stage hardening pieces. The `uv.lock`
landed as source of truth, with the pre-commit pin matching the lockfile
and pip-at-runtime in the Dockerfile. Non-root container user (UID 1000)
shipped. `last_used_at` was added to `api_tokens` with a supporting
covering index; the auth.py UPDATE ran inside the auth-dependency
transaction with autocommit, persisted across pool connections. Migration
`0007` widened the `decisions` UNIQUE constraint from
`(tenant_id, request_id)` to `(tenant_id, request_type, request_id)` —
resolving the BUGS.md follow-up from Phase 3 where booking and
modification share the request_id namespace.

5A.5 caught and fixed a bare `try/except Exception: pass` swallowing the
regression risk on the invalid-token path; 5A.7 reworked a misleading
HTTPException detail string saying "booking-modification namespace
collision" — exactly the case that was no longer a collision post-0007.
Both fixes converged in cycle 2.

### Batch 5B — Tenant-config caching

Five commits delivered `app/tenant_config_cache.py` wrapping
`load_tenant_config`. The cache is an in-process dict keyed by
`tenant_id`, value `(TenantConfig, loaded_at_monotonic)`. TTL is 60
seconds, documented and operator-visible. Per-process scope: multi-worker
uvicorn deployments each carry their own cache, with TTL bounding
divergence at 60s. Reads are lock-free dict lookups followed by a TTL
check (`time.monotonic()` source via a `_now()` seam — module-level so
tests can mock without poisoning asyncio's internal `time.monotonic`
reads). Misses serialize per-tenant via `asyncio.Lock`; misses for
different tenant_ids do NOT serialize against each other. Per-tenant lock
creation uses `dict.setdefault(tenant_id, asyncio.Lock())` — atomic under
CPython GIL.

Invalidation is TTL-only for v1. A config write via
`scripts/tenant_onboard.py` (or future admin write endpoints) takes up
to 60 seconds to propagate to all workers. The staleness window was
acceptable because tenant config changes are operator-initiated narrowing
/ widening of an authenticated tenant's own settings — never a cross-
tenant security boundary. Explicit invalidation was deferred to Phase 5+
if a sub-60s propagation requirement emerged (version-bump signal on
`tenants.config_version` or pub/sub from the onboarding script).

`LookupError` (tenant missing) propagated and was NOT cached, avoiding
the "tenant onboarded but locked out for 60s" race. Cache hit/miss events
became structured-log entries with `metric=True`; 5C's EMF formatter
consumed them.

5B.1 test-reviewer cycle 1 caught 12 of 18 spec families untested; the
`_now()` seam was discovered during initial debug. Parametrized test over
METRIC_SPECS + missing-coverage tests added in cycle 2.

### Batch 5C — Observability backend

Five commits wired the CloudWatch Embedded Metric Format (EMF) backend
into the structlog chain. The namespace is `FreightSentry/RiskD` (single
namespace for all v1 metrics). The discriminator is the `metric=True`
keyword on the structured-log call site — cheap, opt-in, doesn't rename
any existing event. The `emf_processor` short-circuits when the keyword
is absent, so non-metric logs flow through unchanged.

The `MetricSpec` table in `app/observability.py` was the single source of
truth. Each event family declared dimensions (low-cardinality grouping
keys; CloudWatch hashes the tuple per metric point), metrics (numeric
measurements with CloudWatch units `Count` / `Milliseconds` / unitless
for normalized scores), and `synthetic_count` flag for events that fire
as "this happened once" without an inherent numeric payload.

`triggered_rule_count` was DERIVED in the processor from
`len(triggered_rules)` for the two evaluation events. `request_id` was
structurally incapable of being promoted to a dimension — the processor
reads dimensions exclusively from `MetricSpec.dimensions`; it never
iterates `event_dict` to discover keys. End-to-end test enforced this
with a positive control on the regular log field.

An unknown event (`metric=True` whose name was not in `METRIC_SPECS`)
passed through with a one-shot stderr warning, NOT silently dropped —
forward-compat for new metric=True call sites that predated their
`MetricSpec` entry.

5C.1 senior-reviewer cycle 1 caught missing `MetricSpec` entries for
`admin.decision_lookup` and `admin.customer_baseline_lookup`; added in
cycle 2. The Phase 6 ECS Fargate wire-up consumed stdout JSON via the
CloudWatch Logs agent; Phase 5C delivered the formatter, Phase 6 wired
the agent.

### Batch 5D — Runtime role transition + load test + audit

Seven commits delivered the highest-risk Phase 5 work. Migration `0008`
created `riskd_app_login` as a LOGIN role without `BYPASSRLS`. The
auth-time `SELECT ... FROM api_tokens WHERE token_hash = $1` ran BEFORE
the endpoint could set `app.tenant_id`; with the new role, that SELECT
failed because `app.tenant_id` was unset → RLS filtered all rows out →
auth failed. Migration `0009` resolved the chicken-and-egg by DROPping
RLS on `api_tokens` and `app_users` — auth tables that need to be
readable before the tenant context exists. The Phase 8A squash later
collapsed these into a single foundation migration that never enabled RLS
on the auth tables.

The 5D.2 fixture refactor was the highest-risk commit of the phase. The
plan section's "expected failures" listed missing tenant filters but did
not anticipate the auth-vs-tenant-discovery ordering. First-attempt
validation surfaced 394 issues (59 failed + 335 errored under the new
role). Operator escalation followed via the STATUS.md `Unforeseen /
checkpoints` row: options (a) test-fixture refactor to RLS-aware seeding
patterns, (b) selective `SET LOCAL ROLE postgres` carve-outs, or (c)
defer to Phase 6. Operator chose option (a); a 22-file commit landed
after the refactor. The Phase 4 retro lesson (per-commit reviewer panels
regardless of per-batch checkpoint mode) was respected through the
escalation; the cycle 2 retro commit addressed the missing
`ALEMBIC_DATABASE_URL` in `docker-compose.yml`'s `app.environment` block
that broke local `alembic upgrade head`.

`docs/load-test-phase-5.md` recorded the load-test methodology
(`scripts/load_test.py`, `scripts/measure_baseline.py`), the
sustained-RPS measurement (10,970 aggregate requests at 183 RPS), and
the p95~12ms baseline with zero errors and zero RLS violations across
23,720 requests. The Phase 6E reviewer originally cited "1100 successful
checks" — that miscitation was corrected to the actual 10,970 / 183 RPS
figure during the 6E.1 review.

`docs/security-audit-rls-phase-5.md` became the current security baseline
listing eight carry-forward items for the Phase 6 audit. Notable
finding from the security-auditor: the `api_tokens` RLS-drop created a
defense-in-depth gap, documented in the audit doc.

### Phase 5 outcomes

Phase 5 closed with 918 tests passing (+66 net, with one intentional drop
from migration 0009's canary `api_tokens` param), four new migrations
(0006-0009), two new `app/` modules (`tenant_config_cache.py`,
`observability.py`), two new scripts (`measure_baseline.py`,
`load_test.py`), three new docs (`observability.md`,
`load-test-phase-5.md`, `security-audit-rls-phase-5.md`). Three BUGS.md
entries resolved in 5A; five deferred to Phase 6 or future cleanup.
Reviewer-panel discipline held throughout — no panel-skip events on
standard-path commits.

Phase 6 starting prerequisites carried forward: multi-stage Dockerfile to
strip `build-essential` from runtime; rotate `riskd_app_login` password
from AWS Secrets Manager; ECS Fargate task def with vCPU/pool/memory
tuned; CloudWatch Logs agent transport; real enrichment data ingestion
(MaxMind GeoLite2, IP2Proxy PX11, FireHOL, cloud CIDRs); case-1 + case-2
production replay against real data; RDS provisioning + Secrets Manager
wire-up; `.env` host-vs-container `DATABASE_URL` split.

The 5D fixture refactor saga became the canonical Phase 5 cautionary
tale: scope estimates for cross-cutting refactors that touch many test
files are systematically too low; the operator escalation path
(STATUS.md row + options menu) became the standard response.

---

## Phase 6 (Week 6) — Deploy artifacts + fixture replay + case-3 detection

Phase 6 delivered case-3 fraud detection capability (case-3a established-
customer compromise + case-3b brand-new-customer fraud), pivoted the
default currency from USD to CAD, executed the strict-enumeration replay
validation against three corpora (10,000 approved + 500 case-2 + 95
case-3b), produced the deployment-ready artifact set (multi-stage
Dockerfile, ECS task definition, IAM policies, AWS runbook, smoke test,
three GitHub Actions workflows), and seeded the post-launch calibration
backlog. Five batches and 29 commits on 2026-06-03.

### Batch 6A — Case-3 detection capability

Nine commits (original 6A.4 was renumbered to 6A.10 and 6A.5-6A.9 inserted
during the mid-batch case-3b amendment expansion).

Two distinct threat shapes share the "case-3" name:

- **Case-3a** (established-customer compromise). An existing customer with
  a legitimate transaction baseline gets compromised; attacker uses
  stolen credentials to ship under the customer's identity to a fraud
  destination via carrier-facility dropoff. The customer's own history
  is the deviation anchor — the attacker ships from carrier-dropoff
  origin AND through a route the customer does not normally use AND from
  a previously-unseen IP. Detected by `case_3_compound` (weight 0.70,
  maturity_sensitive).

- **Case-3b** (brand-new-customer fraud). The customer is itself
  fraudulent from the first booking; no legitimate prior history. The
  Roulottes Lupien 95-record cluster is case-3b. Detected initially by
  two complementary compounds:
  - `cold_start_country_triangle_with_carrier_dropoff` (simple; weight
    0.65; brand-new customer ships outside their declared country in
    BOTH origin AND destination with carrier-dropoff). This rule was
    **DELETED in 7C.2** after the 6C measurement showed 0/95 detection
    on the Roulottes Lupien census; the asymmetric outbound replacement
    landed in the same atomic commit.
  - `cold_start_population_baseline_rare_with_carrier_dropoff`
    (sophisticated; weight 0.70; brand-new customer ships a route rare
    in the tenant's customer-base population with carrier dropoff).

The structured-field architectural pattern became the durable template
for case-N detection. Two structured signals supply detection inputs from
the booking platform:

- `BookingRequest.shipment.origin_via_carrier_dropoff: bool` — booking
  payload field indicating the shipment was dropped at a carrier
  facility rather than picked up from the origin address.
- `BookingRequest.customer.registered_country: str | None` — the
  customer's declared country (ISO 3166-1 alpha-2). The platform
  integration supplies this at booking time; the field validates
  `^[A-Z]{2}$` at the Pydantic layer. The same validation extends to
  `Address.country` so the `f"{origin_country}||{destination_country}"`
  composite-key pattern in `lane_stats` / `country_route_stats` cannot
  collide via crafted `||`-containing country strings.

Address-string parsing was explicitly rejected: a regex/split parser on
`customer.registered_address` was the cheap path with no schema change,
but format variation across users / forms / platforms makes parsers
silently unreliable. Structured field is the principled fix.

Five new Context fields landed (71 → 76):
`origin_via_carrier_dropoff`, `shipment_route_unfamiliar_for_customer`
(derived from `customer_baselines.country_route_stats`),
`customer_registered_country`, `customer_country_triangle_mismatch`
(derived via `_triangle_mismatch` in `app/context.py`; later replaced),
`shipment_route_rare_for_tenant` (derived via `derive_route_rarity`
querying `tenant_route_baselines`).

Three new rules brought the catalogue 79 → 82.

The new subsystem — tenant route population baseline:

- Table `tenant_route_baselines` with composite PK
  `(tenant_id, customer_country, origin_country, destination_country)`
  and `observation_count bigint` + `last_updated timestamptz`. RLS
  policy `tenant_isolation` USING
  `(tenant_id = current_setting('app.tenant_id')::int)` active under
  `riskd_app_login` per the Phase 5D role transition. The PK's
  leading-column tenant_id provides the prefix scan for both the 6A.7
  UPSERT and the 6A.8 tenant-wide SUM aggregation — no separate
  single-column index.
- Migration `0011` added the table + RLS policy + explicit GRANT to
  `riskd_app` (caught by db-reviewer cycle 1 — the Phase 6A.6 reviewer
  finding generalized into a backlog item for project-wide ALTER
  DEFAULT PRIVILEGES hardening).
- Writer: `app/tenant_route_baselines.update_tenant_route_baseline`
  UPSERTs the triple count after every booking commit, inside the same
  transaction. Bounded UPSERT cost ~1ms p95.
- Reader: `derive_route_rarity` single-CTE round-trip — composite PK
  probe for the triple count + leading-column prefix scan for the
  tenant-wide SUM. Strict-less-than cold-start gate
  (`total_count < 100` → False; the 100th observation passes the gate)
  and strict-less-than rarity threshold (`share < 0.02` → False;
  exactly 2% is NOT rare).

Customer upsert COALESCE preservation: 6A.7 extended
`app/services/entity_upsert.upsert_customer` so `registered_country`
joined the existing COALESCE-on-update pattern. Payload nulls do NOT
overwrite operator-supplied (or earlier-payload-supplied) values.

Signals NOT added (operator decisions):

- **IP-country-unfamiliar signal** — dropped. False-positive risk on
  traveling legitimate customers outweighs detection benefit.
- **Customer-static-IP-set declaration mechanism** — dropped. Existing
  learn-from-observation IP familiarity rules already cover narrow IP
  patterns.
- **Address-string-matching signals** (e.g.
  `ship_from_matches_customer_billing_address`) — dropped. String-
  matching unreliable due to format variation.

Latency budget impact: 6A.7 UPSERT (~1ms) + 6A.8 single-CTE SELECT
(~1ms) = ~4ms combined p95 added to the booking commit path. Phase 5
load-test baseline ~12ms p95 + 4ms case-3 overhead = ~16ms post-amendment
baseline. The launch checklist instructs operators to watch for trend
past 50ms (yellow) or 195ms (red — calibration backlog action before
ceiling breach).

Trust-suppression on mature accounts was documented as a Phase 7+
architectural concern: a mature legitimate customer has low
`account_prior`; if compromised, signals fire but combined score may not
reach BLOCK. The workstream candidates (capability-based trust per-
dimension, session-anomaly signals, asymmetric trust freeze on first
anomaly) were carried to `docs/calibration-backlog.md` in 6E.

### Batch 6B — CAD default switch

Three commits pivoted the default tenant currency from USD to CAD —
the project is a Canadian freight aggregator, and CAD is the operational
currency. USD had been a placeholder during Phase 4B build-out.

The behavior change was a single point: `DEFAULT_VALUE_CAPS` dict
re-keyed `"USD"` → `"CAD"` with the same numeric thresholds (10000 /
5000 / 2000 / 1000); `DEFAULT_ALLOWED_CURRENCIES` and
`TenantConfig.allowed_currencies` default re-keyed `["USD"]` → `["CAD"]`;
`resolve_value_caps` fallback target re-keyed.

Unchanged intentionally: `ShipmentData.currency` /
`ModificationRequest.currency` Pydantic field defaults stayed `"USD"` —
this preserved payload-shape backward-compat with Phase 1-3 requests that
omitted the currency field; the tenant-config layer was what shifted to
CAD-default. Numeric thresholds unchanged. Multi-currency support fully
preserved end-to-end.

This was NOT tuning (which Phase 6 forbade): switching the dict key from
USD to CAD did not change rule-firing semantics on any payload that
reaches the rule evaluator. Tenants with explicit `value_caps` for
USD/CAD/EUR/etc. continued to use those values. No rule weight,
threshold value, or maturity parameter changed.

Test infrastructure deviation: plan 6B.2 estimated ~30 edited lines
across 6 test files; actual blast radius was 126 failures across 26
files. Mid-execution the strategy pivoted from per-test mechanical edits
to a fixture-centric approach — shared tenant fixtures in
`tests/conftest.py` (`seeded_tenant`, `create_tenant_with_token`,
`create_extra_tenant`, `seed_tenant_created_days_ago`) now seed
`allowed_currencies = ["USD", "CAD"]` by default. The CAD-default switch
was still exercised via the value_caps fallback unit tests. The pattern
echoed the 5D fixture refactor saga in scope-creep shape; STATUS.md row
captured the deviation.

### Batch 6C — Replay-validation harness + measurement

Five commits delivered the replay measurement gate. `scripts/replay_validation.py`
was an NDJSON-streamed corpus loader, `httpx.AsyncClient + asyncio.Semaphore(50)`,
idempotent re-runs via deterministic request_id, per-transaction
triggered_rules + score + latency captured. Three corpora exported from
the sibling freight_risk repo: `approved_jan_mar.ndjson` (10,000 records);
`case2_sample.ndjson` (500 records); `case3_census.ndjson` (95 records,
Roulottes Lupien single-customer cluster). Eighteen unit tests on the
orchestrator.

Measurement findings:

| Corpus | Records | BLOCK | REVIEW | ALLOW | Verdict |
|---|---|---|---|---|---|
| Approved (Jan-Mar 2026) | 10,000 | 18 (0.18%) | 4,083 (40.83%) | 5,899 (58.99%) | over target |
| Case-2 (gobolt-non-34x-api) | 500 | 66 (13.2%) | 424 (84.8%) | 10 (2.0%) | recall 98% above target |
| Case-3 (Roulottes Lupien census) | 95 | 0 | 0 | 95 (100%) | 0% detection, far below target |

Case-3b detection was 0/95 on the Roulottes Lupien census. Root cause was
a structural rule-design mismatch with the attack shape: the Roulottes
Lupien customer was CA-registered with home address in Quebec, booking
ships from that same CA address to a US destination, so
`customer_registered_country = "CA"`, `origin_country = "CA"`,
`destination_country = "US"`. The simple compound's
`customer_country_triangle_mismatch` required customer-country to differ
from BOTH origin and destination — and origin matched customer-country,
so triangle-mismatch returned False. The sophisticated compound required
`tenant_route_baselines` to contain ≥100 observations across all triples;
the replay tenant was created immediately before the replay with no
historical bookings, so the rule was structurally dormant under the
cold-start gate. Both behaviors were consistent with rule-design intent
for the population-of-fraud detection target.

Per-rule fire counts on the case-3b census (top of the table):
`unfamiliar_ip_country_for_origin` 85/95; `unknown_destination_address`
82/95; `extreme_value` 2/95; `ip_fully_new_for_customer` 1/95. The fires
did not compound to push score into REVIEW or BLOCK bands at the cold-
start customer priors and maturity-sensitive weights in effect.

Case-2 recall was 98% (490/500), above the ≥85% target. The top
compound was `api_non_cloud_ip` (500/500) + `non_cloud_established_account`
(490/500) + `unknown_destination_address` (480/500) +
`unfamiliar_ip_country_for_origin` (480/500). The compound put scores
firmly into REVIEW or BLOCK bands for the majority of records.

The 18 approved-corpus BLOCK records all fired the same 4-rule top
compound (`unknown_destination_address` + `unfamiliar_ip_country_for_origin`
+ `api_non_cloud_ip` + `non_cloud_established_account`), with
`value_novelty_compound` adding 13/18 and `extreme_value` adding 6/18 to
push over the 0.80 BLOCK threshold. Pattern: high-value bookings from
API-non-cloud-IP customers with unknown destination addresses and
unfamiliar origin-IP-country pairs. These shapes can be legitimate (large
established customers shipping to new partners) but also overlap with the
case-2 fraud surface area. Per-record `request_id` enumeration was
retained in `docs/replay-results/approved.json` for post-launch triage.

The 41% REVIEW rate on operator-approved transactions was high but
operationally interpretable: REVIEW is "human-reviewed, not auto-blocked"
— not a false-positive in the BLOCK sense but worth post-launch
observation.

The Phase 6C closeout was explicit: **NO TUNING was performed in response
to these measurements**. Per the project-wide build-phase discipline,
rule weights, thresholds, maturity parameters, and rule definitions were
NOT changed in response to the findings. Phase 6E synthesized eight
calibration items into `docs/calibration-backlog.md` for the post-launch
real-data observation window. The 5-month post-launch observation window
was the place where tuning would happen; build-phase tuning on synthetic-
history data risks calibrating to the artifact of the synthesis rather
than the production reality.

### Batch 6D — Deployment artifacts

Nine commits produced the deployment-ready artifact set. Claude Code
never touched AWS; the operator executed the runbook and provided
credentials via GitHub Secrets.

The multi-stage Dockerfile separated build and runtime stages: the
builder installed `build-essential` and pip-installed dependencies into
`/install`; the runtime stage copied only the installed site-packages +
entrypoints + application source onto a clean `python:3.13-slim`. No
build toolchain in the runtime image shrunk the attack surface and the
image size. Dependency install via `tomllib` extraction (the builder
reads `pyproject.toml` with stdlib `tomllib` and writes a
`requirements.txt` from `[project].dependencies`) avoided `pip install .`
fragility and `fastapi[standard]` transitive coupling. The HEALTHCHECK
uses stdlib `urllib.request` for the same reason.

`infra/ecs-task-definition.json` (Fargate awsvpc, cpu 1024 / memory 2048,
4 environment + 4 Secrets Manager secrets, healthCheck matching the
Dockerfile, `awslogs-create-group: true`, all placeholders in `${VAR}`
form for single-tool envsubst). Three IAM policy JSONs:
`task-execution-role.json` (ECR pull, Secrets Manager read, CloudWatch
Logs incl. CreateLogGroup); `task-role.json` (empty statements;
documented posture — the app does NOT call AWS at runtime);
`github-actions-deploy-role.json` (ECR push, ECS update, PassRole with
`iam:PassedToService` condition).

`docs/aws-deploy-runbook.md` (567 lines): the operator-executable runbook
covering VPC, subnets, ALB, ECS cluster, RDS, Secrets Manager, IAM
roles, OIDC trust policies, GitHub Secrets configuration. No Terraform,
no CloudFormation, no CDK — the AWS infrastructure is provisioned once
per environment then iterated rarely; IaC's value compounds when
infrastructure churns. The risk acknowledged: GUI provisioning is non-
reproducible. Mitigation: the runbook is testable end-to-end in a fresh
AWS account, and IAM policy JSONs + ECS task definition JSON ARE checked
in, carrying the security-load-bearing contracts independently of the
runbook.

`scripts/smoke_test.py` (stdlib-only) plus 19 unit tests on
`assert_response`. POSTs a CAD booking; asserts HTTP 200, decision band,
score ∈ [0, 1], request_id echo, latency < 5s.

Three GitHub Actions workflows separated by trust boundary:

| Workflow | Trigger | Job | Artifact |
|----------|---------|-----|----------|
| `test.yml` | PR to `main` / `release/*` | ruff + ruff format --check + mypy --strict + pytest unit + Snyk dep scan (parallel) | none |
| `build.yml` | push to `main` | Docker build + ECR push | `dev-<short_sha>` image |
| `deploy.yml` | tag push `v*` | fresh build + ECR push + ECS task-def register + service update + smoke | `<version>` + `<short_sha>` image, same digest |

A unified workflow gating on trigger would conflate the trust boundaries.
Tag-push triggers `deploy.yml` (not push to a release branch): tags are
immutable references in git; a tag pinned to a SHA is a permanent record
of what was deployed when. The dual-tag-on-same-digest image strategy
(`<version>` for operator rollback + `<short_sha>` for forensic
traceability) lets ECR resolve two tags to one underlying digest with no
double upload.

Manual rollback for v1; no auto-rollback. On smoke failure or ECS
task-launch failure, the deploy workflow exits non-zero; the operator
triggers rollback manually via the ECS console per the runbook's
"Rollback" section (update-service back to the prior task-definition
revision). Auto-rollback at single-tenant pre-launch scale adds
workflow complexity without proportional risk reduction.

OIDC over long-lived AWS access keys: both `build.yml` and `deploy.yml`
use `aws-actions/configure-aws-credentials@v4` with `role-to-assume`. The
deploy role's IAM trust policy gates on `token.actions.githubusercontent.com:sub`
matching `repo:<org>/<repo>:ref:refs/heads/main` (build) or
`repo:<org>/<repo>:ref:refs/tags/v*` (deploy). Snyk over SonarCloud:
the threat model is Python-dependency vulnerabilities; SonarCloud's
strength (code-smell detection) is already covered by ruff and mypy.

Migrations were decoupled from deploy: `deploy.yml` does NOT run
`alembic upgrade head`. The runbook documents migrations as a separate
operator-triggered ECS run-task invocation using the same task-definition
with an overridden command. Coupling them would mean a migration failure
aborts a code deploy that was otherwise safe; a code deploy bug forces a
schema rollback. Decoupling lets the operator order the two appropriately
for each release.

The 6D.8 envsubst no-op was a near-miss caught in cycle 1: an earlier
draft mixed envsubst (for some vars) and sed (for the image URI). Because
envsubst only expands `${VAR}` syntax and the JSON used bare identifiers,
the envsubst step silently no-op'd on most placeholders. `register-task-definition`
would have failed on the first real deploy with literal "ACCOUNT_ID" strings
in ARN positions. Aligning the template to `${VAR}` form and consolidating
to one substitution tool eliminated the split-brain. `update-service`
pins to the exact revision ARN returned by `register-task-definition`
rather than passing the family name and relying on ECS to default to
the latest ACTIVE revision — immune to any concurrent-registration race.

### Batch 6E — Wrap

Three commits synthesized the phase: `docs/calibration-backlog.md`
(post-launch tuning checklist, 15 items at 6E close);
`docs/production-launch-checklist.md` (operator-executable launch
sequence Phase A through Phase I); the aggregate REPORT_PHASE_6.md;
`.ai/decisions.md` Phase 6 closeout linking the per-batch amendments.

### Phase 6 outcomes

Phase 6 closed with 82 rules (+3 in 6A), 76 ALLOWED_CONTEXT_FIELDS (+5 in
6A), two new migrations (0010 country_route_stats, 0011 case_3b_schema),
one new `app/` module (`tenant_route_baselines.py`), two new scripts,
four new infra artifacts plus README, three new GitHub Actions workflows,
the multi-stage Dockerfile, four new docs (replay-validation,
aws-deploy-runbook, calibration-backlog, production-launch-checklist).
Zero panel-skip events on standard-path commits.

The Phase 6C measurement was the empirical pivot point: the 0/95 case-3b
detection, the 41% REVIEW rate, and the 18 approved BLOCK records each
fed directly into Phase 7's calibration brief. The Phase 6E closeout
explicitly punted to Phase 7 / post-launch — Phase 6's discipline was to
SHIP the rules as designed, OBSERVE under real traffic, and TUNE against
the calibration backlog with confidence.

The replay-validation methodology became the canonical measurement
contract: NDJSON-streamed deterministic-seed corpora; idempotent
per-record request_id; semaphore-throttled httpx.AsyncClient against a
running app; per-transaction triggered_rules + score captured; aggregate
JSON output for variant comparison. Phase 7B reused the harness for the
5-variant comparison work.

---

## Phase 7 (Week 7) — Pre-launch calibration + case-2 learning + BLOCK target retirement

Phase 7 was the pre-launch calibration pass responding to the three
empirical findings from the Phase 6C replay validation: 41% REVIEW rate
on the operator-approved corpus, 0.18% BLOCK rate, and 0% detection on
the 95-record Roulottes Lupien case-3b census. The phase scope expanded
significantly mid-execution: the original brief was "calibrate 2 FPR
rules + add case-3b compound + delete triangle compound + final
validation," but post-7C.1 measurement surfaced a case-2 architectural
gap that forced the 7C.7 rule replacement; post-7C.11 re-measurement
surfaced the BLOCK overshoot that forced the 7C.12 geo calibration; and
ultimately the operator-decision target retirement (7C.13 closeout at
`58d155e`). Five batches, executed 2026-06-04.

### Scope override

Phase 6's strict "no rule weight, threshold, maturity parameter, or rule
definition was changed in response to these measurements" discipline was
specific to Phase 6 — its purpose was to defer calibration to post-launch
real-data observation. Phase 7 explicitly overrode because Phase 6C
surfaced a launch-blocker (41% REVIEW on operator-approved transactions;
structurally unworkable for human review queue capacity) that could not
reach production without intervention.

### Batch 7A — Phase 6 → Phase 7 transition

Two implementation commits. 7A.0 (`5459511`) was the repo hygiene scrub:
`git filter-repo --invert-paths --path scripts/replay --path docs/replay-results --force`
removed all freight_risk-derived NDJSON corpora and per-record JSON
results from every commit reachable from `feat/refactor`. 174 commits
were reduced. Defense-in-depth `.gitignore` entries added:
`/tmp/riskd-replay/`, `scripts/calibration/` (Phase 7 ephemera),
`scripts/replay/data/` (paranoia), `docs/replay-results/` (paranoia).
Operator approval gate executed pre-rewrite; no remote at risk (zero
pushed commits at scrub time). The aggregate-only output policy applied:
per-record content lived only in `/tmp/` and was never committed.

7A.1 (`e3870e5`) replaced the hardcoded `_CORPUS_DIR` with a `--corpus-dir
PATH` flag; added `--rules PATH` metadata flag (records the rule-file
the replay was measured against — does NOT runtime-swap rules); added
`--out PATH` for JSON aggregate output; added `--compare PATH1 PATH2`
mode for pre-computed delta reports. Removed `per_transaction` array
from output JSON.

7A.2 (`b09b7ed`) added `scripts/calibration/export_from_freight_risk.py`
(read-only SQLite read against the sibling freight_risk DB, 4-tier
customer-country derivation: explicit `country` column → address last-
token regex → modal IP geo via MaxMind → null fallback; per-corpus
hardcoded overrides for case-3 records to `CA` registered country +
`origin_via_carrier_dropoff=true`; CAD currency on all records). The
export script's address-parsing logic was OFFLINE corpus-shaping only;
the riskd app reads the structured field from the payload directly and
never sees address-parsing heuristics. `scripts/calibration/` was Phase
7 ephemera tracked during Phase 7 via `git add -f` (since `.gitignore`
blocked it for defense-in-depth) and was deleted in 7E.3.

### Batch 7B — Variant comparison + rule design

7B.1 (`8815b57`) implemented the variant comparison harness and ran the
empirical 5-variant comparison. Variants A-D were planned ahead; variant
E was added mid-execution at operator request after the original four
missed all targets jointly.

| Variant | Gate | Weight changes | Secondary signal |
|---|---|---|---|
| A | `customer_observations >= 30` on both rules | none | none |
| B | unchanged (`>= 10`) | IPC 0.3→0.15; DEST 0.2→0.10 | none |
| C | `>= 30` AND halved weights | both | none |
| D | unchanged (`>= 10`), weights unchanged | none | IPC: `AND (is_vpn OR is_proxy OR ip2p_threat_any OR ip_in_threat_list OR is_datacenter_ip)`. DEST: `AND shipment_value > shipment_value_threshold_medium`. |
| E | IPC `>= 10` + D-style compound; DEST `>= 30` | none | IPC: D-style compound. DEST: none. |

Where IPC = `unfamiliar_ip_country_for_origin`, DEST =
`unknown_destination_address`. Phase 7 targets were Approved REVIEW <15%
(stretch <10%) AND case-2 recall ≥95%. The decision-band outcomes:

| Variant | Approved REVIEW | Approved BLOCK | Case-2 recall | Case-3b detection |
|---|---|---|---|---|
| A | 38.83% | 0.10% | 97.6% | 0.0% |
| B | 40.67% | 0.09% | 99.0% | 0.0% |
| C | 38.69% | 0.09% | 97.8% | 0.0% |
| D | 4.28% | 0.07% | 43.2% | 0.0% |
| E | 34.67% | 0.09% | 97.0% | 0.0% |

**No variant met both targets simultaneously.** A/B/C suppressed IPC and
DEST fire rates modestly but the approved REVIEW rate barely moved
because the case-2-targeting rules `api_non_cloud_ip` (weight 0.40) and
`non_cloud_established_account` (weight 0.20) fired on ~41% of the
approved corpus on their own. D met the REVIEW target (4.28%) by zeroing
out IPC and DEST, but the same change collapsed case-2 recall to 43.2% —
the case-2 fraud signature (API+non-cloud+unknown destination) depended
on the very IPC and DEST signals D zeroed out. Variant E's asymmetric
design (D-style compound on IPC + A-style gate on DEST) confirmed the
**structural bound**: even with asymmetric harshest treatment on the
highest-FPR-contributing rule, the other rules in the api+non_cloud
compound kept the REVIEW share above 30%.

Case-3b detection stayed 0% across all variants because the case-3b
coverage gap was structurally addressed by the new
`cold_start_outbound_carrier_dropoff` rule landing in 7C.2, not by
variant tuning.

### Batch 7C — Implementation + rule reshape + calibration cycles

What started as a four-commit batch grew to 13 substantive commits as
mid-phase measurements surfaced the case-2 architectural gap and the
BLOCK overshoot.

**7C.1 was SKIPPED** per operator decision (2026-06-04) after reviewing
the 5-variant empirical record. No variant was applied to `app/rules.yaml`.
The two FPR-driving rules retained their baseline conditions and weights.
Calibration-backlog items 1 + 2 remained DEFERRED to post-launch.

**7C.2 / 7C.3** committed as single atomic `f21ff56`, "case-3b detection
swap — asymmetric outbound replaces symmetric triangle." DELETED
`cold_start_country_triangle_with_carrier_dropoff` (Phase 6A.5 symmetric
triangle compound; 0/95 detection on the Roulottes Lupien census).
ADDED `cold_start_outbound_carrier_dropoff` matching the asymmetric
attack shape (customer ships FROM declared country TO outside-country via
carrier-facility drop-off). Weight 0.65; `maturity_sensitive: false`
(cold-start gate inside the condition). NEW derivation
`_outbound_destination_mismatch` in `app/context.py` with defensive
falsy check (None AND empty string both produce False). Field swap:
`customer_country_triangle_mismatch` removed from `ALLOWED_CONTEXT_FIELDS`;
`customer_destination_country_mismatch_outbound` added. Net count
unchanged at 76 (1-for-1 swap). RETAINED unchanged:
`cold_start_population_baseline_rare_with_carrier_dropoff` — different
signal class (tenant-population baseline rarity vs fixed country-equality),
independent retention.

The structured-field architectural pattern was preserved unchanged: the
riskd app consumes `payload.customer.registered_country` directly and
never parses address strings in production.

**7C.6** (`d3b07e7`) added the `unfamiliar_asn_for_customer` Context
derivation as case-2 prep. After post-7C.4 measurement showed Variant C
drove approved REVIEW down to ~6% but had not moved case-2 detection,
diagnosis revealed the case-2 attack shape (API booking from a customer's
familiar tenant but from a never-seen ASN) was not covered by the
existing rule catalogue. The natural fix was to make the rule tenant-
aware via `tenant_config` (each tenant declares expected API source ASNs
or IP ranges). **REJECTED as hardcoding territory**: manual operator
maintenance per tenant; fragile to infrastructure changes (Google Cloud
IP range expansions, ASN reassignments); doesn't scale to many
enterprises under a single tenant.

The learning-based alternative reused the EXISTING per-customer baseline
mechanism: each customer's accumulated ASN history is their reference;
deviation is the signal. No tenant configuration; no operator burden;
scales naturally. The key finding during verification: the ASN tracking
infrastructure ALREADY EXISTED. The `customer_baselines.ip_asn_stats`
jsonb column (populated by `baseline.add_observation` via `_bump`;
decayed uniformly at 90-day half-life) was exactly the per-customer ASN
frequency map the new rule needed. Phase ≤6 work had already designed
the mechanism for this exact pattern. Phase 7's case-2 architectural
rewrite added only the CONSUMER side: the Context derivation (7C.6) and
the new rule (7C.7).

**7C.7** (`09705e5`) was the case-2 architectural rewrite. **DELETED**:
`api_non_cloud_ip` (weight 0.40; tenant-agnostic novelty heuristic; 41%
fire rate on approved corpus baseline). **DELETED**:
`non_cloud_established_account` (weight 0.20; same shape with
`NOT is_new_user`; 40% fire rate). **ADDED**: `api_booking_from_unfamiliar_asn`
(weight 0.65, condition `is_api_booking AND unfamiliar_asn_for_customer`).
Sharp on case-2 attack shape (gobolt customers shifting off Google
Cloud); silent on non-gobolt tenants whose customers have non-cloud ASN
baselines. The cold-start gate (`customer_observations >= 10`) sits
INSIDE the `_asn_unfamiliar_for_customer` derivation, matching the
`_outbound_destination_mismatch` pattern from 7C.2. `maturity_sensitive`
false (downweighting would suppress the very signal used to flag the
threat).

The operator clarification of the case-2 signature: "non-34x" in case-2
means non-Google-Cloud. Legitimate gobolt API traffic comes from Google
Cloud (34.X.X.X address range). Case-2 attacks used compromised API keys
routed through the attackers' own infrastructure — typically residential
ASNs. The `api_non_cloud_ip` rule's 100% fire rate on case-2 was the
right signal; the 41% fire rate on the approved corpus was the rule's
tenant-agnostic application — gobolt's pattern applied universally to
all tenants' API-from-non-cloud traffic. Also,
`unfamiliar_ip_country_for_origin` is pair-novelty (origin_address,
ip_country), not country-match. Legitimate freight customers ship from
many origin addresses; every new origin creates a novel pair even when
the IP country is stable. The rule name was misleading; its 72%
baseline fire rate was a function of legitimate origin expansion.

**7C.8** (`d5dde63`) reduced weights on the pair-novelty rules to
secondary corroborating-signal role: `unfamiliar_ip_country_for_origin`
0.30 → 0.15; `unknown_destination_address` 0.20 → 0.10. Both rules'
CONDITIONS were unchanged. Their 72%/65% baseline fire rates were
intentionally preserved (legitimate freight customers' origin expansion
remains the dominant fire pattern), but their contribution to scoring
dropped below the level that pushes records to REVIEW band standalone.

**7C.9** (`07b1606`) added warmup methodology to the export script and
orchestrator. Phase 7B variant comparison measurements WITHOUT warmup
systematically understated case-2 detection capability: the customer
baseline formed FROM the attack records themselves during the replay (no
pre-replay history), so the new ASN rule couldn't discriminate. Warmup
emits K=100 pre-March-31 legitimate bookings per measurement customer
BEFORE the measurement records (case-3 excluded — brand-new-customer
fraud, no pre-fraud history applicable). The orchestrator processes
warmup as a separate phase: ALL warmup tasks complete via
`asyncio.gather` BEFORE any measurement task starts. Warmup decisions
were recorded in `warmup_summary` and EXCLUDED from FPR/recall aggregates.

**7C.11** (`c36a26d`) was the architectural change that the post-7C.7
re-measurement forced. The case-2 ASN-deviation rule (7C.7) fired on 0
of 500 case-2 attack records — a complete miss vs the design intent.
Investigation of the two compromised customers' actual
`customer_baselines.ip_asn_stats` post-replay revealed why:

- **Customer gPYG** (804 total shipments): 32 distinct ASNs accumulated,
  including 438 Google LLC + 122 Bell Canada + 57 Rogers Communications
  + 26 Videotron + 22 Cogeco + 18 Shaw + ~24 other Canadian residential
  ISPs.
- **Customer nD7** (135 total shipments): 13 distinct ASNs, NO Google;
  baseline entirely Canadian residential ISPs (Bell, Videotron, SOGETEL,
  Altima, etc.).

The customer's "baseline" was a record of every IP/ASN they had EVER
booked from — including the fraud-attack bookings themselves. Attack
ASNs were already "familiar" in the baseline (the attacks polluted the
very baseline they were supposed to evade). The 7C.7 rule condition
`asn_org NOT IN baseline.ip_asn_stats` returned False on every attack
record.

The architectural decision: **customer baseline = record of operator-
confirmed legitimate behavior, NOT record of all evaluated bookings**.
Booking endpoint (`app/api/booking.py`) wraps `baseline.add_observation()`
+ `baseline.save()` in `if result.decision == "ALLOW":`. REVIEW/BLOCK
bookings are HELD in pending state — baseline state unchanged (all
stat-dicts, Welford accumulators, last_booking_*, histograms). Feedback
endpoint (`app/api/feedback.py`): when operator submits `approved`
feedback AND the booking's prior decision was REVIEW or BLOCK, fold the
deferred observation into the baseline NOW. Same `add_observation` shape
as booking time; re-enrich source_ip via the cached `ip_enrichment` row.

The deferred fold provides operator-driven baseline curation: each
ASN/IP enters the baseline only after operator confirmation. Attack
bookings stay OUT of the baseline forever (because they're never
operator-approved). Future attack records from the same ASN remain
"unfamiliar" and trip the case-2 rule correctly.

Operational implications: cold-start ramp lengthens by ~5-15% (new
customers' baseline accumulation slows because REVIEW bookings no longer
contribute); velocity counts unaffected (SQL-based queries on the
shipments table, not baseline state, count all bookings regardless of
decision band); operator-feedback latency (30-180 days post-evaluation)
carries baseline lag (documented as the operational contract).

Phase 7B vs 7D non-comparability: the two measurements use different
architectural states. Phase 7B section in `docs/replay-validation.md`
stays as the historical record of the pre-7C.11 architectural state;
Phase 7D measurement is the source-of-truth for the post-7C.11
catalogue.

Measurement impact of 7C.11: case-3b detection 0% → 100% (the Roulottes
Lupien attack records BLOCK and do NOT fold, so the cold-start gate
stays open for all 95 records); case-2 recall 40.2% → 84% (baselines
stop self-polluting). The 41% → 6.17% REVIEW reduction also held. Side
effect: approved BLOCK rose 1.45% → 3.46% as the cold-start ramp
surfaced novel pair-notes on legitimate customer history.

**7C.12** (`85bb3a4`) calibrated the four MaxMind-enabled geo rules
against the Jan-Mar 2026 measured FPR. The 7D-prep MaxMind mount
(`d11a534`) made the geo signals active for the first time in any
measurement. Per-rule fire share on the 346 approved BLOCKs:
`impossible_travel_geo` 71.7%; `ip_country_change` 58.4%;
`ip_long_distance_new_ip` 57.0%; `ip_intercontinental_jump` 46.0%. The
geographic signals were REAL — operator-approved long-distance bookings
DO involve large IP-distance jumps — but the signal belongs at REVIEW
(operator queue), not auto-BLOCK.

- `impossible_travel_geo`: 0.65 → 0.30
- `ip_intercontinental_jump`: 0.35 → 0.20
- `ip_country_change`: 0.25 → 0.15
- `ip_long_distance_new_ip`: 0.25 → 0.15

Conditions unchanged. This was the cluster of geo-rule weights that
Phase 1-2 explicitly deferred under the "no weight tuning in mid-build
phases — calibrate against real data in Phase 6+" decision. The
"wait for production traffic" framing had assumed production would be
the only data source; the 7D-prep MaxMind mount made historical Jan-Mar
2026 data viable as the calibration corpus. The 7C.12 amendment
documented the principled exception. Impact: approved BLOCK 3.46% →
1.31%; approved REVIEW 6.17% → 4.58%; gPYG case-2 100% preserved
(operator's gate); nD7 case-2 catch 40.7% → 25.9% (geo rules contributed
to nD7 catches but the trade-off was acceptable per operator).

**7C.13** (`58d155e`) was the Phase 7 closeout: re-measurement docs +
BLOCK target retirement.

### Batch 7D — Final measurement

Phase 7D measurement under the post-7C.12 catalogue:

| Metric | 6C baseline | Pre-MaxMind 7D | Post-7C.11 | Post-7C.12 | Target | Verdict |
|---|---|---|---|---|---|---|
| Approved BLOCK | 0.18% | 0.10% | 3.46% | **1.31%** | <0.05% original | RETIRED |
| Approved REVIEW | 41% | 38.83% | 6.17% | **4.58%** | <15% (stretch <10%) | **PASS stretch** |
| Case-2 recall (combined) | 98% | 97.6% | 84.0% | **80.0%** | ≥95% | reframed |
| **Case-2 recall (gPYG)** | n/a | n/a | 100% | **100%** | ≥95% | **PASS** |
| Case-2 recall (nD7) | n/a | n/a | 40.7% | **25.9%** | n/a (no baseline) | deferred |
| Case-3b detection | 0% | 0% | 100% | **100%** | ≥85% | **PASS** |

Per-customer case-2 finding: 500 case-2 records split between two
customers. gPYG had 365 records and 701 pre-attack approved records in
freight_risk, achieving 100% recall. nD7 had 135 records and ZERO
approved records in the entire freight_risk dataset (all 6,684 records
labeled `reject`); achieves 25.9% recall. nD7's complete historical
presence in freight_risk is fraudulent; earliest booking is 2026-03-17
— the same day the attack window starts. No pre-attack legitimate
history. The case-2 ASN-deviation rule correctly fires 0% on nD7 because
the customer's baseline cannot accumulate (no operator-confirmed ALLOW
bookings to fold under the 7C.11 ALLOW-only gate). **This is DESIGNED
behavior, not a bug.**

The "84% structural ceiling" framing from the post-7C.11 / pre-7C.12
snapshot was retired during 7C.13 closeout. It's not a structural
ceiling — it's a customer-mix artifact: one of two case-2 customers is
fraud-only. Customers with legitimate history achieve 100%; fraud-only
customers need a different signal class (calibration-backlog item 20).

### Close decisions

The two operator decisions that closed Phase 7:

1. **BLOCK target <0.05% RETIRED.** Replaced by production operator-
   approved-as-legit tracking per `docs/production-launch-checklist.md`
   Phase E. The original target was set against under-enriched
   measurements; with MaxMind active in production, the achievable
   replay-environment proxy is <0.5%; the actual 1.31% is 2.6x over but
   constitutes a 7.4x reduction from the 6C-baseline-with-MaxMind state
   (which would have been ~9-10% without the cumulative case-2 +
   case-3b + 7C.11 + 7C.12 work). The production-launch checklist Phase
   E adds operator-approved-as-legit rate monitoring; if Day 1-30
   BLOCKs are real fraud catches that were ALLOWed pre-MaxMind, the
   1.31% is a real detection win; if they're predominantly operator-
   approved false positives, post-launch calibration iterates further on
   the geo-rule weights or compounds.

2. **Case-2 reframed per-customer-class.** gPYG (legit-history class,
   365 records, 701 pre-attack approved records): 100% PASS. nD7
   (fraud-only class, 135 records, zero approved records in freight_risk):
   25.9% — deferred to calibration-backlog item 20. The combined-corpus
   80% number conflates two customer classes with structurally different
   signal availability; the per-customer framing makes the calibration
   result interpretable. The ASN-deviation architecture is correct for
   the gPYG-shape attack (sophisticated fraud against an established
   customer); the nD7-shape attack (purely fraudulent customer, never
   legit) needs a different signal class.

### Batch 7E — Closeout

Three commits codified the two operator-decision outcomes. 7E.1 marked
calibration-backlog items 1, 2, 6 RESOLVED with resolution pointers to
Phase 7C commits; items 3-15 unchanged (post-launch tuning workstream);
item 20 added during 7C.13 (nD7-class fraud-only signal class). 7E.2
published REPORT_PHASE_7.md. 7E.3 deleted `scripts/calibration/`
entirely (export script, run_variants, README, unit tests). `.gitignore`
entry retained as defense-in-depth.

### Phase 7 outcomes

The calibration arc was non-linear:

- **Pre-Variant-C** (Phase 6C state): REVIEW 41%, BLOCK 0.18%, case-2
  98%, case-3b 0%.
- **Post-7C.1** (Variant C applied — though SKIPPED in final decision):
  hypothetical REVIEW reduction without case-2 architectural lift.
- **Post-7C.7** (ASN-deviation rule): case-2 detection improved
  architecturally, but baseline pollution from attack records created a
  self-defeating loop.
- **Post-7C.11** (ALLOW-only baseline gating): case-2 40.2% → 84%;
  case-3b 0% → 100%; REVIEW 41% → 6.17%; BLOCK 1.45% → 3.46% (cold-
  start ramp side effect).
- **Post-7C.12** (geo-rule weight calibration): BLOCK 3.46% → 1.31%;
  REVIEW 6.17% → 4.58%; gPYG case-2 100% preserved; nD7 case-2 cuts.

Phase 7 closed with the case-3b structural fix landed, the case-2
architectural rewrite to learning-based ASN deviation landed, the
baseline-pollution prevention landed, the MaxMind-enabled geo
calibration landed, the BLOCK target retired with documented rationale,
and the empirical record fully audit-traceable. Items 1, 2, 6 resolved;
items 3-15 unchanged; item 20 added.

The Phase 7 → Phase 8 → launch sequencing: Phase 7 closes with the case-
3b structural fix landed + the empirical record documented. Phase 8
(test suite audit + doc consolidation + migration squash) follows.
Production launch follows Phase 8 close per
`docs/production-launch-checklist.md`. Operator owns the launch
decision.

The architectural workstream implied by the structural-bound finding (a
new rule that catches case-2 fraud without piggybacking on IPC/DEST) is
documented as Phase 9+ post-launch work. That workstream does not block
Phase 7 or Phase 8 close.

---

## Audit + verification history

Four documents were superseded and deleted in commit 8C.10:

- **Phase 1 verification** (`docs/verification-phase-1.md`, 268 lines).
  Reference-codebase comparison of freight_risk + FreightSentry as of
  2026-05-25 against the Design Context. Documented design-context
  corrections (rule count 102 not 117; recipient-overlap rules
  location), tuned thresholds carried forward (z>6 cadence,
  velocity_spike_daily_api=50, residential_asn_high_velocity=15),
  implementation facts (datacenter keyword constants, IP2Proxy sentinel
  gating, per-IP-type half-lives 365/365/60/180 days). All current-
  state values now live in `.ai/decisions.md`, `app/rules.yaml`,
  `app/baseline.py`, and `app/signals.py` directly. The verification
  document was a process artifact; its load-bearing claims migrated
  into the live docs.

- **Phase 3 RLS audit** (`docs/security-audit-rls-phase-3.md`, 174
  lines, dated 2026-05-28). Query inventory snapshot — 36 asyncpg call
  sites at end of Phase 3 — plus the RLS coverage matrix. The snapshot
  documented the dormant pre-Phase-5 state under postgres-superuser
  bypass. Superseded by `docs/security-audit-rls-phase-5.md` which
  documents the current 7-table active-RLS state under
  `riskd_app_login`.

- **Phase 4 RLS audit** (`docs/security-audit-rls-phase-4.md`, 117
  lines, dated 2026-06-01). Phase 4 delta over Phase 3: five new query
  rows for admin endpoints, `require_admin_role` authorization model,
  PII handling notes for admin responses. Superseded by the Phase 5
  audit doc.

- **Initial audit** (`docs/initial-audit.md`, 456 lines). Project-
  bootstrap comparative audit of FreightSentry vs freight_risk
  recommending the hybrid promote-freight_risk path. Captured the
  discard/cannibalize/migrate matrix and the 5-6 week phase plan.
  Entirely historical; the decision was executed and the current
  codebase IS the outcome.

The audit family's evolution traces the security-posture maturity arc:
Phase 1 dormant RLS with app-layer `tenant_id` filtering as the active
control → Phase 3 audit confirming zero queries with missing scope
under the dormant policies → Phase 4 admin-endpoint delta validating
the read-only authz boundary → Phase 5 active RLS enforcement at
runtime under `riskd_app_login`, including the auth-table RLS-drop
defense-in-depth concession and the eight carry-forward items for
post-launch hardening.

---

## Phase 8 — Pre-launch consolidation

Phase 8 was the pre-launch consolidation pass: migration squash, test
suite audit, doc consolidation, plan-file teardown. Four batches landed:

- **Batch 8A — Migration squash + revision-ID sweep** (commits
  `41c3d90` schema golden, `4fec9bb` atomic squash, `771ca90` revision-ID
  sweep + close). The 11-migration chain (`0001_initial.py` through
  `0011_case_3b_schema.py`) collapsed atomically into a 5-migration
  chain (`0001_foundation`, `0002_booking_flow`, `0003_baselines`,
  `0004_enrichment_global`, `0005_runtime_roles`). `pg_dump` byte-
  equivalent against pre-squash state; round-trip verified.
  `tests/integration/test_schema_golden.py` (`41c3d90`) anchors the
  schema contract as the anti-drift gate.

- **Batch 8B — Test audit + coverage anchor** (commits `d648e59`
  coverage baseline, `695c35f` + `6cdc3f0` phase-named test renames +
  whitelist probe consolidation, `859abe1` fixture survey + close).
  Line coverage anchored at 91% (`tests/coverage_baseline.txt`) as a
  non-regression gate. ~3 redundant whitelist probes collapsed; net
  test count 1118 → 1116 (Phase 8B collapse delta minus 8A.0 schema
  golden addition).

- **Batch 8C — Doc audit + plan-file teardown** (15 commits +
  1 no-op verification, `795d5c0` through `e7f990b`). `.ai/` rewrites
  (`decisions.md`, `schema.md`, `rules.md`, `system-status.md`,
  `enrichment.md`); `docs/` operational updates (`replay-validation.md`,
  `calibration-backlog.md`, `production-launch-checklist.md`,
  `load-test-phase-5.md`); 4 superseded audits deleted (`initial-audit`,
  `security-audit-rls-phase-3/4`, `verification-phase-1`); 51 historical
  plan/report/master files deleted; `docs/history.md` (this file, 1786
  lines pre-Phase-8-close) created absorbing 48+ source docs; CLAUDE.md
  cross-references cleaned.

- **Batch 8D — Phase 8 wrap** (commits `4671fd4` Phase 8 report,
  `64f8f70` checklist verification, `881f3b9` final integration test
  pass at 1116 passed + 91% coverage). Production-launch checklist
  references all resolve. Schema golden passes.

The squash never enabled RLS on `api_tokens` / `app_users` in
`0001_foundation.py`, matching the Phase 5D 0009 RLS-drop outcome.

The Phase 8 plan and report working files (`PLAN_PHASE_8A`–`8D`,
`REPORT_PHASE_8`) were distilled into this section and removed in the
pre-tag archival pass; the batch summaries above are the canonical
Phase 8 record.

---

## Pattern B-lite — In-process enrichment refresh

Post-Phase-8 pre-launch resolution of the enrichment-data launch blocker:
the IP-enrichment sources had no in-process refresh path, so a deploy ran on
whatever data the image baked in. Landed across commits `bfb12ca` (prep)
through the C0–C6 sequence plus a D1–D6 follow-on, all on `feat/refactor`.

- **Refresh module** (`app/enrichment_refresh.py`): an in-process async task,
  spawned by the FastAPI lifespan, refreshes the nine sources (FireHOL L1/L2,
  MaxMind City/ASN, IP2Proxy PX11, AWS/GCP/Azure/Cloudflare CIDR) on a 1×/24h
  cadence. Each tick downloads with bounded jittered retry and two-stage sanity
  floors (raw bytes, then extracted records) before an atomic replace —
  `atomic_replace` for in-memory forms, `atomic_replace_stream` (1 MiB chunks)
  for IP2Proxy's ~1.6 GB BIN.
- **Copy-on-write swap**: a successful tick rebuilds the Enricher and swaps
  `app.state.enricher` by reference, so in-flight `enrich()` callers finish on
  the prior instance — no locks, no torn reads.
- **Health**: `/health` gained an `enrichment: ok | degraded` field; a degraded
  state (a source never loaded) is reported without flipping the endpoint to 503.
- **Out of process**: `scripts/fetch_enrichment.py` is retained as a manual/cron
  fallback; the in-process module is the recommended path.
- **Calibration corrections / tangentials** (logged to `.claude/BUGS.md`):
  IP2Proxy LITE BIN is ~1.6 GB (not the ~50 MB the brief assumed), the download
  token allows ~5 fetches/24h, and `fetch_enrichment.py` saves cloud JSON / the
  IP2Proxy archive in forms the loader does not read. The CFN README and deploy
  runbook launch-blocker banners were demoted to historical notes.

Subsystem detail lives in `.ai/enrichment.md` § Refresh module; verification
facts in `docs/verification-pattern-b-lite.md`.

---

## Refactor pass — Platform-supplied `shipment_id` + `transaction_number`

Post-Phase-8 pre-launch refactor making the upstream platform's shipment
identifier the system of record (a future admin dashboard in a separate repo
would be confused by a riskd-minted ID diverging from the platform's). Landed as
migration `0006_platform_shipment_id` on top of the squashed `0001`–`0005` chain.

- **Schema:** `shipments.id` serial → platform-supplied `text`; PK composite
  `(tenant_id, id)` (cross-tenant collision defense); new `transaction_number
  text NOT NULL`, **unindexed by design**; `decisions.shipment_id` int → `text`
  with composite FK `(tenant_id, shipment_id)` → `shipments(tenant_id, id)`.
  Golden schema regenerated; pre-launch clean redefinition, no backfill.
- **Endpoints:** booking consumes `shipment_id` as identity (drops the
  `RETURNING id` round-trip) and surfaces an intentional **409** on duplicate
  `shipment_id`, discriminated from the `request_id` idempotency 409 by
  constraint name. Modification cross-checks `shipment_id` + `transaction_number`
  against the resolved/stored shipment (**422** on mismatch). Responses echo the
  identity. Idempotency, feedback resolution, and modification cardinality were
  left untouched (verified V3 / decision #10).
- **Verification:** a Phase-1 codebase pass confirmed `shipment_id` type-opacity
  (V1), that all four `decisions ↔ shipments` joins already carried the tenant
  predicate (V2, hard gate — zero rewrites), feedback independence (V3),
  `transaction_number` greenfield within the riskd domain (V4 — the only existing
  references are the `freight_risk` calibration source, confirmatory), and the
  golden-schema regen delta (V5).
- **Calibration ETL:** `export_from_freight_risk.py` threads the source
  `freight_risk` `shipment_id` / `transaction_number` straight through — riskd
  adopts the upstream identifiers verbatim.
- **Out of scope / flagged:** the admin dashboard and any read endpoint, the
  `transaction_number` index and any timestamp index (intentional absences,
  documented in `.ai/decisions.md`), and the platform team's payload-cutover
  coordination. A pre-existing date-sensitive `test_case_2` failure and a host
  vs. container `pg_dump` version skew were logged to `.claude/BUGS.md`.

Architectural rationale (platform identity as system of record, the
unindexed-by-design `transaction_number`) lives in `.ai/decisions.md`; the
`0006` schema contract in `.ai/schema.md`.

---

## Refactor pass — Test-suite soundness

Pre-launch pass making the test suite deterministic and honest about its
dependencies. Nine commits on `feat/refactor`.

- **DB-free unit tier**: the unit tests no longer require a reachable database —
  the asyncpg pool is opt-in, so the full unit tier runs (and passes) under an
  unreachable `DATABASE_URL`. This exposed that the prior CI "green" was hollow:
  the no-Postgres unit job could not have passed as written, because the unit
  tests errored at import without a DB. (Operator to confirm the historical CI
  job status via the GitHub UI; it does not block the tag.)
- **Order-independence**: eliminated cross-test contamination — per-test
  truncation of the global no-RLS `ip_enrichment` table plus a shared-enricher
  reset (the keystone fix), and a structlog `capture_logs` cache-pollution fix.
  The suite is deterministic and order-independent across ≥10 random seeds.
- **Deadlock fix**: a real concurrent booking/feedback deadlock was fixed by
  enforcing the canonical lock order **`customers` before `customer_baselines`**
  (the feedback path was acquiring them in reverse). Regression-pinned; the
  invariant is recorded in `.ai/conventions.md`.
- **CI split**: the workflow now runs a DB-free unit job and a Postgres-backed
  integration job separately.
- **Observability**: added an `enrich.source_load_failed` EMF metric and made
  `/health` reflect a degraded (still-200) enrichment state.

---

## Refactor pass — Documentation staleness audit

Pre-launch sweep replacing superseded phrasing in docs and comments in place
(no new files). Six doc commits plus a three-commit test-fix detour.

- **In-place edits**: README "greenfield" → "pre-launch"; removed dead links to
  the deleted `MASTER_PLAN` / `PLAN_PHASE_1` files; corrected references to the
  deleted symmetric-triangle rule to its asymmetric replacement; clarified the
  Phase-B migrate model (auto-on-every-deploy vs bootstrap-only-manual);
  refreshed `.ai/system-status.md`; synced the `0001` column comment with
  `.ai/schema.md` (operator-approved in-place edit).
- **Test-fix detour**: fixed pre-existing unit failures (an alembic-env stub and
  unguarded enrichment binary loads) so the gate could run — the enrichment
  loader now graceful-degrades on missing binaries instead of raising.
- **Not-actually-stale register (N1–N13)**: phrasings investigated and confirmed
  current, recorded so the Phase-9 doc lens does not re-investigate them.
- Nine pre-existing integration-isolation failures were surfaced and logged to
  `.claude/BUGS.md` for a follow-up pass.

---

## Refactor pass — Dead-capability audit

Pre-launch documentation-only audit (Part A; zero code change) of the unused
capability surface. Two commits.

- Classified all `ALLOWED_CONTEXT_FIELDS` and the rule catalogue: a small set of
  genuinely-dead context fields (two of them cost-bearing "keep-and-wire"
  candidates for post-launch), the inert-but-latent rule set enumerated, and
  **zero** fix-eligible structural bugs found.
- The full audit — consumption graph, per-field dispositions, and the Phase-9
  keep-and-wire seed — is the deliverable and lives at
  `docs/audits/dead-capability-audit.md` (retained). A phone_prefix /
  email_domain population gap was logged to `.claude/BUGS.md`.

---

## Closing pointer

For ongoing operations, see [docs/](.) — the current-state operator
documentation set lives here, including the production launch
checklist, the replay-validation methodology, the calibration backlog,
and the AWS deploy runbook.

For the current rule catalogue (81 rules at Phase 7 close), see
[.ai/rules.md](../.ai/rules.md). For current architectural decisions and
their rationale, see [.ai/decisions.md](../.ai/decisions.md). For the
schema as it ships (5 migrations post-squash), see
[.ai/schema.md](../.ai/schema.md). For runtime state, see
[.ai/system-status.md](../.ai/system-status.md). For the enrichment
subsystem and the ALLOW-only baseline gating contract introduced in
7C.11, see [.ai/enrichment.md](../.ai/enrichment.md).

For audit history, the prior Phase 3 / Phase 4 RLS audits, the Phase 1
reference-codebase verification, and the initial bootstrap audit are
absorbed in this file under the "Audit + verification history" section.
See commit `d6b516e` (8C.10) for the deletion of the four superseded
audit documents. The current security baseline is
[docs/security-audit-rls-phase-5.md](security-audit-rls-phase-5.md),
unchanged through Phases 6 and 7.

For the per-commit citation index used throughout this document, the
git log is authoritative — `git log --oneline feat/refactor` lists the
build's commit history in reverse chronological order. Notable
landmarks: `41c3d90` (8A.0 golden schema baseline), `4fec9bb` (8A.1
migration squash 11 → 5), `58d155e` (7C.13 Phase 7 closeout / BLOCK
target retirement), `c36a26d` (7C.11 ALLOW-only baseline gating),
`09705e5` (7C.7 case-2 architectural rewrite), `f21ff56` (7C.2-3
case-3b detection swap), `968440c` (6E.3 Phase 6 closeout), `2b0bb6a`
(6E.1 calibration backlog + launch checklist), `16de864` (6D.1 multi-
stage Dockerfile), `1a294c0` (6C.4 replay validation execution),
`ab77d76` (4A.7 TenantConfig design), `d2c4437` (4B.7 currency
normalization RESOLVED), `f13fa2a` (Phase 2 close), `72c453d` (1D.8
case-2 fixture end-to-end wire-up).
