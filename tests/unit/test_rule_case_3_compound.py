"""Unit tests for case_3_compound rule.

Truth-table coverage for the case-3a compound:
    origin_via_carrier_dropoff
    AND shipment_route_unfamiliar_for_customer
    AND ip_fully_new
    AND customer_observations >= 10

Plus rule-shape sanity (weight + maturity_sensitive).
"""

from __future__ import annotations

import pytest

from app.rules import RuleSet
from tests.unit.conftest import base_ctx, find_rule


@pytest.fixture
def rule(ruleset: RuleSet):
    return find_rule(ruleset, "case_3_compound")


def _all_three_signals_true_ctx() -> dict:
    """Base context with all three case-3a signals true + maturity gate met."""
    ctx = base_ctx()
    ctx["origin_via_carrier_dropoff"] = True
    ctx["shipment_route_unfamiliar_for_customer"] = True
    ctx["ip_fully_new"] = True
    ctx["customer_observations"] = 15.0
    return ctx


def test_all_three_signals_true_fires(rule) -> None:
    """When all three case-3a signals are true AND customer is mature,
    the rule fires."""
    assert rule.evaluate(_all_three_signals_true_ctx()) is True


def test_dropoff_false_does_not_fire(rule) -> None:
    """origin_via_carrier_dropoff is required."""
    ctx = _all_three_signals_true_ctx()
    ctx["origin_via_carrier_dropoff"] = False
    assert rule.evaluate(ctx) is False


def test_route_familiar_does_not_fire(rule) -> None:
    """shipment_route_unfamiliar_for_customer is required."""
    ctx = _all_three_signals_true_ctx()
    ctx["shipment_route_unfamiliar_for_customer"] = False
    assert rule.evaluate(ctx) is False


def test_ip_not_fully_new_does_not_fire(rule) -> None:
    """ip_fully_new is required (case-3a is by definition a new-IP pattern)."""
    ctx = _all_three_signals_true_ctx()
    ctx["ip_fully_new"] = False
    assert rule.evaluate(ctx) is False


def test_low_maturity_does_not_fire(rule) -> None:
    """customer_observations < 10 blocks the rule (cold-start safety)."""
    ctx = _all_three_signals_true_ctx()
    ctx["customer_observations"] = 5.0
    assert rule.evaluate(ctx) is False


def test_maturity_at_exactly_ten_fires(rule) -> None:
    """Maturity gate is `>= 10`; exactly 10 observations passes."""
    ctx = _all_three_signals_true_ctx()
    ctx["customer_observations"] = 10.0
    assert rule.evaluate(ctx) is True


def test_maturity_just_below_ten_does_not_fire(rule) -> None:
    """Strict boundary: 9 observations does NOT fire."""
    ctx = _all_three_signals_true_ctx()
    ctx["customer_observations"] = 9.0
    assert rule.evaluate(ctx) is False


def test_all_signals_false_does_not_fire(rule) -> None:
    """Trivial baseline — base_ctx default is all-False on case-3a signals."""
    ctx = base_ctx()
    # base_ctx defaults the three new case-3a signals to False, so the
    # AND short-circuits regardless of the customer_observations default.
    assert rule.evaluate(ctx) is False


def test_weight_is_zero_point_seven(rule) -> None:
    """Weight pin: changing the weight requires a deliberate rule edit."""
    assert rule.weight == 0.70


def test_maturity_sensitive_is_true(rule) -> None:
    """maturity_sensitive is True so the noisy-OR downweighting at
    lower maturity degrades the contribution toward REVIEW rather than
    BLOCK on low-maturity customers."""
    assert rule.maturity_sensitive is True


def test_action_is_not_block(rule) -> None:
    """case_3_compound contributes to the noisy-OR; it does NOT hard-block.
    The Layer 1 BLOCK action is reserved for ip_in_level1 and similar
    confirmed-attacker signals."""
    assert rule.action == ""
