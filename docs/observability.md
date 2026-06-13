# Observability — Phase 5C

This document inventories the project's metric emissions and the
CloudWatch EMF (Embedded Metric Format) wire-up that Phase 5C
introduced. Phase 5D's load test consumes the baseline captured here.

## Architecture

Production code emits structured logs via `structlog` (configured in
`app/logging.py`). Events that should appear as CloudWatch metric
points carry the `metric=True` keyword. The `app.observability.emf_processor`
sits in the structlog processor chain between `TimeStamper` and
`JSONRenderer`; on `metric=True` events it adds a
`_aws.CloudWatchMetrics` block to the event dict. `JSONRenderer` then
serializes the augmented dict to a single JSON line on stdout.

The CloudWatch Logs agent (Phase 6 deploy) ingests the lines that
carry an `_aws` block as metric points; the same lines also remain
queryable as structured logs. Non-metric events flow through
`emf_processor` unchanged.

**EMF namespace**: `FreightSentry/RiskD`.

## Metric inventory

The full table of `metric=True` event families and their EMF mapping
lives in `app/observability.py::METRIC_SPECS`. Adding a new
`metric=True` call site requires a corresponding `MetricSpec` entry —
without it the processor emits a one-shot stderr warning and the
event flows through without an EMF block (forward-compat behavior).

| Event | Dimensions | Metrics (unit) |
|---|---|---|
| `risk.evaluation` | tenant_id, decision | score, account_prior, signal_score, maturity, trust_score, flagged_count (Count), triggered_rule_count (Count), count (Count) |
| `modification.evaluation` | tenant_id, decision, modification_type | score, account_prior, signal_score, maturity, modification_velocity_1h (Count), modification_velocity_24h (Count), triggered_rule_count (Count), count (Count) |
| `auth.success` | tenant_id, role | count (Count) |
| `auth.invalid_token` | — | count (Count) |
| `auth.admin_required_denied` | tenant_id, role | count (Count) |
| `auth.carveout_active` | tenant_id | count (Count) |
| `tenant_config.cache.hit` | tenant_id | count (Count) |
| `tenant_config.cache.miss` | tenant_id | cache_size (Count), count (Count) |
| `tenant_config.loaded` | tenant_id | count (Count) |
| `tenant_config.value_caps.fallback` | tenant_id, currency | count (Count) |
| `feedback.applied` | tenant_id, label | flag_delta (Count), fraud_delta (Count), dimensions_written (Count), count (Count) |
| `feedback.monotonicity_skip` | tenant_id, new_label, prior_label | count (Count) |
| `feedback.monotonicity_skip_post_lock` | tenant_id, new_label, prior_label | count (Count) |
| `feedback.idempotent_replay` | tenant_id | count (Count) |
| `booking.idempotent_replay` | tenant_id | count (Count) |
| `modification.idempotent_replay` | tenant_id | count (Count) |
| `enrich.cache_hit` | — | count (Count) |
| `enrich.cache_miss` | — | count (Count) |
| `enrich.source_load_failed` | source | count (Count) |
| `admin.decision_lookup` | tenant_id, request_type | count (Count) |
| `admin.customer_baseline_lookup` | tenant_id | count (Count) |

Metric values without an explicit unit (score, account_prior, etc.)
are rendered without a `Unit` key in the EMF block, per the
CloudWatch spec (a literal `"None"` string would be rejected).

## High-cardinality guard

`request_id` is NEVER promoted to dimensions. CloudWatch hashes the
dimension tuple per metric point — a per-request unique identifier
would explode billing and lookups. The processor reads dimensions
exclusively from the `MetricSpec.dimensions` tuple, so a stray
high-cardinality field in the event dict structurally cannot become a
dimension. The integration test
`test_request_id_never_appears_in_dimensions_at_runtime` enforces
this end-to-end with a positive control on the regular log field.

## Baseline measurement

`scripts/measure_baseline.py` sends ~10K synthetic booking,
modification, and feedback requests against the local docker-compose
stack and reports per-endpoint p50/p95/p99 + mean. The captured
baseline reflects:
- 5B tenant-config cache (most requests hit the cache).
- 5C.2 EMF processor (every metric=True event runs through the
  processor — `synthetic enrichment` data only; Phase 6 staging
  rerun will use real GeoIP/IP2Proxy data).

The Phase 5D load test target is **p95 < 200ms across booking +
modification + feedback at 100 RPS sustained for 60+ seconds**. The
baseline measurement gates against the same envelope plus a
**headroom budget**:
- p95 > 200ms → RED. Surface to `.claude/STATUS.md`. Investigate
  before proceeding to 5D.
- p95 ∈ [170ms, 200ms] → YELLOW. The 5D role-transition + RLS overhead
  may push the load test over budget. Notify operator before 5D.3.
- p95 < 170ms → GREEN. Safe to proceed to 5D.

The script exits 0 / 2 / 3 corresponding to green / yellow / red.

### Captured baseline (5C.4 run, 2026-06-02)

Run: 1000 booking + 1000 modification + 1000 feedback against
`docker compose up -d` (synthetic enrichment data) at 25 concurrency.
Reproduce via:

```bash
PYTHONPATH=$PWD python3 scripts/measure_baseline.py \
    --token <api-token> \
    --booking-count 1000 \
    --modification-count 1000 \
    --feedback-count 1000 \
    --concurrency 25 \
    --json-out docs/baseline-phase-5c.json
```

Stack state: 5A (lockfile + container) + 5B (cache) + 5C.2 (EMF wired)
applied. The cache is warm for all but the first request to a given
tenant — typical production traffic shape.

| Endpoint | p50 (ms) | p95 (ms) | p99 (ms) | Mean (ms) | Headroom to 200ms p95 |
|---|---|---|---|---|---|
| booking | 33.0 | 47.9 | 201.6 | 37.6 | 152.1 |
| modification | 50.9 | 144.9 | 226.3 | 63.7 | 55.1 |
| feedback | 43.8 | 148.7 | 224.4 | 56.5 | 51.3 |

Status: **GREEN**. All endpoints under 200ms p95 with ≥30ms headroom
budget. The p99 tail on modification + feedback (~225ms) shows the
load test should expect a tail population near or just-above the 200ms
gate; p95 is the contractual envelope and remains comfortable.

Phase 5D considerations (informational, not blocking):
- modification + feedback have ~50-55ms headroom. The 5D role
  transition + RLS overhead is expected to add 5-15ms p95 (asyncpg
  policy evaluation per query). Even at the upper end, the load test
  would land at ~160ms p95 for these endpoints — still comfortably
  within the 200ms gate.
- Phase 6 staging-replay with real enrichment data adds 1-5ms p95 per
  request (MaxMind + IP2Proxy + FireHOL disk lookups). Real-data p95
  on modification + feedback is projected at ~155-165ms — still green.

The raw JSON output (`docs/baseline-phase-5c.json`) carries the same
data for programmatic comparison by 5D.3's load-test analysis.

## Production wire-up

Phase 5C delivers the formatter. Phase 6 production deploy wires the
CloudWatch Logs agent — the JSON-lines stream on stdout is consumed
by the agent and metric points are ingested under the
`FreightSentry/RiskD` namespace. No additional structlog
configuration changes are expected for the agent wire-up.
