# PLAN_PHASE_5A — Foundational hardening

Batch 5A of Phase 5. Lands the low-risk infrastructure pieces — `uv.lock` + ruff version reconciliation, non-root container, `last_used_at` writer, and `ux_decisions_tenant_request` UNIQUE widening — so 5B/5C/5D build on stable ground.

## Pre-plan verification findings

Surfaced during planning verification; absorbed below so each commit's scope reflects real codebase state:

- **`api_tokens.last_used_at` column already exists** (`alembic/versions/0001_initial.py:253`). The Phase 5 bootstrap prompt described migration 0006 as "add a column"; the actual delta is **add the supporting index only**. The writer in `app/auth.py` is the substantive work this batch lands.
- **`pyproject.toml` ruff dev-dep is unpinned** (`ruff>=0.7`); the lock-and-bump aligns pre-commit pin + pyproject pin + `uv.lock` resolved version to one stable release.
- **Project does not use uv today** — pyproject.toml has no `[tool.uv]` section, Dockerfile installs with pip. 5A.1 introduces uv-lock-as-source-of-truth without forcing a build-tool switch (`uv lock` reads PEP 621 metadata and produces a portable lock; runtime install remains pip).
- **`api_tokens` has `ix_api_tokens_tenant ON (tenant_id)` already** (`0001_initial.py:256`). The new `(tenant_id, last_used_at DESC)` index is additive and supports the future "stale token" query pattern.
- **`ux_decisions_tenant_request` is a UNIQUE constraint** (not just an index) per `0001_initial.py:141`. Migration 0007 drops the constraint and replaces it with a UNIQUE INDEX named `ux_decisions_tenant_request_type` on `(tenant_id, request_type, request_id)`. UniqueViolationError fires on both shapes, so the existing 409 try/except in booking.py + modification.py remains valid as defense-in-depth.

If any of these findings turns out wrong on execution, treat as substantive drift per CLAUDE.md Phase Scope Discipline and surface via `.claude/STATUS.md`.

## Decisions absorbed (5A-specific)

| Decision | Value | Source |
|---|---|---|
| `uv.lock` location | Repo root, committed | Phase 5 bootstrap |
| uv tooling adoption | `uv lock` for resolution only; runtime install remains pip in Dockerfile | Verification finding (avoid scope creep into build-tool migration) |
| ruff pin reconciliation | Bump `.pre-commit-config.yaml` to ruff 0.15.x (matching local 0.15.7) + pin `pyproject.toml` to `ruff>=0.15.0,<0.16.0` | Phase 4 BUGS.md + bootstrap |
| Format-sync scope | One-shot `ruff format app/ tests/ scripts/` after pin bump, separate commit | Bootstrap + BUGS.md |
| Non-root container UID | 1000 (standard) | Bootstrap |
| `last_used_at` migration shape | Index-only (column already exists) | Verification finding |
| `last_used_at` write path | Synchronous UPDATE inside auth transaction; on auth success only (not on auth failure) | Bootstrap + watch point |
| UNIQUE widening shape | DROP CONSTRAINT then CREATE UNIQUE INDEX `ux_decisions_tenant_request_type` on `(tenant_id, request_type, request_id)` | Bootstrap |
| 409 catch retention | `try/except UniqueViolationError → 409` in booking.py + modification.py stays as defense-in-depth | Bootstrap |
| BUGS.md drain timing | Mark entries RESOLVED in the relevant fix commit; final sweep in 5D.6 | Bootstrap |

## Workflow context

**Per-commit reviewer panel is MANDATORY in Phase 5.** Each commit below lists its triage-gate routing and which reviewers run. Pre-commit hooks (ruff check, ruff format, mypy strict, unit tests) gate every commit — they catch the basics so reviewers focus on contracts and security. Per CLAUDE.md, reviewer panel runs at commit time, not at batch boundary. Operator checkpoint between batches is operator approval of the report, separate from the per-commit reviewer cadence.

Plan-file slicing for reviewer invocations: when invoking reviewers, tell them `Plan file: PLAN_PHASE_5A.md, current commit: 5A.N (<title>), upcoming commits: 5A.(N+1) through 5A.7 sections. Read only those sections.`

## Cross-batch dependencies

- **5B (cache) depends on 5A.7 only indirectly** — the 5B cache module wraps `load_tenant_config`, which doesn't touch `api_tokens` or the `decisions` UNIQUE. 5A unblocks 5B operationally (clean lockfile + ruff sync = no format churn on 5B commits).
- **5C (EMF) is independent of 5A.**
- **5D (role transition) depends on 5A.5** — the `last_used_at` writer issues an UPDATE on `api_tokens` under the auth-context tenant_id. When 5D switches the runtime role to `riskd_app_login`, the writer must succeed under RLS. 5A's writer must be tested under RLS in 5D's full-suite re-run.
- **5D (role transition) depends on 5A.6** — the UNIQUE index must exist before 5D's full-suite re-run, since case-1 + case-2 fixtures may exercise the booking/modification request_id pattern.

## Commits

### 5A.1 — Lockfile + ruff pin reconciliation

**Theme.** Generate `uv.lock` from `pyproject.toml`. Bump ruff in `.pre-commit-config.yaml` to 0.15.x and pin in `pyproject.toml` to `ruff>=0.15.0,<0.16.0`. No source-file reformatting yet (deferred to 5A.2 to keep commit reviewable).

**Files changed.**
- `pyproject.toml` — narrow ruff version constraint
- `.pre-commit-config.yaml` — bump ruff hook `rev:` from `v0.6.0` to `v0.15.7` (or latest 0.15.x at execution time, matching local install)
- `uv.lock` — new file at repo root, generated by `uv lock`

**Specifics.**
- Run `uv lock` from the repo root with no other args. Commit the resulting `uv.lock` verbatim.
- The pin in `pyproject.toml` uses `~=0.15.0` or `>=0.15.0,<0.16.0` (whichever convention is closer to existing dep style — verify by looking at other dev-deps).
- Pre-commit hook `rev:` must match the locked ruff version exactly.
- DO NOT run `ruff format` over the tree in this commit. The format-sync is 5A.2; keeping it separate makes 5A.1 reviewable as "version pin alignment" without thousands of formatting hunks.

**Validation.**
- `pre-commit run --all-files` passes (with the new ruff version). If any files are reformatted at this stage, that's expected behavior of the new version on existing source — but those changes go into 5A.2, not 5A.1. Stash any auto-fixes from `pre-commit run` and resolve in 5A.2.
- Alternatively: run `pre-commit run ruff` only and confirm it identifies the format-sync diffs without applying them (use `--no-stash` + manual review).
- `uv lock --check` (if uv version supports) confirms the lock matches pyproject.

**Risk level.** Low. Version pin updates; no runtime behavior change.

**Reversibility.** High. `git revert` restores prior pin.

**Pre-commit verification.** Hooks run against the new ruff version. Pre-commit's own self-test runs after the hook bump (`pre-commit autoupdate --dry-run` style).

**Observability.** None.

**Test changes.** None.

**Rollback plan.** `git revert` the commit; `pre-commit install` re-pulls old version.

**Declared breaks.**
- Scope: With the new ruff 0.15.x pin active and no format-sync yet applied, `pre-commit run --all-files` reports many would-be-formatted hunks across `app/` + `tests/`. Pre-commit hook output is noisy until 5A.2 lands. Local commits using `--no-verify` are NOT acceptable in this gap.
- Resolved in: 5A.2 (one-shot ruff format-sync across the tree).

**Reviewer routing.** Lightweight per CLAUDE.md: "Single dependency add/bump/remove in `pyproject.toml`" → **security-auditor + senior-engineer**. Both verify the version choice and the propagation between pyproject + pre-commit + lockfile.

---

### 5A.2 — One-shot ruff format-sync

**Theme.** Apply `ruff format` across the entire codebase using the newly-pinned ruff version. Single mechanical commit. Resolves the noise from 5A.1's declared break.

**Files changed.** Many `.py` files under `app/`, `tests/`, `scripts/`, `alembic/` (anywhere ruff format finds reformattable hunks). Expected: ~20-30 files per Phase 4 BUGS.md observation. Could be larger.

**Specifics.**
- Run `ruff format app/ tests/ scripts/ alembic/` from repo root.
- Also run `ruff check --fix app/ tests/ scripts/ alembic/` and accept any auto-fixes.
- Commit message body explicitly lists the categories of changes (frozenset member layout, parenthesized call expansion, implicit-string-concat merges, assert tuple-form, etc.) so reviewers can scan rather than re-deduce.
- Do not hand-edit any file in this commit. Pure ruff-driven output only.

**Validation.**
- `pre-commit run --all-files` clean after the format-sync.
- `pytest tests/unit/ -x --no-header -q` clean (unit tests unaffected by formatting).
- `pytest tests/integration/ --asyncio-mode=auto` clean (integration tests unaffected; pre-commit only runs unit).
- `mypy app/` clean (formatting must not change type semantics).
- `ruff check app/ tests/` clean.

**Risk level.** Low-to-medium. Mechanical reformat; risk is "ruff format produced something unexpected" rather than "behavior changed." Reviewer panel verifies no semantic drift.

**Reversibility.** High. `git revert`.

**Pre-commit verification.** Hooks pass clean after this commit (the whole point).

**Observability.** None.

**Test changes.** None semantically. Test files reformat alongside production code.

**Rollback plan.** `git revert` + re-pin to 0.6.0 in 5A.1 (compound revert).

**Declared breaks.** None.

**Reviewer routing.** Standard panel: **senior-engineer + security-auditor + code-flow-reviewer**. Borderline-rule applies — formatting changes that include rule-driven reflow can mask subtle semantic shifts (e.g., a frozenset reordering that changes iteration order in a hash-dependent assertion). Full panel reviews. Also: **test-reviewer** runs because tests change. The diff is large; reviewers use `git diff --stat` to scope hot spots and read the highest-density files in full.

---

### 5A.3 — `.ai/conventions.md` uv-lock-as-source-of-truth documentation

**Theme.** Document the uv-lock convention so future contributors don't re-derive it. Doc-only commit.

**Files changed.**
- `.ai/conventions.md` — add a "Dependency locking" section explaining: `uv.lock` is committed; regenerate via `uv lock` after any pyproject change; pre-commit pins must match the lockfile-resolved version; runtime install remains pip in Dockerfile.

**Specifics.**
- Section length: ~20-30 lines. Brief, prescriptive.
- Include the command operators run when adjusting deps: `uv lock` → `git add uv.lock pyproject.toml` → `git commit`.
- Explicit note: `uv` is used for resolution + lock; the project does NOT migrate to `uv pip install` at runtime in Phase 5. Dockerfile + dev workflow still use pip.

**Validation.**
- `pre-commit run --all-files` clean.
- Doc-reviewer reads the section in context.

**Risk level.** Trivial.

**Reversibility.** High.

**Pre-commit verification.** No code changes; hooks all pass.

**Observability.** None.

**Test changes.** None.

**Rollback plan.** `git revert`.

**Declared breaks.** None.

**Reviewer routing.** Doc-only per CLAUDE.md → **doc-reviewer only**.

---

### 5A.4 — Non-root container user

**Theme.** Add a non-root user (UID 1000) to the Dockerfile. Adjust ownership of `/app`. Confirm `docker compose up` smokes cleanly.

**Files changed.**
- `Dockerfile` — add `RUN groupadd -g 1000 app && useradd -u 1000 -g app -m -s /bin/bash app` before the COPY step (or appropriate Debian-slim equivalent). Adjust ownership: `RUN chown -R app:app /app`. Add `USER app` before `CMD`.
- `docker-compose.yml` — no change expected (the `app` service inherits the Dockerfile's `USER`); confirm during validation.

**Specifics.**
- Base image is `python:3.13-slim`. Use Debian-style user creation.
- All pip installs happen as root (before `USER app`) so site-packages ownership is fine.
- The `WORKDIR /app` and `COPY` steps land files as root; the `chown -R app:app /app` rewrites ownership after the copy.
- Entrypoint `uvicorn app.main:app --host 0.0.0.0 --port 8000` runs as `app`. Port 8000 is non-privileged; no capability issue.
- DO NOT introduce a Dockerfile multi-stage build in this commit. Keep diff small; multi-stage is out of scope.

**Validation.**
- `docker compose build` succeeds.
- `docker compose up -d` brings up postgres + app; the app responds 200 on `GET /health`.
- `docker compose exec app whoami` returns `app`, not `root`.
- `docker compose exec app alembic upgrade head` still works (alembic-as-superuser pattern — alembic connects to DB as the configured DATABASE_URL user; the container's runtime user only governs filesystem + process identity, not DB identity).
- `pytest tests/integration/ --asyncio-mode=auto` clean against the new container.

**Risk level.** Medium. Container UID changes can produce subtle FS-permission errors at runtime if any code path writes to `/app/data` or similar. Mitigation: `data/` doesn't exist yet (verification confirms); no code path writes to `/app` filesystem at runtime.

**Reversibility.** High. `git revert` restores root.

**Pre-commit verification.** N/A for Dockerfile changes (no pre-commit hook for Docker). Manual `docker compose up` smoke is the validation.

**Observability.** None.

**Test changes.** None. Integration tests run on host pytest, against containerized postgres + app; the container's user identity is transparent.

**Rollback plan.** `git revert`. If a runtime FS-write issue surfaces only in production (Phase 6), the revert pulls back to root; alternatively a `chmod` line can be added in a follow-up.

**Declared breaks.** None.

**Reviewer routing.** Lightweight per CLAUDE.md: "ONLY config-value change in `docker-compose*.yml` or production deploy config → **security-auditor + senior-engineer**." Dockerfile is the production deploy config in this project. Security-auditor checks the user creation + ownership chain doesn't leak; senior-engineer validates the smoke test approach.

---

### 5A.5 — `last_used_at` writer in `auth.py`

**Theme.** Update `last_used_at = now()` on each successful auth. Synchronous UPDATE inside the auth transaction. ~1ms additional latency per request acceptable.

**Files changed.**
- `app/auth.py::require_api_token` — add an UPDATE after the successful SELECT confirms the token. Same connection, same transaction. UPDATE writes `last_used_at = now()` keyed by `id` (PK) or by `token_hash` (UNIQUE). Use `id` (PK) for query plan efficiency.

**Specifics.**
- The current `require_api_token` flow (per verification, `app/auth.py:60-91`):
  1. Parse `Authorization: Bearer <token>` header.
  2. Hash the token (HMAC-SHA256).
  3. SELECT `tenant_id, role` FROM `api_tokens` WHERE `token_hash = $1`.
  4. If no row: emit `auth.invalid_token` log, raise 401.
  5. If row exists: emit `auth.success` log, return `AuthContext(tenant_id, role)`.
- The new flow inserts an UPDATE between steps 3 (row found) and step 5 (return):
  4.5. UPDATE `api_tokens` SET `last_used_at = now()` WHERE `id = $1` AND `token_hash = $2` (defense-in-depth on the WHERE — both predicates).
- Critical: only the SUCCESS path updates `last_used_at`. The invalid-token path (step 4 no-row) MUST NOT trigger a write. (Verify by inspection in the diff.)
- Critical: the UPDATE is on the same connection as the SELECT; both ride the auth-dependency's connection from the asyncpg pool. If the request handler subsequently rolls the transaction back (e.g., business-logic error), the `last_used_at` UPDATE is rolled back too — which is acceptable behavior (the request didn't complete, so "last used" arguably didn't happen).
- The SELECT-then-UPDATE pattern is not strictly atomic without a row-level lock, but for `last_used_at` writes — where two concurrent requests using the same token would both write `now()` and one would win — last-writer-wins is acceptable. No SELECT FOR UPDATE needed.

**Validation.**
- New integration test: `tests/integration/test_api_token_last_used.py` — issue two authed requests with the same token; assert `last_used_at` on the row is non-null after the first, and strictly greater (>=) after the second.
- New integration test: assert that an invalid token (auth-fail path) does NOT create or update any `api_tokens` row (`last_used_at` stays NULL for a known good token if only invalid-token attempts were made).
- New integration test: assert that auth success followed by a business-logic 4xx rollback leaves `last_used_at` updated (the UPDATE is in the auth transaction, but if the auth-context dependency commits at end-of-dependency rather than end-of-request, the update persists). Verify the actual commit boundary by reading `app/db.py` + `app/auth.py` interaction; if the auth dependency does NOT commit independently (i.e., one transaction spans the entire request), then a rolled-back request reverts `last_used_at`. Document the observed behavior in the test.
- Existing 852 tests still pass.
- `pre-commit run --all-files` clean.
- `mypy app/` strict clean.

**Risk level.** Medium. Auth path change. Per-request latency increase ~1ms acceptable but measured. The transaction-boundary question (last_used_at writes survive rollback or not?) is contract-defining; the answer must be documented in the test.

**Reversibility.** High. `git revert` removes the UPDATE; behavior reverts to "never written."

**Pre-commit verification.** Hooks pass. Unit tests pass. Integration tests added in this commit run via `pytest tests/integration/test_api_token_last_used.py`.

**Observability.** No new structured-log event. `last_used_at` is the persisted signal; no need for a `last_used_at.written` event log.

**Test changes.** New `tests/integration/test_api_token_last_used.py` with 3 test functions per validation list above.

**Rollback plan.** `git revert`. If the auth-tx-rollback behavior turns out to be problematic in 5D's RLS suite run (e.g., riskd_app_login lacks UPDATE on api_tokens — see watch point below), an emergency revert plus a fix-up commit re-implementing the writer after the auth-dependency transaction commits.

**Watch point for 5D.** `riskd_app_login` inherits `riskd_app`'s grants, which per `0001_initial.py:325` includes UPDATE on all tables in `public`. The api_tokens UPDATE must succeed under the new role. 5D's full-suite run is the catch.

**Reviewer routing.** Never Skip per CLAUDE.md: "Any change to authentication, authorization, credential handling." → **Full standard panel: senior-engineer + security-auditor + code-flow-reviewer + test-reviewer** (because tests change). Security-auditor scrutinizes: (a) UPDATE only on success path, (b) UPDATE uses both id and token_hash predicates, (c) transaction boundary documented, (d) no token leak in updated row.

---

### 5A.6 — Migration 0006: `last_used_at` supporting index

**Theme.** Add the descending index on `(tenant_id, last_used_at DESC)` to support future "stale token" queries. Migration only; no app code change in this commit.

**Files changed.**
- `alembic/versions/0006_api_tokens_last_used_index.py` — new migration. Revises 0005.

**Specifics.**
- Migration upgrade:
  ```sql
  CREATE INDEX ix_api_tokens_tenant_last_used
    ON api_tokens (tenant_id, last_used_at DESC NULLS LAST);
  ```
- Migration downgrade:
  ```sql
  DROP INDEX IF EXISTS ix_api_tokens_tenant_last_used;
  ```
- `NULLS LAST` matters: future query "tokens never used in last N days" naturally orders nulls (never-used tokens) at the end. Reviewable.
- Index name follows the existing `ix_*` convention for non-unique indexes.
- Migration template matches `0005_tenants_updated_at.py` shape (per verification).
- Migration runs as superuser (alembic-as-superuser pattern); no grant adjustments needed.

**Validation.**
- `docker compose exec app alembic upgrade head` — applies cleanly.
- `docker compose exec app alembic downgrade base && docker compose exec app alembic upgrade head` — round-trips cleanly.
- `docker compose exec postgres psql -U riskd -d riskd -c '\d ix_api_tokens_tenant_last_used'` — confirms index exists with correct columns + order + NULLS LAST.
- All 852+ tests pass.

**Risk level.** Low. Additive index; no row data changes.

**Reversibility.** High. `alembic downgrade -1` drops the index.

**Pre-commit verification.** Hooks pass.

**Observability.** None.

**Test changes.** None — the index supports future queries; no test asserts its presence beyond migration round-trip.

**Rollback plan.** `alembic downgrade 0005` + `git revert`.

**Declared breaks.** None.

**Reviewer routing.** Never Skip: "Any change to migrations or schema." → **Standard panel + db-reviewer**: senior-engineer + security-auditor + code-flow-reviewer + db-reviewer.

---

### 5A.7 — Migration 0007: `ux_decisions_tenant_request` UNIQUE widening + UNIQUE-widening tests + BUGS.md drain

**Theme.** Widen the decisions UNIQUE from `(tenant_id, request_id)` to `(tenant_id, request_type, request_id)`. Add integration tests confirming booking + modification with the same `request_id` both succeed. Mark Phase 3 BUGS.md entry RESOLVED.

**Files changed.**
- `alembic/versions/0007_decisions_unique_widen.py` — new migration. Revises 0006.
- `tests/integration/test_decisions_unique_widening.py` — new test file.
- `tests/integration/test_modification_endpoint.py` — update `test_modification_reusing_booking_request_id_returns_409` test name + body: same `request_id` across types now succeeds (no 409 expected). Rename the test to reflect new contract; assertion flips.
- `app/api/booking.py` + `app/api/modification.py` — update inline comments referencing the BUGS.md entry to say "RESOLVED in Phase 5A.7 (migration 0007)."
- `.claude/BUGS.md` — append `RESOLVED: 5A.7 (migration 0007)` to the UNIQUE widening entry. Also append `RESOLVED: 5A.1 + 5A.2 (lockfile + format-sync)` to the ruff drift entry.

**Specifics.**
- Migration upgrade SQL:
  ```sql
  ALTER TABLE decisions DROP CONSTRAINT ux_decisions_tenant_request;
  CREATE UNIQUE INDEX ux_decisions_tenant_request_type
    ON decisions (tenant_id, request_type, request_id);
  ```
- Migration downgrade SQL:
  ```sql
  DROP INDEX IF EXISTS ux_decisions_tenant_request_type;
  ALTER TABLE decisions
    ADD CONSTRAINT ux_decisions_tenant_request UNIQUE (tenant_id, request_id);
  ```
- Critical: downgrade only succeeds if no booking + modification share a `request_id` (otherwise the old narrower constraint can't be reinstated). Document this in the migration comment.
- The new shape is a UNIQUE INDEX (not a CONSTRAINT) — this is intentional. The DB still enforces uniqueness; UniqueViolationError fires identically. The 409 try/except in booking + modification stays as defense-in-depth.
- No data migration needed: the new shape is a strict widening; all existing rows that satisfied the old constraint also satisfy the new one.
- Constraint name verified in `0001_initial.py:141` as `ux_decisions_tenant_request` (no typo guard needed).

**Validation.**
- `docker compose exec app alembic upgrade head` clean.
- `docker compose exec app alembic downgrade base && docker compose exec app alembic upgrade head` round-trip clean.
- `psql ... '\d decisions'` confirms the new unique index exists and the old constraint is gone.
- New integration test `test_booking_and_modification_share_request_id`: POST booking + POST modification with same `request_id`; both return 200; two rows in `decisions` distinguished by `request_type`.
- New integration test `test_duplicate_booking_same_request_id_returns_409`: POST booking; POST same booking; second returns 409 (existing 409 path still fires on intra-type duplication).
- New integration test `test_duplicate_modification_same_request_id_returns_409`: same for modification.
- Updated existing test `test_modification_reusing_booking_request_id_returns_409` → rename to `test_modification_reusing_booking_request_id_now_succeeds`; assert 200 + distinct row in decisions.
- Full integration suite passes.
- 852 + new tests → ~855 total.

**Risk level.** Medium. Schema change with rollback-asymmetry (downgrade can fail if rows exist that violate the old constraint). Mitigation: the rollback path is exercised in CI via round-trip, and is documented as "only safe before any cross-type request_id reuse."

**Reversibility.** Medium. See above.

**Pre-commit verification.** Hooks pass. Unit tests pass. Integration tests added.

**Observability.** No new log event. UniqueViolation continues to surface as before.

**Test changes.**
- New file: `tests/integration/test_decisions_unique_widening.py` with 3 tests above.
- Modified: `tests/integration/test_modification_endpoint.py` — rename + flip one test.

**Rollback plan.** `alembic downgrade 0006` reinstates the old constraint, BUT only safe before any cross-type request_id reuse. `git revert` reverts the test changes. The BUGS.md entries should be un-marked.

**Declared breaks.** None within this commit. (5A.5's test changes don't interact with 5A.7's UNIQUE shape.)

**Reviewer routing.** Never Skip (migration + UNIQUE constraint touching business idempotency). **Full panel: senior-engineer + security-auditor + code-flow-reviewer + db-reviewer + test-reviewer.** DB-reviewer specifically verifies: (a) constraint name matches 0001 exactly, (b) downgrade SQL doesn't silently corrupt, (c) UNIQUE INDEX semantics match the prior UNIQUE CONSTRAINT for the FK / 409 path, (d) round-trip test is in the validation list.

---

## Batch 5A summary

- 7 commits.
- New migrations: 0006 (last_used_at index), 0007 (UNIQUE widening).
- New code: `last_used_at` writer in `auth.py`.
- New tests: api_token_last_used (3 tests), decisions_unique_widening (3 tests), test_modification_endpoint flip (1 renamed test).
- Existing tests: 852+ retain pass; case-1 + case-2 regression unaffected.
- BUGS.md entries marked RESOLVED: ruff drift (in 5A.1 + 5A.2), UNIQUE widening (in 5A.7).
- `.ai/conventions.md` gains a "Dependency locking" section.
- Container runs as UID 1000 `app` user; smoke verifies.

End of batch: REPORT_PHASE_5A.md is the operator checkpoint. Cumulative test count target: ~858. Reviewer panel verdict distribution captured in the report.
