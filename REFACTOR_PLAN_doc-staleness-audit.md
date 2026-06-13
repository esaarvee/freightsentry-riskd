# REFACTOR_PLAN — Documentation & Comment Staleness Audit

> Pass 2 of the pre-launch refactor sweep. Runs after the dead-capability code audit
> (`docs/audits/dead-capability-audit.md`, required reading) and before any `v*` tag push.
> Scope: replace superseded phrasing in the **forward-facing** prose/comment surface with
> current-state phrasing **in place** (no append, no "supersedes the above"). Ledgers carved out.
> Phase-1 findings: `/tmp/doc-staleness-findings-01.md`.

## Decisions absorbed

| # | Decision | Source |
|---|---|---|
| D1 | Commit strategy | **atomic** (confirmed) — one document-area per commit; 6 commits. Commit 6 (in-place migration comment + schema.md) lands in this pass (confirmed) |
| D2 | F8/F9 comment fix | **Edit `0001_foundation.py` comment in place** (operator-approved exception to append-only `conventions.md:244`); sync `schema.md:75`; **no new migration, no 5→6 count cascade** |
| D3 | F10 system-status | **Refresh** the snapshot (date + PBL D-series/enrichment-refresh note) |
| D4 | F6 checklist Phase B | **Scope-down with corrected emphasis** — migrations run as a gated ECS task on *every* deploy; the manual run is *only* the first-deploy bootstrap |
| D5 | CLAUDE.md | Pre-authorized scope-expansion yielded **no stale phrasing** → no edits |
| D6 | Ledgers | `decisions.md`, `BUGS.md`, `STATUS.md`, `REPORT_*`, `REFACTOR_REPORT_*`, `history.md`, calibration-backlog "Resolved/superseded" → **not edited**; errors flagged in report only |
| D7 | tests/** comments | **Out of declared comment-sweep scope** (`app/**`, `alembic/`, infra only) → observed-not-edited |

## Verified current-state anchors (grounding for every replacement)

- Symmetric triangle rule `cold_start_country_triangle_with_carrier_dropoff` + derivation
  `customer_country_triangle_mismatch` **DELETED in 7C.2**; replaced by asymmetric
  `cold_start_outbound_carrier_dropoff` consuming `customer_destination_country_mismatch_outbound`
  (`app/rules.yaml:678-680`, `app/context.py:368`, `.ai/rules.md:589,620-621`).
- Migrations run automatically as the gated `freightsentry-riskd-migrate` ECS task on **every** `v*`
  deploy (`.github/workflows/deploy.yml:120-207`); first-deploy bootstrap is the only manual run
  (runbook §B.1, CFN README §1). `alembic/env.py:46-63` = three DSN sources.
- Project state: Phases 1-8 complete, pre-launch, deploy to `ca-central-1` next
  (`.ai/system-status.md:3`). `MASTER_PLAN.md` + `PLAN_PHASE_1.md` do not exist (8C.13 teardown).

---

## Commits

### Commit 1 — `README.md`: project status + dead cross-references
**Changes** (`README.md`):
- L39 (F3): "Greenfield. Phase 1 in progress … Six-week production-launch target." → current
  pre-launch status (Phases 1-8 complete; production deploy to `ca-central-1` is the next
  operator-driven step). Source: `.ai/system-status.md:3`, `REPORT_PHASE_8.md`.
- L14 (F4): "**Six-phase plan**: `MASTER_PLAN.md`" → repoint to live status/history
  (`.ai/system-status.md` phase table + `docs/history.md`); drop dead filename + "six-phase" framing.
- L15 (F5): "**Phase 1 detailed plan**: `PLAN_PHASE_1.md`" → remove orphaned pointer (file deleted
  8C.13); the `docs/history.md` link already covers the Phase-1 narrative.
**Tests**: none (doc-only). **Validation**: `grep -n 'MASTER_PLAN\|PLAN_PHASE_1\|Greenfield' README.md`
returns nothing; markdown links resolve.
**Review**: doc-reviewer + senior-engineer.
**Rollback**: revert commit.

### Commit 2 — Deleted symmetric-triangle rule references (checklist + runbook)
**Changes**:
- `docs/production-launch-checklist.md:66` (F1): case-3b signal
  `customer_country_triangle_mismatch` → `customer_destination_country_mismatch_outbound`.
- `docs/aws-deploy-runbook.md:426` (F2): "the simple case-3b compound
  (`cold_start_country_triangle_with_carrier_dropoff`) fires independently…" →
  `cold_start_outbound_carrier_dropoff`. Source: `app/rules.yaml:678-680`; dead-cap §2 hand-off.
**Tests**: none. **Validation**: `grep -rn 'country_triangle' docs/` returns only the carved-out
calibration-backlog "Resolved/superseded" + `history.md` ledger lines (untouched).
**Review**: doc-reviewer + senior-engineer.
**Rollback**: revert commit.

### Commit 3 — `docs/production-launch-checklist.md`: migrate-execution model (Phase B)
**Changes** (`docs/production-launch-checklist.md:50-51`, F6 — scope-down):
Rewrite the Phase B migration step so the **automated** reality is primary and the manual run is
explicitly the one-time bootstrap. Proposed text (operator-confirm wording):
> - [ ] **First-deploy bootstrap only.** Migrations run automatically as a gated ECS task
>   (`freightsentry-riskd-migrate`) on every `v*` tag-push deploy (see
>   `docs/aws-deploy-runbook.md` §B.1 "Auto-migration on deploy"). On the **very first** deploy the
>   automation principal and `riskd_app_login` don't exist yet, so run the bootstrap migration once
>   manually: `alembic upgrade head` via a one-off ECS task using `ALEMBIC_DATABASE_URL` (superuser DSN).
Still-true remainder preserved (the manual command + `ALEMBIC_DATABASE_URL` are correct for the
bootstrap). Source: `deploy.yml:120-207`, runbook §B.1.
**Declared breaks**: none.
**Tests**: none. **Validation**: cross-ref to runbook §B.1 resolves; phrasing matches `deploy.yml`.
**Review**: doc-reviewer + senior-engineer + **code-flow** (verifies the described flow matches
`deploy.yml`). Justification for heavier route: operator-facing migration procedure (the "justify
higher" case in the brief).
**Rollback**: revert commit.

### Commit 4 — `.ai/system-status.md`: snapshot refresh (F10)
**Changes** (`.ai/system-status.md`):
- L68: bump "Last updated: 2026-06-05" to the pass date; note PBL D-series + enrichment-refresh landed.
- Add a brief line (in the existing "Implications" or a one-line addendum) acknowledging
  migrate-automation (gated ECS migrate task on deploy) + the enrichment auto-refresh loop +
  `/health enrichment` field. No restructuring; no count changes (migrations stay 5 per D2).
**Declared breaks**: none.
**Tests**: none. **Validation**: no present-tense sentence contradicts current code; links resolve.
**Review**: doc-reviewer + senior-engineer.
**Rollback**: revert commit.

### Commit 5 — `app/context.py` comments (F7a, F7b)
**Changes** (`app/context.py`):
- L132-134 (F7a): docstring "deleted in 7C.3" → "deleted in 7C.2" (matches the dead-cap pass's
  reviewer-corrected phase; `.ai/rules.md:592,620`).
- L254 (F7b): "The triangle-mismatch derivation (Phase 6A.5) also reads from these intermediates" →
  reference the current asymmetric `_outbound_destination_mismatch` (Phase 7C.2) reader of
  `shipment_destination_country`.
**Code annotated is NOT changed** (comments only).
**Declared breaks**: none.
**Tests**: none changed. **Validation**: `ruff check app/`, `mypy app/`, `pytest tests/unit/ -x -q`
(comment-only — must stay green).
**Review**: senior-engineer + **code-flow** (verifies comments now match the code) + doc-reviewer.
**Rollback**: revert commit.

### Commit 6 — `alembic/versions/0001_foundation.py` comment + `.ai/schema.md` sync (F8, F9)
**Changes**:
- `alembic/versions/0001_foundation.py:130-133` (F8): in the `COMMENT ON COLUMN
  customers.registered_country` string, replace
  "Drives case-3b detection via the customer_country_triangle_mismatch derivation (build_context)" →
  "Drives case-3b detection via the customer_destination_country_mismatch_outbound derivation
  (build_context)". Keep the `tenant_route_baselines population (6A.7 upsert)` + Pydantic clauses
  (still true). **Deliberate in-place edit of a committed migration** — operator-approved exception to
  `conventions.md:244` (D2); commit message states the exception + rationale (pre-launch, rebuild-from-
  scratch, golden dumps `--no-comments`).
- `.ai/schema.md:75` (F9): update the verbatim-quoted comment to match the new migration text exactly.
**Declared breaks**: none (count stays 5; no other doc references this comment).
**Tests/Validation**: `docker compose exec app alembic downgrade base && alembic upgrade head`
(round-trip clean); `pytest tests/integration/test_schema_golden.py` (passes — `--no-comments` makes
the change invisible to the golden snapshot, confirming no DDL/structure drift);
`pytest tests/unit/ -x -q`.
**Review**: **full panel + db-reviewer** (Never-Skip: migration file change). Reviewers consult this
declared exception so the append-only deviation is not flagged as a process failure.
**Rollback**: revert commit (restores original comment).

---

## Cross-cutting verification (pre-commit, every commit)

- Replaced phrasing matches a verified current-state anchor (above); no speculative rewrites.
- No carved-out ledger touched (`grep` the diff for `decisions.md`, `BUGS.md`, `STATUS.md`,
  `REPORT_*`, `REFACTOR_REPORT_*`, `history.md`, backlog "Resolved/superseded").
- No "supersedes/previously/as of Phase N" accretion framing introduced.
- No code logic / rule / config / infra-definition change (commit 6 changes only a `COMMENT` string).
- Cross-references still resolve (README → history/system-status; checklist ↔ runbook §B.1).

## Flags carried to the report (operator follow-ups, NOT edited here)

- **Durable staging RDS** (us-east-2) that already ran `0001`: its column comment stays old until a
  one-off `COMMENT ON COLUMN` or rebuild (cosmetic; from D2).
- **`tests/**` triangle comments** (D7) — out of scope; candidate for a future tests-doc pass;
  `test_customer_registered_country.py:6` is the one loosely-worded instance.
- **Not-actually-stale register** (N1-N13, `/tmp/doc-staleness-findings-01.md`) — feeds Phase 9's
  doc lens so these phrasings aren't re-investigated.

## Out of scope (restated)

Ledger rewrites; any code/rule/config/infra behavior change; doc reorganization/merging;
`history.md` depth-expansion / `rules.md` scope-expansion; dead-capability remediation (identify-only,
already ran). CLAUDE.md: no stale phrasing found → untouched.

## Post-pass deliverable

`REFACTOR_REPORT_doc-staleness-audit.md`: counts (files swept, replacements by category, the one
scoped-down remainder F6, flags left for operator, the in-place-migration exception), the
not-actually-stale notes (N1-N13), and any cross-reference fixes.
