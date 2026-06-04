"""Unit tests for Phase 7C.2 cold_start_outbound_carrier_dropoff rule.

Truth-table coverage for the case-3b asymmetric compound (Roulottes
Lupien attack shape):

    customer_destination_country_mismatch_outbound
    AND origin_via_carrier_dropoff
    AND customer_observations < 10

Plus rule-shape sanity (weight, maturity_sensitive, action) and
null/empty registered-country regression (the derivation's defensive
falsy check is exercised here at the rule level).
"""

from __future__ import annotations

import pytest

from app.rules import RuleSet
from tests.unit.conftest import base_ctx, find_rule


@pytest.fixture
def rule(ruleset: RuleSet):
    return find_rule(ruleset, "cold_start_outbound_carrier_dropoff")


def _firing_ctx() -> dict:
    """Base context with all three conjuncts true (brand-new customer
    shipping outside their declared country with carrier dropoff)."""
    ctx = base_ctx()
    ctx["customer_destination_country_mismatch_outbound"] = True
    ctx["origin_via_carrier_dropoff"] = True
    ctx["customer_observations"] = 3.0
    return ctx


def test_all_three_signals_true_fires(rule) -> None:
    assert rule.evaluate(_firing_ctx()) is True


def test_mismatch_false_does_not_fire(rule) -> None:
    """When derivation returns False (e.g. null registered_country,
    or customer shipping domestically), the rule does not fire."""
    ctx = _firing_ctx()
    ctx["customer_destination_country_mismatch_outbound"] = False
    assert rule.evaluate(ctx) is False


def test_dropoff_false_does_not_fire(rule) -> None:
    ctx = _firing_ctx()
    ctx["origin_via_carrier_dropoff"] = False
    assert rule.evaluate(ctx) is False


def test_established_customer_does_not_fire(rule) -> None:
    """Cold-start gate is < 10; established customers (>= 10) miss
    this rule. Targets brand-new-customer fraud only."""
    ctx = _firing_ctx()
    ctx["customer_observations"] = 15.0
    assert rule.evaluate(ctx) is False


def test_cold_start_boundary_at_nine_fires(rule) -> None:
    """Strict-less-than boundary: 9 fires; 10 does not."""
    ctx = _firing_ctx()
    ctx["customer_observations"] = 9.0
    assert rule.evaluate(ctx) is True


def test_cold_start_boundary_at_ten_does_not_fire(rule) -> None:
    ctx = _firing_ctx()
    ctx["customer_observations"] = 10.0
    assert rule.evaluate(ctx) is False


def test_all_signals_false_does_not_fire(rule) -> None:
    """base_ctx defaults preserve pre-Phase-7C behavior (no fire)."""
    assert rule.evaluate(base_ctx()) is False


def test_weight_is_zero_point_six_five(rule) -> None:
    """Weight pin: 0.65 sits just below BLOCK standalone (REVIEW band).
    Composes with IP-quality / value-tier rules to reach BLOCK."""
    assert rule.weight == 0.65


def test_maturity_sensitive_is_false(rule) -> None:
    """Maturity-insensitive — the cold-start gate (< 10) is in the
    condition itself; downweighting via maturity_sensitive would
    suppress the very signal we're using to flag the threat."""
    assert rule.maturity_sensitive is False


def test_action_is_not_block(rule) -> None:
    """Contributes to noisy-OR; does not hard-block."""
    assert rule.action == ""


# Regression: the rule MUST NOT fire when registered_country is None
# (tier-4 fallback in the freight_risk export). We exercise the
# DERIVATION directly to compute the mismatch field, then evaluate the
# rule against that derived ctx — pinning the helper-to-rule path that
# would otherwise be coupled only through the conftest default.


def test_does_not_fire_on_null_registered_country(rule) -> None:
    """When registered_country is None, the helper returns False,
    which AND-chains the rule's first conjunct to False, so the rule
    does not fire even when the other two conjuncts are true. We
    compute the derived field via the production helper instead of
    setting it manually so the helper-to-rule contract is end-to-end
    pinned at the rule-test layer."""
    from app.context import _outbound_destination_mismatch

    ctx = base_ctx()
    ctx["customer_registered_country"] = None
    ctx["customer_destination_country_mismatch_outbound"] = _outbound_destination_mismatch(
        None, "US"
    )
    ctx["origin_via_carrier_dropoff"] = True
    ctx["customer_observations"] = 3.0
    # Derivation result is False (None input), so the rule should not fire.
    assert ctx["customer_destination_country_mismatch_outbound"] is False
    assert rule.evaluate(ctx) is False


def test_does_not_fire_on_empty_registered_country(rule) -> None:
    """Empty-string defensive path: same helper-to-rule end-to-end
    test as above but with empty registered_country. Pydantic blocks
    this case at ingress in production but the helper's defensive
    falsy check should still produce no signal."""
    from app.context import _outbound_destination_mismatch

    ctx = base_ctx()
    ctx["customer_registered_country"] = ""
    ctx["customer_destination_country_mismatch_outbound"] = _outbound_destination_mismatch("", "US")
    ctx["origin_via_carrier_dropoff"] = True
    ctx["customer_observations"] = 3.0
    assert ctx["customer_destination_country_mismatch_outbound"] is False
    assert rule.evaluate(ctx) is False
