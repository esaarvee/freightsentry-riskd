# conventions-testing.md — Cross-language testing patterns

> Language-agnostic testing principles, mock patterns quick-reference, and
> fixture-level conventions. Load alongside `conventions-go.md` or
> `conventions-python.md` for language-specific details, and
> `conventions-freightsentry.md` for cross-cutting concerns.
>
> Index: see [.ai/conventions.md](./conventions.md).

---

## What unit tests verify

Unit tests validate externally observable behavior only — return values,
errors, persisted outputs, enqueued messages. Tests that pin implementation
details (private function call order, intermediate state shape) become
brittle and slow refactor. If a behavior isn't visible at the function's
boundary, it's either not worth a test or the function's boundary is wrong.

## Test naming

Name tests by behavior or outcome, not by the function or "test case N":

- Go: `t.Run("blocked user → BLOCK, score=1.0", …)` — not `t.Run("test case 1", …)`
- Python: `def test_returns_block_when_ip_is_blacklisted(...)` — verb-first,
  describes the observable outcome

## Case matrix

Every test file should cover:

- **Happy path** (the documented success case)
- **Every error return** (each path that returns an error/raises an exception)
- **Boundary values** (at, below, above each threshold the code reasons about)
- **Nil/None/zero inputs** (especially for fields that are optional in the
  wire format)

## Mock isolation

- Build mocks behind **minimal interfaces** — the mock implements only what
  the code under test calls.
- **Custom mock structs** (no generators) — readable, debuggable, and they
  don't add a tooling dependency.
- **Fresh mock per subtest** — never share mutable mock state across cases.
- **Stub unused interface methods** with `panic("not expected")` (Go) or
  similar — a panic from a stub immediately points at the missing
  expectation; silent default-zero returns hide bugs.

## Mock patterns quick reference

For language-specific code patterns, see:

- Go sequential-queue and pgx.Tx stub: [conventions-go.md](conventions-go.md)
- Python async pool: [conventions-python.md](conventions-python.md)

## Fixture conventions

- **Scope**: default to function-scoped fixtures (the cheapest, most
  isolated). Promote to module/session scope only with a concrete cost
  reason — and reset mutable state per-test.
- **Location**: at the closest `conftest.py` / shared file relevant to the
  code under test. Don't lift a fixture to root scope unless it's used by
  more than one sibling directory.
- **Construction**: each fixture builds one logical thing. Compose fixtures
  rather than threading kwargs into a god-fixture.
- **Cleanup**: pair every external resource (background goroutine, temp
  directory, container, fake clock) with explicit teardown.

## Common pitfalls

- **Async + sync mock mismatch**: in Python, an `AsyncMock` is required for
  `await` targets; a bare `MagicMock` will silently return a non-awaitable
  and the test will fail with an opaque "coroutine was never awaited" or
  the mock will pass even when production code is broken.
- **Time-based assertions**: use injected clocks or sleeps tuned to the
  smallest interval the harness can honor (e.g. miniredis `20ms` for stream
  reads). Bare `time.Sleep(0)` or `await asyncio.sleep(0)` does not yield
  enough scheduler time on every platform.
- **Test that passes when production is broken**: if you can break the
  function under test in an obvious way and the test still passes, the
  test is asserting the wrong thing. This is always a finding — the
  test-reviewer's safety carve-out exists for exactly this case.

See `.ai/gotchas/index.md` for library-specific pitfalls.
