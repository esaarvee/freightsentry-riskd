"""Unit tests for the Phase 2C.1 trust-conditioned rule additions.

Each test loads the production `app/rules.yaml` via `app.rules.load_rules`,
finds the rule by name, and exercises its evaluator with a controlled
ctx dict. This proves the rule lives in YAML, parses through the DSL,
and fires exactly at the documented threshold (boundary-side wins).

Shared helpers (`ruleset` fixture, `base_ctx`, `find_rule`) live in
tests/unit/conftest.py — they're consumed by every Phase 2C rule-test
module.
"""

from __future__ import annotations

from app.rules import RuleSet
from tests.unit.conftest import base_ctx, find_rule

# ---------------------------------------------------------------------------
# very_low_trust — trust_score < 0.2 (strict)
# ---------------------------------------------------------------------------


def test_very_low_trust_fires_below_threshold(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "very_low_trust")
    ctx = base_ctx()
    ctx["trust_score"] = 0.15
    assert rule.evaluate(ctx) is True


def test_very_low_trust_does_not_fire_at_threshold(ruleset: RuleSet) -> None:
    """Strict <: trust_score == 0.2 must NOT fire."""
    rule = find_rule(ruleset, "very_low_trust")
    ctx = base_ctx()
    ctx["trust_score"] = 0.2
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# low_trust_high_value — trust_score < 0.3 AND shipment_value > 1000
# ---------------------------------------------------------------------------


def test_low_trust_high_value_requires_both(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "low_trust_high_value")
    ctx = base_ctx()
    ctx["trust_score"] = 0.25
    ctx["shipment_value"] = 500.0
    assert rule.evaluate(ctx) is False
    ctx["shipment_value"] = 1001.0
    assert rule.evaluate(ctx) is True
    # Strict-boundary catches: shipment_value must be > 1000 (not >=)
    ctx["shipment_value"] = 1000.0
    assert rule.evaluate(ctx) is False
    # Trust must be < 0.3 (not <=); restore value side as firing
    ctx["shipment_value"] = 1001.0
    ctx["trust_score"] = 0.3
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# low_trust_vpn — trust_score < 0.3 AND is_vpn
# ---------------------------------------------------------------------------


def test_low_trust_vpn_compound(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "low_trust_vpn")
    ctx = base_ctx()
    ctx["trust_score"] = 0.25
    ctx["is_vpn"] = False
    assert rule.evaluate(ctx) is False
    ctx["is_vpn"] = True
    assert rule.evaluate(ctx) is True
    ctx["trust_score"] = 0.31
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# very_low_trust_velocity — trust_score < 0.2 AND velocity_user_hourly > 3
# ---------------------------------------------------------------------------


def test_very_low_trust_velocity_compound(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "very_low_trust_velocity")
    ctx = base_ctx()
    ctx["trust_score"] = 0.1
    ctx["velocity_user_hourly"] = 4
    assert rule.evaluate(ctx) is True
    ctx["velocity_user_hourly"] = 3
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# threat_score_moderate — ip_threat_score > 0.5
# ---------------------------------------------------------------------------


def test_threat_score_moderate_above_half(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "threat_score_moderate")
    ctx = base_ctx()
    ctx["ip_threat_score"] = 0.5
    assert rule.evaluate(ctx) is False
    ctx["ip_threat_score"] = 0.51
    assert rule.evaluate(ctx) is True


# ---------------------------------------------------------------------------
# flags_with_value — flagged_count > 3 AND shipment_value > 2000
# ---------------------------------------------------------------------------


def test_flags_with_value_requires_both_conditions(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "flags_with_value")
    ctx = base_ctx()
    ctx["flagged_count"] = 4
    ctx["shipment_value"] = 2001.0
    assert rule.evaluate(ctx) is True
    # Strict > 3 on count
    ctx["flagged_count"] = 3
    assert rule.evaluate(ctx) is False
    # Strict > 2000 on value (catches a >= typo)
    ctx["flagged_count"] = 4
    ctx["shipment_value"] = 2000.0
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# vpn_known_user — is_vpn AND NOT is_new_user
# ---------------------------------------------------------------------------


def test_vpn_known_user_excludes_new(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "vpn_known_user")
    ctx = base_ctx()
    ctx["is_vpn"] = True
    ctx["is_new_user"] = False
    assert rule.evaluate(ctx) is True
    ctx["is_new_user"] = True
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# Rule-loader sanity: trust-conditioned set is integrated
# ---------------------------------------------------------------------------


def test_all_trust_conditioned_rules_load(ruleset: RuleSet) -> None:
    """All 7 rules added in 2C.1 must be present after rule-loader runs."""
    expected = {
        "very_low_trust",
        "low_trust_high_value",
        "low_trust_vpn",
        "very_low_trust_velocity",
        "threat_score_moderate",
        "flags_with_value",
        "vpn_known_user",
    }
    actual = {r.name for r in ruleset.rules}
    missing = expected - actual
    assert not missing, f"missing trust-conditioned rules: {missing}"


def test_total_rule_count_after_2c1(ruleset: RuleSet) -> None:
    """Phase 1 baseline = 14; 2C.1 adds 7 → 21."""
    assert len(ruleset.rules) == 21
