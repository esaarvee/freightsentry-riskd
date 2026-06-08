# freightsentry-riskd CloudFormation

Additive Infrastructure-as-Code for the riskd service. Coexists with the manual procedure in [`docs/aws-deploy-runbook.md`](../../docs/aws-deploy-runbook.md) — this template provisions the IaC-suitable subset (VPC, networking, RDS, ECR, IAM roles, ALB, log group, secret containers, ECS cluster); the runbook continues to document manual procedures (ACM cert issuance, secret value population, ECS service + task-def creation, first tenant onboarding).

---

> ## ⚠ LAUNCH BLOCKER — Pattern B-lite enrichment refresh module
>
> **Deploying this template alone does NOT produce a feature-complete service.** The riskd container starts and passes `/health/`, but `app/enrich.py` lazy-loads MaxMind GeoLite2, IP2Proxy LITE, and FireHOL netsets at first use — and on a fresh deploy with no enrichment files, every booking gets an `EnrichmentRow` with `is_proxy=False`, `is_vpn=False`, `is_tor=False`, `fh_level1/2=False`, `is_cloud=False`, `is_datacenter=False`, and `country=None`. Every rule conditioned on those signals fires on False; only deterministic non-IP rules produce signal. The system looks healthy and accepts requests but is functionally degraded.
>
> Resolution: a separate follow-up pass implements `app/enrichment_refresh.py` (runtime download from upstreams: FireHOL via `raw.githubusercontent.com`, MaxMind via `download.maxmind.com` keyed on `MAXMIND_LICENSE_KEY`, IP2Proxy via `www.ip2location.com` keyed on `IP2PROXY_DOWNLOAD_TOKEN`) with a 24h refresh loop and a health-probe hook that reports `degraded` until first refresh succeeds. CFN-only output of THIS pass provides the NAT egress (C2) and the license-key secret containers (C1) the future module needs.
>
> **Do NOT promote to production until the Pattern B-lite module ships.**

---

## Pre-deploy verification checklist

Run these BEFORE the first `aws cloudformation deploy` in each region:

### 1. VPC CIDR collision check

```sh
aws ec2 describe-vpcs --region <REGION> --query 'Vpcs[*].[VpcId,CidrBlock,Tags[?Key==`Name`].Value|[0]]' --output table
```

Run for both target regions (`ca-central-1` production, `us-east-2` test). If any existing VPC's CIDR overlaps the chosen `VpcCidr` (default `10.1.0.0/16`), abort and override `VpcCidr` in `params/<env>.json` to a non-overlapping fallback:

- `10.2.0.0/16` (next /16 in the 10/8 RFC1918 range)
- `172.20.0.0/16` (RFC1918 in the 172.16/12 range)
- `10.10.0.0/16`, `10.20.0.0/16`, etc.

### 2. GitHub OIDC provider ARN

```sh
aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[?contains(Arn, 'token.actions.githubusercontent.com')].Arn" --output text
```

Returns the account-level GitHub OIDC provider ARN (created once by the platform-app deployment per D5). Paste into both `params/test.json` and `params/production.json` for `GitHubOidcProviderArn`.

If empty, the provider doesn't exist yet — create it once at the account level (out-of-band; this template does NOT create it). See [AWS docs on configuring OIDC for GitHub](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html).

### 3. ACM certificate

Issue an ACM certificate in the target region for the planned ALB DNS or your custom domain (DNS or email validation). Copy the cert ARN into `params/<env>.json` as `AcmCertificateArn`. ACM certs are region-scoped — issue separately in `ca-central-1` (production) and `us-east-2` (test).

### 4. Region pin

Production = `ca-central-1`. Test = `us-east-2`. The template uses the `AWS::Region` pseudo-parameter throughout — region is set by the `--region` flag on the deploy command, not by a template parameter.

---

## Parameters

| Parameter | Type | Default | Required override? | Notes |
|---|---|---|---|---|
| `Environment` | String | (none) | yes (`test` / `production`) | Drives env-suffix on IAM role names (account-global per D13). |
| `LogRetentionDays` | Number | 30 | test → 7 | CloudWatch log retention. |
| `VpcCidr` | String | `10.1.0.0/16` | maybe (collision check) | RFC1918 private; /16–/24. Subnet CIDRs derived via `!Cidr`. |
| `DbInstanceClass` | String | `db.t4g.micro` | no | Scale-up via override; no template change. |
| `DbAllocatedStorage` | Number | 20 | no | GiB, gp3, autoscales up to `DbMaxAllocatedStorage`. |
| `DbMaxAllocatedStorage` | Number | 100 | no | Autoscaling ceiling. |
| `DbMultiAz` | String | `false` | post-launch flip to `true` | Doubles RDS cost; recommended once traffic justifies. |
| `DbBackupRetentionDays` | Number | 7 | test → 1 | Automated backup retention. |
| `DbDeletionProtection` | String | `true` | test → `false` | Prevents accidental destroy on production. |
| `AcmCertificateArn` | String | (none) | **yes** | ACM cert ARN for HTTPS:443. Region-scoped. |
| `GitHubOidcProviderArn` | String | (none) | **yes** | Account-level OIDC provider ARN; from platform-app. |
| `GitHubOidcSubject` | String | `repo:esaarvee/freightsentry-riskd:ref:refs/tags/v*` | maybe (fork / different ref) | Restricts which workflow can assume DeployRole. |

---

## Deploy command

```sh
# Test (us-east-2)
aws cloudformation deploy \
  --template-file infra/cloudformation/freightsentry-riskd.yml \
  --stack-name freightsentry-riskd-test \
  --region us-east-2 \
  --parameter-overrides $(jq -r 'to_entries|map("\(.key)=\(.value)")|join(" ")' infra/cloudformation/params/test.json) \
  --capabilities CAPABILITY_NAMED_IAM
```

```sh
# Production (ca-central-1)
aws cloudformation deploy \
  --template-file infra/cloudformation/freightsentry-riskd.yml \
  --stack-name freightsentry-riskd-production \
  --region ca-central-1 \
  --parameter-overrides $(jq -r 'to_entries|map("\(.key)=\(.value)")|join(" ")' infra/cloudformation/params/production.json) \
  --capabilities CAPABILITY_NAMED_IAM
```

`CAPABILITY_NAMED_IAM` is required because the template creates IAM roles with explicit names (`freightsentry-riskd-{task-exec,task,deploy}-${Environment}`).

Stack creation takes ~15–25 minutes; RDS provisioning is the long pole.

---

## Post-deploy operator steps

The CFN stack creates infrastructure but does NOT make the service operational. Six manual steps remain:

### 1. Run alembic migrations (operator's own AWS credentials, NOT the runtime task)

Per D18, the runtime TaskExecutionRole does NOT have access to `freightsentry-riskd/DB_MASTER`. Operator fetches the master credential with their own AWS principal and runs alembic from a host with VPC connectivity (bastion, temporary EC2 in the new VPC's private subnet, or `aws ssm start-session` to a Fargate one-off task).

```sh
# Fetch master password (operator-side, with own AWS creds)
DB_PASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id freightsentry-riskd/DB_MASTER \
  --region <REGION> \
  --query SecretString --output text | jq -r .password)

DB_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name freightsentry-riskd-<env> --region <REGION> \
  --query "Stacks[0].Outputs[?OutputKey=='DbEndpointAddress'].OutputValue" --output text)

export ALEMBIC_DATABASE_URL="postgresql://riskd:${DB_PASSWORD}@${DB_ENDPOINT}:5432/riskd"
alembic upgrade head
```

The runtime-roles migration creates the `riskd_app_login` role and a placeholder password documented in `docs/security-audit-rls-phase-5.md`.

### 2. Populate runtime DATABASE_URL secret

```sh
# After alembic, set the riskd_app_login password (or use the dev-default for now)
RUNTIME_PASSWORD="<generated by operator or rotated from migration default>"
RUNTIME_DSN="postgresql://riskd_app_login:${RUNTIME_PASSWORD}@${DB_ENDPOINT}:5432/riskd"

aws secretsmanager put-secret-value \
  --secret-id freightsentry-riskd/DATABASE_URL \
  --secret-string "$RUNTIME_DSN" \
  --region <REGION>
```

### 3. Populate HMAC_SECRET

```sh
aws secretsmanager put-secret-value \
  --secret-id freightsentry-riskd/HMAC_SECRET \
  --secret-string "$(openssl rand -hex 32)" \
  --region <REGION>
```

### 4. Populate license-key secrets

```sh
aws secretsmanager put-secret-value \
  --secret-id freightsentry-riskd/MAXMIND_LICENSE_KEY \
  --secret-string "<operator-held MaxMind license key>" \
  --region <REGION>

aws secretsmanager put-secret-value \
  --secret-id freightsentry-riskd/IP2PROXY_DOWNLOAD_TOKEN \
  --secret-string "<operator-held IP2Proxy download token>" \
  --region <REGION>
```

These are dormant until the Pattern B-lite enrichment refresh module ships (launch blocker above).

### 5. First image push + ECS service creation

CFN intentionally does NOT own the ECS service or task-def (D16) — the deploy workflow at `.github/workflows/deploy.yml` registers task-def revisions via `aws ecs register-task-definition` and updates the service via `aws ecs update-service --force-new-deployment`. Putting these in CFN would cause every workflow run to register a revision CFN doesn't know about, and any future `aws cloudformation update-stack` would revert the service. The service is created MANUALLY once; the workflow takes over for subsequent deploys.

```sh
# 5a. First image push (manual, before service exists)
ECR_URI=$(aws cloudformation describe-stacks --stack-name freightsentry-riskd-<env> --region <REGION> \
  --query "Stacks[0].Outputs[?OutputKey=='EcrRepositoryUri'].OutputValue" --output text)

aws ecr get-login-password --region <REGION> | docker login --username AWS --password-stdin "$ECR_URI"
docker build --platform linux/amd64 -t "${ECR_URI}:v0.0.1-bootstrap" .
docker push "${ECR_URI}:v0.0.1-bootstrap"

# 5b. Register first task-def revision
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export REGION=<REGION>
export IMAGE_URI="${ECR_URI}:v0.0.1-bootstrap"

envsubst '${ACCOUNT_ID} ${REGION} ${IMAGE_URI}' \
  < infra/ecs-task-definition.json \
  > /tmp/task-def.json

TASK_DEF_ARN=$(aws ecs register-task-definition --cli-input-json file:///tmp/task-def.json \
  --query 'taskDefinition.taskDefinitionArn' --output text)

# 5c. Create the service ONCE
CLUSTER=$(aws cloudformation describe-stacks --stack-name freightsentry-riskd-<env> --region <REGION> \
  --query "Stacks[0].Outputs[?OutputKey=='EcsClusterName'].OutputValue" --output text)
TG_ARN=$(aws cloudformation describe-stacks --stack-name freightsentry-riskd-<env> --region <REGION> \
  --query "Stacks[0].Outputs[?OutputKey=='TargetGroupArn'].OutputValue" --output text)
PRIVATE_SUBNETS=$(aws cloudformation describe-stacks --stack-name freightsentry-riskd-<env> --region <REGION> \
  --query "Stacks[0].Outputs[?OutputKey=='PrivateSubnetIds'].OutputValue" --output text)
ECS_SG=$(aws ec2 describe-security-groups --region <REGION> \
  --filters "Name=tag:Name,Values=freightsentry-riskd-ecs-sg-<env>" \
  --query 'SecurityGroups[0].GroupId' --output text)

aws ecs create-service \
  --cluster "$CLUSTER" \
  --service-name freightsentry-riskd-service \
  --task-definition "$TASK_DEF_ARN" \
  --launch-type FARGATE \
  --desired-count 1 \
  --network-configuration "awsvpcConfiguration={subnets=[$(echo $PRIVATE_SUBNETS | tr ',' ' ')],securityGroups=[$ECS_SG],assignPublicIp=DISABLED}" \
  --load-balancers "targetGroupArn=$TG_ARN,containerName=app,containerPort=8000" \
  --health-check-grace-period-seconds 60 \
  --region <REGION>
```

Subsequent deploys flow through the existing workflow on `git tag v*` push.

### 6. Configure GitHub Actions secrets (per env)

Set these GitHub Actions secrets to the values from the stack outputs:

| GitHub secret | Source |
|---|---|
| `AWS_ROLE_TO_ASSUME` | stack output `DeployRoleArn` (env-suffixed) |
| `AWS_REGION` | `us-east-2` (test) or `ca-central-1` (production) |
| `AWS_ACCOUNT_ID` | `aws sts get-caller-identity` |
| `ECR_REPOSITORY` | `freightsentry-riskd` (region-scoped repo name; no env suffix) |
| `ECS_CLUSTER` | stack output `EcsClusterName` |
| `ECS_SERVICE` | `freightsentry-riskd-service` (the name used in step 5c above) |
| `SMOKE_TEST_URL` | `https://<custom-domain-or-AlbDnsName>` |
| `SMOKE_TENANT_TOKEN` | first production tenant token (see `scripts/tenant_onboard.py`) |

---

## Update workflow

Parameter changes (e.g., scale RDS, flip Multi-AZ, change retention) flow through `aws cloudformation deploy` again — same command, edited params file. Stack updates do NOT touch the ECS service or task-def per D16.

Application-code deploys flow through the existing GitHub Actions workflow on `git tag v*` push — no CFN interaction.

---

## Teardown

The stack is drift-aware:

- RDS instance: `DeletionPolicy: Snapshot` — final snapshot preserved on delete.
- All 5 Secrets Manager secrets: `DeletionPolicy: Retain`, 30-day API-default recovery window.
- CloudWatch log group: `DeletionPolicy: Retain`.

Manual prerequisite for full teardown: delete the ECS service (CFN doesn't own it, see step 5):

```sh
aws ecs update-service --cluster $CLUSTER --service freightsentry-riskd-service --desired-count 0 --region <REGION>
aws ecs delete-service --cluster $CLUSTER --service freightsentry-riskd-service --force --region <REGION>
```

Then:

```sh
aws cloudformation delete-stack --stack-name freightsentry-riskd-<env> --region <REGION>
```

Retained resources (RDS snapshot, secrets, log group) require manual cleanup from the AWS console / CLI once you're confident the data is no longer needed.

---

## Cost projection (per environment, parameter defaults, pre-launch single-tenant scale)

| Resource | Monthly cost (USD, approx, ca-central-1 / us-east-2) |
|---|---|
| NAT Gateway | ~$32 + $0.045/GB processed |
| ALB | ~$22 + $0.008/LCU-hour |
| RDS db.t4g.micro, 20GB gp3, Single-AZ, 7d backups | ~$13 + storage |
| ECS Fargate (1 task, 1 vCPU / 2GB, 24x7) | ~$36 |
| Secrets Manager (5 secrets) | ~$2 |
| CloudWatch Logs (30d retention, modest volume) | ~$5 |
| ECR storage (10 images, ~500MB each) | ~$0.50 |
| EIP for NAT | ~$3.60 |
| Data transfer | varies |
| **Total** | **~$115/month** |

At CAD conversion (1 USD ≈ 1.35 CAD): ~$155/month per env. Well under the CAD 1000/month per-env ceiling.

Scale-up knobs:
- `DbInstanceClass` → `db.t4g.small` / `db.m6g.large` etc.
- `DbMultiAz` → `true` (doubles RDS line item)
- Task count + size (changed via the deploy workflow, not CFN)
- `LogRetentionDays` ↑ (modest cost)

---

## Deviations from FreightSentry `docs/06-infrastructure.md`

The platform-app uses a different conventions baseline. Differences are intentional, documented, and operator-approved (see plan `Decisions absorbed` table):

| Aspect | FreightSentry | freightsentry-riskd | Why |
|---|---|---|---|
| Secrets store (D1) | SSM Parameter Store, `/freightsentry/<env>/<name>` | AWS Secrets Manager, `freightsentry-riskd/<NAME>` uppercase | Existing deploy workflow, task-def, runbook all wired to Secrets Manager already. Migrating mid-build would require coordinated app+workflow rewrite. |
| Stack naming (D11) | `freightsentry-<env>` | `freightsentry-riskd-<env>` | Cross-app prefix isolation in shared account. |
| Resource physical naming (D13) | env in all names | env in IAM names only (region-scoped resources skip env) | Region scoping handles cross-env collision for most resources. |
| ECS ownership (D16) | Full manual runbook | CFN owns cluster + roles + network; service+task-def stay manual | The existing deploy workflow already does register-task-def + update-service outside CFN. Splitting at the cluster boundary avoids ongoing CFN-vs-workflow drift. |
| Runtime IAM (D18) | Wildcard `freightsentry-riskd/*` (manual policy) | Explicit 4-ARN list; DB_MASTER excluded | Runtime ≠ superuser (Phase 5D principle). Alembic uses operator's own AWS creds. |
| RecoveryWindowInDays | Implicit (API default) | Implicit (API default; `RecoveryWindowInDays` is not a CFN Properties field) | See STATUS.md 2026-06-08 INFRA-CFN C1 row. |

---

## Carry-forward / future work

- **LAUNCH BLOCKER**: Pattern B-lite enrichment refresh app module — see banner at top.
- **Operator-driven**: first test-region deploy; iterate template if discrepancies surface.
- **Optional follow-ups (not blocking launch)**:
  - Auto-scaling configurations (target tracking on CPU / request count).
  - Full CloudWatch alarm suite (RDS CPU, ALB 5xx rate, target group unhealthy host count, freeable memory).
  - AWS WAF on the ALB.
  - Secrets Manager rotation for HMAC_SECRET and DB_MASTER.
  - VPC Flow Logs to CloudWatch (cost trade-off).
  - Drift detection scheduled task on the stack.
  - Customer-managed KMS key for at-rest encryption (currently AWS-managed keys).
  - Multi-AZ RDS flip + cross-AZ NAT redundancy.
  - Eventual migration to SSM Parameter Store secrets (matches FreightSentry; substantial coordinated rewrite).
  - Migrating ECS service + task-def into CFN via `DeploymentController=EXTERNAL` + TaskSet API (requires deploy workflow rewrite).
