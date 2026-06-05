# Phase 5 RLS + security audit (delta over Phase 4)

Supersedes prior Phase 3 + Phase 4 RLS audits (now archived in
`docs/history.md`) for the Phase 5 ship state (commit ranges 5A.1
through 5D.4 inclusive). Captures the security-relevant deltas of
Phase 5 and the operational invariants that Phase 6 deploy must
preserve.

## Phase 5 deltas at a glance

| Area | Pre-5 state | Post-5 state |
|---|---|---|
| Runtime DB role | postgres superuser (BYPASSRLS) | `riskd_app_login` (LOGIN INHERIT; no BYPASSRLS) |
| Migration DB role | DATABASE_URL (same as runtime) | `ALEMBIC_DATABASE_URL` env split |
| RLS enforcement | dormant (superuser bypass) | active on 7 tables |
| api_tokens / app_users RLS | enabled but never enforced | DISABLED (migration 0009) |
| api_tokens.last_used_at | column existed, never written | written on every auth success |
| decisions UNIQUE | `(tenant_id, request_id)` | `(tenant_id, request_type, request_id)` |
| Container runtime user | root | UID 1000 `app` (non-root) |
| Dependency lockfile | none | `uv.lock` at repo root |
| Observability | structlog JSON to stdout | + CloudWatch EMF on `metric=True` events |
| Tenant config DB load | per request | 60s in-process TTL cache |

## RLS role transition (5D.1 + 5D.2)

### Mechanism
Migration 0008 created `riskd_app_login WITH LOGIN INHERIT` and
`GRANT riskd_app TO riskd_app_login`. The legacy `riskd_app` role
remains NOLOGIN (defense in depth — no one connects as it directly).
5D.2 switched the runtime `DATABASE_URL` to point at
`riskd_app_login`; alembic now connects via the separate
`ALEMBIC_DATABASE_URL` env var (superuser DSN; required to run
schema-changing DDL that the de-privileged runtime role can't issue).

### Active RLS coverage
RLS now actively enforces on these 7 tenant-scoped tables:
- enterprises
- customers
- users
- shipments
- decisions
- feedback
- customer_baselines

Policy form (unchanged from 0001):
```sql
CREATE POLICY tenant_isolation ON <table>
    USING (tenant_id = current_setting('app.tenant_id')::int);
```

Postgres applies `USING` as the default `WITH CHECK` for INSERTs,
so write-side enforcement is also active.

### Validation
- 3C.3 canary (`tests/integration/test_rls_enforcement_under_riskd_app.py`)
  refactored to connect as `riskd_app_login` directly. Tests cover
  the 7 tables for read-side filter + an unset-tenant-context block
  + the admin endpoint dual-predicate pattern.
- Full integration suite (918 tests) runs under `riskd_app_login`.
- 5D.4 load test ran 23,720 requests over two runs with 0 HTTP 500s
  and no RLS-policy violations.

### Failure mode
A request handler that forgets to call `set_tenant_id` before
touching a tenant-scoped table will SELECT 0 rows (RLS hides
everything) and INSERTs fail with `InsufficientPrivilegeError`
(RLS WITH CHECK rejects). Both fail closed.

## Auth-table RLS drop (migration 0009)

### Rationale
`require_api_token` runs `SELECT FROM api_tokens WHERE token_hash = $1`
**before** the endpoint handler issues `set_tenant_id` — because the
tenant_id is the *result* of the auth lookup, not the *scope*. With
the pool-init sentinel `app.tenant_id='0'` (sentinel set by
`_pool_setup` in `app/db.py`) and the policy
`tenant_id = current_setting('app.tenant_id')::int`, the api_tokens
RLS policy would filter out every row and auth would fail.

The auth.py docstring (lines 15-18) anticipated this chicken-and-egg
and called it out as a Phase 5 follow-up. Migration 0009
(`alembic/versions/0009_drop_rls_on_auth_tables.py`) resolves it by
disabling RLS on:
- `api_tokens` (used by `require_api_token`)
- `app_users` (Phase 4 admin auth table; not yet active in v1
  endpoints but preemptively cleaned up so future app_users-driven
  admin auth doesn't hit the same chicken-and-egg)

### Why this is acceptable security-wise
Both tables are auth-lookup tables. The secret IS the credential:
- `api_tokens.token_hash` is a SHA-256 over `secrets.token_urlsafe(24)`
  per `app/auth.py:_hash_token` + `scripts/tenant_onboard.py`. Knowing
  the (plaintext) token is itself the authorisation. Once a row is
  returned by `WHERE token_hash = $1`, the `tenant_id` column on that
  row IS the authorised tenant.
- `app_users.password_hash` (4D shape) is the equivalent for admin
  email/password login. Same argument: presenting the password is the
  authorisation.

Under the pre-5D superuser-bypass model, RLS on these tables was
already inert — the superuser bypassed policy evaluation. So
migration 0009 is **not a regression vs the deployed pre-5D state**.

### Defense-in-depth gap created by 0009
With api_tokens and app_users no longer RLS-enabled, **any future bug
that exposes a non-WHERE-token_hash SELECT on these tables (e.g., a
debug endpoint, mis-scoped log dump, or an ORM query in a hypothetical
refactor) would return cross-tenant token-hash rows**. The exposure is
limited (hashes only; no plaintext tokens; no plaintext passwords),
but it removes one defense-in-depth layer.

**Phase 6 reviewer responsibility**: any new code path that reads
`api_tokens` or `app_users` outside the auth path (`require_api_token`
in `app/auth.py`, the admin-auth path in `app/api/admin.py`) is a
high-severity review finding. Recommend adding a `# noqa: 5D5-auth-table`
comment marker at the auth-path sites and a grep-based CI check that
forbids new reads of these tables elsewhere.

## Pool-init sentinel + transaction-scoped per-request override

### Architecture
`app/db.py::_pool_setup` runs once per new pooled connection and sets
`app.tenant_id='0'` at session scope (`is_local=false`). Sentinel '0'
is safe — no tenant has id 0, so RLS-protected SELECTs return empty
and WITH CHECK INSERTs fail.

`app/db.py::set_tenant_id` runs per request inside the request
transaction with `is_local=true`. The transaction-scoped value
overrides the session sentinel for the duration of the request; on
commit/rollback the value reverts to '0'.

### Why this matters
Two desirable properties:
1. **A connection returned to the pool has its tenant context cleared**
   (the session sentinel reverts after the request's transaction).
   No cross-request bleed.
2. **A request handler that forgets to call `set_tenant_id` operates
   under the sentinel** — RLS hides all data; INSERTs fail. Fail
   closed.

Production endpoints (booking, modification, feedback, admin x2) all
call `set_tenant_id` immediately after acquiring their connection.
Verified via grep + the integration test suite.

## `api_tokens.last_used_at` writer (5A.5)

- New column writer on every auth success path in
  `app/auth.py::require_api_token` (in addition to the SELECT that
  resolves the token to a tenant).
- Synchronous UPDATE inside the auth-dependency transaction; auto-
  commits independently of the downstream request handler's
  transaction outcome.
- ~1ms additional per-request latency.
- Supporting index `ix_api_tokens_tenant_last_used` on
  `(tenant_id, last_used_at DESC NULLS LAST)` for future stale-token
  queries.

**No security exposure**: the column tracks when a token was last
used; no plaintext or hash material is exposed.

## `ux_decisions_tenant_request` UNIQUE widening (5A.7)

- Constraint widened from `(tenant_id, request_id)` to
  `(tenant_id, request_type, request_id)`.
- Booking and modification with the same `request_id` legitimately
  coexist (they live in separate request_type namespaces).
- The `try/except UniqueViolation → 409` in booking.py and
  modification.py stays as defense-in-depth for the concurrent same-
  type duplicate-INSERT race.

**No security exposure**: the widening eliminates a spurious 409
attack-surface (cross-type request_id collision used to produce a
non-actionable 409); the new constraint is at least as restrictive
as the old one within each request_type namespace.

## Non-root container (5A.4)

- Dockerfile runs the app process as UID 1000 `app` user.
- `build-essential` is included in the runtime image so the pytricia
  sdist-only dep compiles. **Phase 6 hard prerequisite**: multi-stage
  build that strips the build-tools from the runtime image. Build-
  tools in runtime are a known hardening regression vs production
  posture; logged in `.claude/BUGS.md`.

## Tenant config cache (5B) — security-relevant staleness

- 60s in-process TTL cache per-tenant.
- A config tightening (e.g., removing a currency from
  `allowed_currencies`) takes up to 60s to propagate across all
  worker processes for the same tenant. Per-tenant only; no
  cross-tenant impact.
- Operator-initiated narrowing has a 60s effective-window;
  surfaced to operators via the `scripts/tenant_onboard.py` output.
- Acceptable per the v1 contract; documented in `.ai/decisions.md`.

**No security exposure**: the cache only stores TenantConfig values
(no PII, no secrets, no credentials).

## Observability (5C) — security-relevant constraints

- EMF formatter exclusively reads dimension keys from a fixed
  `MetricSpec` table. **`request_id` is structurally incapable of
  being promoted to a CloudWatch dimension** — high-cardinality
  guard enforced at the processor level. Verified end-to-end by
  `test_request_id_never_appears_in_dimensions_at_runtime`.
- Metric event payloads contain no PII or secret material.
  `token_hash_prefix` (8 hex chars of SHA-256) appears as a regular
  log field but is NOT promoted to dimensions or metrics.
- All metric event names are hardcoded string literals at call sites;
  no user-controlled values reach the EMF namespace.

## Local-dev password (`riskd_app_login_dev`)

Hardcoded in migration 0008 and `.env.example` for docker-compose
convenience. **Phase 6 deployment MUST rotate** from AWS Secrets
Manager before exposing the service. Documented in:
- `alembic/versions/0008_riskd_app_login.py` module docstring + inline
  SQL comment + `COMMENT ON ROLE` (visible via `\du+`)
- `.env.example`
- `docker-compose.yml`
- This audit doc

Recommend a Phase 6 deploy-script guard that refuses to start the app
if the runtime DSN password equals the migration-baked default.

## ALEMBIC_DATABASE_URL split

`alembic/env.py` prefers `ALEMBIC_DATABASE_URL` over `DATABASE_URL`.
Required because `riskd_app_login` lacks privileges to CREATE/ALTER
DDL. Local docker-compose now sets both env vars. Production Phase 6
must source `ALEMBIC_DATABASE_URL` from Secrets Manager.

**Operational gotcha**: if `ALEMBIC_DATABASE_URL` is unset, alembic
silently falls back to `DATABASE_URL` and fails opaquely at the first
DDL. Recommend a Phase 6 hardening pass: refuse the fallback for any
DSN whose user component doesn't look like a superuser.

## Phase 6 audit items (carry-forward)

Items deferred from Phase 5 to the Phase 6 audit:

1. Multi-stage Dockerfile that strips `build-essential` from the
   runtime image.
2. Rotate `riskd_app_login` password from Secrets Manager; refuse to
   start the app with the dev password.
3. Production observability: wire the CloudWatch Logs agent to ECS
   Fargate stdout. EMF formatter (5C) is already in place; only the
   transport remains.
4. RLS coverage check: a CI grep that forbids new reads of
   `api_tokens` / `app_users` outside the auth-path code.
5. Real enrichment data (MaxMind, IP2Proxy, FireHOL, cloud CIDRs)
   shipped to staging; rerun load test against staging-realistic
   latencies.
6. Connection-pool sizing for production traffic — the load test
   showed pool max=10 saturates at 100 concurrent clients. ECS task
   def needs vCPU/pool tuning.
7. `.env` host-vs-container `DATABASE_URL` split (BUGS.md). Phase 5D.2
   could not fully resolve the host-machine `.env` localhost form;
   docker-compose runs require explicit env override. Phase 6 deploy
   sidesteps this (production uses Secrets Manager, not `.env`).
8. SECURITY DEFINER wrap on `require_api_token` lookup as an
   alternative to 0009's RLS-drop on api_tokens, if a stronger
   defense-in-depth posture is wanted. Not blocking; defer to
   threat-model review.

## What this audit DOES NOT cover

- Production deploy infrastructure (ECS Fargate task def, RDS
  provisioning, ALB, Secrets Manager wire-up).
- Real enrichment data ingestion + freshness.
- Case-1 / case-2 production replay against real data.
- Multi-region / multi-AZ failure modes.

All deferred to the Phase 6 audit.

## Sign-off

Phase 5 ships with:
- Active RLS enforcement on all 7 tenant-scoped business-data tables.
- Auth tables (`api_tokens`, `app_users`) RLS disabled with documented
  rationale (token_hash IS the credential; RLS was always vestigial
  under the pre-5D superuser bypass).
- Pool-init sentinel + transaction-scoped per-request override
  composing correctly with RLS.
- Container runs as UID 1000 non-root.
- 918 tests pass under `riskd_app_login`. Case-1 + case-2 regression
  pass.
- Load test 0 errors / 0 RLS-policy violations across 23,720 requests
  over two runs.

Phase 5 security posture is **at parity with Phase 4 ship state for
the tables that retained RLS, and de-privileged at the runtime role
level**. The trade-off in 0009 (api_tokens / app_users RLS-drop) is
explicitly accepted; the defense-in-depth gap is bounded and the
Phase 6 audit will pick up the carry-forward items above.
