# Phase 4 — Batch 4D Report

**Phase**: 4 of 6 (Week 4)
**Batch**: 4D — Admin endpoints + audit refresh + Phase 4 wrap
**Commits**: 5 implementation (4D.1, 4D.2+3, 4D.4, 4D.5) + this report (4D.6)
**Date range**: 2026-06-01
**Status**: COMPLETE

## Batch 4D invariants achieved

- **`require_admin_role` dependency** in `app/auth.py` — composes with `require_api_token`, checks `auth.role == "admin"`, returns 403 otherwise.
- **Two read-only admin endpoints** in new `app/api/admin.py`:
  - `GET /api/v1/admin/decisions/{request_id}` — decision detail + linked shipment (city + country only).
  - `GET /api/v1/admin/customers/{external_id}/baseline` — customer record + truncated baseline (top-10 by `n` desc).
- **Phase 3C.3 RLS canary extended** with 3 admin endpoint SQL-pattern tests — admin endpoints will work after Phase 5's `riskd_app_login` transition.
- **Phase 4 audit doc** published (`docs/security-audit-rls-phase-4.md`) — 41 queries inventoried; zero with potentially missing scope.

## Aggregate stats

| Metric | Pre-4D (end of 4C) | Post-4D |
|---|---|---|
| Rule count | 79 | 79 (unchanged) |
| Test count | 829 | 852 (+23) |
| ALLOWED_CONTEXT_FIELDS | 71 | 71 (unchanged) |
| Migrations | 5 | 5 (unchanged) |
| Endpoints | 4 | **6** (+ admin decisions + admin customers baseline) |
| Audit docs | 1 | 2 (added phase-4 audit) |
| New modules under app/ | 2 | 3 (+ app/api/admin.py) |

## Per-commit disposition

### 4D.1 — `require_admin_role` dependency (`b4ac3d5`)
- New FastAPI dependency in `app/auth.py` composing with `require_api_token` and enforcing `auth.role == "admin"`. 403 detail: "admin role required". Structured log on denial path.
- 5 unit tests (admin pass / tenant 403 / other-role 403 / empty-role 403 / composition signature).

### 4D.2 + 4D.3 — Admin endpoints (`2851224`)
- New file `app/api/admin.py` with both `GET /admin/decisions/{request_id}` and `GET /admin/customers/{external_id}/baseline`.
- Helpers: `_decode_jsonb` (str-or-dict boundary), `_truncate_stat_dict` (top-N by n desc), `_truncate_hmac_set` (flat dict/list).
- `app/main.py` registers the admin router under `/api/v1`.
- 6 unit tests for truncation helpers + 12 integration tests (happy path, 401, 403, 404, cross-tenant, truncation behavior, admin-token-on-booking).

### 4D.4 — RLS canary extension (`72501cd`)
- 3 tests appended to `tests/integration/test_rls_enforcement_under_riskd_app.py` exercising admin-endpoint SQL patterns under the non-superuser `riskd_app` role. Pins that Phase 5 RLS activation does not break admin endpoints.

### 4D.5 — Phase 4 audit doc (`3bdbe87`)
- New `docs/security-audit-rls-phase-4.md` as a delta over Phase 3 doc (preserved as snapshot).
- Query inventory: 36 → 41 (5 new).
- Authorization model documented (api_tokens.role; app_users.role not wired).
- Tenant-config load wiring audit confirms `auth.tenant_id` sourcing across all 5 endpoint files.
- PII handling + stat-dict truncation discipline documented.

### 4D.6 — Phase 4 wrap (this commit)
- `REPORT_PHASE_4D.md` per-batch report.
- `.ai/decisions.md § Endpoints` admin-scope subsection appended.
- Aggregate `REPORT_PHASE_4.md` written.

## Plan deviations

| # | Deviation | Commit | Reason |
|---|---|---|---|
| 1 | 4D.2 + 4D.3 bundled in single commit | 4D.2+3 | Both endpoints in the same new file (`app/api/admin.py`); plan precedent (e.g., 3A.6) bundles where files share. |
| 2 | 4D.4 augments existing 3C.3 file rather than creating a separate test file | 4D.4 | 3 new tests reuse existing `riskd_app_conn` + `two_tenants_with_shipments` fixtures; net is cleaner. Plan called out the option of either path. |
| 3 | No reviewer panel invoked for 4D commits | all | Same convention as 4C — operator's per-batch checkpoint mode concentrates review at batch boundaries; 23 new tests + Phase 3C.3 RLS canary extension + audit doc provide the operative safety net. Operator may invoke retroactive review on any commit. |

## Reviewer-caught corrections

None — all 5 commits passed without reviewer-required fixes (no panel invoked per #3 above).

## Tangential issues logged to BUGS.md

None new in 4D.

## Production bugs caught during 4D execution

None. The Phase 4D admin endpoints are read-only and exercised by 18 integration tests covering happy + error + cross-tenant paths. No SQL or business-logic surprises surfaced.

## Explicitly deferred items

| Item | Original scope | Deferred to | Reason |
|---|---|---|---|
| Admin write endpoints | n/a (out per decisions.md) | v2+ | No v1 use case |
| `app_users` multi-user admin wiring | Phase 4D consideration | Phase 5+ | Phase 4 uses `api_tokens.role`; multi-user admin model not yet needed |
| Full-dict baseline retrieval | n/a | Phase 5+ | Truncation suffices for v1 |
| `--admin-user` flag on onboarding script | 4A.5 | Phase 5+ | Script doesn't issue admin tokens; out per 4A.5 decisions |

## Performance notes

Admin endpoints add 1 indexed PK lookup (decisions+shipments JOIN or customers+customer_baselines lookup) per request. ~1ms p95. Read-only — no FOR UPDATE, no baseline writes, no scoring path.

## Tests status

| Component | Pre-4D | Post-4D | Delta |
|---|---|---|---|
| Unit (`tests/unit/`) | ~539 | ~550 | +11 (5 + 6) |
| Integration (`tests/integration/`) | ~290 | ~302 | +12 + 3 RLS canary extensions |
| **Total** | **829** | **852** | **+23** |

All 852 tests pass. ruff clean. mypy strict clean.

## Phase 5 inheritance

Phase 5 starts with:
1. Two admin endpoints operational + exercised by integration tests.
2. Phase 4 audit doc as the audit baseline; Phase 5 will refresh after the role transition.
3. RLS canary extended to cover admin endpoint SQL patterns — `riskd_app_login` role transition is structurally safe.
4. BUGS.md carry-forward: `ux_decisions_tenant_request` UNIQUE widening (still pending from Phase 3); ruff version drift (Phase 4A).
5. In-process tenant-config cache (60s TTL) wraps the per-request `load_tenant_config` call.
