# Production launch checklist

> Step-by-step operator actions for the production launch of
> freightsentry-riskd. Follow top-to-bottom; check each box. Detailed
> enough that an operator unfamiliar with this specific deploy can
> execute it end-to-end.
>
> Cross-references:
> - `docs/aws-deploy-runbook.md` — one-time AWS infrastructure setup
> - `docs/calibration-backlog.md` — post-launch tuning items
> - `docs/security-audit-rls-phase-5.md` — RLS posture + runtime-role
>   auth chicken-and-egg context
> - `tests/integration/test_schema_golden.py` — schema anti-drift gate
> - `tests/coverage_baseline.txt` — coverage non-regression anchor

---

## Phase A — Pre-deploy infrastructure (one-time)

- [ ] AWS account ready in `ca-central-1` (production region)
- [ ] AWS GUI runbook (`docs/aws-deploy-runbook.md`) executed end-to-end:
  - [ ] VPC + subnets + security groups
  - [ ] ECR repository `freightsentry-riskd`
  - [ ] RDS PostgreSQL 16
  - [ ] Secrets Manager entries (`DATABASE_URL` using `riskd_app_login`,
        `HMAC_SECRET`, `MAXMIND_LICENSE_KEY`, `IP2PROXY_DOWNLOAD_TOKEN`)
  - [ ] CloudWatch Logs group `/ecs/freightsentry-riskd`
  - [ ] IAM roles (3) with policies from `infra/iam-policies/`:
    - [ ] `freightsentry-riskd-task-exec` (ECS task execution role)
    - [ ] `freightsentry-riskd-task` (task role)
    - [ ] `freightsentry-riskd-deploy` (GitHub Actions OIDC role)
  - [ ] ECS cluster + service shell with placeholder task def
  - [ ] ALB + target group → ECS service
- [ ] GitHub Secrets configured:
  - [ ] `AWS_ROLE_TO_ASSUME` (deploy role ARN)
  - [ ] `AWS_REGION` (`ca-central-1`)
  - [ ] `AWS_ACCOUNT_ID`
  - [ ] `ECR_REPOSITORY` (`freightsentry-riskd`)
  - [ ] `ECS_CLUSTER` (`freightsentry-riskd-cluster`)
  - [ ] `ECS_SERVICE` (`freightsentry-riskd-service`)
  - [ ] `SNYK_TOKEN`
  - [ ] `SMOKE_TEST_URL` (ALB DNS or domain)
  - [ ] `SMOKE_TENANT_TOKEN` (placeholder until Phase B completes; reset
        after first deploy)

---

## Phase B — Pre-deploy migrations + tenant bootstrap

- [ ] Run `alembic upgrade head` via one-off ECS task using
      `ALEMBIC_DATABASE_URL` (superuser DSN).
- [ ] Verify `riskd_app_login` role exists in RDS.
- [ ] Verify `riskd_app` role exists and has NO LOGIN permission.
- [ ] Verify all 5 post-squash migrations applied (`0001_foundation`
      through `0005_runtime_roles`). The squashed schema includes
      `customer_baselines.country_route_stats`, `customers.registered_country`,
      `tenant_route_baselines` + RLS, and the `riskd_app_login` runtime role.
- [ ] Verify `tenant_route_baselines` is empty for the new tenant:
      `SELECT COUNT(*) FROM tenant_route_baselines WHERE tenant_id = $1`
      returns 0. This is expected cold-start state; the table populates
      via the runtime UPSERT in `app/api/booking.py` as bookings land.
- [ ] **Platform integration verification (launch-blocking)**: confirm
      the booking platform sends `customer.registered_country`
      (ISO 3166-1 alpha-2) and `shipment.origin_via_carrier_dropoff`
      (bool) in production booking payloads. Case-3b detection signals
      (`customer_country_triangle_mismatch`,
      `shipment_route_rare_for_tenant`) are no-ops until these
      structured fields flow. Until then, the case-3b compound rules
      cannot fire on real traffic.
- [ ] Run `python scripts/tenant_onboard.py --slug <first-tenant>` via
      one-off ECS task; capture returned tenant token; store in
      Secrets Manager; update `SMOKE_TENANT_TOKEN` GitHub Secret.
- [ ] **RLS verification (existing tables)**: connect to RDS as
      `riskd_app_login`, run `SELECT * FROM customers` WITHOUT setting
      `app.tenant_id`. Confirm 0 rows returned.
- [ ] **RLS verification (`tenant_route_baselines`)**: same query against
      `tenant_route_baselines`. Confirm 0 rows returned without
      `set_tenant_id`.

---

## Phase C — First deploy

- [ ] Push first version tag: `git tag v1.0.0 && git push origin v1.0.0`
- [ ] Monitor `.github/workflows/deploy.yml` run in the GitHub Actions UI.
- [ ] Verify ECS service rollover in the ECS console: new task definition
      revision becomes ACTIVE; old task drains.
- [ ] Verify ALB target group: new ECS task registers as healthy
      (HEALTHCHECK on `/health/` passes).
- [ ] Smoke test green (the deploy workflow's `Smoke test against
      deployed endpoint` step output shows `smoke OK: ...`).
- [ ] CloudWatch Logs: EMF metric events flow to `/ecs/freightsentry-riskd`.
- [ ] CloudWatch Metrics: embedded-metric-format metrics auto-extracted;
      key metrics visible (decision-rate, latency-p95, rule-fire-counts).

---

## Phase D — Post-deploy verification (day 1)

- [ ] EMF metrics flowing.
- [ ] Tenant config cache hit ratio at expected baseline (per
      `docs/load-test-phase-5.md`).
- [ ] Decision rate distribution: ALLOW dominates as expected on
      legitimate traffic patterns.
- [ ] Error rate < 0.1%.
- [ ] **`tenant_route_baselines` population check**:
      `SELECT COUNT(*) FROM tenant_route_baselines WHERE tenant_id = $1`
      grows with each booking. Zero growth after Phase C deploy
      indicates the update path is broken — investigate before
      continuing.
- [ ] **`customers.registered_country` population rate** — indicator of
      platform integration health:
      ```sql
      SELECT COUNT(*) FILTER (WHERE registered_country IS NOT NULL)::float
             / NULLIF(COUNT(*), 0)
      FROM customers
      WHERE tenant_id = $1 AND first_seen >= now() - interval '24 hours';
      ```
      Target: >95% of new customers carry structured country once
      platform integration is live. <50% suggests platform integration
      is not sending the field — case-3b detection will be impaired.

---

## Phase E — Day 1-7 monitoring

- [ ] Latency p95 < 200ms (project ceiling).
- [ ] Latency p99 trend stable.
- [ ] **Latency p95 trend monitoring**: load-test baseline is ~16ms
      (Phase 5C load test + case-3 detection overhead).
  - **Yellow flag (≥50ms p95)**: investigate query performance;
    evaluate in-process cache on `tenant_route_baselines` reads.
  - **Red flag (≥195ms p95)**: calibration backlog action before
    the 200ms ceiling breach.
- [ ] False-positive observations: log operator-flagged
      ALLOW→BLOCK transitions or BLOCK→ALLOW transitions to the
      calibration backlog.
- [ ] Calibration-backlog rules' fire rates observed; pattern compared
      to `docs/replay-validation.md` expectations.
- [ ] **Customer baseline cold-start ramp**: monitor `customer_baselines`
      ASN-population rate. The `api_booking_from_unfamiliar_asn` rule
      requires per-customer `customer_observations >= 10` to fire; at
      launch all baselines start empty so case-2 detection capability
      ramps with booking accumulation. Day-1 case-2 detection by this
      rule will be 0% by design.
      - Query to track: `SELECT COUNT(*) FROM customer_baselines
        WHERE ip_asn_stats <> '{}'::jsonb` (number of customers
        with at least one ASN observation; ip_asn_stats column is
        jsonb NOT NULL DEFAULT '{}').
      - Expect 0% at Day 1; growth over Days 1-30 as customers
        cross the 10-observation gate.
      - If the ramp is slower than expected (e.g., low-volume
        tenants), surface to calibration-backlog item 16 for
        post-launch evaluation.
- [ ] **Held-booking backlog**: REVIEW/BLOCK bookings are HELD in
      pending state until operator feedback arrives. Operators may want
      visibility into the backlog size.
      - Query to track:
        ```sql
        SELECT COUNT(*) AS held_count
          FROM decisions d
          LEFT JOIN feedback f
            ON d.tenant_id = f.tenant_id
           AND d.request_id = f.target_request_id
         WHERE d.tenant_id = $1
           AND d.decision IN ('REVIEW', 'BLOCK')
           AND f.id IS NULL;
        ```
      - Expect non-zero from Day 1; growth depends on per-tenant
        REVIEW/BLOCK rate and operator-feedback cadence. Steady-
        state held_count should plateau (new holds ≈ feedback
        completions).
      - If held_count grows persistently (e.g., 30+ days of
        feedback never arriving), surface to calibration-backlog
        item 19 for post-launch architectural decision (force-
        fold admin endpoint or grace-period auto-fold).
- [ ] **Cold-start ramp under ALLOW-gated baselines**: customer
      baseline accumulation requires ALLOW bookings (or operator-
      approved feedback on REVIEW/BLOCK bookings). Expect ~5-15%
      longer cold-start window vs an unconditional baseline. Tenants with high pre-launch
      REVIEW rates see longer ramps. Compare per-tenant
      `customer_baselines.value_n` growth trajectory across
      Days 1-30 against the expected ALLOW-rate × bookings-rate
      product.

---

## Phase F — Week 1-4 (initial observation window)

- [ ] Calibration-backlog items accumulate production-frequency data.
- [ ] No tuning yet — observation only.
- [ ] **Population baseline fire rate monitoring**:
      `shipment_route_rare_for_tenant` fire rate per tenant. Target:
      <10% of bookings. If >10% sustained, suggests either (a)
      insufficient baseline data (tenant still in cold-start) or (b)
      threshold too strict (2% rarity cutoff may need calibration).
      Log to calibration backlog for post-launch evaluation.

---

## Phase G — Month 2-3 (first tuning pass)

- [ ] With ≥30 days production data, evaluate `docs/calibration-backlog.md`
      items 1-6.
- [ ] Per item: confirm pattern; design tuning intervention (weight
      reduction, condition tightening); run staged replay if a current
      corpus is available; plan-mode the tuning commit.
- [ ] Tuning commits follow the CLAUDE.md commit cycle: reviewer panel
      mandatory; declared breaks if any; per-commit validation.

---

## Phase H — Month 4-5 (second tuning pass)

- [ ] Modification weights + previously-rejected weights tuneable with
      real feedback latency data (calibration backlog items 9-10).
- [ ] Re-evaluate cold-start grace multiplier with FPR-on-new-tenant
      evidence (calibration backlog item 11).

---

## Phase I — Month 5+ (ongoing operation)

- [ ] Calibration cycles continue against the backlog.
- [ ] Post-launch architectural workstreams open (auto-rollback,
      multi-environment GitHub Actions promotions, IaC migration,
      additional fraud detection classes, trust-suppression workstream
      — see `docs/calibration-backlog.md` items 7 and 17).

---

## Always-on: auth chicken-and-egg awareness

RLS is DROPPED on `api_tokens` + `app_users` because token lookup precedes
`set_tenant_id` (chicken-and-egg: you need the token to know the tenant
to scope the lookup). **Application-layer `tenant_id` filtering is the
active defense.** Any future change to token validation must preserve
this defense; there is no DB-layer backstop. Documented in
`docs/security-audit-rls-phase-5.md`.

---

## v1 launch limitations (acknowledged)

- No auto-rollback (manual via ECS console per the runbook's
  "Rollback" section).
- No CI integration tests (unit + Snyk only; integration tests run
  locally against docker-compose Postgres).
- No auto-migration on deploy (operator one-off ECS task).
- No IaC (AWS GUI runbook; Terraform/CDK is post-launch scope).
- Single-region per environment (production = `ca-central-1`).
- Single-customer case-3 cluster validated (Roulottes Lupien); cluster
  recall ≠ population recall until real-data observation across
  diverse fraud actors.
