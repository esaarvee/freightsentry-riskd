# Gotchas Index
> Load this file first. Pull only the sub-file relevant to your task.
> Cap: each sub-file ≤ 30 lines. Promote to conventions.md when a pattern recurs 3+ times.

| Scope | File | Load when |
|---|---|---|
| pgx/v5 | `.ai/gotchas/pgx.md` | any Go db work (async-worker) |
| miniredis/v2 | `.ai/gotchas/miniredis.md` | Go test writing touching Redis |
| testify | `.ai/gotchas/testify.md` | any Go test writing |
| scoring / float math | `.ai/gotchas/scoring.md` | scorer, rules, velocity work |
| cidranger | `.ai/gotchas/cidranger.md` | threatintel store or IP matching work |
| maxminddb | `.ai/gotchas/maxminddb.md` | geolookup store or IP geolocation work |
| fsnotify | `.ai/gotchas/fsnotify.md` | rules loader, hot-reload, or file-watch work |
| local dev / OrbStack | `.ai/gotchas/local-dev.md` | IDE run config, go mod, local infra |
| pii HMAC parity | `.ai/gotchas/pii-parity.md` | touching `internal/pii`, `statdict.ContainsHMAC`, or `deadcode` audit output |
| cloudip duplicated parser | `.ai/gotchas/cloudip-dup.md` | touching either service's `internal/cloudip/` or the upstream cloud-IP list format |

## Module → Gotcha Files

When testing a specific module, load these gotcha files:

| Module being tested | Load these gotcha files |
|---|---|
| `scoring/scorer.go` | scoring.md, testify.md |
| `rules/loader.go` | fsnotify.md, testify.md |
| `velocity/checker.go` | miniredis.md, testify.md |
| `audit/handler.go` | pgx.md, miniredis.md, testify.md |
| `ai/handler.go` | pgx.md, testify.md |
| `feedback/handler.go` | pgx.md, testify.md |
| `blacklist/syncer.go` | pgx.md, testify.md, miniredis.md |
| `streams/consumer.go` | miniredis.md, testify.md |
| `threatintel/*` | cidranger.md, testify.md |
| `geolookup/*` | maxminddb.md, testify.md |
| `enrichment.py` | scoring.md |

## Maintenance rules
- **Append** when implementation reveals a behavior that contradicts naive expectations
- **Promote** to `.ai/conventions.md` when the same gotcha recurs in 3+ different tasks — then delete from here
- **Expire** when the relevant dependency is upgraded — audit the file and remove stale entries
- **Never** let a sub-file exceed 30 lines; if it does, promote the oldest/most general entries
