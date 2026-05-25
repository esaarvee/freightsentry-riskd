# freightsentry-riskd — AI Context Index
> Points to knowledge. Doesn't hold it. Load only what the task needs.

## Project Identity
- **App**: freightsentry-riskd — real-time fraud detection SaaS for freight aggregation platforms
- **Stack**: Python 3.13+ · FastAPI · asyncpg · Pydantic v2 · Alembic
- **Storage**: PostgreSQL 16 (single store; multi-tenant via RLS + `tenant_id` columns; JSONB customer baselines)
- **Transport**: REST (FastAPI / uvicorn)
- **Infra**: ECS Fargate (production `ca-central-1`, test/staging `us-east-2`) · Docker Compose (local) | Ceiling: 100 TPS, p95 < 200ms
- **Config prefix**: `FG_` (pydantic-settings, sourced from `.env`)

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

- **Review cadence**: every code-path commit runs the full parallel review panel; every doc-only commit runs the doc-reviewer. The "When to Skip" clause below covers trivial cases (typo / comment / whitespace / config-only).
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
| Tests | `pytest tests/ -v --asyncio-mode=auto` |
| Schema migrate (local) | `docker compose exec app alembic upgrade head` |
| Schema round-trip | `docker compose exec app alembic downgrade base && docker compose exec app alembic upgrade head` |
| Schema verify | `docker compose exec postgres psql -U riskd -d riskd -c '\dt+ public'` |
| Local stack up | `docker compose up -d` |
| Local stack down | `docker compose down -v` |

### 6-Step Commit Cycle

1. **Implement** — write the code changes for one planned commit
2. **Validate** — run the validation commands for the affected paths. All must pass.
3. **Review** — first detect commit type, then route to the appropriate review panel.

   **Detect commit type**: collect all changed file paths via `git diff --name-only HEAD` and `git diff --cached --name-only` (which includes newly staged files). If EVERY changed file is a `.md` file (anywhere in the repo), under `.ai/`, or under `docs/` → **doc-only path**. If ANY file falls outside those patterns (`.py`, `.yaml`, `.sql`, `.json`, `Dockerfile`, `.toml`, etc.) → **code path**.

   **Slice the plan file in the invocation prompt**: when a plan file is in play, do not have reviewers read the whole file. Specify the current commit position (commit number + brief title) and the section ranges or section headers for the current commit and upcoming commits N+1 through M. Reviewers fetch only those sections via Read offset/limit or section anchors.

   Example invocation suffix: `Plan file: PLAN_PHASE_1.md, current commit: 1B.2 (initial migration), upcoming commits: 1B.3 through 1D.8 sections. Read only those sections.`

   The reviewer agents are tuned to load plan context lazily — telling them up-front which sections matter avoids loading thousands of lines of unrelated commit history per cycle.

   **Doc-only path** (single agent):
   - Doc Reviewer: `Agent(subagent_type="general-purpose", prompt="Read .claude/agents/doc-reviewer.md and follow its instructions to review the current uncommitted changes.")`

   Doc-only merge gate:
   - REJECT → must fix before proceeding
   - NEEDS EDITS → fix before merge
   - MINOR TWEAKS → accept with note
   - PUBLISH → proceed

   **Code path** — invoke all reviewer agents as PARALLEL subagents:

   Always run:
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
| Senior Engineer | `.claude/agents/senior-engineer-reviewer.md` | REJECT → NEEDS MAJOR WORK → NEEDS MINOR FIXES → APPROVED WITH RESERVATIONS → SHIP IT | Every code-path commit cycle |
| Security Auditor | `.claude/agents/security-auditor.md` | CRITICAL VULNERABILITY → HIGH RISK → MEDIUM RISK → LOW RISK / CLEAN | Every code-path commit cycle |
| Code Flow Reviewer | `.claude/agents/code-flow-reviewer.md` | REJECT → NEEDS REFACTOR → MINOR ISSUES → CLEAN | Every code-path commit cycle |
| Test Reviewer | `.claude/agents/test-reviewer.md` | GARBAGE → NEEDS WORK → ACCEPTABLE → ACTUALLY GOOD | Tests changed |
| DB Reviewer | `.claude/agents/db-reviewer.md` | REJECT → NEEDS MAJOR WORK → NEEDS MINOR FIXES → APPROVED WITH RESERVATIONS → SHIP IT | `alembic/versions/`, `*.sql`, or ORM/Pydantic model files in diff |
| Doc Reviewer | `.claude/agents/doc-reviewer.md` | REJECT → NEEDS EDITS → MINOR TWEAKS → PUBLISH | Doc-only commits |

### Reviewer routing decision tree

After detecting code-path (per step 3 above), classify the diff and route accordingly. Apply rules in order; first match wins. The **Never Skip** override applies regardless.

#### Complete skip — no review

The diff qualifies for complete skip if ALL hold:
- No changes to `.py`, `.yaml`, `.sql`, `.json`, `.toml`, `Dockerfile`, `Makefile`, or `pyproject.toml`
- No changes to test files
- Total line change under 20 lines OR purely whitespace, formatting, or comment text

Specific always-skip patterns:
- Typo fix in comment or docstring (no behavior change)
- Formatting-only change (whitespace, line breaks, indentation)
- Comment text update without changing what the comment refers to
- Markdown file edits in `docs/` outside `docs/runbooks/`
- Changes purely inside `.git/`, `.vscode/`, `.idea/`, or IDE configuration

#### Lightweight skip — single reviewer

If complete skip doesn't apply, check lightweight skip criteria. Single reviewer invocation for narrowly-scoped changes:

- Single-line constant change with no surrounding logic change → **senior-engineer only**, *unless the constant controls a timeout, size cap, iteration bound, retry count, or any other security-load-bearing limit, in which case full panel runs*
- Variable rename across single file with no semantic change → **senior-engineer only**
- Adding test cases to existing test file, no production code change → **test-reviewer + senior-engineer**
- Single dependency version bump in `pyproject.toml` → **security-auditor + senior-engineer**
- TODO/FIXME comment add/remove → no reviewer; complete skip

#### Partial panel — diff-routed subset

If lightweight skip doesn't apply, check partial-panel criteria:

- ONLY `app/rules.yaml` changed (weights or conditions, not new rule classes) → **senior-engineer + code-flow**
- ONLY documentation under `docs/` outside `.ai/` → **doc-reviewer only**
- ONLY config-value change in `docker-compose*.yml` or `.env.example` → **security-auditor + senior-engineer**
- ONLY test file additions or changes, no production code → **test-reviewer + senior-engineer + code-flow-reviewer**

#### Full panel

If none of the above apply, run the full code-path panel as specified in step 3 (senior-engineer + security-auditor + code-flow-reviewer; test-reviewer when tests changed; db-reviewer when migrations/`*.sql`/model files changed).

#### Never Skip (overrides all above)

Regardless of size or routing, never skip review for:
- Any change to authentication, authorization, credential handling, or secret loading
- Any change to migrations or schema (including comments — migration comments are load-bearing for future audit)
- Any change to `app/rules.yaml` weights, thresholds, or conditions that adds or removes a rule (vs adjusting an existing rule's parameters)
- Any change to the scoring formula or noisy-OR composition
- Any change to the DSL evaluator (`app/dsl.py`) — the rule-eval sandbox
- Any change to RLS policies (`alembic/versions/*` touching `CREATE POLICY` / `ENABLE ROW LEVEL SECURITY`)
- Any change to PII handling (HMAC at egress / `signals.hmac_hex`)
- Any change marked by the operator as significant
- Any commit that introduces a new file (vs editing existing files), unless purely `.md` documentation under `docs/`

#### Operator override

If the user says "skip review" or "just commit", respect that and include in the commit message footer: `Review: operator-skipped`.

If the user requests a class-wide skip rule change, surface as a question — the change should land in CLAUDE.md explicitly, not as implicit conversation memory.

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
