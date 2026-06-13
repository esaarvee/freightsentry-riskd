# REFACTOR_REPORT — Test-Suite Soundness & Enrichment Observability

Branch `feat/refactor`. Base `50e3f3f` → HEAD `2838969`. Canonical env: `.venv` (Python 3.13, full
`[dev,test]` + `pytest-randomly 4.1.0`) against docker `postgres:16-alpine`.

Research base: `/tmp/test-soundness-research-01.md`, `/tmp/pre-tag-triage-01.md`. Plan:
`REFACTOR_PLAN_test-soundness.md` (+ operator-review amendments AM0–AM5).

## Commits landed

| # | Commit | Finding | Review |
|---|---|---|---|
| `05d9db5` | unit tests DB-free — opt-in pool + DB-free app.state | T4 | senior(AWR→fixed)/code-flow/test |
| `5963d15` | per-test isolation of global enrichment state | T2 | senior/code-flow/test all cleanest |
| `3c6132d` | structlog capture_logs order-independent | T3 | senior/code-flow/test all cleanest |
| `9c0824c` | BUGS: structlog/uv.lock version drift | (tangential) | triage-trivial |
| `9abc1c9` | feedback locks customers before customer_baselines | T5 | NEVER-SKIP: senior/security/code-flow/test/db all cleanest |
| `425613d` | alarm on enrichment load-failure (metric + /health) | O1 | NEVER-SKIP: senior/security/code-flow/test all cleanest |
| `1db7bfe` | two-job CI (DB-free unit + Postgres integration) | C1 | senior/code-flow/security all cleanest |
| `a27d5f2` | BUGS: snyk @master floating-ref note | (tangential) | triage-trivial |
| `2838969` | named role-specific IP constants (targeted) | T1 | senior/code-flow/test all cleanest |

## Per-finding disposition

- **T1 — doc-IP collision (root of the maturity cluster).** Mechanism: the global `ip_enrichment` table is
  shared across tests using the same RFC-5737 doc IPs; a leaked malicious `/32` (or a swapped, FireHOL-loaded
  shared enricher) made a later "clean IP" booking BLOCK at Layer-1 (`blacklisted_ip`). **Fixed** primarily by
  T2; T1 added `tests/ips.py` named role constants (disjoint clean `198.51.100.0/24` vs malicious
  `192.0.2.0/24`) and migrated the collision-relevant scoring tests (operator chose the **targeted subset**
  over full-tree once T2 proved categorically sufficient — full-tree was 92 IPs × 43 files of pure cosmetics).
- **T2 — global state never truncated between tests (determinism keystone).** **Fixed.** Autouse
  `_isolate_enrichment_state` reconstructs the shared `app.state.enricher` (undoing the
  `test_enrichment_refresh_lifespan` swap that loads FireHOL fixtures covering the doc ranges) and DELETEs
  `ip_enrichment`, DB-gated so DB-free unit tests never connect. This alone collapsed the entire order-dependent
  cluster.
- **T3 — structlog logger-cache pollution.** **Fixed.** Root cause precisely: a *second* `configure_logging`
  call (lifespan) installs a fresh `default_processors` list; a proxy cached via
  `cache_logger_on_first_use=True` froze the *first* list, so `capture_logs()` (which mutates the live list in
  place) was bypassed. Test-harness fixture disables caching (preserving the list instance — NOT
  `reset_defaults`, which swaps it) and drops stale per-proxy `bind` overrides. Production logging unchanged.
- **T4 — unit tests coupled to Postgres.** **Fixed.** Split the autouse `_pool` into a DB-free autouse
  `_app_state` (ruleset/enricher via `init_runtime`, no DB) + an opt-in `_pool`. Three wholly-DB-bound files
  mis-filed under `tests/unit/` were relocated to `tests/integration/`.
- **T5 — concurrent booking/feedback deadlock.** **REAL bug, operator-authorized fix landed.** Reproduced
  (`asyncpg.DeadlockDetectedError`): booking locks `customers`→`customer_baselines`; the feedback rejected-path
  locked them in reverse. Fixed by acquiring the `customers` `FOR UPDATE` before the baseline load (canonical
  order recorded in `.ai/conventions.md`, parent-before-child, with the cleanup-DELETE exemption). Deterministic
  regression test proven RED (deadlock) without the fix, GREEN with it. Two-table-locker audit confirmed
  booking (canonical), modification (baselines-only), admin (read-only) are otherwise consistent.
- **T6 — feedback_chain failures.** **Artifact (same class as T1/T2), not a feedback/detection bug.** Reproduced
  (seeds 7, 12): assertions failed with `triggered={'blacklisted_ip'}` — a later booking BLOCKed at Layer-1 on a
  leaked malicious IP, pre-empting Layer-2 evaluation. Resolved by T2.
- **O1 — silent enrichment load-failure.** **Fixed.** The three `25f9932` parse-failure paths now record into
  `degraded_sources()` and emit a unified `enrich.source_load_failed` metric (`source` dimension, error-type
  only, leak-safe; registered in `METRIC_SPECS`). `/health` reports `enrichment="degraded"` on parse failure
  while staying **HTTP 200** (the ALB/ECS probe keys off status — a corrupt dataset alarms, never drops tasks).
  The `25f9932` guard logic is unchanged.
- **C1 — CI blind spot.** **Fixed.** CI split into a DB-free `unit` job (no Postgres) and a Postgres-backed
  `integration` job (`alembic upgrade head` provisions the runtime role; full suite at a pinned seed). Gated
  behind T2/T3/T4/T5 so CI never runs a flaky suite.

## R2 resolution (AM5) — was the historical CI "937 green" real or hollow?

`init_pool` uses `min_size=2`, so `asyncpg.create_pool` connects eagerly; the old autouse `_pool` therefore
required a live DB for **every** test. Proven empirically: a pure unit test under an unreachable `DATABASE_URL`
**errors** at fixture setup (`ConnectionRefusedError`) — it does **not** skip. Therefore the prior `test.yml`
unit-only job (no Postgres service) **could not have passed as written**: every test would error at the
session-scoped autouse pool. Conclusion: either the historical CI unit job was red/erroring on the DB-bound
fixture, or it had provisioning not visible in the committed workflow. `gh` is not installed on this
workstation, so the live run history could not be queried — **flagged for the operator** to confirm via the
GitHub UI. Either way the prior "green" signal was softer than it looked (the second time this project's green
proved hollow — cf. the doc-staleness pass's 11-failure surprise). The decouple (T4) + two-job CI (C1) make the
signal real: unit is genuinely DB-free (913 pass under an unreachable DB) and the integration job actually runs
the DB suite.

## R3 verdicts + reproduction

| Straggler | Verdict | Reproduction |
|---|---|---|
| `test_concurrent_..._serialise` (T5) | **REAL deadlock** → fixed | `pytest tests/ -p randomly --randomly-seed=1` (or 4/5/12); deterministic: `pytest tests/integration/test_concurrent_baseline_writes.py::test_feedback_acquires_customers_lock_before_baselines_no_deadlock` (RED if `feedback.py` lock fix reverted) |
| `test_chain_{origin,email}_previously_rejected` (T6) | **Artifact** (IP-collision) | `pytest tests/ -p randomly --randomly-seed=7` (or 12) → `{'blacklisted_ip'}` |
| `test_log_tick_summary_counts` (T3) | **Artifact** (structlog) | collection order (integration before unit), or `pytest tests/integration/test_enrichment_refresh_lifespan.py <the unit test> -p no:randomly` |

R1 IP-usage audit: the FireHOL/cloud fixtures blocklist all three RFC-5737 /24s; the shared enricher only loads
them via the `test_enrichment_refresh_lifespan` swap (now reset per test by T2).

## Final suite state (genuine, order-independent)

| Run | Result |
|---|---|
| full `pytest tests/` @ seed 20260613 (pinned/CI) | **1229 passed** |
| full @ seed 42 | **1229 passed** |
| full @ seed 777 | **1229 passed** |
| (during the pass also green @ seeds 1/2/3/4/5/7/12/99/101) | 1229 passed |
| `pytest tests/unit/` under **unreachable** DATABASE_URL | **913 passed** (genuinely DB-free) |

`ruff check app/ tests/ scripts/` clean; `mypy app/` clean. Working tree clean. No `--no-verify` in the final
state.

## Observability change

`enrich.source_load_failed` metric (EMF, `FreightSentry/RiskD`, `source` dimension) on the three guarded
load-failure paths + `/health` parse-failure reflection (degraded, still 200). Closes the silent fail-open
detection-suppression class the dead-capability audit flagged. `docs/observability.md` inventory updated.

## Deferred (logged, not left vague)

- `.claude/BUGS.md`: structlog/uv.lock version drift; snyk `@master` floating-ref CI supply-chain note.
- Full-tree IP migration beyond the collision set (velocity keeps clean-intent keys inside `192.0.2.0/24`;
  `tests/ips.py` scope note flags the follow-up). FireHOL/cloud loaders don't record parse failures (pre-existing;
  O1 scoped to the three `25f9932` guard paths).

## Tag readiness

The suite is **deterministic and order-independent** (proven across ≥10 seeds, 1229 passing), the unit tier is
**genuinely DB-free**, the integration suite now **actually runs in CI**, the **real concurrency deadlock is
fixed** (regression-pinned), and the **enrichment fail-open is now observable**. A green run means something.
The one operator action item is confirming the historical CI unit-job status (R2/AM5) via the GitHub UI, since
`gh` was unavailable here — it does not block the tag. **Recommendation: the `v*` tag push is the next
reasonable action.**
