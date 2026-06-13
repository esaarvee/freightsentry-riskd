"""3-layer noisy-OR scorer — Layer 1 + Layer 2 + Layer 3.

Layer 1 (hard-block short-circuit): the first rule with `action: BLOCK`
that fires returns immediately with `score = 1.0, decision = BLOCK`.
File-order in `app/rules.yaml` determines precedence. Layer 1 BYPASSES
Layer 2 — hard-blocks never compose with the account prior.

Layer 2 (account prior + trust contribution + flag prior): for each
non-blocked evaluation, compute `account_prior` from customer state.

```
maturity           = clamp(age / MATURITY_AGE_DAYS) * clamp(shipments / MATURITY_SHIPMENTS)
base_prior         = MAX_NEW_ACCOUNT * (1 - maturity)
trust_risk         = max(0, (0.5 - trust_score) / 0.5)
trust_contribution = trust_risk * TRUST_FACTOR
flag_prior         = FLAG_WEIGHTS[flagged_count_tier(flagged_count)]
account_prior      = noisyOR(base_prior, trust_contribution, flag_prior)
```

Layer 3 (signal noisy-OR): for each fired non-BLOCK rule, compute
`effective_weight = weight * (1 - MATURITY_K * (1 - maturity))` when
the rule carries `maturity_sensitive: true`, else `effective_weight =
weight`. `signal_score = noisyOR(effective_weights)`.

Final score: `score = noisyOR(account_prior, signal_score)`.

Score-to-decision banding from `app/rules.yaml` thresholds
(0.60 / 0.80 per `.ai/decisions.md`). See the Layer 2 amendment in
`.ai/decisions.md` for documented divergences from FreightSentry's
`scorer.go:300-415`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from app.rules import Rule, RuleSet, Thresholds
from app.scoring_constants import (
    FLAG_WEIGHTS,
    MATURITY_AGE_DAYS,
    MATURITY_K,
    MATURITY_SHIPMENTS,
    MAX_NEW_ACCOUNT,
    TRUST_FACTOR,
    flagged_count_tier,
)
from app.tenant_config import TenantConfig


def _resolved_maturity_constants(tenant_config: TenantConfig) -> tuple[int, int, float]:
    """Return (age_days_threshold, shipments_threshold, k) with overrides applied.

    None on a TenantConfig override means "use project default from
    app/scoring_constants.py" — the constants module REMAINS the source of
    truth for defaults; TenantConfig overrides on top.
    """
    age = (
        tenant_config.maturity_age_days
        if tenant_config.maturity_age_days is not None
        else MATURITY_AGE_DAYS
    )
    ship = (
        tenant_config.maturity_shipments
        if tenant_config.maturity_shipments is not None
        else MATURITY_SHIPMENTS
    )
    k = tenant_config.maturity_k if tenant_config.maturity_k is not None else MATURITY_K
    return age, ship, k


def _maturity_with_overrides(
    *,
    age_days: int,
    total_shipments: int,
    age_threshold: int,
    ship_threshold: int,
) -> float:
    """Maturity formula consulting tenant-supplied thresholds.

    Mirrors app/scoring_constants.py::maturity but with caller-supplied
    thresholds instead of module-level constants. Multiplicative
    age_frac * ship_frac (per decisions.md Layer 2 amendment).
    """
    age_frac = min(max(age_days, 0) / age_threshold, 1.0)
    ship_frac = min(max(total_shipments, 0) / ship_threshold, 1.0)
    return age_frac * ship_frac


_COLD_START_GRACE_MULTIPLIER: float = 0.5


def _apply_cold_start_grace(
    maturity_value: float,
    tenant_config: TenantConfig,
    *,
    now: datetime | None = None,
) -> float:
    """Apply cold-start grace multiplier to maturity if tenant is within grace.

    For `cold_start_grace_days` after `tenant_config.created_at`, multiply
    maturity by 0.5. After the window, return maturity unchanged.

    `cold_start_grace_days == 0` (default) -> always returns maturity
    unchanged.

    `now` is injected for test determinism; production passes None and
    uses `datetime.now(UTC)`.

    Rationale: a newly-onboarded tenant has no accumulated baselines, so
    maturity-sensitive rules may fire too aggressively on legitimate
    first customers. The 0.5 multiplier softens scoring during the grace
    window, biasing toward REVIEW rather than BLOCK.

    The 0.5 multiplier is hardcoded; staging replay may revise it after
    measuring FPR impact.

    Per-customer cold-start is handled separately by Layer 2 base_prior;
    `cold_start_grace_days` is tenant-wide.
    """
    if tenant_config.cold_start_grace_days <= 0:
        return maturity_value
    effective_now = now if now is not None else datetime.now(UTC)
    elapsed_days = (effective_now - tenant_config.created_at).days
    if elapsed_days < tenant_config.cold_start_grace_days:
        return maturity_value * _COLD_START_GRACE_MULTIPLIER
    return maturity_value


@dataclass(frozen=True)
class CustomerState:
    """Subset of Context needed for Layer 2 + maturity downweight on Layer 3.

    Passed explicitly to `score()` rather than re-derived from the DSL
    Context so the scoring path has typed access without the
    `Mapping[str, Any]` shape. Callers (booking endpoint, integration
    tests) build this from the same fields they put into ctx.

    Carries no PII — all integers + one float.
    """

    trust_score: float
    account_age_days: int
    total_shipments: int
    flagged_count: int


@dataclass(frozen=True)
class RiskFactor:
    name: str
    description: str
    weight: float


@dataclass(frozen=True)
class ScoringResult:
    score: float
    account_prior: float
    signal_score: float
    maturity: float
    decision: Literal["ALLOW", "REVIEW", "BLOCK"]
    classification: Literal["GREEN", "YELLOW", "RED"]
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    triggered_rules: tuple[str, ...]
    risk_factors: tuple[RiskFactor, ...]


def score(
    ruleset: RuleSet,
    ctx: Mapping[str, Any],
    *,
    customer_state: CustomerState,
    tenant_config: TenantConfig,
) -> ScoringResult:
    # Layer 1 — hard-block short-circuit. Bypasses Layer 2 entirely.
    # tenant_config is NOT consulted on the BLOCK fast-path — see
    # test_layer_1_short_circuit_does_not_consult_tenant_config.
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
    m = _apply_cold_start_grace(m, tenant_config)
    base_prior = MAX_NEW_ACCOUNT * (1.0 - m)
    trust_risk = max(0.0, (0.5 - customer_state.trust_score) / 0.5)
    trust_contribution = trust_risk * TRUST_FACTOR
    flag_prior = FLAG_WEIGHTS[flagged_count_tier(customer_state.flagged_count)]
    account_prior = _noisy_or([base_prior, trust_contribution, flag_prior])

    # Layer 3 — signal noisy-OR with maturity downweight (k resolved per-tenant).
    triggered: list[Rule] = []
    effective_weights: list[float] = []
    factors: list[RiskFactor] = []
    for rule in ruleset.rules:
        if rule.action == "BLOCK":
            continue
        if rule.evaluate(ctx):
            w = rule.weight * (1.0 - k * (1.0 - m)) if rule.maturity_sensitive else rule.weight
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


def _to_factor(rule: Rule) -> RiskFactor:
    return RiskFactor(name=rule.name, description=rule.description, weight=rule.weight)


def _noisy_or(weights: list[float]) -> float:
    """Noisy-OR: `1 - prod(1 - w_i)` for `w_i ∈ [0, 1]`. Returns 0 when
    no inputs (no rules fired)."""
    if not weights:
        return 0.0
    prod = 1.0
    for w in weights:
        prod *= 1.0 - w
    return 1.0 - prod


def _decide(
    score_value: float, thresholds: Thresholds
) -> tuple[
    Literal["ALLOW", "REVIEW", "BLOCK"],
    Literal["GREEN", "YELLOW", "RED"],
    Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"],
]:
    if score_value >= thresholds.block_min:
        return "BLOCK", "RED", "CRITICAL"
    if score_value <= thresholds.allow_max:
        risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = (
            "LOW" if score_value < 0.30 else "MEDIUM"
        )
        return "ALLOW", "GREEN", risk_level
    # REVIEW band: strictly `(allow_max, block_min)`. All scores in this
    # band are HIGH risk.
    return "REVIEW", "YELLOW", "HIGH"
