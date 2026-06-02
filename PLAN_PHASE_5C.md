# PLAN_PHASE_5C — Observability backend (CloudWatch EMF)

Batch 5C of Phase 5. Adds a CloudWatch EMF (Embedded Metric Format) processor to the existing structlog chain. Detects `metric=True` events and emits them as EMF JSON. Backward compatible — non-metric logs flow through unchanged.

## Pre-plan verification findings

- **Structlog config** (`app/logging.py:16-30`): `configure_logging(level)` calls `structlog.configure()` with processors `[merge_contextvars, add_log_level, TimeStamper(iso), JSONRenderer()]` ending in `PrintLoggerFactory(stdout)`. No custom processors yet. Called once at app startup from `app/main.py:28`.
- **18 `metric=True` call sites** across 7 modules. Confirmed list:
  - `app/auth.py`: `auth.carveout_active`, `auth.invalid_token`, `auth.success`, `auth.admin_required_denied`.
  - `app/api/booking.py`: `booking.idempotent_replay`, `risk.evaluation`.
  - `app/api/modification.py`: `modification.idempotent_replay`, `modification.evaluation`.
  - `app/api/feedback.py`: `feedback.idempotent_replay`, `feedback.monotonicity_skip`, `feedback.monotonicity_skip_post_lock`, `feedback.applied`.
  - `app/tenant_config.py`: `tenant_config.loaded`, `tenant_config.value_caps.fallback`.
  - `app/enrich.py`: `enrich.cache_hit`, `enrich.cache_miss`.
  - `app/api/admin.py`: 2 sites (lines 139, 229; event names not in grep snippet — verify in plan).
  - **Plus**: 5B adds `tenant_config.cache.hit` and `tenant_config.cache.miss` before 5C lands. So 20 sites when 5C executes.
- **Bootstrap's expected list vs. reality**:
  - `auth.failure` does NOT exist as a single event; it's split into `auth.invalid_token` + `auth.admin_required_denied`. Both have `metric=True`. EMF processor handles them uniformly.
  - `feedback.monotonicity_skipped` is spelled `feedback.monotonicity_skip` (and `feedback.monotonicity_skip_post_lock`). Plan uses the actual names.
  - `modification.evaluated` is spelled `modification.evaluation`. Plan uses the actual name.
- **`risk.evaluation` log shape** (`app/api/booking.py:287-300`): 12 fields — `tenant_id`, `request_id`, `decision`, `score`, `account_prior`, `signal_score`, `maturity`, `triggered_rules` (list[str] — converted from set), `trust_score`, `flagged_count`, plus `metric=True`. `modification.evaluation` has analogous shape plus mod-velocity fields.
- **Serialization**: all `metric=True` event fields are JSON-serializable (sets are already list-converted at emit; no datetime/Decimal/UUID in metric payloads).
- **No existing benchmark infrastructure** (no pytest-benchmark, no scripts/benchmark.py). `locust>=2.32` is in the `[load]` optional extras of `pyproject.toml`. Phase 5C creates baseline measurement infra fresh.
- **No CloudWatch Logs agent wire-up exists**. Phase 5C emits EMF to stdout; production-deploy wires the agent in Phase 6.

## Decisions absorbed (5C-specific)

| Decision | Value | Source |
|---|---|---|
| EMF namespace | `FreightSentry/RiskD` | Bootstrap |
| EMF module location | `app/observability.py` (new), separate from `app/logging.py` (which retains the structlog configuration) | Bootstrap; separation keeps logging.py focused |
| Processor position | EMF processor inserted BEFORE `JSONRenderer()` so it can mutate the event dict; JSONRenderer writes the final JSON line | Standard structlog pattern |
| Non-metric pass-through | If `metric` key absent or falsy, processor is a no-op; event flows through unchanged | Bootstrap |
| EMF output channel | Same stdout sink as existing logs. Production CloudWatch Logs agent picks up EMF-formatted lines automatically (the `_aws` key is the signal). | Bootstrap |
| Per-event dimension policy | A small, fixed dimension set per event family (avoids unbounded cardinality). Documented in `docs/observability.md`. | Bootstrap watch point ("dimensions matter") |
| Dimensions for risk.evaluation | `tenant_id`, `decision` (NOT `request_id` — high cardinality) | Standard practice + bootstrap implicit |
| Metric values for risk.evaluation | `score`, `account_prior`, `signal_score`, `maturity`, `trust_score`, `flagged_count`, `triggered_rule_count` (length of triggered_rules list) | Bootstrap field list |
| `triggered_rules` (list) handling | NOT a metric value (lists aren't numeric). Kept as a non-EMF field (still present in the log line for debugging) — EMF surfaces a derived `triggered_rule_count` metric instead. | Verification + standard EMF practice |
| EMF unit annotations | Counts use `Count`; scores are unitless (`None` per EMF spec, rendered as omitted Unit field) | Standard practice |
| Per-endpoint metric documentation | New `docs/observability.md` enumerates every metric event, its dimensions, its metric values, and emission frequency. | Bootstrap |
| Baseline measurement methodology | Synthetic ~10K request load against the local Docker stack (booking + modification + feedback). Records p50/p95/p99 per endpoint. | Bootstrap |
| Baseline measurement script location | `scripts/measure_baseline.py` (sibling to `scripts/tenant_onboard.py`); uses `httpx.AsyncClient` (already a dep) — NOT locust for the baseline (locust comes in 5D for sustained load testing) | Bootstrap (decides locust vs wrk for 5D, doesn't specify baseline tooling) |
| Baseline output | `docs/observability.md` includes captured p50/p95/p99 table at the end of 5C | Bootstrap |
| Structlog test pattern | `structlog.testing.capture_logs()` per existing convention in `tests/integration/test_layer2_integration.py` | Verification |

## Workflow context

**Per-commit reviewer panel is MANDATORY in Phase 5.** Each commit lists its triage routing. This batch introduces a NEW `.py` file under `app/` (`app/observability.py`) — Never Skip applies. It also modifies `app/logging.py`, which affects every log output in the system — full panel.

Plan-file slicing for reviewers: `Plan file: PLAN_PHASE_5C.md, current commit: 5C.N (<title>), upcoming commits: 5C.(N+1) through 5C.4 sections.`

## Cross-batch dependencies

- **Depends on 5B**: the `tenant_config.cache.hit` / `cache.miss` events are emitted in 5B and consumed by 5C's EMF formatter. If 5C lands before 5B (e.g., operator reorders), the EMF formatter is still functionally correct — it just doesn't see cache events yet.
- **Feeds 5D**: the EMF baseline captured at end of 5C is the reference 5D's load test compares against.
- **No effect on 5A**: lockfile + container + last_used_at + UNIQUE widening are orthogonal.

## Commits

### 5C.1 — `app/observability.py` EMF processor module

**Theme.** New module exposing `emf_processor` (a structlog processor function) and supporting type definitions. Detects `metric=True` events and reshapes them into EMF JSON.

**Files changed.**
- `app/observability.py` — new file.

**Specifics.**
- The module defines:
  - `EMF_NAMESPACE: str = "FreightSentry/RiskD"` constant.
  - A `dict[str, MetricSpec]` mapping `event_name → MetricSpec` where `MetricSpec` declares the dimension keys and metric value keys for that event family. This is the central authority for "what dimensions and metrics each event publishes."
  - The processor function `emf_processor(logger, method_name, event_dict) -> event_dict` (structlog processor signature).
- Algorithm in `emf_processor`:
  1. If `event_dict.get("metric") is not True`, return unchanged.
  2. Read `event_dict["event"]` (the event name). Look up the MetricSpec. If absent, log a warning and pass-through (don't drop metrics for unknown event names; this is forward-compatibility for new metric=True events that haven't been classified).
  3. Build the `_aws.CloudWatchMetrics` block per EMF spec:
     - `Timestamp` (ms since epoch — convert from structlog's ISO timestamp if present, else `int(time.time() * 1000)`).
     - `CloudWatchMetrics: [{Namespace, Dimensions: [[<dim_keys>]], Metrics: [{Name, Unit?}, ...]}]`.
  4. Mutate `event_dict` to include `_aws` block alongside the existing top-level metric values + dimensions. Original event fields are preserved (this is the "metric and a regular log line" pattern recommended by AWS for EMF).
  5. Return `event_dict`.
- MetricSpec table (illustrative — full table in `docs/observability.md`):
  - `risk.evaluation`: dims `[tenant_id, decision]`; metrics `score, account_prior, signal_score, maturity, trust_score, flagged_count, triggered_rule_count`. Note: `triggered_rule_count` is derived in the processor as `len(event_dict.get("triggered_rules", []))`.
  - `modification.evaluation`: dims `[tenant_id, decision, modification_type]`; metrics `score, account_prior, signal_score, maturity, triggered_rule_count, modification_velocity_1h, modification_velocity_24h`.
  - `auth.success`: dims `[tenant_id, role]`; metrics `count=1` (synthetic counter — present as a numeric column).
  - `auth.invalid_token`: dims `[]` (no tenant since auth failed); metrics `count=1`.
  - `auth.admin_required_denied`: dims `[tenant_id, role]`; metrics `count=1`.
  - `auth.carveout_active`: dims `[tenant_id]`; metrics `count=1`.
  - `tenant_config.cache.hit` / `cache.miss`: dims `[tenant_id]`; metrics `count=1`. (Cache size may be added as a metric on miss only.)
  - `feedback.applied`: dims `[tenant_id, label]`; metrics `flag_delta, fraud_delta, dimensions_written, count=1`.
  - `feedback.monotonicity_skip` + `feedback.monotonicity_skip_post_lock`: dims `[tenant_id, label, prior_label]`; metrics `count=1`.
  - `enrich.cache_hit` / `enrich.cache_miss`: dims `[]`; metrics `count=1`.
  - `booking.idempotent_replay` / `modification.idempotent_replay` / `feedback.idempotent_replay`: dims `[tenant_id]`; metrics `count=1`.
  - `tenant_config.loaded` / `tenant_config.value_caps.fallback`: dims `[tenant_id]`; metrics `count=1`.
- Note: `request_id` is NEVER a dimension (high cardinality breaks CloudWatch billing/lookups). Kept in the log line as a regular field, not in the EMF dimension array.
- Type hints strict (mypy clean). `MetricSpec` is a typed dataclass or TypedDict.

**Validation.**
- `pre-commit run --all-files` clean.
- `mypy app/` strict clean.
- New unit test file `tests/unit/test_observability_emf.py`:
  - `emf_processor` on a `metric=True` event with known event name produces a dict containing `_aws.CloudWatchMetrics` with correct namespace, dimensions, and metric definitions.
  - `emf_processor` on `metric=False` event returns unchanged.
  - `emf_processor` on event without `metric` key returns unchanged.
  - `emf_processor` on `metric=True` event with UNKNOWN event name passes through with a one-time warning (use caplog or counter).
  - Dimension array is exactly the configured dimensions; high-cardinality fields like `request_id` are NOT promoted to dimensions.
  - `triggered_rule_count` is derived correctly from `triggered_rules` list.
- 5-8 test functions.

**Risk level.** Medium. New module introducing format that's load-bearing for production observability. Errors here look like "metrics broken in CloudWatch" rather than visible test failures.

**Reversibility.** High. `git revert` removes the module; nothing imports it yet (5C.2 wires it).

**Pre-commit verification.** Hooks pass.

**Observability.** Module IS observability. No new events emitted by it directly.

**Test changes.** New `tests/unit/test_observability_emf.py`.

**Rollback plan.** `git revert`.

**Declared breaks.**
- Scope: `emf_processor` defined but not yet in the structlog chain. No production log line is EMF-formatted yet.
- Resolved in: 5C.2.

**Reviewer routing.** Never Skip (new `.py` under `app/`). **Full standard panel: senior-engineer + security-auditor + code-flow-reviewer + test-reviewer.** Security-auditor scrutinizes: (a) no PII or token data is promoted to dimensions or metrics, (b) error path doesn't drop metrics silently (pass-through-with-warning, not drop), (c) the MetricSpec table is consistent with the actual fields each call site emits.

---

### 5C.2 — Wire EMF processor into structlog chain

**Theme.** Insert `emf_processor` into the structlog processor chain in `app/logging.py`, before `JSONRenderer()`.

**Files changed.**
- `app/logging.py` — modify `configure_logging` to include `emf_processor` in the processor list.

**Specifics.**
- New processor list: `[merge_contextvars, add_log_level, TimeStamper(iso), emf_processor, JSONRenderer()]`.
- Import `emf_processor` from `app.observability`.
- The position before `JSONRenderer()` is intentional: `emf_processor` mutates the event_dict; `JSONRenderer` serializes it to a JSON string.
- Position AFTER `TimeStamper(iso)` is intentional: `emf_processor` can read the ISO timestamp from `event_dict["timestamp"]` and convert to EMF's ms-since-epoch. (Alternative: `emf_processor` calls `time.time()` itself; either is acceptable. Plan uses the ISO timestamp from TimeStamper for consistency.)
- No other changes to `app/logging.py`.

**Validation.**
- `pre-commit run --all-files` clean. `mypy app/` strict clean.
- Existing 850+ tests still pass — non-metric logs are pass-through (verified by `tests/unit/test_observability_emf.py`'s pass-through cases from 5C.1).
- New integration test (added in 5C.3) asserts EMF emission shape end-to-end.
- Manual smoke: run a booking endpoint test with `structlog.testing.capture_logs`; assert the captured `risk.evaluation` event now has an `_aws` key.

**Risk level.** Medium. Touches the system-wide logging configuration. A bug here affects every log line.

**Reversibility.** High. `git revert` restores the original processor chain.

**Pre-commit verification.** Hooks pass.

**Observability.** EMF-formatted lines begin flowing in production traffic paths (when 5C reaches main).

**Test changes.** None directly. 5C.3 adds the integration test that exercises end-to-end EMF emission.

**Rollback plan.** `git revert`. If CloudWatch ingestion (Phase 6) misbehaves, revert this commit; production logs revert to plain JSON; the `app/observability.py` module remains as inert code.

**Declared breaks.** None.

**Reviewer routing.** Standard panel. Touches a system-wide processor chain. **senior-engineer + security-auditor + code-flow-reviewer.** Security-auditor verifies that no log line that previously had no `_aws` block ever loses ingestion compatibility (CloudWatch tolerates plain JSON; EMF augments).

---

### 5C.3 — Integration tests for EMF emission shape

**Theme.** End-to-end integration tests asserting that every `metric=True` event family emits a well-formed EMF block when the structlog chain is exercised.

**Files changed.**
- `tests/integration/test_observability_emf.py` — new file.

**Specifics.**
- Test infrastructure: use `structlog.testing.capture_logs()` to capture events from real endpoint hits.
- One test per metric event family (per the table in 5C.1):
  - `test_risk_evaluation_emits_emf` — POST booking; assert captured `risk.evaluation` event has `_aws.CloudWatchMetrics[0].Namespace == "FreightSentry/RiskD"`, dimensions `[["tenant_id", "decision"]]`, metric definitions for `score`, `account_prior`, etc.
  - `test_modification_evaluation_emits_emf` — analogous for modification.
  - `test_feedback_applied_emits_emf` — POST feedback; assert EMF block on `feedback.applied`.
  - `test_auth_success_emits_emf` — any authenticated request; assert `auth.success` has EMF block.
  - `test_auth_invalid_token_emits_emf` — invalid token request; assert `auth.invalid_token` has EMF block with no `tenant_id` dimension.
  - `test_tenant_config_cache_hit_emits_emf` — two requests for same tenant; assert second `tenant_config.cache.hit` has EMF block.
  - `test_tenant_config_cache_miss_emits_emf` — fresh tenant; assert first `tenant_config.cache.miss` has EMF block.
  - `test_enrich_cache_events_emit_emf` — request triggering enrich; assert `enrich.cache_hit` or `cache_miss` has EMF block.
  - `test_idempotent_replay_emits_emf` — replay any of booking/modification/feedback; assert idempotent_replay event has EMF block.
  - `test_non_metric_log_unchanged` — emit a `metric=False` log; assert no `_aws` block.
- Each test must call the production code path. Do NOT inline-recreate the structured log call in the test (per Phase 2/3 false-pass-test lesson).
- 10-12 test functions.

**Validation.**
- New file runs green. All metric=True call sites verified.
- Full integration suite remains green.

**Risk level.** Low (test-only) but high-coverage value.

**Reversibility.** High.

**Pre-commit verification.** Hooks pass.

**Observability.** Tests validate observability output. Don't emit additional metrics.

**Test changes.** New file ~10-12 functions.

**Rollback plan.** `git revert`.

**Declared breaks.** None.

**Reviewer routing.** Test-only per CLAUDE.md: **test-reviewer + senior-engineer + code-flow-reviewer.** Test-reviewer specifically verifies: (a) each test exercises a production path, not an inline shim, (b) assertions check actual EMF spec shape (Namespace, Dimensions, Metrics keys), (c) dimension cardinality assertions (no `request_id` in dimensions for risk.evaluation), (d) no false-pass shape.

---

### 5C.4 — Baseline measurement script + `docs/observability.md` + `.ai/decisions.md` update

**Theme.** New script `scripts/measure_baseline.py` runs ~10K synthetic requests against the local stack and reports p50/p95/p99 per endpoint. Output documented in `docs/observability.md`. `.ai/decisions.md` gains a Phase 5C section on EMF format choices.

**Files changed.**
- `scripts/measure_baseline.py` — new script.
- `docs/observability.md` — new file (~100-150 lines).
- `.ai/decisions.md` — new section "EMF observability backend (Phase 5C)".

**Specifics.**
- Script:
  - Uses `httpx.AsyncClient` for concurrency. NOT locust (locust comes in 5D's sustained load test).
  - Sends ~10K requests split across booking, modification, feedback endpoints. Per-endpoint count tunable via CLI flag (default: 3000 booking, 3000 modification, 4000 feedback).
  - Uses a fixed test tenant (seeded by script setup or by passing `--tenant-id`).
  - Records per-request latency (httpx response timing). Computes p50/p95/p99 per endpoint at end.
  - Output: human-readable summary table to stdout + JSON dump to `docs/baseline-phase-5c.json` (or appended to `docs/observability.md` as a table).
  - Concurrency: 50 concurrent requests. Lower than 5D's load test (100 RPS sustained) — this is a baseline, not a stress test.
  - Critical: this is run AFTER 5B's cache lands, so cache hit rates are realistic. Run AFTER 5C.2's EMF wire-up so the captured baseline reflects the new processor's overhead.
- `docs/observability.md`:
  - Sections: "Metric events overview" (the table from 5C.1), "EMF format reference" (link to AWS docs + brief example), "Baseline latency (Phase 5C)" (the measured numbers), "Production wire-up" (Phase 6 placeholder note).
- `.ai/decisions.md` section:
  - Why EMF (vs. Prometheus, vs. custom): CloudWatch native, no scrape infrastructure needed, matches the existing AWS deploy target.
  - Namespace choice (`FreightSentry/RiskD`).
  - Why dimensions exclude high-cardinality fields like `request_id`.
  - Why metric=True is the discriminator (cheap, opt-in, no rename required).
  - Why baseline measurement is at p95 < 200ms target (Phase 5D's gate).

**Validation.**
- `pre-commit run --all-files` clean.
- Manual: run `python scripts/measure_baseline.py --tenant-id <T>` against `docker compose up -d` local stack. Confirm output shape.
- **Headroom gate (per operator feedback):**
  - If p95 > 200ms: STOP — surface to `.claude/STATUS.md`. The cache and EMF processor changes shouldn't degrade latency this much; degradation indicates a defect.
  - If p95 ≥ 170ms (less than 30ms headroom for the 5D role-transition + RLS overhead): YELLOW FLAG. Document the tight headroom in `docs/observability.md` AND in `REPORT_PHASE_5C.md` AND surface to operator BEFORE 5D.3 runs. The 5D load test under the new role is likely to consume some of that headroom; if baseline is already this close, 5D.3 may exceed the 200ms gate even without a real defect. Operator decides whether to (a) investigate query plans before 5D, (b) proceed and observe 5D.3 outcome, or (c) defer some Phase 6 optimization forward.
  - If p95 < 170ms: green; record numbers and proceed.
- The baseline output explicitly annotates "Headroom available for 5D role transition + RLS = 200ms − p95" per endpoint, so 5D.3's comparison is direct.
- Baseline numbers go into `docs/observability.md` with the headroom annotation column.

**Risk level.** Low (script + docs).

**Reversibility.** High.

**Pre-commit verification.** Hooks pass.

**Observability.** The script EXERCISES observability; it doesn't add new emit points.

**Test changes.** No automated tests for the baseline script (it's run manually as part of validation). However, a smoke test in `tests/integration/test_baseline_script_smoke.py` verifies the script imports + parses CLI args + doesn't crash on a 10-request micro-run.

**Rollback plan.** `git revert`. The baseline data isn't load-bearing for runtime; reverting removes the docs but doesn't affect the running app.

**Declared breaks.** None.

**Reviewer routing.** Mixed: `.ai/decisions.md` amendment is standard path with doc-reviewer. The new script `scripts/measure_baseline.py` is a new `.py` file (but under `scripts/`, not `app/`) — Never Skip applies for `app/` specifically; for `scripts/` it's a senior-engineer + code-flow review at minimum. Combined routing: **senior-engineer + security-auditor + doc-reviewer + code-flow-reviewer.** Security-auditor verifies: (a) the script doesn't log auth tokens to stdout, (b) the test tenant credentials path is documented and doesn't leak secrets.

---

## Batch 5C summary

- 4 commits.
- New module: `app/observability.py` (~150-200 lines).
- New script: `scripts/measure_baseline.py` (~150 lines).
- New docs: `docs/observability.md` (~100-150 lines).
- New tests: `tests/unit/test_observability_emf.py` (5-8 functions); `tests/integration/test_observability_emf.py` (10-12 functions); `tests/integration/test_baseline_script_smoke.py` (1-2 functions).
- `.ai/decisions.md` gains a Phase 5C section.
- All 20 `metric=True` event families produce well-formed EMF blocks.
- Baseline latency table captured (p50/p95/p99 per endpoint).
- Cumulative test count target: ~885-895.

End of batch: REPORT_PHASE_5C.md. Observability backend operational; baseline established for 5D's load test comparison.
