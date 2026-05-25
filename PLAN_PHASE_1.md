# Phase 1 Plan — Foundation + signal/baseline core

Phase 1 spans Week 1 in four batches: 1A (Day 1 — adapt pre-staged docs), 1B (Days 2-3 — skeleton), 1C (Day 3 end — stub endpoint), 1D (Days 4-7 — signal + baseline core with Layer 1+3 scoring).

Layer 2 (account-prior + trust-score consumption) is deferred to Phase 2.

---

## Decisions absorbed

| Decision | Value | Source |
|---|---|---|
| Repo name | `freightsentry-riskd` | Operator answer 2026-05-25 |
| AWS region target | Production `ca-central-1`; test/staging `us-east-2` (dual-region split) | Operator answer 2026-05-25 |
| Commit strategy | Atomic (one logical change per commit) | Bootstrap prompt default |
| Reviewer cadence | Per-commit full panel for code-path commits; doc-reviewer per doc-only commit | Operator answer 2026-05-25 |
| Scoring layers in Phase 1 | Layer 1 (hard-block short-circuit) + Layer 3 (signal noisy-OR). Layer 2 deferred to Phase 2. | Bootstrap prompt — "Watch points" |
| `app/trust.py` | Defined in Phase 1 (1D.4), consumed in Phase 2 (Layer 2 + trust-conditioned rules). Phase 1 declared break. | Bootstrap prompt — Batch 1D file list + Watch points reconciliation |
| DSL evaluator | Pure Python `ast` whitelist (~150 LOC), `__builtins__: {}`, env-only `Name` resolution | Bootstrap prompt — Watch points |
| Baseline storage | Postgres JSONB columns, per-IP-type half-lives (cloud/dc 365d, residential 60d, unknown 180d), other dims 90d, `SELECT FOR UPDATE` on writes | Design Context + verification §3.2 |
| IP stat-dict entry shape | `{n, r_n, last, type?}` (type omitted for non-IP stats) | Verification §3.1 |
| Datacenter ASN heuristics | Port `_DATACENTER_KEYWORDS` + `_DATACENTER_PROVIDERS` constants verbatim from freight_risk signals.py:132-149 | Verification §2.1 |
| Tuned signal thresholds | `cadence_anomaly` z>6, `velocity_spike_daily_api` 50, `residential_asn_high_velocity` 15, `ip_familiarity_tier` /24-only family-familiar | Verification §2.2 |
| `email_matches_customer_name` | NOT implemented in any form | Bootstrap prompt — constraint #14 / Watch points |
| HMAC at egress | `hmac_hex` without `@lru_cache` (secret-rotation safe) | Verification §2.6 |
| RLS on tenant-scoped tables | Enforced from first migration (1B.2) | Bootstrap prompt — "DO: Apply Postgres row-level security to every table with tenant_id" |
| Decision persistence | **Synchronous within the same transaction as baseline update.** Single txn: INSERT shipments + INSERT decisions + baseline.add_observation/save + UPDATE customers.total_shipments and last_seen. Failures return 500; idempotency on `(tenant_id, request_id)` guarantees retry correctness. The bootstrap prompt's "background asyncio tasks in the same process" forbids a separate worker process; it does NOT mean fire-and-forget writes after the response. | Operator amendment 2026-05-25 |
| Phase 1 initial rule count | 12-15 rules wiring 10 signals; trust-conditioned rules excluded (deferred to Phase 2 with Layer 2) | Bootstrap prompt — Batch 1D + Verification §6.1 |
| Sources of truth for rule thresholds | YAML file is authoritative; Pydantic defaults removed (avoid drift) | Verification §2.3 |
| IP2Proxy `is_proxy` gating | True only when `proxy_type` is non-sentinel | Verification §2.4 |
| `rejected_email_hmacs` / `rejected_phone_hmacs` storage | Separate JSONB columns (per Design Context), not collapsed into `r_n` of `email_hmacs`/`phone_hmacs` | Verification §3.5 |
| FreightSentry rule count correction | 102, not 117 | Verification §1.1 (no plan effect — count was tangential) |
| Recipient-overlap rules origin | freight_risk's catalog (NOT FreightSentry port). Land naturally in Phase 2 with the full 84-rule catalog port. | Verification §1.2 |
| 30-day recent-activity count | **Not persisted as a column.** Computed on demand via `COUNT(*) FROM shipments WHERE booking_ts > now() - interval '30 days'` for rules / admin endpoints that need it. Rules needing a decay-weighted approximation read `customer_baselines.value_n` (post-decay; exposed via Context as `customer_observations`). | Operator amendment 2026-05-25 |
| `customer_baselines.value_n` exposed to rule conditions | Available in Context as the `customer_observations` field — the decay-weighted booking-count proxy. Used by maturity-sensitive rules (e.g. `customer_observations >= 10` guard on novelty/lock-in rules). | Operator amendment 2026-05-25 |
| `customer_locked_cloud_api` | Derived in `build_context` (Phase 2). Phase 1's customer_baselines schema supports it (cloud_share + api_share + effective_observations all stored). | Verification §3.4 |

---

## Workflow context

- 6-step commit cycle: implement → validate → review → iterate → commit → proceed.
- Reviewer panel routing per CLAUDE.md (adapted in Batch 1A).
- Operator checkpoints: after Batch 1A, after Batches 1B + 1C, after Batch 1D (Phase-1 report).
- Per-commit summary line posted after commit, referencing the section ID below.
- `.claude/STATUS.md` `Unforeseen / checkpoints` table is the only mid-run escalation channel.

When invoking reviewer agents, the plan-file slice instruction follows this template:

```
Plan file: PLAN_PHASE_1.md, current commit: {1A.1, 1A.2, ...} of {total},
upcoming commits: {N+1} through {1D.last} sections. Read only those sections.
```

---

## Batch 1A — Adapt pre-staged foundation (Day 1)

7 commits, all doc-only. Each runs through the doc-only path: doc-reviewer agent against the diff. Operator checkpoint at end of batch before Batch 1B.

### 1A.1 — Trim CLAUDE.md

**Theme**: Remove multi-service / gRPC / proto / Redis / Redis-Streams / AI-orchestration / Go-specific references. Preserve the 6-step commit cycle, reviewer panel, declared-breaks mechanism, autonomous-execution rules, and skip-routing decision tree (Python-paths-only).

**Files**:
- `CLAUDE.md` (rewrite)

**Specific deletions** (verified against the pre-staged file already present):
- The "App", "Stack", "Storage", "Transport" project-identity lines (rewrite for single Python service + Postgres-only)
- "Load by Task" rows referencing `services/gateway`, `services/rules-engine`, `services/async-worker`, `proto/`, `cmd/`, `streams/`, `mcp/`, `ai/`, gRPC, Go test commands
- "Validation Commands" rows for Rules Engine, Async Worker, Go build, Proto regen
- Reviewer routing rules conditioned on `.go`, `.proto`, `streams/`, `mcp/` file patterns
- "Skip Go test files" patterns

**Preserve verbatim** (these are workflow discipline and load-bearing):
- The "Plan Mode — Commit Cycle" section in full
- Declared-breaks mechanism (full prose)
- Reviewer routing decision tree (decode: complete skip / lightweight skip / partial panel / full panel / never skip / operator override)
- Quality standards section
- Autonomous Execution section
- `.claude/STATUS.md` reference

**New content**:
- Validation commands table: `ruff check app/ tests/`, `mypy app/`, `pytest tests/ -v --asyncio-mode=auto`, `alembic upgrade head` (docker), `docker compose up -d postgres`
- Load-by-task rows for: any coding task, any test-writing task, enrichment, scoring/rules, DB/migrations, REST API work, IP enrichment, plan mode

**Validation**:
- `markdownlint CLAUDE.md` if available; manual read-through
- Grep for stale references: `grep -E "(grpc|proto|redis|streams|mcp|async-worker|rules-engine|gateway/.*\.go|cmd/|go test|go build)" CLAUDE.md` must return 0 lines
- Grep for service references: `grep -E "services/(gateway|rules-engine|async-worker)" CLAUDE.md` must return 0 lines

**Risk**: **Medium**. The pre-staged CLAUDE.md is the workflow source-of-truth; aggressive trimming risks dropping workflow discipline. Mitigation: keep the commit cycle and reviewer panel verbatim; only remove service-architecture details. Doc-reviewer flags any preserved Go/gRPC/Redis reference as a finding.

**Reversibility**: Easy — file is in git; revert is one command.

**Pre-commit verification**: All grep checks above pass.

**Observability**: N/A (doc-only).

**Test changes**: None.

**Rollback plan**: `git revert <commit>`.

**Declared breaks**:
- Scope: CLAUDE.md references `.ai/decisions.md`, `.ai/conventions.md`, `.ai/rules.md`, `.ai/schema.md`, `.ai/enrichment.md`, `.ai/system-status.md`, `.ai/gotchas/` — these files exist in pre-staged form but with FreightSentry-specific content. Trim/rewrite arrives in 1A.2-1A.6.
- Resolved in: 1A.2 (conventions), 1A.4 (decisions), 1A.5 (rules/schema/enrichment), 1A.6 (system-status + gotchas).

### 1A.2 — Consolidate `.ai/conventions*.md` into single `.ai/conventions.md`

**Theme**: Single Python-only conventions file. Merge pre-staged `conventions-python.md` + `conventions-testing.md` into `conventions.md`. Drop any Go references. Trim duplication.

**Files**:
- `.ai/conventions.md` (rewrite, consolidates the two)
- `.ai/conventions-python.md` (delete)
- `.ai/conventions-testing.md` (delete)

**Validation**:
- Manual read for coherence
- Grep `\.go` / `func ` / `package ` in `.ai/` must return 0 lines

**Risk**: **Low**.

**Reversibility**: Easy.

**Pre-commit verification**: All grep checks pass.

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**: None — self-contained.

### 1A.3 — Adapt reviewer agents

**Theme**: Trim six reviewer agents to Python-only single-service. Drop Go-specific dimensions, multi-service routing instructions, gRPC/proto patterns.

**Files**:
- `.claude/agents/senior-engineer-reviewer.md`
- `.claude/agents/security-auditor.md`
- `.claude/agents/code-flow-reviewer.md`
- `.claude/agents/test-reviewer.md`
- `.claude/agents/db-reviewer.md`
- `.claude/agents/doc-reviewer.md`

**Specific changes per agent**:
- `senior-engineer-reviewer.md`: drop Go test discipline checks; keep Python+SQL+YAML focus; add DSL-evaluator-security-completeness dimension
- `security-auditor.md`: drop gRPC/proto checks; add Postgres RLS coverage, HMAC-at-egress, asyncpg parameter-binding (no string interpolation), DSL `__builtins__` lockdown
- `code-flow-reviewer.md`: drop Go service-boundary checks; add asyncio.gather correctness, asyncpg connection-pool usage, baseline `SELECT FOR UPDATE` discipline
- `test-reviewer.md`: drop Go test verdicts; add pytest+asyncio-mode discipline, integration vs unit boundary, deterministic time/random
- `db-reviewer.md`: drop multi-database checks; focus on Postgres 16, JSONB index strategy, RLS policy completeness, migration reversibility
- `doc-reviewer.md`: keep mostly as-is; verify against the new `.ai/` topology

**Validation**:
- Grep for stale references per agent
- Manual read-through

**Risk**: **Medium**. Reviewer agents are the quality backstop. Cosmetic trimming that misses a load-bearing dimension lets future bugs slip. Mitigation: each agent file is reviewed by the doc-reviewer in batch; reviewer-panel-against-itself catches gaps.

**Reversibility**: Easy.

**Pre-commit verification**: Grep checks; manual read.

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

### 1A.4 — Create `.ai/decisions.md` from Design Context

**Theme**: Distill the bootstrap prompt's Design Context into a permanent project-internal record. This becomes the source-of-truth for "why is X this way" questions during execution.

**Files**:
- `.ai/decisions.md` (new file)

**Content sections**:
- Project identity (real-time SaaS, single service, Postgres-only, multi-tenant)
- Endpoints (the four; admin-read pair in Phase 4)
- Scoring architecture (3-layer noisy-OR, hard-block short-circuit, account-prior formula, signal-layer noisy-OR with maturity downweight, thresholds, configurable constants)
- Trust score (computed on read; not persisted; formula)
- Customer baseline (per-IP-type half-lives; stat-dict shape; Welford triples; HMAC sets)
- Per-tenant config schema (`TenantConfig` Pydantic model; defaults)
- Cold-start strategy
- IP enrichment sources (URLs, license, refresh cadence — verified in Phase 1)
- Rule catalog target (~95-100 end of Phase 2)
- Out-of-scope items (LLM, PDFs, MCP, etc.)
- Cross-cutting constraints (latency target, cost ceiling, simplicity-as-feature)

**Validation**:
- Manual read for completeness against bootstrap prompt
- Doc-reviewer agent

**Risk**: **Low**.

**Reversibility**: Easy.

**Pre-commit verification**: Doc-reviewer pass.

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**:
- Scope: References signals/rules/schema that don't exist until 1D.
- Resolved in: 1D.* (signal + rule + scoring implementation).

### 1A.5 — Trim `.ai/rules.md`, `.ai/schema.md`, `.ai/enrichment.md`

**Theme**: Pre-staged versions reference Go services, multi-database, gRPC. Trim to single-service Postgres-only.

**Files**:
- `.ai/rules.md` (rewrite)
- `.ai/schema.md` (rewrite)
- `.ai/enrichment.md` (rewrite)

**Specific changes**:
- `.ai/rules.md`: drop expr-lang references; document Python `ast` DSL; describe rule action types (BLOCK vs score-only via weight); describe maturity_sensitive flag; describe per-customer rule weight (deferred to post-launch — note this for context)
- `.ai/schema.md`: drop platform-MySQL references; document Postgres-only schema (12 tables in Phase 1); document JSONB stat-dict shape; document `(tenant_id, *)` index strategy
- `.ai/enrichment.md`: trim to four sources (MaxMind, FireHOL, IP2Proxy, cloud CIDRs); URLs and auth from verification §4; the lazy-cache strategy

**Validation**:
- Grep for stale references
- Doc-reviewer

**Risk**: **Low**.

**Reversibility**: Easy.

**Pre-commit verification**: Doc-reviewer pass.

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

### 1A.6 — Rewrite `.ai/system-status.md`; filter `.ai/gotchas/`

**Theme**: New project-status doc; gotchas trimmed to Python + Postgres entries.

**Files**:
- `.ai/system-status.md` (rewrite)
- `.ai/gotchas/index.md` (rewrite or trim)
- `.ai/gotchas/python.md` (or similar — keep)
- `.ai/gotchas/postgres.md` (keep)
- `.ai/gotchas/*` (delete Go-related, gRPC-related, Redis-related, MCP-related, AI-related entries)

**Specific gotchas to add** (from verification doc):
- `python.md`: `@lru_cache` + secret-rotation hazard; `asyncio.gather` swallows the first exception only; FastAPI dependency-overrides only persist within the same instance
- `postgres.md`: JSONB `?` operator vs `@>`; RLS policies + `SET LOCAL ROLE` discipline; `SELECT FOR UPDATE` lock release on transaction end only

**Validation**:
- Doc-reviewer
- Manual read

**Risk**: **Low**.

**Reversibility**: Easy.

**Pre-commit verification**: Doc-reviewer pass.

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

### 1A.7 — Project metadata: README, pyproject, .gitignore, .dockerignore

**Theme**: Project entry points.

**Files**:
- `README.md` (new — repo name placeholder, one-paragraph description, "see CLAUDE.md and MASTER_PLAN.md")
- `pyproject.toml` (new — Python 3.13+, deps: fastapi[standard], asyncpg, pydantic-settings, alembic, sqlalchemy[asyncio], hypothesis (test-time), pytest+asyncio, ruff, mypy, locust (Phase 5))
- `.gitignore` (extend — add `.env`, `.env.local`, `__pycache__`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `data/enrichment/`, `*.log`)
- `.dockerignore` (extend — drop the dev-only files from the image)

**Validation**:
- `python -m build` (or `uv sync`) succeeds
- `ruff check .` clean on all docs+config

**Risk**: **Low**.

**Reversibility**: Easy.

**Pre-commit verification**: pyproject parses successfully via `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"`.

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Operator checkpoint — end of Batch 1A**: post one-line summary; wait for approval before Batch 1B.

---

## Batch 1B — Skeleton (Days 2-3)

4 commits. Each runs the full code-path reviewer panel (1B.2 also runs db-reviewer; 1B.4 also runs test-reviewer).

### 1B.1 — Docker Compose + `.env.example`

**Theme**: Local dev stack.

**Files**:
- `docker-compose.yml` (new — postgres:16-alpine, app service built from Dockerfile)
- `Dockerfile` (new — python:3.13-slim base, uv install, copy app, EXPOSE 8000, entrypoint via uvicorn)
- `.env.example` (new — FG_DATABASE_URL, FG_HMAC_SECRET, FG_API_TOKEN_PREFIX, FG_MAXMIND_LICENSE_KEY, FG_IP2PROXY_DOWNLOAD_TOKEN, FG_LOG_LEVEL)
- `.env` (gitignored; operator copies from example)

**Validation**:
- `docker compose config` clean
- `docker compose up -d postgres && docker compose exec -T postgres pg_isready` succeeds
- No secrets committed to `.env.example` (placeholders only)

**Risk**: **Medium**. Compose file is the local dev contract; mismatched Postgres versions or missing volumes silently fail Phase 1 tests later.

**Reversibility**: Easy.

**Pre-commit verification**: Compose-config; `pg_isready` smoke test; secret scan.

**Observability**: N/A (infra-only).

**Test changes**: None (the test container service lands in 1B.4).

**Rollback plan**: `git revert`.

**Declared breaks**: None.

### 1B.2 — Alembic init + initial migration

**Theme**: Schema for the 12 tables.

**Files**:
- `alembic.ini` (new)
- `alembic/env.py` (new — async migration support via asyncpg)
- `alembic/versions/0001_initial.py` (new — the 12 tables + RLS policies + indexes)

**Tables** (verified against Design Context + verification §3.5-3.6):

1. `tenants` (id, name, config JSONB DEFAULT '{}', first_seen, created_at)
2. `enterprises` (id, tenant_id FK, external_id, first_seen, created_at; UNIQUE(tenant_id, external_id))
3. `customers` (id, tenant_id FK, enterprise_id FK NULLABLE, external_id, registered_address, business_name, is_api_partner BOOL DEFAULT FALSE, first_seen, last_seen, flagged_count INT DEFAULT 0, fraud_confirmed_count INT DEFAULT 0, total_shipments INT DEFAULT 0, created_at; UNIQUE(tenant_id, external_id)). Note: no `shipment_volume_30d` column — 30-day window counts computed on demand from `shipments`.
4. `users` (id, tenant_id FK, customer_id FK, external_id, first_seen, last_seen, created_at; UNIQUE(tenant_id, customer_id, external_id))
5. `shipments` (id, tenant_id FK, customer_id FK, user_id FK, request_id, source_ip INET, origin JSONB, destination JSONB, value NUMERIC, channel TEXT, booking_ts TIMESTAMPTZ, created_at; UNIQUE(tenant_id, request_id) — idempotency)
6. `decisions` (id, tenant_id FK, shipment_id FK, request_id, score NUMERIC, decision TEXT, classification TEXT, risk_level TEXT, triggered_rules TEXT[], risk_factors JSONB, created_at; UNIQUE(tenant_id, request_id))
7. `feedback` (id, tenant_id FK, decision_id FK, label TEXT, reviewer_user_id TEXT NULLABLE, created_at)
8. `customer_baselines` (id, tenant_id FK, customer_id FK, origin_stats JSONB, dest_stats JSONB, lane_stats JSONB, ip_stats JSONB, ip_netblock_stats JSONB, ip_asn_stats JSONB, country_stats JSONB, origin_ip_country_stats JSONB, email_hmacs JSONB, phone_hmacs JSONB, rejected_email_hmacs JSONB, rejected_phone_hmacs JSONB, email_domain_stats JSONB, phone_prefix_stats JSONB, ip_type_hist JSONB, hour_hist JSONB, weekday_hist JSONB, channel_hist JSONB, value_n NUMERIC, value_mean NUMERIC, value_m2 NUMERIC, cadence_n NUMERIC, cadence_mean_h NUMERIC, cadence_m2_h NUMERIC, last_booking_ts TIMESTAMPTZ, last_booking_lat NUMERIC, last_booking_lon NUMERIC, last_booking_country TEXT, decay_anchor_date DATE, first_seen TIMESTAMPTZ, last_seen TIMESTAMPTZ, updated_at TIMESTAMPTZ; UNIQUE(tenant_id, customer_id))
9. `ip_enrichment` (ip INET PK, country TEXT, region TEXT, city TEXT, lat NUMERIC, lon NUMERIC, asn_org TEXT, fh_level1 BOOL, fh_level2 BOOL, fh_lists TEXT, is_cloud BOOL, cloud_provider TEXT, is_datacenter BOOL, is_proxy BOOL, is_vpn BOOL, is_tor BOOL, proxy_type TEXT, threat TEXT, updated_at TIMESTAMPTZ; NOTE: NOT tenant-scoped — IP enrichment is global)
10. `api_tokens` (id, tenant_id FK, token_hash TEXT, role TEXT DEFAULT 'tenant', created_at, last_used_at; UNIQUE(token_hash))
11. `app_users` (id, tenant_id FK, external_id, role TEXT, created_at; UNIQUE(tenant_id, external_id)) — for Phase 4 admin endpoints; defined now to lock in RLS pattern
12. `global_blocked_vectors` (id, vector_type TEXT, vector_hash TEXT, created_by_tenant_id FK, share_enabled BOOL DEFAULT FALSE, created_at; UNIQUE(vector_type, vector_hash)) — capability stub; share disabled in v1

**RLS policies** (every tenant-scoped table):
- Enable RLS: `ALTER TABLE <name> ENABLE ROW LEVEL SECURITY`
- Tenant-isolation policy: `CREATE POLICY tenant_isolation ON <name> USING (tenant_id = current_setting('app.tenant_id')::int)`
- `ip_enrichment` and `global_blocked_vectors` skip RLS (intentionally global)
- App role (created via migration): `CREATE ROLE riskd_app NOLOGIN BYPASSRLS = FALSE`

**Indexes**:
- `customers (tenant_id, external_id)` UNIQUE
- `customers (tenant_id)` — for tenant-scoped admin queries
- `shipments (tenant_id, customer_id, booking_ts)` — for velocity counts
- `shipments (tenant_id, source_ip, booking_ts)` — for IP velocity
- `shipments (tenant_id, request_id)` UNIQUE
- `decisions (tenant_id, request_id)` UNIQUE
- `decisions (tenant_id, shipment_id)`
- `customer_baselines (tenant_id, customer_id)` UNIQUE
- `ip_enrichment (ip)` PK
- `api_tokens (token_hash)` UNIQUE
- `feedback (tenant_id, decision_id)`
- `global_blocked_vectors (vector_type, vector_hash)` UNIQUE

**Validation**:
- `alembic upgrade head` succeeds against fresh Postgres
- `alembic downgrade base && alembic upgrade head` round-trip succeeds
- `psql` (via docker exec): every table has RLS enabled (where applicable)
- `psql`: every UNIQUE constraint and index present
- Migration file passes `ruff check`

**Risk**: **High**. Initial migration is the schema contract. Bad RLS policy or missing index has compounding cost.

**Reversibility**: Easy until any data exists; thereafter must reverse-engineer downgrade.

**Pre-commit verification**: Round-trip migration test; RLS query against pg_policies.

**Observability**: N/A (infra-only; runtime observability lands with code).

**Test changes**: None (test fixtures land 1B.4).

**Rollback plan**: `git revert` (still pre-data).

**Declared breaks**:
- Scope: `tenants` table exists but no tenant-onboarding script; manual SQL insert for Phase 1 dev. Operator script in Phase 4.
- Resolved in: Phase 4 (`scripts/tenant_onboard.py`).
- Scope: `app_users`, `api_tokens`, `global_blocked_vectors` tables defined but unused in Phase 1.
- Resolved in: `api_tokens` consumed in 1B.4 (auth); `app_users` consumed in Phase 4; `global_blocked_vectors` remains capability stub indefinitely.

### 1B.3 — `app/main.py` FastAPI lifespan, `app/db.py` asyncpg pool, `app/config.py` pydantic-settings

**Theme**: App entry point + DB pool + config loader.

**Files**:
- `app/__init__.py`
- `app/main.py` (FastAPI app, lifespan creates pool, registers routes)
- `app/db.py` (`get_pool()`, `get_conn()` async context manager, `set_tenant_id(conn, tenant_id)` for RLS)
- `app/config.py` (`Settings` pydantic-settings with `FG_` prefix; loaded from `.env`)
- `app/logging.py` (structlog setup, JSON output to stdout)

**Validation**:
- `docker compose up -d` brings up app + Postgres; app responds (no endpoints yet beyond what's wired in 1B.4)
- `ruff check app/`
- `mypy app/` strict mode

**Risk**: **Medium**. FastAPI lifespan + asyncpg pool is the request-foundation; gotchas in pool sizing or RLS-session leak compound.

**Reversibility**: Easy.

**Pre-commit verification**: App starts; logs JSON; pool initializes.

**Observability**: structured log entries on lifespan start/stop, pool size, tenant_id session set/clear.

**Test changes**: None (lifespan smoke test lands 1B.4).

**Rollback plan**: `git revert`.

**Declared breaks**:
- Scope: `app/main.py` defines lifespan but no API routes yet (health lands 1B.4).
- Resolved in: 1B.4.

### 1B.4 — `app/auth.py` (API token + RLS scope), `app/api/health.py`, first integration test

**Theme**: Auth middleware + RLS session set + health endpoint + first integration test.

**Files**:
- `app/auth.py` (`require_api_token` FastAPI dependency: parses Authorization header, hashes via SHA-256, looks up in `api_tokens`, returns `tenant_id` + role)
- `app/api/health.py` (`GET /health/` — returns `{ok: true, db: <pool stats>, version: ...}`)
- `app/main.py` (extended — registers health route, wires lifespan)
- `tests/__init__.py`
- `tests/conftest.py` (asyncpg fixture, tenant fixture, api_token fixture, RLS-enabled test session)
- `tests/integration/test_health.py`

**Auth specifics**:
- API tokens stored as `sha256(token)` in `api_tokens.token_hash`
- Issuance: manual SQL insert for Phase 1 dev (operator copy-paste from CLAUDE.md run-book)
- Per-request: dependency loads `tenant_id` + role, then `await set_tenant_id(conn, tenant_id)` sets `app.tenant_id` session variable so RLS policies enforce
- 401 if token missing or unknown
- 403 if role insufficient (admin endpoints only — Phase 4)

**Health endpoint specifics**:
- Returns 200 with current pool stats
- No auth required on `/health/` itself (load-balancer probe)
- DB liveness check: `SELECT 1` with 1s timeout

**Validation**:
- `ruff check app/ tests/`
- `mypy app/`
- `pytest tests/integration/test_health.py -v --asyncio-mode=auto` passes
- Integration test asserts: /health returns 200 with `ok: true`
- Integration test asserts: invalid API token → 401; missing token → 401; valid token sets `app.tenant_id` correctly

**Risk**: **High** (auth + RLS is security-critical). Mitigation: security-auditor explicitly verifies the dependency loads tenant_id BEFORE any DB read; integration test attempts to bypass and confirms blocked.

**Reversibility**: Easy.

**Pre-commit verification**: All tests pass; security-auditor verifies RLS chain end-to-end.

**Observability**: structured log on each auth attempt (success/fail; tenant_id; role; no token plaintext); metric counters `auth.success` / `auth.fail` / `auth.invalid_token`.

**Test changes**: Initial test infrastructure (conftest with asyncpg + tenant + token fixtures) lands here.

**Rollback plan**: `git revert`. Note: auth must work for any subsequent endpoint; reverting forces re-implementation.

**Declared breaks**:
- Scope: `api_tokens` issuance is manual SQL insert; no operator script.
- Resolved in: Phase 4 (`scripts/tenant_onboard.py`).

**Operator checkpoint — end of Batches 1B + 1C**: post summary; wait for approval before Batch 1D.

---

## Batch 1C — Stub booking endpoint (Day 3 end)

2 commits.

### 1C.1 — `app/models.py`, `app/api/booking.py` stub

**Theme**: Pydantic request/response models matching the Design Context booking payload schema; stub endpoint accepts the payload, upserts customer/enterprise/user records, returns ALLOW 0.0, persists decision row.

**Files**:
- `app/models.py` (Pydantic v2 models: `BookingRequest`, `BookingResponse`, `Customer`, `Enterprise`, `User`, `Shipment`, `Contact`)
- `app/api/booking.py` (`POST /api/v1/shipments/booking/evaluate` — stub)
- `app/services/entity_upsert.py` (helper for the implicit-registration upsert)
- `app/services/decision_persist.py` (background-task helper to insert decision row)
- `app/main.py` (extended — registers booking route)

**Endpoint stub specifics**:
- Validates payload via Pydantic
- Upserts customer (by `tenant_id, external_id`): creates if absent with payload metadata; updates `registered_address`/`business_name`/`is_api_partner` if payload provides newer values
- Same for enterprise (if `enterprise.external_id` present) and user
- Idempotency: looks up `decisions` by `(tenant_id, request_id)`; if present, returns same response
- Persists shipment row
- Persists shipment row + decision row + customer update synchronously within a single transaction before returning (no `asyncio.create_task` for writes)
- Returns `{decision: "ALLOW", score: 0.0, classification: "GREEN", triggered_rules: [], risk_factors: [], request_id: ...}` after the transaction commits
- Persistence failure returns 500; retry uses the `(tenant_id, request_id)` idempotency contract
- HMAC for `origin_email`/`origin_phone`/`destination_email`/`destination_phone` at ingress via `signals.hmac_hex` (function does not exist until 1D.1; in 1C.1 it's `app/services/hmac_stub.py` — see declared break below)

**Validation**:
- `ruff check app/`
- `mypy app/`
- `pytest tests/integration/test_booking_stub.py -v` passes
- Integration tests:
  - Valid booking payload returns 200 ALLOW 0.0
  - Invalid payload returns 422 with field-specific errors
  - Duplicate `request_id` returns same response (idempotency)
  - Missing `origin.address` returns 422
  - Customer/enterprise/user records appear in DB after first booking
  - Booking with `enterprise.external_id` sets `customers.enterprise_id` correctly

**Risk**: **Medium**. Stub endpoint validates the payload contract; if Pydantic models drift from Design Context, 1D rules will fire on the wrong field names.

**Reversibility**: Easy.

**Pre-commit verification**: All tests pass.

**Observability**: structured log per request (request_id, tenant_id, customer.external_id, decision, score, latency); counter `booking.accepted`, `booking.duplicate_request_id`.

**Test changes**: New test file `tests/integration/test_booking_stub.py`.

**Rollback plan**: `git revert`.

**Declared breaks**:
- Scope: HMAC for PII uses `app/services/hmac_stub.py` (returns `hashlib.sha256(value).hexdigest()` without secret) — placeholder. Real `signals.hmac_hex(value, secret)` lands in 1D.1.
- Resolved in: 1D.1 (replaces stub call sites).
- Scope: Endpoint returns ALLOW 0.0 always; no signals run.
- Resolved in: 1D.8 (full pipeline wired).

### 1C.2 — End-to-end booking integration test

**Theme**: Confirm the stub-endpoint pipeline works end-to-end against real Docker Compose Postgres with RLS enforcement.

**Files**:
- `tests/integration/test_booking_e2e.py` (new)
- `tests/fixtures/payloads/booking_minimal.json` (new — minimal valid payload)
- `tests/fixtures/payloads/booking_full.json` (new — payload with all optional fields)
- `tests/conftest.py` (extended with payload-loading fixtures)

**Validation**:
- `pytest tests/integration/test_booking_e2e.py` passes
- Tests:
  - Minimal payload → 200 ALLOW 0.0; customer/shipment/decision rows persisted
  - Full payload → 200 ALLOW 0.0; enterprise + user records also persisted; HMAC-stub'd contact fields stored
  - Cross-tenant booking attempt with tenant_b token but tenant_a customer_id in body → still works (customer is auto-created under tenant_b); customer_id collisions across tenants don't conflict (`UNIQUE(tenant_id, external_id)`)
  - RLS bypass attempt: with tenant_a token, query `customers` directly via raw asyncpg conn with RLS set, can only see tenant_a rows

**Risk**: **Medium**.

**Reversibility**: Easy.

**Pre-commit verification**: All tests pass against `docker compose up -d postgres`.

**Observability**: N/A (test-only).

**Test changes**: New test file + fixtures.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

---

## Batch 1D — Signal + baseline core (Days 4-7)

8 commits. Heaviest batch. Layer 1 (hard-block) + Layer 3 (signal noisy-OR) scoring with 10 signals and 12-15 rules.

### 1D.1 — `app/signals.py` pure helpers

**Theme**: Stateless helper functions, no I/O, no DB.

**Files**:
- `app/signals.py` (new) — functions:
  - `normalize_email(s) -> str`
  - `normalize_phone(s) -> str`
  - `normalize_address(s) -> str`
  - `address_match(a, b) -> bool`
  - `hmac_hex(value, secret) -> str` (no LRU cache)
  - `email_domain(email) -> str`
  - `is_email_disposable(email) -> bool`
  - `is_email_blocklisted(email) -> bool`
  - `is_email_suspicious_pattern(email) -> bool`
  - `is_phone_dummy_pattern(phone) -> bool`
  - `is_datacenter_asn(asn_org) -> bool`
  - `netblock_16(ip) -> str`
  - `netblock_24(ip) -> str`
  - `haversine_km(lat1, lon1, lat2, lon2) -> float`
  - Constants: `THROWAWAY_DOMAINS` (verbatim from freight_risk), `EMAIL_BLOCKLIST`, `KEYBOARD_MASH`, `_DATACENTER_KEYWORDS`, `_DATACENTER_PROVIDERS`
- `app/services/hmac_stub.py` — **delete**, replace all call sites with `signals.hmac_hex`
- `tests/unit/test_signals.py` (new) — table-driven tests for every helper

**Validation**:
- `ruff check app/ tests/`
- `mypy app/`
- `pytest tests/unit/test_signals.py -v` — every function has at least 3 positive + 3 negative cases
- Specific coverage:
  - `is_email_disposable` matches every entry in THROWAWAY_DOMAINS
  - `is_datacenter_asn` matches every entry in _DATACENTER_KEYWORDS and _DATACENTER_PROVIDERS
  - `hmac_hex` produces deterministic SHA-256-HMAC; differs from `hashlib.sha256` (which the stub used)
  - `haversine_km` returns 0 for identical points; reasonable km values for known city pairs

**Risk**: **Medium**. These functions are foundational; an off-by-one in `address_match` or `is_email_suspicious_pattern` propagates to false-positive/negative rule firings.

**Reversibility**: Easy (single file).

**Pre-commit verification**: All unit tests pass; ruff + mypy clean.

**Observability**: N/A (pure functions).

**Test changes**: New `tests/unit/test_signals.py`.

**Rollback plan**: `git revert` — but downstream 1D.* commits depend on this; revert cascades.

**Declared breaks**:
- Scope: `signals.py` defined but only consumed by `app/api/booking.py` (HMAC at ingress, where stub was) and by Phase 1 enrichment/baseline/signal/context modules (which land in this batch).
- Resolved in: 1D.5 (context.py wires signal helpers into Context building); 1D.8 (booking endpoint replaces stub HMAC call).

### 1D.2 — `app/enrich.py` + `scripts/fetch_enrichment.py` + `ip_enrichment` writes

**Theme**: IP enrichment pipeline backed by lazy-loaded MMDB / netset / BIN / CIDR files; cached in `ip_enrichment` table.

**Files**:
- `app/enrich.py` (new) — class `Enricher` with methods:
  - `__init__(data_dir)` — lazy-loads MaxMind MMDBs, FireHOL netsets, IP2Proxy BIN, cloud CIDRs
  - `async enrich(ip, conn) -> dict` — checks `ip_enrichment` for cache; if cache miss or `updated_at > 14 days`, runs full enrichment + INSERT-OR-UPDATE; returns dict
  - `_mm_lookup(ip)`, `_firehol_match(ip)`, `_cloud_match(ip)`, `_ip2proxy_lookup(ip)` — per-source lookups
  - `_classify_ip_type(enrichment)` → `"cloud" | "dc" | "residential" | None`
- `scripts/fetch_enrichment.py` (new) — CLI: fetches latest MaxMind / FireHOL / IP2Proxy / cloud CIDR files into `data/enrichment/`
- `tests/unit/test_enrich.py` (new) — unit tests with mocked MMDB / netset / BIN
- `tests/integration/test_enrich_cache.py` (new) — integration test against real `ip_enrichment` table

**Specifics**:
- IP2Proxy `is_proxy` gated on non-empty `proxy_type` (per verification §2.4)
- IPv4-only (matches freight_risk; IPv6 deferred until platform supports it)
- `_classify_ip_type`: if FireHOL Level 1 hit → don't classify (will route to BLOCK); elif `is_cloud` → "cloud"; elif `is_datacenter` (via asn_org keyword match) → "dc"; elif IP is otherwise unknown but ASN suggests residential → "residential"; else None (= unknown)
- FireHOL Level 1 + Level 2 only (extended list skipped per verification §2.7)

**Validation**:
- `ruff check app/ tests/`
- `mypy app/`
- `pytest tests/unit/test_enrich.py -v` — every source's behavior covered with mocked data
- `pytest tests/integration/test_enrich_cache.py -v` — cache hit/miss/stale paths
- Integration test: enrich an IP twice; second call serves from cache (no source-file re-read)
- Integration test: stale enrichment (>14 days) triggers re-fetch
- `scripts/fetch_enrichment.py --dry-run` succeeds (no actual download)

**Risk**: **Medium-High**. Enrichment failures degrade detection across many rules. Mitigation: cache table + 14-day staleness window; serve stale on upstream failure; alert (Phase 5).

**Reversibility**: Easy.

**Pre-commit verification**: All tests; CLI dry-run.

**Observability**: structured log per enrichment (ip, cache hit/miss/stale, source latencies); counters `enrich.cache_hit`, `enrich.cache_miss`, `enrich.cache_stale`, `enrich.upstream_fail`.

**Test changes**: New unit + integration test files.

**Rollback plan**: `git revert`.

**Declared breaks**:
- Scope: `enrich.py` not yet called from any endpoint.
- Resolved in: 1D.5 (context.py wires it into asyncio.gather pipeline); 1D.8 (booking endpoint runs context).

### 1D.3 — `app/baseline.py` load/decay/update/save

**Theme**: Customer baseline read with lazy decay, write with `SELECT FOR UPDATE`.

**Files**:
- `app/baseline.py` (new) — `class CustomerBaseline` + methods:
  - `async load(conn, tenant_id, customer_id) -> CustomerBaseline | None` (with `FOR UPDATE` on writes; without for reads)
  - `decay_to(as_of: date)` — applies per-IP-type half-lives to ip_stats; uniform 90d to others
  - `add_observation(...)` — folds a booking into the stat-dicts (n increment, last update; r_n via feedback, separate method)
  - `add_rejected_observation(...)` — feedback path
  - `async save(conn) -> None`
  - `effective_observations -> float` — derived from sum of value_n or ip_stats total
  - Helpers: `ip_familiarity_tier(ip, enrichment) -> "familiar" | "family_familiar" | "new_known_asn" | "fully_new"` (per verification §2.2: only /24 match confers `family_familiar`)
- `tests/unit/test_baseline.py` (new) — decay math, ip_familiarity_tier
- `tests/integration/test_baseline_db.py` (new) — load/save round-trip; concurrent write race

**Specifics**:
- Stat-dict entry shape: `{n, r_n, last}` + `type` for ip_stats entries only
- Per-IP-type decay: cloud/dc 365d, residential 60d, unknown 180d; other stat-dicts 90d
- decay_anchor_date advances on every successful write
- SELECT FOR UPDATE in the same transaction as save; no exception path that leaves the row locked
- Welford updates for value (`value_n`, `value_mean`, `value_m2`) and cadence (`cadence_n`, `cadence_mean_h`, `cadence_m2_h`)

**Validation**:
- `ruff check app/ tests/`
- `mypy app/`
- `pytest tests/unit/test_baseline.py -v` — decay math reproducible against worked examples; familiarity tier matrix
- `pytest tests/integration/test_baseline_db.py -v` — round-trip; concurrent-write race resolved via FOR UPDATE
- Concurrency test: two simultaneous `update()` calls on same customer; final state reflects both writes (no lost update); test runs `asyncio.gather(t1, t2)` with explicit ordering assertion

**Risk**: **High**. Baseline correctness is the heart of the system. Decay math errors silently miscalibrate detection over weeks.

**Reversibility**: Easy.

**Pre-commit verification**: All tests; concurrency race test must reliably pass 10 consecutive runs.

**Observability**: structured log per load (tenant_id, customer_id, decay_anchor_date, effective_observations); counters `baseline.load`, `baseline.save`, `baseline.concurrent_wait`.

**Test changes**: New unit + integration test files.

**Rollback plan**: `git revert` — cascades to 1D.5+.

**Declared breaks**:
- Scope: `baseline.py` not yet called from any endpoint.
- Resolved in: 1D.5 (context); 1D.8 (booking endpoint).

### 1D.4 — `app/trust.py` `compute_trust_score`

**Theme**: Trust score function (~10 LOC). Used by `build_context` in 1D.5; not consumed by Phase 1 rule conditions (Phase 1 rule set excludes trust-conditional rules).

**Files**:
- `app/trust.py` (new) — `compute_trust_score(customer, baseline) -> float`
  - `account_age_days = (today - customer.first_seen).days`
  - `effective_obs = baseline.effective_observations` (post-decay)
  - `flagged = customer.flagged_count`
  - `fraud_confirmed = customer.fraud_confirmed_count`
  - Formula: `trust = clamp(0.5 + 0.3 * sigmoid((effective_obs - 20) / 10) + 0.2 * sigmoid((account_age_days - 60) / 30) - 0.4 * (flagged > 0) - 0.6 * (fraud_confirmed > 0), 0, 1)`
  - (Exact constants are Design-Context-derived; first-pass; Phase 2 calibration may adjust)
- `tests/unit/test_trust.py` (new) — boundary cases

**Specifics**:
- New customer (age=0, obs=0): trust ≈ 0.5
- Mature customer (age=365, obs=200): trust ≈ 1.0
- Flagged customer (flagged=1): trust ≈ 0.1
- Fraud-confirmed customer: trust ≈ 0.0
- Sub-millisecond per call (no I/O)

**Validation**:
- `pytest tests/unit/test_trust.py -v`
- Boundary test matrix per Specifics
- `mypy app/`

**Risk**: **Low** (function is tiny; not consumed in Phase 1).

**Reversibility**: Easy.

**Pre-commit verification**: All tests.

**Observability**: N/A (pure function).

**Test changes**: New unit test file.

**Rollback plan**: `git revert`.

**Declared breaks**:
- Scope: `compute_trust_score` defined but NO Phase 1 rule reads `trust_score`. Output is placed on Context (1D.5) but unused.
- Resolved in: Phase 2 (Layer 2 reads `trust_score` for trust_contribution; trust-conditioned rules condition on it).

### 1D.5 — `app/context.py` `build_context`

**Theme**: Per-request Context builder. Loads baseline + decay, IP enrichment, customer/enterprise records, velocity counts via SQL, computes trust score; all via `asyncio.gather`.

**Files**:
- `app/context.py` (new) — `class Context` + `async build_context(conn, tenant_id, payload, hmac_secret) -> Context`
- `app/velocity.py` (new) — SQL-backed counters: `velocity_user_hourly`, `velocity_user_daily`, `velocity_ip_hourly`, `velocity_ip_daily`, plus 30-day variants
- `tests/integration/test_context.py` (new)

**Specifics**:
- All loads parallel via `asyncio.gather(load_baseline, enrich_ip, load_customer, load_enterprise, count_velocity_user_hourly, count_velocity_ip_hourly, ...)`
- After parallel load, applies `baseline.decay_to(today)`
- Computes `trust_score` (1D.4) into Context
- Attaches `is_cloud_ip`, `is_datacenter_ip`, `is_vpn`, `is_tor`, `is_proxy`, `is_new_route`, `ip_fully_new`, etc. (booleans/numerics consumed by rules)
- Exposes `baseline.value_n` (post-decay) as Context field `customer_observations` — the decay-weighted activity proxy used by maturity-sensitive rules in lieu of a persisted 30-day count
- Velocity queries bounded by `(tenant_id, customer_id, booking_ts > now() - interval)` with index hits

**Validation**:
- `ruff check`, `mypy`, `pytest`
- Integration test: seed customer + baseline + ip_enrichment; build_context returns expected fields
- Latency benchmark: 95th percentile <50ms on warm cache

**Risk**: **High** (Context is consumed by every rule; missing field = silent rule-no-op).

**Reversibility**: Easy.

**Pre-commit verification**: All tests; latency check.

**Observability**: structured log on every build_context (tenant_id, customer_id, latency); histogram on per-load latency.

**Test changes**: New integration test.

**Rollback plan**: `git revert`.

**Declared breaks**:
- Scope: `build_context` not yet called from booking endpoint.
- Resolved in: 1D.8.

### 1D.6 — `app/dsl.py` Python `ast`-based rule parser

**Theme**: ~150 LOC pure-Python DSL parser. Parses each rule's `condition` string at startup; compiles to a callable; evaluates against Context env at runtime.

**Files**:
- `app/dsl.py` (new) — `parse_condition(s) -> Callable[[dict], bool]` + `DSLError` exception
- `tests/unit/test_dsl.py` (new) — every whitelisted node, every rejected node, every operator
- `tests/security/test_dsl_lockdown.py` (new) — fuzz/property tests asserting `__builtins__` lockdown holds

**Whitelist (any other AST node → `DSLError`)**:
- `BoolOp` (with `And` / `Or`)
- `UnaryOp` (with `Not`)
- `Compare` (with `Gt` / `Lt` / `GtE` / `LtE` / `Eq` / `NotEq`)
- `Name` (env-lookup only; no attribute access; no subscript)
- `Constant` (int / float / str / bool / None only — exclude bytes, complex)
- `Load` context

**Security**:
- Compiled via `compile(ast.fix_missing_locations(tree), "<rule>", "eval")`
- Evaluated via `eval(code, {"__builtins__": {}}, env)` (frozen no-builtins globals)
- env is a `MappingProxyType` over the Context dict (read-only)
- Rule loader (1D.8) asserts every `Name` token in rules.yaml resolves to a known Context field at startup (fail-fast)

**Validation**:
- `pytest tests/unit/test_dsl.py -v` — passes
- `pytest tests/security/test_dsl_lockdown.py -v` — every escape attempt blocked
- Fuzz tests: random AST nodes; only whitelisted forms compile
- Specific attacks blocked: `__class__`, `__bases__`, `().__class__.__mro__`, `getattr`, `import`, `open`, `eval`, `exec`, attribute access (`x.y`), subscript (`x[0]`), function call (`f()`)

**Risk**: **Critical** (security boundary). Mitigation: explicit whitelist (deny by default); security-auditor reviews exhaustively; tests cover every Python AST node type's behavior.

**Reversibility**: Easy.

**Pre-commit verification**: All security tests pass; security-auditor explicit sign-off on whitelist completeness.

**Observability**: structured log per rule load (rule name, condition, status); fail-fast on parse error at startup (no runtime DSL errors).

**Test changes**: New unit + security test files.

**Rollback plan**: `git revert`.

**Declared breaks**:
- Scope: DSL parser defined but no rules.yaml loader yet.
- Resolved in: 1D.8.

### 1D.7 — `app/scoring.py` (Layer 1 + Layer 3)

**Theme**: Hard-block short-circuit + signal-layer noisy-OR. Layer 2 (account-prior) is NOT in this commit.

**Files**:
- `app/scoring.py` (new) — `score(ctx, rules) -> ScoringResult`
- `app/rules.py` (new) — `class Rule`, `RuleSet`, `load_rules(yaml_path, dsl) -> RuleSet`
- `tests/unit/test_scoring.py` (new) — hard-block short-circuit; noisy-OR with various weight combos

**Specifics**:
- Layer 1: iterate rules with `action: BLOCK`; first fire returns `(decision=BLOCK, score=1.0, classification=RED, triggered=[rule.name], factors=[rule])`
- Layer 3: collect weights of all firing non-BLOCK rules; compute `signal_score = 1 - prod(1 - w_i)`
- Final score: `signal_score` (no Layer 2 yet — declared break)
- Thresholds: `allow_max=0.60`, `block_min=0.80` (from rules.yaml; no Pydantic default drift)
- Risk-level bands: `<0.30` LOW, `<0.60` MEDIUM, `<0.80` HIGH, else CRITICAL
- No trust-override (constraint #14)

**Validation**:
- `pytest tests/unit/test_scoring.py -v`
- Test matrix:
  - 0 rules fire → ALLOW 0.0
  - 1 rule fires at weight 0.5 → ALLOW 0.5
  - 2 rules fire at weight 0.5 each → 0.75 (REVIEW)
  - 3 rules fire at weight 0.5 each → 0.875 (BLOCK)
  - BLOCK rule fires → immediate BLOCK 1.0, no further evaluation
  - BLOCK rule + score-only rules fire → BLOCK 1.0 (short-circuit wins)

**Risk**: **High** (scoring engine is the decision authority).

**Reversibility**: Easy.

**Pre-commit verification**: All tests; matrix coverage.

**Observability**: structured log per score (tenant_id, customer_id, score, decision, triggered rules); counters `score.allow` / `score.review` / `score.block` / `score.hard_block_short_circuit`.

**Test changes**: New unit test.

**Rollback plan**: `git revert`.

**Declared breaks**:
- Scope: `app/scoring.py` implements Layer 1 + Layer 3 only. Layer 2 (account-prior + trust_contribution) is absent; final score = signal_score (not `noisyOR(account_prior, signal_score)`).
- Resolved in: Phase 2 (Layer 2 added; final = noisyOR(account_prior, signal_score)).
- Scope: `scoring.py` not yet called from booking endpoint.
- Resolved in: 1D.8.

### 1D.8 — Initial signals + rules.yaml + booking endpoint wire-up + case-2 integration test

**Theme**: 10 signal modules; 12-15 rules in rules.yaml; booking endpoint wired through full pipeline (context → score → decision); case-2 fixture replay integration test.

**Files**:
- `app/signals/__init__.py` (new — exports signal runner)
- `app/signals/ip_class_deviation.py` (new)
- `app/signals/velocity_burst.py` (new)
- `app/signals/dormancy_then_activity.py` (new)
- `app/signals/new_route.py` (new)
- `app/signals/value_outlier.py` (new)
- `app/signals/disposable_email.py` (new)
- `app/signals/dummy_phone.py` (new)
- `app/signals/unfamiliar_origin.py` (new)
- `app/signals/unfamiliar_ip.py` (new)
- `app/signals/ip_geolocation_country.py` (new)
- `app/rules.yaml` (new — 12-15 rules wiring the 10 signals + 2 hard-block rules from freight_risk: `blacklisted_ip` (FireHOL Level 1), `ip2p_threat_botnet_block`)
- `app/api/booking.py` (extended — wires build_context → score → single-transaction persist (INSERT shipments + INSERT decisions + baseline.add_observation/save + UPDATE customers.total_shipments and last_seen, all in one txn with `SELECT FOR UPDATE` on customer_baselines); replaces stub HMAC with real `signals.hmac_hex(value, secret)`. Persistence failure → 500; retry-safe via `(tenant_id, request_id)` idempotency.)
- `tests/fixtures/payloads/case_2_minimal.json` (new — minimal case-2 fixture: customer with cloud-IP-only baseline; new booking from residential proxy IP in burst pattern)
- `tests/integration/test_case_2.py` (new)
- `tests/integration/test_pipeline_e2e.py` (new — end-to-end full pipeline)

**Initial rule set (12-15 rules)**:

Hard-block (2):
1. `blacklisted_ip` — `ip_in_level1` → BLOCK
2. `ip2p_threat_botnet_block` — `ip2p_threat_botnet` → BLOCK

Score-only (10-13):
3. `threat_intel_level2` — `ip_in_level2`, weight 0.4
4. `tor_exit` — `is_tor`, weight 0.5
5. `vpn_high_value` — `is_vpn AND shipment_value > 1000`, weight 0.3
6. `customer_daily_volume_spike` — `velocity_user_daily > customer_daily_mean * 5 AND velocity_user_daily > 20`, weight 0.4 (maturity_sensitive)
7. `ip_velocity_high_ui` — `is_platform_booking AND velocity_ip_hourly > 10`, weight 0.3 (maturity_sensitive)
8. `ip_velocity_high_api` — `is_api_booking AND velocity_ip_hourly > 100`, weight 0.3 (maturity_sensitive)
9. `dummy_email_disposable_domain` — `is_email_disposable`, weight 0.3
10. `dummy_phone_pattern` — `is_phone_dummy_pattern`, weight 0.2
11. `unknown_origin_address` — `not origin_address_familiar AND customer_observations >= 10`, weight 0.25 (maturity_sensitive)
12. `unknown_destination_address` — `not destination_address_familiar AND customer_observations >= 10`, weight 0.2 (maturity_sensitive)
13. `ip_fully_new_for_customer` — `ip_fully_new AND customer_observations >= 10`, weight 0.3 (maturity_sensitive)
14. `unfamiliar_ip_country_for_origin` — `not origin_ip_country_familiar AND customer_observations >= 10`, weight 0.25 (maturity_sensitive)
15. `value_novelty_compound` — `value_zscore > 3 AND new_route AND customer_observations >= 10`, weight 0.4 (maturity_sensitive)

**Note**: Phase 1 rules deliberately exclude every trust-conditioned rule (`very_low_trust`, `low_trust_*`, `mid_trust_*`, `threat_score_moderate`, `flags_with_value`) and every account-prior-dependent rule. Those land in Phase 2 with Layer 2.

**Validation**:
- `ruff check app/ tests/`
- `mypy app/`
- `pytest tests/ -v --asyncio-mode=auto` — full suite green
- `pytest tests/integration/test_case_2.py -v` — case-2 fixture lifecycle:
  - Seed customer with baseline of 100 cloud-IP bookings over 60 days
  - Send first residential-proxy booking → REVIEW (signal: ip_class_deviation via `ip_fully_new_for_customer` + `unfamiliar_ip_country_for_origin`)
  - Send 10 more residential-proxy bookings in 1 hour → BLOCK (signal: `ip_velocity_high_api` + cumulative ip_fully_new bookings)
- `pytest tests/integration/test_pipeline_e2e.py -v` — full pipeline:
  - Build context loads baseline + enrichment + velocity counts in parallel
  - Score runs against context
  - Decision persists to DB
  - Response returned with rule trail
- Latency benchmark: hot-path booking p95 <200ms; load: 50 concurrent requests against single asyncpg pool

**Risk**: **Critical** (case-2 detection is the v1 quality bar).

**Reversibility**: Easy (single commit).

**Pre-commit verification**: All tests; case-2 detection confirmed; latency under threshold.

**Observability**: structured log per evaluation (tenant_id, customer_id, request_id, latency, triggered rules, decision); counters `eval.allow` / `eval.review` / `eval.block`, histogram `eval.latency_ms`, gauge `eval.rules_fired_count`.

**Test changes**: 10 new signal modules with adjacent unit tests + 2 new integration tests (case_2 + pipeline_e2e).

**Rollback plan**: `git revert`.

**Declared breaks**:
- Scope: Rule `value_zscore` references a Context field computed from `value_n`/`value_mean`/`value_m2`; if Welford updates have not run yet (effective_observations < 10), the rule's `customer_observations >= 10` guard prevents firing on cold customers.
- Resolved in: post-Phase 1 — natural saturation as customers accumulate observations.
- Scope: case-1 fixture (dashboard ATO ~50 shipments) NOT in Phase 1 — case-1 fixture lands in Phase 2 (more comprehensive test bar) and is replayed in Phase 6.
- Resolved in: Phase 2 (case-1 fixture + replay).

**Operator checkpoint — end of Batch 1D**: produce `REPORT_PHASE_1.md`; wait for approval before Phase 2.

---

## REPORT_PHASE_1.md shape

To be produced at end of Batch 1D. Per FreightSentry's REFACTOR_REPORT_B* convention:

1. **Aggregate stats**: total commits, total LOC (production + test), total review cycles, total iterations triggered
2. **Per-batch disposition**: 1A / 1B / 1C / 1D — date completed, commits in batch, validation outcome
3. **Plan deviations**: any commit that diverged from the planned scope, why, what landed instead
4. **Reviewer-caught corrections**: enumerate every reviewer finding that required iteration; file:line refs; what was fixed
5. **Explicitly deferred**: what was planned for Phase 1 but moved to Phase 2 (with rationale)
6. **Quality measurements**: case-2 detection result, latency p95/p99 against test fixtures, test coverage percentage
7. **Open items for Phase 2**: any work that surfaced during Phase 1 that Phase 2 should absorb

---

*End of PLAN_PHASE_1.md*
