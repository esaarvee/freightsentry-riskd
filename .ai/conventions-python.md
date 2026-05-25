# conventions-python.md — Python (Gateway + shared libs)

> Working rules for Python code under `services/gateway/` and shared Python
> libs. Load alongside `conventions-freightsentry.md` for cross-language
> concerns (FG_ env prefix, sync-path latency budget, dependencies).
>
> Index: see [.ai/conventions.md](./conventions.md).

---

## Code Conventions — Python

- Python 3.14+, type hints on all function signatures
- Leverage 3.14 features: deferred annotations (no `from __future__ import
  annotations` needed), t-strings where useful, except without parens
- FastAPI with Pydantic v2 models for request/response
- Async everywhere: asyncpg for fraud PostgreSQL, asyncmy for platform MySQL,
  redis.asyncio for Redis, grpc.aio for Rules Engine
- Config via pydantic-settings, env prefix FG_ (see
  `conventions-freightsentry.md` for the FG_ vs unprefixed rules and the
  `FG_DB_DSN` / `FG_PII_HMAC_KEY` exception)
- Ruff for linting (line-length 100)
- Tests with pytest + pytest-asyncio
- Docker base image: python:3.14-slim

---

## Testing — Python (Gateway)

- **Framework**: `pytest` + `pytest-asyncio` with `--asyncio-mode=auto`
  - Do **not** add `@pytest.mark.asyncio` when auto mode is active — it's redundant
- **Structure**: `@pytest.mark.parametrize` for table-driven cases
  - Name test functions by **behavior**, verb-first: `test_returns_block_when_ip_is_blacklisted`
- **Assertions**: plain `assert`; `pytest.raises` for exceptions; assert both `status_code` and `detail`
- **Case matrix**: happy path, every exception path, boundary values, `None`/zero inputs
- **Dependency isolation**: `typing.Protocol` for interfaces; `AsyncMock` for async, `MagicMock` for sync
  - Never use bare `MagicMock` for an async callable
  - Patch at point of use: `patch("app.routes.enrichment.get_db")`
  - Prefer FastAPI `Depends` injection over patching
- **HTTP testing**: `httpx.ASGITransport` + `httpx.AsyncClient` — no real server
- **Auth testing**: the `client` fixture in `tests/conftest.py` installs
  `app.dependency_overrides[require_api_key]` to a stub returning a synthetic
  `AuthContext` — route tests don't need to think about auth. Tests that exercise
  the real auth dependency use the `unauth_client` fixture (no override) plus
  `mock_conn.fetchrow.return_value = auth_row(...)` for the happy-path lookup.
- **Config**: `monkeypatch.setenv` + re-construct `Settings()` — never write `os.environ` directly
- **Fixtures**: `conftest.py` at package level; function-scoped (default)
- **Run command**: `pytest services/gateway/tests/ -v --asyncio-mode=auto`

---

## Python mock patterns

**Python async pool**:
```python
mock_pool = AsyncMock()
mock_conn = AsyncMock()
mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
mock_conn.fetchrow.return_value = {"trust_score": 0.8, "is_blocked": False}
```

See `conventions-testing.md` for cross-language mock principles and
`.ai/gotchas/index.md` for library-specific pitfalls.
