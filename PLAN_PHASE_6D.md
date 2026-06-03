# PLAN_PHASE_6D — Deployment artifacts + GitHub Actions

> **Phase 6, Batch D.** Produces multi-stage Dockerfile + ECS task definition + IAM policies + AWS GUI runbook + smoke test + three GitHub Actions workflows. **Claude Code never touches AWS** — operator executes the runbook + provides credentials via GitHub Secrets.
>
> Companion: 6A → 6B → 6C → **6D (this batch)** → 6E.

---

## Pre-plan verification findings

1. **Current `Dockerfile`** — `python:3.13-slim` base. `apt-get install build-essential` (gcc + libc headers) installs unconditionally for pytricia native build. Non-root user `app` (UID/GID 1000) created BEFORE pip install; ownership transferred via `chown -R app:app /app` post-install. `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`. Single-stage — build-essential ships to runtime (BUGS.md Phase 5 carry-forward).

2. **`docker-compose.yml`** — local-dev only: postgres:16-alpine + app service + healthcheck. Env vars: DATABASE_URL, ALEMBIC_DATABASE_URL, HMAC_SECRET, API_TOKEN_PREFIX, MAXMIND_LICENSE_KEY, IP2PROXY_DOWNLOAD_TOKEN, ENRICHMENT_DATA_DIR, LOG_LEVEL, AUTH_ENABLED. **Locked — 6D does not change.**

3. **`pyproject.toml`** versions:
   - `requires-python = ">=3.13"` — CI must pin 3.13.x.
   - pytest>=8.3, pytest-asyncio>=0.24, pytest-cov>=6.0
   - httpx>=0.28, hypothesis>=6.115
   - ruff>=0.15.0,<0.16.0, mypy>=1.13
   - asyncpg>=0.30, fastapi[standard]>=0.115, uvicorn[standard]>=0.32, pydantic>=2.9
   - `uv.lock` present.

4. **`.pre-commit-config.yaml`** hooks: ruff (check + fix), ruff-format, mypy (strict, app/ only), pytest-unit, check-yaml/toml/json, end-of-file-fixer, trailing-whitespace, check-added-large-files (500KB), detect-private-key, no-commit-to-branch (main protected).

5. **No `boto3`/`aiobotocore` in `app/`** — zero AWS SDK calls. EMF observability emits to **stdout** via structlog (`app/logging.py`); ECS log driver collects and CloudWatch Logs parses EMF blocks. Implication: **ECS task role needs no CloudWatch write permissions** — log driver handles it. Task role is minimal (Secrets Manager read for env vars).

6. **Health endpoint** at `/health/` returns `{"ok": true, "db": "ok", "pool": {...}}` (200) or `{"ok": false, "db": "unreachable"}` (503). ALB target group health check uses this path.

7. **Env vars consumed by app** (`app/config.py`):
   - REQUIRED: `DATABASE_URL`, `HMAC_SECRET`
   - OPTIONAL: `API_TOKEN_PREFIX` ("rsk_"), `MAXMIND_LICENSE_KEY` (""), `IP2PROXY_DOWNLOAD_TOKEN` (""), `ENRICHMENT_DATA_DIR` ("/app/data/enrichment"), `LOG_LEVEL` ("INFO"), `AUTH_ENABLED` (True)
   - ECS task def env block: REQUIRED via `valueFrom` (Secrets Manager); OPTIONAL via `value` (literal).
   - **Phase 5D added `ALEMBIC_DATABASE_URL`** (superuser DSN for migrations) — present in docker-compose.yml; ECS deploy runs migrations as a one-off task (or pre-deploy hook) using this DSN.

8. **No `.github/workflows/`** exists yet.

9. **Python version pinning**: mypy `python_version = "3.13"`. CI uses Python 3.13.x explicitly. Operator dev machine is 3.14 (Phase 5 STATUS row); local floor is 3.13+.

---

## Decisions absorbed

| Decision | Value | Source |
|---|---|---|
| 6D scope | Multi-stage Dockerfile + ECS task def JSON + IAM policy JSONs + AWS GUI runbook + smoke test + 3 GitHub Actions workflows + decisions amendment | Phase 6 prompt |
| Multi-stage Dockerfile | Builder stage installs build-essential + wheels; runtime stage drops build-essential, copies wheels + non-root user | Phase 6 prompt + Phase 5 BUGS carry-forward |
| Production docker-compose | NOT produced — ECS is the orchestrator | Phase 6 prompt |
| Deploy mechanism | Operator creates AWS infrastructure via GUI runbook; GitHub Actions deploys application code | Phase 6 prompt |
| IaC framework | NONE (no Terraform/CloudFormation/CDK) — JSON artifacts pasted into AWS console | Phase 6 prompt |
| Level 1 workflow (`test.yml`) | PR to main or release/* → ruff + mypy + pytest + Snyk dependency scan | Phase 6 prompt |
| Level 2 workflow (`build.yml`) | push to main → Docker build + push to ECR as `dev-<short_sha>` | Phase 6 prompt |
| Level 3 workflow (`deploy.yml`) | tag push matching `v*` → fresh Docker build from tagged commit + dual-tag push (version + short_sha, same digest) + ECS task def register + service update + wait-stable + smoke test | Phase 6 prompt |
| Image tagging | TWO tags on same digest: version (e.g. `v1.0.0`) + short SHA | Phase 6 prompt |
| Rollback strategy | Manual via ECS console; NO auto-rollback in v1 | Phase 6 prompt |
| Snyk vs SonarCloud | Snyk (Python dependency-vuln focus) | Phase 6 prompt |
| Required GitHub Secrets | AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (or AWS_ROLE_TO_ASSUME OIDC), AWS_REGION, ECR_REPOSITORY, ECS_CLUSTER, ECS_SERVICE, ECS_TASK_DEFINITION_FAMILY, SNYK_TOKEN, SMOKE_TEST_URL | Phase 6 prompt |
| ECS task role | Minimal (Secrets Manager read only) — no boto3/CloudWatch calls from app | 6D verification |
| Task execution role | Pull image from ECR + read Secrets Manager + write to CloudWatch Logs (log driver) | Standard ECS pattern |
| GitHub Actions deploy role | Tightly scoped: ECR push to specific repo, ECS update-service on specific cluster+service, RegisterTaskDefinition for specific family | Phase 6 prompt |
| Python version in CI | 3.13.x (mypy pinned, project floor) | 6D verification |
| Migrations on deploy | Operator runs `alembic upgrade head` manually as a one-off ECS task before service rollout for v1; documented in runbook. Future automation deferred | Plan-time derivation (no auto-migrate in v1 — risk-managed approach) |
| Smoke test payload | Known-good booking payload → assert 200 + decision in {ALLOW,REVIEW,BLOCK} + presence of `score` and `request_id` fields | Phase 6 prompt |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md.
- **Reviewer panel MANDATORY per code commit**:
  - **6D.1 (multi-stage Dockerfile)**: standard path + security-auditor focus → **senior-engineer + security-auditor + code-flow**.
  - **6D.2 (ECS task def JSON)**: config file with security implications → **senior-engineer + security-auditor + code-flow**.
  - **6D.3 (IAM policy JSONs)**: security-critical → **security-auditor + senior-engineer + code-flow** (security-auditor leads).
  - **6D.4 (AWS GUI runbook)**: doc-only operational procedure under `docs/runbooks/`-equivalent path → **doc-reviewer + senior-engineer** (CLAUDE.md lightweight path for runbooks).
  - **6D.5 (smoke test script)**: standard path → **senior-engineer + security-auditor + code-flow + test-reviewer** (it's a script that exercises production-like flows).
  - **6D.6 (test.yml — Level 1)**: CI workflow → **senior-engineer + security-auditor + code-flow**.
  - **6D.7 (build.yml — Level 2)**: CI workflow with AWS credential surface → security-auditor critical → **senior-engineer + security-auditor + code-flow**.
  - **6D.8 (deploy.yml — Level 3)**: CI workflow with production deploy authority → security-auditor critical → **senior-engineer + security-auditor + code-flow**.
  - **6D.9 (.ai/decisions.md amendment)**: → **senior-engineer + code-flow + doc-reviewer**.
- Pre-commit gates enforced. The deploy-config files (`infra/ecs-task-definition.json`, `infra/iam-policies/*.json`, `.github/workflows/*.yml`) are subject to check-json and check-yaml hooks.

---

## Cross-batch dependencies

- **6D independent of 6A/6B/6C** — deployment artifacts don't depend on detection code or replay results.
- **6D → 6E**: launch checklist in 6E references the AWS runbook + workflow files + smoke test + multi-stage Dockerfile + IAM JSONs.
- **6D resolves Phase 5 BUGS.md item** (`build-essential` in runtime image) — explicitly noted in commit footer of 6D.1.

---

## Commits

### 6D.1 — Multi-stage `Dockerfile` refactor

**Theme**: Strip build-essential from runtime image. Resolves Phase 5 BUGS.md `build-essential` carry-forward.

**Files**:
- MODIFY `Dockerfile`.

**Specifics**:
```dockerfile
# Builder stage
FROM python:3.13-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv pip install --system --no-cache-dir -r <(uv export --no-hashes --format requirements-txt)
# Wheels now installed into /usr/local/lib/python3.13/site-packages

# Runtime stage
FROM python:3.13-slim AS runtime
RUN groupadd -g 1000 app && useradd -m -u 1000 -g app -d /app -s /sbin/nologin app
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
WORKDIR /app
COPY --chown=app:app app/ ./app/
COPY --chown=app:app alembic/ ./alembic/
COPY --chown=app:app alembic.ini ./
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 CMD python -c "import httpx; r = httpx.get('http://localhost:8000/health/'); r.raise_for_status()"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- Builder installs build-essential, builds all wheels (pytricia + others).
- Runtime stage drops build-essential — only `python:3.13-slim` baseline ships.
- Non-root user retained (UID/GID 1000).
- HEALTHCHECK directive added — both ECS task health check + ALB use it.
- `alembic/` copied into runtime image so operator can run `alembic upgrade head` via ECS exec or one-off task.

**Validation**:
- Local build: `docker build -t freightsentry-riskd:test .` succeeds.
- Image size compare: `docker images freightsentry-riskd:test` — runtime image should be significantly smaller than single-stage (no build-essential, no apt lists). Document size before/after in commit message.
- `docker run --rm freightsentry-riskd:test python -c "import app.main"` — imports succeed (wheels present).
- `docker run --rm freightsentry-riskd:test python -c "import pytricia; print(pytricia.PyTricia)"` — pytricia importable.
- `docker run --rm freightsentry-riskd:test which gcc` — returns empty / non-zero exit (gcc absent).
- `docker compose up -d` local stack still works (compose builds from same Dockerfile).

**Risk level**: medium. Build mechanics change. Mitigation: local docker build + local stack confirm before commit.

**Reversibility**: full via revert.

**Pre-commit verification**: ruff/mypy don't touch Dockerfile; hadolint not in pre-commit. Manual docker build is the verification.

**Observability**: no change to runtime behavior.

**Test changes**: none.

**Rollback plan**: revert.

**Declared breaks**: none.

**Reviewer routing**: → **senior-engineer + security-auditor + code-flow**.

**Footer**: include `Resolves: Phase 5 BUGS.md build-essential carry-forward`.

---

### 6D.2 — ECS task definition JSON template

**Files**:
- NEW `infra/ecs-task-definition.json` — template with placeholder substitution markers.

**Specifics**:
```json
{
  "family": "freightsentry-riskd",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "executionRoleArn": "arn:aws:iam::ACCOUNT_ID:role/freightsentry-riskd-task-exec",
  "taskRoleArn": "arn:aws:iam::ACCOUNT_ID:role/freightsentry-riskd-task",
  "containerDefinitions": [
    {
      "name": "app",
      "image": "ECR_URL:IMAGE_TAG",
      "essential": true,
      "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
      "environment": [
        {"name": "LOG_LEVEL", "value": "INFO"},
        {"name": "AUTH_ENABLED", "value": "true"},
        {"name": "API_TOKEN_PREFIX", "value": "rsk_"},
        {"name": "ENRICHMENT_DATA_DIR", "value": "/app/data/enrichment"}
      ],
      "secrets": [
        {"name": "DATABASE_URL", "valueFrom": "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:freightsentry-riskd/DATABASE_URL"},
        {"name": "HMAC_SECRET", "valueFrom": "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:freightsentry-riskd/HMAC_SECRET"},
        {"name": "MAXMIND_LICENSE_KEY", "valueFrom": "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:freightsentry-riskd/MAXMIND_LICENSE_KEY"},
        {"name": "IP2PROXY_DOWNLOAD_TOKEN", "valueFrom": "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:freightsentry-riskd/IP2PROXY_DOWNLOAD_TOKEN"}
      ],
      "healthCheck": {
        "command": ["CMD-SHELL", "python -c \"import httpx; r = httpx.get('http://localhost:8000/health/'); r.raise_for_status()\""],
        "interval": 30,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 30
      },
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/freightsentry-riskd",
          "awslogs-region": "REGION",
          "awslogs-stream-prefix": "app"
        }
      }
    }
  ]
}
```

- Placeholders `ACCOUNT_ID`, `REGION`, `ECR_URL`, `IMAGE_TAG` are substituted by deploy.yml at deploy time (sed or envsubst).
- CPU/memory at 1024/2048 baseline; can scale up post-launch per load observation.

**Validation**:
- `python -m json.tool infra/ecs-task-definition.json` — valid JSON.
- Verify all REQUIRED env vars from `app/config.py` are present in `secrets` block; all OPTIONAL ones in `environment` block.
- pre-commit `check-json` hook passes.

**Risk level**: medium. Bad task def = bad deploy. Mitigation: doc-reviewer cross-references against `app/config.py` env-var list.

**Reversibility**: full via revert.

**Pre-commit verification**: check-json passes.

**Test changes**: none.

**Declared breaks**: none.

**Reviewer routing**: → **senior-engineer + security-auditor + code-flow**.

---

### 6D.3 — IAM policy JSON documents

**Files**:
- NEW `infra/iam-policies/task-execution-role.json` — ECR pull + Secrets Manager read + CloudWatch Logs write.
- NEW `infra/iam-policies/task-role.json` — minimal (no AWS API calls from app; can be `{"Version": "2012-10-17", "Statement": []}` or a no-op marker; operator still attaches it to satisfy ECS requirement).
- NEW `infra/iam-policies/github-actions-deploy-role.json` — ECR push, ECS update-service + RegisterTaskDefinition, scoped tightly.
- NEW `infra/iam-policies/README.md` — describes each policy's purpose, attachment, and operator-side steps.

**Specifics** — `task-execution-role.json`:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"], "Resource": "arn:aws:ecr:REGION:ACCOUNT_ID:repository/freightsentry-riskd"},
    {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": "arn:aws:logs:REGION:ACCOUNT_ID:log-group:/ecs/freightsentry-riskd:*"},
    {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"], "Resource": "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:freightsentry-riskd/*"}
  ]
}
```

**`task-role.json`**:
```json
{"Version": "2012-10-17", "Statement": []}
```
Comment in README: app makes zero AWS API calls (EMF via stdout). Task role exists for ECS attachment compliance; can be expanded if app gains AWS-API needs.

**`github-actions-deploy-role.json`**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["ecr:PutImage", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:BatchCheckLayerAvailability"], "Resource": "arn:aws:ecr:REGION:ACCOUNT_ID:repository/freightsentry-riskd"},
    {"Effect": "Allow", "Action": ["ecs:RegisterTaskDefinition"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["ecs:UpdateService", "ecs:DescribeServices"], "Resource": "arn:aws:ecs:REGION:ACCOUNT_ID:service/freightsentry-riskd-cluster/freightsentry-riskd-service"},
    {"Effect": "Allow", "Action": ["iam:PassRole"], "Resource": ["arn:aws:iam::ACCOUNT_ID:role/freightsentry-riskd-task-exec", "arn:aws:iam::ACCOUNT_ID:role/freightsentry-riskd-task"]}
  ]
}
```

**Validation**:
- `python -m json.tool` on each JSON file — valid.
- README cross-references each policy to its attachment target (which role gets which policy).
- pre-commit `check-json` + markdown gates.

**Risk level**: HIGH (security). Over-scoped IAM = bad. Mitigation: security-auditor reviews each policy individually for least-privilege.

**Reviewer routing**: → **security-auditor (leads) + senior-engineer + code-flow**.

**Declared breaks**: none.

---

### 6D.4 — AWS GUI runbook at `docs/aws-deploy-runbook.md`

**Files**:
- NEW `docs/aws-deploy-runbook.md`.

**Specifics** — structure:

1. Prerequisites (AWS account, region selection `ca-central-1` production / `us-east-2` test)
2. VPC + subnets (use default VPC for v1; document trade-offs vs custom VPC for v2)
3. Security groups: RDS (5432 from ECS-SG only), ECS service (8000 from ALB-SG only), ALB (443 from internet)
4. ECR repository creation (one repo per region)
5. RDS PostgreSQL 16 instance: subnet group, parameter group (`max_connections`, `shared_buffers` notes), security group attachment, credentials → Secrets Manager
6. Secrets Manager entries: `freightsentry-riskd/DATABASE_URL` (with `riskd_app_login` role user post-Phase-5D), `freightsentry-riskd/HMAC_SECRET`, `freightsentry-riskd/MAXMIND_LICENSE_KEY`, `freightsentry-riskd/IP2PROXY_DOWNLOAD_TOKEN`. Each with rotation policy note.
7. CloudWatch Logs group `/ecs/freightsentry-riskd` (retention 30 days default; adjustable)
8. IAM roles — create three roles, attach policies from `infra/iam-policies/*.json`:
   - `freightsentry-riskd-task-exec` ← task-execution-role.json
   - `freightsentry-riskd-task` ← task-role.json
   - `github-actions-deploy` ← github-actions-deploy-role.json (with GitHub OIDC trust policy template)
9. ECS cluster `freightsentry-riskd-cluster` (Fargate launch type)
10. ECS service `freightsentry-riskd-service` shell with placeholder task def (first deploy populates the real revision)
11. ALB + target group → ECS service registration, health check at `/health/`
12. **Pre-first-deploy migration step**: operator runs `alembic upgrade head` via one-off ECS task (or `aws ecs run-task --task-definition ... --overrides 'containerOverrides:command:[python,-m,alembic,upgrade,head]'`) using `ALEMBIC_DATABASE_URL` (superuser). Document the exact CLI command.
12a. **Population baseline cold-start note (Phase 6A amendment)**: after migrations apply, `tenant_route_baselines` is initially empty for the first production tenant. As the tenant accumulates booking history, the table populates automatically via the synchronous UPSERT in `app/api/booking.py`. Initial bookings have empty baseline → `shipment_route_rare_for_tenant` returns False → sophisticated case-3b compound (`cold_start_population_baseline_rare_with_carrier_dropoff`) does NOT fire until the tenant accumulates ≥100 observations. This is acceptable cold-start behavior; the simple country-triangle compound (`cold_start_country_triangle_with_carrier_dropoff`) fires regardless once platform integration supplies `customer.registered_country` + `origin_via_carrier_dropoff`.
12b. **Platform integration launch-blocking dependency (Phase 6A amendment)**: until the platform integration is updated to supply `customer.registered_country` + `origin_via_carrier_dropoff` in production booking payloads, ALL case-3b detection signals that depend on those structured fields are no-ops (return False). The case-3b compound rules will not fire on real traffic until platform integration ships. Document this as a launch-blocking dependency that the operator coordinates with the platform team. Verify the integration is live before considering case-3b detection capability operational.
13. **Tenant bootstrap step**: operator runs `scripts/tenant_onboard.py` via one-off ECS task to create the first production tenant.
14. **Post-RLS-role-switch verification**: confirm `riskd_app_login` role exists in RDS (created during migration); confirm runtime `DATABASE_URL` uses that role; confirm `riskd_app` role has no LOGIN.
15. First v1.0.0 tag push → GitHub Actions deploys.
16. Smoke verification: smoke_test.py output green.
17. CloudWatch verification: EMF metric events present in `/ecs/freightsentry-riskd` log group; metric filters or embedded metric format auto-detected.
18. Rollback procedure for v1: manual via ECS console — select prior task definition revision, update service. Document UI clicks.

Detail level: enough that an operator unfamiliar with this specific deploy can complete it without external research. Each section names console fields ("In Subnet Group dropdown, select X"). Use checklist boxes.

**Validation**:
- Manual review by operator before commit.
- pre-commit markdown formatting.

**Risk level**: medium (operational). Mitigation: operator walks the runbook before approving and adds gaps via amendment commit if discovered.

**Reviewer routing**: doc commit under `docs/` (NOT under `docs/runbooks/`-named path — but this IS a runbook; following the runbook-naming-convention places it under doc-reviewer-only path per CLAUDE.md lightweight) → **doc-reviewer + senior-engineer**.

**Declared breaks**: none.

---

### 6D.5 — Smoke test script `scripts/smoke_test.py`

**Files**:
- NEW `scripts/smoke_test.py`.

**Specifics**:
- CLI: `python scripts/smoke_test.py --base-url $SMOKE_TEST_URL --tenant-token $SMOKE_TENANT_TOKEN`.
- Sends a known-good booking payload (CAD currency, established customer, clean IP, ALLOW-expected) to POST `/api/v1/shipments/booking/evaluate`.
- Asserts: HTTP 200, JSON body contains `request_id` matching submitted, `decision` in `{ALLOW, REVIEW, BLOCK}`, `score` is float `0 <= s <= 1`.
- Latency < 5 seconds (sanity bound; ALB cold-start tolerated).
- Exit 0 on success, exit 1 on any assertion failure. Stderr captures failure detail.
- Used by `deploy.yml` as post-deploy verification.

**Validation**:
- `python scripts/smoke_test.py --base-url http://localhost:8000 --tenant-token $LOCAL_TOKEN` against local stack — passes.
- Unit test in `tests/unit/test_smoke_test.py` mocks httpx and verifies the assertion logic.

**Risk level**: low.

**Reviewer routing**: → **senior-engineer + security-auditor + code-flow + test-reviewer**.

**Declared breaks**: none.

---

### 6D.6 — Level 1 workflow `.github/workflows/test.yml`

**Files**:
- NEW `.github/workflows/test.yml`.

**Specifics**:
```yaml
name: Test
on:
  pull_request:
    branches: [main, "release/*"]
jobs:
  test:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.13" }
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
      - run: uv run ruff check app/ tests/
      - run: uv run ruff format --check app/ tests/
      - run: uv run mypy app/
      - run: uv run pytest tests/ --asyncio-mode=auto
  snyk:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: snyk/actions/python@master
        env: { SNYK_TOKEN: ${{ secrets.SNYK_TOKEN }} }
        with: { args: --severity-threshold=high }
```

- Test job runs lint/types/full suite. (No PostgreSQL service in v1 — integration tests requiring docker postgres run locally only. Phase 7 may add a postgres service container for CI integration tests.)
- Snyk job fails on high-severity findings.

**Validation**:
- YAML lint via pre-commit `check-yaml`.
- Operator can dry-run by opening a PR after this commit lands.

**Risk level**: medium. CI bring-up.

**Reviewer routing**: → **senior-engineer + security-auditor + code-flow**.

**Note**: integration tests requiring Postgres will SKIP if `DATABASE_URL` env unavailable. Phase 7 may add a postgres service container; v1 ships with unit-only coverage in CI. This is a documented gap, NOT a declared break (the plan never claimed integration tests run in CI v1).

**Declared breaks**: none.

---

### 6D.7 — Level 2 workflow `.github/workflows/build.yml`

**Files**:
- NEW `.github/workflows/build.yml`.

**Specifics**:
```yaml
name: Build
on:
  push:
    branches: [main]
jobs:
  build:
    runs-on: ubuntu-24.04
    permissions: { id-token: write, contents: read }
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_TO_ASSUME }}
          aws-region: ${{ secrets.AWS_REGION }}
      - uses: aws-actions/amazon-ecr-login@v2
        id: ecr
      - name: Build and push dev image
        env:
          ECR_URL: ${{ steps.ecr.outputs.registry }}/${{ secrets.ECR_REPOSITORY }}
        run: |
          SHORT_SHA=$(git rev-parse --short HEAD)
          docker build -t $ECR_URL:dev-$SHORT_SHA .
          docker push $ECR_URL:dev-$SHORT_SHA
```

- OIDC for AWS auth (avoids long-lived access keys; recommended pattern). README in 6D.3 documents the OIDC trust policy template.
- Dev images use `dev-<short_sha>` tag — distinct from version tags.

**Reviewer routing**: → **senior-engineer + security-auditor + code-flow**.

**Declared breaks**: none.

---

### 6D.8 — Level 3 workflow `.github/workflows/deploy.yml`

**Files**:
- NEW `.github/workflows/deploy.yml`.

**Specifics**:
```yaml
name: Deploy
on:
  push:
    tags: ["v*"]
jobs:
  deploy:
    runs-on: ubuntu-24.04
    permissions: { id-token: write, contents: read }
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_TO_ASSUME }}
          aws-region: ${{ secrets.AWS_REGION }}
      - uses: aws-actions/amazon-ecr-login@v2
        id: ecr
      - name: Extract version
        id: meta
        run: |
          echo "version=${GITHUB_REF#refs/tags/}" >> $GITHUB_OUTPUT
          echo "short_sha=$(git rev-parse --short HEAD)" >> $GITHUB_OUTPUT
      - name: Build image
        env:
          ECR_URL: ${{ steps.ecr.outputs.registry }}/${{ secrets.ECR_REPOSITORY }}
          VERSION: ${{ steps.meta.outputs.version }}
          SHORT_SHA: ${{ steps.meta.outputs.short_sha }}
        run: |
          docker build -t $ECR_URL:$VERSION -t $ECR_URL:$SHORT_SHA .
          docker push $ECR_URL:$VERSION
          docker push $ECR_URL:$SHORT_SHA
      - name: Register task definition
        id: register
        env:
          ECR_URL: ${{ steps.ecr.outputs.registry }}/${{ secrets.ECR_REPOSITORY }}
          VERSION: ${{ steps.meta.outputs.version }}
          ACCOUNT_ID: ${{ secrets.AWS_ACCOUNT_ID }}
          REGION: ${{ secrets.AWS_REGION }}
        run: |
          envsubst < infra/ecs-task-definition.json > task-def.json
          aws ecs register-task-definition --cli-input-json file://task-def.json
      - name: Update service
        env:
          CLUSTER: ${{ secrets.ECS_CLUSTER }}
          SERVICE: ${{ secrets.ECS_SERVICE }}
          FAMILY: ${{ secrets.ECS_TASK_DEFINITION_FAMILY }}
        run: |
          aws ecs update-service --cluster $CLUSTER --service $SERVICE --task-definition $FAMILY --force-new-deployment
          aws ecs wait services-stable --cluster $CLUSTER --services $SERVICE
      - name: Smoke test
        env:
          SMOKE_TEST_URL: ${{ secrets.SMOKE_TEST_URL }}
          SMOKE_TENANT_TOKEN: ${{ secrets.SMOKE_TENANT_TOKEN }}
        run: |
          pip install httpx
          python scripts/smoke_test.py --base-url $SMOKE_TEST_URL --tenant-token $SMOKE_TENANT_TOKEN
```

- `docker build -t $ECR_URL:$VERSION -t $ECR_URL:$SHORT_SHA .` produces ONE image with TWO tag references; subsequent `docker push` for each tag pushes the same digest with two tag entries in ECR.
- `aws ecs wait services-stable` blocks until rollout complete; smoke test runs against fully-rolled service.
- On smoke failure: workflow fails. Operator handles rollback manually via ECS console per runbook.
- No auto-migration on deploy — operator handles via one-off ECS task per runbook.

**Validation**:
- After commit, operator dry-runs by pushing a `v0.0.0-test` tag and verifies workflow steps (without actually rolling out to prod — can target a staging service if available).

**Reviewer routing**: → **senior-engineer + security-auditor + code-flow** (security-auditor especially on the workflow's blast radius).

**Declared breaks**: none.

---

### 6D.9 — `.ai/decisions.md` Phase 6D amendment

**Files**:
- MODIFY `.ai/decisions.md`.

**Specifics** — new section:
- Phase 6D deployment-artifacts decisions:
  - Multi-stage Dockerfile (build vs runtime separation); Phase 5 BUGS resolution.
  - ECS Fargate as orchestrator; no production docker-compose.
  - AWS GUI runbook (no IaC for v1); rationale: single-environment v1, operator velocity over reproducibility.
  - Three-level GitHub Actions (test/build/deploy); rationale: separation of concerns + tag-push trigger for deploys (immutable history).
  - Dual-tag-on-same-digest pattern; rationale: version for readability, SHA for forensic traceability.
  - Manual rollback for v1; rationale: auto-rollback adds complexity without proportional risk reduction at single-tenant pre-launch scale.
  - Snyk over SonarCloud; rationale: Python dependency-vuln depth.
  - Migrations as one-off ECS task; rationale: deploy/migration decoupling reduces blast radius.

**Reviewer routing**: → **senior-engineer + code-flow + doc-reviewer**.

**Declared breaks**: none.

---

## End-of-batch state (after 6D.9)

- Multi-stage Dockerfile in place; build-essential out of runtime.
- `infra/ecs-task-definition.json` + 3 IAM policy JSONs + README.
- `docs/aws-deploy-runbook.md` end-to-end operator-executable.
- `scripts/smoke_test.py` + unit tests.
- 3 GitHub Actions workflows under `.github/workflows/`.
- `.ai/decisions.md` carries Phase 6D rationale.
- Required GitHub Secrets list documented in runbook + workflow files.
- Local `docker compose up -d` still works against the new Dockerfile.

## Open items handed to 6E

- 6E aggregates 6D artifacts into the production-launch checklist.
- 6E documents the v1 limitations: no IaC, no auto-rollback, no CI integration tests, no auto-migration on deploy.
