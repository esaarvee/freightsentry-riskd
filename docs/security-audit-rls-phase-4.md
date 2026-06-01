# Phase 4 Multi-tenant Scoping Audit — RLS + Authorization Layer

> Date: 2026-06-01
> Phase: 4D wrap
> Predecessor: `docs/security-audit-rls-phase-3.md` (preserved as snapshot)
> Scope: Phase 4 additions only — admin endpoints + `require_admin_role` dependency

## Summary

Phase 4 adds 2 read-only admin endpoints under `/api/v1/admin/`:

| Endpoint | Auth required | Tenant-scope | RLS-eligible |
|---|---|---|---|
| `GET /admin/decisions/{request_id}` | admin role | YES (auth.tenant_id) | YES (decisions + shipments) |
| `GET /admin/customers/{external_id}/baseline` | admin role | YES (auth.tenant_id) | YES (customers + customer_baselines) |

Both compose with `require_admin_role` (4D.1) which composes with `require_api_token`. Cross-tenant lookups return 404 (hides existence per security-by-default convention).

Phase 3 audit findings remain in effect — Phase 4 does NOT remove RLS policies, does NOT alter tenant-scoping discipline.

## Authorization model

| Role | Source | Phase introduced |
|---|---|---|
| `tenant` | `api_tokens.role` (default) | Phase 1 |
| `admin` | `api_tokens.role` | Phase 4D (first enforcement) |

`app_users.role` exists in Phase 1 schema but is NOT wired to the auth dependency in Phase 4. Phase 5+ may add `app_users` wiring if a multi-user admin model is needed (separate token vs user identity).

The auth dependency `require_admin_role` (`app/auth.py`):
1. Calls `require_api_token` → validates Bearer token, returns AuthContext.
2. Checks `auth.role == "admin"`. Returns 403 otherwise.
3. Returns the AuthContext unchanged (tenant_id preserved).

The `AUTH_ENABLED=false` local-dev carve-out yields `role="tenant"`, which FAILS the admin check. Admin endpoints under local dev require `AUTH_ENABLED=true`.

## Query inventory delta (Phase 3 → Phase 4)

Phase 3 inventory: 36 queries across 4 endpoints (booking, modification, feedback, health).

Phase 4 additions:

| Query # | File:Line | Query | Tenant-scope mechanism | Notes |
|---|---|---|---|---|
| 37 | `app/api/admin.py` (get_admin_decision) | `SELECT config, created_at, updated_at FROM tenants WHERE id = $1` (via `load_tenant_config`) | Explicit WHERE | tenants table not RLS-enabled (intentional) |
| 38 | `app/api/admin.py` (get_admin_decision) | `SELECT FROM decisions JOIN shipments WHERE d.tenant_id = $1 AND d.request_id = $2` | Explicit WHERE on outer + JOIN ON s.tenant_id = d.tenant_id | Dual filter; defense-in-depth above RLS |
| 39 | `app/api/admin.py` (get_admin_customer_baseline) | `SELECT config, created_at, updated_at FROM tenants WHERE id = $1` (via `load_tenant_config`) | Explicit WHERE | Same as #37 |
| 40 | `app/api/admin.py` (get_admin_customer_baseline) | `SELECT FROM customers WHERE tenant_id = $1 AND external_id = $2` | Explicit WHERE | RLS-eligible |
| 41 | `app/api/admin.py` (get_admin_customer_baseline) | `SELECT FROM customer_baselines WHERE tenant_id = $1 AND customer_id = $2` | Explicit WHERE | RLS-eligible |

Total: **41 queries across 6 endpoints**.

## Tenant-config load wiring (Phase 4A)

Phase 4A added `load_tenant_config(conn, tenant_id)` to all 3 pre-existing endpoints (booking, modification, feedback) and now both admin endpoints (4D.2 + 4D.3). The loader uses an explicit `WHERE id = $1` against the non-RLS `tenants` table (intentionally global per `0001_initial.py:36-37`).

Audit point: the tenant_id passed to `load_tenant_config` ALWAYS comes from `auth.tenant_id` (the authenticated AuthContext); NEVER from request payload. Verified across:
- `app/api/booking.py:54` — `await load_tenant_config(conn, auth.tenant_id)`
- `app/api/modification.py:60` — same
- `app/api/feedback.py:115` — same
- `app/api/admin.py:91` (get_admin_decision) — same
- `app/api/admin.py:178` (get_admin_customer_baseline) — same

No code path takes tenant_id from request input.

## Currency normalization (Phase 4B)

The 7 rule rewrites in `app/rules.yaml` consult `shipment_value_threshold_*` Context fields populated by `build_context` from `tenant_config.value_caps`. No new SQL added; no auth surface change. The request-time allowed-list check (4B.3) runs against `tenant_config.allowed_currencies` after `load_tenant_config` — same defensive ordering as admin endpoints.

## Cold-start enforcement (Phase 4C)

`score()` consults `tenant_config` for maturity constants. No new SQL added; no auth surface change. The grace mechanism reads `tenant_config.created_at` (loaded from `tenants.created_at` at row read time) — sourced from the auth-bound tenant config.

## Verification

Phase 3C.3 canary (`tests/integration/test_rls_enforcement_under_riskd_app.py`) is the structural readiness gate for Phase 5's `riskd_app_login` role transition.

Phase 4D.4 extends this canary with 3 admin endpoint scenarios:
1. `test_rls_admin_decisions_join_scoped_by_tenant` — decisions JOIN shipments respects RLS under riskd_app session.
2. `test_rls_admin_customer_lookup_scoped_by_tenant` — customers lookup respects RLS.
3. `test_rls_admin_baseline_lookup_scoped_by_tenant` — customer_baselines respects RLS.

These confirm admin endpoints work under the non-superuser RLS role and continue to scope by tenant_id.

Application-layer cross-tenant integration tests in `tests/integration/test_admin_endpoints.py` (4D.2 + 4D.3) prove that admin endpoints do not leak data between tenants via the FastAPI request path.

## PII handling

`get_admin_decision` returns origin/destination addresses as **city + country only** (not the full street address). The decision response itself carries triggered_rules + risk_factors (no PII).

`get_admin_customer_baseline` surfaces:
- `business_name` and `registered_address` — admin is authorized for their tenant's customer-supplied data.
- HMAC hex strings for `email_hmacs`, `phone_hmacs`, `rejected_email_hmacs`, `rejected_phone_hmacs` — already obfuscated; no PII visible.

No plaintext email or phone is surfaced by any admin endpoint.

## Stat-dict truncation

Customer baseline response truncates each stat-dict to top-10 by `n` desc with a `truncated: bool` + `total_count: int` per dict. Operationally interesting entries (high `n`) are surfaced first; the truncation prevents response size from blowing up on customers with thousands of distinct routes/IPs/etc.

Full-dict retrieval is deferred to Phase 5+ as a potential separate endpoint if needed (Phase 4 doesn't have a use case requiring it).

## Phase 5 carry-forward

- `ux_decisions_tenant_request` UNIQUE widening (BUGS.md entry from Phase 3 remains).
- `riskd_app_login` role transition becomes the production RLS activation. Phase 4D.4 extends the canary to admin endpoints; both can ship the transition without breaking admin reads.
- `app_users` table wiring (if Phase 5+ adds multi-user admin).
- Admin write endpoints (decision overrides, manual feedback, etc.) — v2+ per decisions.md.
- In-process tenant-config cache (60s TTL) — wraps the per-request `load_tenant_config` call.

## Conclusion

Phase 4 admin endpoints are tenant-scoped at the application layer (explicit WHERE/JOIN with `auth.tenant_id`) AND eligible for Phase 5 RLS enforcement (no queries depend on RLS bypass). The `require_admin_role` dependency adds authorization on top of authentication; the cross-tenant test matrix confirms enforcement.

**Zero queries with potentially missing scope identified in Phase 4.**

PII handling discipline preserved — only HMAC'd identifiers and city/country-level addresses surface in admin responses. Stat-dict truncation bounds response size without leaking aggregate information across tenants.
