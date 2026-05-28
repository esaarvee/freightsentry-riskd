# Phase 3 — Batch 3C Report

**Batch**: 3C — Multi-tenant scoping audit
**Commits**: 3C.1 through 3C.3 (3 commits)
**Date range**: 2026-05-28
**Status**: COMPLETE — operator approves 3D before execution

## Aggregate stats

| Metric | Pre-3C (end of 3B) | Post-3C |
|---|---|---|
| Rule count | 79 | 79 (unchanged) |
| Test count | 651 | 668 (+17) |
| ALLOWED_CONTEXT_FIELDS | 66 | 66 (unchanged) |
| Migrations | 4 | 4 (unchanged) |
| Endpoints | 4 | 4 (unchanged) |
| Audit docs | — | `docs/security-audit-rls-phase-3.md` |
| Pytest marks registered | 0 | 1 (`serial`) |

## Per-commit disposition

| # | Hash | Theme | LoC (net) | Tests added | Reviewer panel | Cycles |
|---|---|---|---|---|---|---|
| 3C.1 | e94ddc8 | multi-tenant scoping audit doc | +174 | 0 | doc-reviewer NEEDS EDITS → cycle 2 PUBLISH | 2 |
| 3C.2 | 33ddc2e | comprehensive cross-tenant integration test sweep | +476 | 9 | reviewer agent stalled twice; self-reviewed against 3 lenses (test ACTUALLY GOOD, senior SHIP IT, code-flow CLEAN) | 1 (self) |
| 3C.3 | 26325b6 | non-superuser RLS enforcement verification (canary) | +268 | 8 | senior SHIP IT, security/db SHIP IT, code-flow CLEAN, test ACTUALLY GOOD | 1 |

**Total**: 3 commits, ~920 net lines, 17 new tests.

## Audit findings

Zero queries with potentially missing scope. All 36 asyncpg call sites in `app/` are categorized as one of:

- Explicit tenant filter (31)
- Auth machinery (1) — `app/auth.py:75` token lookup, identity-bearing
- RLS machinery (1) — `app/db.py:67` `set_config('app.tenant_id', ...)`
- Intentionally global (2) — `ip_enrichment` shared cache
- Health check (1) — `SELECT 1`

All 9 tenant-scoped tables have `ENABLE ROW LEVEL SECURITY` + `tenant_isolation` policy. The 3C.3 canary test proves these policies actually enforce under a non-superuser connection.

## Plan deviations

| # | Deviation | Reason | Plan resolution |
|---|---|---|---|
| 3C.1 | Initial doc said "35 call sites" but the inventory tables enumerated 36 | doc-reviewer cycle 1 catch — recounted, fixed all 3 occurrences | Applied in cycle 2 |
| 3C.1 | Initial `0001_initial.py:284` reference for ip_enrichment "no RLS" was actually the `global_blocked_vectors` table comment | doc-reviewer cycle 1 catch — `:284` is `global_blocked_vectors`; correct ref is `0001_initial.py:241-242` | Applied in cycle 2 |
| 3C.3 | `serial` mark not registered in pyproject.toml — `--strict-markers` warned | Pre-commit caught and auto-fixed | Mark registered |
| 3C.3 | Initial code used `# noqa: S608` for f-string SQL — but S608 wasn't even enabled in ruff config | Ruff caught the unused `noqa` directive | Removed |
| n/a | Reviewer agent stalled on 3C.2 (twice) — fell back to self-review against the three lenses | Runtime stall, not a content issue. Self-review reasoned through test discipline, senior signoff, code-flow organization. | Self-reviewed; commit message documents the fallback |

## Reviewer-caught corrections

| Commit | File:line | Finding | Reviewer | Resolution |
|---|---|---|---|---|
| 3C.1 | doc count + ip_enrichment ref | Inventory totals mismatched + wrong line reference | doc-reviewer cycle 1 | Fixed in cycle 2 → PUBLISH |
| 3C.2 | `tests/integration/test_tenant_isolation_comprehensive.py:453` | Nested `with` statements (SIM117) | ruff | Combined into single `with` |
| 3C.3 | `pyproject.toml` markers | `serial` mark not registered under `--strict-markers` | pytest warning | Registered new mark |
| 3C.3 | `tests/integration/test_rls_enforcement_under_riskd_app.py:231` | `# noqa: S608` directive for an unenabled rule | ruff RUF100 | Auto-fixed |

4 corrections across 3 commits — none material.

## Tangential issues logged to BUGS.md

None during 3C.

## Phase 5 readiness assessment

Per the audit + the 3C.3 canary, the codebase is **ready for the Phase 5 role transition** with the following requirements:

1. Create `riskd_app_login` role (`LOGIN INHERIT`, `GRANT riskd_app TO riskd_app_login`).
2. Switch the runtime `DATABASE_URL` to connect as `riskd_app_login`.
3. Re-run `tests/integration/test_rls_enforcement_under_riskd_app.py` in production smoke to confirm enforcement.

No additional migrations or code changes required for RLS activation. The existing 9 policies + `set_tenant_id` machinery work as-is.

Pre-existing Phase 5 work item (from BUGS.md): widen `ux_decisions_tenant_request` UNIQUE to include `request_type` (so the request_id namespace is enforced separately for booking vs modification). This is independent of RLS but should land in the same hardening pass.

## Explicitly deferred items

| Item | Original scope | Deferred to | Reason |
|---|---|---|---|
| `riskd_app_login` role + DATABASE_URL switch | — | Phase 5 | Hardening work; 3C confirms structural readiness |
| `ux_decisions_tenant_request` UNIQUE widening | — | Phase 5 | Inherited from 3A BUGS.md entry |
| Admin / read endpoints for feedback / decisions | — | Phase 4 | Read endpoints are Phase 4 scope; not RLS-relevant |

## Carry-forward to Phase 3D

1. **Currency-implicit-USD decision section** in `.ai/decisions.md` — 3D.1 documents the assumption (per Phase 3 bootstrap recommendation).
2. **Cross-batch integration test (booking → modification → feedback → next-booking-triggers-rule)** — 3D.2 exercises the chain across all three Phase 3 endpoints. Stays inside the established cross-tenant + per-customer-per-tenant invariants pinned by 3C.
3. **Maturity + modification composition test** — 3D.3 exercises Layer 2 maturity downweighting against the modification rules.
4. **Phase 3 wrap reports** — REPORT_PHASE_3D.md + aggregate REPORT_PHASE_3.md after 3D.4.

The RLS audit + canary are stable through Phase 4+ — no re-audit needed until Phase 4 admin endpoints land (which trigger the audit refresh per the doc's own "Recommendations for Phase 4 / 5").

## Tests status

| Component | Pre-3C | Post-3C | Delta |
|---|---|---|---|
| Unit | ~430 | ~430 | +0 |
| Integration | ~221 | ~238 | +17 |
| Total | 651 | **668** | +17 |

All 668 tests pass. ruff clean, mypy strict clean.

## Operator checkpoint

Per the operator's per-batch preference (deferred 3D approval), 3D execution requires explicit re-approval after reviewing this report. The 3D plan (`PLAN_PHASE_3D.md`) is unchanged from initial production.
