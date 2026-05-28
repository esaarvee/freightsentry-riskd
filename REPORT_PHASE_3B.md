# Phase 3 — Batch 3B Report

**Batch**: 3B — Feedback endpoint stack
**Commits**: 3B.1 through 3B.7 (7 commits)
**Date range**: 2026-05-27 to 2026-05-28
**Status**: COMPLETE — operator approves 3C before execution

## Aggregate stats

| Metric | Pre-3B (end of 3A) | Post-3B |
|---|---|---|
| Rule count | 75 | 79 (+4 previously-rejected) |
| Test count | 561 | 651 (+90) |
| ALLOWED_CONTEXT_FIELDS | 62 | 66 (+4 previously-rejected) |
| Migrations | 3 | 4 (+0004 feedback drop-and-recreate + shipments.email_hmac/phone_hmac) |
| New `.py` files under `app/` | — | `app/api/feedback.py` |
| New endpoints | 3 | 4 (+ feedback) |
| `.ai/decisions.md` new subsections | — | "Previously-rejected rule weight rationale (Phase 3B)" |
| Production bug fixes during testing | — | 1 (feedback endpoint race; surfaced by 3B.7 concurrency test) |

## Per-commit disposition

| # | Hash | Theme | LoC (net) | Tests added | Reviewer panel | Cycles |
|---|---|---|---|---|---|---|
| 3B.1 | 5ac1eaf | feedback drop-and-recreate + shipments HMAC columns | +139 | 0 (manual round-trip) | senior SHIP IT, security LOW RISK/CLEAN, code-flow CLEAN, db SHIP IT | 1 |
| 3B.2 | 7afcab7 | Pydantic FeedbackRequest / FeedbackResponse | +180 | 24 | senior SHIP IT, security LOW RISK/CLEAN, code-flow CLEAN, test ACTUALLY GOOD | 1 |
| 3B.3 | 75142ff | endpoint route + baseline writer + booking INSERT patch | +890 | 27 (18 unit + 9 integration) | senior SHIP IT, security LOW RISK/CLEAN, code-flow CLEAN, db SHIP IT, test ACTUALLY GOOD | 1 |
| 3B.4 | 62b3416 | DSL whitelist +4 + Context derivations | +192 | 16 | senior SHIP IT, security LOW RISK/CLEAN, code-flow CLEAN, test ACTUALLY GOOD | 1 |
| 3B.5 | 065aff6 | 4 previously-rejected rules + per-rule tests | +200 | 10 | senior SHIP IT, security LOW RISK/CLEAN, code-flow CLEAN, test ACTUALLY GOOD | 1 |
| 3B.6 | 97d0d07 | E2E feedback chain integration tests | +374 | 6 | senior SHIP IT, code-flow CLEAN, test ACTUALLY GOOD | 1 |
| 3B.7 | 20f6bee | concurrent baseline-writes + endpoint race fix | +309 | 3 | senior SHIP IT, security CLEAN, code-flow CLEAN, db SHIP IT, test ACTUALLY GOOD | 1 |

**Total**: 7 commits, ~2,300 net lines, 86 new tests, **all 7 commits passed reviewer panel in a single cycle**.

This is a dramatic improvement over Batch 3A's ~50% cycle-2 rate. Root cause: 3B benefited from clearer plan + 3A-learned discipline applied preemptively (cast at DB-to-Pydantic boundary, dual tenant filters, UniqueViolationError handling, exhaustively-tested helper extraction).

## Plan deviations

| # | Deviation | Reason | Plan resolution |
|---|---|---|---|
| 3B.1 | SQL COMMENT contained `$1` and `:311-312` patterns that sqlalchemy interpreted as bind parameters | Mid-implementation discovery (the alembic upgrade failed with `bind parameter '311'`). Reworded comments to plain prose ("decisions.request_id lookup", "lines 296 and 311-312"). No semantic change. | Applied before commit |
| 3B.3 | `cast(FeedbackLabel | None, ...)` at DB-to-Pydantic boundary | mypy strict required explicit narrowing of `str | None` from asyncpg into the Literal alias. CHECK ck_feedback_label guarantees the cast is safe by construction. | Applied during 3B.3 |
| 3B.3 | UniqueViolationError → 409 on the audit INSERT in BOTH the apply path AND the monotonicity-skip path | Defense-in-depth + symmetric handling. Discovered during plan-implementation that the monotonicity-skip path also INSERTs an audit row (operator-action visibility) and that path also needs the try/except. | Applied during 3B.3 |
| 3B.3 | `_decision_id` parameter dropped from `_insert_feedback_row` (originally accepted-but-unused) | Senior + code-flow reviewer feedback. The pure-bootstrap feedback schema has no decision_id column. | Applied between cycles |
| 3B.4 | `build_context` signature extended with optional `email_hmac` + `phone_hmac` kwargs | Required to populate the 4 previously-rejected Context fields (the existing flow didn't carry them). Default None — `build_modification_context` keeps working via its synthetic-booking pattern. | Applied during 3B.4 |
| 3B.7 | **SCOPE EXPANSION** — bundled production race fix into the test commit | Writing test (c) (concurrent rejected + fraud_confirmed for same target) surfaced a real production race: the tier-2 monotonicity SELECT ran BEFORE the FOR UPDATE on customer_baselines, allowing two concurrent applies to both see prior_label=None. Splitting test from fix would leave a broken intermediate (per `feedback_atomic_commits` memory). | Bundled in 3B.7 with reviewer panel re-scoped to 5 lenses |

## Reviewer-caught corrections

| Commit | File:line | Finding | Reviewer | Resolution |
|---|---|---|---|---|
| 3B.3 | `app/api/feedback.py:318` | Unused `_decision_id` parameter | senior + code-flow | Dropped at both call sites |
| 3B.3 | `tests/unit/test_feedback_counter_transitions.py:6` | Docstring contained `×` (RUF002) | ruff | Auto-fixed |
| 3B.7 | `app/api/feedback.py:222` | Code comment could cross-reference the pre-lock SELECT it mirrors | code-flow | Added explicit cross-reference |

3 corrections across 7 commits — much lower than 3A's 15.

## Tangential issues logged to BUGS.md

None during 3B.

## Production bug surfaced during 3B execution

**Feedback endpoint race condition** (3B.7) — fixed in same commit as the test that discovered it.

The original endpoint's tier-2 monotonicity SELECT ran BEFORE acquiring the FOR UPDATE lock on `customer_baselines`. Two concurrent `rejected`/`fraud_confirmed` feedback POSTs for the same `target_request_id` both read `prior_label=None`, both applied, and `customers.flagged_count` was double-incremented.

Fix: after the FOR UPDATE acquisition, re-run the tier-2 SELECT. The lock guarantees the second transaction blocks until the first commits; the re-read sees the committed label and `_label_stronger` correctly blocks the duplicate apply. The audit row is still INSERTed (operator-action visibility); only baseline + counter writes are skipped on the loser.

Verification: `test_concurrent_upgrade_feedbacks_apply_correctly` fails on pre-fix code (flagged_count=2) and passes on post-fix (flagged_count=1).

## Explicitly deferred items

| Item | Original scope | Deferred to | Reason |
|---|---|---|---|
| `ux_decisions_tenant_request` UNIQUE widening to include `request_type` | 3A.6 → BUGS.md | Phase 5 | Inherited from 3A; not relevant to 3B feedback path |
| Per-tenant operator_id validation (e.g., `TenantConfig.allowed_operator_ids`) | — | Phase 4 | operator_id is opaque text in Phase 3B; tenant-config validation belongs in Phase 4 |
| Feedback admin/query endpoint (list feedback for a target, etc.) | — | Phase 4 | Read endpoints are Phase 4 scope |

## Performance notes

Feedback endpoint runs:
- 1 SELECT (tier-1 idempotency)
- 1 SELECT (target resolution, 2-table JOIN)
- 1 SELECT (tier-2 monotonicity, pre-lock)
- 1 SELECT FOR UPDATE (`customer_baselines.load`) — on rejected/fraud_confirmed path
- 1 SELECT (tier-2 monotonicity, post-lock re-read, race fix) — on rejected/fraud_confirmed apply path
- 1 UPDATE (baseline.save)
- 1 UPDATE (customers counter, conditional on flag/fraud delta non-zero)
- 1 INSERT (feedback audit row)

Total: 5–8 sequential awaits depending on label and path. All single-row indexed operations. Approved path skips FOR UPDATE + baseline.save + counter UPDATE → ~3 awaits.

Concurrent booking + feedback on same customer serialise on `customer_baselines` FOR UPDATE (no deadlock — both endpoints acquire same lock first, then touch other rows in deterministic order). Concurrent feedbacks on same customer serialise the same way + benefit from the post-lock tier-2 re-read.

## Carry-forward to Phase 3C

1. **Cross-tenant test pattern**: 3B introduced multiple cross-tenant tests using `create_tenant_with_token`. 3C's comprehensive cross-tenant test sweep extends this pattern across all 4 endpoints.
2. **RLS audit input**: 4 endpoints now (`booking`, `health`, `modification`, `feedback`). 3C audit doc enumerates queries from all of them.
3. **Phase 5 RLS prerequisite**: the new `feedback` table (3B.1) has `ENABLE ROW LEVEL SECURITY` + `tenant_isolation` policy + `riskd_app` grants in place. 3C audit verifies completeness against the 4 endpoints.
4. **Concurrency lesson**: the race surfaced in 3B.7 (read-before-lock) is a generalizable pattern. 3C's RLS audit + non-superuser test should look for similar patterns in admin/Phase-4 endpoints when they land.

## Tests status

| Component | Pre-3B | Post-3B | Delta |
|---|---|---|---|
| Unit | ~340 | ~430 | +90 |
| Integration | ~221 | ~221 | +0 (excluding 3B count split — see total) |
| Total | 561 | **651** | +90 |

All 651 tests pass. ruff clean, mypy strict clean.

## Operator checkpoint

Per the operator's per-batch preference (deferred 3C/3D approval), 3C execution requires explicit re-approval after reviewing this report. The 3C plan (`PLAN_PHASE_3C.md`) is unchanged from initial production. Operator may approve as-is or request revisions.
