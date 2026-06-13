"""Unit tests for the residential-ASN + IP-class diversity rules.

Specifically tests the `(is_cloud_ip OR is_datacenter_ip)` parenthesized
sub-expression in web_booking_from_cloud_ip — the DSL evaluator
supports arbitrary boolean trees; this is the first rule to exercise
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
# api_booking_from_unfamiliar_asn (replacement for the deleted
# api_non_cloud_ip + non_cloud_established_account pair) —
# is_api_booking AND unfamiliar_asn_for_customer
# ---------------------------------------------------------------------------


def test_api_booking_from_unfamiliar_asn_truth_table(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "api_booking_from_unfamiliar_asn")

    def fires(**overrides: object) -> bool:
        ctx = base_ctx()
        ctx["is_api_booking"] = True
        ctx["is_platform_booking"] = False
        ctx["unfamiliar_asn_for_customer"] = True
        ctx.update(overrides)  # type: ignore[arg-type]
        return rule.evaluate(ctx)

    assert fires() is True
    assert fires(is_api_booking=False, is_platform_booking=True) is False
    assert fires(unfamiliar_asn_for_customer=False) is False


def test_api_booking_from_unfamiliar_asn_weight_and_maturity(ruleset: RuleSet) -> None:
    """Weight 0.65 places the rule in REVIEW band standalone (just
    below BLOCK 0.80). maturity_sensitive false because the cold-start
    gate (customer_observations >= 10) is INSIDE the
    _asn_unfamiliar_for_customer derivation — downweighting via
    maturity would suppress the signal we use to flag the threat."""
    rule = find_rule(ruleset, "api_booking_from_unfamiliar_asn")
    assert rule.weight == 0.65
    assert rule.maturity_sensitive is False
    assert rule.action == ""


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
    """All 6 IP-class rules must be present after the rule-loader runs.
    Canonical total-count audit lives in the rule-count test."""
    expected = {
        "residential_asn_high_velocity",
        # api_non_cloud_ip + non_cloud_established_account replaced by
        # api_booking_from_unfamiliar_asn.
        "api_booking_from_unfamiliar_asn",
        "new_user_api_non_cloud",
        "web_booking_from_cloud_ip",
        "web_only_customer_using_api",
    }
    actual = {r.name for r in ruleset.rules}
    missing = expected - actual
    assert not missing, f"missing IP-class rules: {missing}"
    # Pin deletion: a future accidental revive of the symmetric pair
    # surfaces here.
    assert "api_non_cloud_ip" not in actual
    assert "non_cloud_established_account" not in actual
