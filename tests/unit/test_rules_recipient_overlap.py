"""Unit tests for the recipient-overlap rules.

Two rules that detect destination addresses receiving shipments from
many customers within the same tenant — a fraud-ring drop-point pattern.
The two rules are tier-DISJOINT: the lower-weight rule covers 4-10
distinct customers; the higher-weight covers >10. The upper bound on
the lower rule (<= 10) prevents both firing simultaneously, which
freight_risk's catalogue source did not intend.

Cross-tenant isolation (the SQL boundary that backs
recipient_cross_customer_count) is verified at the velocity-helper
level (tests/integration/test_velocity.py) and at the Context-wiring
level (tests/integration/test_context.py). These rule tests check
the threshold semantics on the already-tenant-scoped int.
"""

from __future__ import annotations

from app.rules import RuleSet
from tests.unit.conftest import base_ctx, find_rule

# ---------------------------------------------------------------------------
# recipient_used_by_many_customers — 3 < count <= 10 AND observations >= 10
# ---------------------------------------------------------------------------


def test_many_customers_in_4_to_10_range_fires(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "recipient_used_by_many_customers")
    ctx = base_ctx()
    ctx["customer_observations"] = 15.0
    ctx["recipient_cross_customer_count"] = 5
    assert rule.evaluate(ctx) is True
    ctx["recipient_cross_customer_count"] = 7
    assert rule.evaluate(ctx) is True
    ctx["recipient_cross_customer_count"] = 10
    assert rule.evaluate(ctx) is True


def test_many_customers_below_lower_bound_does_not_fire(ruleset: RuleSet) -> None:
    """Strict > 3 — exactly 3 must NOT fire."""
    rule = find_rule(ruleset, "recipient_used_by_many_customers")
    ctx = base_ctx()
    ctx["customer_observations"] = 15.0
    ctx["recipient_cross_customer_count"] = 3
    assert rule.evaluate(ctx) is False


def test_many_customers_above_upper_bound_does_not_fire(ruleset: RuleSet) -> None:
    """The <= 10 upper bound prevents this rule from firing on counts
    that the higher-stakes _very_many_customers rule covers — tier
    disjointness."""
    rule = find_rule(ruleset, "recipient_used_by_many_customers")
    ctx = base_ctx()
    ctx["customer_observations"] = 15.0
    ctx["recipient_cross_customer_count"] = 11
    assert rule.evaluate(ctx) is False


def test_many_customers_requires_observations(ruleset: RuleSet) -> None:
    """customer_observations >= 10 — strict <= 9 must NOT fire even at
    the in-range count."""
    rule = find_rule(ruleset, "recipient_used_by_many_customers")
    ctx = base_ctx()
    ctx["recipient_cross_customer_count"] = 5
    ctx["customer_observations"] = 9.0
    assert rule.evaluate(ctx) is False
    ctx["customer_observations"] = 10.0
    assert rule.evaluate(ctx) is True


# ---------------------------------------------------------------------------
# recipient_used_by_very_many_customers — count > 10 (no observations gate)
# ---------------------------------------------------------------------------


def test_very_many_customers_above_ten_fires(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "recipient_used_by_very_many_customers")
    ctx = base_ctx()
    ctx["recipient_cross_customer_count"] = 11
    assert rule.evaluate(ctx) is True


def test_very_many_customers_at_ten_does_not_fire(ruleset: RuleSet) -> None:
    """Strict > 10 — exactly 10 must NOT fire (the lower-weight rule
    covers 10)."""
    rule = find_rule(ruleset, "recipient_used_by_very_many_customers")
    ctx = base_ctx()
    ctx["recipient_cross_customer_count"] = 10
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# Tier disjointness: at no count do both rules fire simultaneously
# ---------------------------------------------------------------------------


def test_recipient_overlap_rules_are_tier_disjoint(ruleset: RuleSet) -> None:
    """For every possible count in [0, 20], at most one of the two
    rules can fire. This is the load-bearing invariant that prevents
    noisy-OR double-counting freight_risk's catalogue didn't intend."""
    many = find_rule(ruleset, "recipient_used_by_many_customers")
    very_many = find_rule(ruleset, "recipient_used_by_very_many_customers")
    ctx = base_ctx()
    ctx["customer_observations"] = 15.0
    for count in range(0, 21):
        ctx["recipient_cross_customer_count"] = count
        fires_many = many.evaluate(ctx)
        fires_very_many = very_many.evaluate(ctx)
        assert not (fires_many and fires_very_many), (
            f"both recipient-overlap rules fired at count={count} — "
            "the <= 10 upper bound on _many_customers is broken"
        )


def test_recipient_overlap_rules_load(ruleset: RuleSet) -> None:
    expected = {
        "recipient_used_by_many_customers",
        "recipient_used_by_very_many_customers",
    }
    actual = {r.name for r in ruleset.rules}
    missing = expected - actual
    assert not missing, f"missing recipient-overlap rules: {missing}"
