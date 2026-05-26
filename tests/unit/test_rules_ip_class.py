"""Unit tests for the Phase 2C.3 residential-ASN + IP-class diversity rules.

Specifically tests the `(is_cloud_ip OR is_datacenter_ip)` parenthesized
sub-expression in web_booking_from_cloud_ip — Phase 1's DSL evaluator
supports arbitrary boolean trees; this is the first 2C rule to exercise
OR-precedence within parens.
"""

from __future__ import annotations

from app.rules import RuleSet
from tests.unit.conftest import base_ctx, find_rule

# ---------------------------------------------------------------------------
# residential_asn_high_velocity — is_residential_asn AND velocity_ip_hourly > 15
# ---------------------------------------------------------------------------


def test_residential_asn_high_velocity_threshold_15(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "residential_asn_high_velocity")
    ctx = base_ctx()
    ctx["is_residential_asn"] = True
    ctx["velocity_ip_hourly"] = 15
    # Strict > 15 — exactly 15 does NOT fire
    assert rule.evaluate(ctx) is False
    ctx["velocity_ip_hourly"] = 16
    assert rule.evaluate(ctx) is True
    # ASN gate
    ctx["is_residential_asn"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# api_non_cloud_ip — is_api_booking AND NOT is_cloud_ip AND NOT is_datacenter_ip
# ---------------------------------------------------------------------------


def test_api_non_cloud_ip_truth_table(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "api_non_cloud_ip")

    def fires(**overrides: object) -> bool:
        ctx = base_ctx()
        ctx["is_api_booking"] = True
        ctx["is_platform_booking"] = False
        ctx["is_cloud_ip"] = False
        ctx["is_datacenter_ip"] = False
        ctx.update(overrides)  # type: ignore[arg-type]
        return rule.evaluate(ctx)

    assert fires() is True
    assert fires(is_api_booking=False, is_platform_booking=True) is False
    assert fires(is_cloud_ip=True) is False
    assert fires(is_datacenter_ip=True) is False


# ---------------------------------------------------------------------------
# new_user_api_non_cloud — is_new_user AND is_api_booking AND NOT cloud AND NOT dc
# ---------------------------------------------------------------------------


def test_new_user_api_non_cloud_requires_new_user(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "new_user_api_non_cloud")
    ctx = base_ctx()
    ctx["is_api_booking"] = True
    ctx["is_platform_booking"] = False
    ctx["is_cloud_ip"] = False
    ctx["is_datacenter_ip"] = False
    ctx["is_new_user"] = True
    assert rule.evaluate(ctx) is True
    ctx["is_new_user"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# non_cloud_established_account — same shape but inverted on is_new_user
# ---------------------------------------------------------------------------


def test_non_cloud_established_account_excludes_new_users(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "non_cloud_established_account")

    def fires(**overrides: object) -> bool:
        ctx = base_ctx()
        ctx["is_api_booking"] = True
        ctx["is_platform_booking"] = False
        ctx["is_cloud_ip"] = False
        ctx["is_datacenter_ip"] = False
        ctx["is_new_user"] = False
        ctx.update(overrides)  # type: ignore[arg-type]
        return rule.evaluate(ctx)

    assert fires() is True
    assert fires(is_new_user=True) is False
    assert fires(is_cloud_ip=True) is False
    assert fires(is_datacenter_ip=True) is False
    assert fires(is_api_booking=False, is_platform_booking=True) is False


# ---------------------------------------------------------------------------
# web_booking_from_cloud_ip — is_platform_booking AND (is_cloud_ip OR is_datacenter_ip)
# OR-inside-parens precedence test
# ---------------------------------------------------------------------------


def test_web_booking_from_cloud_ip_or_precedence(ruleset: RuleSet) -> None:
    """Exercises the (A OR B) parenthesized sub-expression. If parens
    were dropped, AND would bind tighter and the rule would fire only
    on `is_platform_booking AND is_cloud_ip` (the OR clause would be
    silently dropped). With parens intact, EITHER cloud OR datacenter
    fires the rule (combined with web channel)."""
    rule = find_rule(ruleset, "web_booking_from_cloud_ip")
    ctx = base_ctx()
    ctx["is_platform_booking"] = True
    ctx["is_api_booking"] = False
    # Cloud only → fires (left side of OR)
    ctx["is_cloud_ip"] = True
    ctx["is_datacenter_ip"] = False
    assert rule.evaluate(ctx) is True
    # Datacenter only → fires (right side of OR — the parens make this work)
    ctx["is_cloud_ip"] = False
    ctx["is_datacenter_ip"] = True
    assert rule.evaluate(ctx) is True
    # Both → fires
    ctx["is_cloud_ip"] = True
    assert rule.evaluate(ctx) is True
    # Neither → does NOT fire
    ctx["is_cloud_ip"] = False
    ctx["is_datacenter_ip"] = False
    assert rule.evaluate(ctx) is False
    # Web channel gate: API booking + cloud → does NOT fire
    ctx["is_platform_booking"] = False
    ctx["is_api_booking"] = True
    ctx["is_cloud_ip"] = True
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# web_only_customer_using_api — customer_locked_web_only AND is_api_booking
# AND customer_observations >= 20
# ---------------------------------------------------------------------------


def test_web_only_customer_using_api_compound(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "web_only_customer_using_api")

    def fires(**overrides: object) -> bool:
        ctx = base_ctx()
        ctx["customer_locked_web_only"] = True
        ctx["is_api_booking"] = True
        ctx["is_platform_booking"] = False
        ctx["customer_observations"] = 25.0
        ctx.update(overrides)  # type: ignore[arg-type]
        return rule.evaluate(ctx)

    assert fires() is True
    assert fires(customer_locked_web_only=False) is False
    assert fires(is_api_booking=False, is_platform_booking=True) is False
    assert fires(customer_observations=19.0) is False
    assert fires(customer_observations=20.0) is True


# ---------------------------------------------------------------------------
# Set-level audit
# ---------------------------------------------------------------------------


def test_ip_class_rules_load(ruleset: RuleSet) -> None:
    """All 6 rules added in 2C.3 must be present after the rule-loader runs.
    Canonical total-count audit lives in 2C.7."""
    expected = {
        "residential_asn_high_velocity",
        "api_non_cloud_ip",
        "new_user_api_non_cloud",
        "non_cloud_established_account",
        "web_booking_from_cloud_ip",
        "web_only_customer_using_api",
    }
    actual = {r.name for r in ruleset.rules}
    missing = expected - actual
    assert not missing, f"missing IP-class rules: {missing}"
