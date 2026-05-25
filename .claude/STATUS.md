# .claude/STATUS.md

Phase-execution state and mid-run deviations. See `CLAUDE.md` "Autonomous Execution" for when rows land here.

## Unforeseen / checkpoints

| Date | Commit | What happened | What to do next |
|---|---|---|---|
| 2026-05-25 | 1A.2 | Doc-reviewer surfaced a Python import collision in the plan: 1D.1 plans `app/signals.py` (helper module) while 1D.8 plans `app/signals/<name>.py` package — Python cannot have both at the same path. **Decision**: rename `app/signals.py` → `app/signal_helpers.py`. The package `app/signals/` retains the per-signal rule-evaluation modules. | When executing 1D.1 (Day 4-5), create `app/signal_helpers.py` (not `app/signals.py`). When executing 1D.8, the `app/signals/` package coexists without collision. PLAN_PHASE_1.md text refers to `app/signals.py` in 1D.1; treat this STATUS row as authoritative for naming. |
| 2026-05-25 | post-1A.7 | Operator amendment: drop the `FG_` env-var prefix project-wide. Env vars match pydantic-settings field names verbatim (e.g. `DATABASE_URL` not `FG_DATABASE_URL`). 9 docs updated in a single follow-up commit; no Batch 1A commits rewritten. | When executing 1B.1, `.env.example` uses unprefixed names: `DATABASE_URL`, `HMAC_SECRET`, `API_TOKEN_PREFIX`, `MAXMIND_LICENSE_KEY`, `IP2PROXY_DOWNLOAD_TOKEN`, `LOG_LEVEL`, `AUTH_ENABLED`. `app/config.py` defines `Settings(BaseSettings)` with NO `env_prefix=` argument — pydantic-settings then reads `field_name.upper()` as the env var name. |
