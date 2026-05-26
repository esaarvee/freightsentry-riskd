"""3-layer noisy-OR scorer — Phase 2 ships Layer 1 + Layer 2 + Layer 3.

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
from typing import Any, Literal

from app.rules import Rule, RuleSet, Thresholds
from app.scoring_constants import (
    FLAG_WEIGHTS,
    MATURITY_K,
    MAX_NEW_ACCOUNT,
    TRUST_FACTOR,
    flagged_count_tier,
    maturity,
)


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
) -> ScoringResult:
    # Layer 1 — hard-block short-circuit. Bypasses Layer 2 entirely.
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

    # Layer 2 — account prior.
    m = maturity(customer_state.account_age_days, customer_state.total_shipments)
    base_prior = MAX_NEW_ACCOUNT * (1.0 - m)
    trust_risk = max(0.0, (0.5 - customer_state.trust_score) / 0.5)
    trust_contribution = trust_risk * TRUST_FACTOR
    flag_prior = FLAG_WEIGHTS[flagged_count_tier(customer_state.flagged_count)]
    account_prior = _noisy_or([base_prior, trust_contribution, flag_prior])

    # Layer 3 — signal noisy-OR with maturity downweight.
    triggered: list[Rule] = []
    effective_weights: list[float] = []
    factors: list[RiskFactor] = []
    for rule in ruleset.rules:
        if rule.action == "BLOCK":
            continue
        if rule.evaluate(ctx):
            if rule.maturity_sensitive:
                w = rule.weight * (1.0 - MATURITY_K * (1.0 - m))
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
