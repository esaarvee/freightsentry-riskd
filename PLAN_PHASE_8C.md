# PLAN_PHASE_8C — Documentation audit and consolidation

Phase 8 batch 8C. Restructures current-state docs, deletes superseded ones, creates a single `docs/history.md` absorbing PLAN/REPORT/MASTER_PLAN narratives and historical sections moved out of current-state docs. Production launch readiness is the gate.

## Decisions absorbed

| Decision | Resolution | Source |
|---|---|---|
| history.md target length | 1200-1800 lines (~150-200 per phase). Reflects actual PLAN/REPORT inventory of 48 files, not the prompt's assumed 14. | Operator AskUserQuestion |
| REPORT_PHASE_7.md handling | Generate as part of 8C (commit 8C.11). Mirrors the per-phase REPORT pattern present for Phases 1-6. Source material: PLAN_PHASE_7*.md + `.ai/decisions.md` Phase 7 sections + `docs/replay-validation.md` Phase 7C/7D sections + git log for commits 7A.0 through 7C.13. | Operator AskUserQuestion |
| rules.md catalogue scope | Write Phase 4-7 rule catalogue from scratch in 8C.3 (current rules.md catalogue stops at Phase 3). Target length ~600-900 lines (up from prompt's 400-600). | Operator AskUserQuestion |
| calibration-backlog.md restructure | Minimal: update each item's current status (active/partial/resolved); reorder for thematic clarity only; do NOT archive items 1, 2 to history.md (the partial weight reductions remain as live monitoring items). | Operator AskUserQuestion |
| MASTER_PLAN.md handling | Delete; absorb introduction + cross-phase invariants section summary into history.md. Update CLAUDE.md + system-status.md cross-references. | Operator AskUserQuestion |
| security-audit-rls-phase-5.md retention | Keep (operational reference for the runtime role + RLS setup). Per Phase 8 prompt explicit allowance. | Phase 8 prompt §S-3 |
| Operational runbooks | Light edits only on production-launch-checklist.md, aws-deploy-runbook.md, load-test-phase-5.md. Trim verbose phase-preambles; keep SQL/CLI snippets verbatim. | Phase 8 prompt §S-3 + §Quality 5 |
| Cross-reference cleanup discipline | Every deletion commit greps for refs to the deleted file across `app/`, `tests/`, `scripts/`, `docs/`, `.ai/`, `CLAUDE.md`, `alembic/`. Broken refs fixed in the same commit. | Phase 8 prompt §Scope 6 |
| Atomic commit cadence | Each commit operates on one doc (or one tight grouping). Multi-doc commits only where the split would create broken cross-references in the intermediate state. | MEMORY.md feedback_atomic_commits |
| docs/runbooks/ subdirectory | Not currently used; production-launch-checklist.md lives directly under `docs/`. 8C does not introduce the subdirectory (out-of-scope reshuffle). | Repo state |
| Reviewer panel routing | Doc-reviewer mandatory on every commit. Senior-engineer added on doc rewrites that paraphrase code (schema.md, rules.md). DB-reviewer added on schema.md (V-2 paraphrase). | CLAUDE.md triage routing |

## Pre-batch verification

Completed during the Phase 8 verification phase. Key findings (see PLAN_PHASE_8A.md verification section for the full report):

- 28 PLAN files + 19 REPORT files + MASTER_PLAN.md at repo root (48 docs to delete).
- REPORT_PHASE_7.md does NOT exist (8C.11 generates it).
- `.ai/system-status.md` stuck at "Phase 1 in progress" (V-8).
- `.ai/schema.md` H2 header is "Phase 1 tables (12)"; no Phase 6/7 additions (V-8).
- `.ai/rules.md` catalogue ends at "Modification-specific (Phase 3)"; Phase 4-7 rules entirely absent (V-9).
- `docs/calibration-backlog.md` has 20 items, only items 1, 2 are explicitly PARTIAL (V-10).
- Cross-references to PLAN_PHASE_*/REPORT_PHASE_*/MASTER_PLAN: 7 hits across CLAUDE.md (3), `.ai/system-status.md`, `.ai/decisions.md` (3), `docs/verification-phase-1.md`, `docs/replay-validation.md`, `docs/load-test-phase-5.md` (V-7, V-11).

## Commits

### 8C.1 — Restructure `.ai/decisions.md` to topic-organized current-architecture

**Changes**:
- Read current `.ai/decisions.md` (108K, ~3000 lines per the inventory size; structure is chronological-by-phase).
- Reorganize sections to topic-organized current-architecture:
  - Scoring architecture (3-layer noisy-OR + maturity downweight + cold-start grace)
  - Customer baseline (JSONB schema; Welford triples; decay; ALLOW-gated accumulation)
  - RLS multi-tenancy (current state: tenants/customers/users/shipments/decisions/feedback/customer_baselines/tenant_route_baselines all have RLS; api_tokens/app_users do NOT)
  - Authentication and tokens (api_tokens lookup; riskd_app_login runtime role)
  - IP enrichment (cache + MaxMind GeoIP integration)
  - Rule catalogue overview (links to `.ai/rules.md` for full catalogue)
  - Cold-start and maturity (per-tenant overrides; population baseline; grace multiplier)
  - Modification evaluation (Phase 3A architecture)
  - Feedback ingestion (Phase 3B + 7C.11 fold-on-approved-label)
  - Production observability (CloudWatch EMF; METRIC_SPECS taxonomy)
- Each topic: current state + brief rationale + reference to `docs/history.md` (forward-reference; created in 8C.12) for full historical reasoning.
- Historical detail (Phase-N "Why we chose X" narratives) extracted to `/tmp/history_drafts/decisions_history.md` as staging for 8C.12.
- Cross-references to `MASTER_PLAN.md`, `REPORT_PHASE_*.md`, `PLAN_PHASE_*.md` removed (they become broken in 8C.13).
- Length target: ~400-700 lines (down from current ~3000).

**Tests**: 0 (doc commit).

**Validation**:
- `cat .ai/decisions.md | wc -l` shows target range.
- `grep -n '^## ' .ai/decisions.md` lists topic sections.
- doc-reviewer panel.

**Declared breaks**:
- Cross-references to history.md introduce forward-references that resolve in 8C.12. Scope: `[history.md]` link placeholders in decisions.md.
- Resolved in: 8C.12 creates `docs/history.md`.

**Reviewer panel**: doc-reviewer + senior-engineer (architecture content paraphrases load-bearing code).

### 8C.2 — Rewrite `.ai/schema.md` against post-squash schema

**Changes**:
- Read post-squash migrations (5 files from 8A).
- Rewrite `.ai/schema.md`:
  - H2 sections per post-squash migration (foundation, booking_flow, baselines, enrichment_global, runtime_roles).
  - Per migration: tables, columns (with comments), indexes, RLS policies, GRANTs.
  - Stat-dict entry shape section (preserved from current schema.md, updated for current customer_baselines JSONB additions).
  - RLS pattern section (preserved; document the api_tokens/app_users exemption explicitly).
  - Index strategy section (preserved + Phase 6/7 additions).
  - Migration discipline section (updated for the squash; reference `tests/integration/test_schema_golden.py` as the anti-drift gate).
- Cross-references to `.ai/decisions.md` topic sections (post-8C.1 structure).
- Length target: ~500-800 lines.

**Tests**: 0 (doc commit). The schema golden test from 8A.0 already verifies what schema.md describes.

**Validation**:
- All current tables + columns documented.
- doc-reviewer + db-reviewer panel.

**Declared breaks**: none.

**Reviewer panel**: doc-reviewer + senior-engineer + db-reviewer.

### 8C.3 — Rewrite `.ai/rules.md` (Phase 4-7 catalogue from scratch)

**Changes**:
- Read `.ai/rules.md`. Confirm current catalogue stops at "Modification-specific (Phase 3)".
- Keep + verify: Scoring model section, DSL grammar section, YAML rule shape section, Context fields section.
- **Update**: Context fields section enumeration — bring current to `ALLOWED_CONTEXT_FIELDS` (77 fields per `app/rules.py`); add the Phase 4-7 additions (carrier dropoff, tenant route, customer registered country, etc.).
- **Add**: Phase 4 rules subsection (cold-start grace, tuned thresholds, per-tenant maturity overrides).
- **Add**: Phase 5 rules subsection (any new rules from Phase 5 — verify via `git log app/rules.yaml`).
- **Add**: Phase 6 rules subsection (cold_start_outbound_carrier_dropoff, cold_start_population_baseline_rare_with_carrier_dropoff, customer_destination_country_mismatch_outbound — case-3a + case-3b).
- **Add**: Phase 7 rules subsection (api_booking_from_unfamiliar_asn — case-2 learning-based; weight calibrations on unfamiliar_ip_country_for_origin (0.15) + unknown_destination_address (0.10)).
- **Document deletion**: cold_start_country_triangle_with_carrier_dropoff (deleted in 7C.2; in rules.yaml only as comment) — note in history.md, NOT in rules.md.
- Cross-references to `app/rules.yaml` for authoritative weights.
- Length target: ~600-900 lines (up from current ~12K bytes / ~400 lines; nearly doubled to accommodate Phase 4-7).

**Tests**: 0 (doc commit).

**Validation**:
- Every rule name in current `app/rules.yaml` is documented in rules.md.
- Every Context field in `ALLOWED_CONTEXT_FIELDS` is documented.
- Weights cited match `app/rules.yaml` current values.
- doc-reviewer + senior-engineer panel.

**Declared breaks**: none.

**Reviewer panel**: doc-reviewer + senior-engineer (paraphrases rules.yaml + DSL).

### 8C.4 — Refresh `.ai/system-status.md`

**Changes**:
- Rewrite to actual pre-launch state.
- Stage description: "Pre-launch. Phase 8 cleanup pass in progress; production deploy to ca-central-1 upcoming."
- Phase status table: Phases 1-7 complete with one-line outcomes; Phase 8 in progress.
- Anti-drift gates section (new): `tests/integration/test_schema_golden.py` (from 8A.0), `tests/coverage_baseline.txt` (from 8B.0), CI lint/type/test gates (from `.github/workflows/test.yml`).
- Updates: remove MASTER_PLAN.md reference (it's deleted in 8C.13); replace with reference to `docs/history.md` for historical context.
- Updates: remove `PLAN_PHASE_{N}.md` and `REPORT_PHASE_{N}.md` per-batch references; these files are deleted in 8C.13.
- Length target: ~120-180 lines.

**Tests**: 0 (doc commit).

**Validation**: doc-reviewer panel.

**Declared breaks**:
- Forward-reference to `docs/history.md` resolves in 8C.12.
- Resolved in: 8C.12.

**Reviewer panel**: doc-reviewer.

### 8C.5 — Trim `docs/replay-validation.md`

**Changes**:
- Read current `docs/replay-validation.md` (~41K bytes / ~900 lines).
- KEEP IN FULL: Phase 7D final measurement section (current operational record; load-bearing for production launch audit trail).
- KEEP IN FULL: Methodology section (load-bearing for future calibration cycles).
- KEEP: Acceptance gates section, updated for Phase 7E decisions (BLOCK target retired; case-2 per-customer framing).
- TRIM to 1-paragraph summary: Phase 7C variant-comparison + intervention application (~200 lines → ~30 lines); full detail moved to `/tmp/history_drafts/replay_history.md` for 8C.12.
- TRIM to 2-3 paragraph summary: Phase 7B variant testing.
- TRIM aggressively: Phase 6C measurement (the rules being measured no longer exist post-7C.2); 1 paragraph + history.md pointer.
- Length target: ~250-400 lines.

**Tests**: 0 (doc commit).

**Validation**:
- Phase 7D final measurement intact.
- Methodology intact.
- doc-reviewer panel.

**Declared breaks**:
- Forward-reference to `docs/history.md` resolves in 8C.12.
- Resolved in: 8C.12.

**Reviewer panel**: doc-reviewer.

### 8C.6 — Update `docs/calibration-backlog.md` (minimal restructure)

**Changes**:
- Per-operator decision: minimal restructure, status updates only; keep all 20 items.
- For each item: verify current status against the most recent commit; mark "Active", "Partial", "Resolved", or "Deferred to post-launch tuning roadmap" as appropriate.
- Items 1, 2: stay PARTIAL (weight reductions in 7C.8); add monitoring note about post-launch FPR re-measurement at 5-month mark.
- Items 11, 15-20: explicitly tag as "Post-launch tuning roadmap" per Phase 7E close decision.
- Items 7, 17: tag as "Architectural workstream"; deferred unless launch evidence demands.
- Add a brief preamble explaining the post-launch tuning model (1-paragraph; refers to history.md for the Phase 7E close-decision context).
- Length target: ~250-350 lines (some growth from status annotations; no aggressive trim).

**Tests**: 0 (doc commit).

**Validation**: doc-reviewer panel.

**Declared breaks**:
- Forward-reference to `docs/history.md` resolves in 8C.12.
- Resolved in: 8C.12.

**Reviewer panel**: doc-reviewer.

### 8C.7 — Light-audit `docs/production-launch-checklist.md`

**Changes**:
- Per-phase preambles trimmed where verbose. E.g., "Phase 7C.11 added monitoring item..." → "Monitor held-booking backlog."
- Operational queries (SQL snippets) preserved verbatim.
- Operational acceptance criteria preserved verbatim.
- Cross-references to `PLAN_PHASE_*.md` / `REPORT_PHASE_*.md` / `MASTER_PLAN.md` removed or redirected to `docs/history.md`.
- Updates: reference the anti-drift gates established in 8A.0 / 8B.0 (schema golden + coverage baseline).

**Tests**: 0 (doc commit).

**Validation**: doc-reviewer panel.

**Declared breaks**: none (no forward references; checklist is self-contained).

**Reviewer panel**: doc-reviewer.

### 8C.8 — Update `.ai/enrichment.md` for 7C.11 ALLOW-only baseline gating

**Changes**:
- Locate the baseline section describing `add_observation` (or the equivalent customer_baselines update path).
- Update to reflect that the update runs only on ALLOW decisions; REVIEW/BLOCK held pending operator feedback.
- Cross-reference: `app/api/booking.py:207` (the conditional gate); `.ai/decisions.md` topic section "Customer baseline" (post-8C.1).
- Light touch; most of enrichment.md is still accurate.

**Tests**: 0 (doc commit).

**Validation**: doc-reviewer panel.

**Declared breaks**: none.

**Reviewer panel**: doc-reviewer.

### 8C.9 — Verify `docs/observability.md` METRIC_SPECS currency

**Changes**:
- Read `app/observability.py::METRIC_SPECS` (or wherever metric specs live).
- Compare to the table in `docs/observability.md`.
- If any Phase 6/7 metric was added (per `git log app/observability.py`): document it.
- If any was removed: remove from the doc.
- Otherwise: no changes (likely path; if so, commit is a no-op verification noted in PLAN_PHASE_8C.md).

**Tests**: 0 (doc commit).

**Validation**: doc-reviewer panel.

**Declared breaks**: none.

**Reviewer panel**: doc-reviewer.

### 8C.10 — Delete superseded audit + verification docs

**Changes**:
- Read each of: `docs/security-audit-rls-phase-3.md`, `docs/security-audit-rls-phase-4.md`, `docs/verification-phase-1.md`, `docs/initial-audit.md`.
- For each: extract any content with unique current-state value (most should have none, since phase-5 audit supersedes phase 3/4; verification-phase-1 is a Phase 1 artifact; initial-audit was the project-setup audit).
- Unique content (if any) absorbed into `/tmp/history_drafts/audit_history.md` for 8C.12.
- `git rm docs/security-audit-rls-phase-3.md docs/security-audit-rls-phase-4.md docs/verification-phase-1.md docs/initial-audit.md`.
- Grep cross-references: `grep -rn 'security-audit-rls-phase-3\|security-audit-rls-phase-4\|verification-phase-1\|initial-audit' app/ tests/ scripts/ docs/ .ai/ CLAUDE.md alembic/ 2>/dev/null`. Fix or remove any matches.

**Tests**: 0.

**Validation**:
- Files absent from working tree.
- `grep -rln 'security-audit-rls-phase-3\|security-audit-rls-phase-4\|verification-phase-1\|initial-audit' .` returns empty (or only the history.md draft).
- doc-reviewer panel.

**Declared breaks**: none.

**Reviewer panel**: doc-reviewer.

### 8C.11 — Generate REPORT_PHASE_7.md

**Changes**:
- Create `REPORT_PHASE_7.md` at repo root (mirrors REPORT_PHASE_1 through REPORT_PHASE_6 pattern).
- Source material: PLAN_PHASE_7A.md through 7E.md narratives; `.ai/decisions.md` Phase 7 sections (pre-8C.1 restructure — capture before they're moved); `docs/replay-validation.md` Phase 7C/7D sections; `git log` commits 7A.0 through 7C.13 + 58d155e.
- Structure mirrors REPORT_PHASE_6.md: Phase goals; per-batch outcomes; calibration results; close-decisions; carry-forward.
- Length target: matches REPORT_PHASE_6.md (~23K bytes / ~500 lines).
- This file will be deleted in 8C.13 alongside the other REPORT files (and absorbed into history.md in 8C.12); creating it here gives 8C.12 a consistent input set.

**Tests**: 0 (doc commit).

**Validation**: doc-reviewer panel.

**Declared breaks**:
- File created in 8C.11; deleted in 8C.13 after absorption. Scope: `REPORT_PHASE_7.md` at repo root.
- Resolved in: 8C.12 (absorption) + 8C.13 (deletion).

**Reviewer panel**: doc-reviewer + senior-engineer (Phase 7 architectural content fidelity).

### 8C.12 — Create `docs/history.md`

**Changes**:
- Create `docs/history.md` consolidating all historical sources:
  - PLAN_PHASE_1.md, 2A-2D, 3A-3D, 4A-4D, 5A-5D, 6A-6E, 7A-7E narratives (28 files → ~150-200 lines per phase, summarizing key decisions, blockers, batch outcomes; ~150 lines/phase × 7 phases ≈ 1050 lines for phase narratives alone).
  - REPORT_PHASE_1.md, 2, 2A-2D, 3, 3A-3D, 4, 4_RETRO, 4A-4D, 5, 5A-5D, 6 (19 files; outcomes per phase merge into the same phase section in history.md).
  - REPORT_PHASE_7.md from 8C.11.
  - MASTER_PLAN.md cross-phase invariants + introduction (~50-100 lines into history.md's introduction).
  - `/tmp/history_drafts/decisions_history.md` (from 8C.1).
  - `/tmp/history_drafts/replay_history.md` (from 8C.5).
  - `/tmp/history_drafts/audit_history.md` (from 8C.10, if non-empty).
- Structure:
  - Introduction (~50 lines): document purpose; pointer to current-state docs (`.ai/system-status.md`, `.ai/decisions.md`, `.ai/schema.md`, `.ai/rules.md`); MASTER_PLAN.md absorption note.
  - Phase 1 (~150 lines): goals, decisions, outcomes, measurement state at close.
  - Phase 2 (~150 lines): same structure.
  - Phase 3 (~180 lines): includes Phase 4 retro absorption.
  - Phase 4 (~150 lines): includes Phase 4 retro decisions.
  - Phase 5 (~180 lines): security hardening; runtime role transition; load test; deploy infra.
  - Phase 6 (~250 lines): case-3a + case-3b detection; tenant_route_baselines; replay-validation methodology.
  - Phase 7 (~250 lines): calibration; case-2 learning-based rule; weight calibration; close decisions (BLOCK target retirement; case-2 per-customer framing).
  - Closing pointer (~30 lines): "for ongoing operations, see [docs/](../docs/) and [.ai/](../.ai/); for the current rule catalogue see [.ai/rules.md](../.ai/rules.md); etc."
- Length target: 1200-1800 lines (operator decision).
- Cross-references from `.ai/decisions.md` (8C.1), `.ai/system-status.md` (8C.4), `docs/replay-validation.md` (8C.5), `docs/calibration-backlog.md` (8C.6) all resolve once this file lands.

**Tests**: 0 (doc commit, large).

**Validation**:
- `wc -l docs/history.md` within target range.
- Each phase section covers goals + decisions + outcomes + measurement state.
- All forward-reference links from 8C.1/8C.4/8C.5/8C.6 now resolve.
- doc-reviewer + senior-engineer panel (completeness check).

**Declared breaks**:
- Resolves all forward-references introduced in 8C.1, 8C.4, 8C.5, 8C.6.

**Reviewer panel**: doc-reviewer + senior-engineer.

### 8C.13 — Delete PLAN_PHASE_*.md, REPORT_PHASE_*.md, MASTER_PLAN.md

**Changes**:
- After 8C.12 absorption verified by spot-check (operator manually opens history.md and confirms several phase narratives):
- `git rm PLAN_PHASE_*.md REPORT_PHASE_*.md MASTER_PLAN.md`.
- Note: `PLAN_PHASE_8A.md`, `PLAN_PHASE_8B.md`, `PLAN_PHASE_8C.md`, `PLAN_PHASE_8D.md` are NOT deleted here — they're the current Phase 8 plan record. They'll be addressed at production launch (per operator preference: either absorbed at that time or kept as canonical Phase 8 record).
- If `SQUASH_PLAN.md` exists at this point: `git rm SQUASH_PLAN.md` (V-6 confirmed none currently; the `if exists` clause guards).
- Grep cross-references: `grep -rn 'PLAN_PHASE_\|REPORT_PHASE_\|MASTER_PLAN' app/ tests/ scripts/ docs/ .ai/ alembic/ 2>/dev/null` (CLAUDE.md handled separately in 8C.14). Update any non-CLAUDE matches.

**Tests**: 0.

**Validation**:
- `ls PLAN_PHASE_[1-7]*.md REPORT_PHASE_*.md MASTER_PLAN.md 2>&1 | head -5` returns "no matches found" or equivalent.
- `ls PLAN_PHASE_8?.md` returns 4 files (8A, 8B, 8C, 8D).
- doc-reviewer panel.

**Declared breaks**: none (cross-references already updated in prior 8C commits).

**Reviewer panel**: doc-reviewer.

### 8C.14 — Update CLAUDE.md

**Changes**:
- Grep `CLAUDE.md` for `PLAN_PHASE_\|REPORT_PHASE_\|MASTER_PLAN` (V-11 found 3 hits, all generic-example references).
- Line 124: `Example invocation suffix: 'Plan file: PLAN_PHASE_1.md, ...'` — keep as illustrative example (the file doesn't have to exist for the example to teach the pattern). Optionally annotate "PLAN_PHASE_N.md — these files are deleted after phase close; see history.md for narratives".
- Line 288: `e.g. PLAN_PHASE_2.md 2D.3` — same illustrative-example handling.
- Line 313: `produces MASTER_PLAN amendments (if any) and PLAN_PHASE_N.md` — MASTER_PLAN is now deleted; rewrite to "produces PLAN_PHASE_N.md and feeds into docs/history.md at phase close".
- Verify no other CLAUDE.md content references deleted files.

**Tests**: 0 (doc commit).

**Validation**:
- `grep -n 'MASTER_PLAN' CLAUDE.md` returns no matches (post-edit).
- doc-reviewer panel.

**Declared breaks**: none.

**Reviewer panel**: doc-reviewer.

### 8C.15 — Batch close

**Changes**:
- Verify all deleted files absent.
- Verify all rewritten/new files present and committed.
- Run cross-reference audit: `grep -rn 'PLAN_PHASE_[1-7]\|REPORT_PHASE_\|MASTER_PLAN\|security-audit-rls-phase-3\|security-audit-rls-phase-4\|verification-phase-1\|initial-audit' app/ tests/ scripts/ docs/ .ai/ alembic/ CLAUDE.md` — must return clean (no matches outside `docs/history.md` and `PLAN_PHASE_8?.md`).
- PLAN_PHASE_8C.md final state with execution record.

**Tests**: 0 (doc commit).

**Validation**: doc-reviewer panel.

**Declared breaks**: none.

**Reviewer panel**: doc-reviewer.

## Acceptance criteria for 8C close

1. `.ai/decisions.md` restructured to topic-organized; historical detail in `docs/history.md`.
2. `.ai/schema.md` rewritten against post-squash 5-migration state.
3. `.ai/rules.md` rewritten with full Phase 4-7 catalogue.
4. `.ai/system-status.md` reflects pre-launch state.
5. `docs/replay-validation.md` trimmed; Phase 7D measurement intact.
6. `docs/calibration-backlog.md` updated with current status per item; 20 items preserved.
7. Operational runbooks light-edited.
8. `docs/security-audit-rls-phase-3.md`, `phase-4.md`, `verification-phase-1.md`, `initial-audit.md` deleted.
9. `REPORT_PHASE_7.md` generated, then absorbed and deleted.
10. `docs/history.md` created at 1200-1800 lines.
11. `PLAN_PHASE_1-7*.md`, `REPORT_PHASE_*.md`, `MASTER_PLAN.md` deleted from working tree.
12. `CLAUDE.md` updated; no broken references.
13. Cross-reference audit clean: no references to deleted files outside `docs/history.md` and the 4 Phase 8 plan files.
14. PLAN_PHASE_8C.md final state with execution record.

## Notes for downstream

- **8D**: Verifies `docs/production-launch-checklist.md` references are all live (every doc the checklist references exists post-8C). 8D.2 is the verification gate.
- **8D.1 — REPORT_PHASE_8.md**: must reference `docs/history.md` instead of historical PLAN files.
