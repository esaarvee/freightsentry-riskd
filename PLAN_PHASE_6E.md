# PLAN_PHASE_6E — Phase 6 wrap + production launch readiness

> **Phase 6, Batch E.** Synthesizes 6A–6D into the aggregate Phase 6 report, the calibration backlog document, and the production-launch checklist. **No new code** — this batch is pure synthesis + documentation.
>
> Companion: 6A → 6B → 6C → 6D → **6E (this batch)**.

---

## Pre-plan verification

6E's content is templated against the upcoming batch reports — its detail is finalized after 6A–6D execute. This plan defines the SHAPE of the wrap deliverables:

1. **Aggregate report** at `REPORT_PHASE_6.md` — follows the Phase 4/5 report pattern.
2. **Calibration backlog** at `docs/calibration-backlog.md` — explicit deferred-list from 6C findings + carry-forwards from earlier phases.
3. **Production launch checklist** at `docs/production-launch-checklist.md` — operator-executable launch sequence.
4. **BUGS.md drain** for any Phase 6 entries discovered + final state of Phase 5 carry-forwards (multi-stage Dockerfile resolved in 6D.1; redundant index can be deferred; serial-test 409 unreachable, _assert_decisions_equivalent dup, docker-compose .env mismatch — operator triages between phases).
5. **`.ai/decisions.md` Phase 6 closeout** entry — aggregate-level Phase 6 architectural decisions documented (already happens per-batch in 6A.4 / 6B.3 / 6C.5 / 6D.9; 6E adds the closeout summary).

---

## Decisions absorbed

| Decision | Value | Source |
|---|---|---|
| 6E scope | Aggregate Phase 6 report + calibration backlog + production-launch checklist + BUGS drain + decisions closeout | Phase 6 prompt |
| Deferred-items enumeration | Case-1 replay (no data, deferred indefinitely); modification weights (post-launch); previously-rejected weights (post-launch); FPR-concerning rules from 6C (calibration backlog); pool-max scaling (post-launch load profile); sub-60s tenant config invalidation (deferred unless requirement emerges) | Phase 6 prompt |
| Phase 5D auth chicken-and-egg | Documented in launch readiness — `api_tokens` + `app_users` have RLS dropped per migration 0009 because lookup precedes `set_tenant_id`; application-layer scoping is the active defense | Phase 6 prompt watch point |
| Single-customer case-3 cluster caveat | Documented in 6E aggregate + calibration backlog (cluster ≠ population) | AskUserQuestion 2026-06-03 |
| 5-month observation window methodology | Documented in launch checklist (when to start tuning, what data to collect) | Phase 6 prompt |
| NO new code | 6E is synthesis-only | Phase 6 prompt |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md.
- **Reviewer panel MANDATORY**:
  - **6E.1 (calibration backlog + production launch checklist)**: doc-only commit under `docs/` → **doc-reviewer + senior-engineer**.
  - **6E.2 (REPORT_PHASE_6.md aggregate)**: doc-only commit. → **doc-reviewer + senior-engineer**.
  - **6E.3 (`.ai/decisions.md` Phase 6 closeout + BUGS.md drain + STATUS.md sync)**: `.ai/decisions.md` amendment → standard + doc-reviewer → **senior-engineer + code-flow + doc-reviewer**.
- Pre-commit gates enforced.

---

## Cross-batch dependencies

- **6A–6D must execute before 6E commits** — 6E synthesizes their outputs.
- After 6E.3 commits, Phase 6 is complete on `feat/refactor`. Operator decides merge-to-main + launch timeline separately.

---

## Commits

### 6E.1 — `docs/calibration-backlog.md` + `docs/production-launch-checklist.md`

**Theme**: Operator-facing deferred-items list + launch sequence. These docs are what the operator references during the 5-month observation window and at launch.

**Files**:
- NEW `docs/calibration-backlog.md`.
- NEW `docs/production-launch-checklist.md`.

#### `docs/calibration-backlog.md` structure

**Purpose**: Explicit list of items deferred to post-launch real-data observation. Future operators (or post-launch Claude Code phases) reference this to drive calibration cycles.

**Sections**:
1. **6C findings — rules surfacing FPR concerns** (populated from `docs/replay-validation.md`):
   - Per concerning rule: rule name, observed pattern (e.g. "fired on 12/10000 approved corpus records"), deferred-action ("observe ≥N fires in production over 4-8 weeks; if pattern persists, evaluate weight reduction or condition tightening").
   - If 6C surfaced no concerns: explicit "empty backlog from 6C; production observation will populate".
2. **6C findings — case-2 recall**: observed BLOCK + REVIEW rates on the 500 case-2 corpus + ALLOW count (the misses). Misses get per-record breakdown to inform which rules failed to fire.
3. **6C findings — case-3b detection on Roulottes Lupien census (Phase 6A amendment)**: observed combined fire rate for `cold_start_country_triangle_with_carrier_dropoff` + `cold_start_population_baseline_rare_with_carrier_dropoff` + existing cold-start rules on 95-record census. Detection target ≥85% (≥81 records reach REVIEW or BLOCK). If <85%, surface the gap with per-record `triggered_rules` attribution. Document single-customer cluster caveat: cluster recall ≠ population recall. Population case-3b detection awaits post-launch traffic across diverse customers.
4. **Trust-suppression on mature accounts (Phase 6A amendment) — Phase 7+ architectural workstream**:
   - Pattern: mature legitimate customer has low `account_prior`; if compromised, signals fire but combined score may not reach BLOCK.
   - Deferred to Phase 7+; this is architectural, not parameter tuning.
   - Recommended designs to evaluate: capability-based trust (per-dimension trust), session-anomaly signals (device/location change indicators), asymmetric trust freeze (rapid trust erosion on first anomaly).
5. **Population baseline thresholds (Phase 6A amendment)**:
   - Current values: 2% rarity threshold + 100-observation minimum in `app/context.py::derive_route_rarity`.
   - Tune post-launch with real production traffic data once tenant baselines accumulate diverse legitimate routes.
6. **`case_3_compound` empirical validation (Phase 6A amendment)**:
   - Currently no empirical validation possible — case-3a fraud (established-customer compromise) not observed in pre-launch data.
   - Defer to post-launch when (a) booking platform supplies `customer.registered_country` + `origin_via_carrier_dropoff` AND (b) case-3a-style fraud observed in production.
7. **Modification weight calibration** — deferred to post-launch (no real modification feedback data in Phase 6).
8. **Previously-rejected weight calibration** — deferred to post-launch.
9. **Cold-start grace multiplier (0.5)** — hardcoded; FPR impact unmeasured. Deferred to post-launch.
10. **Pool-max scaling** — current max=10; Phase 5 load test at 1100 successful checks. Deferred to post-launch load profile.
11. **Sub-60s tenant config cache invalidation** — current TTL acceptable; deferred unless requirement emerges.
12. **Case-1 replay** — deferred indefinitely (no enrichment data from the training window).
13. **Latency budget watch (Phase 6A amendment)** — 6A.7 + 6A.8 added ~4ms p95 (1 UPSERT + 1 SELECT per booking). Phase 5 baseline ~12ms → post-amendment ~16ms. Monitoring threshold: if p95 trends past 50ms (yellow flag) or 195ms (red — calibration backlog action before 200ms ceiling breach), evaluate in-process cache or query optimization.
14. **Phase-by-phase post-launch tuning timeline** (cross-reference launch checklist's 5-month observation methodology).

#### `docs/production-launch-checklist.md` structure

**Purpose**: Step-by-step operator actions for the actual production launch. Follow top-to-bottom; check each box. Detailed enough that someone unfamiliar with this specific deploy can complete it.

**Sections**:

**Phase A — Pre-deploy infrastructure (one-time)**
- [ ] AWS account ready in `ca-central-1` (production region)
- [ ] AWS GUI runbook (`docs/aws-deploy-runbook.md`) executed end-to-end:
  - [ ] VPC + security groups
  - [ ] ECR repository
  - [ ] RDS PostgreSQL 16
  - [ ] Secrets Manager entries (DATABASE_URL using `riskd_app_login`, HMAC_SECRET, MAXMIND_LICENSE_KEY, IP2PROXY_DOWNLOAD_TOKEN)
  - [ ] CloudWatch Logs group `/ecs/freightsentry-riskd`
  - [ ] IAM roles (3) with policies from `infra/iam-policies/`
  - [ ] ECS cluster + service shell with placeholder task def
  - [ ] ALB + target group → ECS service
- [ ] GitHub Secrets configured (AWS_ROLE_TO_ASSUME, AWS_REGION, AWS_ACCOUNT_ID, ECR_REPOSITORY, ECS_CLUSTER, ECS_SERVICE, ECS_TASK_DEFINITION_FAMILY, SNYK_TOKEN, SMOKE_TEST_URL, SMOKE_TENANT_TOKEN)

**Phase B — Pre-deploy migrations + tenant bootstrap**
- [ ] Run `alembic upgrade head` via one-off ECS task (using `ALEMBIC_DATABASE_URL` superuser DSN)
- [ ] Verify `riskd_app_login` role exists in RDS
- [ ] Verify `riskd_app` role has no LOGIN permission
- [ ] **Verify migrations 0010 (`customer_baselines.country_route_stats`) + 0011 (`customers.registered_country` + `tenant_route_baselines` + RLS) applied (Phase 6A amendment)**
- [ ] **Verify `tenant_route_baselines` is empty for new tenant**: `SELECT COUNT(*) FROM tenant_route_baselines WHERE tenant_id = $first_tenant` returns 0. This is expected cold-start state; the table populates via the runtime UPSERT in `app/api/booking.py` as bookings land.
- [ ] **Platform integration verification (launch-blocking, Phase 6A amendment)**: confirm the booking platform sends `customer.registered_country` (ISO 3166-1 alpha-2) and `shipment.origin_via_carrier_dropoff` (bool) in production booking payloads. Case-3b detection signals (`customer_country_triangle_mismatch`, `shipment_route_rare_for_tenant`) are no-ops until these structured fields flow. Until then, the case-3b compound rules cannot fire on real traffic.
- [ ] Run `python scripts/tenant_onboard.py --slug <first-tenant>` via one-off ECS task; capture returned tenant token; store in Secrets Manager
- [ ] **RLS verification**: connect to RDS as `riskd_app_login`, run a cross-tenant query (`SELECT * FROM customers` without setting `app.tenant_id`) — confirm 0 rows returned
- [ ] **RLS verification on new table (Phase 6A amendment)**: same query against `tenant_route_baselines` — confirm 0 rows returned without `set_tenant_id`

**Phase C — First deploy**
- [ ] Push first `v1.0.0` tag: `git tag v1.0.0 && git push origin v1.0.0`
- [ ] Monitor `.github/workflows/deploy.yml` workflow run in GitHub UI
- [ ] Verify ECS service rolls over: in ECS console, observe new task definition revision becomes ACTIVE, old task drains
- [ ] Verify ALB target group: new ECS task registers as healthy
- [ ] Smoke test green (deploy.yml asserts; verify the smoke-test step output)
- [ ] CloudWatch Logs: verify EMF metric events flowing to `/ecs/freightsentry-riskd` log group
- [ ] CloudWatch Metrics: verify embedded-metric-format metrics auto-extracted; key metrics (decision-rate, latency-p95, rule-fire-counts) visible

**Phase D — Post-deploy verification (day 1)**
- [ ] EMF metrics flowing
- [ ] Tenant config cache hit ratio at expected baseline (from `docs/load-test-phase-5.md`)
- [ ] Decision rate distribution: ALLOW dominates as expected on legitimate-traffic patterns
- [ ] Error rate < 0.1%
- [ ] **`tenant_route_baselines` population check (Phase 6A amendment)**: `SELECT COUNT(*) FROM tenant_route_baselines WHERE tenant_id = $1` — should grow with each booking. Zero growth after Phase C deploy indicates the update path is broken; investigate before continuing.
- [ ] **`customers.registered_country` population rate (Phase 6A amendment)**: `SELECT COUNT(*) FILTER (WHERE registered_country IS NOT NULL)::float / COUNT(*) FROM customers WHERE tenant_id = $1 AND first_seen >= now() - interval '24 hours'` — indicator of platform integration health. Target: >95% of new customers carry structured country once platform integration is live. <50% suggests platform integration is not sending the field (case-3b detection will be impaired).

**Phase E — Day 1–7 monitoring**
- [ ] Latency p95 < 200ms (project ceiling)
- [ ] Latency p99 trends stable
- [ ] **Latency p95 trend monitoring (Phase 6A amendment)**: Phase 5 baseline was ~12ms; with +4ms overhead from 6A.7 + 6A.8, post-deploy baseline shifts to ~16ms. Watch for trend past 50ms (yellow flag — investigate query performance) or 195ms (red — calibration backlog action before ceiling breach).
- [ ] False-positive observations: operator-flagged ALLOW→BLOCK transitions or BLOCK→ALLOW transitions get logged for the calibration backlog
- [ ] Any calibration-backlog rules' fire rate observed; pattern compared to 6C prediction

**Phase F — Week 1–4 (initial observation window)**
- [ ] Calibration backlog items get production-frequency data
- [ ] No tuning yet — observation only
- [ ] **Population baseline fire rate monitoring (Phase 6A amendment)**: `shipment_route_rare_for_tenant` fire rate per tenant. Target: <10% of bookings. If >10% sustained, suggests insufficient baseline data (tenant still in cold-start) OR threshold too strict (2% rarity cutoff may need calibration). Log to calibration backlog for post-launch evaluation.

**Phase G — Month 2–3 (first tuning pass)**
- [ ] With ≥30 days production data, evaluate calibration backlog items
- [ ] Per item: confirm pattern, design tuning intervention (weight reduction, condition tightening), run staged replay if a recent corpus is available, plan-mode the tuning commit
- [ ] **Tuning commits follow the same CLAUDE.md commit cycle as Phase 6** — reviewer panel mandatory; declared breaks if any; per-commit validation

**Phase H — Month 4–5 (second tuning pass)**
- [ ] Modification weights + previously-rejected weights become tuneable with real feedback latency data
- [ ] Re-evaluate cold-start grace multiplier with FPR-on-new-tenant evidence

**Phase I — Month 5+ (ongoing operation)**
- [ ] Calibration cycles continue
- [ ] Phase 7+ scope opens (auto-rollback, multi-environment GitHub Actions promotions, additional fraud detection)

**Phase 5D auth chicken-and-egg awareness** (always-on):
- RLS is DROPPED on `api_tokens` + `app_users` because token lookup precedes `set_tenant_id`. Application-layer `tenant_id` filtering is the active defense. Any future change to token validation must preserve this defense; there is no DB-layer backstop. Documented in `docs/security-audit-rls-phase-5.md`.

**Limitations of v1 launch**:
- No auto-rollback (manual via ECS console)
- No CI integration tests (unit + Snyk only; integration tests run locally)
- No auto-migration on deploy (operator one-off task)
- No IaC (AWS GUI runbook; future Terraform/CDK scope)
- No multi-region (single-region per environment; `ca-central-1` production)
- Single-customer case-3 cluster validated (not generalizable to population case-3 until real-data observation)

**Validation**:
- Manual review by operator.
- pre-commit markdown gates.

**Risk level**: low (doc).

**Reviewer routing**: → **doc-reviewer + senior-engineer**.

**Declared breaks**: none.

---

### 6E.2 — `REPORT_PHASE_6.md` aggregate

**Theme**: The Phase 6 wrap report. Same shape as `REPORT_PHASE_5.md`.

**Files**:
- NEW `REPORT_PHASE_6.md`.

**Structure** (mirrors Phase 5 wrap):

1. **Header + commit-count summary** (per-batch breakdown)
2. **Per-batch summary**:
   - 6A: commit count, test count delta, reviewer panel verdict distribution
   - 6B: same
   - 6C: same + 6C measurement findings
   - 6D: same
   - 6E: same
3. **Reviewer-panel corrections across the phase** — list each reviewer-caught issue, the commit that surfaced it, the resolution
4. **Plan deviations** — any execution that diverged from the per-batch plans (substantive drift only; trivial drift in commit messages alone)
5. **Production bugs caught** — any pre-launch defects surfaced during 6C replay or 6D smoke-test that required code fixes
6. **BUGS.md state** — Phase 5 carry-forward dispositions, new Phase 6 entries, drain table
7. **6C measurement findings (strict enumeration)**:
   - Approved corpus 10K: FPR breakdown (BLOCK count + REVIEW count, per-transaction rules-contributing)
   - Case-2 corpus 500: BLOCK + REVIEW + ALLOW; recall = (BLOCK + REVIEW) / 500
   - Case-3b corpus 95: BLOCK + REVIEW + ALLOW; combined detection rate target ≥85% (≥81 records reach REVIEW or BLOCK) via case-3b compounds + existing cold-start rules. Per-record `triggered_rules` attribution.
   - Case-3a empirical validation deferred to post-launch (case_3_compound not expected to fire on case-3b census)
   - Single-customer cluster caveat for case-3 (Roulottes Lupien only)
8. **Three new rules summary (Phase 6A amendment)** — threat model coverage table:
   - `case_3_compound` (case-3a, established-customer compromise) — weight 0.70, maturity_sensitive, condition: `origin_via_carrier_dropoff AND shipment_route_unfamiliar_for_customer AND ip_fully_new AND customer_observations >= 10`. Empirical validation deferred.
   - `cold_start_country_triangle_with_carrier_dropoff` (case-3b simple) — weight 0.65, condition: `customer_country_triangle_mismatch AND origin_via_carrier_dropoff AND customer_observations < 10`. Validated against Roulottes Lupien census.
   - `cold_start_population_baseline_rare_with_carrier_dropoff` (case-3b sophisticated) — weight 0.70, condition: `shipment_route_rare_for_tenant AND origin_via_carrier_dropoff AND customer_observations < 10`. Fires conditional on tenant baseline state.
9. **New subsystem summary (Phase 6A amendment)** — tenant route population baseline:
   - Table `tenant_route_baselines` (PK on `(tenant_id, customer_country, origin_country, destination_country)`; RLS enforced)
   - Migration 0011 with empty seed for prototype data
   - Synchronous UPSERT on every booking commit (`app/tenant_route_baselines.py::update_tenant_route_baseline`)
   - Eval-time derivation via `derive_route_rarity` (2% rarity threshold + 100-observation minimum)
   - Rule integration via `cold_start_population_baseline_rare_with_carrier_dropoff`
10. **Structured-field architectural pattern (Phase 6A amendment)** — `origin_via_carrier_dropoff` and `customer.registered_country` both follow the same pattern: platform supplies the signal at booking time; freightsentry-riskd consumes structured Pydantic-field passthrough; replay corpus injects ground truth where known (CA for Roulottes Lupien); signal returns False/None when absent, eliminating accidental false positives on corpora without ground truth. Pattern documented for future case-N detection additions. Address-string parsing rejected.
11. **Deferred calibration backlog** — cross-reference `docs/calibration-backlog.md`
12. **Phase 6 readiness assessment**:
   - Code surface: case-3a + case-3b detection live; CAD default; structured `Customer.registered_country` field; population baseline subsystem
   - Field count: `ALLOWED_CONTEXT_FIELDS` 71 → 76 (+5); rule count 58 → 61 (+3)
   - Infrastructure artifacts: Dockerfile + ECS task def + IAM JSONs + runbook + workflows
   - Test coverage: ~918+ + Phase 6 additions (~50 new tests)
   - Security posture: unchanged from `docs/security-audit-rls-phase-5.md` (Phase 6 adds RLS-protected table; no posture change)
   - Observability: unchanged from `docs/observability.md` baseline; new rule-fire events emit automatically via existing EMF mechanism
   - Latency budget: ~16ms p95 baseline (was ~12ms); 184ms ceiling headroom retained
10. **Open items for post-launch**:
    - All calibration backlog
    - 5-month observation window methodology in launch checklist
    - Phase 7+ scope hints (auto-rollback, multi-environment, etc.)
11. **Phase 6 totals**: commits, tests added, files touched, reviewer cycles

**Validation**: manual review by operator.

**Risk level**: trivial.

**Reviewer routing**: → **doc-reviewer + senior-engineer**.

**Declared breaks**: none.

---

### 6E.3 — `.ai/decisions.md` Phase 6 closeout + `.claude/BUGS.md` drain + `.claude/STATUS.md` sync

**Theme**: Final Phase 6 ledger entries. Closes BUGS items resolved (or DEFERRED) and adds a STATUS row marking phase completion.

**Files**:
- MODIFY `.ai/decisions.md` — Phase 6 closeout summary section: links to per-batch decisions amendments (6A.4, 6B.3, 6C.5, 6D.9), Phase 6 verdict, hand-off to post-launch.
- MODIFY `.claude/BUGS.md`:
  - Phase 5 carry-forwards:
    - Multi-stage Dockerfile carryforward → `RESOLVED: 6D.1`
    - Redundant `ix_api_tokens_tenant` index → `DEFERRED to Phase 7 cleanup` (low-priority; write amplification negligible)
    - 409 catch unreachable in serial tests → `DEFERRED to Phase 7` (low-priority; defense-in-depth code, no test coverage gap)
    - `_assert_decisions_equivalent` duplicated → `DEFERRED to Phase 7` (cleanup)
    - docker-compose `.env` localhost mismatch → `DEFERRED to Phase 7` (operator works around; documented in Phase 5 STATUS row)
  - Any new Phase 6 BUGS entries: drain or carry to Phase 7 per severity
- MODIFY `.claude/STATUS.md` — add row marking Phase 6 complete: `2026-XX-XX | Phase 6 wrap | All five batches executed; 6E.3 commits the closeout. Working tree clean on feat/refactor. Operator drives launch separately. | Operator: merge to main + tag v1.0.0 when ready per docs/production-launch-checklist.md.`

**Specifics**:
- `.ai/decisions.md` closeout is short (10-15 lines) — points to per-batch sections for detail.
- `.claude/BUGS.md` resolutions follow the existing format: `RESOLVED: <commit hash> / DROPPED / DEFERRED to <plan>`.

**Validation**:
- `pytest tests/ --asyncio-mode=auto` — full suite passes.
- `git status` clean post-commit.

**Risk level**: trivial.

**Reviewer routing**: `.ai/decisions.md` amendment → **senior-engineer + code-flow + doc-reviewer**.

**Declared breaks**: none.

---

## End-of-batch state (after 6E.3)

- `REPORT_PHASE_6.md` aggregate published.
- `docs/calibration-backlog.md` enumerates deferred items.
- `docs/production-launch-checklist.md` is operator-ready.
- `.ai/decisions.md` has Phase 6 closeout entry.
- `.claude/BUGS.md` Phase 5 carry-forwards triaged.
- `.claude/STATUS.md` carries Phase 6 completion row.
- Working tree clean on `feat/refactor`.

## Post-Phase 6 hand-off

- **Operator action**: merge `feat/refactor` to `main`; tag `v1.0.0`; execute `docs/production-launch-checklist.md`.
- **First production deploy**: `deploy.yml` workflow fires on tag push; brings up service per Phase 6D artifacts.
- **5-month observation window** begins at operator-driven launch. Calibration backlog drives post-launch phases.
- **Phase 7 scope**: auto-rollback, multi-environment GitHub Actions promotions, IaC migration, post-launch tuning. Plan structure inherits Phase 6's per-batch plan-file pattern.
