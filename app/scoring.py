"""3-layer noisy-OR scorer — Phase 1 ships Layer 1 + Layer 3.

Layer 1 (hard-block short-circuit): the first rule with `action: BLOCK`
that fires returns immediately with `score = 1.0, decision = BLOCK`.
File-order in app/rules.yaml determines precedence.

Layer 2 (account prior + trust contribution + flag prior) — lands
Phase 2 alongside trust-score consumption.

Layer 3 (signal noisy-OR): collect every fired non-BLOCK rule's weight
and compose via `1 - prod(1 - w_i)`. Maturity downweighting on rules
marked `maturity_sensitive: true` lands Phase 2 (depends on customer
maturity arithmetic which depends on Layer 2's `effective_observations`
read).

Score → decision banding from `app/rules.yaml` thresholds (0.60 / 0.80
per `.ai/decisions.md`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.rules import Rule, RuleSet, Thresholds


@dataclass(frozen=True)
class RiskFactor:
    name: str
    description: str
    weight: float


@dataclass(frozen=True)
class ScoringResult:
    score: float
    decision: Literal["ALLOW", "REVIEW", "BLOCK"]
    classification: Literal["GREEN", "YELLOW", "RED"]
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    triggered_rules: tuple[str, ...]
    risk_factors: tuple[RiskFactor, ...]


def score(ruleset: RuleSet, ctx: Mapping[str, Any]) -> ScoringResult:
    # Layer 1 — hard-block short-circuit.
    for rule in ruleset.rules:
        if rule.action != "BLOCK":
            continue
        if rule.evaluate(ctx):
            return ScoringResult(
                score=1.0,
                decision="BLOCK",
                classification="RED",
                risk_level="CRITICAL",
                triggered_rules=(rule.name,),
                risk_factors=(_to_factor(rule),),
            )

    # Layer 3 — signal noisy-OR.
    triggered: list[Rule] = []
    weights: list[float] = []
    for rule in ruleset.rules:
        if rule.action == "BLOCK":
            continue
        if rule.evaluate(ctx):
            triggered.append(rule)
            weights.append(rule.weight)

    signal_score = _noisy_or(weights)
    decision, classification, risk_level = _decide(signal_score, ruleset.thresholds)

    return ScoringResult(
        score=signal_score,
        decision=decision,
        classification=classification,
        risk_level=risk_level,
        triggered_rules=tuple(r.name for r in triggered),
        risk_factors=tuple(_to_factor(r) for r in triggered),
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
    # REVIEW band: (allow_max, block_min)
    review_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = (
        "MEDIUM" if score_value < 0.60 else "HIGH"
    )
    return "REVIEW", "YELLOW", review_level
