# REFACTOR_PLAN — Comment & Docstring Cleanliness

Remove phase/commit/batch/finding IDs and decision-stack (change-history) narrative from
**source comments and docstrings only**. Comment/docstring TEXT only — no code logic,
signature, or value changes. Ends with a convention + reviewer-agent regression guard.
Runs after the test-soundness pass (HEAD `5e8934f`), before the `v*` tag.

Inventory: `/tmp/comment-cleanliness-inventory-01.md` (~402 edit-bearing hits across ~120 files).

## Decisions absorbed

| # | Decision | Resolution |
|---|---|---|
| D1 | alembic `COMMENT ON … IS '…'` literals carrying IDs/history (8 sites) | **Edit in place** — strip IDs/history from the literals too. Accepts append-only deviation; never-skip review + db-reviewer gate it. |
| D2 | Phase IDs in string-literal *values* (assert-failure messages, stdout) (~15 sites) | **Out of scope** — leave all. They are values, not comments; the pass forbids value changes. |
| D3 | `Amendment N FX` inline finding-stamps (12 sites) — not in any ledger | **Rewrite-to-timeless** — strip the `Amendment N FX` stamp, keep the load-bearing invariant (CoW swap, no-lock, ZIP-stream). |
| D4 | Convention wording for `.ai/conventions.md` | **Option A (concise)** — single bullet (verbatim in Commit 9). |
| D5 | Reviewer-check strictness | **Reject (blocking)** — introduced ID/narrative in a comment/docstring drops the verdict below APPROVED. |
| — | Squash "Folds in:" provenance | Delete fold-in bullets; keep+rewrite the invariants they wrap; "byte-equivalent under the canonical normalizer". |
| — | EPHEMERA blocks | Rewrite to a timeless one-liner that keeps the `/tmp`-only-output safety invariant. |
| — | `freight_risk's source was N` | Keep (cross-system rationale, not a project ID); strip only co-located project-phase tokens. |
| — | Code identifiers/fn names with phase IDs (`_PHASE_2B_ADDITIONS`, `test_…_after_6a8`, …) | Out of scope — renaming is a code change. Keep. |
| — | Stale-vs-code comments | In scope per "fix the docstring to match the code" — rewrite to current behavior; senior verifies. |
| — | Ledger pointers (`.ai/decisions.md`, `docs/history.md`, `.claude/STATUS.md`, `verification §X`) | Keep; strip only co-located phase IDs. |

## Global conventions for every commit

- **Comment/docstring text only.** No statement, signature, or value edits. (Exception by D1:
  the alembic `COMMENT ON` *string literals* — these are the only string-values touched, and
  only in the alembic commit, gated by db-reviewer.)
- **Rewrite, don't append.** No "(formerly X)" / "previously" framing. Strip the history; state
  the present.
- **Preserve load-bearing rationale**, rewritten timeless.
- **No `Declared breaks` anywhere** — text-only edits introduce no transitional state; every
  commit leaves the tree green. (Per CLAUDE.md, absence is the signal — omitted, not "none".)
- **Validation per commit:** `ruff check app/ tests/` + `ruff format --check` (pre-commit gates),
  `mypy app/` (app commits), `pytest tests/unit/ -x --no-header -q` (pre-commit). Comment-only
  edits cannot change behavior, so unit tests are a tripwire for accidental code edits. The
  alembic commit additionally runs the schema round-trip + `tests/integration/test_schema_golden.py`
  (the COMMENT-ON edits must not perturb the golden schema dump — verifies the edits hit only
  comment text). Full `pytest tests/` after the last tests/ commit.
- **Commit footer:** each references the inventory area; no IDs in commit *bodies* either.

## Reviewer routing (per CLAUDE.md triage gate + pass overlay)

The pass routes comment commits to **doc-reviewer + senior-engineer**, adding **code-flow** where
a rewrite touches a non-obvious construct's rationale. CLAUDE.md **never-skip** overrides force a
fuller panel on specific files — folded in below:

| Commit | Files | Panel | Why |
|---|---|---|---|
| 1 | scoring.py, scoring_constants.py, dsl.py, rules.py | senior + security + code-flow + doc | never-skip: `scoring.py` (formula), `dsl.py` (sandbox) |
| 2 | context.py, models.py | senior + code-flow + doc | non-obvious rewrites (7C.2 residue, derivations) |
| 3 | api/{booking,modification,feedback,admin,health}.py | senior + code-flow + doc | request-path rationale rewrites |
| 4 | auth.py, db.py, logging.py, observability.py, main.py, trust.py, velocity.py, baseline.py, enrichment_refresh.py, tenant_config.py, tenant_config_cache.py, tenant_route_baselines.py, services/entity_upsert.py | senior + security + code-flow + doc | never-skip: `auth.py` (auth/RLS context) |
| 5 | app/rules.yaml | senior + code-flow + doc | rules.yaml comment edits (no rule add/remove → not never-skip) |
| 6 | alembic/versions/0001-0005, docker-compose.yml | senior + security + code-flow + **db** + doc | never-skip: migration/schema comments; D1 in-place COMMENT-ON edits |
| 7 | tests/unit/** | test + senior + code-flow + doc | test comment edits |
| 8 | tests/integration/** + tests/security/** + tests/conftest.py | test + senior + code-flow + doc | test comment edits |
| 9 | .ai/conventions.md, .claude/agents/doc-reviewer.md, .claude/agents/senior-engineer-reviewer.md | senior + doc | convention + reviewer guard |

Merge gate per CLAUDE.md: iterate to APPROVED WITH RESERVATIONS or higher; cap 3 cycles.

---

## Commits

### Commit 1 — app/ scoring & rule-eval core
**Files:** `app/scoring.py`, `app/scoring_constants.py`, `app/dsl.py`, `app/rules.py`
**Changes:** strip phase IDs from module docstrings and `#` comments; rewrite the rule-eval
rationale timeless. Notable:
- `scoring.py:1` `"…— Phase 2 ships Layer 1 + Layer 2 + Layer 3."` → `"…— Layer 1 + Layer 2 + Layer 3."`
- `scoring.py:84/118` strip "Phase 2A formula is UNCHANGED" / "Phase 6 staging replay"; keep the
  multiplicative-formula + decisions.md pointer and the "0.5 hardcoded, may revise after FPR" why.
- `dsl.py:115` "(Rule loader in 1D.7 validates names…)" → "(The rule loader validates names at startup…)".
- `rules.py:127-131` **SPECIAL-ATTENTION**: drop "Phase 7C.2 replaced the symmetric triangle-mismatch
  … per the Phase 7B empirical record"; keep "case-3b signals … the outbound-destination check is
  asymmetric, matching the Roulottes Lupien attack shape." `rules.py:14-16` reduce to the
  `.ai/decisions.md` pointer.
**Risk:** low (text). `scoring.py`/`dsl.py` are never-skip → full panel confirms no logic touched.
**Rollback:** `git revert` the commit; isolated to 4 files.

### Commit 2 — app/ request context & wire models
**Files:** `app/context.py`, `app/models.py`
**Changes:** the heaviest rewrite cluster. Notable:
- `context.py:131-134` & `:362-367` **SPECIAL-ATTENTION**: delete the symmetric-triangle "deleted in
  7C.2" history; keep the asymmetric (destination-only) current-behavior description.
- `context.py:10-12` rewrite "Phase 2B adds 11 fields" → describe the Layer-2 fields as part of the
  current Context. `:224-225`/`:705` keep the sequential-await latency rationale, drop "Phase 5 load
  test revisits". `:426-433` keep `resolve_value_caps` fallback semantics, drop USD→CAD history + IDs.
- `context.py:646` **stale-vs-code**: velocity_1h/_24h are wired below — rewrite the placeholder note
  to current behavior (senior verifies against lines ~707+).
- `models.py:38-49` **SPECIAL-ATTENTION**: drop "originally-paired symmetric triangle … replaced in
  7C.2"; keep the structured-country-signal contract. `:82-89` strip IDs, keep the case-3 attack
  rationale + the `docs/production-launch-checklist.md Phase B` doc pointer (section name, kept).
  `:77-80` strip "Phase 1-3 payloads" history (leave the `USD` default literal **unchanged** — a
  possible USD/CAD-default inconsistency is logged to BUGS.md, not fixed here).
**Risk:** low-medium — rewrites touch public-contract docstrings; senior confirms each still matches code.
**Rollback:** `git revert`; 2 files.

### Commit 3 — app/ API endpoints
**Files:** `app/api/booking.py`, `app/api/modification.py`, `app/api/feedback.py`, `app/api/admin.py`, `app/api/health.py`
**Changes:** strip endpoint-docstring phase IDs; rewrite request-path rationale timeless. Notable:
- `booking.py:3-4` **stale-vs-code**: "Layer 2 lands Phase 2" → current "Layer 1 + Layer 2 + Layer 3".
- `booking.py:52` + `modification.py:59` **stale-vs-code**: "no caching in Phase 4 (Phase 5 wraps)"
  contradicts `load_tenant_config_cached` → "Loaded via the TTL cache (load_tenant_config_cached)".
- `booking.py:120-121` keep `.claude/STATUS.md` pointer + dormant-RLS invariant, drop "Phase 1".
- `booking.py:263-274` keep the idempotency/`request_type` rationale, drop "(3A.6 …)" + "RESOLVED in 5A.7".
- `feedback.py:19-28` **D3**-adjacent: strip "Phase 7C.11", keep the ALLOW-gating-to-avoid-case-2-
  pollution rationale. `health.py:38` **D3**: strip "Amendment 1 F2", keep the "degraded doesn't change
  HTTP status (probes stay in rotation)" invariant + operator-decision phrasing.
**Risk:** low. **Rollback:** `git revert`; 5 files.

### Commit 4 — app/ supporting modules
**Files:** `app/auth.py`, `app/db.py`, `app/logging.py`, `app/observability.py`, `app/main.py`,
`app/trust.py`, `app/velocity.py`, `app/baseline.py`, `app/enrichment_refresh.py`,
`app/tenant_config.py`, `app/tenant_config_cache.py`, `app/tenant_route_baselines.py`,
`app/services/entity_upsert.py`
**Changes:** bulk remove-id + targeted rewrites. Notable:
- `observability.py:157` **SHA**: drop `25f9932`; rewrite to what the alarm guards (fail-open
  source-load path) timelessly.
- `main.py:6` + `enrichment_refresh.py:126/590/597/1030` **D3**: strip `Amendment N FX` stamps, keep
  CoW/no-lock/ZIP-stream invariants.
- `auth.py:15-18` keep the superuser-bypasses-RLS-now / non-superuser-needs-SECURITY-DEFINER invariant
  + STATUS.md pointer; drop "Phase 1"/"Phase 5". `tenant_config.py:12-17/220` delete phase-scope and
  "Phase 5 wraps" provenance (cache documented in `tenant_config_cache.py`). `tenant_config_cache.py:17`
  keep the `.ai/decisions.md` pointer, drop "Phase 5B section" token.
**Risk:** low; `auth.py` never-skip → security + full panel confirm no auth-logic touched.
**Rollback:** `git revert`; 13 files.

### Commit 5 — app/rules.yaml
**Files:** `app/rules.yaml`
**Changes:** strip section-header phase IDs; rewrite weight-change records to timeless role
statements (current weight is the YAML value). Notable:
- `:649-676` **named-in-scope (`:675` residue)**: drop "Phase 7C.2", "Replaces the symmetric triangle
  compound", "Phase 6C empirical (0/95)", and "DELETED in 7C.3 / co-existed transiently (declared
  break)"; keep the asymmetric attack-shape + predicate definitions + weight-band rationale.
- `:92-97`/`:110-117`/`:443-446` drop "weight 0.20 -> 0.10" change-records + IDs; keep the
  secondary-corroborating-signal / calibration rationale.
- `:8-11`/`:13-14`/`:342-349`/`:388-419`/`:489-497` remove-narrative (Layer-2-now-wired, roadmap,
  triage/deferral, calibration change-record). Keep loader-invariant `:3-6`, naming-collision
  `:450-453`, booking-path-dormancy `:536-544`.
**Risk:** low-medium — must not touch a single weight/condition/rule-name; code-flow + senior verify
the YAML *values* are byte-identical (diff shows comment lines only).
**Rollback:** `git revert`; 1 file.

### Commit 6 — alembic migrations + infra
**Files:** `alembic/versions/0001_foundation.py`, `0002_booking_flow.py`, `0003_baselines.py`,
`0004_enrichment_global.py`, `0005_runtime_roles.py`, `docker-compose.yml`
**Changes:**
- Docstrings/`#`/`--`: delete "Phase 8A squash / Folds in:" provenance bullets; keep+rewrite the
  no-RLS-on-auth rationale (`0001:22-44`) and column-ordering-for-byte-equivalence ("byte-equivalent
  under the canonical normalizer"). `docker-compose.yml:28` strip "Phase 5D.2".
- **D1 — `COMMENT ON` literals (8), in place:** strip IDs/history, keep the audit-trail contract:
  `0001:90` (`updated_at`), `0001:130-135` (`registered_country`), `0002:82-85` (email/phone_hmac —
  drop "written before Phase 3B"), `0002:115-116` (`request_type`), `0002:122-123` (idempotency index
  — keep the why, drop "Replaces 0001 flat … 5A.7"), `0002:152-153` (`operator_id`), `0003:128-133`
  (`tenant_route_baselines` table comment — dense with rule names, keep all, drop 6A.x), `0005:105-106`
  (`riskd_app_login` role).
**Validation (extra):** schema round-trip (`downgrade base && upgrade head`) +
`tests/integration/test_schema_golden.py` must stay green — proves the COMMENT-ON edits changed only
comment text, and the byte-equivalence claims still hold under the normalizer.
**Risk:** medium — only commit touching emitted DDL (string literals). db-reviewer + security + full
panel. Append-only deviation is operator-approved (D1).
**Rollback:** `git revert`; migrations are independent of later commits.

### Commit 7 — tests/unit
**Files:** `tests/unit/**` + `tests/unit/conftest.py`
**Changes:** strip `"""Unit tests for Phase X …"""` docstring openers (bulk remove-id); rewrite
history-bearing comments timeless; **leave D2 assert-message/stdout string values untouched**;
**leave code-identifier names untouched** (`_PHASE_2B_ADDITIONS`, `test_…_after_6a8`). Stale-vs-code:
`test_booking_stub.py:3` "Phase 1 returns ALLOW 0.0" → current "0.10" behavior.
**Risk:** low; test-reviewer + senior confirm no assertion/logic/name changed (diff = comments/
docstrings + D2-exempt strings only).
**Rollback:** `git revert`.

### Commit 8 — tests/integration + tests/security + root conftest
**Files:** `tests/integration/**`, `tests/security/**`, `tests/conftest.py`
**Changes:** same rules. Notable: `test_currency_normalization_e2e.py` the repeated
`CAD-default (Phase 6B; numeric thresholds unchanged from prior USD-default)` parenthetical (lines
4/12/17/93/168/223/264) → `CAD-default`. `test_rls_enforcement_under_riskd_app.py:261-267`
**stale-vs-code** → current runtime-role state. Keep ledger pointers (Amendment 1 F5 → D3 strip stamp;
docs/history.md; .ai/decisions.md; verification §X).
**Validation (extra):** full `pytest tests/` after this commit (per pass: combined with test-soundness,
pre-tag cleanup complete).
**Risk:** low. **Rollback:** `git revert`.

### Commit 9 — convention + reviewer regression guard
**Files:** `.ai/conventions.md`, `.claude/agents/doc-reviewer.md`, `.claude/agents/senior-engineer-reviewer.md`
**Changes:**
- `.ai/conventions.md` "Comments" section — append **D4 Option A** verbatim:
  > - No phase/commit/batch/finding IDs and no change-history in comments or docstrings. Describe the
  >   code as it is now; provenance lives in git and the ledgers (`.ai/decisions.md`, `docs/history.md`).
  >   A pointer like `see .ai/decisions.md §X` is fine; an inlined ID or previously/changed-from/
  >   superseded narrative is not. TODOs carry no phase/commit ID.
- doc-reviewer.md + senior-engineer-reviewer.md — add a **D5 reject/blocking** check: a new
  comment/docstring carrying a phase/commit/batch/finding ID (incl. `Amendment N FX`, SHAs) or
  change-history narrative is a must-fix finding that drops the verdict below APPROVED. Ledger
  pointers and bare TODOs are explicitly allowed.
**Risk:** low (doc/agent text). doc + senior.
**Rollback:** `git revert`; self-contained.

---

## Final report

After Commit 9: produce `REFACTOR_REPORT_comment-cleanliness.md` — counts (IDs removed, narrative
removed, rewritten-to-timeless, kept-as-rationale, kept-as-pointer) by area; the boundary-case
dispositions (D1–D5 + defaults); the convention added; the reviewer check added; any BUGS.md entries
logged (e.g. the models.py USD/CAD-default inconsistency). Then the `v*` tag is the next action.
