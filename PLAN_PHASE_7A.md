# PLAN_PHASE_7A — Repository hygiene + harness updates

> **Phase 7, Batch A.** Scrubs all freight_risk-derived data from repo history via `git filter-repo`, updates the replay orchestrator's argument surface and output shape to aggregate-only, and adds the freight_risk export script under `scripts/calibration/` (ephemera; deleted in 7E.3).

## Decisions absorbed

| Decision | Value | Source |
|---|---|---|
| Phase 7 scope | Calibrate 2 FPR rules + add case-3b compound + delete triangle compound + final validation | Phase 7 prompt; operator review of Phase 6C |
| Variant testing | FOUR variants (A=tightened gate, B=halved weights, C=combined, D=compound-with-secondary-signal) measured before any rule change committed | Operator AskUserQuestion 2026-06-04 |
| Variant A | `unfamiliar_ip_country_for_origin` gate `>= 30`, weight unchanged 0.3; `unknown_destination_address` gate `>= 30`, weight unchanged 0.2 | Operator AskUserQuestion 2026-06-04 |
| Variant B | Halve weights: `unfamiliar_ip_country_for_origin` 0.3→0.15; `unknown_destination_address` 0.2→0.10. Gates unchanged at `>= 10`. | Operator AskUserQuestion 2026-06-04 |
| Variant C | Combined: gates tightened to `>= 30` AND weights halved | Derived from A+B |
| Variant D | Compound with secondary signal. `unfamiliar_ip_country_for_origin`: append `AND (is_vpn OR is_proxy OR ip2p_threat_any OR ip_in_threat_list OR is_datacenter_ip)`, weight 0.3. `unknown_destination_address`: append `AND shipment_value > shipment_value_threshold_medium`, weight 0.2. Gates stay at `>= 10`. | Operator AskUserQuestion 2026-06-04 |
| Case-3b rule | New derived bool `customer_destination_country_mismatch_outbound` (null-safe helper `_outbound_destination_mismatch`); rule condition `customer_destination_country_mismatch_outbound AND origin_via_carrier_dropoff AND customer_observations < 10`; weight 0.65; `maturity_sensitive: false` | Operator AskUserQuestion 2026-06-04 |
| Triangle compound | DELETED (`cold_start_country_triangle_with_carrier_dropoff` + derivation + tests + field) | Phase 7 prompt; operator confirmation |
| Population baseline compound | RETAINED unchanged | Phase 7 prompt |
| Field count math | 76 → 75 (7C.3 drops `customer_country_triangle_mismatch`) → 76 (7C.2 adds `customer_destination_country_mismatch_outbound`). Net: 76. | V-7 + operator AskUserQuestion |
| Export script lifecycle | `scripts/calibration/` tracked during Phase 7; deleted entirely in 7E.3 | Phase 7 prompt |
| NDJSON output location | `/tmp/riskd-replay/` only; never committed | Phase 7 prompt |
| Customer country derivation | 4-tier (explicit col → address last-token regex → modal IP geo → null) | Phase 7 prompt |
| History scrub tooling | `git filter-repo --invert-paths` after mandatory operator approval | Phase 7 prompt |
| Python tooling | `python3 -m pip install git-filter-repo` (system `pip` bare-name not on PATH; `pip3` and `python3 -m pip` available — pip 25.2 / Python 3.14) | V-6 verification |
| `scripts/replay/` directory | DELETED whole (data dir + README + EXPORT_SCRIPT_REFERENCE) alongside `docs/replay-results/` in 7A.0 | Operator AskUserQuestion 2026-06-04 |
| Aggregate-only output policy | Orchestrator emits decision counts, per-rule fire counts, latency percentiles. NO per_transaction array. | Phase 7 prompt |
| Reviewer panel discipline | Per-commit, full panel, no skips | CLAUDE.md, Phase 4 retro |
| Commit strategy | Atomic — one logical change per commit | CLAUDE.md default |
| Max review cycles | 3 | CLAUDE.md default |

## Batch composition

| Commit | Title | Risk | Reviewer panel |
|---|---|---|---|
| 7A.0 | Repository scrub via git filter-repo + gitignore additions | HIGH (irreversible) | senior-engineer + code-flow + doc-reviewer (plus operator approval BEFORE execution) |
| 7A.1 | `scripts/replay_validation.py` argument surface + aggregate-only output | LOW | senior-engineer + code-flow + test-reviewer |
| 7A.2 | `scripts/calibration/` export script + run-variants scaffold + README | MEDIUM | senior-engineer + security-auditor + code-flow + test-reviewer + doc-reviewer |

---

## Commit 7A.0 — Repository scrub via git filter-repo

**Theme**: Remove all freight_risk-derived data from every commit reachable from `feat/refactor` via `git filter-repo --invert-paths`. Add gitignore patterns defending against re-introduction.

**Mid-pass checkpoint**: **Operator approval REQUIRED before this commit begins.** The history rewrite is irreversible. Pre-flight confirmation:
- Branch is `feat/refactor`, working tree clean (verified 2026-06-04).
- No commits pushed to remote (verified: `git log --branches --not --remotes` returns 174 unpushed commits; no remote at risk).
- No other clones exist (operator confirmation).

**Paths removed from all history**:
- `scripts/replay/` (whole directory — `data/` 6.2 MB NDJSON + README.md + EXPORT_SCRIPT_REFERENCE.md)
- `docs/replay-results/` (3.1 MB JSON with per_transaction arrays)

**Procedure**:

1. Install git-filter-repo (one-time): `python3 -m pip install git-filter-repo`.
2. Confirm pre-conditions: `git status` clean; `git rev-parse --abbrev-ref HEAD` == `feat/refactor`.
3. Run filter-repo: `git filter-repo --invert-paths --path scripts/replay --path docs/replay-results --force`.
4. Verify tree state:
   - `test ! -d scripts/replay` AND `test ! -d docs/replay-results` (both directories absent).
   - `git log --all --oneline | wc -l` reports a reduced commit count vs the pre-rewrite 174 (commits that only added these paths are gone).
   - `git grep -E 'scripts/replay/data|docs/replay-results|scripts/replay/README|scripts/replay/EXPORT_SCRIPT_REFERENCE'` returns ONLY references in plan/report markdown files. No production code references.
5. Run full test suite: `pytest tests/ --asyncio-mode=auto -q`. All tests must pass. (`tests/unit/test_replay_validation.py` references the orchestrator's hardcoded paths and will fail after the rewrite; 7A.1 fixes the orchestrator AND the tests together. **Declared break** below.)

**Declared breaks**:

- **Scope**: `tests/unit/test_replay_validation.py` references the orchestrator's `_CORPUS_DIR` hardcoded path and the `--corpus`-only argument surface, both of which 7A.1 replaces with `--corpus-dir`. The tests will fail between 7A.0 and 7A.1. Pre-commit hooks (which include `pytest tests/unit/`) will be bypassed via `git commit --no-verify` for 7A.0 ONLY.
- **Resolved in**: 7A.1 — orchestrator argument surface update and matching test rewrite.

  Test bypass justification: filter-repo rewrites every commit's SHA; if we attempted to land 7A.1's test changes BEFORE the rewrite, the rewrite would erase those test changes from the rewritten commits. Sequence must be: rewrite (7A.0) → land new orchestrator + tests in a fresh commit (7A.1) on top of the rewritten history.

- **Scope**: `scripts/replay_validation.py` module-level constants `_CORPUS_DIR` and `_CORPUS_FILES` reference the now-deleted `scripts/replay/data/` path. The orchestrator will raise `FileNotFoundError` at runtime until 7A.1 rewrites it.
- **Resolved in**: 7A.1 — orchestrator argument surface update.

**Files changed in this commit (post-rewrite)**:

- `.gitignore` — append:
  ```
  /tmp/riskd-replay/
  scripts/calibration/  # phase 7 ephemera; tracked but defense-in-depth
  scripts/replay/data/  # paranoia (path removed from history)
  docs/replay-results/  # paranoia (path removed from history)
  ```

**Validation**:

- Working tree contains no files under `scripts/replay/` or `docs/replay-results/`.
- `git log --all --oneline | wc -l` shows reduced commit count.
- `git grep -E 'scripts/replay/data|docs/replay-results'` returns only plan/report markdown references; no production code.
- `pytest tests/unit/test_replay_validation.py` FAILS (expected; declared break). All other tests pass.
- `git commit -m '...' --no-verify` (justified by declared break, scope explicitly named).

**Reviewer routing**: senior-engineer + code-flow + doc-reviewer. Plus mid-pass operator approval BEFORE execution.

- senior-engineer verifies tree-state integrity (no orphaned files, no broken module imports in production code).
- code-flow verifies no production code path references the deleted directories.
- doc-reviewer verifies the gitignore additions are documented and serve a clear purpose.

**Risk**: HIGH (irreversible rewrite). Mitigations: pre-flight verification of unpushed state; operator approval before execution; declared-break scope explicitly limited to the test file.

---

## Commit 7A.1 — Orchestrator argument surface + aggregate-only output

**Theme**: Replace `scripts/replay_validation.py`'s hardcoded corpus path with a `--corpus-dir` CLI flag, add `--rules` flag for variant rule files, add `--compare` mode for A/B side-by-side measurement, and rewrite the output JSON to aggregate-only (no per_transaction array). Restore the test suite that 7A.0 declared broken.

**Files modified**:

- `scripts/replay_validation.py` — argparser rewrite:
  - `--corpus {approved|case2|case3}` retained as selector (which of the three NDJSON files to run).
  - **NEW** `--corpus-dir PATH` (required; replaces hardcoded `_CORPUS_DIR`). Directory must contain `approved_jan_mar.ndjson`, `case2_sample.ndjson`, `case3_census.ndjson`. Fail-fast with a clear error if any file is missing.
  - **NEW** `--rules PATH` (default `app/rules.yaml`). Path to the rule file passed to `app.rules.load_rules`. Required so variant rule files in `/tmp/rules-variants/` can be measured without touching `app/rules.yaml`. **Note**: the orchestrator does NOT load rules client-side; it POSTs against a server endpoint that loads `app/rules.yaml` at app lifespan startup. To measure a different rule file the operator MUST restart the docker-compose stack with the variant file. The `--rules` argument records WHICH rule file the orchestrator ran against — emitted in the output JSON metadata for auditability — but does not itself swap rules. See "Variant orchestration constraint" in 7B.1 for the full mechanism.
  - **NEW** `--out PATH` (default stdout). JSON aggregate output.
  - **NEW** `--compare PATH1 PATH2` (optional). Two pre-computed result JSON files; emits a delta report (FPR change, recall change, per-rule fire rate change) to stdout. Does NOT run new replays. Decouples the comparison logic from the replay execution.
  - REMOVE the hardcoded `_CORPUS_DIR` module constant; `_CORPUS_FILES` becomes a relative-name lookup applied to `--corpus-dir`.
  - REMOVE `per_transaction` array from the output JSON. Keep: corpus, started_at, finished_at, throttle_concurrency, requested/responses_200/errors counts, decision_distribution, per_rule_fire_counts, latency_summary, rules_file_recorded (metadata of `--rules` arg). Per-record content (request_id, score, triggered_rules) NOT emitted. error_details retained but request_id field stripped (replaced with `index` integer).

- `tests/unit/test_replay_validation.py` — rewrite tests for the new argument surface:
  - Missing `--corpus-dir` → exit code 2 (argparse error).
  - `--corpus-dir` points to non-existent dir → clear error, exit code 2.
  - `--corpus-dir` exists but missing one of the three NDJSON files → fail-fast with a per-file message.
  - Happy-path single-rules run (mock the httpx client; verify aggregate output shape).
  - `--compare` mode happy path (mock two JSON files; verify delta report shape).
  - Aggregate-only output verification: assert NO `per_transaction` key in the output dict.
  - `--rules` metadata round-trip (assert the path string appears verbatim in the output's `rules_file_recorded` field).

**Validation**:

- `pytest tests/unit/test_replay_validation.py -v` — all tests pass (restores the declared break from 7A.0).
- `pytest tests/ --asyncio-mode=auto -q` — full suite passes.
- `ruff check app/ scripts/ tests/` clean.
- `mypy app/` strict-mode clean (orchestrator is in scripts/, not app/, so mypy doesn't gate it; ruff still applies).
- Integration smoke test (manual; not enforced via pytest): create a 3-record dummy NDJSON in `/tmp/dummy-corpus/`, run `python scripts/replay_validation.py --corpus approved --corpus-dir /tmp/dummy-corpus --tenant-token DUMMY --out /tmp/dummy-out.json` against a running docker-compose stack. Verify aggregate output shape; cleanup.

**Reviewer routing**: senior-engineer + code-flow + test-reviewer.

**Risk**: LOW (orchestrator script; no production code path affected; tests restore the declared break).

---

## Commit 7A.2 — Export script + variant scaffold + README

**Theme**: Create the freight_risk export script that reads the sibling SQLite DB and writes NDJSON corpora to `/tmp/riskd-replay/`. Create the variant-orchestration scaffold that 7B.1 invokes. Both files live under `scripts/calibration/` — tracked during Phase 7, deleted in 7E.3.

**Files added**:

- `scripts/calibration/__init__.py` — empty marker so unit tests can import.
- `scripts/calibration/export_from_freight_risk.py` — the export script.
- `scripts/calibration/run_variants.py` — variant orchestration scaffold (full implementation in 7B.1; this commit lands a stub with the CLI signature + docstring so 7B.1 only adds business logic, not the file framing).
- `scripts/calibration/README.md` — usage notes + Phase 7 ephemera warning.
- `tests/unit/test_export_from_freight_risk.py` — unit tests for the export script.

**`scripts/calibration/export_from_freight_risk.py`** key behavior:

- CLI: `--db PATH` (default `/Users/drshott/PycharmProjects/miscProj/freight_risk/freight_risk.db`), `--out-dir PATH` (default `/tmp/riskd-replay/`), `--seed INT` (default 42).
- Reads the freight_risk SQLite DB read-only (`mode=ro` URI param).
- Writes three NDJSON files to `--out-dir`:
  - `approved_jan_mar.ndjson` — 10,000 records, query: `WHERE feedback='approve' AND target_date BETWEEN '2026-01-01' AND '2026-03-31'`, random sample with `random.seed(seed)`.
  - `case2_sample.ndjson` — 500 records, query: `WHERE feedback='reject' AND notes='gobolt-non-34x-api'`, random sample with seed.
  - `case3_census.ndjson` — 95 records, query: `WHERE feedback='reject' AND notes='Roulottes Lupien — entire customer history fraud (user-confirmed)'`, full census (no sample).

- Customer-country derivation (4 tiers, priority order, per record):
  1. **Explicit column**: if freight_risk's `customers` table has a `country` column, use the value when non-null and matches `[A-Z]{2}` regex.
  2. **Address last-token regex**: parse the customer's primary address with `re.match(r'.*,\s*([A-Z]{2})\s*$', addr)`. Only succeeds on addresses ending in 2-letter uppercase code.
  3. **Modal IP geo**: gather the customer's historical source IPs from freight_risk's shipments table. MaxMind-lookup each (requires `~/.maxmind/GeoLite2-Country.mmdb` accessible). Take modal country. Require `n >= 5` lookups AND `top_share >= 0.70` for the modal result to count.
  4. **None**: when none of the above produces a result, set `customer.registered_country = null`.

- Tier transitions logged to stderr at run end: `"tier 1: N records, tier 2: N records, tier 3: N records, tier 4 (null): N records"`.

- Per-corpus hardcoded overrides:
  - **case-3 records**: `customer.registered_country = "CA"` (overrides derivation; operator ground truth from the Roulottes Lupien investigation). `shipment.origin_via_carrier_dropoff = true`.
  - **case-2 and approved records**: derivation result for `customer.registered_country`; `shipment.origin_via_carrier_dropoff = false`.

- All records: `shipment.currency = "CAD"` (Phase 6B project default; freight_risk values are currency-implicit).

- Output schema: each line MUST validate against `app.models.BookingRequest`. The script imports `app.models` and runs `BookingRequest.model_validate(record)` on a sampled subset (every 100th record) for shape validation; any validation failure stops the export with a clear error.

**`scripts/calibration/run_variants.py`** scaffold (full logic in 7B.1):

- Stub `main()` function with argparse signature: `--corpus-dir PATH`, `--base-rules PATH` (default `app/rules.yaml`), `--variants-dir PATH` (default `/tmp/rules-variants/`), `--results-dir PATH` (default `/tmp/phase-7b-results/`), `--tenant-token TOKEN`, `--base-url URL` (default `http://localhost:8000`).
- Stub body: prints `"Not implemented; see Phase 7B.1"` and exits with code 1.
- Docstring lays out the four-variant plan (A/B/C/D) with the exact rule modifications per variant for forward reference.

**`scripts/calibration/README.md`**:

- Opening: "This directory is Phase 7 ephemera. Created in PLAN_PHASE_7A.md commit 7A.2; deleted in PLAN_PHASE_7E.md commit 7E.3. Do not rely on it after Phase 7 closes."
- Usage walk-through for `export_from_freight_risk.py` (DB path, /tmp output, expected runtime ~30s).
- Usage walk-through for `run_variants.py` (post-7B.1).
- Note: no rules variant is committed to repo; variant rule YAMLs are temporary `/tmp/` artifacts only.
- Note: NDJSON corpora are temporary `/tmp/` artifacts only.

**`tests/unit/test_export_from_freight_risk.py`** unit tests (against a synthetic SQLite created in a tmp_path fixture):

- Tier 1 priority over tier 2 (explicit `country` column wins over address regex).
- Tier 2 priority over tier 3 (parseable address wins over IP modal).
- Tier 3 concentration threshold (top_share < 0.70 → tier 4 null).
- Tier 3 sample-count threshold (n < 5 → tier 4 null).
- Tier 4 null fallback (no signals available).
- case-3 override hardcoded to "CA" regardless of derivation tier result.
- case-3 `origin_via_carrier_dropoff` hardcoded to True.
- case-2 + approved `origin_via_carrier_dropoff` hardcoded to False.
- Output NDJSON validates against `BookingRequest.model_validate` for every emitted record.
- Random seed reproducibility: two consecutive runs with the same `--seed` produce byte-identical output.
- Per-corpus record counts: 10,000 / 500 / 95.

**Validation**:

- `pytest tests/unit/test_export_from_freight_risk.py -v` — all pass.
- `pytest tests/ --asyncio-mode=auto -q` — full suite passes.
- `ruff check scripts/ tests/` clean.
- **Integration validation (executed in this commit's validation phase)**: run `python3 scripts/calibration/export_from_freight_risk.py --db /Users/drshott/PycharmProjects/miscProj/freight_risk/freight_risk.db --out-dir /tmp/riskd-replay/ --seed 42`. Verify three files exist with expected record counts: `wc -l /tmp/riskd-replay/*.ndjson` reports 10000, 500, 95. Verify schema via `python3 -c "import json; from app.models import BookingRequest; [BookingRequest.model_validate(json.loads(l)) for l in open('/tmp/riskd-replay/approved_jan_mar.ndjson').readlines()[:100]]"`.

**Reviewer routing**: senior-engineer + security-auditor + code-flow + test-reviewer + doc-reviewer.

- security-auditor: external DB read (read-only mode); output path validation; no PII enumeration in committed code.
- doc-reviewer: README's Phase 7 ephemera warning is unambiguous.

**Risk**: MEDIUM. External SQLite read + filesystem writes to `/tmp/`. Mitigated by read-only DB mode + output path validation + schema validation per record.

---

## Batch 7A acceptance criteria

1. `git log --all --oneline | wc -l` reports a strictly lower count vs the pre-7A.0 174 commits.
2. `git grep -E 'scripts/replay|docs/replay-results'` returns no production code references (plan/report MD files OK).
3. `pytest tests/ --asyncio-mode=auto -q` passes after 7A.1.
4. `python3 scripts/calibration/export_from_freight_risk.py --out-dir /tmp/riskd-replay/` produces 3 NDJSON files with the expected record counts.
5. `.gitignore` carries the four defense-in-depth entries.

Operator checkpoint after 7A.2 completes: proceed to PLAN_PHASE_7B.md (variant comparison).
