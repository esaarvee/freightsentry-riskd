# REPORT_PHASE_5 — Aggregate report

Phase 5 (Week 5) of the freightsentry-riskd build: security hardening
+ observability + performance pass. Four batches, ~28 commits, started
2026-06-02 and wrapped 2026-06-03.

## Per-batch reports

- `REPORT_PHASE_5A.md` — foundational hardening (6 commits)
- `REPORT_PHASE_5B.md` — tenant-config cache (5 commits)
- `REPORT_PHASE_5C.md` — observability backend (5 commits)
- `REPORT_PHASE_5D.md` — RLS role transition + load test + audit (7 commits)
- Phase 5 plans + retro fixes intermingled

## Phase 5 totals

- **Commits**: ~28 (plans + implementation + reports)
- **Tests**: 852+ at start of Phase 5 → 918 at end (+66 net, with 1 lost to canary api_tokens param drop)
- **Migrations**: 4 added — 0006 (api_tokens last_used index), 0007 (decisions UNIQUE widen), 0008 (riskd_app_login role), 0009 (drop RLS on auth tables)
- **New `.py` modules under app/**: 2 — `app/tenant_config_cache.py`, `app/observability.py`
- **New `.py` modules under scripts/**: 2 — `scripts/measure_baseline.py`, `scripts/load_test.py`
- **New docs/**: 3 — `docs/observability.md`, `docs/load-test-phase-5.md`, `docs/security-audit-rls-phase-5.md`
- **BUGS.md state**: 3 RESOLVED in 5A, 5 DEFERRED to Phase 6 / future cleanup
- **STATUS.md rows**: 2 added (5A.4 docker `.env` discovery; 5D.2 RLS-fixture refactor scope expansion)

## Reviewer panel verdict distribution

Across ~22 code-touching commits in Phase 5 that invoked reviewer panels:

- **Cleanest-on-first-pass verdicts** (SHIP IT / LOW RISK / CLEAN / ACTUALLY GOOD / PUBLISH): ~14 commits
- **Cycle 2 needed** (NEEDS MINOR FIXES / MINOR ISSUES / ACCEPTABLE / APPROVED WITH RESERVATIONS): 5 commits (5A.5 test cycle 1, 5A.7 multiple reviewers cycle 1, 5B.1 test cycle 1, 5B.3 senior + code-flow cycle 1, 5C.1 senior + test cycle 1, 5D.2 senior + security + code-flow cycle 1)
- **Cycle 3+**: 0
- **Operator escalation**: 1 (5D.2 first-attempt 59 failed + 335 errored — STATUS.md surface for option (a) vs (b) vs (c) decision)

**Reviewer-panel discipline held throughout Phase 5.** No panel-skip events. The Phase 4 retro lesson (per-commit panels regardless of per-batch checkpoint mode) was respected at every commit.

## Production bugs caught by reviewer panels

- **5A.5 test-reviewer cycle 1**: bare `try/except Exception: pass` swallowed the regression risk on the invalid-token path. False-pass shape caught + fixed in cycle 2.
- **5A.7 code-flow cycle 1**: misleading HTTPException detail string saying "booking-modification namespace collision" — exactly the case that is no longer a collision post-0007. Reworded to "intra-type duplicate" in cycle 2.
- **5B.1 test-reviewer cycle 1**: 12 of 18 spec families untested; `_now()` seam needed to avoid poisoning asyncio internals (discovered during initial debug). Parametrized test over METRIC_SPECS + missing-coverage tests added in cycle 2.
- **5C.1 senior-reviewer cycle 1**: missing MetricSpec entries for `admin.decision_lookup` + `admin.customer_baseline_lookup` (pre-plan verification correctly flagged but implementation missed first pass). Added in cycle 2.
- **5D.2 senior-reviewer cycle 1**: missing ALEMBIC_DATABASE_URL in docker-compose app.environment block (plan-spec divergence; broke local `docker compose exec app alembic upgrade head`). Added in cycle 2 retro commit.
- **5D.2 security-auditor cycle 1**: api_tokens RLS-drop creates defense-in-depth gap. Documented in 5D.5 audit doc.

## Phase 5 watch points retrospective

The PLAN_PHASE_5D.md "Phase 5 watch points (consolidated)" predicted ten concerns. Retrospective:

1. **Reviewer panel discipline**. HELD. Every code commit invoked the panel; no per-batch-mode skip event.
2. **5D.2 is the highest-risk commit in the phase**. CORRECT. First-attempt validation surfaced 394 issues; operator escalation; option (a) refactor lands a 22-file commit; reviewer cycle 2 retro needed.
3. **`last_used_at` rollback semantics**. HELD. Test contract documented; auth.py UPDATE inside auth-dependency transaction with autocommit; persists across pool connections.
4. **Cache staleness is user-facing**. HELD. 60s window documented in `.ai/decisions.md`, `docs/observability.md`, and `scripts/tenant_onboard.py` output.
5. **Cache concurrent-load behavior**. HELD. Unit tests with `asyncio.Event` barrier + integration tests with real pool prove 10 → 1 DB load for same tenant and 10 → 10 concurrent for distinct.
6. **EMF formatter preserves non-metric log shape**. HELD. Backward compat tests passing.
7. **Load test runs against synthetic enrichment**. HELD. Documented in load-test doc; Phase 6 staging adds +1-5ms p95.
8. **Container hardening preserves alembic-as-superuser pattern**. HELD via ALEMBIC_DATABASE_URL split (cycle 2 retro fix).
9. **UNIQUE widening migration downgrade is conditional**. HELD. Documented in migration docstring.
10. **`riskd_app_login` password is local-dev only**. HELD. Documented in migration + audit doc + `.env.example` + `docker-compose.yml`.

Additional watch points the plan didn't anticipate:
- **Auth chicken-and-egg surfaced under RLS** — required migration 0009 to drop RLS on api_tokens + app_users. Plan section 5D.2's "expected failures" listed missing tenant filters but not the auth-vs-tenant-discovery ordering. `app/auth.py` docstring lines 15-18 had flagged it as Phase 5 follow-up.
- **Test-fixture refactor scope** — 18 integration test files needed RLS-aware patching. Plan said "any test that depends on superuser-only operations will fail" but underestimated the breadth of raw `db_conn.execute(INSERT...)` patterns.

## Phase 6 readiness assessment

Phase 5 delivered everything Phase 6 needs to start:

✓ **RLS actively enforces at runtime** — production deploy doesn't need additional RLS work. Pool-init sentinel + per-request `set_tenant_id` composes correctly with policies.

✓ **Observability backend operational** — CloudWatch EMF wired in. Phase 6 only needs the CloudWatch Logs agent transport on the ECS Fargate task def.

✓ **Tenant-config cache reduces production query load** — 60s TTL bounded; per-process scoped; documented.

✓ **Load test methodology + baseline + role-transition results available** — Phase 6 staging-replay can rerun the same harness against real enrichment data + production-sized pool.

✓ **Phase 5 audit doc is the current security baseline** — `docs/security-audit-rls-phase-5.md` lists 8 carry-forward items for the Phase 6 audit.

✓ **All foundational hardening complete** — uv.lock, non-root container (UID 1000), last_used_at writer + supporting index, UNIQUE widening, riskd_app_login role.

✓ **Test count + regression gate** — 918 tests passing including case-1 + case-2 regression tests under `riskd_app_login`.

Phase 6 starting prerequisites (carry-forward):
1. Multi-stage Dockerfile to strip `build-essential` from runtime image.
2. Rotate `riskd_app_login` password from AWS Secrets Manager.
3. ECS Fargate task def with vCPU/pool/memory tuned for ~100 RPS production target.
4. CloudWatch Logs agent transport.
5. Real enrichment data ingestion (MaxMind GeoLite2, IP2Proxy PX11, FireHOL, cloud CIDRs).
6. Case-1 + case-2 production replay against real data.
7. RDS provisioning + Secrets Manager wire-up.
8. `.env` host-vs-container `DATABASE_URL` split (dev-host UX cleanup).

## Decision trail summary

Decisions added to `.ai/decisions.md` during Phase 5:
- Tenant-config caching (Phase 5B) — TTL=60s, per-tenant asyncio.Lock, TTL-only invalidation
- EMF observability backend (Phase 5C) — namespace, dimensions-vs-metrics, high-cardinality guard, test pattern

Decisions added to `.ai/conventions.md`:
- Dependency locking — `uv.lock` as source of truth; pre-commit pin matches lockfile; pip-at-runtime in Dockerfile

## Test count progression

```
Start of Phase 5:       852+ (precondition)
End of 5A.1:            856 (+4 from format-sync test count delta)
End of 5A:              863 (+7 net across 5A.5 + 5A.7)
End of 5B:              873 (+10 from cache module + concurrency tests)
End of 5C:              919 (+46 from EMF unit + integration tests)
End of 5D:              918 (−1 from api_tokens canary param drop)

Net: +66 over Phase 5 (-1 intentional drop from migration 0009)
```

## Sign-off

Phase 5 ships with:
- Active RLS enforcement on 7 business-data tables under `riskd_app_login`.
- 60s in-process TTL cache fronting `load_tenant_config`.
- CloudWatch EMF observability backend wired into the structlog chain.
- 4 new migrations (0006-0009).
- Non-root container.
- `last_used_at` writer + supporting index on api_tokens.
- Widened decisions UNIQUE to `(tenant_id, request_type, request_id)`.
- 918 tests passing including case-1 + case-2 regression under
  the active-RLS role.
- Load test 0 errors / 0 RLS violations across 23,720 requests with
  p95=12ms aggregate.

Phase 6 starts with the carry-forward list above. Production deploy
work can begin with the security model from `docs/security-audit-rls-phase-5.md`
as the baseline.
