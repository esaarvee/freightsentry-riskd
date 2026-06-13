"""Unit tests for the 8 modification rules.

Per-rule: one fire case plus 1-2 negative cases (below-threshold and/or
wrong-discriminator). Tests load the production app/rules.yaml via the
shared `ruleset` fixture and exercise each rule via `rule.evaluate(ctx)`
— production code path, no inline re-implementation of rule conditions
(false-pass lesson).

Plus two cross-cutting tests:
- test_modification_rules_dormant_under_booking_path_defaults — pins
  the invariant that NO modification rule fires under base_ctx
  (booking-path defaults from app.context.BOOKING_PATH_MODIFICATION_DEFAULTS)
- test_phase_3a_modification_rule_count — pins the count at 8

Total: 25 test cases.

base_ctx sets modification_type='none' as a neutral default (matching
production via the shared constant), so without an explicit override
these rules do not fire — implicit in every "no fire" assertion below.
"""

from __future__ import annotations

from app.rules import RuleSet
from tests.unit.conftest import base_ctx, find_rule

# ---------------------------------------------------------------------------
# modification_within_30_min_value_increase
# Condition: type==value AND time==within_30_min AND magnitude > 0.2
# Weight: 0.65, not maturity-sensitive
# ---------------------------------------------------------------------------


def test_modification_within_30_min_value_increase_fires(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "modification_within_30_min_value_increase")
    ctx = base_ctx()
    ctx["modification_type"] = "value"
    ctx["modification_time_since_booking"] = "within_30_min"
    ctx["modification_magnitude"] = 0.25
    assert rule.evaluate(ctx) is True


def test_modification_within_30_min_value_increase_no_fire_below_magnitude(
    ruleset: RuleSet,
) -> None:
    rule = find_rule(ruleset, "modification_within_30_min_value_increase")
    ctx = base_ctx()
    ctx["modification_type"] = "value"
    ctx["modification_time_since_booking"] = "within_30_min"
    ctx["modification_magnitude"] = 0.20  # boundary — strictly greater required
    assert rule.evaluate(ctx) is False


def test_modification_within_30_min_value_increase_no_fire_wrong_type(
    ruleset: RuleSet,
) -> None:
    rule = find_rule(ruleset, "modification_within_30_min_value_increase")
    ctx = base_ctx()
    ctx["modification_type"] = "destination"  # not value
    ctx["modification_time_since_booking"] = "within_30_min"
    ctx["modification_magnitude"] = 0.50
    assert rule.evaluate(ctx) is False


def test_modification_within_30_min_value_increase_no_fire_wider_window(
    ruleset: RuleSet,
) -> None:
    rule = find_rule(ruleset, "modification_within_30_min_value_increase")
    ctx = base_ctx()
    ctx["modification_type"] = "value"
    ctx["modification_time_since_booking"] = "within_1_hour"
    ctx["modification_magnitude"] = 0.50
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# modification_destination_change_pre_pickup
# Condition: type==destination AND time==within_24_hours AND direction==unfamiliar
# Weight: 0.55, maturity-sensitive
# ---------------------------------------------------------------------------


def test_modification_destination_change_pre_pickup_fires(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "modification_destination_change_pre_pickup")
    ctx = base_ctx()
    ctx["modification_type"] = "destination"
    ctx["modification_time_since_booking"] = "within_24_hours"
    ctx["modification_direction"] = "unfamiliar"
    assert rule.evaluate(ctx) is True
    assert rule.maturity_sensitive is True


def test_modification_destination_change_pre_pickup_no_fire_familiar(
    ruleset: RuleSet,
) -> None:
    rule = find_rule(ruleset, "modification_destination_change_pre_pickup")
    ctx = base_ctx()
    ctx["modification_type"] = "destination"
    ctx["modification_time_since_booking"] = "within_24_hours"
    ctx["modification_direction"] = "familiar"
    assert rule.evaluate(ctx) is False


def test_modification_destination_change_pre_pickup_no_fire_after_24h(
    ruleset: RuleSet,
) -> None:
    rule = find_rule(ruleset, "modification_destination_change_pre_pickup")
    ctx = base_ctx()
    ctx["modification_type"] = "destination"
    ctx["modification_time_since_booking"] = "1_to_7_days"
    ctx["modification_direction"] = "unfamiliar"
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# modification_high_velocity_1h
# Condition: modification_velocity_1h > 3
# Weight: 0.70, not maturity-sensitive
# ---------------------------------------------------------------------------


def test_modification_high_velocity_1h_fires_at_4(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "modification_high_velocity_1h")
    ctx = base_ctx()
    ctx["modification_velocity_1h"] = 4
    assert rule.evaluate(ctx) is True
    assert rule.maturity_sensitive is False  # campaign signal regardless of age


def test_modification_high_velocity_1h_no_fire_at_3(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "modification_high_velocity_1h")
    ctx = base_ctx()
    ctx["modification_velocity_1h"] = 3  # boundary — strictly greater required
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# modification_high_velocity_24h
# Condition: modification_velocity_24h > 10
# Weight: 0.45, maturity-sensitive
# ---------------------------------------------------------------------------


def test_modification_high_velocity_24h_fires_at_11(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "modification_high_velocity_24h")
    ctx = base_ctx()
    ctx["modification_velocity_24h"] = 11
    assert rule.evaluate(ctx) is True
    assert rule.maturity_sensitive is True


def test_modification_high_velocity_24h_no_fire_at_10(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "modification_high_velocity_24h")
    ctx = base_ctx()
    ctx["modification_velocity_24h"] = 10
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# modification_low_trust_customer
# Condition: trust_score < 0.3 AND modification_type == "destination"
# Weight: 0.55, not maturity-sensitive
# ---------------------------------------------------------------------------


def test_modification_low_trust_customer_fires(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "modification_low_trust_customer")
    ctx = base_ctx()
    ctx["trust_score"] = 0.29
    ctx["modification_type"] = "destination"
    assert rule.evaluate(ctx) is True


def test_modification_low_trust_customer_no_fire_at_trust_boundary(
    ruleset: RuleSet,
) -> None:
    rule = find_rule(ruleset, "modification_low_trust_customer")
    ctx = base_ctx()
    ctx["trust_score"] = 0.30  # boundary — strictly less than required
    ctx["modification_type"] = "destination"
    assert rule.evaluate(ctx) is False


def test_modification_low_trust_customer_no_fire_wrong_type(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "modification_low_trust_customer")
    ctx = base_ctx()
    ctx["trust_score"] = 0.10
    ctx["modification_type"] = "value"  # not destination
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# modification_dormant_customer
# Condition: is_abnormally_dormant AND modification_type == "destination"
# Weight: 0.60, maturity-sensitive
# ---------------------------------------------------------------------------


def test_modification_dormant_customer_fires(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "modification_dormant_customer")
    ctx = base_ctx()
    ctx["is_abnormally_dormant"] = True
    ctx["modification_type"] = "destination"
    assert rule.evaluate(ctx) is True
    assert rule.maturity_sensitive is True


def test_modification_dormant_customer_no_fire_not_dormant(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "modification_dormant_customer")
    ctx = base_ctx()
    ctx["is_abnormally_dormant"] = False
    ctx["modification_type"] = "destination"
    assert rule.evaluate(ctx) is False


def test_modification_dormant_customer_no_fire_value_type(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "modification_dormant_customer")
    ctx = base_ctx()
    ctx["is_abnormally_dormant"] = True
    ctx["modification_type"] = "value"
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# modification_recipient_change_to_unfamiliar
# Condition: type==recipient AND direction==unfamiliar
# Weight: 0.40, maturity-sensitive
# ---------------------------------------------------------------------------


def test_modification_recipient_change_to_unfamiliar_fires(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "modification_recipient_change_to_unfamiliar")
    ctx = base_ctx()
    ctx["modification_type"] = "recipient"
    ctx["modification_direction"] = "unfamiliar"
    assert rule.evaluate(ctx) is True
    assert rule.maturity_sensitive is True


def test_modification_recipient_change_to_unfamiliar_no_fire_familiar(
    ruleset: RuleSet,
) -> None:
    rule = find_rule(ruleset, "modification_recipient_change_to_unfamiliar")
    ctx = base_ctx()
    ctx["modification_type"] = "recipient"
    ctx["modification_direction"] = "familiar"
    assert rule.evaluate(ctx) is False


def test_modification_recipient_change_to_unfamiliar_no_fire_destination_type(
    ruleset: RuleSet,
) -> None:
    rule = find_rule(ruleset, "modification_recipient_change_to_unfamiliar")
    ctx = base_ctx()
    ctx["modification_type"] = "destination"
    ctx["modification_direction"] = "unfamiliar"
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# modification_destination_change_residential_asn
# Condition: type==destination AND is_residential_asn
# Weight: 0.35, maturity-sensitive
# ---------------------------------------------------------------------------


def test_modification_destination_change_residential_asn_fires(
    ruleset: RuleSet,
) -> None:
    rule = find_rule(ruleset, "modification_destination_change_residential_asn")
    ctx = base_ctx()
    ctx["modification_type"] = "destination"
    ctx["is_residential_asn"] = True
    assert rule.evaluate(ctx) is True
    assert rule.maturity_sensitive is True


def test_modification_destination_change_residential_asn_no_fire_non_residential(
    ruleset: RuleSet,
) -> None:
    rule = find_rule(ruleset, "modification_destination_change_residential_asn")
    ctx = base_ctx()
    ctx["modification_type"] = "destination"
    ctx["is_residential_asn"] = False
    assert rule.evaluate(ctx) is False


def test_modification_destination_change_residential_asn_no_fire_value_type(
    ruleset: RuleSet,
) -> None:
    rule = find_rule(ruleset, "modification_destination_change_residential_asn")
    ctx = base_ctx()
    ctx["modification_type"] = "value"
    ctx["is_residential_asn"] = True
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# Invariant: modification rules do NOT fire under the booking-path
# defaults (modification_type='none', velocity/magnitude zeros, time
# 'over_7_days', direction 'unknown'). Without this, every booking
# evaluation would unexpectedly trigger modification rules.
# ---------------------------------------------------------------------------


def test_modification_rules_dormant_under_booking_path_defaults(
    ruleset: RuleSet,
) -> None:
    """With base_ctx defaults (which match build_context's booking-path
    defaults), none of the 8 modification rules fire. This invariant
    must hold for the booking endpoint not to trigger modification
    rules on every booking evaluation."""
    modification_rule_names = {
        "modification_within_30_min_value_increase",
        "modification_destination_change_pre_pickup",
        "modification_high_velocity_1h",
        "modification_high_velocity_24h",
        "modification_low_trust_customer",
        "modification_dormant_customer",
        "modification_recipient_change_to_unfamiliar",
        "modification_destination_change_residential_asn",
    }
    ctx = base_ctx()
    fired = [
        rule.name
        for rule in ruleset.rules
        if rule.name in modification_rule_names and rule.evaluate(ctx)
    ]
    assert not fired, f"modification rules fired under booking defaults: {fired}"


def test_modification_rule_count(ruleset: RuleSet) -> None:
    """Pin the modification-rule count at 8."""
    modification_rules = [r for r in ruleset.rules if r.name.startswith("modification_")]
    assert len(modification_rules) == 8
