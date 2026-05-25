# Local Dev (OrbStack) Gotchas

## Use .orb.local DNS — never localhost or 127.0.0.1
OrbStack's TCP proxy accepts connections but causes go-redis v9 HELLO handshake timeouts
when using localhost/127.0.0.1. IDE run configs must use the OrbStack service DNS names.

```
# WRONG — causes go-redis v9 HELLO timeout
REDIS_URL=redis://localhost:6379
FG_DB_HOST=localhost

# CORRECT
REDIS_URL=redis://redis.freightcom-risk.orb.local:6379
FG_DB_HOST=postgres.freightcom-risk.orb.local
FG_PLATFORM_DB_HOST=mysql.freightcom-risk.orb.local
OLLAMA_URL=http://ollama.freightcom-risk.orb.local:11434
```

**Exception**: `FG_RULES_ENGINE_ADDR=localhost:50051` is correct — the rules engine runs
on the host (not in Docker) when launched from the IDE.

## go mod tidy must run from the service directory, not repo root
Both Go services have independent go.mod files. Running `go mod tidy` from repo root does nothing.

```bash
# WRONG
cd /repo && go mod tidy

# CORRECT
cd services/rules-engine && go mod tidy
cd services/async-worker && go mod tidy
```

Also applies to `go test ./internal/...` and `go build ./...` — always run from the service directory.
