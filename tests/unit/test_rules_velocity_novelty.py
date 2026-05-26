"""Unit tests for the Phase 2C.5 velocity + identity-novelty rules.

Eleven rules grouped into:
- Velocity spikes (5 rules) — hourly + daily spikes per channel + IP-velocity
  + VPN/new-user compounds
- Simple identity-novelty (4 rules) — email blocklist/pattern, new-user
  compounds with VPN/value

Each test loads the production app/rules.yaml via the shared `ruleset`
fixture in tests/unit/conftest.py and exercises the rule's evaluator.
"""

from __future__ import annotations

from app.rules import RuleSet
from tests.unit.conftest import base_ctx, find_rule

# ---------------------------------------------------------------------------
# velocity_spike_hourly_ui — is_platform_booking AND velocity_user_hourly > 60
# ---------------------------------------------------------------------------


def test_velocity_spike_hourly_ui_strict_60(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "velocity_spike_hourly_ui")
    ctx = base_ctx()
    ctx["is_platform_booking"] = True
    ctx["is_api_booking"] = False
    ctx["velocity_user_hourly"] = 60
    assert rule.evaluate(ctx) is False
    ctx["velocity_user_hourly"] = 61
    assert rule.evaluate(ctx) is True
    # API channel must not fire
    ctx["is_platform_booking"] = False
    ctx["is_api_booking"] = True
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# velocity_spike_hourly_api — is_api_booking AND velocity_user_hourly > 500
# ---------------------------------------------------------------------------


def test_velocity_spike_hourly_api_strict_500(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "velocity_spike_hourly_api")
    ctx = base_ctx()
    ctx["is_api_booking"] = True
    ctx["is_platform_booking"] = False
    ctx["velocity_user_hourly"] = 500
    assert rule.evaluate(ctx) is False
    ctx["velocity_user_hourly"] = 501
    assert rule.evaluate(ctx) is True


# ---------------------------------------------------------------------------
# velocity_spike_daily_ui — UI AND velocity_user_daily > 300 AND obs < 30
# ---------------------------------------------------------------------------


def test_velocity_spike_daily_ui_requires_relatively_new_account(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "velocity_spike_daily_ui")
    ctx = base_ctx()
    ctx["is_platform_booking"] = True
    ctx["is_api_booking"] = False
    ctx["velocity_user_daily"] = 301
    ctx["customer_observations"] = 29.0
    assert rule.evaluate(ctx) is True
    # Observations >= 30 → does NOT fire (strict < 30)
    ctx["customer_observations"] = 30.0
    assert rule.evaluate(ctx) is False
    # Below daily threshold → does NOT fire
    ctx["customer_observations"] = 29.0
    ctx["velocity_user_daily"] = 300
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# velocity_spike_daily_api — API AND velocity_user_daily > 50 AND obs < 30
# Tuned threshold per verification §2.2
# ---------------------------------------------------------------------------


def test_velocity_spike_daily_api_tuned_threshold_50(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "velocity_spike_daily_api")
    ctx = base_ctx()
    ctx["is_api_booking"] = True
    ctx["is_platform_booking"] = False
    ctx["velocity_user_daily"] = 51
    ctx["customer_observations"] = 20.0
    assert rule.evaluate(ctx) is True
    # Strict > 50 — exactly 50 must NOT fire
    ctx["velocity_user_daily"] = 50
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# ip_velocity_threat — velocity_ip_daily > 5 AND ip_in_threat_list
# ---------------------------------------------------------------------------


def test_ip_velocity_threat_compound(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "ip_velocity_threat")
    ctx = base_ctx()
    ctx["velocity_ip_daily"] = 6
    ctx["ip_in_threat_list"] = True
    assert rule.evaluate(ctx) is True
    # Strict > 5
    ctx["velocity_ip_daily"] = 5
    assert rule.evaluate(ctx) is False
    # Without threat-list match
    ctx["velocity_ip_daily"] = 6
    ctx["ip_in_threat_list"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# user_velocity_vpn — velocity_user_daily > 3 AND is_vpn
# ---------------------------------------------------------------------------


def test_user_velocity_vpn_compound(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "user_velocity_vpn")
    ctx = base_ctx()
    ctx["velocity_user_daily"] = 4
    ctx["is_vpn"] = True
    assert rule.evaluate(ctx) is True
    ctx["velocity_user_daily"] = 3
    assert rule.evaluate(ctx) is False
    ctx["velocity_user_daily"] = 4
    ctx["is_vpn"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# user_velocity_new_user — velocity_user_daily > 3 AND is_new_user
# ---------------------------------------------------------------------------


def test_user_velocity_new_user_compound(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "user_velocity_new_user")
    ctx = base_ctx()
    ctx["velocity_user_daily"] = 4
    ctx["is_new_user"] = True
    assert rule.evaluate(ctx) is True
    ctx["is_new_user"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# dummy_email_blocklisted — single boolean
# ---------------------------------------------------------------------------


def test_dummy_email_blocklisted_fires_on_true(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "dummy_email_blocklisted")
    ctx = base_ctx()
    ctx["is_email_blocklisted"] = False
    assert rule.evaluate(ctx) is False
    ctx["is_email_blocklisted"] = True
    assert rule.evaluate(ctx) is True


# ---------------------------------------------------------------------------
# dummy_email_suspicious_pattern — single boolean
# ---------------------------------------------------------------------------


def test_dummy_email_suspicious_pattern_fires_on_true(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "dummy_email_suspicious_pattern")
    ctx = base_ctx()
    ctx["is_email_suspicious_pattern"] = True
    assert rule.evaluate(ctx) is True
    ctx["is_email_suspicious_pattern"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# vpn_new_user — is_vpn AND is_new_user
# ---------------------------------------------------------------------------


def test_vpn_new_user_requires_both(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "vpn_new_user")
    ctx = base_ctx()
    ctx["is_vpn"] = True
    ctx["is_new_user"] = True
    assert rule.evaluate(ctx) is True
    ctx["is_new_user"] = False
    assert rule.evaluate(ctx) is False
    ctx["is_new_user"] = True
    ctx["is_vpn"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# high_value_new_user — shipment_value > 5000 AND is_new_user
# ---------------------------------------------------------------------------


def test_high_value_new_user_strict_5000(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "high_value_new_user")
    ctx = base_ctx()
    ctx["shipment_value"] = 5001.0
    ctx["is_new_user"] = True
    assert rule.evaluate(ctx) is True
    # Strict > 5000 — exactly 5000 must NOT fire
    ctx["shipment_value"] = 5000.0
    assert rule.evaluate(ctx) is False
    # Established user
    ctx["shipment_value"] = 5001.0
    ctx["is_new_user"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# Set-level audit
# ---------------------------------------------------------------------------


def test_velocity_novelty_rules_load(ruleset: RuleSet) -> None:
    """All 11 rules added in 2C.5 must be present after the rule-loader
    runs. Canonical total-count audit lives in 2C.7."""
    expected = {
        "velocity_spike_hourly_ui",
        "velocity_spike_hourly_api",
        "velocity_spike_daily_ui",
        "velocity_spike_daily_api",
        "ip_velocity_threat",
        "user_velocity_vpn",
        "user_velocity_new_user",
        "dummy_email_blocklisted",
        "dummy_email_suspicious_pattern",
        "vpn_new_user",
        "high_value_new_user",
    }
    actual = {r.name for r in ruleset.rules}
    missing = expected - actual
    assert not missing, f"missing 2C.5 rules: {missing}"
