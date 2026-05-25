# Gotchas Index
> Load this file first. Pull only the sub-file relevant to your task.
> Cap: each sub-file ≤ 60 lines. Promote to `.ai/conventions.md` when a pattern recurs 3+ times.

| Scope | File | Load when |
|---|---|---|
| Python async + Pydantic + FastAPI | `.ai/gotchas/python.md` | any Python code work |
| Postgres + asyncpg + JSONB + RLS | `.ai/gotchas/postgres.md` | any schema, migration, or DB-access work |
| Scoring / float math | `.ai/gotchas/scoring.md` | scorer, rules, velocity work |
| Local dev / Docker Compose | `.ai/gotchas/local-dev.md` | local stack startup, env config |

## Module → Gotcha files

When working on a specific module, load these gotcha files:

| Module | Load these gotcha files |
|---|---|
| `app/scoring.py` | scoring.md, python.md |
| `app/rules.py` | scoring.md, python.md |
| `app/dsl.py` | python.md |
| `app/baseline.py` | postgres.md, python.md |
| `app/enrich.py` | postgres.md, python.md |
| `app/velocity.py` | postgres.md |
| `app/context.py` | python.md |
| `app/db.py`, `app/auth.py` | postgres.md, python.md |
| `app/api/*.py` | python.md |
| `alembic/versions/*.py` | postgres.md |

## Maintenance rules

- **Append** when implementation reveals a behavior that contradicts naive expectations.
- **Promote** to `.ai/conventions.md` when the same gotcha recurs in 3+ different tasks — then delete from here.
- **Expire** when the relevant dependency is upgraded — audit the file and remove stale entries.
- **Never** let a sub-file exceed 60 lines; if it does, promote the oldest/most general entries to `.ai/conventions.md`.
