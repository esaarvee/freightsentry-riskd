"""Unit tests for the Phase 2C.6 value-anomaly + geographic + threat-
intel composite rules.

Seventeen rules grouped into:
- Value-anomaly (6): z-score and absolute-value thresholds, with one
  pair (extreme_value + above_normal_value) intentionally tier-disjoint
  via the `<= 3.0` upper bound on the lower-tier rule
- Geographic (5): IP distance + country-change + impossible-travel
- Threat-intel composites (6): Level-2-with-VPN, IP2P signal/scanner/
  new-user/api, open-proxy (NOT VPN AND NOT Tor)

Note: PLAN_PHASE_2C.md triaged 2 rules out of the original 19-rule list
(threat_intel_level1 — Phase 1 BLOCK already covers; outside_allowed_country
— defers to Phase 4 tenant-config landing). After triage, 17 rules
land here.
"""

from __future__ import annotations

import pytest

from app.rules import RuleSet
from tests.unit.conftest import base_ctx, find_rule

# ---------------------------------------------------------------------------
# Value-anomaly: extreme_value + above_normal_value tier disjointness
# ---------------------------------------------------------------------------


def test_extreme_value_above_three_sigma(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "extreme_value")
    ctx = base_ctx()
    ctx["customer_observations"] = 15.0
    ctx["value_zscore"] = 3.1
    assert rule.evaluate(ctx) is True
    # Strict > 3.0
    ctx["value_zscore"] = 3.0
    assert rule.evaluate(ctx) is False
    # Observations gate
    ctx["value_zscore"] = 3.1
    ctx["customer_observations"] = 9.0
    assert rule.evaluate(ctx) is False


def test_above_normal_value_tier_window(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "above_normal_value")
    ctx = base_ctx()
    ctx["customer_observations"] = 15.0
    # Strict > 2.0
    ctx["value_zscore"] = 2.0
    assert rule.evaluate(ctx) is False
    ctx["value_zscore"] = 2.5
    assert rule.evaluate(ctx) is True
    # Upper bound <= 3.0 — at 3.0 still fires (inclusive)
    ctx["value_zscore"] = 3.0
    assert rule.evaluate(ctx) is True
    # Above 3.0 — does NOT fire (extreme_value takes over)
    ctx["value_zscore"] = 3.1
    assert rule.evaluate(ctx) is False


def test_value_zscore_rules_are_tier_disjoint(ruleset: RuleSet) -> None:
    """At no z-score in [0, 5] do both extreme_value and above_normal_
    value fire simultaneously."""
    extreme = find_rule(ruleset, "extreme_value")
    above = find_rule(ruleset, "above_normal_value")
    ctx = base_ctx()
    ctx["customer_observations"] = 15.0
    for z_int in range(0, 50):
        ctx["value_zscore"] = z_int / 10.0
        assert not (
            extreme.evaluate(ctx) and above.evaluate(ctx)
        ), f"both value-anomaly rules fired at z={z_int / 10.0}"


def test_above_normal_value_vpn(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "above_normal_value_vpn")
    ctx = base_ctx()
    ctx["value_zscore"] = 2.5
    ctx["is_vpn"] = True
    assert rule.evaluate(ctx) is True
    # Strict > 2.0
    ctx["value_zscore"] = 2.0
    assert rule.evaluate(ctx) is False
    # Without VPN
    ctx["value_zscore"] = 2.5
    ctx["is_vpn"] = False
    assert rule.evaluate(ctx) is False


def test_absolute_high_value(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "absolute_high_value")
    ctx = base_ctx()
    ctx["shipment_value"] = 10001.0
    assert rule.evaluate(ctx) is True
    ctx["shipment_value"] = 10000.0
    assert rule.evaluate(ctx) is False


def test_threat_intel_high_value(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "threat_intel_high_value")
    ctx = base_ctx()
    ctx["ip_in_threat_list"] = True
    ctx["shipment_value"] = 2001.0
    assert rule.evaluate(ctx) is True
    # Strict > 2000
    ctx["shipment_value"] = 2000.0
    assert rule.evaluate(ctx) is False
    # Without threat-list
    ctx["shipment_value"] = 2001.0
    ctx["ip_in_threat_list"] = False
    assert rule.evaluate(ctx) is False


def test_ip2p_threat_high_value(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "ip2p_threat_high_value")
    ctx = base_ctx()
    ctx["ip2p_threat_any"] = True
    ctx["shipment_value"] = 2001.0
    assert rule.evaluate(ctx) is True
    ctx["ip2p_threat_any"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# Geographic
# ---------------------------------------------------------------------------


def test_ip_intercontinental_jump(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "ip_intercontinental_jump")
    ctx = base_ctx()
    ctx["ip_distance_km"] = 5001.0
    assert rule.evaluate(ctx) is True
    ctx["ip_distance_km"] = 5000.0
    assert rule.evaluate(ctx) is False


def test_ip_long_distance_new_ip(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "ip_long_distance_new_ip")
    ctx = base_ctx()
    ctx["ip_distance_km"] = 2001.0
    ctx["is_new_ip"] = True
    assert rule.evaluate(ctx) is True
    # Without new IP
    ctx["is_new_ip"] = False
    assert rule.evaluate(ctx) is False
    # Strict > 2000
    ctx["is_new_ip"] = True
    ctx["ip_distance_km"] = 2000.0
    assert rule.evaluate(ctx) is False


def test_ip_country_change(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "ip_country_change")
    ctx = base_ctx()
    ctx["ip_country_changed"] = True
    ctx["is_new_ip"] = True
    assert rule.evaluate(ctx) is True
    ctx["is_new_ip"] = False
    assert rule.evaluate(ctx) is False
    ctx["ip_country_changed"] = False
    ctx["is_new_ip"] = True
    assert rule.evaluate(ctx) is False


def test_api_country_change_unfamiliar(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "api_country_change_unfamiliar")
    ctx = base_ctx()
    ctx["is_api_booking"] = True
    ctx["is_platform_booking"] = False
    ctx["ip_country_changed"] = True
    ctx["is_new_ip"] = True
    assert rule.evaluate(ctx) is True
    # Drop API channel
    ctx["is_api_booking"] = False
    ctx["is_platform_booking"] = True
    assert rule.evaluate(ctx) is False


def test_impossible_travel_geo(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "impossible_travel_geo")
    ctx = base_ctx()
    ctx["impossible_travel"] = False
    assert rule.evaluate(ctx) is False
    ctx["impossible_travel"] = True
    assert rule.evaluate(ctx) is True


# ---------------------------------------------------------------------------
# Threat-intel composites
# ---------------------------------------------------------------------------


def test_threat_level2_vpn(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "threat_level2_vpn")
    ctx = base_ctx()
    ctx["ip_in_level2"] = True
    ctx["is_vpn"] = True
    assert rule.evaluate(ctx) is True
    ctx["is_vpn"] = False
    assert rule.evaluate(ctx) is False


@pytest.mark.parametrize(
    "rule_name,flag_name",
    [
        ("ip2p_threat_scanner_signal", "ip2p_threat_scanner"),
        ("ip2p_threat_spam_signal", "ip2p_threat_spam"),
    ],
)
def test_ip2p_threat_signal_single_boolean(
    ruleset: RuleSet, rule_name: str, flag_name: str
) -> None:
    rule = find_rule(ruleset, rule_name)
    ctx = base_ctx()
    ctx[flag_name] = False
    assert rule.evaluate(ctx) is False
    ctx[flag_name] = True
    assert rule.evaluate(ctx) is True


def test_ip2p_threat_new_user(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "ip2p_threat_new_user")
    ctx = base_ctx()
    ctx["ip2p_threat_any"] = True
    ctx["is_new_user"] = True
    assert rule.evaluate(ctx) is True
    ctx["is_new_user"] = False
    assert rule.evaluate(ctx) is False


def test_ip2p_threat_api(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "ip2p_threat_api")
    ctx = base_ctx()
    ctx["ip2p_threat_any"] = True
    ctx["is_api_booking"] = True
    ctx["is_platform_booking"] = False
    assert rule.evaluate(ctx) is True
    ctx["is_api_booking"] = False
    ctx["is_platform_booking"] = True
    assert rule.evaluate(ctx) is False


def test_open_proxy_excludes_vpn_and_tor(ruleset: RuleSet) -> None:
    """is_proxy AND NOT is_vpn AND NOT is_tor — open proxy is a
    distinct class from VPN/Tor, so the rule fires only when the IP
    is flagged proxy but neither VPN nor Tor."""
    rule = find_rule(ruleset, "open_proxy")
    ctx = base_ctx()
    ctx["is_proxy"] = True
    ctx["is_vpn"] = False
    ctx["is_tor"] = False
    assert rule.evaluate(ctx) is True
    # Add VPN — should NOT fire (the NOT is_vpn excludes)
    ctx["is_vpn"] = True
    assert rule.evaluate(ctx) is False
    ctx["is_vpn"] = False
    # Add Tor — should NOT fire
    ctx["is_tor"] = True
    assert rule.evaluate(ctx) is False
    # No proxy at all
    ctx["is_tor"] = False
    ctx["is_proxy"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# Set-level audit (canonical total-count check lives in 2C.7)
# ---------------------------------------------------------------------------


def test_value_geo_threat_rules_load(ruleset: RuleSet) -> None:
    """All 17 rules added in 2C.6 must be present after the rule-loader
    runs. (Plan summary line lists "13" — arithmetic error in the plan;
    the section table lists 19, triaged 2, lands at 17.)"""
    expected = {
        # Value-anomaly
        "extreme_value",
        "above_normal_value",
        "above_normal_value_vpn",
        "absolute_high_value",
        "threat_intel_high_value",
        "ip2p_threat_high_value",
        # Geographic
        "ip_intercontinental_jump",
        "ip_long_distance_new_ip",
        "ip_country_change",
        "api_country_change_unfamiliar",
        "impossible_travel_geo",
        # Threat-intel composites
        "threat_level2_vpn",
        "ip2p_threat_scanner_signal",
        "ip2p_threat_spam_signal",
        "ip2p_threat_new_user",
        "ip2p_threat_api",
        "open_proxy",
    }
    actual = {r.name for r in ruleset.rules}
    missing = expected - actual
    assert not missing, f"missing 2C.6 rules: {missing}"


def test_triaged_rules_not_present(ruleset: RuleSet) -> None:
    """threat_intel_level1 and outside_allowed_country are deferred per
    plan triage; confirm they're not accidentally added."""
    rejected = {"threat_intel_level1", "outside_allowed_country"}
    actual = {r.name for r in ruleset.rules}
    overlap = rejected & actual
    assert not overlap, (
        f"plan-triaged rules unexpectedly present: {overlap} — "
        "these were deferred (threat_intel_level1 duplicates the Phase "
        "1 BLOCK; outside_allowed_country awaits Phase 4 tenant config)"
    )
