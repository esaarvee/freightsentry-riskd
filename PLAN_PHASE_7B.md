# PLAN_PHASE_7B — Calibration variant measurement

> **Phase 7, Batch B.** Generates four rule variants (A, B, C, D) programmatically and runs the replay orchestrator against all three corpora for each variant. Commits aggregate results only — variant rule files live in `/tmp/` and are NEVER committed.

## Decisions absorbed

Inherited from PLAN_PHASE_7A.md. Phase 7B-specific:

| Decision | Value | Source |
|---|---|---|
| Variants measured | A (tightened gate), B (halved weights), C (combined), D (compound-with-secondary-signal) | Operator AskUserQuestion 2026-06-04 |
| Variant rule files location | `/tmp/rules-variants/{a,b,c,d}.yaml` — never committed | Phase 7 prompt + operator NDJSON-only policy |
| Variant replay results | `/tmp/phase-7b-results/{a,b,c,d}-{approved,case2,case3}.json` — never committed | Same policy |
| Committed artifact | `docs/replay-validation.md` Phase 7B section — aggregate-only table | Phase 7 prompt |
| Variant rule swap mechanism | Copy variant YAML onto `app/rules.yaml`, restart docker-compose `app` service, run replay, restore original from git via `git restore app/rules.yaml`. Repeat. | Inferred from app lifespan rule loading |
| Mid-pass checkpoint | Operator approval REQUIRED before 7C starts (operator picks the winning variant) | Phase 7 prompt |

## Batch composition

| Commit | Title | Risk | Reviewer panel |
|---|---|---|---|
| 7B.1 | Variant comparison harness implementation + execution + replay-validation.md Phase 7B section | LOW (measurement-only; no rule changes committed) | senior-engineer + code-flow + test-reviewer + doc-reviewer |

---

## Commit 7B.1 — Four-variant comparison + execution + measurement document

**Theme**: Implement `scripts/calibration/run_variants.py` (stub created in 7A.2), execute the four-variant measurement against all three corpora, write Phase 7B section of `docs/replay-validation.md` with aggregate comparison tables.

**Files modified**:

- `scripts/calibration/run_variants.py` — full implementation (replaces 7A.2's stub).
- `tests/unit/test_run_variants.py` — new file. Tests the variant-YAML generation logic (no I/O against docker-compose; mocks the orchestrator subprocess).
- `docs/replay-validation.md` — append "## Phase 7B variant comparison" section with the aggregate tables.

### Variant rule generation

`run_variants.py` reads the base `app/rules.yaml` and writes four variants to `/tmp/rules-variants/`. Two rules are modified per variant; all other rules pass through unchanged. The script uses `ruamel.yaml` (round-trip preserves comments) OR a focused string-replacement (simpler; the two rules' blocks are easy to locate by name) — the implementation picks the simpler path.

**Variant A — Tightened maturity gate, weights unchanged**:

```yaml
- name: unfamiliar_ip_country_for_origin
  description: "Origin paired with an unseen IP country for this established customer"
  condition: "NOT origin_ip_country_familiar AND customer_observations >= 30"
  weight: 0.3
  maturity_sensitive: true

- name: unknown_destination_address
  description: "Destination address not seen before for this established customer"
  condition: "NOT destination_address_familiar AND customer_observations >= 30"
  weight: 0.2
  maturity_sensitive: true
```

**Variant B — Halved weights, gates unchanged at `>= 10`**:

```yaml
- name: unfamiliar_ip_country_for_origin
  condition: "NOT origin_ip_country_familiar AND customer_observations >= 10"  # unchanged
  weight: 0.15  # 0.3 → 0.15

- name: unknown_destination_address
  condition: "NOT destination_address_familiar AND customer_observations >= 10"  # unchanged
  weight: 0.10  # 0.2 → 0.10
```

**Variant C — Combined (gates tightened to `>= 30` AND weights halved)**:

```yaml
- name: unfamiliar_ip_country_for_origin
  condition: "NOT origin_ip_country_familiar AND customer_observations >= 30"
  weight: 0.15

- name: unknown_destination_address
  condition: "NOT destination_address_familiar AND customer_observations >= 30"
  weight: 0.10
```

**Variant D — Compound with secondary signal, gates at `>= 10`, weights unchanged**:

```yaml
- name: unfamiliar_ip_country_for_origin
  description: "Origin paired with an unseen IP country for this established customer AND a corroborating IP-quality signal"
  condition: "NOT origin_ip_country_familiar AND customer_observations >= 10 AND (is_vpn OR is_proxy OR ip2p_threat_any OR ip_in_threat_list OR is_datacenter_ip)"
  weight: 0.3
  maturity_sensitive: true

- name: unknown_destination_address
  description: "Destination address not seen before for this established customer AND value above tenant medium tier"
  condition: "NOT destination_address_familiar AND customer_observations >= 10 AND shipment_value > shipment_value_threshold_medium"
  weight: 0.2
  maturity_sensitive: true
```

Variant D's condition fields are all already present in `ALLOWED_CONTEXT_FIELDS` (`is_vpn`, `is_proxy`, `ip2p_threat_any`, `ip_in_threat_list`, `is_datacenter_ip`, `shipment_value`, `shipment_value_threshold_medium`) — no whitelist extension needed. The script verifies this pre-flight by calling `app.rules.load_rules` on each generated variant; if any variant fails to load, the script aborts with the loader error.

### Orchestration mechanism

For each variant ∈ {A, B, C, D} and each corpus ∈ {approved, case2, case3}:

1. Restore baseline: `git restore app/rules.yaml` (ensures a clean starting state).
2. Copy variant: `cp /tmp/rules-variants/{variant}.yaml app/rules.yaml`.
3. Restart app: `docker compose restart app`. Wait for healthcheck: poll `GET /health` with 1s intervals, fail after 30s.
4. Verify the running app loaded the variant: `GET /admin/rules-summary` (existing endpoint) and assert the two modified rules have the variant's exact `weight` value.
5. Run replay: `python3 scripts/replay_validation.py --corpus {corpus} --corpus-dir /tmp/riskd-replay/ --rules app/rules.yaml --tenant-token $REPLAY_TENANT_TOKEN --out /tmp/phase-7b-results/{variant}-{corpus}.json`.
6. Verify exit code 0 and output JSON validates.

After all 12 runs complete (4 variants × 3 corpora):

7. Restore baseline FINAL: `git restore app/rules.yaml`. Restart app one last time to return to baseline.
8. Aggregate results into the docs/replay-validation.md Phase 7B section.

### Variant rule swap safety

The mechanism modifies a tracked file (`app/rules.yaml`) during the run. Safety properties:

- The run-variants script is invoked with a clean working tree (precondition asserted via `git status --porcelain` returning empty before step 1).
- Every variant iteration BEGINS with `git restore app/rules.yaml` — even if a prior iteration crashed mid-step.
- The script wraps the per-variant logic in a `try / finally` that runs `git restore app/rules.yaml` + `docker compose restart app` on exit, so a crashed run leaves the working tree clean and the app on baseline.
- If `git restore` fails (e.g., the user has uncommitted changes to `app/rules.yaml`), the script ABORTS immediately and refuses to run any variant.

### Aggregate output format

The committed Phase 7B section of `docs/replay-validation.md` is aggregate-only:

```markdown
## Phase 7B variant comparison

Baseline (Phase 6C, no calibration):
- Approved corpus: 41% REVIEW / 0.18% BLOCK
- Case-2 recall: 98%
- Case-3b detection: 0%

### Decision-band outcomes

| Variant | Approved REVIEW % | Approved BLOCK % | Case-2 recall % | Case-3b detection % |
|---|---|---|---|---|
| Baseline | 40.83 | 0.18 | 98.0 | 0.0 |
| A — gate `>= 30`, weights unchanged | ... | ... | ... | ... |
| B — gates unchanged, weights halved | ... | ... | ... | ... |
| C — combined | ... | ... | ... | ... |
| D — compound with secondary signal | ... | ... | ... | ... |

### Per-rule fire rates on approved corpus

| Rule | Baseline | A | B | C | D |
|---|---|---|---|---|---|
| `unfamiliar_ip_country_for_origin` | 71.83% | ... | ... | ... | ... |
| `unknown_destination_address` | 64.82% | ... | ... | ... | ... |

### Per-rule fire rates on case-2 corpus

| Rule | Baseline | A | B | C | D |
|---|---|---|---|---|---|
| `unfamiliar_ip_country_for_origin` | 96.0% | ... | ... | ... | ... |
| `unknown_destination_address` | 96.0% | ... | ... | ... | ... |

### Per-rule fire rates on case-3b corpus

| Rule | Baseline | A | B | C | D |
|---|---|---|---|---|---|
| `unfamiliar_ip_country_for_origin` | 89.5% | ... | ... | ... | ... |
| `unknown_destination_address` | 86.3% | ... | ... | ... | ... |

### Phase 7 targets at-a-glance

Targets (from Phase 7 prompt):
- Approved-corpus BLOCK rate <0.05% (from 0.18%)
- Approved-corpus REVIEW rate <15% target / <10% stretch (from 41%)
- Case-2 recall ≥95% floor (from 98%)
- Case-3b detection ≥85% (from 0%)
- `unfamiliar_ip_country_for_origin` fire rate <15% (from 72%)
- `unknown_destination_address` fire rate <20% (from 65%)

Note: case-3b detection is NOT expected to improve from variant testing alone — variants only adjust the two FPR-driving rules. Case-3b improvement comes from 7C.2 (new compound). The Phase 7B variant table shows variant impact on the FPR-driving rules; the case-3b column should remain ~0% across all four variants and is included for transparency, not for variant selection.
```

The actual numbers fill in during execution; the table structure is committed.

### `tests/unit/test_run_variants.py` test cases

- Variant A YAML generation: assert the two rule blocks have the expected gate tightening, weights unchanged.
- Variant B YAML generation: gates unchanged, weights `0.15` / `0.10`.
- Variant C YAML generation: gates `>= 30`, weights `0.15` / `0.10`.
- Variant D YAML generation: conditions include the secondary-signal compound; weights unchanged at `0.3` / `0.2`.
- Each generated variant YAML loads cleanly via `app.rules.load_rules` (no whitelist violations, no DSL errors).
- The script ASSERTS clean working tree before invocation (mock `git status` returning dirty → abort).
- The script restores `app/rules.yaml` on KeyboardInterrupt (mock the orchestrator subprocess to raise; verify the cleanup branch runs).
- All variants land in `/tmp/rules-variants/`, NEVER under `app/` or `scripts/`.

These tests do NOT run docker-compose or the orchestrator subprocess. The full variant-execution path is exercised manually in this commit's validation phase (an in-loop docker-compose stack with the export-script-produced corpora at /tmp/riskd-replay/).

### Variant orchestration constraint

`scripts/replay_validation.py`'s `--rules PATH` argument records WHICH file the orchestrator was told about but does NOT itself swap rules in the running app. The app loads `app/rules.yaml` at FastAPI lifespan startup; rule changes require an app restart. `run_variants.py` performs that restart explicitly. The `--rules` metadata argument exists so the output JSON carries the variant identity for audit; it is NOT a runtime rule selector.

### Mid-pass checkpoint

**Operator approval REQUIRED before 7C starts.** The operator reviews the Phase 7B section and selects the winning variant (A, B, C, or D). The default expectation per Phase 7 prompt is C (combined), but the empirical data drives the decision; the operator can request a fifth variant if none of the four hit the Phase 7 targets.

### Validation

- `pytest tests/unit/test_run_variants.py -v` — all pass.
- `pytest tests/ --asyncio-mode=auto -q` — full suite passes.
- `ruff check scripts/ tests/` clean.
- **Integration**: full variant run executes end-to-end against the local docker-compose stack with `/tmp/riskd-replay/` populated from 7A.2. Verify all 12 result JSON files exist and contain non-NaN aggregate fields. Verify `app/rules.yaml` returns to baseline after the run (`git diff app/rules.yaml` is empty).
- The committed `docs/replay-validation.md` Phase 7B section contains aggregate stats only — `grep -E '"request_id"|request_id:' docs/replay-validation.md` returns empty.

**Reviewer routing**: senior-engineer + code-flow + test-reviewer + doc-reviewer.

- senior-engineer: variant generation correctness; `try/finally` cleanup logic on the rule-file swap.
- code-flow: no production-path mutation lingers after the run.
- test-reviewer: unit tests cover the variant YAML shapes, not just smoke.
- doc-reviewer: the committed section is aggregate-only, narrative cites the per_rule fire rates correctly, the case-3b column is annotated as expected-flat-across-variants.

**Risk**: LOW. Measurement-only; no committed rule changes; the working-tree restore safety is explicit.

---

## Batch 7B acceptance criteria

1. `scripts/calibration/run_variants.py` is a fully-implemented script (not a stub).
2. `/tmp/rules-variants/{a,b,c,d}.yaml` exist and load cleanly via `app.rules.load_rules`.
3. `/tmp/phase-7b-results/` contains 12 JSON files (4 variants × 3 corpora).
4. `docs/replay-validation.md` Phase 7B section is committed with aggregate tables fully populated.
5. `git diff app/rules.yaml` returns empty after the run (working tree clean).
6. The aggregate tables cite NO per-record content.

Operator checkpoint after 7B.1 completes: operator picks the winning variant. Proceed to PLAN_PHASE_7C.md.
