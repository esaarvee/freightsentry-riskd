# Phase 4 — Batch 4C Plan — Cold-start window enforcement

> **Status (2026-06-01)**: Pending operator approval. Approval may be deferred until after 4B execution reports.

Batch 4C extends `app/scoring.py` to consult `tenant_config` for the maturity constants (`MATURITY_AGE_DAYS`, `MATURITY_SHIPMENTS`, `MATURITY_K`) and adds the cold-start grace period mechanism. The grace period applies a 0.5x multiplier to maturity during the configured days after tenant onboarding, softening maturity-sensitive rule firing for newly-onboarded tenants while they accumulate baselines.

**Critical framing.** The Phase 2A scoring formula is the contract — 4C does NOT change the formula, only the constants the formula consults. The noisy-OR composition, Layer 1 short-circuit, and Layer 3 maturity downweight math are all untouched.

Target: **5 commits**.

---

## Decisions absorbed (batch-relevant subset)

| Decision | Value | Source |
|---|---|---|
| Override fields consulted by scoring | `tenant_config.maturity_age_days`, `tenant_config.maturity_shipments`, `tenant_config.maturity_k` (None → use project default from `app/scoring_constants.py`) | Phase 4 prompt |
| Project-default constants source | `app/scoring_constants.py` REMAINS source of truth. NOT moved into TenantConfig. NOT moved into rules.yaml. Tenant config overrides on top. | Phase 4 prompt |
| `score()` signature change | Add `tenant_config: TenantConfig` parameter. Callers (booking, modification endpoints) already have `tenant_config` in scope from 4A. | Phase 4 prompt |
| Maturity formula | UNCHANGED. `maturity = age_frac * ship_frac` (multiplicative; Phase 2A divergence from FreightSentry per decisions.md). Only the threshold values consulted change. | decisions.md § Layer 2 |
| Maturity downweight formula | UNCHANGED. `effective_weight = weight * (1 - MATURITY_K * (1 - maturity))`. Only `MATURITY_K` source changes. | decisions.md § Layer 3 |
| Cold-start grace period semantics | `cold_start_grace_days` field on TenantConfig (already in 4A's model). During grace window (now - tenants.created_at < cold_start_grace_days), multiply computed maturity by 0.5. After grace window, no multiplier. | Phase 4 prompt |
| Grace window measured from | `tenants.created_at` (existing column, Phase 1 schema). Compared against `datetime.now(UTC)` at scoring time. | Phase 4 prompt + Phase 1 schema |
| Grace multiplier value | 0.5 (hardcoded; not tenant-configurable in Phase 4). The MULTIPLIER itself is a Phase 6 calibration candidate. | Phase 4 prompt |
| Grace check placement | Inside `score()` in `app/scoring.py`, BEFORE the existing `m = maturity(...)` line. Applies once to the single `m` value used by both Layer 2 base_prior and Layer 3 maturity downweight. | Phase 4 prompt |
| `tenant_config.created_at` source | Already populated by `load_tenant_config` (4A.2) from `tenants.created_at`. Scoring uses `tenant_config.created_at`, not a separate DB read. | Phase 4 prompt + 4A wiring |
| Per-customer maturity override | EXPLICITLY OUT. Tenant-level only. | Phase 4 prompt |
| Maturity constants module | `app/scoring_constants.py` UNCHANGED. No new constants added in 4C. | Phase 4 prompt + Phase 2A discipline |
| Scoring formula divergences from FreightSentry | Preserved (multiplicative maturity, linear shipments fraction, 4-tier flag prior, no customer-inheritance). | decisions.md § Amendment 2026-05-26 |
| Feedback endpoint impact | None. Feedback path does NOT call `score()`. 4C's signature change touches only booking + modification endpoints. | Phase 3B + 4C scope |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md. Pre-commit hooks active.
- Reviewer routing per CLAUDE.md never-skip clause: "Any change to the scoring formula or noisy-OR composition (`app/scoring.py`)" — every commit touching `app/scoring.py` is **never-skip → standard panel mandatory**.
- Per-commit reviewer routing:
  - 4C.1 (scoring.py per-tenant constant consultation refactor): **Never-Skip (scoring.py)** → standard panel + test-reviewer.
  - 4C.2 (cold-start grace period helper + integration in score()): **Never-Skip (scoring.py)** → standard panel + test-reviewer + security-auditor (timestamp arithmetic on hot path).
  - 4C.3 (call site updates in 2 endpoints): Never-Skip (auth-handling/transaction-scoped code) → standard panel + test-reviewer.
  - 4C.4 (integration tests): test-only → test-reviewer + senior + code-flow.
  - 4C.5 (`.ai/decisions.md` cold-start subsection): doc-only → doc-reviewer only.

- Reviewer-invocation slice template:
  > `Plan file: PLAN_PHASE_4C.md, current commit: 4C.N (<title>), upcoming commits: 4C.{N+1} through 4C.5 sections. Read only those sections.`

---

## Cross-batch dependencies

- **Consumes from 4A**: `TenantConfig` (`maturity_age_days`, `maturity_shipments`, `maturity_k`, `cold_start_grace_days`, `created_at`), `load_tenant_config` already wired into endpoints.
- **Consumes from Phase 2**: `app/scoring.py::score`, `app/scoring_constants.py` constants, `CustomerState` shape.
- **Consumes from Phase 1**: `tenants.created_at` column.
- **Does NOT consume from 4B**: 4C and 4B are independent consumers of 4A; 4C does not depend on currency normalization.
- **Consumed by 4D**: admin endpoints DO NOT call `score()` (read-only); 4C's changes are invisible at the admin endpoint layer. Phase 4 wrap report (4D) includes 4C summary.

---

## 4C.1 — Refactor `score()` to consult `tenant_config` for maturity constants

**Theme**: Add `tenant_config: TenantConfig` parameter to `score()` and use it to resolve `maturity_age_days`, `maturity_shipments`, `maturity_k`. Falls back to project defaults from `app/scoring_constants.py` when `None`.

**Files**:
- `app/scoring.py` (EDIT — signature + maturity calls)
- `tests/unit/test_scoring_per_tenant_overrides.py` (NEW)

**Specifics**:

### `app/scoring.py` changes

```python
from app.tenant_config import TenantConfig

# Import the bare project defaults for fallback. These remain the
# source of truth; TenantConfig overrides on top.
from app.scoring_constants import (
    FLAG_WEIGHTS,
    MATURITY_AGE_DAYS,
    MATURITY_K,
    MATURITY_SHIPMENTS,
    MAX_NEW_ACCOUNT,
    TRUST_FACTOR,
    flagged_count_tier,
)


def _resolved_maturity_constants(tenant_config: TenantConfig) -> tuple[int, int, float]:
    """Return (age_days_threshold, shipments_threshold, k) with overrides applied.

    None means "use project default from app/scoring_constants.py".
    """
    age = tenant_config.maturity_age_days if tenant_config.maturity_age_days is not None else MATURITY_AGE_DAYS
    ship = tenant_config.maturity_shipments if tenant_config.maturity_shipments is not None else MATURITY_SHIPMENTS
    k = tenant_config.maturity_k if tenant_config.maturity_k is not None else MATURITY_K
    return age, ship, k


def _maturity_with_overrides(
    *, age_days: int, total_shipments: int, age_threshold: int, ship_threshold: int,
) -> float:
    """Maturity formula consulting tenant-supplied thresholds.

    Mirrors `app/scoring_constants.py::maturity` but with caller-supplied
    thresholds instead of module-level constants. Phase 2A formula is
    UNCHANGED: multiplicative age_frac * ship_frac.
    """
    age_frac = min(max(age_days, 0) / age_threshold, 1.0)
    ship_frac = min(max(total_shipments, 0) / ship_threshold, 1.0)
    return age_frac * ship_frac


def score(
    ruleset: RuleSet,
    ctx: Mapping[str, Any],
    *,
    customer_state: CustomerState,
    tenant_config: TenantConfig,    # NEW required keyword arg
) -> ScoringResult:
    # Layer 1 — hard-block short-circuit (unchanged).
    for rule in ruleset.rules:
        if rule.action != "BLOCK":
            continue
        if rule.evaluate(ctx):
            return ScoringResult(
                score=1.0,
                account_prior=0.0,
                signal_score=0.0,
                maturity=0.0,
                decision="BLOCK",
                classification="RED",
                risk_level="CRITICAL",
                triggered_rules=(rule.name,),
                risk_factors=(_to_factor(rule),),
            )

    # Layer 2 — account prior with per-tenant maturity overrides.
    age_threshold, ship_threshold, k = _resolved_maturity_constants(tenant_config)
    m = _maturity_with_overrides(
        age_days=customer_state.account_age_days,
        total_shipments=customer_state.total_shipments,
        age_threshold=age_threshold,
        ship_threshold=ship_threshold,
    )
    # (Cold-start grace multiplier added in 4C.2; left for now as a marker
    # comment so 4C.2 has a precise insertion point and reviewers can
    # verify the grace multiplier applies BEFORE Layer 2 / 3 consume m.)
    # m = _apply_cold_start_grace(m, tenant_config)    # 4C.2

    base_prior = MAX_NEW_ACCOUNT * (1.0 - m)
    trust_risk = max(0.0, (0.5 - customer_state.trust_score) / 0.5)
    trust_contribution = trust_risk * TRUST_FACTOR
    flag_prior = FLAG_WEIGHTS[flagged_count_tier(customer_state.flagged_count)]
    account_prior = _noisy_or([base_prior, trust_contribution, flag_prior])

    # Layer 3 — signal noisy-OR with per-tenant maturity-K downweight.
    triggered: list[Rule] = []
    effective_weights: list[float] = []
    factors: list[RiskFactor] = []
    for rule in ruleset.rules:
        if rule.action == "BLOCK":
            continue
        if rule.evaluate(ctx):
            if rule.maturity_sensitive:
                w = rule.weight * (1.0 - k * (1.0 - m))     # uses resolved k (was MATURITY_K)
            else:
                w = rule.weight
            triggered.append(rule)
            effective_weights.append(w)
            factors.append(RiskFactor(name=rule.name, description=rule.description, weight=w))

    signal_score = _noisy_or(effective_weights)
    final_score = _noisy_or([account_prior, signal_score])
    decision, classification, risk_level = _decide(final_score, ruleset.thresholds)

    return ScoringResult(
        score=final_score,
        account_prior=account_prior,
        signal_score=signal_score,
        maturity=m,
        decision=decision,
        classification=classification,
        risk_level=risk_level,
        triggered_rules=tuple(r.name for r in triggered),
        risk_factors=tuple(factors),
    )
```

### Important: `app/scoring_constants.py::maturity` stays in place

The module-level `maturity()` helper in `app/scoring_constants.py` is NOT touched — it remains the project-default-only path. The Phase 2A divergences (multiplicative, linear) documented in decisions.md continue to apply. Callers OUTSIDE `score()` (e.g., a hypothetical future analytics path) keep using the pure-default `maturity()`. The scoring path uses `_maturity_with_overrides` because it threads tenant-supplied thresholds.

### Unit tests

`tests/unit/test_scoring_per_tenant_overrides.py` — 12 tests:

1. **Empty tenant_config (all None overrides)**: `score()` produces identical output to pre-4C `score()` for any given input.
2. **maturity_age_days=90 override**: customer with `age_days=60, total_shipments=50` reaches maturity=1.0 with 90 (60/90 capped to 60/90 = 0.67; 50/50 = 1.0 → m=0.67 with override) WHEREAS default (180) → m=0.33. Compare effective_weights of a maturity-sensitive rule.
3. **maturity_shipments=10 override**: customer with `age_days=180, total_shipments=10` reaches maturity=1.0 (vs 0.2 default).
4. **maturity_k=0.10 override**: lower K means less aggressive downweighting for new customers. With m=0.5, weight=1.0 → effective=1*(1-0.10*0.5)=0.95 (vs 0.85 default). Test asserts the resolved K is used.
5. **maturity_k=0.50 override**: more aggressive downweighting.
6. **All three overrides combined**: maturity_age_days=90, maturity_shipments=20, maturity_k=0.20. Compute expected score by hand and assert.
7. **Layer 1 short-circuit unaffected**: BLOCK rule fires; tenant_config overrides are not consulted (the function returns before reaching Layer 2). Assert via `unittest.mock.patch` on `_resolved_maturity_constants` to verify it's NOT called when Layer 1 fires.
8. **Layer 2 base_prior uses resolved m**: with maturity_age_days=90 + customer age_days=30: m≈0.17 (default) vs m≈0.33 (override). base_prior = MAX_NEW_ACCOUNT * (1 - m) differs.
9. **trust_contribution unaffected by maturity overrides**: same trust_score → same trust_contribution.
10. **flag_prior unaffected**: same flagged_count_tier mapping.
11. **maturity_sensitive rule downweight uses resolved k**: rule fires; effective weight computed with override k.
12. **Multiple maturity-sensitive rules**: all use the same resolved k; consistent across the rule set.

**Validation**:
- `pytest tests/unit/test_scoring_per_tenant_overrides.py -v --asyncio-mode=auto` → 12 tests pass.
- `pytest tests/ --asyncio-mode=auto -q` → **expected failures** in existing tests that call `score()` without `tenant_config`. **Declared break** — see below.
- `mypy app/` strict clean.

**Risk**: **High**. The scoring formula is the contract; touching `score()` requires careful review. Risk areas:
- Wrong fallback (`is not None` check missing) — None override silently uses 0 instead of project default.
- Forgot to swap one of the constants in the formula (e.g., used `MATURITY_K` in Layer 3 downweight instead of resolved `k`).
- Layer 1 short-circuit accidentally calls `_resolved_maturity_constants` (wasted work + breaks fast-path latency).

Mitigations: extensive unit tests pinning resolution logic per-override; Layer 1 test 7 pins the short-circuit invariance.

**Reversibility**: Medium. Revert restores the prior signature; downstream commits in 4C must also revert.

**Pre-commit verification**: pre-commit hook may fail on `pytest tests/unit/` because of broken existing-test call sites. **Bypass: `--no-verify` permitted** per declared break.

**Observability**: existing structlog `risk.evaluation` log already carries `maturity` field; with overrides applied, that field now reflects the per-tenant computation. The decisions.md cold-start section (4C.5) calls out the observability change.

**Test changes**: 12 unit tests; existing tests that call `score(...)` without `tenant_config` will fail — fixed in 4C.3 + 4C.4.

**Rollback plan**: `git revert` (must revert subsequent 4C commits if shipped).

**Declared breaks**:
- **Scope**: `score()` signature gains required `tenant_config` parameter. Existing tests in `tests/unit/test_scoring*.py`, `tests/unit/test_layer2*.py`, etc. that invoke `score()` directly will fail.
- **Resolved in**: 4C.3 (endpoint call sites + scoring-test fixtures updated).
- **Pre-commit bypass**: `git commit --no-verify` permitted. **Specifically bypassed**: `pytest tests/unit/ -x` and possibly `mypy app/`. `ruff check` + `ruff format` must still pass.

**Reviewer routing**: **Never-Skip (scoring.py)** → standard panel + test-reviewer.

---

## 4C.2 — Cold-start grace period helper + integration in `score()`

**Theme**: Add the `_apply_cold_start_grace(maturity, tenant_config)` helper. Wire it into `score()` between the maturity computation and Layer 2 base_prior consumption.

**Files**:
- `app/scoring.py` (EDIT — add helper + call in score())
- `tests/unit/test_scoring_cold_start_grace.py` (NEW)

**Specifics**:

```python
from datetime import UTC, datetime

def _apply_cold_start_grace(
    maturity_value: float,
    tenant_config: TenantConfig,
    *,
    now: datetime | None = None,
) -> float:
    """Apply cold-start grace multiplier to maturity if tenant is within grace.

    For `cold_start_grace_days` after `tenant_config.created_at`, multiply
    maturity by 0.5. After the window, return maturity unchanged.

    `cold_start_grace_days == 0` (default) → always returns maturity
    unchanged.

    `now` is injected for test determinism; production passes None and
    uses `datetime.now(UTC)`.

    Grace mechanism rationale: a newly-onboarded tenant has no
    accumulated baselines, so maturity-sensitive rules may fire too
    aggressively on legitimate first customers. The 0.5 multiplier
    softens the maturity-derived account_prior + maturity-downweighted
    Layer 3 weights, biasing toward REVIEW rather than BLOCK during
    the grace window.

    The 0.5 multiplier is hardcoded (not tenant-configurable in Phase 4)
    — Phase 6 staging replay measures FPR impact.

    Phase 4 scope: grace mechanism applies tenant-wide, not per-customer.
    Per-customer cold-start is handled naturally by Layer 2 base_prior
    (decisions.md § Cold start).
    """
    if tenant_config.cold_start_grace_days <= 0:
        return maturity_value
    effective_now = now if now is not None else datetime.now(UTC)
    elapsed_days = (effective_now - tenant_config.created_at).days
    if elapsed_days < tenant_config.cold_start_grace_days:
        return maturity_value * 0.5
    return maturity_value
```

### Wire into `score()`

After the existing `m = _maturity_with_overrides(...)` line in 4C.1:

```python
m = _apply_cold_start_grace(m, tenant_config)
```

(Replaces the marker comment from 4C.1.)

### Unit tests

`tests/unit/test_scoring_cold_start_grace.py` — 8 tests:

1. `cold_start_grace_days=0` → returns maturity unchanged.
2. `cold_start_grace_days=7, tenant created 3 days ago` → returns maturity * 0.5.
3. `cold_start_grace_days=7, tenant created 7 days ago` → returns maturity unchanged (boundary: elapsed >= grace).
4. `cold_start_grace_days=7, tenant created 8 days ago` → returns maturity unchanged.
5. `cold_start_grace_days=30, tenant created 1 day ago` → maturity * 0.5.
6. `maturity=0.0 + grace active` → still 0.0 (0 * 0.5 = 0).
7. `maturity=1.0 + grace active` → 0.5.
8. **Integration with score()**: customer with `age_days=180, total_shipments=50` (m=1.0 default) + tenant `cold_start_grace_days=14, created 5 days ago` → score()'s ScoringResult.maturity reports 0.5; base_prior = MAX_NEW_ACCOUNT * (1 - 0.5) = 0.05 (vs 0.0 without grace).

### Watch-point: composition with maturity-sensitive rules

A maturity=0.5 (under grace) with a maturity-sensitive rule weight=0.6:
- effective_weight = 0.6 * (1 - 0.30 * (1 - 0.5)) = 0.6 * 0.85 = 0.51

vs maturity=1.0 (post-grace) with same rule:
- effective_weight = 0.6 * (1 - 0.30 * 0) = 0.6 * 1.0 = 0.60

vs maturity=0.0 (brand-new customer at non-grace tenant) with same rule:
- effective_weight = 0.6 * (1 - 0.30 * 1.0) = 0.6 * 0.70 = 0.42

So grace creates a behavior path "softer than mature, harder than brand-new". This is intentional per the Phase 4 prompt watch-point. Test 8 documents the composition.

**Validation**:
- `pytest tests/unit/test_scoring_cold_start_grace.py -v --asyncio-mode=auto` → 8 tests pass.
- `mypy app/` strict clean.

**Risk**: **Medium-High**. Composition with maturity-sensitive rules is non-obvious; reviewer must verify formula composition matches decisions.md.

**Reversibility**: Easy — remove the helper call and helper definition.

**Pre-commit verification**: All gates green (assuming 4C.3 has landed; if 4C.2 is committed before 4C.3, declared break from 4C.1 still applies).

**Observability**: Grace-window scoring is reflected in the existing `risk.evaluation` log's `maturity` field — a tenant within grace will show maturity values systematically halved.

**Test changes**: 8 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None (does not change signature beyond 4C.1).

**Reviewer routing**: **Never-Skip (scoring.py)** → standard panel + test-reviewer + security-auditor (timestamp arithmetic + UTC discipline).

---

## 4C.3 — Endpoint call site updates + scoring test fixture updates

**Theme**: Pass `tenant_config` to `score()` from `app/api/booking.py` and `app/api/modification.py`. Update unit tests that call `score()` directly to pass a default TenantConfig. Restores green suite (resolves 4C.1 declared break).

**Files**:
- `app/api/booking.py` (EDIT — pass `tenant_config=tenant_config` to `score()`)
- `app/api/modification.py` (EDIT — pass `tenant_config=tenant_config` to `score()`)
- `tests/unit/test_scoring*.py` and `tests/unit/test_layer2*.py` etc. (EDIT — pass `tenant_config=make_default_tenant_config()` from the helper added in 4A.4)
- `tests/integration/test_*` files that invoke `score()` directly (likely few; most go through the endpoint)

**Specifics**:

### Booking endpoint

```python
result = score(ruleset, context_env, customer_state=customer_state, tenant_config=tenant_config)
```

### Modification endpoint

Same.

### Test fixture updates

Every test calling `score()` directly grows a `tenant_config=make_default_tenant_config()` kwarg. The helper from 4A.4 is reused.

For tests that need to override (e.g., test maturity_age_days=90), construct a TenantConfig with the override field set:

```python
tc = TenantConfig(
    tenant_id=1,
    config_version=0,
    maturity_age_days=90,
    created_at=datetime.now(UTC),
    updated_at=datetime.now(UTC),
)
result = score(ruleset, ctx, customer_state=cs, tenant_config=tc)
```

**Validation**:
- `pytest tests/ --asyncio-mode=auto -q` → full suite green (declared break resolved).
- `pytest tests/integration/test_layer2_integration.py -v --asyncio-mode=auto` — Layer 2 tests with default TenantConfig produce identical results to Phase 2 (regression gate).
- `pytest tests/integration/test_case_1_detection.py tests/integration/test_case_2.py -v --asyncio-mode=auto` — case-1 and case-2 BLOCK assertions hold with default TenantConfig (no behavioral change from 4C with empty config).
- `mypy app/` strict clean.

**Risk**: **High**. Touches both scoring-path endpoints + ~10-20 scoring/Layer 2 test files. Risk: a niche test missed → that test fails post-merge.

**Reviewer attention (operator note, 2026-06-01)**: 4C.3 mirrors 4A.4's wide-fixture-update risk shape. Same discipline applies: reviewer panel **must** enumerate every `score(` call site via `grep -rn 'score(' app/ tests/` (filter to the `app.scoring.score` import, not unrelated identifiers) and verify each carries `tenant_config=...`. The 4C.1 → 4C.3 declared break is correctly identified; 4C.3 resolves it. Cross-check the diff against the call-site inventory before approving.

**Reversibility**: Hard — depends on 4C.1 and 4C.2.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: ~20 fixture updates across scoring/Layer 2 tests; no new tests added.

**Rollback plan**: `git revert`.

**Declared breaks**: None (restores transitional state).

**Reviewer routing**: Never-Skip (auth-handling/transaction-scoped code on hot path) → standard panel + test-reviewer.

---

## 4C.4 — Integration tests: per-tenant maturity overrides + cold-start grace

**Theme**: Integration tests proving the per-tenant override mechanism works end-to-end and the cold-start grace mechanism produces the documented behavioral difference.

**Files**:
- `tests/integration/test_per_tenant_maturity_overrides.py` (NEW)
- `tests/integration/test_cold_start_grace_period.py` (NEW)

**Specifics**:

### `tests/integration/test_per_tenant_maturity_overrides.py` — 8 tests:

1. **maturity_age_days=90 tenant**: 60-day-old customer with 50 shipments produces a higher maturity than under the default 180. Compare two requests under different tenants; ScoringResult.maturity differs.
2. **maturity_shipments=10 tenant**: 10-shipment customer reaches maturity=1.0 (under default 50 would be 0.2).
3. **maturity_k=0.10 tenant**: less aggressive downweight. A maturity-sensitive rule with weight 1.0 and m=0.5 contributes 0.95 under K=0.10 vs 0.85 default.
4. **All three overrides combined**: customer at threshold boundaries produces expected score.
5. **maturity_k=0.50 tenant**: more aggressive downweight; score lower for new-customer rule firings.
6. **Override interaction with maturity-sensitive rule**: pick a maturity-sensitive rule from Phase 2 (e.g., `dormant_new_ip` weight=0.35); confirm override changes its contribution to signal_score noisy-OR.
7. **Cross-tenant independence**: same customer-shape booking under tenant_a (default) and tenant_b (override) produces different ScoringResult.maturity.
8. **No interaction with Layer 1 BLOCK**: tenant_config overrides don't affect Layer 1; a BLOCK rule fires → score=1.0 regardless of overrides.

### `tests/integration/test_cold_start_grace_period.py` — 6 tests:

1. **Grace=0 tenant**: scoring unchanged regardless of `tenants.created_at`.
2. **Grace=7, fresh tenant (created today)**: maturity halved; score reflects the softer firing.
3. **Grace=7, tenant created 8 days ago**: grace expired; scoring at full maturity.
4. **Grace=30, tenant created 14 days ago**: still in grace; maturity halved.
5. **Grace + maturity overrides combined**: tenant with both maturity_age_days=90 AND cold_start_grace_days=14, created 5 days ago. Expected: computed maturity using 90-day threshold, then * 0.5. Test asserts the composed value.
6. **Grace + Layer 1 short-circuit**: grace doesn't suppress BLOCK; a hard-block rule fires regardless.

### Synthetic tenant.created_at

These integration tests need to control `tenants.created_at` precisely. Add a helper to `tests/conftest.py`:

```python
async def seed_tenant_created_days_ago(
    db_conn: asyncpg.Connection,
    *,
    days_ago: int,
    config: dict | None = None,
) -> int:
    """Insert a tenant whose created_at is exactly `days_ago` days ago.
    Used by cold-start grace integration tests."""
    tenant_id = await db_conn.fetchval(
        """
        INSERT INTO tenants (name, config, created_at, updated_at)
        VALUES ($1, $2::jsonb, now() - make_interval(days => $3), now() - make_interval(days => $3))
        RETURNING id
        """,
        f"test-tenant-{secrets.token_hex(4)}",
        json.dumps(config or {}),
        days_ago,
    )
    return tenant_id
```

Each cold-start test uses this helper + the existing api_token seeding pattern.

**Validation**:
- `pytest tests/integration/test_per_tenant_maturity_overrides.py tests/integration/test_cold_start_grace_period.py -v --asyncio-mode=auto` → 14 tests pass.
- `pytest tests/ --asyncio-mode=auto -q` → full suite green.

**Risk**: **Medium**.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 14 integration tests + 1 conftest helper.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: test-only — test-reviewer + senior-engineer + code-flow.

---

## 4C.5 — `.ai/decisions.md` update — cold-start subsection

**Theme**: Document the per-tenant maturity overrides + cold-start grace mechanism in `.ai/decisions.md`.

**Files**:
- `.ai/decisions.md` (EDIT — extend § Cold start section)

**Specifics**:

Append to the existing `## Cold start` section (line ~286-292):

```markdown
### Per-tenant maturity overrides (Phase 4C — 2026-06-01)

`app/scoring.py::score` consults `tenant_config` for the three Layer 2
+ Layer 3 maturity constants:

| Constant | Override field | Project default |
|---|---|---|
| `maturity_age_days` | `tenant_config.maturity_age_days` | 180 (`MATURITY_AGE_DAYS`) |
| `maturity_shipments` | `tenant_config.maturity_shipments` | 50 (`MATURITY_SHIPMENTS`) |
| `maturity_k` | `tenant_config.maturity_k` | 0.30 (`MATURITY_K`) |

`None` on a TenantConfig override means "use project default from
`app/scoring_constants.py`". The constants module REMAINS source of
truth; TenantConfig is overrides on top.

The Phase 2A scoring formula is unchanged (multiplicative maturity,
linear shipments fraction, 4-tier flag prior, no customer-inheritance).
Only the thresholds consulted change.

### Cold-start grace period (Phase 4C — 2026-06-01)

`tenant_config.cold_start_grace_days` (default 0; disabled) — during
the grace window after tenant onboarding (measured from
`tenants.created_at`), the maturity formula multiplies its computed
value by 0.5. After the window, no multiplier.

Rationale: a newly-onboarded tenant has no accumulated baselines,
so maturity-sensitive rules may fire too aggressively on legitimate
first customers. The 0.5 multiplier softens scoring during the grace
window, biasing toward REVIEW rather than BLOCK while the tenant
builds baselines.

The 0.5 multiplier is hardcoded — not tenant-configurable in Phase 4.
Phase 6 staging replay measures FPR impact and may revise.

Per-customer cold-start (a customer is new to a mature tenant) is
NOT affected by this mechanism — that's handled by Layer 2 base_prior
already. `cold_start_grace_days` is tenant-wide.

### Composition

Grace * maturity composition with a maturity-sensitive rule (weight 0.6):

| Maturity state | m | K=0.30 effective weight |
|---|---|---|
| Mature (post-grace, ≥180 days, ≥50 shipments) | 1.0 | 0.60 |
| Grace-active, mature customer (m_raw=1.0) | 0.5 | 0.51 |
| Brand-new at default tenant (m_raw=0.0) | 0.0 | 0.42 |

Grace creates an intermediate behavior path "softer than mature, harder
than brand-new" — intentional. Phase 4C integration tests pin the
formula behavior.
```

**Validation**:
- Doc-reviewer reads the section.

**Risk**: **Low**.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green (doc-only).

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: doc-only → doc-reviewer only.

---

## Batch 4C summary table

| Commit | Theme | Files | Tests added | Risk | Reviewer panel |
|---|---|---|---|---|---|
| 4C.1 | Per-tenant maturity constant consultation in score() | `app/scoring.py` (EDIT), 1 new test | 12 | High (declared break) | Never-Skip (scoring.py) + test-reviewer |
| 4C.2 | Cold-start grace helper + score() wiring | `app/scoring.py` (EDIT), 1 new test | 8 | Medium-High | Never-Skip + test-reviewer + security-auditor |
| 4C.3 | Endpoint + test fixture updates | `app/api/booking.py`, `app/api/modification.py`, multiple test files | 0 | High | Never-Skip + test-reviewer |
| 4C.4 | Integration tests (overrides + grace) | 2 new tests + conftest helper | 14 | Medium | test-reviewer + senior + code-flow |
| 4C.5 | `.ai/decisions.md` cold-start subsection | `.ai/decisions.md` (EDIT) | 0 | Low | doc-reviewer only |
| **Total** | | | **34 new tests** | | |

Expected test count at end of Batch 4C: **~781 (post-4B) + 34 = ~815 tests**.

Migrations count at end of Batch 4C: **5** (unchanged).

ALLOWED_CONTEXT_FIELDS at end of Batch 4C: **71** (unchanged — 4C does NOT add Context fields).

`app/scoring.py` LOC delta: ~30-40 lines added (2 helpers + score() signature/body extension).

`app/scoring_constants.py`: **UNCHANGED** (no new constants; project defaults stable).

Rule count: **79** (unchanged).
