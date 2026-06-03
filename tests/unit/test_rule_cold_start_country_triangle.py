"""Unit tests for Phase 6A.5 cold_start_country_triangle_with_carrier_dropoff rule.

Truth-table coverage for the case-3b simple compound:
    customer_country_triangle_mismatch
    AND origin_via_carrier_dropoff
    AND customer_observations < 10

Plus rule-shape sanity (weight, maturity_sensitive, action).
"""

from __future__ import annotations

import pytest

from app.rules import RuleSet
from tests.unit.conftest import base_ctx, find_rule


@pytest.fixture
def rule(ruleset: RuleSet):
    return find_rule(ruleset, "cold_start_country_triangle_with_carrier_dropoff")


def _firing_ctx() -> dict:
    """Base context with all three conditions true (brand-new customer)."""
    ctx = base_ctx()
    ctx["customer_country_triangle_mismatch"] = True
    ctx["origin_via_carrier_dropoff"] = True
    ctx["customer_observations"] = 3.0
    return ctx


def test_all_three_signals_true_fires(rule) -> None:
    assert rule.evaluate(_firing_ctx()) is True


def test_triangle_false_does_not_fire(rule) -> None:
    ctx = _firing_ctx()
    ctx["customer_country_triangle_mismatch"] = False
    assert rule.evaluate(ctx) is False


def test_dropoff_false_does_not_fire(rule) -> None:
    ctx = _firing_ctx()
    ctx["origin_via_carrier_dropoff"] = False
    assert rule.evaluate(ctx) is False


def test_established_customer_does_not_fire(rule) -> None:
    """Cold-start gate is < 10; established customers (>= 10) miss this rule
    (case-3a case_3_compound covers their threat shape instead)."""
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
    """base_ctx defaults preserve pre-Phase-6A behavior (no fire)."""
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
