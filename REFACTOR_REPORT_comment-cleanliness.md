# REFACTOR_REPORT — Comment & Docstring Cleanliness

Removed phase/commit/batch/finding IDs and decision-stack (change-history) narrative from
**source comments and docstrings only**. Comment/docstring text only — no code logic,
signature, or value changes. Ran after the test-soundness pass (`5e8934f`), before the `v*` tag.

Plan: `REFACTOR_PLAN_comment-cleanliness.md` · Inventory: `/tmp/comment-cleanliness-inventory-01.md`

## Result

- **11 commits**, **119 source files** touched (comments/docstrings only).
- **Full-tree clean**: no phase/commit/batch/finding ID or change-history remains in any source
  comment or docstring, except the deliberate carve-outs (below).
- **Full test suite green**: `pytest tests/` → **1229 passed, 0 failed** (golden schema test passes
  under the canonical container-pg16 harness). Comment-only edits proven non-behavioral by AST
  code-equality (docstrings blanked) on every Python file in every commit.
- **Regression guard landed** so this doesn't un-do itself on future phased work.

## Counts by area

| Area | Files | Insertions | Deletions | Notes |
|---|---|---|---|---|
| `app/**` (excl. rules.yaml) | 24 | 233 | 271 | scoring/dsl/rules core, context+models, api, supporting modules |
| `app/rules.yaml` | 1 | 52 | 135 | section headers, weight-change records, case-3b `:675` residue, calibration/triage blocks |
| `alembic/**` + `docker-compose.yml` | 7 | 65 | 144 | squash "Folds in" provenance + 8 in-place `COMMENT ON` literals (D1) + env.py DSN note |
| `scripts/**` | 8 | 49 | 52 | docstring openers, 2 EPHEMERA blocks |
| `tests/**` | 79 | 409 | 425 | ~docstring-opener ID strips + history rewrites + bare-batch-token sweep |
| guard (`.ai/conventions.md`, `.claude/agents/*`) | 3 | 10 | 0 | convention + 2 reviewer checks |

Classification (from the Phase-1 inventory, ~402 edit-bearing hits): dominant work was `remove-id`
(strip a phase/PBL/batch token from a docstring opener or `#`/`--` line) and `rewrite-to-timeless`
(history-bearing rationale where the *why* survives but the provenance is stripped). `remove-narrative`
covered pure-history blocks (squash "Folds in" bullets, triage/deferral notes, calibration change-records).

## Boundary-case dispositions (decided at the Phase-1 checkpoint)

| # | Decision | Resolution |
|---|---|---|
| D1 | alembic `COMMENT ON … IS '…'` audit literals carrying IDs/history (8 sites) | **Edited in place** — IDs/history stripped, audit contract kept. Verified: edited migrations upgrade head cleanly on a scratch DB; scratch schema `--no-comments` byte-identical to live; live schema matches the golden file under container pg16. Append-only deviation operator-approved. |
| D2 | Phase IDs inside string-literal VALUES (assert-failure messages, stdout, argparse descriptions) | **Left untouched** (~15 sites). The AST-code-equality check enforced this automatically — any string-value edit would have shown as an AST diff. |
| D3 | `Amendment N FX` inline finding-stamps (12 sites; not in any ledger) | **Rewritten to timeless** — stamp stripped, the CoW-swap / no-lock health-state / ZIP-stream invariants kept. |
| D4 | Convention wording | **Option A (concise)** — single bullet in `.ai/conventions.md` § Comments. |
| D5 | Reviewer-check strictness | **Reject/blocking** — an introduced ID/narrative in a comment/docstring is a must-fix finding. |
| — | Squash "Folds in" provenance | Deleted; the no-RLS-on-auth and column-ordering-byte-equivalence invariants kept ("byte-equivalent under the canonical normalizer"). |
| — | EPHEMERA scripts | Rewritten to a timeless one-liner keeping the `/tmp`-only-output safety invariant. |
| — | `freight_risk` / `scorer.go` cross-system refs | Kept (rationale, not project IDs). |
| — | Code identifiers with phase tokens (`_PHASE_2B_ADDITIONS`, `test_…_after_6a8`) | Left untouched (renaming is out of scope); stale ones logged to BUGS.md. |
| — | Stale-vs-code comments | Rewritten to current behavior (booking "Layer 2 ships", TTL-cache load, velocity wired, riskd_app_login runtime role, account_prior 0.10). |
| — | Ledger pointers (`.ai/decisions.md`, `docs/history.md`, `.claude/STATUS.md`, `verification §X`, checklist `Phase B`) | Kept; only co-located IDs stripped. |

## Convention added

`.ai/conventions.md` § Comments — new bullet (D4 Option A):
> No phase/commit/batch/finding IDs and no change-history in comments or docstrings. Describe the
> code as it is now; provenance lives in git and the ledgers (`.ai/decisions.md`, `docs/history.md`).
> A pointer like `see .ai/decisions.md §X` is fine; an inlined ID or previously/changed-from/
> superseded narrative is not. TODOs carry no phase/commit ID.

## Reviewer check added

- `doc-reviewer.md` — new **Review Dimension 6: Comment/Docstring Provenance Hygiene** (must-fix;
  never PUBLISH over it).
- `senior-engineer-reviewer.md` — new **Style & Conventions** check (must-fix; drops below SHIP IT).

Both share the same finding categories (phase/commit/batch/finding IDs + change-history) and carve-outs
(ledger pointers, bare TODOs, string-literal values, unchanged pre-existing lines, domain terms,
cross-system references), scoped to ADDED/MODIFIED comments only.

## Reviews

Every code-path commit ran its full panel per `CLAUDE.md` triage routing; never-skip files
(`scoring.py`/`dsl.py`, `auth.py`, the migrations) drew the security/db reviewers. All commits reached
the merge gate. One second cycle was required: Commit 8 (tests/integration) — doc-reviewer returned
NEEDS EDITS on two `Pre-6B`/`post-6B` change-history remnants; those plus several additional prefix-less
bare-batch-token misses (`5B`/`4A`/`3A+3B`/`Pre-4C`) were fixed and re-reviewed → PUBLISH.

## Process notes

- The Phase-1 inventory greps keyed on the `Phase`/`PBL` prefix, so **prefix-less bare batch tokens**
  (`5B`, `4A`, `3A`, `7B`, `Pre-4C`, `post-5D`) slipped the per-commit implementers. A full-tree sweep
  caught the stragglers in already-committed files (`feedback.py`, `booking.py`, `admin.py`,
  `test_phase3_cross_batch_chain.py`) plus `alembic/env.py` (which was outside the migration commit's
  scope), fixed in the **residual-sweep commit** (`6add9e2`).
- The plan's detailed commit list omitted the `scripts/` group named in the plan intro; added as a
  commit (`b079d14`) and logged to `.claude/STATUS.md`.

## Tangential issues logged to `.claude/BUGS.md` (not fixed here)

1. `auth.py` docstring asserts a superuser connecting-role; runtime is `riskd_app_login` (pre-existing
   accuracy drift; correcting it needs auth-path RLS confirmation).
2. `models.py` shipment-currency default is `USD` while the project default is `CAD`.
3. `test_schema_golden` fails on host pg_dump 18 (golden generated with pg16) — passes under container pg16.
4. Stale test identifier `test_allowed_context_fields_count_is_76_after_6a8` asserts `==77` (rename is
   a code change, out of scope).
5. Stale migration numbers `0007/0008/0009/0011` in integration-test comments (post 11→5 squash).

## After this pass

Source comments and docstrings state current behavior and timeless rationale only; provenance lives in
git and the ledgers, and the convention + reviewer check keep it that way through future phased passes.
Combined with the test-soundness pass, the pre-tag cleanup is complete — the `v*` tag is the next action.
