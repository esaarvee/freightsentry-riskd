"""Unit tests for cold_start_population_baseline_rare_with_carrier_dropoff rule.

Truth-table coverage for the case-3b sophisticated compound:
    shipment_route_rare_for_tenant
    AND origin_via_carrier_dropoff
    AND customer_observations < 10

Plus rule-shape sanity (weight 0.70, maturity_sensitive=false,
action="").
"""

from __future__ import annotations

import pytest

from app.rules import RuleSet
from tests.unit.conftest import base_ctx, find_rule


@pytest.fixture
def rule(ruleset: RuleSet):
    return find_rule(ruleset, "cold_start_population_baseline_rare_with_carrier_dropoff")


def _firing_ctx() -> dict:
    """Base context with all three conditions true (brand-new customer +
    rare population route + carrier dropoff)."""
    ctx = base_ctx()
    ctx["shipment_route_rare_for_tenant"] = True
    ctx["origin_via_carrier_dropoff"] = True
    ctx["customer_observations"] = 3.0
    return ctx


def test_all_three_signals_true_fires(rule) -> None:
    assert rule.evaluate(_firing_ctx()) is True


def test_route_not_rare_does_not_fire(rule) -> None:
    ctx = _firing_ctx()
    ctx["shipment_route_rare_for_tenant"] = False
    assert rule.evaluate(ctx) is False


def test_dropoff_false_does_not_fire(rule) -> None:
    ctx = _firing_ctx()
    ctx["origin_via_carrier_dropoff"] = False
    assert rule.evaluate(ctx) is False


def test_established_customer_does_not_fire(rule) -> None:
    """Cold-start gate is < 10; established customers (>= 10) miss this rule."""
    ctx = _firing_ctx()
    ctx["customer_observations"] = 15.0
    assert rule.evaluate(ctx) is False


def test_cold_start_boundary_at_nine_fires(rule) -> None:
    """Strict-less-than boundary: 9 fires."""
    ctx = _firing_ctx()
    ctx["customer_observations"] = 9.0
    assert rule.evaluate(ctx) is True


def test_cold_start_boundary_at_ten_does_not_fire(rule) -> None:
    """Strict-less-than boundary: 10 does not fire."""
    ctx = _firing_ctx()
    ctx["customer_observations"] = 10.0
    assert rule.evaluate(ctx) is False


def test_all_signals_false_does_not_fire(rule) -> None:
    """base_ctx defaults preserve baseline behavior (no fire).
    The case-3b signals (rare + dropoff) default False; the AND
    short-circuits regardless of the customer_observations default."""
    assert rule.evaluate(base_ctx()) is False


def test_weight_is_zero_point_seven(rule) -> None:
    """Weight pin: 0.70 — slightly higher than the simple case-3b
    compound (0.65) because the tenant-population-derived signal is
    more specific."""
    assert rule.weight == 0.70


def test_maturity_sensitive_is_false(rule) -> None:
    """Maturity-insensitive — the cold-start gate (< 10) is in the
    condition itself; downweighting via maturity_sensitive would
    suppress the very signal we're using to flag the threat."""
    assert rule.maturity_sensitive is False


def test_action_is_not_block(rule) -> None:
    """Contributes to noisy-OR; does not hard-block."""
    assert rule.action == ""
