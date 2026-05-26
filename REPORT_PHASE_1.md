# REPORT_PHASE_1.md

Phase 1 execution disposition. Mid-pass operator checkpoint per
`PLAN_PHASE_1.md` and the bootstrap-prompt mandatory-stop list. Waiting
on operator approval before any Phase 2 scope is opened.

---

## Aggregate stats

| Metric | Value |
|---|---|
| Commits in Phase 1 | 22 (`d292b41` planning artifacts → `72c453d` 1D.8) |
| Production source files | 22 |
| Tests passing | 267 / 267 (1.64 s wall-time) |
| Validation tooling | `ruff check` clean · `mypy --strict` clean · `pytest --asyncio-mode=auto` 267/267 · `alembic upgrade head` + round-trip downgrade clean |
| Schema | 12 tables · 9 RLS policies · `riskd_app` role · 9 `ux_*` + 4 `ix_*` indexes |
| Rules in `app/rules.yaml` | 14 (2 Layer 1 BLOCK + 12 Layer 3 score-only) |
| DSL Context-field whitelist | 45 fields (`ALLOWED_CONTEXT_FIELDS`) |
| Endpoints live | `GET /health/`, `POST /api/v1/shipments/booking/evaluate` |

---

## Per-batch disposition

### Batch 1A — Foundation adaptation (7 commits, 1 amendment)

| Commit | Theme | Outcome |
|---|---|---|
| `d292b41` | Phase 1 planning artifacts | operator-skipped (pre-approved) |
| `d5c70fb` | 1A.1 Trim CLAUDE.md | doc-reviewer PUBLISH |
| `ce7deee` | 1A.2 Consolidate `.ai/conventions*.md` | doc-reviewer PUBLISH (1 deviation: `app/signals.py` → `app/signal_helpers.py` rename, recorded in `.claude/STATUS.md`) |
| `292ce0a` | 1A.3 Adapt 6 reviewer agents + `_shared` | doc-reviewer PUBLISH |
| `337d73a` | 1A.4 Create `.ai/decisions.md` | doc-reviewer PUBLISH |
| `2f48a35` | 1A.5 Rewrite `.ai/rules/schema/enrichment.md` | doc-reviewer MINOR TWEAKS → PUBLISH |
| `dd5b9ac` | 1A.6 Rewrite `.ai/system-status.md` + filter gotchas | doc-reviewer NEEDS EDITS → PUBLISH (2 cycles) |
| `ffe9dae` | 1A.7 README + pyproject + ignores | full panel: SHIP IT / LOW RISK / CLEAN |
| `429d9f2` | Amendment: drop `FG_` env-var prefix | operator-directed, mechanical |

### Batch 1B — Skeleton (4 commits + 1 retrospective-fix commit)

| Commit | Theme | Outcome |
|---|---|---|
| `347ed0d` | 1B.1 Docker Compose + Dockerfile + `.env.example` | full panel: SHIP IT / LOW RISK / CLEAN |
| `403cd34` | 1B.2 Alembic + initial migration | panel + db-reviewer: APPROVED WITH RESERVATIONS → 3 Important fixes applied (`ux_*` naming, redundant `ix_users_tenant_customer` dropped, `ix_customers_enterprise_id` deferred to Phase 4) |
| `65b2068` | 1B.3 FastAPI lifespan + asyncpg pool + config + structlog | **review platform-quota-deferred** at the time; retrospective full panel returned SHIP IT / LOW RISK / CLEAN |
| `63eca43` | 1B.4 auth + `/health` + first integration tests | **review platform-quota-deferred** at the time; retrospective panel returned APPROVED WITH RESERVATIONS / LOW RISK / MINOR ISSUES / ACCEPTABLE |
| `b80fe66` | Address 1B.3-1B.4 retrospective findings | 503 on db failure, carve-out audit log, 7 new tests added (`AUTH_ENABLED=false`, case-insensitive Bearer, admin-role, hash-algo pin, `/health` 503 path, strengthened assertion specificity) |

### Batch 1C — Stub booking + e2e (2 commits)

| Commit | Theme | Outcome |
|---|---|---|
| `1d80aa4` | 1C.1 Stub booking endpoint + Pydantic models + entity upsert | full panel + test-reviewer: SHIP IT / LOW RISK / CLEAN / ACTUALLY GOOD |
| `9fbe994` | 1C.2 Booking e2e + payload fixtures | full panel + test-reviewer: MINOR ISSUES → 5 findings applied (extract `_TENANT_SCOPED_TABLES` constant + `_cleanup_tenant`, fix vacuous-pass risk on global-tables RLS check, widen PII-leak scan to 4 fields × 5 columns, lock COALESCE-fixture coupling, fix missing decisions-row assertion) |

### Batch 1D — Signal + baseline core (8 commits)

| Commit | Theme | Outcome |
|---|---|---|
| `1abbb37` | 1D.1 `app/signal_helpers.py` + 77 unit tests | **review deferred — see open items §6** |
| `13bfb53` | 1D.2 IP enrichment pipeline + offline refresh script | review deferred |
| `aeffad1` | 1D.3 `app/baseline.py` + 31 tests (incl. SELECT FOR UPDATE concurrency) | review deferred |
| `ee8d2ca` | 1D.4 `app/trust.py` + 10 boundary tests | review deferred |
| `4381df2` | 1D.6 `app/dsl.py` + 78 tests (unit + lockdown matrix) | review deferred — **highest-stakes commit in 1D**, security review essential |
| `383be56` | 1D.7 `app/rules.py` + `app/scoring.py` + 24 tests | review deferred |
| `affc7c2` | 1D.5 `app/context.py` + `app/velocity.py` + 6 tests | review deferred |
| `72c453d` | 1D.8 Wire full pipeline + 14 rules + case-2 fixture | review deferred — wires every prior 1D component |

---

## Plan deviations (recorded in `.claude/STATUS.md` `Unforeseen / checkpoints`)

| Date | Commit | What happened | Resolution |
|---|---|---|---|
| 2026-05-25 | 1A.2 | `app/signals.py` (helpers) + `app/signals/` (per-signal modules) would collide in Python's import system. | Renamed helpers module to `app/signal_helpers.py`. Recorded as a STATUS row; PLAN_PHASE_1.md text in 1D.1 refers to the original name but the STATUS row is authoritative. |
| 2026-05-25 | post-1A.7 | Operator amendment: drop the `FG_` env-var prefix project-wide. | 9 docs updated in single follow-up commit `429d9f2`; no Batch 1A commits rewritten. |
| 2026-05-25 | 1B.2 | Phase 1 RLS policies dormant because the app connects as the Postgres bootstrap superuser (which bypasses RLS). | Migration documents the gap; Phase 5 introduces a non-superuser login role with `riskd_app` membership; app-layer `tenant_id` filtering is the active control until then. |
| 2026-05-26 | 1B.3 | Reviewer subagents returned platform-quota errors mid-cycle. | Self-review against documented dimensions; retrospective full panel ran in `b80fe66` against `65b2068` + `63eca43`. |
| 2026-05-26 | 1D.* | All 8 1D commits proceeded without reviewer-panel feedback at commit time. | See "Open items" §6 below — retrospective review of 1D is the highest-priority open item before Phase 2 opens. |

Two architecture-level deviations from the plan (also documented in commit messages):

- **Sequential `build_context` reads instead of `asyncio.gather`.** asyncpg does not multiplex operations on a single connection; running 7 reads in parallel on the same txn connection raises `InterfaceError: another operation is in progress`. The simplest correct alternative is sequential awaits on the txn connection — works within the 30-50 ms context-load budget at Phase 1 cardinality. Phase 5 load test revisits if parallel reads via separate pool connections become necessary.
- **No separate `app/signals/` module package.** The plan listed 10 signal modules in 1D.8. In practice every "signal" in the Phase 1 catalogue is either a Context-field derivation (handled directly in `build_context`) or a stateless classifier (`signal_helpers.is_email_disposable`, etc.). Creating empty module files would have been dead-code ceremony. The plan's intent is satisfied by `ALLOWED_CONTEXT_FIELDS` listing every signal and `app/rules.yaml` consuming them.

---

## Reviewer-caught corrections (Batch 1A-1C)

Material findings that turned into code changes:

- **1B.2**: rename inline `UNIQUE(...)` constraints to `CONSTRAINT ux_<table>_<columns>` so they follow the project naming convention (9 unique constraints renamed; pre-data so cheap).
- **1B.2**: drop redundant `ix_users_tenant_customer` — covered by the leading prefix of `ux_users_tenant_customer_external`.
- **1B.2**: defer `ix_customers_enterprise_id` to Phase 4 (no Phase 1 query uses it).
- **1B.2**: add Phase 5 RLS-role-transition row to `.claude/STATUS.md` (migration comment promised the STATUS reference).
- **1B.4** retrospective: narrow `/health` `except` to `(TimeoutError, asyncpg.PostgresError, OSError)`; return 503 on db failure (load balancers key on status code, not body).
- **1B.4** retrospective: add `auth.carveout_active` warning log on the `AUTH_ENABLED=false` branch (audit-trail gap).
- **1B.4** retrospective: 7 new tests for `AUTH_ENABLED=false` carve-out, case-insensitive Bearer (4 cases), admin-role plumbing, hash-algorithm pin, `/health` 503 path, strengthened error-message assertions.
- **1C.2**: extract `_TENANT_SCOPED_TABLES` + `_cleanup_tenant` helper to `conftest.py` (was duplicated three places).
- **1C.2**: fix vacuous-pass risk on `test_global_tables_have_no_rls` (asserts found-set equals expected, not "iterate over rows").
- **1C.2**: widen PII-leak scan to 4 contact fields × 5 columns across 3 tables.
- **1C.2**: lock COALESCE-fixture coupling by explicitly `pop`-ing the field before the second POST.

---

## Explicitly deferred from Phase 1

Per `.ai/decisions.md` + plan declared-breaks:

- **Layer 2 scoring** (account-prior + trust-contribution + maturity downweight). Phase 2 wires it; `trust_score` is computed and attached to Context in Phase 1 but no rule reads it. `maturity_sensitive` flag is preserved on 5 rules but does not downweight.
- **11 trust-conditioned FreightSentry-port rules** (`very_low_trust`, `low_trust_*`, etc.). Phase 2.
- **Modification endpoint** (`POST /api/v1/shipments/modification/evaluate`). Phase 3.
- **Feedback endpoint** (`POST /api/v1/shipments/feedback`). Phase 3.
- **Per-tenant config validation** (`TenantConfig` Pydantic model) + cold-start window enforcement + read-only admin endpoints. Phase 4.
- **Real enrichment data** (MaxMind GeoLite2, IP2Proxy LITE PX11, FireHOL netsets, cloud CIDRs). Phase 1 dev runs with empty `data/enrichment/`; `scripts/fetch_enrichment.py` is wired but unused. Phase 6 staging replay needs real data.
- **Case-1 fixture replay** (dashboard ATO ~50 shipments). Phase 6 staging measures recall.
- **Observability backend** (CloudWatch EMF sink). Phase 5; structured logs are tagged `metric: true` ready for ingestion.
- **Phase 5 RLS role transition** (non-superuser login role with `riskd_app` membership) — see `.claude/STATUS.md`.
- **`scripts/tenant_onboard.py`**. Phase 4.

---

## Quality measurements

- **Case-2 pipeline verification** (`test_unfamiliar_ip_against_established_customer_triggers_signals`): a booking from a brand-new residential /24 against an established cloud-IP customer fires `ip_fully_new_for_customer` + (typically) `unfamiliar_ip_country_for_origin`; score > 0.0 confirms the 1C.1 stub is replaced by real scoring.
- **Velocity-burst verification** (`test_velocity_burst_from_one_ip_trips_ip_velocity_high_ui`): 11 web-channel bookings from one IP within an hour → the 12th fires `ip_velocity_high_ui`.
- **Clean-baseline sanity** (`test_clean_baseline_no_rules_fire`): a new customer with no enrichment data and no contact PII returns `ALLOW 0.0` with empty `triggered_rules` — pipeline doesn't false-positive on the simplest case.
- **DSL security lockdown** (34 escape-attempt cases in `tests/security/test_dsl_lockdown.py`): every CPython sandbox-bypass pattern (`__class__` walks, `getattr`/`open`/`eval`/`exec`/`__import__`, subscript walks, comprehensions, walrus, starred, f-strings) rejected at parse time.
- **Baseline concurrency** (`test_select_for_update_blocks_concurrent_writers`): two simultaneous `value_n += 1` transactions land final `value_n == 2.0` — no lost update under `SELECT FOR UPDATE`.
- **Latency**: not measured under realistic load (Phase 5 load test enforces the <200 ms p95 ceiling).
- **FPR / recall**: unmeasured. Phase 6 staging replay against real enrichment data calibrates both.

---

## Open items for Phase 2 (and the next operator action)

1. **Retrospective review of Batch 1D (8 commits)** — the most critical open item.
   - 1D.6 (DSL evaluator) is the highest-stakes commit: security-auditor + senior-engineer + code-flow must validate the whitelist, the `{"__builtins__": {}}` lockdown, and the `MappingProxyType` env wrapping.
   - 1D.3 (baseline) needs db-reviewer + senior-engineer attention on the JSONB-heavy save path and `SELECT FOR UPDATE` ordering.
   - 1D.8 (pipeline wire-up) needs the full panel since it touches the largest surface (booking endpoint, lifespan, conftest).
   - Suggested execution: same pattern as the 1B.3-1B.4 retrospective panel — 4 agents in parallel against each commit's diff, address findings via follow-up commits (no history rewrite per CLAUDE.md git-safety protocol).

2. **Operator approval** to open Phase 2 scope. Per `MASTER_PLAN.md` Phase 2 adds:
   - Layer 2 scoring (account-prior + trust contribution + maturity downweight)
   - 11 trust-conditioned + 3 dormancy + 2 lock-in + 1 residential-asn FreightSentry-port rules
   - Case-1 (dashboard ATO) fixture integration test
   - Per-rule weight calibration kicks off (operator-tuneable)

3. **Tracked-for-Phase-5 hardening** (not blocking Phase 2):
   - Non-superuser RLS-enforcing role
   - `uv.lock` lockfile for reproducible builds
   - Non-root container user
   - `last_used_at` writer on `api_tokens`
   - In-process tenant-config cache (60 s TTL)

---

End of Phase 1. Working tree clean. `feat/refactor` branch ready for retrospective review of Batch 1D, followed by Phase 2 scope.
