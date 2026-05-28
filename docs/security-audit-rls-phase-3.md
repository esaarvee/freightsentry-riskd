# Multi-tenant scoping audit — Phase 3

**Audit date**: 2026-05-28
**Audited commit range**: post-3B execution (after migration 0004)
**Auditor**: Phase 3C.1, mechanical grep against current HEAD

## Executive summary

`freightsentry-riskd` has two layers of multi-tenant defense:

1. **App-layer `WHERE tenant_id = $N` filtering** — active today (the runtime app connects as the bootstrap postgres superuser, which bypasses RLS — per STATUS row 1B.2).
2. **PostgreSQL Row-Level Security policies** — structurally complete on every tenant-scoped table; activates at runtime via the Phase 5 role transition to a non-superuser.

This audit confirms both layers are structurally complete for all 9 tenant-scoped tables and identifies **zero queries with potentially missing scope**. Phase 5's role transition can rely on the policies as written; the application-layer filters remain as defense-in-depth.

## Endpoints in scope

| Endpoint | Method | File | Phase landed |
|---|---|---|---|
| `/health` | GET | `app/api/health.py` | Phase 1B |
| `/api/v1/shipments/booking/evaluate` | POST | `app/api/booking.py` | Phase 1C |
| `/api/v1/shipments/modification/evaluate` | POST | `app/api/modification.py` | Phase 3A.6 |
| `/api/v1/shipments/feedback` | POST | `app/api/feedback.py` | Phase 3B.3 |

## Query inventory

All asyncpg connection-method call sites in `app/` cataloged below. Categorization:

- **Explicit tenant filter** — `WHERE tenant_id = $N` present in the query (safe under both app-layer filtering and RLS)
- **Auth machinery** — pre-tenant-context lookup; intentionally scoped by token hash, returns tenant context to caller
- **RLS machinery** — `set_config('app.tenant_id', ...)` per-transaction
- **Intentionally global** — table has no tenant_id (e.g., `ip_enrichment` shared cache)
- **Health check** — no table; `SELECT 1`
- **POTENTIALLY MISSING SCOPE** — warning category

### Explicit tenant filter (safe under both layers)

| File | Line | Method | Table(s) | Query shape |
|---|---|---|---|---|
| `app/baseline.py` | 215 | `fetchrow` | customer_baselines | `WHERE tenant_id = $1 AND customer_id = $2` |
| `app/baseline.py` | 227 | `execute` | customer_baselines | `INSERT ... (tenant_id, customer_id, ...)` |
| `app/baseline.py` | 236 | `fetchrow` | customer_baselines | `WHERE tenant_id = $1 AND customer_id = $2 FOR UPDATE` |
| `app/baseline.py` | 490 | `execute` | customer_baselines | `INSERT ... ON CONFLICT (tenant_id, customer_id) DO UPDATE` |
| `app/api/booking.py` | 59 | `fetchrow` | decisions | `WHERE tenant_id = $1 AND request_id = $2 AND request_type = 'booking'` (idempotency check; Never-Skip §request_type symmetry) |
| `app/api/booking.py` | 102 | `fetchrow` | customers | `WHERE id = $1 AND tenant_id = $2` |
| `app/api/booking.py` | 182 | `fetchval` | shipments | `INSERT INTO shipments (tenant_id, ..., email_hmac, phone_hmac, ...) RETURNING id` |
| `app/api/booking.py` | 225 | `execute` | decisions | `INSERT INTO decisions (tenant_id, ..., request_type='booking') ...` (try/except UniqueViolation → 409) |
| `app/api/booking.py` | 254 | `execute` | customers | `UPDATE customers SET last_seen = now(), total_shipments = total_shipments + 1 WHERE id = $1 AND tenant_id = $2` |
| `app/api/modification.py` | 62 | `fetchrow` | decisions | `WHERE tenant_id = $1 AND request_id = $2 AND request_type = 'modification'` (idempotency check) |
| `app/api/modification.py` | 95 | `fetchrow` | decisions JOIN shipments JOIN customers JOIN users | prior resolution; tenant_id explicit on every JOIN leg + outer WHERE |
| `app/api/modification.py` | 143 | `fetchrow` | customers | `WHERE id = $1 AND tenant_id = $2` |
| `app/api/modification.py` | 196 | `execute` | decisions | `INSERT INTO decisions (tenant_id, ..., request_type='modification') ...` (try/except UniqueViolation → 409) |
| `app/api/feedback.py` | 111 | `fetchrow` | feedback | `WHERE tenant_id = $1 AND request_id = $2` (tier-1 idempotency) |
| `app/api/feedback.py` | 137 | `fetchrow` | decisions JOIN shipments | target resolution; tenant_id on both JOIN legs + outer WHERE |
| `app/api/feedback.py` | 166 | `fetchrow` | feedback | `WHERE tenant_id = $1 AND target_request_id = $2 ORDER BY feedback_ts DESC LIMIT 1` (tier-2 pre-lock) |
| `app/api/feedback.py` | 224 | `fetchrow` | feedback | `WHERE tenant_id = $1 AND target_request_id = $2 ORDER BY feedback_ts DESC LIMIT 1` (tier-2 post-lock; race fix in 3B.7) |
| `app/api/feedback.py` | 273 | `execute` | customers | `UPDATE customers SET flagged_count = flagged_count + $1, fraud_confirmed_count = fraud_confirmed_count + $2 WHERE id = $3 AND tenant_id = $4` |
| `app/api/feedback.py` | 366 | `execute` | feedback | `INSERT INTO feedback (tenant_id, request_id, target_request_id, label, feedback_ts, note, operator_id) ...` |
| `app/services/entity_upsert.py` | 32 | `fetchrow` | enterprises | `INSERT ... ON CONFLICT (tenant_id, external_id) DO UPDATE RETURNING id` |
| `app/services/entity_upsert.py` | 68 | `fetchrow` | customers | `INSERT ... ON CONFLICT (tenant_id, external_id) DO UPDATE RETURNING id` |
| `app/services/entity_upsert.py` | 108 | `fetchrow` | users | `INSERT ... ON CONFLICT (tenant_id, customer_id, external_id) DO UPDATE RETURNING id` |
| `app/velocity.py` | 18 | `fetchval` | shipments | `WHERE tenant_id = $1 AND customer_id = $2 AND booking_ts > now() - interval '1 hour'` |
| `app/velocity.py` | 31 | `fetchval` | shipments | `WHERE tenant_id = $1 AND customer_id = $2 AND booking_ts > now() - interval '24 hours'` |
| `app/velocity.py` | 44 | `fetchval` | shipments | `WHERE tenant_id = $1 AND customer_id = $2 AND booking_ts > now() - interval '30 days'` |
| `app/velocity.py` | 57 | `fetchval` | shipments | `WHERE tenant_id = $1 AND source_ip = $2::inet AND booking_ts > now() - interval '1 hour'` |
| `app/velocity.py` | 70 | `fetchval` | shipments | `WHERE tenant_id = $1 AND source_ip = $2::inet AND booking_ts > now() - interval '24 hours'` |
| `app/velocity.py` | 92 | `fetchval` | shipments | `SELECT COUNT(DISTINCT source_ip) ... WHERE tenant_id = $1 AND customer_id = $2 AND booking_ts > now() - interval '30 days'` |
| `app/velocity.py` | 123 | `fetchval` | decisions JOIN shipments | modification_velocity_1h; **dual** `d.tenant_id = $1 AND s.tenant_id = $1` per .ai/conventions.md (Phase 3A.5 cycle-2 fix) |
| `app/velocity.py` | 148 | `fetchval` | decisions JOIN shipments | modification_velocity_24h; same dual filter |
| `app/velocity.py` | 178 | `fetchval` | shipments | `SELECT COUNT(DISTINCT customer_id) FROM shipments WHERE tenant_id = $1 AND destination_hmac = $2 AND booking_ts > now() - interval '30 days'` (recipient overlap, security-load-bearing per Phase 2B.6) |

### Auth machinery (cross-tenant by design)

| File | Line | Method | Table | Rationale |
|---|---|---|---|---|
| `app/auth.py` | 75 | `fetchrow` | api_tokens | `WHERE token_hash = $1` — token lookup is identity-bearing and returns the tenant_id to the caller. Pre-resolution, the request has no tenant context. Acceptable per .ai/decisions.md authentication model. |

### RLS machinery

| File | Line | Method | Purpose |
|---|---|---|---|
| `app/db.py` | 67 | `execute` | `SELECT set_config('app.tenant_id', $1, true)` — per-transaction tenant context for the RLS policies to evaluate against. is_local=true scopes the setting to the current transaction. |

### Intentionally global (no RLS by design)

| File | Line | Method | Table | Rationale |
|---|---|---|---|---|
| `app/enrich.py` | 189 | `fetchrow` | ip_enrichment | Shared IP enrichment cache keyed by IP only; no tenant scope per `alembic/versions/0001_initial.py:241-242` table comment ("Intentionally global (no RLS): IP enrichment is shared across tenants") |
| `app/enrich.py` | 285 | `execute` | ip_enrichment | Cache upsert; same rationale |

### Health check

| File | Line | Method | Table |
|---|---|---|---|
| `app/api/health.py` | 35 | `fetchval` | (none — `SELECT 1`) |

### Potentially missing scope

**None.** All 36 asyncpg call sites fall in one of the safe categories above.

## RLS policy coverage

Tenant-scoped tables — every one has both `ENABLE ROW LEVEL SECURITY` and a `tenant_isolation` policy.

| Table | tenant_id | ENABLE RLS | tenant_isolation policy | Notes |
|---|---|---|---|---|
| enterprises | ✓ | `0001_initial.py:291` | `0001_initial.py:301-302` | |
| customers | ✓ | `0001_initial.py:292` | `0001_initial.py:303-304` | |
| users | ✓ | `0001_initial.py:293` | `0001_initial.py:305-306` | |
| shipments | ✓ | `0001_initial.py:294` | `0001_initial.py:307-308` | + email_hmac/phone_hmac in 0004 |
| decisions | ✓ | `0001_initial.py:295` | `0001_initial.py:309-310` | + request_type in 0003 |
| feedback (Phase 3B shape) | ✓ | `0004_feedback_phase3_shape.py:70` | `0004_feedback_phase3_shape.py:71-72` | Original at `0001_initial.py:296, 311-312`; reapplied after 3B.1 drop-and-recreate |
| customer_baselines | ✓ | `0001_initial.py:297` | `0001_initial.py:313-314` | |
| api_tokens | ✓ | `0001_initial.py:298` | `0001_initial.py:315-316` | |
| app_users | ✓ | `0001_initial.py:299` | `0001_initial.py:317-318` | |

### Intentionally global (no RLS)

| Table | Why no RLS |
|---|---|
| ip_enrichment | Shared cache keyed by IP, no tenant scope (per `0001_initial.py:241-242` table comment) |
| global_blocked_vectors | Phase 5+ stub for cross-tenant block sharing (per `0001_initial.py:283-284` table comment) |
| tenants | Parent table; no per-tenant filtering applicable |

All policies use the same shape:

```sql
CREATE POLICY tenant_isolation ON <table>
    USING (tenant_id = current_setting('app.tenant_id')::int);
```

## Runtime dormancy

The application connects to PostgreSQL as the bootstrap `postgres` superuser (per `.claude/STATUS.md` row 1B.2). PostgreSQL superuser BYPASSES RLS — policies are evaluated but always pass. Therefore:

- **Today**, app-layer `WHERE tenant_id = $N` is the ONLY active isolation. The 30+ explicit-filter queries above are correct and necessary; they are not redundant with a dormant RLS layer.
- **Phase 5** will create `riskd_app_login` (a `LOGIN INHERIT` role granted `riskd_app`) and switch the runtime `DATABASE_URL` to connect as that role. At that point RLS activates: a query that forgot `WHERE tenant_id` would be invisibly scoped by the policy.

Until Phase 5, RLS is **defense-in-depth structure** that is not yet structurally enforced at the connection layer.

## Tests proving each layer

### App-layer filtering (active today)

- `tests/integration/test_tenant_isolation.py` (Phase 2B.6) — proves the recipient overlap query filters by tenant_id.
- `tests/integration/test_tenant_isolation_comprehensive.py` (Phase 3C.2 — this batch) — extends to all 4 endpoints with a parametrized scenario table.

### RLS runtime enforcement (proven structurally in 3C.3)

- `tests/integration/test_rls_enforcement_under_riskd_app.py` (Phase 3C.3 — this batch) — connects as the `riskd_app` non-superuser role (granted LOGIN temporarily for the test), sets `app.tenant_id`, and asserts cross-tenant queries return only the in-scope rows. This is the canary test that catches RLS misconfiguration; it would fail if any of the 9 tables lost its policy or had a wrong expression.

## Recommendations for Phase 4 / 5

1. **Phase 4 (admin endpoints)**: any new admin endpoint MUST add to the query inventory and re-run the audit. The CLAUDE.md triage gate routes admin endpoints to the standard panel + db-reviewer.
2. **Phase 5 (RLS runtime)**: create `riskd_app_login`, switch DATABASE_URL, re-run 3C.3's test to confirm runtime enforcement. Per BUGS.md, also widen `ux_decisions_tenant_request` UNIQUE to include `request_type` so the request_id namespace is enforced separately for booking vs modification.
3. **Audit cadence**: re-run the grep at each phase boundary and refresh this doc.

## Appendix A — Repeatable audit command

```bash
grep -rnE 'conn\.(fetch|fetchrow|fetchval|execute|executemany|copy_)' app/
```

Categorize each match against the matrix above.

## Appendix B — Phase 3 deltas vs Phase 1 inventory

| Phase | Endpoints | App-layer queries | RLS-protected tables | Notes |
|---|---|---|---|---|
| Phase 1 end | 2 | ~16 | 9 | booking + health |
| Phase 3A end | 3 | ~26 | 9 | + modification + decisions.request_type + 2 velocity helpers |
| Phase 3B end | 4 | 36 | 9 | + feedback + shipments.email_hmac/phone_hmac + post-lock tier-2 re-read |

The 9 tenant-scoped tables are unchanged across Phase 3 — feedback is recreated in 0004 but the policy is reapplied. No new tenant-scoped tables introduced. All Phase 3 endpoints reuse the existing tenant_id machinery.
