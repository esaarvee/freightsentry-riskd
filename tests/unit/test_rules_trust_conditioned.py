"""Unit tests for the Phase 2C.1 trust-conditioned rule additions.

Each test loads the production `app/rules.yaml` via `app.rules.load_rules`,
finds the rule by name, and exercises its evaluator with a controlled
ctx dict. This proves the rule lives in YAML, parses through the DSL,
and fires exactly at the documented threshold (boundary-side wins).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.rules import ALLOWED_CONTEXT_FIELDS, Rule, load_rules

_RULES_YAML = Path(__file__).resolve().parents[2] / "app" / "rules.yaml"


def _base_ctx() -> dict[str, Any]:
    """Neutral ctx with every whitelisted field populated. Tests
    override specific keys to exercise their target rule."""
    ctx: dict[str, Any] = {
        # numerics default to non-firing values
        "shipment_value": 100.0,
        "booking_hour_utc": 12,
        "booking_weekday": 2,
        "customer_observations": 100.0,
        "account_age_days": 365,
        "total_shipments": 100,
        "flagged_count": 0,
        "fraud_confirmed_count": 0,
        "trust_score": 1.0,
        "ip_threat_score": 0.0,
        "ip_distance_km": 0.0,
        "velocity_user_hourly": 0,
        "velocity_user_daily": 0,
        "velocity_user_30d": 0,
        "velocity_ip_hourly": 0,
        "velocity_ip_daily": 0,
        "customer_distinct_ips_30d": 0,
        "recipient_cross_customer_count": 0,
        "value_zscore": 0.0,
        "cadence_zscore_hours": 0.0,
        "days_since_last_booking": 0,
        # strings
        "ip_country": "US",
        "ip_familiarity_tier": "familiar",
        # booleans default to False (non-firing)
        "is_api_booking": False,
        "is_platform_booking": True,
        "is_cloud_ip": False,
        "is_datacenter_ip": False,
        "is_vpn": False,
        "is_tor": False,
        "is_proxy": False,
        "ip_in_level1": False,
        "ip_in_level2": False,
        "ip_in_threat_list": False,
        "ip_country_changed": False,
        "ip2p_threat_botnet": False,
        "ip2p_threat_scanner": False,
        "ip2p_threat_spam": False,
        "ip2p_threat_any": False,
        "is_residential_asn": False,
        "is_new_ip": False,
        "ip_new_known_asn": False,
        "ip_fully_new": False,
        "ip_family_familiar": True,
        "is_new_route": False,
        "origin_address_familiar": True,
        "destination_address_familiar": True,
        "origin_ip_country_familiar": True,
        "is_abnormally_dormant": False,
        "customer_locked_cloud_api": False,
        "customer_locked_web_only": False,
        "is_new_user": False,
        "impossible_travel": False,
        "is_email_disposable": False,
        "is_email_blocklisted": False,
        "is_email_suspicious_pattern": False,
        "is_phone_dummy_pattern": False,
    }
    # Sanity: every whitelist field has a default — catches drift if
    # the whitelist grows without the test fixture being updated.
    missing = ALLOWED_CONTEXT_FIELDS - set(ctx.keys())
    assert not missing, f"_base_ctx missing fields: {missing}"
    return ctx


@pytest.fixture(scope="module")
def ruleset() -> Any:
    return load_rules(_RULES_YAML)


def _find(ruleset: Any, name: str) -> Rule:
    for r in ruleset.rules:
        if r.name == name:
            return r
    msg = f"rule {name!r} not found in ruleset"
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
# very_low_trust — trust_score < 0.2 (strict)
# ---------------------------------------------------------------------------


def test_very_low_trust_fires_below_threshold(ruleset: Any) -> None:
    rule = _find(ruleset, "very_low_trust")
    ctx = _base_ctx()
    ctx["trust_score"] = 0.15
    assert rule.evaluate(ctx) is True


def test_very_low_trust_does_not_fire_at_threshold(ruleset: Any) -> None:
    """Strict <: trust_score == 0.2 must NOT fire."""
    rule = _find(ruleset, "very_low_trust")
    ctx = _base_ctx()
    ctx["trust_score"] = 0.2
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# low_trust_high_value — trust_score < 0.3 AND shipment_value > 1000
# ---------------------------------------------------------------------------


def test_low_trust_high_value_requires_both(ruleset: Any) -> None:
    rule = _find(ruleset, "low_trust_high_value")
    ctx = _base_ctx()
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


def test_low_trust_vpn_compound(ruleset: Any) -> None:
    rule = _find(ruleset, "low_trust_vpn")
    ctx = _base_ctx()
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


def test_very_low_trust_velocity_compound(ruleset: Any) -> None:
    rule = _find(ruleset, "very_low_trust_velocity")
    ctx = _base_ctx()
    ctx["trust_score"] = 0.1
    ctx["velocity_user_hourly"] = 4
    assert rule.evaluate(ctx) is True
    ctx["velocity_user_hourly"] = 3
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# threat_score_moderate — ip_threat_score > 0.5
# ---------------------------------------------------------------------------


def test_threat_score_moderate_above_half(ruleset: Any) -> None:
    rule = _find(ruleset, "threat_score_moderate")
    ctx = _base_ctx()
    ctx["ip_threat_score"] = 0.5
    assert rule.evaluate(ctx) is False
    ctx["ip_threat_score"] = 0.51
    assert rule.evaluate(ctx) is True


# ---------------------------------------------------------------------------
# flags_with_value — flagged_count > 3 AND shipment_value > 2000
# ---------------------------------------------------------------------------


def test_flags_with_value_requires_both_conditions(ruleset: Any) -> None:
    rule = _find(ruleset, "flags_with_value")
    ctx = _base_ctx()
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


def test_vpn_known_user_excludes_new(ruleset: Any) -> None:
    rule = _find(ruleset, "vpn_known_user")
    ctx = _base_ctx()
    ctx["is_vpn"] = True
    ctx["is_new_user"] = False
    assert rule.evaluate(ctx) is True
    ctx["is_new_user"] = True
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# Rule-loader sanity: trust-conditioned set is integrated
# ---------------------------------------------------------------------------


def test_all_trust_conditioned_rules_load(ruleset: Any) -> None:
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


def test_total_rule_count_after_2c1(ruleset: Any) -> None:
    """Phase 1 baseline = 14; 2C.1 adds 7 → 21."""
    assert len(ruleset.rules) == 21
