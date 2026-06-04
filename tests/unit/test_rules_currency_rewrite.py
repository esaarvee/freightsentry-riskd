"""Per-rule + USD-default invariance tests for the 7 currency-implicit rules
rewritten in 4B.5.

For each rule:
1. Fires when the threshold field equals the pre-4B literal AND
   shipment_value is just above.
2. Does NOT fire when tenant_config elevates the threshold above the
   shipment_value (custom-elevated thresholds).

Plus a parametrized USD-default invariance test pinning that across a
matrix of shipment_value values, every rewritten rule fires identically
to its pre-Phase-4B condition under USD-default thresholds.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.unit.conftest import base_ctx, find_rule

# ---------------------------------------------------------------------------
# Per-rule fire / no-fire tests (7 rules x 2 tests = 14)
# ---------------------------------------------------------------------------


def test_vpn_high_value_fires_at_default_low(ruleset: Any) -> None:
    ctx = {
        **base_ctx(),
        "is_vpn": True,
        "shipment_value": 1001.0,
        "shipment_value_threshold_low": 1000.0,
    }
    assert find_rule(ruleset, "vpn_high_value").evaluate(ctx) is True


def test_vpn_high_value_does_not_fire_when_custom_low_elevated(ruleset: Any) -> None:
    ctx = {
        **base_ctx(),
        "is_vpn": True,
        "shipment_value": 1001.0,
        "shipment_value_threshold_low": 5000.0,
    }
    assert find_rule(ruleset, "vpn_high_value").evaluate(ctx) is False


def test_low_trust_high_value_fires_at_default_low(ruleset: Any) -> None:
    ctx = {
        **base_ctx(),
        "trust_score": 0.1,
        "shipment_value": 1001.0,
        "shipment_value_threshold_low": 1000.0,
    }
    assert find_rule(ruleset, "low_trust_high_value").evaluate(ctx) is True


def test_low_trust_high_value_does_not_fire_when_custom_low_elevated(ruleset: Any) -> None:
    ctx = {
        **base_ctx(),
        "trust_score": 0.1,
        "shipment_value": 1001.0,
        "shipment_value_threshold_low": 9999.0,
    }
    assert find_rule(ruleset, "low_trust_high_value").evaluate(ctx) is False


def test_flags_with_value_fires_at_default_medium(ruleset: Any) -> None:
    ctx = {
        **base_ctx(),
        "flagged_count": 4,
        "shipment_value": 2001.0,
        "shipment_value_threshold_medium": 2000.0,
    }
    assert find_rule(ruleset, "flags_with_value").evaluate(ctx) is True


def test_flags_with_value_does_not_fire_when_custom_medium_elevated(ruleset: Any) -> None:
    ctx = {
        **base_ctx(),
        "flagged_count": 4,
        "shipment_value": 2001.0,
        "shipment_value_threshold_medium": 9999.0,
    }
    assert find_rule(ruleset, "flags_with_value").evaluate(ctx) is False


def test_high_value_new_user_fires_at_default_new_user(ruleset: Any) -> None:
    ctx = {
        **base_ctx(),
        "is_new_user": True,
        "shipment_value": 5001.0,
        "shipment_value_threshold_new_user": 5000.0,
    }
    assert find_rule(ruleset, "high_value_new_user").evaluate(ctx) is True


def test_high_value_new_user_does_not_fire_when_custom_new_user_elevated(ruleset: Any) -> None:
    ctx = {
        **base_ctx(),
        "is_new_user": True,
        "shipment_value": 5001.0,
        "shipment_value_threshold_new_user": 99999.0,
    }
    assert find_rule(ruleset, "high_value_new_user").evaluate(ctx) is False


def test_absolute_high_value_fires_at_default_high(ruleset: Any) -> None:
    ctx = {
        **base_ctx(),
        "shipment_value": 10001.0,
        "shipment_value_threshold_high": 10000.0,
    }
    assert find_rule(ruleset, "absolute_high_value").evaluate(ctx) is True


def test_absolute_high_value_does_not_fire_when_custom_high_elevated(ruleset: Any) -> None:
    ctx = {
        **base_ctx(),
        "shipment_value": 10001.0,
        "shipment_value_threshold_high": 50000.0,
    }
    assert find_rule(ruleset, "absolute_high_value").evaluate(ctx) is False


def test_threat_intel_high_value_fires_at_default_medium(ruleset: Any) -> None:
    ctx = {
        **base_ctx(),
        "ip_in_threat_list": True,
        "shipment_value": 2001.0,
        "shipment_value_threshold_medium": 2000.0,
    }
    assert find_rule(ruleset, "threat_intel_high_value").evaluate(ctx) is True


def test_threat_intel_high_value_does_not_fire_when_custom_medium_elevated(ruleset: Any) -> None:
    ctx = {
        **base_ctx(),
        "ip_in_threat_list": True,
        "shipment_value": 2001.0,
        "shipment_value_threshold_medium": 99999.0,
    }
    assert find_rule(ruleset, "threat_intel_high_value").evaluate(ctx) is False


def test_ip2p_threat_high_value_fires_at_default_medium(ruleset: Any) -> None:
    ctx = {
        **base_ctx(),
        "ip2p_threat_any": True,
        "shipment_value": 2001.0,
        "shipment_value_threshold_medium": 2000.0,
    }
    assert find_rule(ruleset, "ip2p_threat_high_value").evaluate(ctx) is True


def test_ip2p_threat_high_value_does_not_fire_when_custom_medium_elevated(ruleset: Any) -> None:
    ctx = {
        **base_ctx(),
        "ip2p_threat_any": True,
        "shipment_value": 2001.0,
        "shipment_value_threshold_medium": 99999.0,
    }
    assert find_rule(ruleset, "ip2p_threat_high_value").evaluate(ctx) is False


# ---------------------------------------------------------------------------
# USD-default invariance: with the default thresholds, the rewritten rules
# fire identically to their pre-Phase-4B (literal-threshold) conditions.
# Parametrized across a value matrix that brackets every tier boundary.
# ---------------------------------------------------------------------------


_RULE_USD_DEFAULTS = [
    # (rule_name, pre-Phase-4B literal threshold, tier-field name, extra-conds)
    ("vpn_high_value", 1000.0, "shipment_value_threshold_low", {"is_vpn": True}),
    (
        "low_trust_high_value",
        1000.0,
        "shipment_value_threshold_low",
        {"trust_score": 0.1},
    ),
    (
        "flags_with_value",
        2000.0,
        "shipment_value_threshold_medium",
        {"flagged_count": 4},
    ),
    (
        "high_value_new_user",
        5000.0,
        "shipment_value_threshold_new_user",
        {"is_new_user": True},
    ),
    ("absolute_high_value", 10000.0, "shipment_value_threshold_high", {}),
    (
        "threat_intel_high_value",
        2000.0,
        "shipment_value_threshold_medium",
        {"ip_in_threat_list": True},
    ),
    (
        "ip2p_threat_high_value",
        2000.0,
        "shipment_value_threshold_medium",
        {"ip2p_threat_any": True},
    ),
]


@pytest.mark.parametrize("shipment_value", [500.0, 1500.0, 2500.0, 4000.0, 5500.0, 9500.0, 10500.0])
def test_usd_default_invariance_matrix(ruleset: Any, shipment_value: float) -> None:
    """For each rewritten rule + each shipment_value, the rule's evaluation
    under USD-default thresholds must match the pre-Phase-4B literal
    threshold semantics (rule fires iff shipment_value > literal AND other
    conditions met).

    This is the surgical invariance check for the 4B.5 rewrite. If any rule
    flips outcome relative to its Phase 2 literal, the rewrite has a bug.
    """
    base = base_ctx()
    base["shipment_value"] = shipment_value
    for rule_name, literal, _tier_field, extra_conds in _RULE_USD_DEFAULTS:
        # USD-default thresholds are already populated in base_ctx by 4B.4's
        # conftest update.
        ctx = {**base, **extra_conds}
        rule = find_rule(ruleset, rule_name)
        # Pre-Phase-4B condition: shipment_value > literal AND extra_conds met
        # All extra_conds are truthy by construction (we set them); the
        # invariant is rule.evaluate(ctx) == (shipment_value > literal).
        expected = shipment_value > literal
        assert rule.evaluate(ctx) is expected, (
            f"USD-default invariance broken for {rule_name!r} at "
            f"shipment_value={shipment_value}: expected {expected}, "
            f"got {rule.evaluate(ctx)}"
        )


def test_rule_count_after_6a9(ruleset: Any) -> None:
    """4B.5 rewrites 7 rules but adds/removes none → 79; Phase 6A.3 adds
    case_3_compound → 80; Phase 6A.5 adds
    cold_start_country_triangle_with_carrier_dropoff → 81; Phase 6A.9
    adds cold_start_population_baseline_rare_with_carrier_dropoff → 82.
    Phase 7C.2 swaps cold_start_country_triangle_with_carrier_dropoff
    for cold_start_outbound_carrier_dropoff (1-for-1) → unchanged at 82.

    Uses the conftest `ruleset` fixture (cwd-independent) rather than
    opening rules.yaml by relative path.
    """
    assert len(ruleset.rules) == 82
