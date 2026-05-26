"""Unit tests for app/scoring.py — Layer 1 + Layer 3 noisy-OR."""

import pytest

from app.rules import Rule, RuleSet, Thresholds
from app.scoring import _noisy_or, score


def _rule(
    name: str,
    condition: bool,
    *,
    weight: float,
    action: str = "",
) -> Rule:
    """Build a Rule whose evaluator returns the given bool unconditionally."""
    return Rule(
        name=name,
        description=f"test rule {name}",
        condition=f"<test fixture: {name}>",
        weight=weight,
        action=action,  # type: ignore[arg-type]
        evaluator=lambda _ctx: condition,
    )


def _ruleset(rules: list[Rule]) -> RuleSet:
    return RuleSet(rules=tuple(rules), thresholds=Thresholds())


# ---------------------------------------------------------------------------
# Noisy-OR math
# ---------------------------------------------------------------------------


def test_noisy_or_empty_returns_zero() -> None:
    assert _noisy_or([]) == 0.0


def test_noisy_or_single_weight_passthrough() -> None:
    assert _noisy_or([0.3]) == pytest.approx(0.3)


def test_noisy_or_two_weights() -> None:
    """1 - (1-0.3)(1-0.4) = 1 - 0.42 = 0.58."""
    assert _noisy_or([0.3, 0.4]) == pytest.approx(0.58)


def test_noisy_or_saturates_at_one() -> None:
    """Weight of 1.0 in the mix → result is exactly 1.0 regardless of others."""
    assert _noisy_or([0.3, 1.0, 0.5]) == pytest.approx(1.0)


def test_noisy_or_commutative() -> None:
    assert _noisy_or([0.2, 0.5, 0.3]) == pytest.approx(_noisy_or([0.5, 0.3, 0.2]))


# ---------------------------------------------------------------------------
# Layer 1 — hard-block short-circuit
# ---------------------------------------------------------------------------


def test_no_rules_fire_returns_allow_zero() -> None:
    rs = _ruleset([_rule("r1", False, weight=0.5)])
    result = score(rs, {})
    assert result.score == 0.0
    assert result.decision == "ALLOW"
    assert result.classification == "GREEN"
    assert result.risk_level == "LOW"
    assert result.triggered_rules == ()


def test_block_rule_short_circuits_with_score_one() -> None:
    rs = _ruleset([
        _rule("blocked", True, weight=1.0, action="BLOCK"),
        _rule("would_fire", True, weight=0.5),  # should NOT contribute
    ])
    result = score(rs, {})
    assert result.score == 1.0
    assert result.decision == "BLOCK"
    assert result.classification == "RED"
    assert result.risk_level == "CRITICAL"
    assert result.triggered_rules == ("blocked",)


def test_block_rule_not_firing_falls_through_to_layer3() -> None:
    rs = _ruleset([
        _rule("blocked", False, weight=1.0, action="BLOCK"),
        _rule("r1", True, weight=0.5),
    ])
    result = score(rs, {})
    assert result.decision == "ALLOW"  # 0.5 ≤ 0.60 → ALLOW
    assert result.triggered_rules == ("r1",)


def test_first_block_rule_wins_file_order() -> None:
    """Multiple BLOCK rules firing: first one in the list wins; later
    BLOCK rules are NOT collected as risk_factors."""
    rs = _ruleset([
        _rule("block_a", True, weight=1.0, action="BLOCK"),
        _rule("block_b", True, weight=1.0, action="BLOCK"),
    ])
    result = score(rs, {})
    assert result.triggered_rules == ("block_a",)


# ---------------------------------------------------------------------------
# Layer 3 — signal noisy-OR + decision banding
# ---------------------------------------------------------------------------


def test_single_low_weight_rule_stays_allow() -> None:
    rs = _ruleset([_rule("r1", True, weight=0.30)])
    result = score(rs, {})
    assert result.score == pytest.approx(0.30)
    assert result.decision == "ALLOW"
    assert result.risk_level == "MEDIUM"  # 0.30 ≤ score < 0.60


def test_two_rules_below_threshold_compose_under_block_min() -> None:
    rs = _ruleset([
        _rule("r1", True, weight=0.4),
        _rule("r2", True, weight=0.4),
    ])
    # 1 - 0.6*0.6 = 0.64 → REVIEW band
    result = score(rs, {})
    assert result.score == pytest.approx(0.64)
    assert result.decision == "REVIEW"
    assert result.classification == "YELLOW"


def test_three_rules_compose_to_block() -> None:
    rs = _ruleset([
        _rule("r1", True, weight=0.5),
        _rule("r2", True, weight=0.5),
        _rule("r3", True, weight=0.5),
    ])
    # 1 - 0.5^3 = 0.875 → BLOCK (>= 0.80)
    result = score(rs, {})
    assert result.score == pytest.approx(0.875)
    assert result.decision == "BLOCK"
    assert result.classification == "RED"


def test_score_at_allow_max_boundary_is_allow() -> None:
    """`score <= allow_max` (0.60) → ALLOW (per .ai/rules.md)."""
    rs = _ruleset([_rule("r1", True, weight=0.60)])
    result = score(rs, {})
    assert result.score == pytest.approx(0.60)
    assert result.decision == "ALLOW"


def test_score_at_block_min_boundary_is_block() -> None:
    """`score >= block_min` (0.80) → BLOCK."""
    rs = _ruleset([_rule("r1", True, weight=0.80)])
    result = score(rs, {})
    assert result.score == pytest.approx(0.80)
    assert result.decision == "BLOCK"


def test_triggered_rules_preserves_file_order() -> None:
    rs = _ruleset([
        _rule("r1", True, weight=0.2),
        _rule("r2", False, weight=0.5),  # skipped
        _rule("r3", True, weight=0.1),
    ])
    result = score(rs, {})
    assert result.triggered_rules == ("r1", "r3")


def test_risk_factors_carry_metadata() -> None:
    rs = _ruleset([_rule("r1", True, weight=0.3)])
    result = score(rs, {})
    assert result.risk_factors[0].name == "r1"
    assert result.risk_factors[0].weight == 0.3
    assert "r1" in result.risk_factors[0].description
