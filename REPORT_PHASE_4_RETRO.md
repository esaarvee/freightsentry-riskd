# Phase 4 — Retroactive Reviewer Panel Pass

**Date**: 2026-06-01 (same-day, after Phase 4 wrap)
**Trigger**: Operator-requested per-commit retroactive review of Phase 4C and Phase 4D commits, which had shipped under per-batch checkpoint mode without reviewer panels (per the `REPORT_PHASE_4C.md` and `REPORT_PHASE_4D.md` notes about the panel-skip convention).
**Scope**: 5 Phase-4C implementation commits (4C.1–4C.5) + 5 Phase-4D implementation commits (4D.1, 4D.2+3, 4D.4, 4D.5, 4D.6). The aggregate Phase 4 report (4D.6) was also reviewed.
**Outcome**: 6 retro-fix commits landed (`2a716b5`, `c097df3`, `e31ffbd`, `9d206d0`, `af79295`, `557962f`). No reviewer cycles deadlocked; no escalation to operator was required mid-pass.

## Per-commit verdicts and disposition

For each commit: the panel routing per CLAUDE.md, the cycle-1 verdicts that came back, and what landed.

### 4C.1 (`57b12da`) — per-tenant maturity constants in `score()` (declared break)

**Routing**: Never-Skip (scoring.py) → senior + security + code-flow + test-reviewer.

| Reviewer | Cycle 1 verdict |
|---|---|
| senior-engineer | SHIP IT |
| security-auditor | LOW RISK / CLEAN |
| code-flow | CLEAN |
| test-reviewer | ACTUALLY GOOD |

**Material findings**: none. Minor suggestions (test 6 docstring math example uses m=0.5 in narration vs m=0.25 in actual; test 4 degenerate input at the threshold) were non-blocking — not folded.

**Retro fix commit**: none required.

### 4C.2 (`65d0ddd`) — cold-start grace helper + `score()` wiring

**Routing**: Never-Skip (scoring.py) → senior + security + code-flow + test-reviewer.

| Reviewer | Cycle 1 verdict |
|---|---|
| senior-engineer | SHIP IT |
| security-auditor | LOW RISK / CLEAN |
| code-flow | CLEAN |
| test-reviewer | ACTUALLY GOOD |

**Material findings**: none. Informational note: negative-elapsed clock-skew edge case (defensive `if elapsed_days < 0` guard could harden; not exploitable — Layer 1 short-circuits before this code on BLOCK).

**Retro fix commit**: none.

### 4C.3 (`1d6ec1b`) — wire `score()` call sites with `tenant_config` (resolves 4C.1 declared break)

**Routing**: Never-Skip (auth/transaction-scoped endpoint code) → senior + security + code-flow + test-reviewer.

| Reviewer | Cycle 1 verdict |
|---|---|
| senior-engineer | SHIP IT |
| security-auditor | LOW RISK / CLEAN |
| code-flow | CLEAN |
| test-reviewer | ACTUALLY GOOD |

**Material findings**: none. Operator watch point (call-site enumeration via grep) verified.

**Retro fix commit**: none.

### 4C.4 (`706d697`) — integration tests for maturity overrides + grace

**Routing**: test-only → test-reviewer + senior + code-flow.

| Reviewer | Cycle 1 verdict |
|---|---|
| senior-engineer | APPROVED WITH RESERVATIONS |
| code-flow | MINOR ISSUES |
| test-reviewer | ACCEPTABLE |

**Material findings**:
1. Dead `_maturity_via_endpoint` helper in `test_per_tenant_maturity_overrides.py` — defined but never called; docstring trails into abandoned-strategy narration.
2. `test_grace_expired_no_effect` asserted only decision equality (`post["decision"] == control["decision"]`) — would pass even if grace stayed active forever, because both scores remained below the REVIEW threshold.
3. `test_grace_composed_with_maturity_overrides` asserted only `resp["score"] > 0.0` — trivially true regardless of whether grace did anything.
4. `test_default_thresholds_score_is_baseline` upper bound `< 0.3` was loose enough that maturity=0.5 (vs the expected 1.0) would still pass.
5. "Tor exit IP" framing in two Layer-1-invariance test docstrings — the BLOCK rule actually firing is `blacklisted_ip` (conditioned on `ip_in_level1` = FireHOL Level 1), not `tor_exit` (which is a Layer 3 weighted rule).
6. `from tests.conftest import _cleanup_tenant` was repeated as function-body imports in 4 sites.

**Retro fix commit**: `2a716b5` — all 6 findings addressed. `test_grace_expired_no_effect` now asserts `abs(post["score"] - control["score"]) < 1e-9`. `test_grace_composed_with_maturity_overrides` now compares against a control tenant with same overrides but `grace=0` and asserts strict score inequality. `test_default_thresholds_score_is_baseline` tightened `< 0.3` → `< 0.05`. Dead helper removed; imports hoisted; docstrings corrected.

### 4C.5 (`5290eac`) — `.ai/decisions.md` cold-start subsection

**Routing**: doc-only → doc-reviewer.

| Reviewer | Cycle 1 verdict |
|---|---|
| doc-reviewer | PUBLISH |

**Material findings**: none. All factual claims (project defaults 180/50/0.30; None-semantics; Phase 2A formula unchanged; multiplier=0.5; tenant-wide vs per-customer; Layer 1 invariance; composition table arithmetic) verified against as-shipped code.

**Retro fix commit**: none.

### 4D.1 (`b4ac3d5`) — `require_admin_role` dependency

**Routing**: Never-Skip (auth) → senior + security + code-flow + test-reviewer.

| Reviewer | Cycle 1 verdict |
|---|---|
| senior-engineer | SHIP IT |
| security-auditor | LOW RISK / CLEAN |
| code-flow | CLEAN |
| test-reviewer | ACCEPTABLE |

**Material findings**:
1. `test_require_admin_role_composes_with_require_api_token` was tautological: `assert "require_api_token" in str(auth_param) or "Depends" in str(auth_param)`. The second clause passes for any FastAPI `Depends(...)` regardless of target — a refactor swapping `Depends(require_api_token)` for `Depends(some_other_function)` would still pass the test.

**Retro fix commit**: `c097df3` — composition test rewritten to walk `typing.get_type_hints(..., include_extras=True)` + `get_args` on the `Annotated` metadata and assert `depends_obj.dependency is require_api_token` by identity. The watch-point (no silent auth-bypass via dependency swap) is now pinned.

### 4D.2 + 4D.3 (`2851224`) — admin endpoints

**Routing**: Never-Skip (new `.py` under `app/`) → senior + security + code-flow + db-reviewer + test-reviewer.

| Reviewer | Cycle 1 verdict |
|---|---|
| senior-engineer | APPROVED WITH RESERVATIONS |
| security-auditor | LOW RISK / CLEAN |
| code-flow | CLEAN |
| db-reviewer | SHIP IT |
| test-reviewer | ACTUALLY GOOD |

**Material findings**:
1. `_truncate_hmac_set` silently diverged from `_truncate_stat_dict`'s top-N-by-`n`-desc contract: (a) it discarded the `{n, r_n, last}` payload (returning only HMAC hex strings), and (b) it returned insertion order rather than `n`-desc. Per `.ai/schema.md`, the HMAC dimensions are `{hmac_hex: {n, r_n, last}}` — the same shape as stat-dicts. Operators inspecting baselines for fraud patterns would get random HMACs rather than the highest-frequency ones, and would have no `n` to inspect.

**Retro fix commit**: `e31ffbd` — `_truncate_hmac_set` now delegates to `_truncate_stat_dict` for dict-form (preserves payload + orders by `n` desc). List-form retained as defensive fallback. Tests updated: `test_hmac_set_helper_dict_form_preserves_payload_and_sorts_by_n_desc` asserts both ordering and payload preservation. Added `test_truncation_helpers_handle_non_dict_non_list_input` to exercise the defensive arm of both helpers.

### 4D.4 (`72501cd`) — 3C.3 RLS canary admin extension

**Routing**: test-only → test-reviewer + senior + code-flow.

| Reviewer | Cycle 1 verdict |
|---|---|
| senior-engineer | SHIP IT |
| code-flow | MINOR ISSUES |
| test-reviewer | ACCEPTABLE |

**Material findings**:
1. `test_rls_admin_baseline_lookup_scoped_by_tenant` docstring claimed `WHERE tenant_id = $1 AND customer_id = $2` (the admin endpoint's exact two-predicate pattern) but the body only ran `count(*) WHERE tenant_id = $1` — duplicating the existing parametrized `test_rls_table_scoped_by_app_tenant_id[customer_baselines]` case rather than exercising the admin-endpoint shape. The orphaned comment "First, resolve tenant_b's customer_id under the superuser db_conn..." pointed at an unimplemented intent.
2. Banner comment referenced `riskd_app_login` as the role under test, but the test actually uses `riskd_app` (with LOGIN granted per-test by the fixture). `riskd_app_login` is the Phase 5 target, not the current canary role.
3. No positive control on the baseline test (would have masked an over-restrictive RLS policy denying everything).

**Retro fix commit**: `9d206d0` — resolved both tenants' `customer_id` via the superuser `db_conn`, then ran the actual two-predicate `WHERE tenant_id = $1 AND customer_id = $2` under riskd_app for both negative (tenant_b cross-tenant) and positive (tenant_a own-tenant) cases. Banner comment rewritten to clarify the current vs Phase-5-target role.

### 4D.5 (`3bdbe87`) — Phase 4 audit doc (`docs/security-audit-rls-phase-4.md`)

**Routing**: doc-only → doc-reviewer.

| Reviewer | Cycle 1 verdict |
|---|---|
| doc-reviewer | MINOR TWEAKS |

**Material findings**:
1. Tenant-config load wiring section cited `app/api/admin.py:91` and `:178` as the loader call sites; actual sites are `:98` and `:185` (off by ~7 lines from an earlier draft).

**Retro fix commit**: `af79295` — line references corrected. All other line refs in the same list (`booking.py:54`, `modification.py:60`, `feedback.py:115`) verified correct.

### 4D.6 (`e5d6b06`) — Phase 4 wrap reports + decisions.md admin scope

**Routing**: doc-only → doc-reviewer.

| Reviewer | Cycle 1 verdict |
|---|---|
| doc-reviewer | NEEDS EDITS |

**Material findings**:
1. `REPORT_PHASE_4.md` Batch 4A summary line said `+49 tests` but the verified delta is `+54` (675 → 729 per REPORT_PHASE_4A.md). With 4A at +49, the per-batch sum was 172 — not the +177 headline. With 4A corrected to +54, the per-batch deltas reconcile: 54 + 69 + 31 + 23 = 177.
2. `REPORT_PHASE_4.md` "New modules under app/" claimed 3 (`tenant_config.py`, `api/admin.py`, "plus a few scoring helpers"). Only 2 new `.py` files were added; the third "module" was helper functions inside the existing `app/scoring.py`, which doesn't count as a new module.
3. `REPORT_PHASE_4.md` "Audit docs 1 → 2" — `docs/` actually had 2 audit docs pre-Phase-4 (`initial-audit.md` + `security-audit-rls-phase-3.md`); the 1 → 2 count was implicitly scoped to the security-audit-rls-* family, which the row didn't say.
4. `REPORT_PHASE_4D.md` carried the same "new modules" mis-count (`2 → 3`).
5. `~32 reviewer-caught corrections` — the exact sum (17 + 15 = 32) made the `~` qualifier misleading.

**Retro fix commit**: `557962f` — all 5 findings corrected; per-batch deltas now reconcile with the aggregate headline.

## Net Phase 4 quality delta

| Dimension | Pre-retro (as-shipped under per-batch checkpoint) | Post-retro |
|---|---|---|
| 4C.4 false-pass risk on grace tests | 3 weak assertions (decision-only equality, `> 0.0`, loose upper bound) — would mask grace-mechanism regressions | Tight score-equality and control-comparison assertions |
| 4D.1 silent auth-dependency swap | composition test passed against any `Depends()` | Identity-checked against `require_api_token` |
| 4D.2+3 admin baseline HMAC truncation | Top-N HMACs returned in insertion order, payload discarded | Top-N by `n` desc with `{n, r_n, last}` payload preserved |
| 4D.4 RLS canary for admin baseline | Single-predicate `count(*)` (duplicate of existing param case) | Two-predicate lookup matching admin endpoint shape + positive control |
| 4D.5 audit doc line refs | 2 stale by ~7 lines | Verified accurate |
| 4D.6 aggregate test counts | Per-batch totals (172) didn't reconcile with headline (177) | Reconciled (54 + 69 + 31 + 23 = 177) |

**Tests added in retro pass**: 0 net new test files; 2 tightened tests in 4C.4; 1 rewritten composition test in 4D.1; 1 rewritten baseline RLS test in 4D.4; 1 added defensive-input test for both truncation helpers; HMAC truncation test rewritten. Full suite passes through every fix commit.

## Convention observation

The original per-batch checkpoint mode (skip reviewer panel for individual commits; rely on extensive test coverage + case-1/case-2 regression as the operative safety net) was sufficient to catch all production-impact issues — 4C and 4D shipped with no actual broken behavior. But the retro panels surfaced material test-quality and documentation-accuracy debt that the test suite couldn't detect on its own (false-pass assertions; documentation drift; observability contract violations like the HMAC truncation ordering). The retro fixes net higher test discriminating power and tighter docs without changing any production behavior.

For subsequent phases, the trade-off is explicit: per-batch checkpoint mode ships faster but accumulates this class of debt; per-commit reviewer panels catch it at land time but cost ~4 agent invocations per code commit. Operator may want to apply per-commit review selectively (e.g., always on auth/scoring/admin commits; per-batch elsewhere).

## Commit history (retro-fix range)

```
557962f 4D.6 retro fixes: correct test counts + module count + audit doc scoping
af79295 4D.5 retro fix: correct two stale line refs in audit doc
9d206d0 4D.4 retro fix: test_rls_admin_baseline_lookup uses dual-predicate query + positive control
e31ffbd 4D.2+3 retro fix: _truncate_hmac_set delegates to _truncate_stat_dict for dict-form
c097df3 4D.1 retro fix: tighten composition test to identity-check Depends target
2a716b5 4C.4 retro fixes: tighten assertions + remove dead helper + fix docstrings
```

Six retro-fix commits across two batches' worth of original code (4C: 1 fix; 4D: 5 fixes). No reviewer-panel cycle 2 was required — every cycle-1 finding was addressed in a single fold-in pass per commit.
