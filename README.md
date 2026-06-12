# freightsentry-riskd

Real-time fraud detection SaaS for freight aggregation platforms. Single Python service (FastAPI + asyncpg + Pydantic v2), Postgres-only, multi-tenant via Row-Level Security.

## Quick links

- **Working rules**: `.ai/conventions.md`
- **Architectural decisions**: `.ai/decisions.md`
- **Schema reference**: `.ai/schema.md`
- **Scoring + DSL contract**: `.ai/rules.md`
- **IP enrichment + context building**: `.ai/enrichment.md`
- **Library gotchas**: `.ai/gotchas/index.md`
- **Workflow (commit cycle, reviewer panel)**: `CLAUDE.md`
- **Phase history + decisions**: `docs/history.md`
- **Project status + mid-run deviations**: `.ai/system-status.md`, `.claude/STATUS.md`

## Stack

- Python 3.13+
- FastAPI + uvicorn
- asyncpg
- Pydantic v2 + pydantic-settings
- Alembic
- PostgreSQL 16
- Docker Compose (local dev) · ECS Fargate (production)

## Local development

```
cp .env.example .env       # fill in operator-supplied secrets (HMAC key, enrichment tokens)
docker compose up -d       # bring up app + postgres
docker compose exec app alembic upgrade head
docker compose exec app pytest tests/ -v --asyncio-mode=auto
```

## Project status

Phases 1-8 complete; pre-launch. Production deploy to `ca-central-1` is the next operator-driven step. See `.ai/system-status.md` for the current phase status and `docs/history.md` for the per-phase narrative.
