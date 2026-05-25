# testify Gotchas

## Float comparisons — always InDelta, never Equal/GreaterOrEqual
```go
// WRONG — fails on IEEE 754 drift
assert.Equal(t, 0.10, score)
assert.GreaterOrEqual(t, score, 0.10)

// CORRECT
assert.InDelta(t, 0.10, score, 1e-6)
```
Computed floats (noisy-OR, weighted sums) carry IEEE 754 rounding.
`noisyOR(0.10)` evaluates to `0.09999999...`, not `0.10` — Equal and GreaterOrEqual both fail.

## require vs assert — when to use which
- `require.*` — stops the test immediately (use for setup, preconditions, and values you'll dereference)
- `assert.*` — logs failure and continues (use for result checks so all failures appear in one run)
- Always `require.NoError(t, err)` before using the returned value — dereferencing after a failed call is undefined

## ErrorIs for wrapped sentinels
```go
// CORRECT for wrapped errors
require.ErrorIs(t, err, pgx.ErrNoRows)

// Only use this for opaque error message checks
require.Error(t, err)
assert.Contains(t, err.Error(), "expected substring")
```
