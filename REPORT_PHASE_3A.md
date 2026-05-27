# Phase 3 — Batch 3A Report

**Batch**: 3A — Modification endpoint stack
**Commits**: 3A.1 through 3A.8 (8 commits) + Phase 3 plans commit
**Date range**: 2026-05-27
**Status**: COMPLETE — operator approves 3B before execution

## Aggregate stats

| Metric | Pre-3A (end of Phase 2) | Post-3A |
|---|---|---|
| Rule count | 67 | 75 (+8 modification rules) |
| Test count | 432 | 561 (+129) |
| ALLOWED_CONTEXT_FIELDS | 56 | 62 (+6 modification fields) |
| Migrations | 2 (0001, 0002) | 3 (+0003 decisions.request_type) |
| New `.py` files under `app/` | — | `app/api/modification.py` |
| New endpoints | 2 (booking, health) | 3 (+ modification) |
| `.ai/decisions.md` new subsections | — | "Modification rule weight rationale (Phase 3A)" |

## Per-commit disposition

| # | Hash | Theme | LoC (net) | Tests added | Reviewer panel | Cycles |
|---|---|---|---|---|---|---|
| 3A.1 | 5d26f8d | `decisions.request_type` migration + index | +60 | 0 (manual round-trip) | senior SHIP IT, security LOW RISK/CLEAN, code-flow CLEAN, db SHIP IT | 1 |
| 3A.2 | 331d9bb | Pydantic ModificationRequest / ModificationResponse | +228 | 35 | senior SHIP IT, security LOW RISK/CLEAN, code-flow CLEAN, test ACTUALLY GOOD | 1 |
| 3A.3 | 9a8f4a5 | DSL whitelist +6 modification fields | +122 | 11 | senior SHIP IT, security LOW RISK/CLEAN, code-flow CLEAN, test ACCEPTABLE → cycle 2: ACTUALLY GOOD | 2 |
| 3A.4 | d2d4433 | `build_modification_context` + 3 pure helpers | +499 | 37 | code-flow MINOR ISSUES, security LOW RISK/CLEAN, test ACTUALLY GOOD, senior NEEDS MINOR FIXES → cycle 2: all SHIP IT/CLEAN/LOW RISK | 2 |
| 3A.5 | b8f3fb4 | Modification velocity SQL helpers + wiring | +316 | 6 | senior SHIP IT, security LOW RISK/CLEAN, code-flow CLEAN, db APPROVED WITH RESERVATIONS, test ACCEPTABLE → cycle 2: db SHIP IT | 2 |
| 3A.6 | e790bd0 | Endpoint route + booking patch + 409 collision safety | +617 | 8 | db SHIP IT, test ACTUALLY GOOD, senior NEEDS MINOR FIXES, security MEDIUM RISK, code-flow MINOR ISSUES → cycle 2: senior SHIP IT, security LOW RISK/CLEAN, code-flow CLEAN | 2 |
| 3A.7 | 245c070 | 8 modification rules + booking-path defaults + 25 unit tests | +444 | 25 | security LOW RISK/CLEAN, test ACTUALLY GOOD, senior APPROVED WITH RESERVATIONS, code-flow MINOR ISSUES → cycle 2: senior SHIP IT, code-flow CLEAN | 2 |
| 3A.8 | dbcff67 | E2E modification flow integration tests | +426 | 7 | test ACTUALLY GOOD, senior SHIP IT, code-flow CLEAN | 1 |

**Total**: 8 commits, ~2,700 net lines of code + tests, 129 new tests, ~50% of commits required a cycle-2 review.

## Plan deviations

| # | Deviation | Reason | Plan resolution |
|---|---|---|---|
| 3A.1 | DEFAULT 'booking' retained on `decisions.request_type` (plan called for DROP DEFAULT post-backfill) | Per operator feedback during planning: dropping DEFAULT would create a declared break between 3A.1 and 3A.6 requiring `--no-verify`. Retained as safety net; explicit literal at INSERT call sites in 3A.6 covers the intent-at-call-site goal. | Plan amended pre-execution (2026-05-27) |
| 3A.1 | Index columns: `(tenant_id, request_type, created_at)` not `(tenant_id, request_type)` | Per db-reviewer cycle-1 suggestion: matches `ix_shipments_tenant_*_booking_ts` project pattern, enables range scan at the index leaf for 3A.5 velocity queries | Applied before commit |
| 3A.2 | `source_ip: IPv4Address` (plan called for `IPvAnyAddress`) | Matches existing `BookingRequest.source_ip` convention — v1 is IPv4-only per `.ai/decisions.md` | Applied during 3A.2 execution |
| 3A.2 | Response omits `account_prior` / `signal_score` / `maturity` | Plan said "if exposed there" — BookingResponse doesn't expose them; ModificationResponse mirrors BookingResponse | Applied during 3A.2 execution |
| 3A.4 | `_modification_direction` does NOT take `hmac_secret` | Cycle-1 reviewers (security + code-flow + test) flagged the HMAC computation as dead code (never compared); removed entirely. Direction uses plaintext baseline.dest_stats membership (matches baseline._bump precedent). | Applied in cycle 2 |
| 3A.5 | Tests landed in `tests/integration/test_velocity.py` (plan named `tests/unit/test_velocity_modifications.py`) | These are DB-touching SQL helpers — integration is the correct home. Reuses existing fixtures. | Noted in commit message |
| 3A.5 | `WHERE d.tenant_id = $1 AND s.tenant_id = $1` (dual filter) | Per `.ai/conventions.md` "Tenant scoping in raw SQL" — explicit filter on every leg, not just the lead table. Db-reviewer cycle-1 reservation. | Applied in cycle 2 |
| 3A.6 | Idempotency SELECT scoped by `request_type` on BOTH endpoints (booking and modification); UniqueViolationError → 409 | Per converging reviewer feedback: the flat `ux_decisions_tenant_request` UNIQUE creates a cross-namespace collision 500 path. Migration to widen UNIQUE deferred to Phase 5 via BUGS.md follow-up; 409 catch is the safety net. | Applied in cycle 2 |
| 3A.6 | Customer-not-found defensive 404 → assert | The FK from shipments.customer_id enforces the row exists; defensive 404 was dead code that would hide schema corruption. | Applied in cycle 2 |
| 3A.7 | `BOOKING_PATH_MODIFICATION_DEFAULTS` extracted as Final[dict] constant | Per code-flow cycle-1 finding: production/test fixture defaults were duplicated → drift risk. Single source of truth in app/context.py spread by both call sites. | Applied in cycle 2 |
| 3A.7 | `'none'` sentinel for `modification_type` default (matches no enum literal) | Necessary for DSL evaluator: every referenced field must be populated at eval time. Sentinel ensures modification rules don't fire on the booking path. | Built into design |

## Reviewer-caught corrections

| Commit | File:line | Finding | Reviewer | Resolution |
|---|---|---|---|---|
| 3A.1 | `alembic/versions/0003_decisions_request_type.py:42` | Index should be `(tenant_id, request_type, created_at)` per project pattern | db-reviewer | Applied before commit |
| 3A.2 | `app/models.py:126` | `new_value: dict[str, Any]` lacks shape-varies comment | code-flow + security | Comment added before commit |
| 3A.3 | `tests/unit/test_rules_modification_whitelist.py` | Plan-specified `load_rules` smoke test missing; trivial assertions on `tree` variable; tautological `DSLError importable` test; misleading negative-control test name | test-reviewer | All 4 fixed in cycle 2 |
| 3A.4 | `app/context.py:_modification_direction` | Dead HMAC computation + misleading comment + unreachable else-branch | senior + security + code-flow | Removed entirely in cycle 2 |
| 3A.4 | `app/context.py:_modification_magnitude` value branch | Crashes on non-numeric `new_value["value"]` | security + code-flow | try/except → 0.0 in cycle 2 |
| 3A.4 | `app/context.py:25` | `date_cls` alias adds churn without benefit | code-flow + senior | Reverted to `date` in cycle 2 |
| 3A.5 | `app/velocity.py:122,153` | Missing explicit `s.tenant_id = $1` in WHERE per `.ai/conventions.md` | db-reviewer | Dual filter applied in cycle 2 |
| 3A.5 | `tests/integration/test_velocity.py:752` | Working-notes-in-docstring on test_modifications_ignores_booking_decisions | senior + code-flow | Cleaned in cycle 2 |
| 3A.6 | `app/api/booking.py:50` | Idempotency SELECT lacks `request_type` filter (asymmetric with modification endpoint) | senior + security + code-flow | Applied in cycle 2 |
| 3A.6 | `app/api/{booking,modification}.py` INSERT | UniqueViolationError not caught → 500 on cross-namespace collision | senior + security + code-flow | try/except → 409 in cycle 2 |
| 3A.6 | `app/api/modification.py:107-112` | Defensive 404 on customer-not-found hides schema corruption | code-flow | Converted to assert in cycle 2 |
| 3A.7 | `app/rules.yaml:467-471` | Block comment factually wrong (describes pre-3A.7 state) | senior + code-flow | Rewrote in cycle 2 |
| 3A.7 | `app/context.py` + `tests/unit/conftest.py` | 6-field default duplication → drift risk | code-flow | Extracted `BOOKING_PATH_MODIFICATION_DEFAULTS` constant in cycle 2 |
| 3A.7 | `app/rules.py:102` | Whitelist comment doesn't mention the 'none' sentinel | security | Added in cycle 2 |
| 3A.7 | `tests/unit/test_rules_modification.py:1-10` | Docstring claims "1 fire + 1 no-fire per rule" but actual is 1 fire + 1-2 no-fire | senior | Rewrote in cycle 2 |

15 reviewer-caught corrections across 8 commits — median 2 per commit.

## Tangential issues logged to BUGS.md

1. **2026-05-27 — decisions.ux_decisions_tenant_request UNIQUE is flat across request_type** (severity=medium). The flat `ux_decisions_tenant_request` UNIQUE constraint precedes the Phase 3A `request_type` discriminator. Both endpoints' idempotency SELECTs scope by their own `request_type`, but the DB constraint does not. 3A.6 added try/except → 409 on both endpoints; Phase 5 should widen the UNIQUE to `(tenant_id, request_type, request_id)`. Discovered by senior + security + code-flow during 3A.6 cycle 1.

## Explicitly deferred items

| Item | Original scope | Deferred to | Reason |
|---|---|---|---|
| `ux_decisions_tenant_request` UNIQUE widening | 3A.6 | Phase 5 (BUGS.md tracked) | Pre-launch in-tenant blast radius; 409 catch is sufficient safety net |
| Modification velocity parallelism (separate-pool connections) | Per Phase 3 watch-point | Phase 5 load test | 11 sequential awaits on the txn connection on modification path; latency budget review |
| `last_seen` update on modification | Implicit in booking endpoint | Phase 4+ | Plan deliberately scoped to decision-only persistence on modifications |
| Modification weight calibration | 3A.7 | Phase 6 staging replay | Per `.ai/decisions.md` rule weight tuning policy + `feedback_no_weight_tuning_phase2` memory entry |
| Migration of email/phone HMACs on shipments | 3B.1 | Phase 3B | Necessary for feedback endpoint per-shipment HMAC lookup; modification path doesn't need them |
| Modification of modification (modify-of-modification) | — | Out-of-scope v1+ | Phase 3 scope decision; endpoint returns 422 |
| Extended modification decision states (REVERT, CANCEL) | — | v2+ | Phase 3 scope decision |

## Performance notes

Modification endpoint runs **11 sequential awaits** on the txn connection (9 inherited from `build_context` + 2 modification-velocity SQL queries). Per Phase 3 watch-point, this is acceptable for the current pre-launch latency budget; Phase 5 load test revisits if needed.

Concurrent booking + modification on the same customer serialise correctly via `SELECT FOR UPDATE` on `customer_baselines` — verified by `test_concurrent_booking_and_modification_serialise` (3A.8).

## Carry-forward to Phase 3B

1. **`shipments.email_hmac` / `phone_hmac` columns**: 3B.1 migration adds these so the feedback endpoint can populate baseline.rejected_email_hmacs / rejected_phone_hmacs per-shipment.
2. **Feedback endpoint drop-and-recreate**: 3B.1 migrates feedback table to pure bootstrap shape (operator-decided 2026-05-27).
3. **Booking endpoint INSERT patch**: 3B.3 will patch booking.py INSERT to write email_hmac/phone_hmac (which currently aren't captured in shipments).
4. **`request_type` namespace collision pattern**: Phase 5 widens the UNIQUE constraint (BUGS.md tracked); 3B endpoint's INSERT should adopt the same try/except → 409 pattern preventively.
5. **`BOOKING_PATH_MODIFICATION_DEFAULTS`** is a precedent: if 3B adds new context fields with booking-vs-feedback default divergence, follow the same single-source-of-truth constant pattern.

## Tests status

| Component | Pre-3A | Post-3A | Delta |
|---|---|---|---|
| Unit | ~250 | ~340 | +90 |
| Integration | ~180 | ~221 | +41 (modification endpoint, velocity, E2E flow) |
| Total | 432 | **561** | +129 |

All 561 tests pass. ruff clean, mypy strict clean.

## Operator checkpoint

Per the operator's per-batch preference (deferred 3B/3C/3D approval), 3B execution requires explicit re-approval after reviewing this report. The 3B plan (`PLAN_PHASE_3B.md`) is unchanged from its initial production except for the drop-and-recreate migration choice (already absorbed during planning). Operator may approve as-is or request revisions.
