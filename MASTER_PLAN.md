# Master Plan — Real-time freight fraud detection SaaS (v1)

Six-week build, single Python service, Postgres-only, multi-tenant from day one. This document covers all six phases at planning grain; per-phase commit-by-commit plans live in their own `PLAN_PHASE_{N}.md` files.

Design Context (architectural choices, scoring layers, half-lives, persistence decisions, endpoint surface, constraints) is fixed in the bootstrap prompt. Discrepancies between Design Context and reference codebases are recorded in `docs/verification-phase-1.md`. This plan does not re-litigate Design Context decisions; it sequences them.

---

## Cross-phase invariants

- **Single Python service** (FastAPI + asyncpg + Pydantic v2). No second process, no second language, no second storage engine, no LLM in the decision path.
- **Multi-tenant from day one.** Every table has `tenant_id`. Every query scopes by it. Postgres row-level security configured as a defensive backstop.
- **6-step commit cycle** (CLAUDE.md). Implement → validate → review → iterate → commit → proceed.
- **Reviewer panel routing** per CLAUDE.md's decision tree. Doc-only commits run doc-reviewer; code commits run senior-engineer + security-auditor + code-flow-reviewer (+ test-reviewer when tests change, +db-reviewer when migrations/schema change).
- **Declared breaks** subsection on any commit that introduces a transitional state. Reviewers plan-suppress findings inside declared scope; over-declaring degrades reviewer accuracy.
- **Test cadence**: tests land in the same commit as the code they cover. No separate "tests next commit" pattern.
- **Observability cadence**: every commit that adds runtime behavior emits a structured log + (where applicable) a counter tagged `metric: true` for Phase-5 CloudWatch sink. Phase 1 has no CloudWatch backend; logs carry the metric tag for later.
- **Latency budget**: <200ms p95 for all evaluations. Phase 5 load test enforces.
- **Cost ceiling**: CAD 1000/month operational at single-tenant production-like volumes.
- **Quality gate**: case-1 (dashboard ATO ~50 shipments) and case-2 (API ATO ~21K shipments) must both detect at REVIEW or BLOCK in Phase 6 staging replay. Recall must match or improve freight_risk's 98% on case 2.

---

## Phase 1 (Week 1) — Foundation + signal/baseline core

**Detailed plan**: `PLAN_PHASE_1.md`.

### Scope

Adapt the pre-staged FreightSentry-calibrated foundation to single-service Python, stand up the skeleton (FastAPI + Postgres + Alembic + Docker Compose), wire a stub booking endpoint, then implement the signal/baseline/enrichment core with Layer 1 (hard-block) + Layer 3 (signal noisy-OR) scoring. Layer 2 (account-prior + trust-score consumption) is **deferred to Phase 2**.

### Batches

| Batch | Day | Theme |
|---|---|---|
| 1A | 1 | Adapt pre-staged docs (CLAUDE.md trim, conventions consolidation, reviewer agent adaptation, .ai/* trim, .ai/decisions.md creation from Design Context) |
| 1B | 2-3 | Skeleton: Docker Compose, Alembic, schema migration (12 tables), FastAPI lifespan, asyncpg pool, auth, RLS, health |
| 1C | 3 end | Stub booking endpoint: payload acceptance, entity upsert, ALLOW 0.0, decision persistence |
| 1D | 4-7 | Signal + baseline core: enrichment pipeline, baseline decay/update/save, trust score function, context builder, 10 signals, 12-15 rules, DSL evaluator, Layer 1+3 scoring, end-to-end wiring |

### Key files

```
CLAUDE.md (trimmed)
.ai/conventions.md, .ai/decisions.md, .ai/rules.md, .ai/schema.md, .ai/enrichment.md, .ai/system-status.md
.ai/gotchas/python.md, .ai/gotchas/postgres.md
.claude/agents/{senior-engineer,security-auditor,code-flow,test,db,doc}-reviewer.md
pyproject.toml, docker-compose.yml, .env.example, README.md, .gitignore, .dockerignore
alembic.ini, alembic/env.py, alembic/versions/0001_initial.py
app/main.py, app/db.py, app/auth.py, app/models.py, app/config.py
app/api/health.py, app/api/booking.py
app/signals.py (pure helpers), app/enrich.py, app/baseline.py, app/trust.py, app/context.py
app/signals/{ip_class_deviation,velocity_burst,dormancy_then_activity,new_route,value_outlier,disposable_email,dummy_phone,unfamiliar_origin,unfamiliar_ip,ip_geolocation_country}.py
app/dsl.py, app/scoring.py, app/rules.yaml
scripts/fetch_enrichment.py
tests/ (integration + unit suites)
```

### Validation gates

- `ruff check app/ tests/` clean
- `mypy app/` clean (strict mode)
- `pytest tests/ -v --asyncio-mode=auto` all pass
- Integration test: case-2 fixture causes `ip_class_deviation` + `velocity_burst` to fire and routes to BLOCK
- Integration test: hard-block rule (e.g. Tor exit IP) returns BLOCK with score 1.0 in <50ms
- Latency check: 95th percentile of synthetic booking evaluations <200ms

### Dependencies

None. Greenfield start from pre-staged docs.

### Risk assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Batch 1A trim is cosmetic, not genuine — pre-staged docs still reference Go/gRPC/Redis/AI | High | Reviewer panel explicitly checks for stale references; doc-reviewer flagged on Batch 1A. Operator checkpoint after Batch 1A before proceeding. |
| DSL evaluator security holes (unrestricted AST nodes, `__builtins__` leak) | Critical | Whitelist exactly: `BoolOp`, `UnaryOp(Not)`, `Compare` w/ `Gt`/`Lt`/`GtE`/`LtE`/`Eq`/`NotEq`, `Name` (env lookup only), `Constant` (int/float/str/bool/None), `Load`, `And`, `Or`, `Not`. Any other node → `DSLError`. `eval` runs with `__builtins__: {}` AND a frozen env dict. Security-auditor explicitly verifies completeness. |
| Baseline race: concurrent bookings for same customer → lost update | High | `SELECT FOR UPDATE` in same transaction as baseline write. Code-flow reviewer verifies. Integration test for concurrent update. |
| Per-IP-type half-life requires extending stat-dict shape — easy to miss `type` field on writes | Medium | Single point of write through `baseline.add_ip_observation()`; unit test asserts `type` populated on every IP observation. |
| Decision persistence is on the hot path by design (single transaction: INSERT shipments + INSERT decisions + baseline save + UPDATE customers) — accommodated by latency budget | Medium | Single-txn write is bounded; baseline JSONB save is the dominant cost (~5-15ms). Persistence failure returns 500; idempotency on `(tenant_id, request_id)` guarantees retry correctness. Reviewers verify no fire-and-forget writes leak past the txn boundary. Operator amendment 2026-05-25 reframed this — prior framing as background-write was incorrect. |
| Adapting reviewer agents introduces regressions (e.g. dropping a security check that still applies) | Medium | Reviewer agents adapted in Batch 1A; reviewers themselves verify the trim by being routed against Batch 1B-1D commits. |

### Explicitly deferred from Phase 1

- Trust score consumption: `app/trust.py` ships in Phase 1 (function defined, called by `build_context`) but **no Phase 1 rule conditions read `trust_score`**. The value is attached to Context but unused. Declared break, consumer arrives Phase 2.
- Account-prior layer (Layer 2 of scoring) — Phase 2.
- ~13 FreightSentry-port rules (trust/dormancy/lock-in/residential-asn) — Phase 2 (most depend on trust_score or `customer_locked_cloud_api` derivation).
- Modification endpoint — Phase 3.
- Feedback endpoint — Phase 3.
- Per-tenant config validation, admin endpoints — Phase 4.
- Observability backend (CloudWatch EMF) — Phase 5.
- Production deploy — Phase 6.

---

## Phase 2 (Week 2) — Trust score, account-prior, full rule library

### Scope

Wire Layer 2 (account-prior + trust-contribution + flag-prior + maturity downweighting) into the scorer. Implement `customer_locked_cloud_api` derivation. Port the ~13 FreightSentry-exclusive rules. Add the freight_risk rules deferred from Phase 1 (recipient overlap, additional novelty/lock-in/dormancy rules). Round rule total to ~95-100.

### Key files

```
app/scoring.py (extended to Layer 2 + maturity downweight on Layer 3)
app/trust.py (consumed by scorer; tests added for edge cases)
app/context.py (adds `customer_locked_cloud_api` derivation)
app/signals/{dormancy_then_activity_extended,recipient_overlap,customer_lock_in,residential_proxy_farm,…}.py
app/rules.yaml (expanded to ~95-100 rules; tuning thresholds from verification doc applied — cadence z>6, velocity_daily_api=50, etc.)
tests/ (case-1 fixture: dashboard ATO; account-prior unit tests; maturity-downweight unit tests)
```

### Validation gates

- All Phase 1 gates
- Integration test: case-1 fixture (dashboard ATO ~50 shipments) detects at REVIEW or BLOCK
- Integration test: case-2 fixture re-runs at REVIEW or BLOCK with the extended rule set; recall does not regress
- Unit test matrix for account-prior: new customer (maturity≈0) elevates prior; established customer (maturity≈1) zeroes prior; high trust score zeroes trust_risk; flagged_count tiers map correctly to flag_prior
- Unit test: `customer_locked_cloud_api` flips True when cloud_share_n/total > 0.95 AND api_share_n/total > 0.95 AND effective_observations ≥ 20
- Latency unchanged from Phase 1 (Layer 2 is constant-time arithmetic)

### Dependencies

- Phase 1 baseline + decay + context-builder + Layer 1+3 scoring

### Risk assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Account-prior tuning constants (MaturityK=0.30, flag_weights 4-tier) diverge from FreightSentry-production values (0.70, 2-tier). FPR/recall at the new operating point is unmeasured. | Medium | Constants are Design-Context-authoritative; verification doc §3.3 records the divergence. Phase 6 staging replay measures FPR; constants exposed as tenant-config overridable for post-launch tuning. |
| Maturity downweight applied incorrectly (wrong direction, wrong K) silently miscalibrates scores | High | Unit-test matrix covers boundary cases. Code-flow reviewer verifies formula matches Design Context. |
| Recipient-overlap signal requires cross-customer query: SELECT on shipments joined by destination HMAC | Medium | Index on `(tenant_id, destination_hmac, created_at)`. Query bounded to last 30 days. Load-test in Phase 5. |
| Rule-condition typo against trust_score field name silently no-ops | Medium | DSL parser asserts all `Name` references in rules.yaml resolve to a known Context field at app startup (fail-fast on unknown field). Same pattern freight_risk uses (`_NUMERIC_FIELDS`/`_BOOL_FIELDS` whitelist). |

### Explicitly deferred

- Per-rule per-customer rule-weight learning (Mechanism C) — post-launch.
- Modification endpoint — Phase 3.

---

## Phase 3 (Week 3) — Modification endpoint, feedback endpoint, multi-tenant scoping audit

### Scope

`POST /api/v1/shipments/modification/evaluate`: accepts a booking modification, evaluates against modification-specific signals (time-since-booking bucket, magnitude of change, direction, modification velocity). `POST /api/v1/shipments/feedback`: accepts approved/rejected labels for prior decisions, folds into baseline `n`/`r_n` fields with decay.

Multi-tenant scoping audit: confirm every query in `app/` filters by `tenant_id`; confirm RLS policies are present on every tenant-scoped table; add an integration test that attempts cross-tenant read with row-level security enabled and confirms it fails.

### Key files

```
app/api/modification.py, app/api/feedback.py
app/signals/modification_{time_since_booking,magnitude,direction,velocity}.py
app/rules.yaml (modification-specific rules added)
app/baseline.py (feedback-driven r_n update path)
alembic/versions/0002_modification_rls_audit.py (any missed RLS policies)
tests/integration/{modification,feedback,tenant_scoping}.py
```

### Validation gates

- All Phase 1-2 gates
- Integration test: modification within-hour after booking, recipient changed to freight forwarder, fires at REVIEW or BLOCK
- Integration test: feedback marking a prior decision as "fraud confirmed" increments customer's `fraud_confirmed_count`, decrements baseline `n` for offending dimensions, increments `r_n`
- Integration test: tenant_a session attempting to read tenant_b customer fails at RLS layer, never reaches app code
- Latency: modification endpoint <200ms p95; feedback endpoint <100ms p95 (no scoring path)

### Dependencies

- Phase 1 schema (decisions, customer_baselines, feedback tables)
- Phase 2 rule library

### Risk assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Modification signals are fresh design — no reference implementation. Initial thresholds are hypotheses. | High | Phase 3 ships with conservative weights (lower than booking equivalents); Phase 6 replay measures impact; thresholds tunable via rules.yaml. |
| Feedback path can corrupt baseline if r_n update races with n update | High | All baseline writes through `baseline.update()` go through `SELECT FOR UPDATE`; feedback path is no exception. Unit test for concurrent feedback + booking. |
| RLS bypass via SET LOCAL role escalation — overlooked policy on join table | Critical | Security-auditor explicitly enumerates every table touched by RLS policy migration; integration test attempts every cross-tenant read pattern from CLAUDE.md threat model. |
| Modification of a booking that doesn't exist in our state (e.g. external race) → no baseline to compare against | Medium | Modification endpoint requires `request_id` matching a prior booking; returns 404 if absent. Idempotency on modification's own `request_id`. |

### Explicitly deferred

- Per-tenant config validation — Phase 4.
- Admin endpoints — Phase 4.
- Observability — Phase 5.

---

## Phase 4 (Week 4) — Per-tenant config, cold-start, admin reads

### Scope

`tenants.config` JSONB column validated by `TenantConfig` Pydantic model (`app/config_tenant.py`). Tenant onboarding script (`scripts/tenant_onboard.py`) creates tenant row, generates API token, validates config. Cold-start window enforcement: for the first `cold_start_days` (per-tenant config), mid-band scores route to REVIEW more aggressively (compress the ALLOW band).

Two read-only admin endpoints:
- `GET /api/v1/admin/customers/{id}/baseline` — admin-role auth, PII fields HMAC'd in response
- `GET /api/v1/admin/decisions/{request_id}` — admin-role auth, full decision details with rule trail

### Key files

```
app/config_tenant.py (TenantConfig Pydantic model)
app/api/admin.py
app/auth.py (admin role added)
scripts/tenant_onboard.py
alembic/versions/0003_tenant_config_validation.py (JSONB validation constraint, if applicable)
tests/integration/{tenant_config,cold_start,admin_endpoints}.py
```

### Validation gates

- All Phase 1-3 gates
- Integration test: tenant_a with `allow_max=0.55, block_min=0.75` decides differently from tenant_b with defaults on the same payload
- Integration test: cold-start tenant (within `cold_start_days`) routes mid-band scores to REVIEW more aggressively than mature tenant
- Integration test: admin endpoint returns 401 without admin-role token; returns 403 attempting cross-tenant; HMAC's email/phone fields in response
- Onboarding script creates valid tenant, prints API token (one-time), tenant operates normally on next request

### Dependencies

- Phase 1 tenants table
- Phase 2 maturity arithmetic (for cold-start logic)

### Risk assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Tenant config schema validates at write but stale config in-process cache serves wrong values after update | Medium | Phase 4 reads config per request; in-process cache (with 60s TTL) lands in Phase 5 only after profiling shows it matters. |
| Admin endpoint leaks PII via non-HMAC'd field in response | High | Security-auditor verifies every field in response model is either non-PII or HMAC'd. Pydantic response model is the authoritative schema. |
| Cold-start compression introduces a per-tenant non-monotonic behavior change at day N → N+1 | Medium | Compression is linear over the window: factor = max(0, 1 - days_since_first/cold_start_days). Smooth boundary. |

### Explicitly deferred

- Observability backend — Phase 5.

---

## Phase 5 (Week 5) — Observability, security hardening, load test

### Scope

Structured-log emitter (`app/observability.py`) producing JSON lines on stdout with `metric: true` tag for counters/histograms. CloudWatch EMF sidecar wiring (deferred to Phase 6 deploy, but log shape ready). In-process tenant-config cache (60s TTL). Security audit pass: secrets (Pydantic Settings sourcing from environment), HMAC scope (every PII field at egress), RLS coverage final audit. Load test scripted via `locust` or `k6` against staging Docker Compose.

### Key files

```
app/observability.py
app/cache.py (tenant config cache)
docs/06-infrastructure.md (CloudWatch EMF, secrets, deploy targets — adapted from pre-staged)
load_tests/booking.py, load_tests/modification.py
docs/security-audit-phase-5.md (audit report)
```

### Validation gates

- All Phase 1-4 gates
- Load test: 100 TPS sustained for 10 minutes at <200ms p95 against staging Docker Compose
- Security audit: written report with audit IDs (S-/D-/P- per CLAUDE.md taxonomy)
- Per-rule metric counters fire on every evaluation (rules-fired histogram, decision distribution, latency)

### Dependencies

- All prior phases

### Risk assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Load test surfaces Postgres-tuning issues (slow count queries, missing indexes, lock contention) | High | Index plan documented in Phase 1 schema; load test runs against real Postgres with realistic baseline cardinality; Phase 5 has budget for index additions. |
| Velocity-count SQL queries dominate latency at high tenant cardinality | Medium | Consider materialized view per (tenant_id, customer_id, hour_bucket) if load test shows aggregate > 50ms p95. |
| Observability cache miss on tenant config doubles latency | Low | Cache TTL 60s; warmup on lifespan; cache stampede avoided via per-tenant async lock. |

### Explicitly deferred

- Production deploy — Phase 6.
- Per-rule per-customer weight learning (Mechanism C) — post-launch.

---

## Phase 6 (Week 6) — Deploy + fixture replay + cost validation

### Scope

Deploy dual-region: **production** to `ca-central-1` (Canadian data residency), **test/staging** to `us-east-2` (cheaper, separate failure domain). Single-tenant ECS Fargate task per region. Single RDS Postgres `db.t4g.small` per region (or operator-confirmed instance class). Run `fetch_enrichment.py` against production data sources. Replay case-1 + case-2 fixtures end-to-end via staging environment in `us-east-2`. Promote to `ca-central-1` only after staging-replay quality gates pass. Measure FPR alongside recall. Confirm monthly cost projection within CAD 1000 ceiling (combined across both regions; staging instance allowed to be smaller).

### Key files

```
infra/ (ECS task def, RDS module, ALB, secrets)
docker-compose.production.yml (validation against prod-like local image)
replay/{case_1_fixture,case_2_fixture}.json
docs/replay-report-phase-6.md (recall, FPR, latency, cost)
REPORT_PHASE_6.md
```

### Validation gates

- Deployment health: ECS task healthy, ALB reachable, /health/ returns 200, /api/v1/shipments/booking/evaluate accepts and persists
- Case-1 replay: dashboard ATO ~50-shipment fixture detects ≥45 shipments at REVIEW or BLOCK; first detection within first 10 shipments
- Case-2 replay: API ATO ~21K-shipment fixture detects ≥98% (≥20.6K) at REVIEW or BLOCK; matches freight_risk's published recall
- FPR: on a known-legitimate fixture (operator-supplied), <2% routed to REVIEW (operator-confirms tolerable; threshold-tune if not)
- Latency under load: p95 <200ms; p99 <500ms
- Cost: 30-day extrapolation from CloudWatch billing exporter or AWS Cost Explorer <CAD 1000/month

### Dependencies

- All prior phases
- Operator: confirm RDS instance class per region, supply MaxMind and IP2Proxy license keys via AWS Secrets Manager (separate secrets per region), supply a legitimate-traffic fixture for FPR measurement
- AWS region split confirmed at plan time: production `ca-central-1`, test/staging `us-east-2`

### Risk assessment

| Risk | Severity | Mitigation |
|---|---|---|
| First production deploy surfaces config / secret / networking gaps | High | Local docker-compose.production.yml validates the same image/config combo before push. Smoke test against staging RDS before production. |
| FPR exceeds operator tolerance — REVIEW queue too large | High | Phase 6 surfaces measurement to operator; threshold tuning via tenants.config (per-tenant overrides). Per-rule weight tuning out of scope for v1; deferred to post-launch. |
| Case-1 replay catches fewer than expected (signal stack assumptions wrong) | High | If <80% case-1 detection, halt cutover and add signals before production. Acceptable: REVIEW (operator confirms) rather than BLOCK. |
| Cost overruns: enrichment refresh + load-balancer + RDS storage exceed CAD 1000 | Medium | Pre-deploy cost projection from AWS Calculator; daily monitor for first 30 days; tenant-config-based throttle on enrichment cache writes if needed. |
| MaxMind / IP2Proxy license activation fails on first request | Medium | Pre-deploy validation: refresh script runs against production credentials in staging environment before cutover. |

### Explicitly deferred (out of v1 scope)

- Per-rule per-customer rule-weight learning (Mechanism C)
- Operator dashboard / admin UI
- LLM integration of any form
- Device fingerprint rules / user-agent rules
- PDF report generation, daily summary jobs
- Cross-tenant intelligence auto-sharing (capability stubbed in `global_blocked_vectors` schema)
- Federated auth (OAuth/SSO/SAML)
- Customer-supplied custom signals/rules
- Hot-reload of rules via fsnotify

These remain deferred indefinitely unless the operator explicitly pulls them in via a new phase prompt.

---

## Plan amendments

Mid-phase amendments tracked in `MASTER_PLAN_AMENDMENTS.md` (created lazily when first amendment lands). Amendments are limited to:

- Renamed files (record old→new)
- Scope movements between phases (record what moves, why)
- Discovered constraints that re-shape a future phase (record the constraint and the affected phase)

Amendments do not re-litigate the Design Context. Items the operator wants reconsidered enter via a new phase prompt, not via amendments.

---

## Workflow recap

Each phase, after completion:

1. Operator reviews the phase plan (`PLAN_PHASE_{N}.md`).
2. Operator approves; execution begins.
3. Each commit runs the 6-step cycle (CLAUDE.md).
4. At each batch boundary within a phase, post a one-line summary and wait for operator approval before proceeding.
5. End-of-phase report (`REPORT_PHASE_{N}.md`) — aggregate stats, per-batch disposition, plan deviations, reviewer-caught corrections (file:line refs), explicitly deferred items.
6. Operator approves the report; next phase begins.

For multi-hour autonomous runs, operator launches with `--permission-mode bypassPermissions` on a clean feature branch. `.claude/STATUS.md` `Unforeseen / checkpoints` table is the only mid-run escalation channel (validation failures after second retry, or unanticipated decisions).
