# REFACTOR_PLAN — Test-Suite Soundness & Enrichment Observability

HEAD `50e3f3f` · branch `feat/refactor`. Research base: `/tmp/test-soundness-research-01.md`,
`/tmp/pre-tag-triage-01.md`. Canonical env: `.venv` (full `[dev,test]` + `pytest-randomly 4.1.0`).
Runs BEFORE the `v*` tag.

## Decisions absorbed (operator checkpoints)

| # | Decision | Source |
|---|---|---|
| D1 | **T5 deadlock: fix lock ordering now** (product change, operator-authorized scope addition; never-skip review). Canonical order `customers → customer_baselines`; feedback takes the `customers` lock before the baseline `FOR UPDATE`. | Phase-1 checkpoint |
| D2 | **Unit tests must be DB-free, period.** Make the asyncpg pool opt-in; split the DB-free `app.state` (ruleset/enricher) setup into its own autouse fixture. | Phase-1 checkpoint |
| D3 | **CI = two jobs**: a DB-free unit gate (no Postgres service) + a Postgres-backed integration job. | Phase-1 checkpoint |
| D4 | No product-logic change anywhere EXCEPT the D1 lock-ordering fix. No assertion weakening. No calibration. | Pass spec |
| D5 | T1 needs **no fixture-narrowing**: the `/24` FireHOL/cloud fixtures never load into the shared `app.state.enricher` (verified). The collision is leaked `/32` rows in the never-truncated global `ip_enrichment` table. T2 truncation is the categorical fix; T1 is disjoint-`/32` hygiene. | R1 refinement |

## Operator-review amendments (binding — override the per-commit text below where they conflict)

**AM0 — commit reorder.** New order so the truncation fixture is born DB-gated and never fights the
DB-free-unit work: **(1) T4 DB-free unit → (2) T2 DB-gated truncation → (3) T1 intent-aware IPs →
(4) T3 structlog → (5) T5 lock fix → (6) O1 observability → (7) CI).**

**AM1 — T2 truncation must be DB-gated (anti-fight with T4).** The per-test enrichment isolation fixture must
NOT open a DB connection on DB-free tests. Implementation: a function-scoped `autouse` fixture that no-ops when
the pool is not initialised (`app.db._pool is None`) and otherwise truncates `ip_enrichment` + resets the
shared enricher source state. Because T4 lands first (AM0), `_pool` is opt-in, so DB-free unit tests never
initialise it → the fixture no-ops → no connection. **Acceptance (re-run after T2 lands):**
`DATABASE_URL=…:1/x pytest tests/unit/ -q` is green **with the truncation fixture present** (proves DB-free
purity survives). The two-job CI (unit-only job never initialises the pool) keeps this correct in CI.

**AM2 — T5 is a full two-table-locker sweep + a durable convention, not just a feedback edit.** Audit result
(every production path that locks both `customers` and `customer_baselines` in one txn):
- `booking.py` — `upsert_customer` (:109) → baseline `FOR UPDATE` (:155). **customers → customer_baselines = CANONICAL.**
- `feedback.py` — baseline `FOR UPDATE` (:250/:355) → `UPDATE customers` (:470). **VIOLATES → the fix.**
- `modification.py` — locks `customer_baselines` only (`build_modification_context`); does **not** write
  `customers`. Safe today; obligated to the convention if it ever writes `customers`.
- `admin.py` `/customers/{id}/baseline` — plain read-only `SELECT`s on both tables, **no row locks**. Safe.
- `scripts/e2e_verify.py` cleanup — DELETEs children-first (`customer_baselines` → `customers`) for FK
  integrity = reverse of the lock order; **exempt** (maintenance, never concurrent with request traffic).
- `scripts/tenant_onboard.py` — no `customer_baselines`. Safe.
  → Fix is `feedback.py` only; record the **canonical lock order in `.ai/conventions.md`** (extend the
  existing FOR-UPDATE note at lines 45-46): *"When a transaction locks both `customers` and
  `customer_baselines`, acquire the `customers` row lock first, then the `customer_baselines` `FOR UPDATE` —
  matching the booking path. Reverse order deadlocks. FK-cascade DELETE in maintenance scripts is the
  documented exception (children-first, non-concurrent)."* A comment protects this commit; the convention
  protects the next author (esp. the deferred modification-evaluation write path).

**AM3 — O1 `/health` must NOT flip the rotation probe.** The Dockerfile `HEALTHCHECK` and ALB/ECS probe key
off the **HTTP status** of `/health/` (`urlopen` raises on non-2xx → task killed). Enrichment parse-failure
must surface as the **metric** + an `enrichment: "degraded"` field in a **still-200** response — it must NEVER
change the status code (only DB-unreachable returns 503, unchanged). Add an explicit test:
corrupt-but-downloaded source → `/health` returns **200** with `enrichment == "degraded"`. Alarm fires off the
metric, not off task death. (Current code already returns 200 for degraded; preserve that invariant.)

**AM4 — T1 sweep is intent-aware.** Replace *incidental* doc-IPs with named constants, but **preserve
load-bearing addresses** — any IP whose specific value drives an assertion (e.g. "IP in CIDR resolves to
country X", a netblock/ASN-stats key, a velocity-key identity) keeps an address with that property. No blind
find-replace.

**AM5 — report the R2/CI resolution plainly.** The final report must state whether the historical CI "937
green" was real or hollow: unit tests **error** (not skip) without a DB (`ConnectionRefusedError`), so a
no-Postgres CI unit job could not have passed vacuously — either CI was red/failing on the unit job or had
provisioning not visible in `test.yml`. Check `gh run` history during execution; state the finding explicitly
(second time this project's green signal proved softer than it looked).

## Findings → fixes

| ID | Finding | Disposition |
|---|---|---|
| T1 | Doc-IP collision: clean-intent and malicious-intent tests share `/32`s; polluters leak malicious rows | Commit 2 (named disjoint IP constants) |
| T2 | Global `ip_enrichment` never truncated between tests → leaked `/32` rows BLOCK later clean bookings | Commit 1 (per-test truncation) — **determinism keystone** |
| T3 | structlog `cache_logger_on_first_use=True` + `capture_logs()` → 1 unit failure when integration runs first | Commit 3 (test-harness reset) |
| T4 | Autouse `_pool` forces a live DB on every unit test (`min_size=2`) | Commit 4 (opt-in pool + DB-free app.state) |
| T5 | Deadlock: booking locks `customers→baselines`, feedback `baselines→customers` | Commit 5 (lock-ordering product fix + deterministic regression) |
| T6 | feedback_chain (2): `{'blacklisted_ip'}` Layer-1 pre-empt — same class as T1/T2 | Resolved by Commits 1+2 (no own commit) |
| O1 | Enrichment load-failure silent: 3 WARNINGs carry no metric; `/health` keys off download not parse | Commit 6 (metric + `/health` reflection) |
| C1 | CI runs unit-only, no Postgres; and unit currently needs a DB | Commit 7 (two-job CI) — last, gated on 1–4 green |

---

## Commit 1 — T2: per-test `ip_enrichment` truncation + enricher source reset (determinism keystone)

**Changes** (`tests/conftest.py`): add an `autouse=True` function-scoped fixture that, after each test,
`TRUNCATE`s the global non-tenant-scoped enrichment state — `ip_enrichment` — and resets the shared
`app.state.enricher` source flags (`_loaded=False` and source tries to `None`/empty) so no enrich-persisted
or directly-INSERTed `/32` row, and no in-process source state, survives into the next test. Truncation runs
as the `riskd` (ALEMBIC) role connection used by fixtures (no RLS on `ip_enrichment`). Document why this table
is special (global, no-RLS, never tenant-cleaned by `_cleanup_tenant`).

**Tests**: no new test logic; this fixture is validated by the suite becoming order-independent. Add one
explicit isolation assertion test: seed a malicious `/32`, run a clean-IP booking helper, assert the prior
test's row does not leak (i.e. a fresh table) — proving the truncation fires.

**Validation**:
- `pytest tests/ -p randomly --randomly-seed=<pinned>` green; then `--randomly-seed=<s2>` and `<s3>` green
  (seed-independence). Pinned seed recorded in the report.
- `pytest tests/unit/ -x -q` still green.
- Confirm no test relies on cross-test `ip_enrichment` persistence (any failure here is a latent bad test → fix
  or log).

**Risk**: medium — an autouse truncation touching shared state; a test that (wrongly) depended on another
test's enrichment row would surface. Mitigated by multi-seed validation.

**Rollback**: remove the fixture; behavior reverts to today's order-dependence.

Reviewers: test-reviewer + senior + code-flow (harness/conftest).

---

## Commit 2 — T1: named role-specific IP constants (disjoint by intent)

**Changes**: introduce IP constants in `tests/conftest.py` (or `tests/_ips.py`) — `CLEAN_IP`, `MALICIOUS_IP`
(fh_level1), `TOR_IP`, `VPN_IP`, `CLOUD_IP`, each a distinct `/32`. Migrate the collision-relevant tests
(reproduced failing set + their polluters: maturity, feedback_chain, concurrent, baseline_gating,
currency_normalization, cold_start_grace, case_1/2, booking_stub) so clean-intent bookings use `CLEAN_IP` and
malicious seeds use the matching role constant — **no two intents share a `/32`**. Assertions unchanged. No
fixture changes (D5). **Operator decision (Phase-2): full-tree migration** — replace every incidental
RFC-5737 doc-IP (`192.0.2.*`, `198.51.100.*`, `203.0.113.*`) across all test files with the intent-matching
named constant. Where a test's intent is "just some address" (e.g. velocity counting), use a neutral
`CLEAN_IP`/`CLEAN_IP_2` so no incidental address collides with a malicious-role constant.

**Tests**: existing tests keep their assertions; they now reference constants. Add a guard test asserting the
constants are pairwise distinct.

**Validation**: `pytest tests/ -p randomly` green across the pinned + 2 seeds (defense-in-depth on top of
Commit 1); `ruff`, `mypy`.

**Risk**: low — mechanical, test-only, assertions preserved.

**Declared breaks**: none.

Reviewers: test-reviewer + senior.

---

## Commit 3 — T3: structlog test-harness isolation

**Changes** (`tests/conftest.py`): add an `autouse=True` fixture that, per test, resets structlog to a
test-capture-friendly configuration (e.g. `structlog.reset_defaults()` or reconfigure with
`cache_logger_on_first_use=False`) so `capture_logs()` always intercepts regardless of run order. **Production
`app/logging.py:34` (`cache_logger_on_first_use=True`) is unchanged** (constraint #5).

**Tests**: `test_log_tick_summary_counts` now passes regardless of order. Validate by running it after an
integration test in the same invocation (the polluting order) and under the full suite.

**Validation**: `pytest tests/integration/test_enrich.py tests/unit/test_enrichment_refresh.py::TestCowConcurrencyInvariant::test_log_tick_summary_counts -p no:randomly` green; full suite green under pinned seed.

**Risk**: low — test-harness only.

**Declared breaks**: none.

Reviewers: test-reviewer + senior + code-flow.

---

## Commit 4 — T4: unit tests are DB-free (opt-in pool + DB-free app.state)

**Changes** (`tests/conftest.py`): split the current autouse session `_pool`:
- A **DB-free** autouse fixture sets `app.state.ruleset`/`app.state.enricher` via `init_runtime(settings)`
  (no DB needed) for all tests.
- The asyncpg pool becomes **opt-in**: a `db_pool` (session) / `db_conn` fixture that integration tests
  request explicitly (directly or transitively via `seeded_tenant` etc.). Unit tests that don't request a DB
  fixture never open a connection. Optionally a `@pytest.mark.db` marker for clarity.
Verify the 2 unit-test files that legitimately use a DB fixture still get it.

**Tests**: prove DB-free: run `pytest tests/unit/` with an **unreachable** `DATABASE_URL` → green (this is the
acceptance criterion for D2). Run `tests/unit/` with no DB at all in the report.

**Validation**:
- `DATABASE_URL=postgresql://…:1/x pytest tests/unit/ -q` → green (no connection attempts).
- Full `pytest tests/` (with DB) green under pinned + 2 seeds.
- `mypy app/` unaffected.

**Risk**: medium — conftest fixture-graph refactor; a unit test that implicitly relied on the pool would
surface (it shouldn't, but the unreachable-DB run proves it).

**Declared breaks**: none (fixture graph changes land atomically).

Reviewers: test-reviewer + senior + code-flow.

---

## Commit 5 — T5: lock-ordering product fix + deterministic deadlock regression (operator-authorized, NEVER-SKIP)

**Changes** (`app/api/feedback.py`): establish the canonical lock order **`customers` before
`customer_baselines`** to match the booking path (`booking.py:109` upsert → `:155` baseline `FOR UPDATE`).
In the feedback transaction, acquire a `SELECT … FROM customers WHERE id=$1 AND tenant_id=$2 FOR UPDATE`
(or equivalent) **before** `CustomerBaseline.load(for_update=True)` (currently `feedback.py:250`, and the
second load site `:355`), so both endpoints lock the two rows in the same order. The later
`UPDATE customers SET flagged_count…` (`:470`) then updates the already-locked row. **No scoring/rule/maturity
change.** Add a comment documenting the canonical order and the deadlock it prevents.

**Tests**:
- Make `test_concurrent_booking_and_feedback_serialise` a **deterministic** reproduction: instead of relying on
  `asyncio.gather` timing, drive two real pooled connections with an explicit interleave (advisory-lock or
  step barrier) that forces the opposite-order acquisition — RED before the fix (`DeadlockDetectedError`),
  GREEN after. Keep the existing final-state assertions (flagged_count==1, n>=2, r_n>=1) intact.
- Keep the existing timing-based concurrency test too (now reliably green).

**Validation**:
- New deterministic test RED at parent commit (capture the deadlock), GREEN after the fix.
- `pytest tests/ -p randomly` green under pinned + 2 seeds (T5 no longer appears under any seed; sweep seeds
  1,4,5,12 which previously failed → green).
- `mypy app/`, `ruff`.

**Risk**: medium-high — product change to a write-path transaction. Justification: removes a real deadlock at
root; the change only reorders lock acquisition within an existing single transaction (no new queries beyond a
`FOR UPDATE` already implied by the later UPDATE), no behavioral/scoring change. Verified by the
deterministic regression + full multi-seed suite.

**Declared breaks**: none (fix + its test land together).

Reviewers (NEVER-SKIP, operator-significant + write-path): senior + security + code-flow + test
(+ db-reviewer — touches lock/transaction semantics on tenant-scoped tables).

---

## Commit 6 — O1: enrichment load-failure observability (metric + `/health` reflection) (NEVER-SKIP, security)

**Changes**:
- `app/enrich.py`: add `metric=True` to the three `25f9932` load-failure WARNINGs
  (`enrich.maxmind_city_load_failed`, `enrich.maxmind_asn_load_failed`, `enrich.ip2proxy_load_failed`) — or a
  unified `enrich.source_load_failed` event with a `source` dimension — leak-safe (error **type** only, no
  payloads; the existing `path`/`error=type(exc).__name__` shape is preserved). **Guard logic from `25f9932`
  unchanged** (constraint #5).
- `app/observability.py`: add the matching `MetricSpec` entry/entries (Count; dimension = `source` if unified)
  so the EMF processor emits under `FreightSentry/RiskD` rather than the forward-compat stderr warning.
- Parse-success reflection: have the enricher expose parse-failure state (e.g. a `parse_failures()` set or a
  `degraded` flag set when a downloaded source fails to open), and extend `/health` (`app/api/health.py:52`)
  so `enrichment` reports `degraded` when a source was downloaded/seeded but failed to parse — not just when
  `all_sources_loaded_at_least_once()` is false. Both halves land in **this one commit** (the alarm is
  incomplete with only one).

**Tests**: unit tests for (a) each load-failure path emits the metric event with a spec (assert via
`capture_logs` + the `_aws` block / `METRIC_SPECS` membership), leak-safe (no payload fields); (b) `/health`
returns `degraded` when a corrupt-but-downloaded source is simulated (reader `None` after parse failure while
the source is marked loaded). Add `MetricSpec`-coverage assertion.

**Validation**: `pytest tests/unit/ tests/integration/test_health.py -q` green; `mypy app/`; full suite green.

**Risk**: medium — touches `/health` (operational) and the metric pipeline (detection-suppression surface).
Justification: closes the silent fail-open the dead-cap/doc audits flagged; no guard-logic change.

**Declared breaks**: none.

Reviewers (NEVER-SKIP — detection-suppression + health): senior + security + code-flow + test.

---

## Commit 7 — C1/F: two-job CI (DB-free unit + Postgres integration) — LAST, gated on 1–4 green

**Changes** (`.github/workflows/test.yml`): split `lint-types-tests` into:
- `unit` — ruff + ruff-format + mypy + `pytest tests/unit/` with **no Postgres service** (relies on Commit 4
  making unit DB-free; proven by the unreachable-DB run).
- `integration` — adds a `services: postgres:16` block + `alembic upgrade head` + the `riskd_app_login` role
  setup + inline `DATABASE_URL`/`ALEMBIC_DATABASE_URL`/`HMAC_SECRET`, then runs `pytest tests/integration/`
  (and the full suite) under the **pinned seed** so CI matches local determinism. Add `pytest-randomly` to the
  test extras / CI install.
If the service wiring grows beyond a contained workflow change (secrets, runner cost, matrix), **stop and flag**
rather than expand (per spec).

**Tests**: N/A (CI config). Validate by reading the workflow + a local dry-run of each job's command set.

**Validation**: `ruff`/`yaml` lint of the workflow; the two job command sets run green locally
(`pytest tests/unit/` DB-free; `pytest tests/integration/` with DB).

**Risk**: low-medium — CI config; no app code. Gated on Commits 1–4 (determinism) so we never enable a flaky
suite in CI.

**Declared breaks**: none.

Reviewers: senior + code-flow (+ db-reviewer if the service block touches migration invocation).

---

## Execution notes

- 6-step cycle per commit. Pinned seed chosen at Commit 1 and used throughout; report records pinned + 2 spot
  seeds with pass/fail counts (order-independence proof).
- `pytest-randomly` added to dev/test deps so the seed machinery is reproducible in CI and by the operator.
- Intermediate declared-break `--no-verify` only if a commit is mid-repair; the pass ends genuinely green.
- Final deliverable: `REFACTOR_REPORT_test-soundness.md` (per-finding disposition T1–T6/O1/C1, R2 decouple
  shape, R3 verdicts + repro commands, final suite state with seeds, observability change, tag-readiness).
