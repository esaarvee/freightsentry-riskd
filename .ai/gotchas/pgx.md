# pgx/v5 Gotchas

## defer tx.Rollback() always fires after Commit
`defer tx.Rollback()` executes even when the transaction committed successfully.
pgx.Tx.Rollback() is a no-op after a successful Commit — this is correct pgx behavior, not a bug.
In tests: set `rolledBack = true` in the mock Rollback and assert it IS called — do not assert it is not called.

## Pool.QueryRow vs Pool.Query
- `Pool.QueryRow` returns `pgx.Row` — call `.Scan()` on it directly; the error surfaces from Scan, not QueryRow
- `Pool.Query` returns `(pgx.Rows, error)` — check error first, then iterate with `rows.Next()` / `rows.Scan()`
- Never ignore the `rows.Err()` check after `for rows.Next()` loop

## pgx.Rows.Scan field order
Column order in Scan() must match the SELECT column order exactly.
pgx does not map by name — positional only. Mismatch silently scans into the wrong field.

## pgx.Tx interface width
`pgx.Tx` has 11 methods. When mocking, stub unused methods with `panic("not expected in <test>")`
to surface accidental calls — do not leave them as empty no-ops.
