# PLAN_PHASE_7D — Final validation

> **Phase 7, Batch D.** Re-runs the replay orchestrator against the post-7C calibrated catalogue. Verifies every Phase 7 target is hit; iterates within Phase 7 if any target misses.

## Decisions absorbed

Inherited from PLAN_PHASE_7A.md, PLAN_PHASE_7B.md, PLAN_PHASE_7C.md. Phase 7D-specific:

| Decision | Value | Source |
|---|---|---|
| Phase 7 targets | Approved BLOCK <0.05%, Approved REVIEW <15% (stretch <10%), Case-2 recall ≥95% floor, Case-3b detection ≥85%, `unfamiliar_ip_country_for_origin` <15%, `unknown_destination_address` <20% | Phase 7 prompt |
| Iteration policy | If any target misses, surface to operator via AskUserQuestion. Additional 7C-style commits adjust weights or conditions. Phase 7 CANNOT close with missed targets. | Phase 7 prompt |
| Replay re-export | Re-run `scripts/calibration/export_from_freight_risk.py` to ensure `/tmp/riskd-replay/` is fresh (handles session restart between 7A and 7D) | Phase 7 prompt |
| Mid-pass checkpoint | Operator approval REQUIRED before 7E starts | Phase 7 prompt |

## Batch composition

| Commit | Title | Risk | Reviewer panel |
|---|---|---|---|
| 7D.1 | Final validation against all three corpora + `docs/replay-validation.md` Phase 7D section | MEDIUM (empirical validation; targets may miss) | senior-engineer + doc-reviewer |

---

## Commit 7D.1 — Final validation pass against all three corpora

**Theme**: Measure the post-7C calibrated catalogue against all three corpora. Verify Phase 7 targets are hit. Commit the aggregate-only Phase 7D measurement section.

**Files modified**:

- `docs/replay-validation.md` — append "## Phase 7D final validation" section with the measurements.

**Procedure**:

1. **Pre-flight**: docker-compose stack running with the post-7C `app/rules.yaml` loaded. Verify via `GET /admin/rules-summary` that the rule catalogue reflects the chosen variant AND the new outbound rule AND the absence of the triangle rule.

2. **Re-export corpora**: `python3 scripts/calibration/export_from_freight_risk.py --db /Users/drshott/PycharmProjects/miscProj/freight_risk/freight_risk.db --out-dir /tmp/riskd-replay/ --seed 42`. Verify three NDJSON files exist with the expected record counts (10000, 500, 95). Same seed as Phase 7B → byte-identical corpora.

3. **Three replays** (one per corpus, recorded to separate output files):
   ```
   python3 scripts/replay_validation.py --corpus approved --corpus-dir /tmp/riskd-replay/ --rules app/rules.yaml --tenant-token $REPLAY_TENANT_TOKEN --out /tmp/phase-7d-results/approved.json
   python3 scripts/replay_validation.py --corpus case2 --corpus-dir /tmp/riskd-replay/ --rules app/rules.yaml --tenant-token $REPLAY_TENANT_TOKEN --out /tmp/phase-7d-results/case2.json
   python3 scripts/replay_validation.py --corpus case3 --corpus-dir /tmp/riskd-replay/ --rules app/rules.yaml --tenant-token $REPLAY_TENANT_TOKEN --out /tmp/phase-7d-results/case3.json
   ```

4. **Target verification** — read each result file's aggregate fields and check against the Phase 7 targets:

   | Target | Measurement | Pass if |
   |---|---|---|
   | Approved BLOCK | `approved.json` decision_distribution.BLOCK / requested | < 0.0005 (<0.05%) |
   | Approved REVIEW | `approved.json` decision_distribution.REVIEW / requested | < 0.15 (target) or < 0.10 (stretch) |
   | Case-2 recall | (`case2.json` BLOCK + REVIEW) / requested | ≥ 0.95 |
   | Case-3b detection | (`case3.json` BLOCK + REVIEW) / requested | ≥ 0.85 |
   | `unfamiliar_ip_country_for_origin` rate | `approved.json` per_rule_fire_counts[rule] / requested | < 0.15 |
   | `unknown_destination_address` rate | `approved.json` per_rule_fire_counts[rule] / requested | < 0.20 |

5. **Target evaluation**:

   - **All targets hit (including stretch)**: Phase 7 succeeded on first validation. Proceed to docs commit.
   - **Targets hit, stretch missed (REVIEW between 10% and 15%)**: Phase 7 succeeded on the floor target. Note the stretch miss in the docs section; proceed.
   - **Any target missed**: STOP. Surface to operator via AskUserQuestion. Iterate via additional 7C-style commits (e.g., 7C.5 weight adjustment, 7C.6 condition tightening) — Phase 7 does NOT close until all targets hit.

6. **Docs section** — `docs/replay-validation.md` Phase 7D section:

```markdown
## Phase 7D final validation

Post-7C calibrated catalogue measured against all three corpora.

### Targets-versus-actuals

| Metric | Phase 6C baseline | Phase 7 target | Phase 7D actual | Pass? |
|---|---|---|---|---|
| Approved BLOCK | 0.18% | <0.05% | ... | ... |
| Approved REVIEW | 40.83% | <15% (stretch <10%) | ... | ... |
| Case-2 recall | 98.0% | ≥95% (floor) | ... | ... |
| Case-3b detection | 0.0% | ≥85% | ... | ... |
| `unfamiliar_ip_country_for_origin` fire | 71.83% | <15% | ... | ... |
| `unknown_destination_address` fire | 64.82% | <20% | ... | ... |

### Decision distribution per corpus

| Corpus | ALLOW | REVIEW | BLOCK |
|---|---|---|---|
| approved (10000) | ... | ... | ... |
| case2 (500) | ... | ... | ... |
| case3 (95) | ... | ... | ... |

### Top per-rule fire counts per corpus

| Corpus | Rule | Fires |
|---|---|---|
| approved | ... | ... |
| ... | ... | ... |

(Top 10 rules per corpus; aggregate counts only; no per-record content.)

### Latency observations (replay conditions; not load-test)

| Corpus | p50 | p95 | p99 |
|---|---|---|---|
| approved | ... | ... | ... |
| case2 | ... | ... | ... |
| case3 | ... | ... | ... |

The Phase 6A.10 latency budget (200ms ceiling) is NOT enforced under
replay conditions. Production latency monitoring per
`docs/production-launch-checklist.md` Phase E remains the source of
truth.

### Phase 7 acceptance

[On success]
All Phase 7 targets hit. Phase 7 calibration complete. Phase 8 cleanup
follows.

[On stretch-miss]
All floor targets hit; REVIEW stretch target missed. Operator decision
required to whether to iterate or accept the floor.

[On target miss — only if a target misses]
[Section describes which targets missed and what the next iteration
intervention will look like. Operator approval required to proceed.]
```

**Validation**:

- All three result JSON files exist at `/tmp/phase-7d-results/`.
- Each file's aggregate fields are non-NaN and within expected ranges (sanity check: requested == expected record count).
- The committed Phase 7D section in `docs/replay-validation.md` contains aggregate stats only — `grep -E '"request_id"|"per_transaction"' docs/replay-validation.md` returns empty.
- If any target missed: the section explicitly documents the miss and the next-iteration plan; the commit MUST include the operator-approved iteration commits BEFORE this section claims acceptance.

**Reviewer routing**: senior-engineer + doc-reviewer.

- senior-engineer: target-verification math is correct; the iteration-policy language is unambiguous about Phase 7 closure conditions.
- doc-reviewer: aggregate-only enforcement; the targets-versus-actuals table is the load-bearing artifact for Phase 7's closeout.

**Risk**: MEDIUM. The empirical validation may miss a target. Mitigation: explicit iteration policy + AskUserQuestion gate + operator-approved iteration commits.

---

## Phase 7 iteration policy (if 7D.1 misses a target)

If the chosen Phase 7B variant + the new case-3b rule do not jointly hit every Phase 7 target, Phase 7 does NOT close. The iteration path:

1. **Surface to operator** via AskUserQuestion: "Phase 7D missed target X (actual: Y, required: Z). Proposed interventions: <list>. Which should we apply?"

2. **Operator picks intervention**. Likely shapes:
   - Further weight reduction on a specific rule (`unknown_destination_address` 0.10 → 0.05, etc.).
   - Tighten a maturity gate further (`>= 30` → `>= 50`).
   - Adjust the new case-3b rule's weight if case-3b detection misses (e.g., 0.65 → 0.70 to push it standalone into REVIEW reliably).
   - Add a secondary signal to a rule (similar to Variant D but more targeted).

3. **New atomic commits** in Phase 7C-style (let's call them 7C.5, 7C.6, etc.): each intervention is one commit with full reviewer panel. Re-run validation after each intervention.

4. **Cap**: if the iteration runs more than three rounds without converging, surface the deadlock to the operator. The Phase 7 closeout-or-revert decision is operator-only.

5. Phase 7D's `docs/replay-validation.md` section is rewritten at each iteration to reflect the LATEST measurement. The historical iterations are recorded as subsections (`### Iteration 1`, `### Iteration 2`, etc.) so the audit trail is intact.

---

## Batch 7D acceptance criteria

1. `/tmp/phase-7d-results/{approved,case2,case3}.json` exist.
2. The committed `docs/replay-validation.md` Phase 7D section contains the targets-versus-actuals table fully populated.
3. ALL Phase 7 targets hit (the floor targets at minimum; stretch target optional).
4. No per-record content in the committed section.

Operator checkpoint after 7D.1 completes (and any iteration commits closed): proceed to PLAN_PHASE_7E.md (Phase 7 wrap).
