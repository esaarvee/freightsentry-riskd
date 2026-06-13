"""Unit tests for the dormancy + customer-lock-in rule additions.

These are the case-1 (dashboard ATO) + case-2 (API ATO) primary detectors.
Per CLAUDE.md these are HIGH-risk rules — boolean composition typos
would silently miscalibrate the highest-stakes rule in the catalog.
Truth-table coverage is the mitigation.

Shared helpers in tests/unit/conftest.py.
"""

from __future__ import annotations

from app.rules import RuleSet
from tests.unit.conftest import base_ctx, find_rule

# ---------------------------------------------------------------------------
# dormant_vpn — is_abnormally_dormant AND is_vpn
# ---------------------------------------------------------------------------


def test_dormant_vpn_requires_both(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "dormant_vpn")
    ctx = base_ctx()
    # Both False (neutral) → no fire
    assert rule.evaluate(ctx) is False
    # Only dormancy → no fire
    ctx["is_abnormally_dormant"] = True
    assert rule.evaluate(ctx) is False
    # Only VPN → no fire
    ctx["is_abnormally_dormant"] = False
    ctx["is_vpn"] = True
    assert rule.evaluate(ctx) is False
    # Both → fires
    ctx["is_abnormally_dormant"] = True
    assert rule.evaluate(ctx) is True


# ---------------------------------------------------------------------------
# dormant_new_ip — is_abnormally_dormant AND ip_fully_new
# ---------------------------------------------------------------------------


def test_dormant_new_ip_compound(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "dormant_new_ip")
    ctx = base_ctx()
    ctx["is_abnormally_dormant"] = True
    ctx["ip_fully_new"] = False
    assert rule.evaluate(ctx) is False
    ctx["ip_fully_new"] = True
    assert rule.evaluate(ctx) is True
    ctx["is_abnormally_dormant"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# ip_distance_dormant — ip_distance_km > 1000 AND is_abnormally_dormant
# ---------------------------------------------------------------------------


def test_ip_distance_dormant_kilometre_threshold(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "ip_distance_dormant")
    ctx = base_ctx()
    ctx["is_abnormally_dormant"] = True
    ctx["ip_distance_km"] = 999.0
    assert rule.evaluate(ctx) is False
    ctx["ip_distance_km"] = 1001.0
    assert rule.evaluate(ctx) is True
    # Strict > 1000 (not >=)
    ctx["ip_distance_km"] = 1000.0
    assert rule.evaluate(ctx) is False
    # Dormancy is required
    ctx["ip_distance_km"] = 1001.0
    ctx["is_abnormally_dormant"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# cloud_api_customer_deviation_iptype — 5-clause compound
# customer_locked_cloud_api AND is_api_booking AND NOT is_cloud_ip
# AND NOT is_datacenter_ip AND customer_observations >= 20
# ---------------------------------------------------------------------------


def test_cloud_api_deviation_full_conditions(ruleset: RuleSet) -> None:
    """Positive case AND each of the 5 conditions individually flipped
    to the non-firing side. Catches a missing AND / wrong NOT direction."""
    rule = find_rule(ruleset, "cloud_api_customer_deviation_iptype")

    def fires(**overrides: object) -> bool:
        ctx = base_ctx()
        # Set up the positive case first.
        ctx["customer_locked_cloud_api"] = True
        ctx["is_api_booking"] = True
        ctx["is_platform_booking"] = False
        ctx["is_cloud_ip"] = False
        ctx["is_datacenter_ip"] = False
        ctx["customer_observations"] = 25.0
        ctx.update(overrides)  # type: ignore[arg-type]
        return rule.evaluate(ctx)

    # Positive: all 5 clauses satisfied
    assert fires() is True
    # Each clause flipped individually must break the AND
    assert fires(customer_locked_cloud_api=False) is False
    assert fires(is_api_booking=False, is_platform_booking=True) is False
    assert fires(is_cloud_ip=True) is False
    assert fires(is_datacenter_ip=True) is False
    # observations >= 20 (boundary)
    assert fires(customer_observations=19.0) is False
    assert fires(customer_observations=20.0) is True


# ---------------------------------------------------------------------------
# locked_customer_unfamiliar_ip — 4-clause compound
# customer_locked_cloud_api AND is_api_booking AND ip_fully_new
# AND customer_observations >= 20
# ---------------------------------------------------------------------------


def test_locked_customer_unfamiliar_ip_compound(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "locked_customer_unfamiliar_ip")

    def fires(**overrides: object) -> bool:
        ctx = base_ctx()
        ctx["customer_locked_cloud_api"] = True
        ctx["is_api_booking"] = True
        ctx["is_platform_booking"] = False
        ctx["ip_fully_new"] = True
        ctx["customer_observations"] = 25.0
        ctx.update(overrides)  # type: ignore[arg-type]
        return rule.evaluate(ctx)

    assert fires() is True
    assert fires(customer_locked_cloud_api=False) is False
    assert fires(is_api_booking=False, is_platform_booking=True) is False
    assert fires(ip_fully_new=False) is False
    assert fires(customer_observations=19.0) is False


# ---------------------------------------------------------------------------
# Set-level integration
# ---------------------------------------------------------------------------


def test_dormancy_lockin_rules_load(ruleset: RuleSet) -> None:
    """All 5 dormancy + customer-lock-in rules must be present after the
    rule-loader runs. Canonical total-count audit lives in the rule-count test."""
    expected = {
        "dormant_vpn",
        "dormant_new_ip",
        "ip_distance_dormant",
        "cloud_api_customer_deviation_iptype",
        "locked_customer_unfamiliar_ip",
    }
    actual = {r.name for r in ruleset.rules}
    missing = expected - actual
    assert not missing, f"missing dormancy/lock-in rules: {missing}"
