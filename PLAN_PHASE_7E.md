# PLAN_PHASE_7E — Phase 7 wrap

> **Phase 7, Batch E.** Marks calibration backlog items 1, 2, 6 RESOLVED with resolution pointers, produces the Phase 7 aggregate report, and deletes the `scripts/calibration/` measurement infrastructure now that its job is done.

## Decisions absorbed

Inherited from PLAN_PHASE_7A.md through PLAN_PHASE_7D.md. Phase 7E-specific:

| Decision | Value | Source |
|---|---|---|
| Backlog items resolved | 1 (`unfamiliar_ip_country_for_origin`), 2 (`unknown_destination_address`), 6 (case-3b detection on Roulottes Lupien census) | Phase 7 prompt |
| Backlog items remaining | 3, 4, 5, 7-15 stay post-launch | Phase 7 prompt |
| Cleanup scope | DELETE `scripts/calibration/` entirely (export script, run_variants, README, tests). `.gitignore` entry for the path STAYS (defense-in-depth). | Phase 7 prompt |
| Phase 7 report | `REPORT_PHASE_7.md` at repo root following the Phase 6 report's structure | CLAUDE.md per-phase report convention |
| Phase 8 boundary | Phase 7 closes here. Phase 8 (test suite audit + doc consolidation + migration squash) follows in a separate plan after operator approval. | Phase 7 prompt |

## Batch composition

| Commit | Title | Risk | Reviewer panel |
|---|---|---|---|
| 7E.1 | `docs/calibration-backlog.md` mark items 1, 2, 6 RESOLVED | TRIVIAL | doc-reviewer |
| 7E.2 | `REPORT_PHASE_7.md` aggregate report | TRIVIAL (doc-only) | doc-reviewer + senior-engineer |
| 7E.3 | Delete `scripts/calibration/` + associated tests | LOW (deletions) | senior-engineer + code-flow + test-reviewer + doc-reviewer |

---

## Commit 7E.1 — Calibration backlog updates

**Theme**: Mark items 1, 2, and 6 RESOLVED in `docs/calibration-backlog.md` with resolution pointers to Phase 7's commits. Other items (3, 4, 5, 7-15) remain unchanged — post-launch tuning workstream.

**Files modified**:

- `docs/calibration-backlog.md` — for items 1, 2, 6 append a RESOLVED block under the existing observation:

For item 1 (`unfamiliar_ip_country_for_origin`):
```markdown
**RESOLVED** (Phase 7C, 2026-06-04): Phase 7B four-variant comparison
selected Variant <X> as winner. Applied in PLAN_PHASE_7C.md commit
7C.1. Phase 7D measurement confirmed approved-corpus fire rate
<actual>%, target <15% — pass. See `docs/replay-validation.md`
Phase 7B + 7D sections and `.ai/decisions.md` Phase 7 section for
the full rationale.
```

For item 2 (`unknown_destination_address`): analogous block referencing 7C.1.

For item 6 (case-3b detection):
```markdown
**RESOLVED** (Phase 7C, 2026-06-04): Symmetric triangle compound
deleted (7C.3); new asymmetric `cold_start_outbound_carrier_dropoff`
rule added (7C.2). Phase 7D measurement on the Roulottes Lupien
census: <actual>% detection, target ≥85% — pass. Sub-pattern
recognition driven by the empirically-validated asymmetric attack
shape. Sophisticated population-baseline compound
(`cold_start_population_baseline_rare_with_carrier_dropoff`)
retained unchanged — independent signal class that activates as
tenants accumulate baseline. See `.ai/decisions.md` Phase 7
section.
```

The deferred-action narrative within each resolved item STAYS — operators reading the file should see what the deferred action was, what was implemented, and where to find the decision record. The RESOLVED block is APPENDED, not a replacement of prior content.

**Validation**:

- `grep -E '^\*\*RESOLVED\*\*' docs/calibration-backlog.md | wc -l` returns 3 (one per resolved item).
- doc-reviewer reads end-to-end.

**Reviewer routing**: doc-reviewer.

**Risk**: TRIVIAL.

---

## Commit 7E.2 — `REPORT_PHASE_7.md` aggregate report

**Theme**: The Phase 7 aggregate report at repo root, following the Phase 6 report's structure.

**Files added**:

- `REPORT_PHASE_7.md` — Phase 7 aggregate report.

**Structure**:

```markdown
# REPORT_PHASE_7

Phase 7 — Pre-launch calibration.

## Per-batch composition

[Table: 7A.0 through 7E.3 with one-line per-commit summary]

## Totals

- Commits landed: 12 (or N if iteration occurred)
- Tests added: <N>
- Tests deleted: <N>
- Rules added: 1 (cold_start_outbound_carrier_dropoff)
- Rules deleted: 1 (cold_start_country_triangle_with_carrier_dropoff)
- Rules modified: 2 (per chosen variant)
- ALLOWED_CONTEXT_FIELDS count: 76 → 76 (one field swapped)
- Deletions (LoC, files)

## Per-batch summary

### Batch 7A — Repository hygiene + harness updates
[One-paragraph per commit]

### Batch 7B — Calibration variant measurement
[One-paragraph per commit]

### Batch 7C — Apply chosen variant + case-3b redesign + cleanup
[One-paragraph per commit]

### Batch 7D — Final validation
[One-paragraph per commit including the targets-vs-actuals]

### Batch 7E — Phase 7 wrap
[One-paragraph per commit]

## Reviewer panel verdict distribution

[Table: per-commit reviewer verdicts. Phase 6's report carries this; Phase 7's mirrors the shape]

## Reviewer-caught corrections table

[Table: per-cycle reviewer findings that drove iteration]

## Plan deviations

Decisions absorbed via AskUserQuestion at the verification phase
(2026-06-04):
- Rule weights: 0.35 assumed in prompt → actuals 0.3 / 0.2; variant
  B/C halved targets.
- Maturity gates: prompt's Variant A added a gate that was already
  present; Variant A redesigned to tighten gates to `>= 30`.
- Variant D added by operator: compound-with-secondary-signal.
- Null handling for new case-3b rule: derived bool field pattern.
- `scripts/replay/` directory: delete whole alongside data dir.

Other deviations captured during execution (if any):
- [List]

## Phase 7 readiness assessment

[Targets-vs-actuals table mirrored from PHASE_7D]
[Phase 7 entry conditions met / unmet]
[Production launch gating per `docs/production-launch-checklist.md`:
  Phase 8 cleanup must close before launch]

## Decision trail summary

- `.ai/decisions.md` Phase 7 section: chosen variant + case-3b redesign
  rationale + scope-distinction note.
- `docs/replay-validation.md` Phase 7B/7C/7D sections: measurement
  audit trail.
- `docs/calibration-backlog.md`: items 1, 2, 6 marked RESOLVED.

## Phase 7 sign-off

Phase 7 calibration complete. Phase 8 (test suite audit + doc
consolidation + migration squash) is the next pass. Production
launch follows Phase 8 close.

Pointer to Phase 8: NO plan file exists yet. Operator opens
Phase 8 when ready.
```

**Validation**:

- doc-reviewer reads end-to-end.
- Citations in the report match the actual commit hashes (the report references commits via batch number, not SHA, so re-orderings remain valid).
- The report contains aggregate stats only.

**Reviewer routing**: doc-reviewer + senior-engineer.

- doc-reviewer: structure, accuracy, no per-record content.
- senior-engineer: technical accuracy of the targets-vs-actuals and the deviations narrative.

**Risk**: TRIVIAL.

---

## Commit 7E.3 — Delete `scripts/calibration/` measurement infrastructure

**Theme**: Phase 7's measurement infrastructure has done its job. Remove the export script, the variant runner, the README, and their unit tests. The `.gitignore` entry for the path STAYS as defense-in-depth.

**Files deleted**:

- `scripts/calibration/__init__.py`
- `scripts/calibration/export_from_freight_risk.py`
- `scripts/calibration/run_variants.py`
- `scripts/calibration/README.md`
- `tests/unit/test_export_from_freight_risk.py`
- `tests/unit/test_run_variants.py`

`scripts/calibration/` becomes empty; the directory itself is removed via `rmdir` (git tracks files, not empty dirs, but the deletion is complete).

**Files NOT deleted**:

- `.gitignore`'s `scripts/calibration/` entry — STAYS. Defense-in-depth for any future re-run.
- `/tmp/riskd-replay/` and `/tmp/rules-variants/` and `/tmp/phase-7b-results/` and `/tmp/phase-7d-results/` — these are /tmp; the OS may have already cleaned them up depending on session state. NOT committed; not in scope of git.

**Verification before commit**:

- `git grep -E 'scripts\.calibration|from scripts\.calibration|scripts/calibration'` returns NO matches in `app/`, `tests/` (other than the deleted files), or any committed code/config.
- `pytest tests/ --asyncio-mode=auto -q` — full suite passes (the deleted tests are gone; remaining tests don't reference the deleted modules).
- `ruff check app/ tests/ scripts/` clean.

**Validation**:

- `ls scripts/calibration/ 2>&1` returns "No such file or directory".
- `git grep -E 'scripts\.calibration'` returns empty.
- Full test suite passes.

**Reviewer routing**: senior-engineer + code-flow + test-reviewer + doc-reviewer.

- senior-engineer: deletion scope is fully enumerated; no orphaned references.
- code-flow: no production code path references the deleted directory.
- test-reviewer: no test file imports symbols from deleted modules.
- doc-reviewer: the gitignore entry retention is justified in `.gitignore`'s inline comment (already added in 7A.0).

**Risk**: LOW. Deletion of Phase 7 ephemera with explicit retention of the defense-in-depth gitignore line.

---

## Batch 7E acceptance criteria

1. `docs/calibration-backlog.md` items 1, 2, 6 carry RESOLVED blocks with Phase 7 commit pointers.
2. `REPORT_PHASE_7.md` exists at repo root with the full Phase 7 aggregate.
3. `scripts/calibration/` no longer exists.
4. `.gitignore` retains its `scripts/calibration/` defense-in-depth entry.
5. `pytest tests/ --asyncio-mode=auto` — full suite passes.

Phase 7 closes after 7E.3 commits. Phase 8 cleanup is the next pass.

---

## Phase 7 end-of-pass

Phase 7's outcome (if all targets hit at 7D.1):

- Two FPR-driving rules calibrated per the operator-selected variant.
- Case-3b detection redesigned (asymmetric compound) with empirical validation on the Roulottes Lupien census.
- Triangle compound deleted; ALLOWED_CONTEXT_FIELDS net unchanged at 76.
- Aggregate measurement docs published; per-record content NOT committed anywhere.
- Repository history scrubbed of all freight_risk-derived data.

Production launch readiness is a Phase 8 closeout concern. Phase 7's job is the calibration; Phase 8 cleans the repo (test audit + doc consolidation + migration squash); launch readiness is the operator's call at Phase 8 close.

Operator owns the launch decision.
