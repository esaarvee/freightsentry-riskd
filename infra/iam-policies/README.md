# IAM policies for freightsentry-riskd deploy

Four IAM policy JSON documents the operator pastes into the AWS
IAM console at infrastructure setup time. Each is least-privileged
against a specific principal: the ECS task-execution role, the
container-runtime task role, the one-off onboarding task role, and
the GitHub Actions deploy role.

The detailed AWS GUI runbook at `docs/aws-deploy-runbook.md`
(Phase 6D.4) walks through console-side role creation and
attachment.

**PBL D3 note.** A fourth role, `MigrationTaskExecutionRole`, was
added for the gated migrate-then-deploy flow (PBL D3–D5). It is
declared in CloudFormation (`infra/cloudformation/freightsentry-riskd.yml`),
not in this directory — the JSON files here are the legacy hand-
applied path. The migrate role grants ECR pull + CloudWatch Logs
write + `secretsmanager:GetSecretValue` on `DbMasterSecret` and
`DatabaseUrlSecret` only. The runtime task-execution role described
below remains excluded from `DB_MASTER`, preserving the D18
invariant. The deploy role (also CFN) gains `ecs:RunTask` scoped to
the `freightsentry-riskd-migrate:*` task-def family and
`iam:PassRole` for the migrate execution role.

## Placeholders

Each JSON contains the following uppercase placeholders. Substitute
at attach time:

| Placeholder | Replace with |
|---|---|
| `ACCOUNT_ID` | 12-digit AWS account number |
| `REGION` | AWS region (e.g. `ca-central-1`, `us-east-2`) |

Project-scoped identifiers are hardcoded throughout — IAM role
names (`freightsentry-riskd-task-exec`, `freightsentry-riskd-task`,
`freightsentry-riskd-onboard-task`, `freightsentry-riskd-deploy`)
and ECS resource names
(`freightsentry-riskd-cluster`, `freightsentry-riskd-service`).
These match the names referenced by `infra/ecs-task-definition.json`.

## task-execution-role.json

**Principal**: ECS task-execution IAM role
`freightsentry-riskd-task-exec`.

**Purpose**: identity ECS Fargate uses BEFORE the container
starts — pulls the image from ECR, fetches secrets from Secrets
Manager (injected into container env per the task definition's
`secrets` block), creates the CloudWatch Logs group + streams, and
writes log events.

**Permissions**:

| Sid | Action | Resource |
|---|---|---|
| ECRAuthorization | `ecr:GetAuthorizationToken` | `*` (required by ECR API contract — token is global, not resource-scoped) |
| ECRImagePull | `ecr:BatchCheckLayerAvailability` + `ecr:GetDownloadUrlForLayer` + `ecr:BatchGetImage` | `arn:aws:ecr:REGION:ACCOUNT_ID:repository/freightsentry-riskd` (scoped to the one image repo) |
| CloudWatchLogsWrite | `logs:CreateLogGroup` + `logs:CreateLogStream` + `logs:PutLogEvents` | `arn:aws:logs:REGION:ACCOUNT_ID:log-group:/ecs/freightsentry-riskd:*` (scoped to the one log group + its streams) |
| SecretsManagerRead | `secretsmanager:GetSecretValue` | `arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:freightsentry-riskd/*` (scoped to project-prefixed secrets only) |

**Why `logs:CreateLogGroup` is included** (Phase 6D.2 cross-reviewer
coordination): the task definition sets `awslogs-create-group: true`
on the awslogs driver — first deploy attempts to create
`/ecs/freightsentry-riskd` if it doesn't exist. Without
`logs:CreateLogGroup` on the execution role the create attempt
fails silently or hard depending on ECS driver mode, and the
container won't start. Granting at-creation time on the project
log-group ARN keeps the permission tightly scoped.

**Also attach the AWS-managed policy**
`AmazonECSTaskExecutionRolePolicy` for the standard baseline (the
above is on top of, not in place of, the AWS baseline — see the
runbook for guidance).

## task-role.json

**Principal**: ECS task IAM role `freightsentry-riskd-task`
(referenced as `taskRoleArn` in the task definition).

**Purpose**: identity the application process inside the container
assumes. The app makes ZERO AWS API calls (no boto3 / aiobotocore
imports; CloudWatch metrics are emitted via EMF on stdout, the ECS
log driver harvests them — see `app/observability.py`). The task
role therefore needs zero permissions. The empty statements list
is the documented project posture, NOT an omission.

**Permissions**: none (intentional).

If a future feature lands an explicit AWS API call from the
application (e.g. calling SQS, S3, KMS), expand this policy to
match. Document the addition in `.ai/decisions.md`.

The onboarding one-off task does NOT reuse this role — it carries a
separate, narrower task role (`onboard-task-role.json` below) so the
app process keeps its zero-permission posture.

## onboard-task-role.json

**Principal**: ECS task IAM role `freightsentry-riskd-onboard-task`
(referenced as `taskRoleArn` only in
`infra/ecs-task-definition-onboard.json`, NOT in the app task def).

**Purpose**: identity the one-off tenant-onboarding task assumes.
`scripts/tenant_onboard.py --token-secret-id <id>` writes the freshly
issued tenant API token to AWS Secrets Manager so the plaintext never
lands in CloudWatch Logs. This is the ONLY AWS API call the project
makes from a task role; it is deliberately isolated to the onboarding
task rather than added to the shared app `task-role.json`, so the
public request-serving container retains zero AWS privileges.

**Permissions**:

| Sid | Action | Resource |
|---|---|---|
| TenantTokenSecretWrite | `secretsmanager:PutSecretValue` + `secretsmanager:CreateSecret` | `arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:freightsentry-riskd/tenants/*` (scoped to per-tenant token secrets only) |

`PutSecretValue` covers `--rotate-token` re-issues against an existing
secret; `CreateSecret` covers the first token for a tenant (the script
falls back to create on `ResourceNotFoundException`). The wildcard
suffix matches the random 6-character suffix AWS appends to secret
ARNs. New-secret default encryption uses the AWS-managed
`aws/secretsmanager` KMS key, which grants Secrets-Manager-mediated
access without an explicit `kms:*` statement here.

## github-actions-deploy-role.json

**Principal**: IAM role `freightsentry-riskd-deploy` assumed by the
GitHub Actions deploy workflow (`.github/workflows/deploy.yml` in
6D.8) via OIDC.

**Purpose**: scope of credentials granted to the deploy automation.
Tightly bounded to:
- pushing built images to the project's single ECR repo
- registering new ECS task-definition revisions (the action is
  unscoped because `ecs:RegisterTaskDefinition` operates on the
  family identifier inside the JSON body, not on an ARN)
- updating the specific project ECS service + describing tasks
  for stability waits
- passing the two project roles to ECS (with the
  `iam:PassedToService = ecs-tasks.amazonaws.com` condition so
  the role can't be reused to launch arbitrary tasks elsewhere)

**Permissions**:

| Sid | Action | Resource |
|---|---|---|
| ECRAuthorization | `ecr:GetAuthorizationToken` | `*` |
| ECRPush | `ecr:BatchCheckLayerAvailability` + `ecr:CompleteLayerUpload` + `ecr:InitiateLayerUpload` + `ecr:PutImage` + `ecr:UploadLayerPart` | project ECR ARN |
| ECSTaskDefinitionRegister | `ecs:RegisterTaskDefinition` | `*` (AWS API contract — no per-resource scoping) |
| ECSServiceUpdate | `ecs:UpdateService` + `ecs:DescribeServices` + `ecs:DescribeTasks` + `ecs:ListTasks` | project service + tasks under the project cluster |
| IAMPassRoleForECS | `iam:PassRole` | the two project roles only, with `ecs-tasks.amazonaws.com` service condition |

**OIDC trust policy** (set on the role separately, NOT inside the
permissions JSON above): the operator configures the role's
trust-policy to trust the GitHub OIDC provider with a `sub`
condition matching the project repository path
(`repo:<org>/<repo>:ref:refs/tags/v*` per the deploy
workflow's tag-push trigger). The runbook in 6D.4 walks through
the trust-policy console steps.

## Attachment order (per the 6D.4 runbook)

1. Create the role with the JSON permissions above.
2. Attach AWS-managed policies (only on `task-execution-role` —
   `AmazonECSTaskExecutionRolePolicy`).
3. Configure the trust policy (only on `github-actions-deploy-role`
   — GitHub OIDC).
4. Note the role ARN; substitute into the task definition (6D.2)
   and into the GitHub Secrets used by the deploy workflow (6D.8).

## Auditing

Each JSON's actions are listed in order matching the runbook walk-
through. Operator can paste each verbatim, replace the two
placeholders, and the resulting policy is what the runbook
expects. No fields are operator-discretionary inside the JSON.
