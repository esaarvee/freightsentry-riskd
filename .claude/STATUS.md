# .claude/STATUS.md

Phase-execution state and mid-run deviations. See `CLAUDE.md` "Autonomous Execution" for when rows land here.

## Unforeseen / checkpoints

| Date | Commit | What happened | What to do next |
|---|---|---|---|
| 2026-05-25 | 1A.2 | Doc-reviewer surfaced a Python import collision in the plan: 1D.1 plans `app/signals.py` (helper module) while 1D.8 plans `app/signals/<name>.py` package — Python cannot have both at the same path. **Decision**: rename `app/signals.py` → `app/signal_helpers.py`. The package `app/signals/` retains the per-signal rule-evaluation modules. | When executing 1D.1 (Day 4-5), create `app/signal_helpers.py` (not `app/signals.py`). When executing 1D.8, the `app/signals/` package coexists without collision. PLAN_PHASE_1.md text refers to `app/signals.py` in 1D.1; treat this STATUS row as authoritative for naming. |
