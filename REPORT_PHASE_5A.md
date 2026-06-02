# REPORT_PHASE_5A — Foundational hardening

Batch 5A of Phase 5. 6 implementation commits + Phase 5 plan commit + this report.

## Commit list with reviewer-panel verdicts

| Commit | Title | Routing | Reviewers (verdict at land) |
|---|---|---|---|
| 6214aa6 | Phase 5 plans (5A-5D) | Doc-only | doc-reviewer: PUBLISH with 3 minor tweaks applied |
| 5232841 | 5A.1: uv.lock + ruff 0.15.15 pin + format-sync (merged from former 5A.1+5A.2 per atomic-commits preference) | Standard panel + test-reviewer | senior: SHIP IT · security: LOW RISK · code-flow: CLEAN · test: ACTUALLY GOOD |
| b764f77 | 5A.3: `.ai/conventions.md` Dependency locking section | Doc-only | doc-reviewer: MINOR TWEAKS → applied (time-bound reference removed, command sequence promoted to code block) |
| 449d3bd | 5A.4: Dockerfile non-root user (UID 1000) + build-essential for pytricia | Lightweight (security-auditor + senior-engineer) | senior: SHIP IT · security: LOW RISK / CLEAN |
| b37dd2d | 5A.5: last_used_at writer in require_api_token (auth success path) | Never Skip — full panel | senior: SHIP IT · security: LOW RISK · code-flow: CLEAN · test cycle-1: NEEDS WORK → cycle-2: ACTUALLY GOOD |
| 87d6653 | 5A.6: Migration 0006 — api_tokens supporting index | Never Skip + db-reviewer | senior: SHIP IT · security: LOW RISK · code-flow: CLEAN · db: SHIP IT |
| c2ec8d3 | 5A.7: Migration 0007 widens decisions UNIQUE to (tenant_id, request_type, request_id) | Never Skip + db-reviewer + test-reviewer | senior cycle-1: APPROVED WITH RESERVATIONS → cycle-2: SHIP IT · security: LOW RISK · code-flow cycle-1: MINOR ISSUES → cycle-2: CLEAN · db: SHIP IT · test cycle-1: ACCEPTABLE → cycle-2: ACTUALLY GOOD |

**Reviewer-panel discipline.** Every code commit invoked the panel per CLAUDE.md triage routing. No panel skips. The Phase 4 retro lesson held.

## Per-commit corrections

- 5A.5: test-reviewer cycle 1 flagged 3 issues (bare `try/except Exception: pass`, `asyncio.sleep(0.01)` violating the no-sleep convention, test #3 docstring overclaiming). All three applied; cycle 2 ACTUALLY GOOD. Also added an AUTH_ENABLED=false coverage test per the missing-coverage note.
- 5A.7: cycle-1 had three reviewers with non-cleanest verdicts. Five fixes applied: (a) HTTPException detail strings reworded from "booking-modification namespace collision" to "intra-type duplicate", (b) test file module docstring no longer claims a test design that wasn't implemented, (c) DB-level row-count assertions added to all four cross-type / replay tests, (d) full envelope equality replaced with `_assert_decisions_equivalent` using `pytest.approx` on score (numeric(5,4) DB roundtrip), (e) seeding assertion added in duplicate-modification test. Cycle 2 cleanest verdicts across all five reviewers.
- 5A.3: doc-reviewer flagged the file's own anti-time-bound-reference convention being violated by "Phase 5A retroactive fix" parenthetical. Applied.

## Test count delta

- Start of Phase 5: 852+ per precondition (verified: 856 after 5A.1 format-sync; the 4-test delta from precondition reflects the +4 from format-sync that didn't change semantics but counted differently).
- After 5A.5: 856 → 860 (+4 from `tests/integration/test_api_token_last_used.py`).
- After 5A.6: 860 (no test count change — migration-only commit).
- After 5A.7: 860 → 863 (+3 from `tests/integration/test_decisions_unique_widening.py`; 1 flipped test in `test_modification_endpoint.py` is net-zero).
- **End of batch 5A: 863 tests pass. mypy strict clean. pre-commit clean.**

Case-1 + case-2 regression tests: continue to pass (verified at each commit's full-suite run).

## Plan-vs-delivery notes

**5A.1+5A.2 merged.** The plan split lockfile-and-pin (5A.1) from format-sync (5A.2). The split would have created a broken intermediate state where `pre-commit run --all-files` reports format diffs (declared break in the plan). Per operator's stored preference for atomic commits when a split creates broken intermediate state, the two were merged at execution time. Cycle-2 review confirmed this was the right call (senior: SHIP IT, security: LOW RISK).

**5A.7 test naming.** The plan named tests `test_duplicate_booking_same_request_id_returns_409` and `test_duplicate_modification_same_request_id_returns_409`, but the endpoint's SELECT-then-INSERT idempotency contract returns 200 (replay) before the INSERT can fail in serial flow. Implementer delivered `_replays` variants with full DB-level row-count assertions. The 409 catch is correct defense-in-depth but only reachable under concurrent-write race (two writers SELECT-miss in parallel, then race the INSERT). Senior-engineer's cycle-2 verdict explicitly endorsed this reframing as a phase-level lesson: "when a plan prescribes test names that assert exception paths, verify those paths are reachable in the test harness at plan time, not delivery time." This is the second instance of "verify exception-path reachability at plan time" — sibling lesson to the "bake-schema-drift-into-plan" feedback already absorbed in memory.

## BUGS.md state

### RESOLVED in batch 5A

- `2026-05-27 — PLAN_PHASE_2C.md 2C.6 rule-count arithmetic error` (RESOLVED pre-Phase-5; annotation added in 5A.7's BUGS.md drain commit).
- `2026-05-27 — decisions.ux_decisions_tenant_request UNIQUE is flat across request_type` (RESOLVED in 5A.7 via migration 0007).
- `2026-06-01 — ruff version drift between pre-commit pin and local install` (RESOLVED in 5A.1).

### New entries added during batch 5A

- `2026-06-02 — docker-compose app service unusable without DATABASE_URL override` (medium; .env `localhost:5432` overrides the docker-compose default in the container env). **Suggested action**: address in 5D.2 alongside the `ALEMBIC_DATABASE_URL` split, or via a separate `DATABASE_URL_HOST` / `.env.docker` pattern.
- `2026-06-02 — Dockerfile pip install failed (pytricia sdist + missing build deps)` (medium pre-existing; build resolved in 5A.4 by adding `build-essential`. The build-tools-in-runtime hardening regression deferred to Phase 6 multi-stage as a hard prerequisite for production deploy).
- `2026-06-02 — Redundant index ix_api_tokens_tenant after 0006 lands` (low; deferred to a future cleanup migration).
- `2026-06-02 — UniqueViolation 409 catch in booking/modification is unreachable in serial tests` (low; defense-in-depth path. Add asyncio.gather race test in Phase 5B or dedicated concurrency commit).
- `2026-06-02 — _assert_decisions_equivalent helper duplicated across two test files` (low; lift to shared `tests/integration/_helpers.py` in Phase 5B/5C cleanup).

## STATUS.md state

One row appended at 5A.4: documents the dual pre-existing Docker infrastructure issues that surfaced during the non-root smoke test. Operator-decision pointer: "at the 5A → 5B checkpoint, decide whether to expand 5D.2 to absorb the env-split or leave for Phase 6."

## What batch 5A delivered

- **Dependency locking**: `uv.lock` at repo root as the single source of truth. Pre-commit pin + pyproject ruff constraint aligned. `.ai/conventions.md` documents the workflow.
- **Container hardening**: Dockerfile runs as UID 1000 `app` user. `build-essential` resolves the pre-existing pytricia build failure (Phase 6 multi-stage strips for production).
- **`last_used_at` writer**: `require_api_token` stamps `api_tokens.last_used_at = now()` on success path only, with both `id` and `token_hash` in the WHERE for defense-in-depth. Autocommit contract documented in tests.
- **`api_tokens` supporting index**: `(tenant_id, last_used_at DESC NULLS LAST)` for future stale-token queries.
- **UNIQUE widening**: `decisions` UNIQUE is now `(tenant_id, request_type, request_id)`. Booking and modification with the same `request_id` legitimately coexist. Plan's flat-UNIQUE BUGS entry RESOLVED.

## Operator checkpoint

Batch 5A complete. Operator decisions to weigh before 5B begins:

1. **5A.4 unforeseens**: STATUS.md row + two BUGS.md entries surface the `.env` localhost mismatch (medium, deferred) and Phase 6 multi-stage hardening (hard prerequisite for production deploy, deferred). Should 5D.2 absorb the `.env` split, or defer to Phase 6?
2. **5B approval**: PLAN_PHASE_5B.md (tenant-config cache) ready to execute once approved. Decisions absorbed: TTL=60s, asyncio.Lock per tenant_id, TTL-only invalidation.
3. **Plan-vs-delivery test-name divergence**: 5A.7's reframing of `*_returns_409` → `*_replays` is the second instance of this class of plan defect. Memorable enough to absorb as a planning lesson? (Suggested memory: "When planning tests that assert exception paths, verify the path is reachable in the test harness — not just the catch exists.")
