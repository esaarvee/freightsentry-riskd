# conventions.md — Working Rules Index

> This file is an index. Load the topical convention file(s) relevant to
> the language and concern at hand. Always load
> `conventions-freightsentry.md` for cross-language rules; pair it with the
> language file matching what the diff covers.

## Load by task

| Task | Load |
|---|---|
| Any coding task | `conventions-freightsentry.md` + the language file matching the diff |
| Pure Python work | `conventions-freightsentry.md` + `conventions-python.md` |
| Pure Go work | `conventions-freightsentry.md` + `conventions-go.md` |
| Mixed Python/Go work | `conventions-freightsentry.md` + `conventions-python.md` + `conventions-go.md` |
| Test-writing (Python) | `conventions-python.md` + `conventions-testing.md` + `conventions-freightsentry.md` + `.ai/contracts/<service>.md` + `.ai/gotchas/index.md` |
| Test-writing (Go) | `conventions-go.md` + `conventions-testing.md` + `conventions-freightsentry.md` + `.ai/contracts/<service>.md` + `.ai/gotchas/index.md` |
| Cross-language test patterns / mock isolation | `conventions-testing.md` |
| Schema / migrations | `conventions-freightsentry.md` (SQL section) + `.ai/decisions-data.md` [ALEMBIC-MIGRATIONS] + `.ai/schema.md` |
| Proto / gRPC | `conventions-freightsentry.md` (Proto section) + `proto/fraud_evaluation.proto` |
| ECS / deployment | `conventions-freightsentry.md` (ECS section) + `docs/06-infrastructure.md` |

## Topical files

- [conventions-freightsentry.md](conventions-freightsentry.md) — Role,
  How to Think, FG_ env prefix rules, dependencies version pins, SQL /
  Migrations, Proto / gRPC, ECS / Deployment, Output Rules, Guardrails.
- [conventions-python.md](conventions-python.md) — Python code conventions,
  pytest patterns, Python mock examples.
- [conventions-go.md](conventions-go.md) — Go code conventions, testify
  patterns, Go mock examples (sequential queue, pgx.Tx stub).
- [conventions-testing.md](conventions-testing.md) — Language-agnostic
  testing principles (what unit tests verify, case matrix, mock isolation,
  fixture conventions, common pitfalls).
