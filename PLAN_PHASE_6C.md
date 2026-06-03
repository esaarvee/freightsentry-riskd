# PLAN_PHASE_6C — Replay validation

> **Phase 6, Batch C.** Measures freightsentry-riskd against three corpora exported from freight_risk's SQLite. **No tuning** based on findings — calibration backlog only.
>
> Companion: 6A (case-3 detection code) → 6B (CAD default) → **6C (this batch)** → 6D (deployment) → 6E (wrap).

---

## Pre-plan verification findings

### freight_risk SQLite schema (`/Users/drshott/PycharmProjects/miscProj/freight_risk/freight_risk.db`)

1. **Feedback table** `shipment_feedback`:
   - `shipment_id TEXT PRIMARY KEY`
   - `feedback TEXT NOT NULL` — values: `'approve'` | `'reject'`
   - `reviewed_at TEXT NOT NULL`
   - `notes TEXT DEFAULT ''`

2. **Distinct reject `notes` categories**:
   - `'gobolt-non-34x-api'` — **21,573 records** (case-2 candidates)
   - `'Roulottes Lupien — entire customer history fraud (user-confirmed)'` — **95 records** (case-3 census)

3. **Shipments table** `shipments` — key columns: `shipment_id`, `target_date` (YYYY-MM-DD indexed), `customer_id`, `customer_name`, `origin_address`, `destination_address`, `total` (REAL), `source_ip`, `source` (web/api/edi), `booking_started_at`, `ingested_at`.

4. **Join path**: `shipment_feedback.shipment_id = shipments.shipment_id`.

5. **Date range**: 2026-01-01 → 2026-06-01. Total shipments 2,265,975; total approved 35,206; total rejected 21,611.

6. **Case-3 distribution**: all 95 records in May 2026, single customer `Roulottes Lupien 2000 inc.`, CA origin → US destinations. **Single-cluster caveat** documented downstream.

7. **Case-2 distribution**: 21,573 records across multiple months; 6C samples 500 uniformly random.

8. **Approved corpus**: 26,569 approved-labeled records in Jan/Feb/March 2026 (verified). 6C samples 10,000 uniformly random with strict `WHERE feedback = 'approve'` filter to prevent any reject-labeled bleed-through.

### freightsentry-riskd booking-API payload shape (`app/models.py`)

POST `/api/v1/shipments/booking/evaluate` body — `BookingRequest`:

```
request_id: str (REQUIRED — idempotency key)
customer:
  external_id: str (REQUIRED)
  registered_address: str | None
  business_name: str | None
  first_seen_at: datetime | None
  is_api_partner: bool | None
user:
  external_id: str (REQUIRED)
  first_seen_at: datetime | None
source_ip: IPv4Address (REQUIRED)
shipment:
  origin:
    address: str (REQUIRED)
  destination:
    address: str (REQUIRED)
  value: Decimal (REQUIRED, ≥0)
  channel: str (REQUIRED)
  currency: str (default — was "USD"; after 6B: "CAD")
  origin_via_carrier_dropoff: bool = False (NEW after 6A.1; passed-through)
booking_ts: datetime (REQUIRED, ISO 8601)
enterprise: { external_id: str } | None
contact:
  origin_email, origin_phone, destination_email, destination_phone (all optional)
```

### Phase 5C replay-orchestrator pattern (`scripts/measure_baseline.py`)

- httpx.AsyncClient (timeout=10s).
- `asyncio.Semaphore` for concurrency limit.
- Idempotency: `request_id` constructed deterministically (e.g. `replay-approved-{idx}`).
- Latency: `time.monotonic()` per-request millis; percentile aggregation.
- Output: JSON with per-endpoint stats.

### Local stack pool max

`app/db.py:48` — `max_size=10`. Replay throttle MUST stay at-or-below pool saturation; **50 concurrent in-flight requests** at most (5x pool to allow request queuing without overrun).

---

## Decisions absorbed

| Decision | Value | Source |
|---|---|---|
| 6C scope | Three corpora replay against running freightsentry-riskd stack + measurement doc | Phase 6 prompt |
| Approved corpus size + filter | 10,000 random shipments WHERE feedback='approve' AND target_date BETWEEN '2026-01-01' AND '2026-03-31' | Phase 6 prompt + AskUserQuestion 2026-06-03 |
| Case-2 corpus | 500 random sample WHERE feedback='reject' AND notes='gobolt-non-34x-api' | Phase 6 prompt |
| Case-3 corpus | **Full 95-record census** WHERE feedback='reject' AND notes='Roulottes Lupien — entire customer history fraud (user-confirmed)' | AskUserQuestion 2026-06-03 |
| Case-3 detection target | **≥85% of 95 records fire `case_3_compound` rule** (≥81 records) | AskUserQuestion 2026-06-03 |
| Single-customer cluster caveat | Documented in 6E; not generalizable to population case-3 — only proves the Roulottes Lupien pattern is detected | AskUserQuestion 2026-06-03 |
| FPR reading | **Strict** — any BLOCK or REVIEW on approved corpus enumerated in `docs/replay-validation.md` with contributing rules per transaction; no threshold-gated alarm | Phase 6 prompt |
| Export script location | freight_risk repo (sibling); freightsentry-riskd commits the output JSON fixtures + the orchestrator | Phase 6 prompt |
| Replay orchestrator location | freightsentry-riskd `scripts/replay_validation.py` | Phase 6 prompt |
| Concurrency cap | 50 concurrent (5x pool max 10; matches Phase 5 load-test cadence pattern) | 6C verification |
| Replay currency | All payloads `currency: "CAD"` (post-6B project default; freight_risk `total` is currency-implicit → treat as CAD) | 6B carry-forward |
| Idempotency request_id pattern | `replay-{corpus}-{idx}` (e.g. `replay-approved-0`, `replay-case2-15`, `replay-case3-7`) | 6C verification + Phase 5C pattern |
| Tenant for replay | Single dedicated `riskd-replay` test tenant with `allowed_currencies: ["CAD"]` and CAD-keyed `value_caps`; created via `tenant_onboard.py` | 6C plan derivation |
| NO tuning | Document findings; defer to calibration backlog. Hard rule | Project-wide discipline |
| Measurement doc location | `docs/replay-validation.md` (sibling to `docs/load-test-phase-5.md`) | Phase 6 prompt |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md.
- **Reviewer panel MANDATORY per code commit**:
  - **6C.1 (sibling-repo export script doc + corpus JSON files)**: lightweight — `scripts/replay/data/*.json` are test data; doc commit references sibling script. → **senior-engineer + code-flow** (test-data path).
  - **6C.2 (`scripts/replay_validation.py` orchestrator)**: new Python script → standard path. → **senior-engineer + security-auditor + code-flow**.
  - **6C.3 (orchestrator unit tests)**: tests-only → **test-reviewer + senior-engineer + code-flow**.
  - **6C.4 (execute replay + commit `docs/replay-validation.md` with raw results)**: doc commit with measurement evidence. Doc-reviewer covers content; senior-engineer + code-flow validate methodology and rule-contribution attribution accuracy. → **senior-engineer + code-flow + doc-reviewer**.
  - **6C.5 (calibration backlog seed in `.ai/decisions.md`)**: `.ai/decisions.md` amendment → standard + doc-reviewer.
- Pre-commit gates enforced.

---

## Cross-batch dependencies

- **6A + 6B must land before 6C executes** — 6A's `case_3_compound` rule and `origin_via_carrier_dropoff` field are tested by case-3 replay; 6B's CAD default is the currency context for replay payloads.
- **6C → 6E**: 6E synthesizes 6C measurements into calibration backlog and production-launch checklist.

---

## Commits

### 6C.1 — Export-from-freight_risk script (sibling-repo) documentation + committed corpus JSON files

**Theme**: Document the sibling-repo export script in `docs/replay-validation.md` (skeleton) and commit the three corpus JSON files under `scripts/replay/data/`. This lets the orchestrator run from committed inputs and isolates the freight_risk dependency at a clean boundary.

**Files**:
- NEW `scripts/replay/data/approved_jan_mar.json` — 10,000 records.
- NEW `scripts/replay/data/case2_sample.json` — 500 records (random.seed deterministic).
- NEW `scripts/replay/data/case3_census.json` — 95 records (full Roulottes Lupien census).
- NEW `scripts/replay/README.md` — documents the export script (sibling-repo path), the SQL queries used (so the data is reproducible), the schema mapping (freight_risk → BookingRequest), the random seed, and the run timestamp. Includes the SQL queries verbatim:
  ```sql
  -- approved corpus (10K random, strict filter)
  SELECT s.* FROM shipments s
  JOIN shipment_feedback f ON f.shipment_id = s.shipment_id
  WHERE f.feedback = 'approve'
    AND s.target_date BETWEEN '2026-01-01' AND '2026-03-31'
  ORDER BY RANDOM() LIMIT 10000;
  -- case-2 (500 random)
  ... WHERE f.feedback = 'reject' AND f.notes = 'gobolt-non-34x-api' ... LIMIT 500;
  -- case-3 (full census, 95)
  ... WHERE f.feedback = 'reject' AND f.notes = 'Roulottes Lupien — entire customer history fraud (user-confirmed)';
  ```
- NEW `scripts/replay/EXPORT_SCRIPT_REFERENCE.md` — describes the Python script in the freight_risk repo at `/Users/drshott/PycharmProjects/miscProj/freight_risk/scripts/export_for_riskd.py` (sibling-repo path; not committed to freightsentry-riskd). Includes its argv interface (`--corpus {approved|case2|case3}` `--out <path>` `--seed N`), schema mapping table (freight_risk column → BookingRequest field), currency handling (`currency: "CAD"` injected at export time per 6B default), and the structured-field hardcoding rationale:

  > **Hardcoded structured fields per corpus** (Phase 6A amendment):
  >
  > Two structured fields not present in freight_risk's source data: `origin_via_carrier_dropoff` and `customer.registered_country`. Neither was exported from the platform's source CSV into freight_risk SQLite — both are import-scope artifacts, not production data gaps. The platform captures both signals at booking time; absence in freight_risk is purely a historical export omission.
  >
  > For replay validation, these fields are hardcoded per corpus:
  > - case-3 records (95): `origin_via_carrier_dropoff: true` and `customer.registered_country: "CA"` (operator-supplied ground truth from the fraud investigation — Roulottes Lupien is a Canadian business that used carrier dropoff for all 95 fraud transactions)
  > - case-2 records (500): both fields `false` / `null` respectively
  > - approved records (10K): both fields `false` / `null` respectively
  >
  > Implication for empirical detection rate measurement: rules that depend on these signals can only fire on case-3 records during replay. Corpora without ground-truth structured data produce zero false positives on country-triangle and carrier-dropoff compound rules because the signals can't evaluate True without source data. Structured-field architecture preserved: app reads `Address.country` / `Customer.registered_country` passthroughs; no in-app parsing.

**Specifics**:
- Each JSON file is an array of BookingRequest payloads ready to POST.
- Mapping (freight_risk → BookingRequest):
  - `shipment_id` → `request_id: "replay-{corpus}-{idx}"` (NOT shipment_id; orchestrator generates deterministic idempotency keys).
  - `customer_id` → `customer.external_id`
  - `customer_name` → `customer.business_name`
  - `source_ip` → `source_ip`
  - `origin_address` → `shipment.origin.address`
  - `destination_address` → `shipment.destination.address`
  - `total` → `shipment.value` (as Decimal)
  - `source` → `shipment.channel`
  - `booking_started_at` (or `target_date` if missing) → `booking_ts`
  - Synthesize `user.external_id` from a deterministic hash of `customer_id` (freight_risk doesn't model per-user identity; one synthetic user per customer).
  - `shipment.currency = "CAD"` (constant).
  - **`shipment.origin_via_carrier_dropoff`** (hardcoded per corpus):
    - case-3 corpus: `True` (operator-supplied ground truth — Roulottes Lupien fraud used carrier-dropoff)
    - case-2 corpus: `False` (gobolt API ATO pattern was automated booking)
    - approved corpus: `False` (no per-record source data; approximates legitimate freight as home-pickup)
  - **`customer.registered_country`** (hardcoded per corpus, Phase 6A amendment):
    - case-3 corpus: `"CA"` (operator-supplied ground truth — Roulottes Lupien is a Canadian business)
    - case-2 corpus: `null` (no per-record source data)
    - approved corpus: `null` (no per-record source data)
  - **`shipment.origin.country` + `shipment.destination.country`** (parsed from address last-token at export time using the same regex parser convention freight_risk addresses follow — sample addresses end in 2-letter country codes like "CA" / "US"). Returns None when unparseable. **Note**: this is an EXPORT-side parser used to populate STRUCTURED `Address.country` fields for the replay payload. It is NOT an in-app parser — freightsentry-riskd consumes the structured field passthrough. The structured-field architectural pattern is preserved: the app reads `payload.shipment.origin.country` directly; the export script's parser is an offline corpus-shaping artifact documented in `scripts/replay/README.md`.
- Corpus size sanity:
  - approved: 10,000 ✓
  - case-2: 500 ✓
  - case-3: 95 ✓ (full census; not sampled)

**Validation**:
- `python -c "import json; print(len(json.load(open('scripts/replay/data/approved_jan_mar.json'))))"` → 10000.
- Same for case-2 (500) and case-3 (95).
- Spot-check: open case3_census.json, confirm all 95 records have `customer.business_name` starting with "Roulottes Lupien" and `shipment.origin_via_carrier_dropoff: true`.
- `python -c "import json; data = json.load(open('scripts/replay/data/approved_jan_mar.json')); from app.models import BookingRequest; [BookingRequest.model_validate(r) for r in data[:100]]"` — first 100 records validate against current Pydantic model.

**Risk level**: medium. Corpus selection bug = measurement corruption. Mitigation: SQL verbatim in README, deterministic random.seed, spot-check + pydantic validation in this commit.

**Reversibility**: full via revert.

**Pre-commit verification**: `check-added-large-files` hook caps at 500KB. The 10K approved corpus may exceed this. Action: if total >500KB, raise the limit to 5MB in `.pre-commit-config.yaml` for this commit and document in commit footer. (10K × ~500 bytes ≈ 5MB — likely needed.) Alternatively split into multiple smaller JSON files (Lines records / NDJSON).

**Observability**: no change.

**Test changes**: none in this commit.

**Rollback plan**: revert.

**Declared breaks**:
- **Scope**: corpus JSON files committed but no orchestrator runs them yet.
  **Resolved in**: 6C.2 (orchestrator).

**Reviewer routing**: data files + docs → lightweight test-data path → **senior-engineer + code-flow + doc-reviewer**.

---

### 6C.2 — `scripts/replay_validation.py` orchestrator

**Theme**: The replay engine. Loads a corpus JSON, POSTs payloads to local freightsentry-riskd booking API with throttling + idempotency, captures decisions, aggregates results.

**Files**:
- NEW `scripts/replay_validation.py` — main script.

**Specifics**:
- CLI: `python scripts/replay_validation.py --corpus {approved|case2|case3} --base-url http://localhost:8000 --tenant-token $RIPLAY_TENANT_TOKEN --out docs/replay-results-{corpus}.json [--concurrency 50] [--limit N]`.
- Loads `scripts/replay/data/{approved_jan_mar|case2_sample|case3_census}.json`.
- httpx.AsyncClient + `asyncio.Semaphore(concurrency)` (default 50).
- Each request: POST `/api/v1/shipments/booking/evaluate` with `Authorization: Bearer {tenant_token}` header.
- For each response: capture (`request_id`, `decision`, `score`, `classification`, `triggered_rules`, `latency_ms`).
- Aggregation:
  - decision distribution (BLOCK / REVIEW / ALLOW counts)
  - per-rule fire counts (across all transactions)
  - per-transaction `triggered_rules` payload retained for the approved corpus (so we can enumerate "which rules fired on which approved record" in 6C.4 doc)
  - latency p50/p95/p99
- Output: a single JSON file at `--out` containing the raw results + aggregates.
- **Idempotency**: orchestrator uses `request_id: "replay-{corpus}-{idx}"`. Re-runs against the same tenant produce identical decisions; second POST returns idempotency-replay (200 with same decision). Document this behavior in script docstring.

**Validation**:
- `mypy app/ scripts/` clean (script gets type-checked too if added to mypy paths).
- `ruff check scripts/` clean.
- `pytest tests/unit/test_replay_validation.py` — passes (tests written in 6C.3).

**Risk level**: medium. Network code + concurrency. Tests in 6C.3 cover the corpus loader, payload mapper, and results aggregator.

**Reversibility**: full via revert.

**Pre-commit verification**: ruff + mypy + unit tests (6C.3 lands tests; this commit ships the script). Pre-commit unit tests pass against existing tests; new tests land next commit. This is NOT a declared break — the script is independent of existing tests.

**Observability**: script logs progress (count of in-flight, completed, error) at INFO level via structlog. EMF events from the freightsentry-riskd app run as normal during replay; the orchestrator doesn't emit metrics directly.

**Test changes**: none in this commit (tests land in 6C.3).

**Rollback plan**: revert script.

**Declared breaks**:
- **Scope**: orchestrator script exists with no unit-test coverage.
  **Resolved in**: 6C.3 (unit tests for loader, mapper, aggregator).

**Reviewer routing**: new Python file under `app/`-equivalent surface (scripts/) → standard path → **senior-engineer + security-auditor + code-flow**. (Security-auditor on the auth-token-handling code path.)

---

### 6C.3 — Orchestrator unit tests

**Theme**: Unit-level coverage for the orchestrator's deterministic surfaces: corpus loader, payload mapper, results aggregator.

**Files**:
- NEW `tests/unit/test_replay_validation.py` — tests.

**Specifics**:
- `test_corpus_loader_reads_approved` — opens fixture JSON, returns list of BookingRequest payloads.
- `test_corpus_loader_validates_against_model` — every payload passes `BookingRequest.model_validate`.
- `test_aggregator_decision_distribution` — given a synthetic list of (decision, score, triggered_rules) tuples, computes correct counts.
- `test_aggregator_per_rule_fire_counts` — synthetic input; per-rule counter is accurate.
- `test_aggregator_latency_percentiles` — synthetic latencies; p50/p95/p99 match numpy reference.
- `test_request_id_pattern_is_deterministic` — `replay-{corpus}-{idx}` format.
- NO network test (orchestrator's POST loop is integration-only; replays test it implicitly).

**Validation**:
- `pytest tests/unit/test_replay_validation.py -v` — all pass.
- `pytest tests/ --asyncio-mode=auto` — full suite still passes.
- `ruff check tests/` clean; `mypy app/` clean (tests under `tests/unit/` excluded from strict by pyproject).

**Risk level**: low.

**Reversibility**: full via revert.

**Pre-commit verification**: all gates pass.

**Observability**: no change.

**Test changes**: 6 new unit tests.

**Rollback plan**: revert tests.

**Declared breaks**: none.

**Reviewer routing**: tests-only → **test-reviewer + senior-engineer + code-flow**.

---

### 6C.4 — Execute replay + commit `docs/replay-validation.md` with results

**Theme**: Run the orchestrator end-to-end against all three corpora and commit the measurement doc. **No tuning.** No rule weight, threshold, or maturity parameter changes — even if findings look concerning. Concerns surface to 6C.5 calibration backlog.

**Files**:
- NEW `docs/replay-validation.md` — comprehensive measurement doc.
- NEW (or generated under ignored path) `docs/replay-results-approved.json`, `docs/replay-results-case2.json`, `docs/replay-results-case3.json` — raw orchestrator output.

**Workflow for this commit**:
1. Operator brings up local stack: `docker compose up -d` + `alembic upgrade head` + create replay tenant: `python scripts/tenant_onboard.py --slug riskd-replay --allowed-currencies CAD`. Save returned tenant token.
2. Run all three replays:
   ```
   python scripts/replay_validation.py --corpus approved --tenant-token $T --out docs/replay-results-approved.json
   python scripts/replay_validation.py --corpus case2 --tenant-token $T --out docs/replay-results-case2.json
   python scripts/replay_validation.py --corpus case3 --tenant-token $T --out docs/replay-results-case3.json
   ```
3. Generate `docs/replay-validation.md` per the structure below.
4. **STOP before tuning.** If FPR on approved looks concerning (any BLOCK or notable REVIEW count), if case-2 recall <50%, or if case-3 BLOCK rate <85% — DO NOT tune. Document the findings and proceed to 6C.5.
5. Commit.

**`docs/replay-validation.md` structure**:
- Methodology section: corpus selection criteria, SQL queries used (cross-reference `scripts/replay/README.md`), random seeds, throttle config, tenant config.
- Approved-corpus section:
  - Total: 10,000
  - Decision distribution: BLOCK count | REVIEW count | ALLOW count
  - **For each BLOCK + each REVIEW**: `request_id`, `score`, `triggered_rules` — the strict-reading enumeration the prompt requires
  - Per-rule fire counts across the 10K (descending by count)
  - Latency p50/p95/p99
- Case-2 corpus section:
  - Total: 500
  - Decision distribution
  - Per-rule fire counts
  - Recall pattern: which rules contributed to the BLOCK / REVIEW decisions
- **Case-3 corpus section** (with threat-model framing per Phase 6A amendment):
  - Total: 95
  - Decision distribution: BLOCK / REVIEW / ALLOW
  - **Detection target (amended)**: ≥85% of 95 records reach REVIEW or BLOCK via any combination of rules (≥81 records). NOT specifically via `case_3_compound`.
  - **Sub-section: case-3a empirical validation** — DEFERRED. The `case_3_compound` rule (established-customer compromise pattern) is not expected to fire on this census: the Roulottes Lupien customer's first 9 records hit the maturity gate (`customer_observations >= 10` false), and from record 10+ the customer's own route baseline is contaminated by the prior fraud records (so `shipment_route_unfamiliar_for_customer` evaluates False against the fraud-derived "familiar" baseline). The rule is in production for the case-3a threat class; empirical validation waits for case-3a fraud observed in production traffic with platform-supplied structured fields.
  - **Sub-section: case-3b detection on Roulottes Lupien census** — primary measurement surface. Detection contributors:
    - `cold_start_country_triangle_with_carrier_dropoff` (simple compound) — expected to fire on every case-3 record (all three signal conditions met by the hardcoded ground truth: `customer_registered_country="CA"` ≠ origin/destination US countries; `origin_via_carrier_dropoff=true`; `customer_observations < 10`)
    - `cold_start_population_baseline_rare_with_carrier_dropoff` (sophisticated compound) — fires conditional on tenant baseline state; see next sub-section
    - Existing maturity-insensitive cold-start rules: IP-quality (cloud/datacenter/VPN), country mismatch, IP-country novelty
  - **Sub-section: tenant route population baseline for case-3 measurement** — During 6C execution, operator decides via AskUserQuestion: (a) replay tenant gets `tenant_route_baselines` seeded with synthetic legitimate-shaped historical data (so the sophisticated compound can fire on case-3 census), or (b) replay tenant runs with empty baseline (so only the simple compound + existing cold-start rules drive detection). Either path produces a valid measurement; option (b) is more conservative (relies less on synthetic data). Default proposal: option (b); operator confirms at 6C execution kickoff.
  - **Per-record rule attribution**: for each case-3 record, list `triggered_rules` for transparency.
  - **Single-customer cluster caveat**: all 95 from Roulottes Lupien. Validates the specific pattern; population case-3b detection rate against diverse customers awaits post-launch traffic.
- **Explicit non-tuning statement**: "Findings documented per Phase 6 discipline. No rule weight, threshold, maturity parameter, or rule definition changed in response to these measurements. Calibration backlog at `docs/calibration-backlog.md` (created in 6E) enumerates items for post-launch real-data observation window."
- Limitations section: synthetic-customer-history, label-noise tolerance, single-customer case-3 cluster, IP-enrichment data freshness.

**Specifics**:
- The orchestrator's output JSON files are large (10K records). Commit them under `docs/` (or under a `replay-results/` subdir). If they exceed pre-commit large-file cap, document in commit footer + raise the cap or split.

**Validation**:
- `python -c "import json; print(json.load(open('docs/replay-results-approved.json'))['summary'])"` — sanity-check the summary block.
- Manual review of `docs/replay-validation.md` for prose accuracy.
- No automated test changes.

**Risk level**: medium. The strict-reading FPR enumeration must be accurate (rule-to-record attribution from `triggered_rules` payload). The orchestrator already retains this data; the doc generation must faithfully transcribe.

**Reversibility**: full via revert (delete docs + results JSON files).

**Pre-commit verification**: large-files cap may need adjustment.

**Observability**: this commit doesn't change observability; the replay run itself exercises the existing EMF emit path under load.

**Test changes**: none.

**Rollback plan**: revert.

**Declared breaks**: none.

**Reviewer routing**: doc commit with measurement evidence + methodology validation → **senior-engineer + code-flow + doc-reviewer**.

**Watch point**: if the operator inspects the run and finds discrepancies (e.g., orchestrator crashed mid-corpus, returned errors on N requests), the commit should NOT proceed with partial data. Stop, re-run, then commit complete results. The doc must be honest about any partial-run events.

---

### 6C.5 — Calibration backlog seed in `.ai/decisions.md`

**Theme**: Translate 6C findings into the deferred-calibration backlog. This is the project-wide "no tuning during build phases" discipline enforcement: findings get NAMED here, not acted on.

**Files**:
- MODIFY `.ai/decisions.md` — add Phase 6C section enumerating findings and the calibration backlog as deferred items.

**Specifics**:
- Phase 6C section structure:
  - **Findings summary** (cross-reference `docs/replay-validation.md` for detail).
  - **Calibration backlog** — bullet list of rules/thresholds that surfaced concerns:
    - Each item: rule name + observed pattern (BLOCK on approved corpus / case-X recall) + deferred-action ("observe in 5-month post-launch window, decide whether to tune").
    - Example shape: `- Rule X fired on N/10000 approved corpus records. Deferred: post-launch real-data observation will determine if this is a noise pattern requiring weight reduction or a true positive (operator-mislabeled-approved).`
  - **Additional calibration backlog seeds (Phase 6A amendment)**:
    - **Trust-suppression on mature accounts** — Phase 7+ architectural workstream. Pattern: mature legitimate customer has low `account_prior`; if compromised, signals fire but combined score may not reach BLOCK. Recommended designs: capability-based trust (per-dimension), session-anomaly signals (device/location change indicators), asymmetric trust freeze (rapid trust erosion on first anomaly).
    - **Population baseline tuning** — initial values from 6A.8: 2% rarity threshold + 100-observation minimum. Tune post-launch with real production traffic data once tenant baselines accumulate beyond the cold-start window.
    - **Empirical validation of `case_3_compound` rule** — deferred until (a) platform integration ships structured `Customer.registered_country` + `origin_via_carrier_dropoff` AND (b) case-3a-style fraud (established-customer compromise) is observed in production.
  - **Non-tuning statement**: rule weights, thresholds, maturity parameters UNCHANGED in Phase 6. Calibration commences post-launch.
- If 6C found NO concerning patterns (clean approved, strong recall), this section still exists and explicitly notes "no calibration items identified from 6C; the three Phase 6A amendment backlog seeds above carry forward to 6E `docs/calibration-backlog.md`."

**Validation**:
- `pytest tests/ --asyncio-mode=auto` — full suite passes (no code touched).

**Risk level**: trivial.

**Reversibility**: full via revert.

**Pre-commit verification**: doc gates only.

**Observability**: no change.

**Test changes**: none.

**Rollback plan**: revert.

**Declared breaks**: none.

**Reviewer routing**: `.ai/decisions.md` amendment → **senior-engineer + code-flow + doc-reviewer**.

---

## End-of-batch state (after 6C.5)

- 3 corpus JSON files committed (`scripts/replay/data/`).
- `scripts/replay_validation.py` orchestrator + 6 unit tests.
- `docs/replay-validation.md` measurement doc with raw FPR enumeration, case-2 recall, case-3 detection rate.
- 3 raw results JSON files (`docs/replay-results-*.json`).
- `.ai/decisions.md` carries Phase 6C findings + calibration backlog seed.
- **Zero rule weight / threshold / parameter tuning.**

## Open items handed to 6D/6E

- **6E** synthesizes the calibration backlog into the dedicated `docs/calibration-backlog.md` and folds 6C findings into the production-launch readiness checklist.
- **6E** documents the single-customer case-3 cluster caveat in the aggregate report.
