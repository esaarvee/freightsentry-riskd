# conventions.md — Working Rules

Single conventions file for the project. Load this for any coding or test-writing task. Library-specific pitfalls live in `.ai/gotchas/`.

---

## Role and posture

- You are working in a single Python service for real-time fraud detection. Optimise for simplicity, correctness, and the latency budget (<200ms p95).
- Concrete names over abstract ones. No designing for hypothetical future tenants.
- Three similar lines is better than a premature abstraction. Don't introduce a class hierarchy until you have two real subtypes.
- Multi-tenant from day one: every query scopes by `tenant_id`; Postgres RLS is the defensive backstop.

---

## Code conventions

### Language and runtime

- Python 3.13+. Type hints on every function signature.
- Use modern features sparingly and consistently: PEP 604 unions (`int | None`), match statements where they clarify intent, `dataclasses` over ad-hoc tuples.
- Prefer `pathlib.Path` over `os.path`.
- Imports: stdlib → third-party → first-party (`app.*`), each group alphabetised; let `ruff` handle this.

### Framework and stack

- FastAPI for the REST surface. Routes live in `app/api/<topic>.py`. One module per logical endpoint group.
- Pydantic v2 for request/response models in `app/models.py`. Response models are the authoritative wire schema — anything not in a response model does not leak.
- asyncpg for Postgres. Single connection pool at app lifespan. Per-request connection acquired via `async with pool.acquire() as conn:`. No SQLAlchemy ORM — raw SQL with parameter binding only.
- pydantic-settings with env prefix `FG_` (loaded from `.env` in dev, AWS Secrets Manager in prod). Settings are loaded once at app lifespan; never re-read at request time.
- Alembic for migrations. **Sync** `env.py` using psycopg (v3) — the runtime app uses asyncpg, but alembic uses sync psycopg because asyncpg's prepared-statement protocol rejects multi-statement DDL scripts. The two drivers coexist without runtime impact. Every migration must define `upgrade()` AND `downgrade()` (round-trip tested).

### Async discipline

- `async def` everywhere on the request path.
- `asyncio.gather` for parallel independent loads. **Do not** use `gather` for fire-and-forget — exceptions only surface for the first failure; remaining tasks may complete or hang silently.
- Never call blocking code (`requests.get`, `time.sleep`, filesystem reads without `aiofiles`) from an `async def` function on the request path.
- For background work that genuinely should not block the response (rare in this project — see decisions.md on synchronous persistence), wrap in `asyncio.create_task` and `asyncio.shield` if it must outlive the request.

### Database access

- All SQL goes through asyncpg with positional parameters (`$1`, `$2`). **Never** f-string SQL with user input.
- Per-request: acquire a connection, set the RLS tenant context via `set_tenant_id(conn, tenant_id)` (executes `SET LOCAL app.tenant_id = '<id>'`), then run queries.
- Writes that include a baseline update use `SELECT FOR UPDATE` on `customer_baselines` to serialise concurrent updates for the same customer. The lock is held until the enclosing transaction commits.
- Decision persistence is **synchronous within the same transaction** as the baseline update — INSERT shipments + INSERT decisions + baseline save + UPDATE customers all in one txn. Idempotency on `(tenant_id, request_id)` guarantees retry correctness.

### Config and secrets

- All config in `app/config.py` via a single `Settings` pydantic-settings class. No env prefix — env var names match field names verbatim (e.g. `DATABASE_URL`, `HMAC_SECRET`, `MAXMIND_LICENSE_KEY`).
- Secret values: never logged, never returned in API responses, never included in exception messages. Audit log fields containing HMAC'd PII are acceptable.
- `Settings()` is constructed once at lifespan; pass via FastAPI `Depends(get_settings)`.

### PII handling

- Emails, phones, and any operator-supplied free text identifying a person → HMAC at ingress using `signals.hmac_hex(value, settings.hmac_secret)`. Store the HMAC hex only. The plaintext does not leave the request handler.
- `hmac_hex` is **not** decorated with `@lru_cache` — a cache keyed on the secret hazards rotation.
- The HMAC secret rotates via env-var update + container redeploy. Stat-dict entries created under the old secret are not portable across rotations — accept the cost or stage rotation across a cold period.

### Error handling

- Validate at the system boundary (FastAPI request model). Once a Pydantic model parses, downstream code trusts the types.
- Internal exceptions surface as 500. Don't catch broad exceptions to log-and-continue; let the request fail and rely on idempotency for retries.
- Custom exception types only when callers need to discriminate (`DSLError`, `BaselineLockTimeout`, `EnrichmentUpstreamFailure`).

### Linting and type-checking

- `ruff check app/ tests/` — line-length 100, default ruleset plus selected extras (UP, B, SIM, I, N). No `# noqa` without a comment explaining why.
- `mypy app/` strict mode. No `# type: ignore` without a comment.

### Comments

- Default to writing no comments. Code reads top-to-bottom; names carry meaning.
- Only add a comment when the *why* is non-obvious: hidden constraint, subtle invariant, workaround for a specific bug.
- Don't reference the current task, the PR, the operator, or the issue tracker — those rot.

### File and directory layout

```
app/
  __init__.py
  main.py           # FastAPI app + lifespan
  config.py         # Settings (pydantic-settings)
  db.py             # asyncpg pool + per-request connection helpers
  auth.py           # API token + RLS session context
  logging.py        # structlog setup
  models.py         # Pydantic request/response
  signal_helpers.py # pure stateless helpers (HMAC, normalisation, classification)
  enrich.py         # IP enrichment + cache
  baseline.py       # customer baseline (load, decay, update, save)
  trust.py          # compute_trust_score
  context.py        # build_context (per-request orchestration)
  velocity.py       # SQL-backed velocity counters
  dsl.py            # rule condition parser/evaluator
  scoring.py        # 3-layer noisy-OR scorer
  rules.py          # rule loader + RuleSet
  rules.yaml        # rule catalogue
  api/
    __init__.py
    health.py
    booking.py
    modification.py
    feedback.py
    admin.py
  signals/          # signal modules (one per signal); compute Context flags
    __init__.py
    <signal_name>.py
  services/         # cross-cutting helpers (entity upsert, decision persist)
alembic/
  env.py
  versions/
scripts/
  fetch_enrichment.py
  tenant_onboard.py
tests/
  conftest.py
  unit/
  integration/
  security/
  fixtures/
```

---

## Testing conventions

### What unit tests verify

Unit tests validate externally observable behavior only — return values, raised exceptions, persisted outputs, enqueued effects. Tests that pin implementation details (private call order, intermediate state shape) become brittle and slow refactor. If a behavior isn't visible at the function's boundary, it's either not worth a test or the boundary is wrong.

### Framework

- `pytest` + `pytest-asyncio` with `--asyncio-mode=auto` (set in `pyproject.toml`).
- Do **not** add `@pytest.mark.asyncio` when auto mode is active — it's redundant.

### Naming

- Name tests by behavior, verb-first: `test_returns_block_when_ip_is_blacklisted`.
- Avoid generic names like `test_case_1` or `test_function_x`. The name is the documentation.

### Structure

- `@pytest.mark.parametrize` for table-driven cases. Use `ids=` for human-readable case names in output.
- One assertion concept per test; multiple `assert` lines on the same concept are fine.
- For exception tests: `with pytest.raises(MyError, match=r"...")` — match the message.

### Case matrix

Every test module covers:
- Happy path (documented success case)
- Every error / exception path
- Boundary values (at, below, above each threshold the code reasons about)
- `None` / empty / zero inputs (especially for optional fields)

### Mocks and isolation

- Build mocks behind minimal interfaces: the mock implements only what the code under test calls.
- Custom mock classes preferred over framework auto-mocks for non-trivial cases — they are readable and debuggable.
- Fresh mock per subtest; never share mutable mock state across cases.
- Async-target mocks: use `AsyncMock`, never bare `MagicMock`. A bare `MagicMock` returns a non-awaitable and either explodes opaquely or (worse) lets a broken production path pass.
- Stub unused interface methods with `side_effect=NotImplementedError("call not expected: <method>")` (or use `spec=Class` to make the mock reject unknown attribute access). A silent default-zero return hides bugs; a deliberate raise points at the missing expectation.
- Patch at point of use: `patch("app.api.booking.get_pool")`, not `patch("app.db.get_pool")`. The canonical helper is `app.db.get_pool`.
- Prefer FastAPI `dependency_overrides` over `patch` for routes.

### Python async pool mock pattern

```python
mock_pool = AsyncMock()
mock_conn = AsyncMock()
mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
mock_conn.fetchrow.return_value = {"trust_score": 0.8, "is_blocked": False}
```

### HTTP testing

- `httpx.ASGITransport` + `httpx.AsyncClient` against the FastAPI app directly. No real server, no port binding.
- The `client` fixture in `tests/conftest.py` installs `app.dependency_overrides[require_api_token]` returning a synthetic `AuthContext` with `tenant_id=1`. Route tests don't need to think about auth.
- Tests exercising the real auth dependency use the `unauth_client` fixture (no override) plus seeded `api_tokens` rows.

### Config in tests

- `monkeypatch.setenv("DATABASE_URL", "value")` (or whichever field) then re-construct `Settings()`. Never write `os.environ` directly.

### Fixtures

- Function-scoped by default. Promote to module/session scope only with a concrete cost reason — and reset mutable state per-test.
- Location: closest `conftest.py` relevant to the code under test. Lift to root only when used by more than one sibling directory.
- Each fixture builds one logical thing. Compose fixtures rather than threading kwargs into a god-fixture.
- Pair every external resource (background task, temp directory, container) with explicit teardown.

### Integration tests

- Run against real Postgres via `docker compose up -d postgres`.
- Per-test transaction with rollback: the `db` fixture begins a transaction and rolls back on teardown so tests are isolated.
- RLS-aware: the fixture sets `app.tenant_id` per test scenario.
- Layered: `tests/unit/` for pure-Python; `tests/integration/` for DB-touching; `tests/security/` for DSL-evaluator lockdown and similar adversarial tests.

### Time and randomness

- Inject `now()` via a parameter or settings field. Tests pass an explicit `as_of`.
- Use `random.Random(seed)` for any test-time randomness.
- Avoid `asyncio.sleep` — if a test feels like it needs a sleep, the production code's contract is wrong.

### Common pitfalls

- **Async + sync mock mismatch**: `AsyncMock` for `await` targets; bare `MagicMock` returns a non-awaitable.
- **Test that passes when production is broken**: if you can break the function under test in an obvious way (return wrong value, raise unexpected exception) and the test still passes, the test is asserting the wrong thing. Test-reviewer flags this as critical.
- **Floating-point equality**: use `pytest.approx` or `math.isclose` for any numeric comparison that involves division or accumulation.
- **Order-dependent JSONB**: Postgres preserves JSONB key order on input but doesn't guarantee output order. Compare via `json.loads(...) == expected_dict`, not string equality.
- **RLS leakage in tests**: setting `app.tenant_id` per test prevents accidental cross-tenant data exposure. If a test sees data from another tenant's seed, the RLS setup is wrong, not the test.

---

## SQL / migrations

- Every migration has `upgrade()` AND `downgrade()`. Round-trip tested.
- Migrations are append-only — never edit a committed migration. To revert, write a new migration.
- Column comments are load-bearing (audit trail). Every non-obvious column gets a comment.
- Indexes named explicitly: `ix_<table>_<columns>` for non-unique, `ux_<table>_<columns>` for unique.
- RLS: `ENABLE ROW LEVEL SECURITY` on every tenant-scoped table; `CREATE POLICY tenant_isolation ON <table> USING (tenant_id = current_setting('app.tenant_id')::int)`.
- Schema discovery in tests: pull from `information_schema` rather than hard-coding column lists.

---

## Output rules

- Code first, prose second. Don't add narrative explanations as comments.
- Don't write multi-paragraph docstrings. One short line describing the boundary contract is enough.
- Don't add type hints inside function bodies (`x: int = 5`) — only on signatures and class attributes.
- No `if __name__ == "__main__":` in `app/` modules. Scripts live in `scripts/`.

---

## Guardrails

- No LLM in the request path.
- No second process, no second language, no second storage engine.
- No Redis. Velocity counters are SQL queries against `shipments` with appropriate indexes.
- No PDF generation, no daily reports, no scheduled summary jobs.
- No external database reads (no platform MySQL, no tenant-side DB calls).
- No persisted `trust_score` column — computed on read.
- No `email_matches_customer_name` function — out of scope per decisions.md.
- No negative-weight rules, no trust-override mechanisms, no signal-suppression patterns. Signals contribute monotonically to risk scoring.
- DSL evaluator: whitelist-only AST nodes, `__builtins__: {}` lockdown. Any change to `app/dsl.py` is never-skip review (security boundary).

See `.ai/gotchas/` for library-specific pitfalls and `.ai/decisions.md` for the architectural decisions that shaped these conventions.
