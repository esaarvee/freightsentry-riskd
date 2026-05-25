# Postgres gotchas

## RLS requires `SET LOCAL app.tenant_id` per transaction, not per session

`SET app.tenant_id = '1'` (without `LOCAL`) persists for the entire pool connection's lifetime. Subsequent requests using the same pooled connection inherit it — cross-tenant leak. Always use `SET LOCAL` (transaction-scoped); on `ROLLBACK` or `COMMIT` it clears automatically.

## `SELECT FOR UPDATE` releases on transaction end only

The lock acquired by `SELECT FOR UPDATE` holds until the enclosing transaction commits or rolls back, regardless of whether you continue to read or write the row. A long-running transaction holding a baseline-row lock blocks every concurrent booking for that customer. Keep transactions short; do all the parallel reads (`asyncio.gather`) before opening the write transaction if possible.

## JSONB `?` (key existence) vs `@>` (containment)

- `data ? 'key'` — does `data` have top-level key `'key'`? Use for shallow lookups.
- `data @> '{"key": "value"}'` — does `data` contain the path? Use for deep lookups.

Both can be GIN-indexed but require different operator classes (`jsonb_path_ops` for `@>` only; default `jsonb_ops` for both). For our stat-dict reads (Python-side after `baseline.load`), we don't query JSONB keys via SQL — Phase 1 needs no GIN indexes.

## asyncpg INET binding

asyncpg's `inet` codec accepts `ipaddress.IPv4Address` and `str`. **Don't** pass an `int` — it silently casts to text representation of the int, not an IPv4 address. Strict: cast to `IPv4Address` before binding.

## `numeric` precision in asyncpg returns `Decimal`

Columns typed `numeric(14,2)` come back as `decimal.Decimal`, not `float`. Arithmetic that mixes `Decimal` and `float` raises `TypeError`. Either keep numeric work in `Decimal` end-to-end, or cast at the boundary (`float(value)`).

## Alembic `CREATE INDEX CONCURRENTLY` cannot run inside a transaction

Standard Alembic migrations run inside a transaction. `CREATE INDEX CONCURRENTLY` does not. Wrap concurrent index creation with:

```python
with op.get_context().autocommit_block():
    op.execute("CREATE INDEX CONCURRENTLY ix_... ON ...")
```

Or set `transaction_per_migration=False` globally (not recommended — loses txn-wrapped DDL safety for the rest of the migration).

## `ALTER TABLE ... ADD COLUMN NOT NULL DEFAULT` is INSTANT in PG 11+ only with constant defaults

Volatile defaults (`NOW()`, `gen_random_uuid()`, `random()`) still rewrite the table. `DEFAULT 0`, `DEFAULT FALSE`, `DEFAULT 'literal'` are instant. Verify with `EXPLAIN (ANALYZE, BUFFERS)` on a representative-sized table before merging.

## RLS bypass via pool connection leak in tests

If a test fixture acquires a connection from the pool, sets `app.tenant_id`, and returns the connection to the pool WITHOUT clearing the session variable, a subsequent test reusing that connection inherits the previous test's tenant. Always wrap test DB work in a transaction with rollback on teardown — the rollback discards `SET LOCAL` state.
