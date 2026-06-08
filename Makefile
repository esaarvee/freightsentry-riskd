# =============================================================================
# freightsentry-riskd — local-dev Makefile
#
# Wraps the standard docker compose workflow against the stack defined in
# docker-compose.yml. Works with OrbStack, Docker Desktop, Colima, or native
# dockerd — uses only `docker compose` subcommands, no runtime-specific paths.
#
# Quick start (from a clean clone):
#   make up               # build + start + migrate + seed (one command)
#   make seed-admin       # mint an admin-role API token for verify Step 8
#   make verify           # 10-step end-to-end correctness probe
#   make verify-cleanup   # remove e2e_verify_* / e2e-verify-* test rows
#   make down             # stop containers (volumes preserved)
#   make clean CONFIRM=yes  # destructive: stop + drop volumes + drop tokens
#
# `make help` lists every target with a one-line description.
# =============================================================================

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# Compose service names — keep aligned with docker-compose.yml.
APP_SERVICE := app
DB_SERVICE  := postgres
DB_USER     := riskd
DB_NAME     := riskd

# Seeded tenant for `make seed` + `make verify`.
TENANT_SLUG := e2e-test
TENANT_NAME := E2E Test Tenant

# Token files (gitignored; created by seed / seed-admin).
TOKEN_DIR        := .tokens
TENANT_TOKEN     := $(TOKEN_DIR)/$(TENANT_SLUG).txt
ADMIN_TOKEN      := $(TOKEN_DIR)/$(TENANT_SLUG)-admin.txt

# ANSI color helpers — disabled when NO_COLOR is set (CI-friendly).
ifdef NO_COLOR
C_RESET :=
C_BOLD  :=
C_GREEN :=
C_YELLOW :=
C_RED   :=
else
C_RESET := \033[0m
C_BOLD  := \033[1m
C_GREEN := \033[32m
C_YELLOW := \033[33m
C_RED   := \033[31m
endif

.PHONY: help build up down clean migrate seed seed-admin logs logs-db \
        shell shell-db test test-unit test-integration lint \
        verify verify-cleanup status restart rebuild \
        _wait-db _wait-app _seed-if-absent _seed-impl

## help: List available targets with one-line descriptions.
help:
	@printf "$(C_BOLD)freightsentry-riskd — local-dev targets$(C_RESET)\n\n"
	@grep -E '^## [a-zA-Z_-]+:' Makefile | sed -E 's/^## ([a-zA-Z_-]+): (.*)/  \x1b[32m\1\x1b[0m\t\2/' | expand -t 22

## build: Build the app container image.
build:
	docker compose build $(APP_SERVICE)

## up: Bring up the full stack; wait for health; migrate; seed if needed.
up:
	@printf "$(C_BOLD)→ docker compose up -d$(C_RESET)\n"
	docker compose up -d
	@$(MAKE) --no-print-directory _wait-db
	@$(MAKE) --no-print-directory _wait-app
	@$(MAKE) --no-print-directory migrate
	@$(MAKE) --no-print-directory _seed-if-absent
	@printf "$(C_GREEN)✓ stack ready$(C_RESET) — token at $(TENANT_TOKEN); run 'make verify' to probe.\n"

## down: Stop containers (volumes preserved).
down:
	docker compose down

## clean: Destructive — stop + drop volumes + drop $(TOKEN_DIR). Requires CONFIRM=yes.
clean:
ifneq ($(CONFIRM),yes)
	@printf "$(C_RED)refusing to clean without explicit confirmation.$(C_RESET)\n"
	@printf "this drops the postgres volume AND deletes $(TOKEN_DIR)/.\n"
	@printf "re-run: $(C_BOLD)make clean CONFIRM=yes$(C_RESET)\n"
	@exit 1
endif
	docker compose down -v
	rm -rf $(TOKEN_DIR)
	@printf "$(C_GREEN)✓ stack stopped, volumes dropped, tokens removed$(C_RESET)\n"

## migrate: Run alembic upgrade head inside the app container.
migrate:
	docker compose exec -T $(APP_SERVICE) alembic upgrade head

## seed: Create the e2e-test tenant and capture its API token. Idempotent.
seed:
	@$(MAKE) --no-print-directory _seed-impl

# Internal: only seed if tenant absent.
_seed-if-absent:
	@if docker compose exec -T $(DB_SERVICE) psql -U $(DB_USER) -d $(DB_NAME) -tAc \
	    "SELECT 1 FROM tenants WHERE name='$(TENANT_SLUG)'" | grep -q 1; then \
	  printf "$(C_YELLOW)tenant '$(TENANT_SLUG)' already exists; skipping seed.$(C_RESET)\n"; \
	else \
	  $(MAKE) --no-print-directory _seed-impl; \
	fi

# Internal: actual onboarding + token capture. The tenant_onboard.py
# stdout includes a one-shot `api_token=<plaintext>  # CAPTURE NOW` line;
# we extract it into a shell var and ECHO A REDACTED FORM of the stdout
# (api_token line stripped) so terminal scrollback / CI log capture
# never persists the plaintext token. The actual plaintext is written
# only to $(TENANT_TOKEN), under umask 077 / chmod 600 / gitignored
# $(TOKEN_DIR).
_seed-impl:
	@install -d -m 700 $(TOKEN_DIR)
	@printf "$(C_BOLD)→ onboarding tenant '$(TENANT_SLUG)'$(C_RESET)\n"
	@out="$$(docker compose exec -T $(APP_SERVICE) python scripts/tenant_onboard.py \
	    --external-id $(TENANT_SLUG) \
	    --display-name "$(TENANT_NAME)")"; \
	  printf '%s\n' "$$out" | sed -E 's/^api_token=.*/api_token=<redacted — written to $(TENANT_TOKEN)>/'; \
	  tok=$$(printf '%s\n' "$$out" | sed -nE 's/^api_token=([^ ]+).*/\1/p'); \
	  if [ -n "$$tok" ]; then \
	    umask 077; printf '%s' "$$tok" > $(TENANT_TOKEN); \
	    chmod 600 $(TENANT_TOKEN); \
	    printf "$(C_GREEN)✓ token written to $(TENANT_TOKEN)$(C_RESET)\n"; \
	  else \
	    printf "$(C_YELLOW)no new token issued (existing tenant); $(TENANT_TOKEN) not modified.$(C_RESET)\n"; \
	  fi

## seed-admin: Mint an admin-role API token for the e2e-test tenant (for verify Step 8).
# Token values flow via STDIN into `python -c` (read from sys.stdin), never via
# argv or shell interpolation into a Python string literal — so a hostile
# .tokens/ file cannot pivot to in-container Python execution and tokens
# never appear in `ps auxww` listings. The INSERT uses `psql -v` variables
# bound to :tid / :'th' (string-quoted) for defense-in-depth even though
# `tenant_id` is a SELECT result (integer) and `token_hash` is fixed-charset
# SHA-256 hex.
#
# Single-shell recipe by design: the idempotency `exit 0` short-circuits the
# entire target, so a stale-file re-run cannot fall through to a duplicate
# mint that would orphan the prior api_tokens row.
seed-admin:
	@install -d -m 700 $(TOKEN_DIR); \
	  printf "$(C_BOLD)→ minting admin token for tenant '$(TENANT_SLUG)'$(C_RESET)\n"; \
	  if [ -f $(ADMIN_TOKEN) ] && [ -s $(ADMIN_TOKEN) ]; then \
	    existing_hash=$$(cat $(ADMIN_TOKEN) | docker compose exec -T $(APP_SERVICE) python -c \
	      'import sys; from app.auth import _hash_token; print(_hash_token(sys.stdin.read().strip()))'); \
	    found=$$(docker compose exec -T $(DB_SERVICE) psql -U $(DB_USER) -d $(DB_NAME) -tAc \
	      "SELECT 1 FROM api_tokens WHERE token_hash='$$existing_hash' AND role='admin'"); \
	    if [ "$$found" = "1" ]; then \
	      printf "$(C_YELLOW)admin token already present in DB; $(ADMIN_TOKEN) unchanged.$(C_RESET)\n"; \
	      exit 0; \
	    fi; \
	  fi; \
	  tenant_id=$$(docker compose exec -T $(DB_SERVICE) psql -U $(DB_USER) -d $(DB_NAME) -tAc \
	    "SELECT id FROM tenants WHERE name='$(TENANT_SLUG)'"); \
	  if [ -z "$$tenant_id" ]; then \
	    printf "$(C_RED)tenant '$(TENANT_SLUG)' not found; run 'make seed' first.$(C_RESET)\n"; \
	    exit 1; \
	  fi; \
	  plaintext=$$(docker compose exec -T $(APP_SERVICE) python -c \
	    'import secrets; print(secrets.token_urlsafe(32))'); \
	  token_hash=$$(printf '%s' "$$plaintext" | docker compose exec -T $(APP_SERVICE) python -c \
	    'import sys; from app.auth import _hash_token; print(_hash_token(sys.stdin.read().strip()))'); \
	  docker compose exec -T $(DB_SERVICE) psql -U $(DB_USER) -d $(DB_NAME) -v ON_ERROR_STOP=1 \
	    -v tid="$$tenant_id" -v th="$$token_hash" -c \
	    "INSERT INTO api_tokens (tenant_id, token_hash, role) VALUES (:tid, :'th', 'admin') ON CONFLICT (token_hash) DO NOTHING" >/dev/null; \
	  umask 077; printf '%s' "$$plaintext" > $(ADMIN_TOKEN); \
	  chmod 600 $(ADMIN_TOKEN); \
	  printf "$(C_GREEN)✓ admin token written to $(ADMIN_TOKEN)$(C_RESET)\n"

## logs: Tail app container logs.
logs:
	docker compose logs -f $(APP_SERVICE)

## logs-db: Tail postgres container logs.
logs-db:
	docker compose logs -f $(DB_SERVICE)

## shell: Open an interactive shell inside the app container.
shell:
	docker compose exec $(APP_SERVICE) bash

## shell-db: Open a psql session against the riskd database.
shell-db:
	docker compose exec $(DB_SERVICE) psql -U $(DB_USER) -d $(DB_NAME)

# NOTE: test / lint targets run against the host venv. The production app
# image (Dockerfile:29) installs only [project.dependencies] — pytest /
# httpx / ruff / mypy live under the [test] and [dev] extras and are NOT
# present in the runtime container. The pre-commit hooks invoke these the
# same way (host-side), so the Makefile mirrors that contract.

## test: Run the full pytest suite (host venv).
test:
	pytest tests/ -v --asyncio-mode=auto

## test-unit: Run unit tests only (fast; host venv).
test-unit:
	pytest tests/unit/ -x --no-header -q

## test-integration: Run integration tests only (host venv; needs stack up).
test-integration:
	pytest tests/integration/ -v --asyncio-mode=auto

## lint: Run ruff + mypy (host venv).
lint:
	ruff check app/ tests/ scripts/
	mypy app/

## verify: Run the 10-step end-to-end correctness probe.
verify:
	@if [ ! -s $(TENANT_TOKEN) ]; then \
	  printf "$(C_RED)$(TENANT_TOKEN) missing — run 'make seed' first.$(C_RESET)\n"; exit 1; \
	fi
	python scripts/e2e_verify.py \
	    --host http://localhost:8000 \
	    --token-file $(TENANT_TOKEN) \
	    --admin-token-file $(ADMIN_TOKEN)

## verify-cleanup: Remove e2e_verify_* test rows without running the probe.
verify-cleanup:
	python scripts/e2e_verify.py --cleanup-only --token-file $(TENANT_TOKEN)

## status: Print container state + recent decision / customer / feedback counts.
status:
	@printf "$(C_BOLD)— containers —$(C_RESET)\n"
	@docker compose ps
	@printf "\n$(C_BOLD)— recent decisions (last 5) —$(C_RESET)\n"
	@docker compose exec -T $(DB_SERVICE) psql -U $(DB_USER) -d $(DB_NAME) -c \
	    "SELECT id, tenant_id, request_type, decision, score, created_at FROM decisions ORDER BY id DESC LIMIT 5" || true
	@printf "\n$(C_BOLD)— counters —$(C_RESET)\n"
	@docker compose exec -T $(DB_SERVICE) psql -U $(DB_USER) -d $(DB_NAME) -c \
	    "SELECT (SELECT count(*) FROM customers) AS customers, \
	            (SELECT count(*) FROM customer_baselines) AS baselines, \
	            (SELECT count(*) FROM feedback) AS feedback_rows" || true

## restart: Restart the app container only.
restart:
	docker compose restart $(APP_SERVICE)

## rebuild: Rebuild the app image and restart.
rebuild:
	docker compose down $(APP_SERVICE)
	docker compose up -d --build $(APP_SERVICE)

# =============================================================================
# Internal wait helpers — not exposed via `help`.
# =============================================================================

# Wait for postgres to report healthy. 60s ceiling.
_wait-db:
	@printf "$(C_BOLD)→ waiting for postgres healthcheck$(C_RESET)..."
	@for i in $$(seq 1 60); do \
	  state=$$(docker inspect --format '{{.State.Health.Status}}' \
	    $$(docker compose ps -q $(DB_SERVICE)) 2>/dev/null || echo "starting"); \
	  if [ "$$state" = "healthy" ]; then printf " $(C_GREEN)ok$(C_RESET)\n"; exit 0; fi; \
	  printf "."; sleep 1; \
	done; \
	printf " $(C_RED)timeout$(C_RESET)\n"; \
	docker compose logs --tail=30 $(DB_SERVICE); \
	exit 1

# Wait for app to return HTTP 200 on /health/. 60s ceiling.
_wait-app:
	@printf "$(C_BOLD)→ waiting for app /health/$(C_RESET)..."
	@for i in $$(seq 1 60); do \
	  code=$$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health/ || true); \
	  if [ "$$code" = "200" ]; then printf " $(C_GREEN)ok$(C_RESET)\n"; exit 0; fi; \
	  printf "."; sleep 1; \
	done; \
	printf " $(C_RED)timeout$(C_RESET)\n"; \
	docker compose logs --tail=30 $(APP_SERVICE); \
	exit 1
