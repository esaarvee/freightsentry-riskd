# Local dev gotchas

## Docker Compose: app + postgres only

`docker-compose.yml` (added in 1B.1) declares two services: `app` (Python 3.13+, FastAPI via uvicorn) and `postgres` (postgres:16-alpine). No Redis, no MySQL, no second worker, no LLM container.

```
docker compose up -d           # both services
docker compose up -d postgres  # postgres only (for host-run tests)
docker compose down -v         # tear down + delete volumes
```

## DB URL hostname differs by execution context

Compose-up form: hostname is the service name (`postgres`). From the host (running tests outside the container): `localhost`.

```
# inside the container (e.g. docker compose run --rm app pytest tests/integration/)
DATABASE_URL=postgresql://riskd:riskd@postgres:5432/riskd

# from the host
DATABASE_URL=postgresql://riskd:riskd@localhost:5432/riskd
```

## `.env` is gitignored; `.env.example` is committed

Never commit `.env`. Always copy from `.env.example` and fill in operator-supplied secrets (MaxMind license key, IP2Proxy download token, HMAC secret) locally. CI / staging / prod inject env vars via the platform secret manager.

## Alembic must run with the right `DATABASE_URL`

Round-trip test from the host:

```
DATABASE_URL=postgresql://riskd:riskd@localhost:5432/riskd \
  alembic downgrade base && alembic upgrade head
```

A common foot-gun: running `alembic upgrade head` with the in-container URL (`@postgres:5432`) from the host — it silently fails to connect.
