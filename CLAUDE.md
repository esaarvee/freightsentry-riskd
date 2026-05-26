# freightsentry-riskd — AI Context Index
> Points to knowledge. Doesn't hold it. Load only what the task needs.

## Project Identity
- **App**: freightsentry-riskd — real-time fraud detection SaaS for freight aggregation platforms
- **Stack**: Python 3.13+ · FastAPI · asyncpg · Pydantic v2 · Alembic
- **Storage**: PostgreSQL 16 (single store; multi-tenant via RLS + `tenant_id` columns; JSONB customer baselines)
- **Transport**: REST (FastAPI / uvicorn)
- **Infra**: ECS Fargate (production `ca-central-1`, test/staging `us-east-2`) · Docker Compose (local) | Ceiling: 100 TPS, p95 < 200ms
- **Config**: pydantic-settings, no env prefix (env var names match field names verbatim, e.g. `DATABASE_URL`, `HMAC_SECRET`); sourced from `.env` in dev, platform secret manager in prod

## Load by Task
| Task | Read |
|---|---|
| Any coding task | `.ai/conventions.md` |
| Any test-writing task | `.ai/conventions.md` + `.ai/gotchas/index.md` |
| Enrichment work | `.ai/enrichment.md` |
| Scoring model or rule design | `.ai/rules.md` + `app/rules.yaml` |
| Stream payloads or async patterns | `.ai/conventions.md` (asyncio + asyncpg sections) |
| Architecture or tech-stack proposal | `.ai/decisions.md` |
| DB schema or migrations | `alembic/versions/` |
| REST API work | `app/api/<relevant>.py` |
| Plan mode / commit cycle | This file + `.claude/agents/*.md` |
| Repo navigation | Use Glob/Grep tools |

## Plan Mode — Commit Cycle

When working in plan mode on multi-step tasks, follow this disciplined commit cycle to prevent quality drift as context fills up.
When planning work, create a logical sequence of atomic commits. Each commit in the plan must include:

- What changes are made
- What tests are added or modified
- Validation criteria to confirm the commit is correct

### Before finalizing the plan

Defaults (do NOT ask the user — they are pre-decided in this file):

- **Review cadence**: every code-path commit runs the full parallel review panel; every doc-only commit runs the doc-reviewer. Trivial-path commits skip reviewers entirely (see "Triage gate" below). Lightweight-path commits run a narrowly scoped subset.
- **Max review cycles**: iterate until APPROVED WITH RESERVATIONS or higher; if still not approved after **3 cycles**, stop and surface the disagreement to the user. The cap is an escape hatch for reviewer/code deadlock, not a quality compromise.

The only per-plan preference still asked via AskUserQuestion:

- **Commit strategy**: atomic (one logical change per commit) / batched (group related changes)

Resolve every other architectural decision via AskUserQuestion DURING plan creation and record the answers in a "Decisions absorbed" table at the top of the plan. During execution the plan is the source of truth — see "Autonomous Execution" below.

### Plan Structure

Each planned commit should specify:
- **Changes**: what files are modified and why
- **Tests**: what test changes accompany the code
- **Validation**: which commands verify correctness

**Declared breaks subsection (per commit)**: identify transitional states the commit introduces — code, schema, or contracts that are temporarily broken or incomplete because the resolving change lands in a later commit.

For each transitional state, add a `Declared breaks` subsection to the commit. Each entry specifies:
- **Scope**: what specifically is in a transitional state (be precise — 'field X removed from BookingRequest', not 'model changed')
- **Resolved in**: which later commit number resolves the transitional state, with a one-line reference to what that commit does

Reviewers consult this subsection to plan-suppress findings that match declared scope. Reviewer correctness depends on declarations being specific enough to distinguish declared-and-expected breakage from incidental bugs. Over-declaring degrades reviewer accuracy.

When a commit introduces no transitional state, omit the `Declared breaks` subsection entirely. Do not write 'Declared breaks: none' — absence is the signal.

Common cases to declare:
- Function/method added but not yet called by production code
- Pydantic model field added/moved/removed with consumer update in later commit
- Schema column added before writer wired up (or vice versa)
- Test deleted before replacement test lands
- Interface introduced before its second implementation
- Migration adds column that backfill commit will populate
- Configuration value added before code reads it (or vice versa)

Cases that are NOT declared breaks (flag normally, do not suppress):
- Existing functionality unintentionally broken
- Test coverage gap not addressed by any later commit
- Unused code with no future caller in the plan
- Dead branches with no resolving change

### Validation Commands

| Task | Command |
|---|---|
| Python lint | `ruff check app/ tests/` |
| Python type-check | `mypy app/` |
| Tests (full) | `pytest tests/ -v --asyncio-mode=auto` |
| Tests (unit only, fast) | `pytest tests/unit/ -x --no-header -q` |
| Schema migrate (local) | `docker compose exec app alembic upgrade head` |
| Schema round-trip | `docker compose exec app alembic downgrade base && docker compose exec app alembic upgrade head` |
| Schema verify | `docker compose exec postgres psql -U riskd -d riskd -c '\dt+ public'` |
| Local stack up | `docker compose up -d` |
| Local stack down | `docker compose down -v` |

### Pre-commit enforcement

Validation gates run via `pre-commit` hooks as non-bypassable enforcement. Configuration in `.pre-commit-config.yaml` at repo root.

Hooks fire on `git commit` and block the commit if any fail:

- `ruff check --fix --exit-non-zero-on-fix` — lint with auto-fix
- `ruff format` — Python formatting
- `mypy app/` (strict mode) — type checking on `app/` only
- `pytest tests/unit/ -x --no-header -q` — fast unit tests only (integration tests in `tests/integration/` are too slow for the per-commit hook)

Install once per worktree:

```bash
pip install pre-commit
pre-commit install
```

Subagents MUST NOT mark a task done until pre-commit passes locally. Failures from pre-commit are the implementer's responsibility to fix; they are NOT material for the reviewer panel — if pre-commit catches it, reviewers shouldn't need to.

**Bypass policy.** `git commit --no-verify` is allowed only for declared-break commits introducing transitional state, and only when the declared break explicitly names which gates are bypassed and why. The next commit in the same plan must restore the gates. Outside declared breaks, `--no-verify` is a workflow violation — reviewers flag any commit using `--no-verify` without matching declared-break documentation as a process failure.

### 6-Step Commit Cycle

1. **Implement** — write the code changes for one planned commit
2. **Validate** — run the validation commands for the affected paths. Pre-commit hooks enforce the basic gates (lint, format, types, unit tests); the agent runs broader validation as needed (integration tests, schema round-trip).
3. **Review** — first apply the triage gate, then route to the appropriate reviewer panel (see "Triage gate and reviewer routing" below).

   **Slice the plan file in the invocation prompt**: when a plan file is in play, do not have reviewers read the whole file. Specify the current commit position (commit number + brief title) and the section ranges or section headers for the current commit and upcoming commits N+1 through M. Reviewers fetch only those sections via Read offset/limit or section anchors.

   Example invocation suffix: `Plan file: PLAN_PHASE_1.md, current commit: 1B.2 (initial migration), upcoming commits: 1B.3 through 1D.8 sections. Read only those sections.`

   The reviewer agents are tuned to load plan context lazily — telling them up-front which sections matter avoids loading thousands of lines of unrelated commit history per cycle.

   **Doc-only path** (single agent, applies to triage-gate documentation routes):
   - Doc Reviewer: `Agent(subagent_type="general-purpose", prompt="Read .claude/agents/doc-reviewer.md and follow its instructions to review the current uncommitted changes.")`

   Doc-only merge gate:
   - REJECT → must fix before proceeding
   - NEEDS EDITS → fix before merge
   - MINOR TWEAKS → accept with note
   - PUBLISH → proceed

   **Code path** — invoke reviewer agents as PARALLEL subagents per the routing in "Triage gate and reviewer routing".

   Standard-panel agents:
   - Senior Engineer Reviewer: `Agent(subagent_type="general-purpose", prompt="Read .claude/agents/senior-engineer-reviewer.md and follow its instructions to review the current uncommitted changes. Run git diff to see the changes.")`
   - Security Auditor: `Agent(subagent_type="general-purpose", prompt="Read .claude/agents/security-auditor.md and follow its instructions to review the current uncommitted changes. Run git diff to see the changes.")`
   - Code Flow Reviewer: `Agent(subagent_type="general-purpose", prompt="Read .claude/agents/code-flow-reviewer.md and follow its instructions to review the current uncommitted changes. Run git diff to see the changes.")`

   When tests changed, also run:
   - Test Reviewer: `Agent(subagent_type="general-purpose", prompt="Read .claude/agents/test-reviewer.md and follow its instructions to review the current uncommitted test changes. Run git diff to see the changes.")`

   When diff includes any file matching `alembic/versions/`, `*.sql`, or ORM/Pydantic model files (e.g. `app/models.py`, `app/models/*.py`), also run:
   - DB Reviewer: `Agent(subagent_type="general-purpose", prompt="Read .claude/agents/db-reviewer.md and follow its instructions to review the current uncommitted changes. Run git diff to see the changes.")`

   Code path merge gate:

   If all reviewers in the panel return their cleanest verdict (SHIP IT / CLEAN / ACTUALLY GOOD / PUBLISH / LOW RISK) on first pass, the merge gate is satisfied. Second review cycle is unnecessary and is skipped.

   Second cycle runs only when at least one reviewer returns a verdict requiring fixes.

   - REJECT / CRITICAL VULNERABILITY / NEEDS REFACTOR (any reviewer) → must fix before proceeding
   - NEEDS MAJOR WORK (any reviewer) → must fix before proceeding
   - NEEDS MINOR FIXES / HIGH RISK / MINOR ISSUES → must fix before proceeding
   - APPROVED WITH RESERVATIONS / MEDIUM RISK → must fix before proceeding
   - CLEAN / SHIP IT / LOW RISK → proceed

4. **Iterate** — fix issues from review. Re-validate. Re-review if verdict < APPROVED WITH RESERVATIONS (up to max cycles).
5. **Commit** — stage and commit with a descriptive message. Do not push unless asked.
6. **Proceed** — move to the next planned commit.

### Quality standards

- No shortcuts. No laziness. Quality is non-negotiable.
- Include this workflow context in all task descriptions.

### Review Agents

| Agent | File | Verdicts | Invoked When |
|---|---|---|---|
| Senior Engineer | `.claude/agents/senior-engineer-reviewer.md` | REJECT → NEEDS MAJOR WORK → NEEDS MINOR FIXES → APPROVED WITH RESERVATIONS → SHIP IT | Standard-panel code commits |
| Security Auditor | `.claude/agents/security-auditor.md` | CRITICAL VULNERABILITY → HIGH RISK → MEDIUM RISK → LOW RISK / CLEAN | Standard-panel code commits |
| Code Flow Reviewer | `.claude/agents/code-flow-reviewer.md` | REJECT → NEEDS REFACTOR → MINOR ISSUES → CLEAN | Standard-panel code commits |
| Test Reviewer | `.claude/agents/test-reviewer.md` | GARBAGE → NEEDS WORK → ACCEPTABLE → ACTUALLY GOOD | Tests changed |
| DB Reviewer | `.claude/agents/db-reviewer.md` | REJECT → NEEDS MAJOR WORK → NEEDS MINOR FIXES → APPROVED WITH RESERVATIONS → SHIP IT | `alembic/versions/`, `*.sql`, or ORM/Pydantic model files in diff |
| Doc Reviewer | `.claude/agents/doc-reviewer.md` | REJECT → NEEDS EDITS → MINOR TWEAKS → PUBLISH | Doc-only commits |

### Triage gate and reviewer routing

Apply the triage gate BEFORE any reviewer routing. The triage gate is a fast filter that bypasses reviewer panels entirely for genuinely trivial changes. Only changes that don't qualify for the trivial path proceed to the lightweight / standard / never-skip ladder.

#### Triage gate — trivial path (no reviewers)

A change qualifies for the trivial path if ALL changed files fall in these categories, AND the change is non-substantive:

**Config files (non-substantive edits only):**
- `pyproject.toml` (tool configuration, formatter rules, line length — NOT dependency adds/removes/version bumps)
- `uv.lock`, `poetry.lock`, `requirements*.txt` (lock-file syncs without explicit dep changes)
- `.gitignore`, `.dockerignore`, `.editorconfig`
- `.env.example` (placeholder additions only — must not contain real values)
- `alembic.ini` (alembic configuration, NOT new migration files)
- `.pre-commit-config.yaml` (hook version bumps; new hooks added go to standard path)

**Documentation files (non-decision edits):**
- `README.md` (description, usage, links — NOT architectural intent changes)
- Files under `docs/` (NOT `docs/runbooks/` — those carry operational procedures)
- Comments and docstrings in any file (whitespace + `#` / `"""` lines only, no executable changes)

**Test data (data only):**
- Files under `tests/fixtures/` or `tests/data/` (JSON, YAML, CSV) — NOT test code

**Text-only fixes:**
- Typo fixes in log messages, error messages, user-facing strings (no behavior change)
- Whitespace, formatting, indentation only

Trivial path behavior:
- Pre-commit hooks still run (mandatory)
- No reviewer panel invocation
- Commit proceeds; commit message footer: `Review: triage-gate-trivial`
- No mid-batch checkpoint required

#### Lightweight path — single reviewer or two-reviewer subset

If trivial doesn't apply, check lightweight criteria. Narrow-scope changes get a small reviewer subset:

- Single dependency add/bump/remove in `pyproject.toml` → **security-auditor + senior-engineer**
- Single-line constant change with no surrounding logic change → **senior-engineer only**, *unless the constant controls a timeout, size cap, iteration bound, retry count, or any security-load-bearing limit — then full panel runs*
- Variable rename across single file with no semantic change → **senior-engineer only**
- Adding test cases to existing test file, no production code change → **test-reviewer + senior-engineer**
- ONLY `app/rules.yaml` changed (weights or conditions, not new rule classes) → **senior-engineer + code-flow**
- ONLY documentation under `docs/runbooks/` (operational procedures) → **doc-reviewer only**
- ONLY config-value change in `docker-compose*.yml` or production deploy config → **security-auditor + senior-engineer**
- ONLY test file additions or changes, no production code → **test-reviewer + senior-engineer + code-flow-reviewer**

#### Standard path — full panel

If neither trivial nor lightweight applies, run the full code-path panel (senior-engineer + security-auditor + code-flow-reviewer; test-reviewer when tests changed; db-reviewer when migrations/SQL/model files changed).

#### Never Skip (overrides all above)

Regardless of size or routing, never skip review for:
- Any change to authentication, authorization, credential handling, or secret loading
- Any change to migrations or schema (including comments — migration comments are load-bearing for audit)
- Any change to `app/rules.yaml` weights, thresholds, or conditions that adds or removes a rule (vs adjusting an existing rule's parameters — lightweight)
- Any change to the scoring formula or noisy-OR composition (`app/scoring.py`)
- Any change to the DSL evaluator (`app/dsl.py`) — the rule-eval sandbox
- Any change to RLS policies (`alembic/versions/*` touching `CREATE POLICY` / `ENABLE ROW LEVEL SECURITY`)
- Any change to PII handling (HMAC at egress / `signal_helpers.hmac_hex`)
- Any change marked by the operator as significant
- Any commit that introduces a new `.py` file under `app/` (vs editing existing files)

#### Borderline rule

When unsure between trivial and lightweight, or between lightweight and standard, route to the heavier path. The cost of an unnecessary reviewer pass is much lower than the cost of a missed catch. Specifically:

- A config change that affects runtime behavior (e.g. pyproject dependency that pulls a different library version): lightweight at minimum
- A docstring update that contains executable code examples: standard path
- A README change that revises architectural intent: standard path (treat as `.ai/decisions.md` edit)
- A `.ai/decisions.md` amendment: ALWAYS standard path with doc-reviewer at minimum

#### Operator override

If the user says "skip review" or "just commit", respect that and include in the commit message footer: `Review: operator-skipped`.

If the user requests a class-wide skip rule change, surface as a question — the change should land in this file explicitly, not as implicit conversation memory.

### Autonomous Execution

The 6-step commit cycle is designed to run for hours without operator intervention once a plan is approved. Three rules support that:

1. **Decisions are baked into the plan, not asked at execution time.** Resolve every architectural choice during plan creation (via AskUserQuestion) and record the answers in the plan's "Decisions absorbed" table. During execution, do NOT introduce new AskUserQuestion prompts. If an unanticipated decision surfaces mid-execution, pick the lowest-risk reversible option, append a row to the `Unforeseen / checkpoints` section of [.claude/STATUS.md](.claude/STATUS.md) in the format `YYYY-MM-DD · commit X.Y · what happened · what to do next`, and continue.

2. **Validation failures stop after the second attempt on the same commit.** If a commit's validation commands fail, fix and re-run once. If they fail a second time, do NOT continue iterating — append a row to `.claude/STATUS.md` `Unforeseen / checkpoints` with the failure detail, leave the working tree as-is (do not partial-commit), and stop. Operator resumes on the next session. This rule prevents infinite fix-loops from consuming a multi-hour run.

3. **Review-cycle deadlocks resolve at 3 cycles.** Reviewers escalate to the user only via the cycle cap (see "Before finalizing the plan"). Otherwise the cycle runs to completion silently.

For multi-hour autonomous runs, the operator should additionally:

- Launch the session with `claude --permission-mode bypassPermissions` (skips all permission prompts; pair with a clean working tree on a feature branch so `git reset --hard` is the recovery path).
- Confirm the plan absorbs every decision before approval.
- Expect to be paged only when a row lands in `.claude/STATUS.md` `Unforeseen / checkpoints`, or at plan boundaries.

The project allowlist in [.claude/settings.json](.claude/settings.json) is a defense-in-depth backstop for sessions started WITHOUT `bypassPermissions` — it covers the highest-frequency non-auto-allowed commands (`pytest`, `ruff check`, `mypy`, `alembic`). Personal/machine-specific allowlist entries belong in `.claude/settings.local.json` (gitignored).

### Tangential issue handling

When a subagent encounters an issue tangential to its current task — a bug in adjacent code, an unclear comment, a missing TODO follow-up, a schema drift, a stale doc — it MUST NOT fix inline or stop work. Instead:

1. Append a structured entry to `.claude/BUGS.md`:

   ```
   ## YYYY-MM-DD — <one-line summary>
   
   Discovered by: <subagent role> during <task ID, e.g. PLAN_PHASE_2.md 2D.3>
   Location: <file:line or N/A>
   Severity: low | medium | high
   Observation: <2-3 sentences of what's wrong>
   Suggested action: <one line> OR "investigation needed"
   ```

2. Continue with the original task.

3. At the next operator checkpoint, the per-batch summary line includes `N issues logged to BUGS.md`.

**EXCEPTION**: severity = high (security risk, data corruption potential, RLS bypass, auth bypass, secret exposure, scoring-formula error that produces wrong decisions) interrupts the task. Surface to `.claude/STATUS.md` immediately and wait for operator instruction before continuing.

`.claude/BUGS.md` is distinct from `.claude/STATUS.md`:
- **BUGS.md** — issues the agent kept going past; operator triages between phases
- **STATUS.md** — checkpoints the agent stopped at; operator addresses before resumption

The operator drains BUGS.md at phase boundaries. Items get triaged into: dropped as out-of-scope, pulled into the current phase's amendment, or scheduled for a future phase. Drained entries get a `RESOLVED: <commit> / DROPPED / DEFERRED to <plan>` line appended; never deleted from history.

### Phase scope discipline

Each work phase has a distinct job. Verification, planning, and execution should not blur into each other during a run.

- **Verification phase** discovers facts about the codebase. Writes to `docs/verification-phase-N.md`. Does NOT change code, plans, or decisions. Reads anywhere the bootstrap prompt directs; writes only the verification doc.

- **Planning phase** produces MASTER_PLAN amendments (if any) and PLAN_PHASE_N.md. Reads verification output and the design context. Does NOT re-explore the codebase; if planning needs facts not in verification, return to verification rather than shortcut.

- **Execution phase** applies the plan. Reads what the plan says to read; modifies what the plan says to modify. Does NOT decide new things or re-plan in flight.

When execution encounters something the plan didn't anticipate:

- **Trivial drift** (a renamed import, a stale path reference, a missing comment): fix inline with a one-line STATUS.md note; commit message references the deviation.
- **Substantive drift** (the plan's approach won't work, a different design is needed, a constraint was missed): STOP. Surface via AskUserQuestion or append to STATUS.md `Unforeseen / checkpoints`. Do not paper over and continue.
- **Tangential issues** (something is wrong, but it's not blocking and not in scope): log to BUGS.md per "Tangential issue handling" above and continue.

Blurring the phases — discovering things in execution, re-planning in flight, amending decisions without operator approval — is how the codebase drifts away from its documented state. The plan is the contract.

### Parallel sessions and worktrees

When two Claude Code sessions need to work on different changes simultaneously (e.g., bugfix while a feature lands; Phase N+1 setup while Phase N wraps), use `git worktree` rather than branch-switching in the main repo.

#### Setup

From the main repo root:

```bash
git worktree add ../freightsentry-riskd-<topic> -b <branch>
```

Examples:
```bash
git worktree add ../freightsentry-riskd-phase-3 -b feature/phase-3
git worktree add ../freightsentry-riskd-bugfix-auth -b fix/auth-rls-gap
```

Each worktree is a separate working directory with its own checked-out branch. Sessions run from their own worktree path:

```bash
cd ../freightsentry-riskd-phase-3
claude  # this session sees only feature/phase-3 state
```

#### Conventions

- Worktree directory names follow `freightsentry-riskd-<topic>` for visual disambiguation in shell prompts and process lists.
- Each worktree gets its own Claude Code session, started from that directory.
- Pre-commit hooks live in `.git/hooks/` (and `.git/` is shared across worktrees) — hooks replicate automatically; no per-worktree install needed.
- `.claude/STATUS.md` and `.claude/BUGS.md` are shared (same `.git/`) — entries from different sessions interleave; the date stamp and task-ID disambiguate.
- Both sessions can read each other's working files via `../freightsentry-riskd-<other>/` paths, but this is rarely needed — coordination happens through STATUS / BUGS / plan files at merge time.

#### When NOT to use worktrees

- For changes that touch the same files concurrently. Use sequential commits on one branch instead — worktrees don't resolve concurrent edit conflicts, they isolate them.
- For trivial changes that take under 30 minutes. Branch switching in the main repo is lower-overhead.
- For exploratory work that may not produce a commit. Worktrees are a commitment to a branch.

#### Cleanup

After the branch merges back to main:

```bash
git worktree remove ../freightsentry-riskd-<topic>
git branch -d <branch>
```

The worktree's `.git` references are cleaned up automatically by `git worktree remove`.