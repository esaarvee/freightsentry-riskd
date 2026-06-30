# System status — freightsentry-riskd

**Stage**: Pre-launch. Phases 1-8 complete. Production deploy to `ca-central-1` is the next operator-driven step.

**Production region**: `ca-central-1`. **Test/staging region**: `us-east-2`. Single-tenant cutover at launch; SaaS multi-tenant capability ready from the foundation schema onward (RLS-enforced; `tenants` table is the multi-tenant root).

For per-phase historical narrative, decisions, and outcome records, see [`docs/history.md`](../docs/history.md). For current architecture, see [`.ai/decisions.md`](./decisions.md). For the schema, see [`.ai/schema.md`](./schema.md). For the rule catalogue, see [`.ai/rules.md`](./rules.md).

## Implications for design and execution (pre-launch)

- **No production traffic yet.** All validation runs against integration tests (1118 tests passing per the `tests/coverage_baseline.txt` anchor at Phase 8A close), the staging replay corpus (`us-east-2`), and synthetic fixtures (case-1 ATO ~50 shipments, case-2 ATO ~21K shipments, Phase 6/7 case-3 carrier-dropoff fixtures).
- **No production logs, no production telemetry yet.** Phase 5C wired EMF-formatted JSON to stdout for the CloudWatch sink. Phase 6 wired the rule-fire and decision metrics; Phase 7 added held-booking + case-2/case-3 metrics. Live observability begins when production traffic starts.
- **Latency claims validated under staging load only.** Phase 5C load test against the staging Docker Compose stack confirmed the <200 ms p95 ceiling; production re-measurement happens post-launch.
- **Operator runbooks** in [`docs/`](../docs/) describe the launched-system procedures. The production-launch-checklist and AWS deploy runbook are the active operational references.
- **Post-Phase-8 refactor (Pattern B-lite).** Enrichment sources auto-refresh in-process ([`app/enrichment_refresh.py`](../app/enrichment_refresh.py) + the FastAPI lifespan refresh loop), with `/health` reporting `enrichment: "ok" | "degraded"`. Deploys run a gated `freightsentry-riskd-migrate` ECS task — exit-0-gated before the app rollout — on every `v*` tag push ([`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml)).

## Phase status

| Phase | Status | Outcome |
|---|---|---|
| Phase 1 — Foundation + signal/baseline core | Complete | Postgres + Alembic + RLS + DSL + initial rule catalogue + skeleton API |
| Phase 2 — Trust + account-prior + full rule library | Complete | Layer 2 wired; trust score computed on read; ~62 rules total |
| Phase 3 — Modification + feedback + tenant scoping | Complete | `/v1/modification` endpoint + feedback ingestion (3B) + previously-rejected baselines |
| Phase 4 — Per-tenant config + cold-start + admin reads | Complete | `TenantConfig` + currency ALLOW-list + cold-start grace + admin endpoints |
| Phase 5 — Observability + security hardening + load test | Complete | EMF observability; tenant-config cache; `riskd_app_login` runtime role; load test green |
| Phase 6 — Deploy artifacts + fixture replay + case-3 detection | Complete | Case-3a + case-3b rules; `tenant_route_baselines`; multi-stage Docker; GitHub Actions test/build/deploy |
| Phase 7 — Pre-launch calibration + case-2 learning + retire BLOCK target | Complete | `api_booking_from_unfamiliar_asn` (case-2); ALLOW-only baseline gating (7C.11); geo-rule weight calibration; BLOCK target retired in favour of per-customer case-2 framing |
| Phase 8 — Pre-launch consolidation | Complete | Migration squash 11→5 (8A); coverage anchor at 91% + test rename (8B); doc consolidation + history.md absorption + plan-file teardown (8C); phase wrap + 1116-test final pass (8D) |

## Anti-drift gates

Established in Phase 8A/8B to prevent silent regression as the codebase matures:

- **Schema golden test** — [`tests/integration/test_schema_golden.py`](../tests/integration/test_schema_golden.py) (from 8A.0). Snapshots the post-squash schema (5 migrations, 13 tables, 2 roles) and fails CI if any column, index, constraint, RLS policy, or grant drifts without a corresponding alembic migration.
- **Coverage non-regression** — [`tests/coverage_baseline.txt`](../tests/coverage_baseline.txt) (from 8B.0). Anchors line coverage at 91% (Phase 8B baseline). CI enforces non-regression; a drop below baseline fails the build.
- **Lint / type / unit-test gate** — [`.github/workflows/test.yml`](../.github/workflows/test.yml) runs `ruff check`, `ruff format --check`, `mypy app/`, `pytest tests/unit/` on every push. Integration tests run on PR.
- **Pre-commit hooks** — [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) replicates the lint/type/unit-test gate locally as a non-bypassable per-commit enforcement (per CLAUDE.md "Pre-commit enforcement").

## Tech stack snapshot

- **Language**: Python 3.13+ (3.14 is the operator's local; pre-commit pins `python3` auto-discovery).
- **Web framework**: FastAPI on `uvicorn`. Single ASGI app at `app/main.py`.
- **Database**: PostgreSQL 16. Connection pool via `asyncpg`. Migrations via Alembic (5 post-squash revisions in [`alembic/versions/`](../alembic/versions/)).
- **Schema validation**: Pydantic v2. All request/response models live in `app/api/<endpoint>.py`.
- **Config**: `pydantic-settings`. No `env_prefix` — env var names match field names verbatim (e.g. `DATABASE_URL`, `HMAC_SECRET`). Sourced from `.env` locally; from the platform secret manager in production.
- **External data**: MaxMind GeoLite2-Country + GeoLite2-ASN (geo lookup); IP2Proxy PX11 (VPN / Tor / threat tagging); FireHOL Level 1 + Level 2 (IP threat feeds). All cached locally via `ip_enrichment` table.
- **Container**: Multi-stage Dockerfile (build vs runtime separation, per Phase 6D). Runtime image strips build-tools.
- **CI**: GitHub Actions two-stage pipeline ([`.github/workflows/test.yml`](../.github/workflows/test.yml) on PRs to `v*`/`release/*`, [`deploy.yml`](../.github/workflows/deploy.yml) on `v*` tag push).
- **Production target**: ECS Fargate (`ca-central-1`). OIDC for AWS auth (no long-lived access keys).

## Pre-launch readiness

The Phase 8 close gates production launch. Items the operator confirms before flipping production traffic:

- [`docs/production-launch-checklist.md`](../docs/production-launch-checklist.md) — operational acceptance criteria and SQL probes.
- [`docs/aws-deploy-runbook.md`](../docs/aws-deploy-runbook.md) — GUI walkthrough for the ECS Fargate deploy.
- [`docs/calibration-backlog.md`](../docs/calibration-backlog.md) — open monitoring + tuning items deferred to the post-launch 5-month observation window.
- [`docs/security-audit-rls-phase-5.md`](../docs/security-audit-rls-phase-5.md) — RLS + runtime-role audit (operational reference; do not delete).
- [`docs/replay-validation.md`](../docs/replay-validation.md) — Phase 7D final measurement record and methodology.

The launch is operator-driven, not Claude-driven. The 5-month FPR observation window begins at the operator's launch flip.

## Mid-run deviations

[`.claude/STATUS.md`](../.claude/STATUS.md) `Unforeseen / checkpoints` table captures decisions surfaced during execution that diverge from the approved plan. Phase 1-7 closed with 9 logged checkpoints; the operator triages at phase boundaries.

[`.claude/BUGS.md`](../.claude/BUGS.md) captures tangential issues discovered mid-task. The operator drains at phase boundaries; resolved items receive a `RESOLVED: <commit>` annotation; deferred items get a `DEFERRED to <plan>` annotation.

Last updated: 2026-06-13 (Phase 8 complete; post-Phase-8 Pattern B-lite refactor landed enrichment auto-refresh + gated migrate-on-deploy; production launch pending operator action).
