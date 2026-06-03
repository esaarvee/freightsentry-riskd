# REPORT_PHASE_6 — Aggregate report

Phase 6 (Week 6) of the freightsentry-riskd build: case-3 detection
capability + currency-default pivot + measurement validation +
deployment artifacts + launch readiness. Five batches, 29 commits
including this report (6E.2) and the upcoming closeout (6E.3),
executed 2026-06-03.

## Per-batch composition

- **6A** — case-3 detection capability (9 commits; original 6A.4 was
  renumbered to 6A.10 and 6A.5-6A.9 inserted during the case-3b
  amendment expansion)
- **6B** — CAD default switch + multi-currency tenant config (3 commits)
- **6C** — replay-validation harness + 10K+500+95 corpus execution +
  measurement doc (5 commits)
- **6D** — deployment artifacts: Dockerfile + ECS task def + IAM + AWS
  runbook + smoke test + 3 GitHub Actions workflows (9 commits — 6D.1
  through 6D.9)
- **6E** — wrap: calibration backlog + launch checklist + this report +
  decisions closeout (3 commits)

## Phase 6 totals

- **Commits**: 29 at end of phase (excluding the Phase 6 plan-file
  commit c92eaa2): 9 (6A) + 3 (6B) + 5 (6C) + 9 (6D) + 3 (6E)
- **Rules**: 82 in `app/rules.yaml` (added 3 in Phase 6A:
  `case_3_compound`, `cold_start_country_triangle_with_carrier_dropoff`,
  `cold_start_population_baseline_rare_with_carrier_dropoff`)
- **`ALLOWED_CONTEXT_FIELDS`**: 76 in `app/rules.py` (Phase 6A added 5:
  `origin_via_carrier_dropoff`, `shipment_route_unfamiliar_for_customer`,
  `customer_registered_country`, `customer_country_triangle_mismatch`,
  `shipment_route_rare_for_tenant`)
- **Migrations**: 2 added — `0010_country_route_stats.py`,
  `0011_case_3b_schema.py`
- **New `.py` modules under `app/`**: 1 — `app/tenant_route_baselines.py`
- **New `.py` modules under `scripts/`**: 2 —
  `scripts/replay_validation.py`, `scripts/smoke_test.py`
- **New `infra/` artifacts**: 4 — `infra/ecs-task-definition.json`,
  `infra/iam-policies/{task-execution-role,task-role,github-actions-deploy-role}.json`
  + `infra/iam-policies/README.md`
- **New `.github/workflows/`**: 3 — `test.yml`, `build.yml`, `deploy.yml`
- **New runtime artifacts**: `Dockerfile` rewritten as multi-stage
- **New docs/**: 4 — `docs/replay-validation.md`,
  `docs/aws-deploy-runbook.md`, `docs/calibration-backlog.md`,
  `docs/production-launch-checklist.md`
- **New replay corpora**: `scripts/replay/data/{approved_jan_mar,case2_sample,case3_census}.ndjson`
  + README + EXPORT_SCRIPT_REFERENCE
- **`.ai/decisions.md`**: 2 new dated amendment sections (Phase 6A
  case-3 detection capability; Phase 6C replay-validation findings)
  + Phase 6D deployment artifacts (6D.9) + Phase 6 closeout (6E.3)

## Per-batch summary

### Batch 6A — case-3 detection capability (9 commits)

**Theme**: implement case-3 fraud detection (brand-new-customer
sophisticated fraud) in two sub-classes:
- **case-3a** — established-customer compromise (`case_3_compound`,
  weight 0.70, maturity_sensitive). Empirical validation deferred to
  post-launch when (a) platform integration supplies the structured
  signals AND (b) case-3a-style fraud observed in production.
- **case-3b** — brand-new-customer fraud, two compounds:
  - `cold_start_country_triangle_with_carrier_dropoff` (weight 0.65,
    simple) — fires when customer ≠ origin AND customer ≠ destination
    AND carrier dropoff AND customer_observations < 10.
  - `cold_start_population_baseline_rare_with_carrier_dropoff` (weight
    0.70, sophisticated) — fires when tenant-population baseline says
    the route is rare (<2% of observations across ≥100 baseline
    observations) AND carrier dropoff AND customer_observations < 10.

**Structured-field architectural pattern**: case-3 introduces two
structured Pydantic fields:
- `Customer.registered_country` (ISO 3166-1 alpha-2, validated)
- `Shipment.origin_via_carrier_dropoff` (bool)

Both follow the same pattern: platform supplies the signal at booking
time; freightsentry-riskd consumes structured Pydantic-field passthrough;
replay corpus injects ground truth where known (CA for Roulottes Lupien);
signal returns False / None when absent, eliminating accidental
false positives on corpora without ground truth. Pattern documented for
future case-N detection additions. Address-string parsing rejected.

**New subsystem — tenant route population baseline**:
- Table `tenant_route_baselines` (composite PK
  `(tenant_id, customer_country, origin_country, destination_country)`;
  RLS-enforced; GRANT on every-new-table per Phase 6A BUGS lesson).
- Migration `0011_case_3b_schema.py` adds the table + RLS policy.
- Synchronous UPSERT on every booking commit via
  `app/tenant_route_baselines.py::update_tenant_route_baseline`.
- Eval-time derivation via `derive_route_rarity` (2% rarity threshold
  + 100-observation minimum).
- Rule integration via the sophisticated compound above.

**Cold-start posture**: at first booking, `tenant_route_baselines` is
empty for the new tenant; `shipment_route_rare_for_tenant` returns
False; sophisticated compound does NOT fire. Simple compound fires
once structured signals flow regardless of population state. This is
acceptable cold-start behavior documented in
`docs/production-launch-checklist.md` Phase B.

**Latency**: 6A.7 + 6A.8 added ~4ms p95 (one UPSERT + one SELECT per
booking). Phase 5 baseline ~12ms → post-amendment ~16ms. Well within
the 200ms ceiling; calibration backlog watch thresholds documented in
`docs/calibration-backlog.md` item 15.

### Batch 6B — CAD default switch (3 commits)

**Theme**: pivot the default tenant currency from USD to CAD
(Canadian operator default), preserving multi-currency tenant support.

**Implementation**:
- `app/tenant_config.py`: `DEFAULT_VALUE_CAPS` USD → CAD (same numeric
  thresholds); `DEFAULT_ALLOWED_CURRENCIES` `["USD"]` → `["CAD"]`;
  `resolve_value_caps` fallback → CAD.
- **Fixture-centric test reconciliation** (6B.2): the original
  estimated 30-line / 6-file diff turned into 126 failures across 26
  test files. The pivot to fixture-side multi-currency seeding
  (`allowed_currencies=["USD","CAD"]` in all tenant-onboarding
  fixtures) was the durable fix vs per-test currency overrides.
  Documented in STATUS.md as a substantive deviation from the plan's
  estimate.

### Batch 6C — replay-validation harness + measurement (5 commits)

**Theme**: Phase 6 measurement gate — run case-1 + case-2 + case-3
corpora through the production rule engine; produce strict-enumeration
findings; populate the calibration backlog seed.

**Implementation**:
- `scripts/replay_validation.py` — NDJSON-streamed corpus loader,
  httpx.AsyncClient + asyncio.Semaphore(50), idempotent re-runs via
  deterministic request_id, per-transaction triggered_rules + score +
  latency captured.
- 3 corpora: `approved_jan_mar.ndjson` (10,000 records);
  `case2_sample.ndjson` (500 records); `case3_census.ndjson` (95
  records, Roulottes Lupien single-customer cluster).
- 18 unit tests on the orchestrator.
- `docs/replay-validation.md` — measurement doc with strict
  enumeration of all three corpus results.

**Measurement findings** (full detail in `docs/replay-validation.md`):

| Corpus | Records | BLOCK | REVIEW | ALLOW | Notes |
|---|---|---|---|---|---|
| approved | 10,000 | 18 (0.18%) | 4,083 (40.83%) | 5,899 (58.99%) | 18 BLOCK records and 3 high-fire-rate rules surface in the calibration backlog |
| case-2 | 500 | 66 (13.2%) | 424 (84.8%) | 10 (2.0%) | Recall 98% — above ≥85% target |
| case-3b | 95 | 0 (0%) | 0 (0%) | 95 (100%) | 0% detection — single-customer cluster caveat; gap surfaced to `docs/calibration-backlog.md` item 6 deferred-actions 1 + 2 |

**Why case-3b census detection is 0%**: structural rule-design
mismatch with the Roulottes Lupien attack shape (CA-registered
customer shipping CA→US). The triangle compound requires both origin
AND destination to differ from customer country; this attack has
origin matching. The sophisticated compound requires ≥100 baseline
observations; the test tenant was empty. Both behaviors are
consistent with rule-design intent for the population-of-fraud
detection target. The 0% result on a single-customer cluster is
neither a build defect nor a calibration miss — it's evidence that
this specific attack shape (domestic-origin + carrier dropoff +
cross-border destination) is not covered by the current case-3b
compound catalogue. Backlog item 6 covers the candidate calibration
intervention.

**case_3_compound on case-3b census**: not expected to fire by design
(maturity gate `customer_observations >= 10` + customer baseline
contamination from prior fraud records — see `docs/replay-validation.md`
"case-3a empirical validation" section). Validation deferred to
post-launch.

### Batch 6D — deployment artifacts (9 commits)

**Theme**: produce the deployment-ready artifact set. **Claude Code
NEVER touches AWS**; operator executes runbook + provides credentials
via GitHub Secrets.

**Implementation**:
- **6D.1** — Multi-stage Dockerfile. Builder installs build-essential
  + pip-installs deps via `tomllib`-extracted `pyproject.toml`
  `[project].dependencies`. Runtime is clean `python:3.13-slim` with
  installed site-packages + entrypoints + app source. Non-root user
  (uid 1000). HEALTHCHECK uses stdlib `urllib.request` (no
  `fastapi[standard]` transitive dep coupling).
- **6D.2** — `infra/ecs-task-definition.json`. FARGATE awsvpc cpu 1024
  / memory 2048. 4 environment + 4 Secrets Manager secrets. healthCheck
  matches Dockerfile. awslogs-create-group: true. All placeholders in
  `${VAR}` form (`${ACCOUNT_ID}`, `${REGION}`, `${IMAGE_URI}`) for
  single-tool envsubst.
- **6D.3** — `infra/iam-policies/` 3 JSONs + README:
  - `task-execution-role.json` — ECR pull, Secrets Manager read,
    CloudWatch Logs (incl. CreateLogGroup).
  - `task-role.json` — empty statements; documented project posture
    (app does NOT call AWS at runtime).
  - `github-actions-deploy-role.json` — ECR push, ECS update,
    PassRole with `iam:PassedToService` condition.
- **6D.4** — `docs/aws-deploy-runbook.md` — 567-line
  operator-executable runbook (VPC, RDS, ALB, ECS, Secrets Manager,
  IAM, OIDC trust policies, GitHub Secrets configuration).
- **6D.5** — `scripts/smoke_test.py` (stdlib-only) + 19 unit tests on
  `assert_response`. POSTs CAD booking; asserts HTTP 200, decision
  band, score ∈ [0, 1], request_id echo, latency < 5s.
- **6D.6** — `.github/workflows/test.yml` (Level 1 CI). PR trigger;
  ruff + ruff format --check + mypy --strict + pytest unit + Snyk
  dep scan (parallel job). `permissions: contents: read`;
  `timeout-minutes`.
- **6D.7** — `.github/workflows/build.yml` (Level 2 CI). Push-to-main
  trigger; build + push ECR `dev-<short_sha>`. OIDC AWS auth.
- **6D.8** — `.github/workflows/deploy.yml` (Level 3 production
  deploy). Tag-push (`v*`) trigger; fresh build with dual tags
  (`<version>` + `<short_sha>`, same digest); envsubst into task def;
  register revision; `update-service --task-definition $REVISION_ARN`;
  `wait services-stable`; smoke test against ALB.
- **6D.9** — `.ai/decisions.md` Phase 6D amendment. Documents
  multi-stage rationale, ECS Fargate choice, no-IaC posture, three-
  level Actions trust-boundary separation, dual-tag image strategy,
  manual rollback, Snyk over SonarCloud, OIDC over static keys,
  migration decoupling from deploy, substitution-pipeline lesson.

### Batch 6E — wrap (3 commits)

**Theme**: synthesize 6A-6D into operator-facing wrap documents.

- **6E.1** — `docs/calibration-backlog.md` + `docs/production-launch-checklist.md`.
- **6E.2** — this report.
- **6E.3** — `.ai/decisions.md` Phase 6 closeout + `.claude/BUGS.md`
  drain + `.claude/STATUS.md` sync.

## Reviewer panel verdict distribution

Across 22 reviewer-panel-invoked commits in Phase 6 (excluding 6E
which routes through reviewer-panel-required commits but is doc-only
where the cleanest verdict is the modal outcome):

- **Cleanest-on-first-pass verdicts** (SHIP IT / LOW RISK / CLEAN /
  ACTUALLY GOOD / PUBLISH): the majority of commits.
- **Cycle 2 needed**: 6A.5 (test-reviewer NEEDS WORK — local
  `_triangle_mismatch` helper instead of importing production); 6A.6
  (db-reviewer NEEDS MINOR FIXES — missing GRANT for `tenant_route_baselines`);
  6A.10 (doc-reviewer NEEDS EDITS — multi-line heading + stale
  parenthetical + premature reference to calibration-backlog.md);
  6B.2 (originally a 30-line / 6-file estimate; pivoted to
  fixture-centric strategy after 126 failures); 6D.1 (senior-engineer
  NEEDS MINOR FIXES — fragile `pip install .` + httpx HEALTHCHECK
  dependency); 6D.8 (senior-engineer NEEDS MAJOR WORK — envsubst
  no-op on bare `ACCOUNT_ID` / `REGION` tokens; resolved by aligning
  task-def template to `${VAR}` form + dropping sed); 6E.1
  (doc-reviewer MINOR TWEAKS — "1100 successful checks" load-test
  miscitation corrected to 10,970 / 183 RPS).
- **Cycle 3+**: 0.
- **Operator escalation**: 0.

**Reviewer-panel discipline held throughout Phase 6.** No panel-skip
events on standard-path commits.

## Reviewer-panel corrections — explicit table

| Commit | Reviewer | Cycle 1 verdict | Issue | Resolution |
|---|---|---|---|---|
| 6A.5 | test-reviewer | NEEDS WORK | `_triangle_mismatch` duplicated as local test helper instead of imported from production | Extracted `_triangle_mismatch` to `app/context.py`; test imports it. Production code is now the single source of truth. |
| 6A.6 | db-reviewer | NEEDS MINOR FIXES | Missing `GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_route_baselines TO riskd_app` | Added to UPGRADE_SQL + matching `REVOKE` in DOWNGRADE_SQL. ALTER DEFAULT PRIVILEGES hardening logged to BUGS.md. |
| 6A.10 | doc-reviewer | NEEDS EDITS | Multi-line markdown heading; stale "58→61" rule count; present-tense ref to `docs/calibration-backlog.md` (Phase 6E deliverable) | Inline fix. |
| 6B.2 | implementer (plan deviation) | n/a | 30-line / 6-file estimate hit 126 failures across 26 files | Fixture-centric strategy: every tenant-onboarding fixture seeds `allowed_currencies=["USD","CAD"]`. STATUS.md row added. |
| 6D.1 | senior-engineer | NEEDS MINOR FIXES | Builder `pip install --prefix=/install .` relied on hatchling tolerating missing app/ source; HEALTHCHECK `import httpx` depends on undeclared transitive of `fastapi[standard]` | Switched builder to `tomllib`-based explicit dep extraction; HEALTHCHECK uses stdlib `urllib.request`. |
| 6D.8 | senior-engineer | NEEDS MAJOR WORK | `envsubst` no-op on bare `ACCOUNT_ID` / `REGION` tokens; `REVISION_ARN` captured but unused | Aligned `infra/ecs-task-definition.json` to `${VAR}` form throughout; single envsubst call (sed dropped); `update-service --task-definition $REVISION_ARN` pin. |
| 6D.8 | code-flow | MINOR ISSUES | docker build without explicit `--platform`; `REVISION_ARN` unused | `--platform linux/amd64` added; ARN pin (same as above). |
| 6E.1 | doc-reviewer | MINOR TWEAKS | "1100 successful checks" miscitation from Phase 5 load test | Corrected to "10,970 aggregate requests at 183 RPS / ~12ms p95". |

## Plan deviations

- **6B.2 fixture-centric pivot** (substantive). Plan estimated ~30
  lines across ~6 files; execution surfaced 126 failures across 26
  test files. Pivot to fixture-centric seeding of multi-currency
  tenants logged in STATUS.md.
- **6D.8 deploy workflow** (corrective). Plan section listed
  `ECS_TASK_DEFINITION_FAMILY` as a required GitHub Secret; the
  implemented workflow uses `--task-definition $REVISION_ARN` directly
  and does NOT consume the FAMILY secret. Runbook A.11 updated to
  drop the secret from the required list.
- **6E.1 calibration backlog item 8 location** (correction). Plan
  pointed at `app/context.py::derive_route_rarity` for
  `RARITY_MIN_OBSERVATIONS` / `RARITY_THRESHOLD`; actual location is
  `app/tenant_route_baselines.py`. Doc cites the correct file.

## Production bugs caught by reviewer panels

(See "Reviewer-panel corrections" table above for the full list.)

Highlight: the **6D.8 envsubst no-op** would have been a first-deploy
failure — `register-task-definition` rejects ARNs containing literal
"ACCOUNT_ID" / "REGION" strings. Caught by the senior-engineer
reviewer cycle 1 verdict NEEDS MAJOR WORK before any tag push. The
companion fix to the task-def template aligned every placeholder to
`${VAR}` form, enabling single-tool substitution end-to-end.

## BUGS.md state at Phase 6 close

Phase 5 carry-forwards drained at 6E.3:

| BUGS entry | Disposition |
|---|---|
| Multi-stage Dockerfile carry-forward | RESOLVED: 6D.1 |
| `ix_api_tokens_tenant` redundant index | DEFERRED to Phase 7 cleanup |
| 409 catch unreachable in serial tests | DEFERRED to Phase 7 |
| `_assert_decisions_equivalent` duplicated | DEFERRED to Phase 7 |
| docker-compose `.env` localhost mismatch | DEFERRED to Phase 7 (dev-host-only; production uses Secrets Manager) |
| Missing ALTER DEFAULT PRIVILEGES (Phase 6A.6 discovery) | DEFERRED to Phase 7 (per-new-table explicit GRANT enforced by reviewer panel until then) |

No new high-severity BUGS entries surfaced during Phase 6.

## Three new rules — threat-model coverage table

| Rule | Threat sub-class | Weight | Maturity gate | Condition |
|---|---|---|---|---|
| `case_3_compound` | case-3a (established-customer compromise) | 0.70 | maturity_sensitive | `origin_via_carrier_dropoff AND shipment_route_unfamiliar_for_customer AND ip_fully_new AND customer_observations >= 10` |
| `cold_start_country_triangle_with_carrier_dropoff` | case-3b (simple) | 0.65 | cold-start | `customer_country_triangle_mismatch AND origin_via_carrier_dropoff AND customer_observations < 10` |
| `cold_start_population_baseline_rare_with_carrier_dropoff` | case-3b (sophisticated) | 0.70 | cold-start | `shipment_route_rare_for_tenant AND origin_via_carrier_dropoff AND customer_observations < 10` |

## New subsystem — tenant route population baseline

- Table `tenant_route_baselines` (composite PK on
  `(tenant_id, customer_country, origin_country, destination_country)`;
  RLS enforced; GRANT explicit per Phase 6A.6 reviewer-caught lesson).
- Migration `0011_case_3b_schema.py` (UPGRADE_SQL + DOWNGRADE_SQL; RLS
  policy; GRANT/REVOKE pair).
- Synchronous UPSERT on every booking commit via
  `app/tenant_route_baselines.py::update_tenant_route_baseline`.
- Eval-time derivation via `derive_route_rarity` (`RARITY_MIN_OBSERVATIONS = 100`;
  `RARITY_THRESHOLD = 0.02`).
- Cold-start behavior: empty baseline → `shipment_route_rare_for_tenant`
  returns False → sophisticated compound does not fire until baseline
  matures (documented in `docs/production-launch-checklist.md` Phase B
  + Phase D).

## Structured-field architectural pattern

`Customer.registered_country` and `Shipment.origin_via_carrier_dropoff`
both follow the same pattern:

1. Platform supplies the signal at booking time as a structured
   Pydantic field.
2. freightsentry-riskd consumes via structured passthrough — no
   string parsing of address fields.
3. Replay corpus injects ground truth where known (CA for Roulottes
   Lupien; absent for the rest of the case-3b census records).
4. Signal returns `False` / `None` when absent. Rules condition on
   the signal's presence; absent signals do not contribute to fraud
   indication. This eliminates accidental false positives on corpora
   without ground truth.

**Address-string parsing was explicitly rejected** in Phase 6A
amendment design: parsing the last token of an address string for
country code is brittle, locale-dependent, and produces unreliable
signal. The structured-field pattern is the durable answer and
the template for future case-N detection additions.

## Phase 6 readiness assessment

- **Code surface**: case-3a + case-3b detection capability live;
  CAD default with multi-currency tenant support; structured
  `Customer.registered_country`; population baseline subsystem.
- **Rule count**: 82 rules in `app/rules.yaml` (+3 in Phase 6A).
- **Context-field surface**: 76 fields in `ALLOWED_CONTEXT_FIELDS`
  (+5 in Phase 6A).
- **Infrastructure artifacts**: Dockerfile (multi-stage),
  ECS task definition, 3 IAM policy JSONs + README, 567-line AWS GUI
  runbook, 3 GitHub Actions workflows, smoke test.
- **Security posture**: unchanged from
  `docs/security-audit-rls-phase-5.md` baseline. Phase 6 adds one
  RLS-protected table (`tenant_route_baselines`); no posture change.
  Phase 5D auth chicken-and-egg awareness preserved in launch
  checklist always-on section.
- **Observability**: unchanged from `docs/observability.md` baseline.
  New rule-fire events emit automatically via the existing EMF
  mechanism. CloudWatch Logs target configured in ECS task def.
- **Latency budget**: ~16ms p95 post-amendment baseline (~12ms Phase
  5D + ~4ms Phase 6A.7/6A.8 overhead). 184ms ceiling headroom
  retained. Yellow flag at 50ms / red flag at 195ms documented in
  calibration backlog item 15.

## Open items for post-launch

- All 15 items in `docs/calibration-backlog.md`.
- The 5-month observation window methodology in
  `docs/production-launch-checklist.md` Phase E-H.
- Phase 7+ scope hints documented in launch checklist Phase I:
  auto-rollback, multi-environment GitHub Actions promotions, IaC
  migration (Terraform/CDK), additional fraud detection sub-classes,
  trust-suppression architectural workstream.

## Decision trail summary

Sections added or extended in `.ai/decisions.md` during Phase 6:

- **Case-3 detection capability** (Phase 6A — 6A.10 amendment).
  Structured-field pattern, case-3a/case-3b distinction, population
  baseline subsystem rationale, latency budget bookkeeping, Phase 7+
  trust-suppression workstream.
- **Phase 4B currency-default switch** (Phase 6B.3 amendment to the
  existing Currency Normalization section). CAD → default; multi-
  currency tenant support unchanged.
- **Phase 6C replay-validation findings + calibration backlog seed**
  (Phase 6C.5). Strict measurement enumeration; 0% case-3b cluster
  detection diagnosed; calibration items seeded.
- **Phase 6D deployment artifacts** (6D.9). Multi-stage Dockerfile
  rationale, ECS Fargate, no-IaC posture, three-level Actions
  separation, OIDC, manual rollback, single-tool substitution lesson.
- **Phase 6 closeout** (6E.3 — links to the per-batch amendments;
  hand-off to post-launch).

## Phase 6 sign-off

Phase 6 ships with:

- Case-3a + case-3b detection capability (3 new rules, 2 new
  structured fields, 1 new population-baseline subsystem).
- CAD-default tenant config with multi-currency tenant support
  preserved.
- Strict-enumeration measurement findings from a 10,000+500+95
  three-corpus replay; calibration backlog populated for the
  5-month observation window.
- Deployment-ready artifact set: multi-stage Dockerfile, ECS Fargate
  task definition, three IAM policy JSONs, 567-line AWS GUI runbook,
  smoke test script, three GitHub Actions workflows (test / build /
  deploy via OIDC).
- Operator-facing wrap documents: `docs/calibration-backlog.md`,
  `docs/production-launch-checklist.md`, this report.

Operator decides merge to `main` + tag `v1.0.0` + 5-month observation
launch timeline separately per
`docs/production-launch-checklist.md`.
