# AWS deploy runbook — freightsentry-riskd

Step-by-step operator-executable procedure for standing up
freightsentry-riskd on AWS via the AWS console (one-time
infrastructure setup). Phase 6D ships the artifacts (Dockerfile, ECS
task definition, IAM policies, GitHub Actions workflows); this
runbook walks the operator through pasting them in the right places.

Follow top-to-bottom on first deploy. Each section's checkbox
prefix `[ ]` is meant to be ticked as you go.

> **Historical: Pattern B-lite enrichment refresh module — RESOLVED.**
> The Pattern B-lite refresh module landed on 2026-06-09 (`feat/refactor`,
> PBL C0–C6). The runtime container's lifespan now spawns a 24h refresh
> task that downloads FireHOL / MaxMind / IP2Proxy / cloud-CIDR feeds
> into `ENRICHMENT_DATA_DIR` and atomically swaps a freshly-loaded
> `Enricher` on each successful tick. The `/health/` response exposes an
> `enrichment: "ok" | "degraded"` field — degraded does NOT change the
> HTTP status code, so the ALB target stays in rotation while sources
> warm up. See [`.ai/enrichment.md`](../.ai/enrichment.md) § Refresh
> module for the current architecture and the per-source download
> contract.
>
> Operator action before first production deploy: populate the
> `freightsentry-riskd/MAXMIND_LICENSE_KEY` and
> `freightsentry-riskd/IP2PROXY_DOWNLOAD_TOKEN` secret containers (CFN
> step 4) so the MaxMind + IP2Proxy paths produce successful refresh
> ticks. FireHOL + cloud-CIDR feeds refresh without secrets.
>
> Disk budget: `ENRICHMENT_DATA_DIR` needs ≥3.5 GiB free at any moment
> (IP2Proxy LITE BIN is ~1.6 GiB; atomic-replace tempfile peaks at 2×
> that during the swap). ECS Fargate ephemeral storage default 20 GiB
> is comfortable.

## IaC-managed resources (alternative to manual Phase A)

As of the INFRA-CFN buildout (June 2026), the IaC-suitable subset of
Phase A is captured in [`infra/cloudformation/freightsentry-riskd.yml`](../infra/cloudformation/freightsentry-riskd.yml).
See [`infra/cloudformation/README.md`](../infra/cloudformation/README.md)
for deploy commands, pre-deploy verification, post-deploy operator
steps, parameter table, cost projection, and deviations.

The CFN template provisions: VPC + networking, security groups, RDS,
ECR, CloudWatch log group, 5 Secrets Manager containers, IAM roles
(task-exec / task / deploy), ALB + target group + HTTPS listener, ECS
cluster.

The CFN template intentionally does NOT provision: ECS service or
task-definition (stays in `infra/ecs-task-definition.json` + the
manual procedure below so it doesn't conflict with the existing
deploy workflow's `register-task-definition` / `update-service`
ownership). Secret VALUES (only the empty containers). ACM cert
(operator issues out-of-band). Domain / Route 53 records.

The remainder of this runbook documents the manual procedures
(Phase A — non-IaC pieces; Phase B — post-deploy ops; rollback; etc.).
Operators using the CFN template should skip Phase A items already
covered by the template and pick up at the manual pieces noted in
the CFN README's "Post-deploy operator steps".

## Prerequisites

- [ ] AWS account with administrative access
- [ ] AWS region selected
  - Production: `ca-central-1`
  - Test / staging: `us-east-2`
- [ ] GitHub repository hosting this project
- [ ] Operator-side checkout of this repo (for pasting the JSON
      bodies under `infra/`)

> **Cost note.** The runbook provisions ALB + ECS Fargate + RDS
> Postgres + Secrets Manager + CloudWatch Logs. Cost depends on
> instance sizing — the runbook uses the smallest reasonable
> defaults (db.t4g.micro, ECS 1024/2048).

---

## Phase A — Pre-deploy infrastructure (one-time)

### A.1 VPC + subnets

Default VPC is acceptable for v1 (single-region, single-AZ deploy).
Document trade-offs vs custom VPC for v2 expansion.

- [ ] **VPC console** → confirm a default VPC exists in your region
- [ ] **Subnets** → identify at least 2 private subnets across 2
      AZs (ECS service requires multi-AZ for ALB target-group
      health). For default VPC these are the auto-created public
      subnets; for v1 deploy they are acceptable. Custom VPC with
      private subnets + NAT gateway is the v2 hardening path.
- [ ] Note the subnet IDs — you'll need them at the ECS service step.

### A.2 Security groups

Three security groups; created in dependency order so each can
reference the next.

- [ ] **RDS-SG** (`freightsentry-riskd-rds-sg`)
  - VPC: default
  - Inbound: TCP 5432 from `ECS-SG` (created next; come back here
    after to add the rule)
  - Outbound: default (allow all)
- [ ] **ECS-SG** (`freightsentry-riskd-ecs-sg`)
  - VPC: default
  - Inbound: TCP 8000 from `ALB-SG` only (created next; come back
    to add)
  - Outbound: default (allow all — for ECR, Secrets Manager, RDS,
    enrichment downloads)
- [ ] **ALB-SG** (`freightsentry-riskd-alb-sg`)
  - VPC: default
  - Inbound: TCP 443 from `0.0.0.0/0` (HTTPS public)
  - Outbound: TCP 8000 to `ECS-SG`
- [ ] **Now back-fill the dependent rules**:
  - `RDS-SG` inbound: TCP 5432 from `ECS-SG` (reference by sg-id)
  - `ECS-SG` inbound: TCP 8000 from `ALB-SG` (reference by sg-id)

### A.3 ECR repository

- [ ] **ECR console** → **Create repository**
  - Visibility: Private
  - Name: `freightsentry-riskd`
  - Tag immutability: ENABLED (v1.0.0 tags should not be silently
    overwritten)
  - Image scan on push: ENABLED
- [ ] Note the repository URI: `<ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/freightsentry-riskd`

### A.4 RDS PostgreSQL 16

- [ ] **RDS console** → **Create database**
  - Engine: PostgreSQL 16
  - Template: Production (or Dev/Test for non-prod regions)
  - DB instance identifier: `freightsentry-riskd`
  - Master username: `riskd` (this is the superuser used by
    `ALEMBIC_DATABASE_URL` per Phase 5D — keep this name to match
    project conventions)
  - Master password: generate strong; save for Secrets Manager
    step below
  - DB instance class: `db.t4g.micro` (v1; scale up post-launch as
    needed)
  - Storage: gp3, 20 GiB; enable storage autoscaling
  - **Connectivity**:
    - VPC: default
    - Subnet group: create new — pick 2 subnets across 2 AZs
    - Public access: NO
    - Security group: `freightsentry-riskd-rds-sg` (from A.2)
  - **Database authentication**: password only (v1)
  - Initial database name: `riskd`
  - Backup retention: 7 days (production) / 1 day (non-prod)
- [ ] After creation, note the endpoint: `freightsentry-riskd.<random>.<REGION>.rds.amazonaws.com`
- [ ] **Parameter group** (optional v1 tuning, skip if unsure):
      `max_connections=200`, `shared_buffers=256MB` — operator can
      revisit post-launch with real-load profile

### A.5 Secrets Manager entries

Create four secrets. All under the `freightsentry-riskd/` name
prefix so the IAM policy's `freightsentry-riskd/*` resource scope
matches.

- [ ] **Secret 1**: `freightsentry-riskd/DATABASE_URL`
  - Type: Other type of secret → plaintext
  - Value: `postgresql://riskd_app_login:<RUNTIME_PASSWORD>@<RDS_ENDPOINT>:5432/riskd`
  - Note: `riskd_app_login` is the runtime non-superuser role
    created in migration 0008 (Phase 5D). The password is
    distinct from the RDS master password and will be set during
    the migration step (B.1 below) when the role is created.
    For now insert a placeholder; rotate to the real password
    after B.1 completes.
  - Tags: `project=freightsentry-riskd`
- [ ] **Secret 2**: `freightsentry-riskd/HMAC_SECRET`
  - Value: generate via `openssl rand -hex 32`
  - Tags: `project=freightsentry-riskd`
- [ ] **Secret 3**: `freightsentry-riskd/MAXMIND_LICENSE_KEY`
  - Value: paste your MaxMind GeoLite2 license key
  - Tags: `project=freightsentry-riskd`
- [ ] **Secret 4**: `freightsentry-riskd/IP2PROXY_DOWNLOAD_TOKEN`
  - Value: paste your IP2Proxy download token
  - Tags: `project=freightsentry-riskd`
- [ ] Rotation: not enabled in v1; document a rotation policy
      post-launch (Phase 7+)

### A.6 CloudWatch Logs group

- [ ] **CloudWatch console** → **Log groups** → **Create log group**
  - Name: `/ecs/freightsentry-riskd` (must match the awslogs
    driver config in `infra/ecs-task-definition.json`)
  - Retention: 30 days (production default — adjust per
    operational preference)
- [ ] *Alternative*: skip this step and rely on the
      `awslogs-create-group: true` flag in the task definition +
      `logs:CreateLogGroup` permission in the execution role
      (added in 6D.3) — first deploy will create the group
      automatically. Belt-and-suspenders pre-create is the
      defensive default; auto-create works too.

### A.7 IAM roles (three roles, three policies — see `infra/iam-policies/`)

#### A.7.1 `freightsentry-riskd-task-exec` (task execution role)

- [ ] **IAM console** → **Roles** → **Create role**
- [ ] Trusted entity: AWS service → Elastic Container Service →
      Elastic Container Service Task
- [ ] Permissions:
  - Attach AWS-managed policy: `AmazonECSTaskExecutionRolePolicy`
    (standard baseline)
  - Inline policy: paste the contents of
    `infra/iam-policies/task-execution-role.json`, replacing
    `ACCOUNT_ID` and `REGION` placeholders with your values
- [ ] Role name: `freightsentry-riskd-task-exec`
- [ ] Note the role ARN

#### A.7.2 `freightsentry-riskd-task` (container-runtime task role)

- [ ] Same trusted-entity flow as A.7.1
- [ ] Permissions: **inline policy only** — paste
      `infra/iam-policies/task-role.json` (empty Statements list;
      app makes zero AWS API calls)
- [ ] Role name: `freightsentry-riskd-task`
- [ ] Note the role ARN

#### A.7.3 `freightsentry-riskd-deploy` (GitHub Actions deploy role)

- [ ] **IAM console** → **Identity providers** → **Add provider**
  - Provider type: OpenID Connect
  - Provider URL: `https://token.actions.githubusercontent.com`
  - Audience: `sts.amazonaws.com`
  - This step creates the GitHub OIDC provider in your account;
    skip if already created
- [ ] **IAM console** → **Roles** → **Create role**
- [ ] Trusted entity: Web identity → GitHub
  - Identity provider: the OIDC provider you just added
  - Audience: `sts.amazonaws.com`
  - GitHub organization: `<your-org>`
  - Repository: `<your-repo>` (e.g. `esaarvee/freightsentry-riskd`)
  - Branch: leave wildcard for now; tightened to `main` + `v*`
    tags via trust-policy edit below
- [ ] Permissions: inline policy from
      `infra/iam-policies/github-actions-deploy-role.json` with
      placeholders substituted
- [ ] Role name: `freightsentry-riskd-deploy`
- [ ] **Edit trust policy** after creation to scope `sub` to:
      `repo:<org>/<repo>:ref:refs/heads/main` (for build.yml) AND
      `repo:<org>/<repo>:ref:refs/tags/v*` (for deploy.yml). Use a
      `StringLike` condition with both patterns in an array.
- [ ] Note the role ARN — this goes into GitHub Secrets as
      `AWS_ROLE_TO_ASSUME`

### A.8 ECS cluster

- [ ] **ECS console** → **Clusters** → **Create cluster**
  - Cluster name: `freightsentry-riskd-cluster` (must match the
    name in IAM policies + task definition)
  - Infrastructure: AWS Fargate
- [ ] Note the cluster ARN

### A.9 ALB + target group

- [ ] **EC2 console** → **Target groups** → **Create target group**
  - Target type: IP
  - Name: `freightsentry-riskd-tg`
  - Protocol: HTTP, Port 8000
  - VPC: default
  - Health check:
    - Path: `/health/`
    - Healthy threshold: 2
    - Unhealthy threshold: 3
    - Timeout: 5s
    - Interval: 30s
    - Success code: 200
  - Do NOT register any targets at this step (ECS service
    registers them automatically)
- [ ] **EC2 console** → **Load balancers** → **Create**
  - Type: Application Load Balancer
  - Scheme: internet-facing (or internal if behind a corporate VPN)
  - VPC: default; subnets: at least 2 across AZs
  - Security group: `freightsentry-riskd-alb-sg`
  - Listener: HTTPS 443 → forward to `freightsentry-riskd-tg`
    - Default action: forward
    - TLS certificate: ACM certificate for your domain (create one
      in ACM console if you don't have one — `app.example.com`
      etc.)
- [ ] Note the ALB DNS name; this becomes the `SMOKE_TEST_URL`
      GitHub Secret + the operator-facing endpoint

### A.10 ECS service (shell — first deploy populates the task def)

- [ ] **ECS console** → **Clusters** → `freightsentry-riskd-cluster`
      → **Create service**
  - Launch type: Fargate
  - Platform version: LATEST
  - Service name: `freightsentry-riskd-service` (must match the
    name in IAM policies)
  - Task definition family: `freightsentry-riskd` (must exist
    before this step — register an empty placeholder via:
    1. Open `infra/ecs-task-definition.json`
    2. Replace `ACCOUNT_ID`, `REGION`, and `ECR_URL:IMAGE_TAG`
       with placeholder values
    3. In **ECS console** → **Task definitions** → **Create new
       task definition** → JSON → paste; click Create. This is
       the placeholder revision; the GitHub Actions deploy
       workflow registers real revisions on each tag push.
  - Revision: latest
  - Desired tasks: 1 (scale up post-launch)
  - VPC: default
  - Subnets: at least 2 across AZs
  - Security group: `freightsentry-riskd-ecs-sg`
  - Public IP: DISABLED
  - Load balancer:
    - Type: Application Load Balancer
    - Load balancer: select the ALB you created in A.9
    - Container to load balance: `app:8000`
    - Target group: `freightsentry-riskd-tg`
- [ ] Service health check grace period: 60 seconds

### A.11 GitHub Secrets

Configure the following secrets in **GitHub repo → Settings →
Secrets and variables → Actions**:

- [ ] `AWS_ROLE_TO_ASSUME` — the `freightsentry-riskd-deploy` role
      ARN from A.7.3
- [ ] `AWS_REGION` — your region (e.g. `ca-central-1`)
- [ ] `AWS_ACCOUNT_ID` — your 12-digit account number
- [ ] `ECR_REPOSITORY` — `freightsentry-riskd`
- [ ] `ECS_CLUSTER` — `freightsentry-riskd-cluster`
- [ ] `ECS_SERVICE` — `freightsentry-riskd-service`
- [ ] `SNYK_TOKEN` — from snyk.io (free for OSS; required for the
      test.yml Snyk dependency-scan step)
- [ ] `SMOKE_TEST_URL` — `https://<your-alb-dns-or-domain>`
- [ ] `SMOKE_TENANT_TOKEN` — the first production tenant token
      from B.2 below; reset after first deploy if a v1 token
      placeholder was used

---

## Phase B — Pre-deploy migrations + tenant bootstrap (one-time)

### B.1 Run alembic migrations

The runtime role `riskd_app_login` doesn't exist yet — migration
0008 creates it. Migrations run as the superuser (`riskd`).

- [ ] **ECS console** → **Clusters** → `freightsentry-riskd-cluster`
      → **Tasks** → **Run task**
  - Launch type: Fargate
  - Task definition: `freightsentry-riskd` (the placeholder
    revision from A.10 — operator can also use any real revision
    that contains the alembic/ directory in the image)
  - Subnets + security group: same as the service
  - **Container override**:
    - Container name: app
    - Command: `python,-m,alembic,upgrade,head`
    - Environment overrides:
      - `ALEMBIC_DATABASE_URL` = `postgresql://riskd:<RDS_MASTER_PASSWORD>@<RDS_ENDPOINT>:5432/riskd`
        (superuser DSN — migrations need this; the runtime
        DATABASE_URL secret already in the task def is the
        `riskd_app_login` non-superuser DSN)
- [ ] Run the task. Migration produces a non-zero exit on failure
      → check CloudWatch Logs for the task's stream.
- [ ] After completion: connect to RDS with the superuser
      credentials and verify:
  - `\du` shows both `riskd` (superuser) and `riskd_app_login`
    (LOGIN, INHERIT, no BYPASSRLS)
  - `\dt` shows the expected tables (customers, shipments,
    decisions, …, plus the Phase 6A 0010+0011 additions:
    `tenant_route_baselines` and the new column `customers.registered_country`)
- [ ] Set the `riskd_app_login` password to match the value
      embedded in the `freightsentry-riskd/DATABASE_URL` Secrets
      Manager secret (A.5). Migration 0008 creates the role with
      a default dev password (`riskd_app_login_dev`) — rotate it
      for production:
      ```sql
      ALTER ROLE riskd_app_login WITH PASSWORD '<NEW_PASSWORD>';
      ```
      Then update the secret value in Secrets Manager to use the
      same password.

### B.1a Population baseline cold-start (Phase 6A.7 / 6A.8 informational)

- [ ] `tenant_route_baselines` is initially empty for the first
      production tenant — the table populates via the runtime
      UPSERT in `app/api/booking.py` as bookings land.
- [ ] Until then, the sophisticated case-3b compound
      (`cold_start_population_baseline_rare_with_carrier_dropoff`)
      does NOT fire (cold-start gate at `RARITY_MIN_OBSERVATIONS = 100`).
      Acceptable cold-start behavior; the simple case-3b compound
      (`cold_start_country_triangle_with_carrier_dropoff`) fires
      independently once platform integration supplies
      `customer.registered_country` + `origin_via_carrier_dropoff`.

### B.1b Platform integration launch-blocking dependency (Phase 6A informational)

- [ ] **Confirm with the platform team**: production booking
      payloads MUST supply
      - `customer.registered_country` (ISO 3166-1 alpha-2)
      - `shipment.origin_via_carrier_dropoff` (bool)
      Without these structured fields, case-3b detection signals
      are no-ops (return False; rules cannot fire on real
      traffic). This is a launch-blocking integration
      prerequisite; the case-3b detection capability is in
      production code but inert until the platform integration
      ships.

### B.2 Tenant bootstrap

The application onboards tenants via `scripts/tenant_onboard.py`.
Run it as a one-off ECS task against the production tenant.

- [ ] Prepare a tenant config JSON locally:
      ```json
      {
        "allowed_currencies": ["CAD"],
        "value_caps": {
          "CAD": {"high": 10000, "new_user": 5000, "medium": 2000, "low": 1000}
        }
      }
      ```
      (Phase 6B set CAD as the project default. Add USD here if
      the tenant accepts USD payments.)
- [ ] Upload the file to a temporary location accessible from
      the ECS task — easiest is to bake it into the runtime image
      under `/app/configs/`, or use an S3 bucket + `aws s3 cp` in
      the container override.
- [ ] **ECS console** → **Run task**:
  - Task definition: same `freightsentry-riskd` placeholder
  - Command: `python,scripts/tenant_onboard.py,--external-id,<TENANT_EXTERNAL_ID>,--display-name,<TENANT_DISPLAY_NAME>,--config-json,/app/configs/tenant.json,--rotate-token`
- [ ] After the task completes, retrieve the printed token from
      CloudWatch Logs and store in:
  - Secrets Manager (for operator reference)
  - GitHub Secret `SMOKE_TENANT_TOKEN` (for the deploy workflow
    smoke test)

### B.3 RLS verification

- [ ] **Connect to RDS as `riskd_app_login`**:
      ```bash
      psql "postgresql://riskd_app_login:<PASSWORD>@<RDS_ENDPOINT>:5432/riskd"
      ```
- [ ] **Run** without setting `app.tenant_id`:
      ```sql
      SELECT * FROM customers;
      ```
      Expected: ERROR (`current_setting('app.tenant_id')`
      undefined) OR 0 rows. Either confirms RLS is enforcing —
      the runtime role cannot read tenant-scoped data without an
      `app.tenant_id` session variable.
- [ ] **Run** for the new `tenant_route_baselines` table same as
      above; confirm same RLS behavior.
- [ ] **Confirm** `riskd_app` role has NO `LOGIN` (parent role
      should be NOLOGIN; only `riskd_app_login` inherits its
      permissions):
      ```sql
      SELECT rolname, rolcanlogin FROM pg_roles
      WHERE rolname IN ('riskd_app', 'riskd_app_login');
      ```
      Expected: `riskd_app` → `f`; `riskd_app_login` → `t`.

---

## Phase C — First deploy

### C.1 First v1.0.0 tag push

- [ ] On the operator machine, in the project repo:
      ```bash
      git checkout main
      git pull origin main
      git tag v1.0.0
      git push origin v1.0.0
      ```
- [ ] **GitHub UI** → **Actions** → watch the `Deploy` workflow
      run:
  - Configure AWS credentials (OIDC)
  - Login to ECR
  - Build Docker image
  - Push to ECR with two tags (`v1.0.0` + short SHA)
  - Register new ECS task definition revision
  - `aws ecs update-service --force-new-deployment`
  - `aws ecs wait services-stable` blocks until rollout completes
  - Run `scripts/smoke_test.py` against `$SMOKE_TEST_URL`

### C.2 Verify ECS service rolls over

- [ ] **ECS console** → service detail → **Deployments**:
      observe new revision becomes ACTIVE, old task drains
- [ ] **Target group**: new ECS task registers as Healthy
      within the grace period (60s)
- [ ] **CloudWatch Logs** `/ecs/freightsentry-riskd`: verify
      structured-log lines flowing; verify EMF metric blocks
      auto-detected

### C.3 Smoke test green

- [ ] Deploy workflow's smoke-test step output is green
- [ ] Manual `curl`:
      ```bash
      curl -sS https://<SMOKE_TEST_URL>/health/
      ```
      Expected: `{"ok":true,"db":"ok",...}` 200

### C.4 CloudWatch metrics flowing

- [ ] **CloudWatch console** → **Metrics** → namespace
      `FreightSentry/RiskD`:
  - `risk.evaluation` count
  - `auth.success` / `auth.failure` counts
  - `tenant_config.cache.hit` / `tenant_config.cache.miss` counts
  - Other Phase 5C EMF events per `docs/observability.md`

---

## Phase D — Post-deploy verification (day 1)

- [ ] EMF metrics flowing per C.4
- [ ] Tenant config cache hit ratio at baseline (from
      `docs/load-test-phase-5.md`)
- [ ] Decision rate distribution (ALLOW / REVIEW / BLOCK):
      ALLOW dominates on legitimate traffic
- [ ] Error rate < 0.1%
- [ ] **`tenant_route_baselines` population check** (Phase 6A):
      ```sql
      SELECT COUNT(*) FROM tenant_route_baselines WHERE tenant_id = <first_tenant>;
      ```
      Should grow with each booking. Zero growth indicates the
      6A.7 UPSERT path is broken; investigate before continuing.
- [ ] **`customers.registered_country` population rate** (Phase 6A):
      ```sql
      SELECT COUNT(*) FILTER (WHERE registered_country IS NOT NULL)::float / COUNT(*)
      FROM customers
      WHERE tenant_id = <first_tenant>
        AND first_seen >= now() - interval '24 hours';
      ```
      Target > 95% once platform integration is live. <50% suggests
      the integration doesn't supply the structured field;
      case-3b detection will be impaired.

---

## Phase E — Day 1-7 monitoring

- [ ] Latency p95 < 200ms (project ceiling)
- [ ] Latency p99 trends stable
- [ ] **Latency p95 trend monitoring (Phase 6A)**: Phase 5 baseline
      was ~12ms; with +4ms overhead from 6A.7 + 6A.8, post-deploy
      baseline shifts to ~16ms. Watch for trend past 50ms (yellow
      flag — investigate query performance) or 195ms (red —
      calibration backlog action before ceiling breach).
- [ ] False-positive observations: operator-flagged ALLOW→BLOCK
      transitions or BLOCK→ALLOW transitions get logged for
      `docs/calibration-backlog.md` (6E)
- [ ] Calibration-backlog rules' fire rates observed; pattern
      compared to 6C prediction

---

## Phase F — Week 1-4 (initial observation window)

- [ ] Calibration backlog items get production-frequency data
- [ ] No tuning yet — observation only
- [ ] **Population baseline fire rate monitoring (Phase 6A)**:
      `shipment_route_rare_for_tenant` fire rate per tenant.
      Target: <10% of bookings. If >10% sustained, suggests
      insufficient baseline data (tenant still in cold-start) OR
      threshold too strict (2% rarity cutoff may need calibration).
      Log to calibration backlog.

---

## Rollback (manual; v1)

If a deploy causes a critical regression:

- [ ] **ECS console** → service → **Update service**
- [ ] Task definition: select a prior healthy revision (the
      revision number is visible under **Task definitions** →
      revision history)
- [ ] **Force new deployment**: yes
- [ ] Click **Update**
- [ ] **ECS console** waits — `aws ecs wait services-stable` would
      block ~3-5 minutes for the rollback to complete

For database-shape rollback (alembic migration revert) the same
one-off-task pattern as B.1 applies, with command
`python,-m,alembic,downgrade,-1`. Do this BEFORE rolling the ECS
service back if the new task definition requires a schema not
present at the prior revision. Phase 7+ may automate rollback;
v1 is manual.

---

## Phase 5D auth chicken-and-egg awareness (always-on)

RLS is **DROPPED** on `api_tokens` + `app_users` because token
lookup precedes `set_tenant_id` (per migration 0009 and
`.ai/decisions.md` Phase 5D notes). Application-layer
`tenant_id` filtering is the active defense; there is NO DB-layer
backstop on those two tables. Any future change to token
validation must preserve this defense — documented in
`docs/security-audit-rls-phase-5.md`.

---

## v1 launch limitations

- No auto-rollback (manual via ECS console per the rollback section
  above)
- No CI integration tests (unit + Snyk only; integration tests run
  locally)
- No auto-migration on deploy (operator one-off task per B.1)
- No IaC framework (this AWS GUI runbook is the deployment
  mechanism; Terraform/CDK is a Phase 7+ scope)
- No multi-region (single-region per environment; `ca-central-1`
  production)
- Single-customer case-3 cluster empirically validated only — not
  generalizable to population case-3 until real-data observation
  (per `docs/replay-validation.md`)
