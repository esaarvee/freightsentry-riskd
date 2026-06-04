"""Unit tests for the 4 Phase 3B.5 previously-rejected rules.

Per rule: one fire case (the corresponding *_previously_rejected
context field set to True) and one no-fire case (False). All 4 rules
are maturity-sensitive — pinned inline alongside the fire case so a
YAML config drift fails per-rule rather than only surfacing in scoring
integration tests.

Tests call rule.evaluate(ctx) directly — production code path; no
inline re-implementation of rule conditions (Phase 2 false-pass
lesson).
"""

from __future__ import annotations

from app.rules import RuleSet
from tests.unit.conftest import base_ctx, find_rule

# ---------------------------------------------------------------------------
# email_previously_rejected_for_customer
# Condition: email_previously_rejected
# Weight: 0.60, maturity-sensitive
# ---------------------------------------------------------------------------


def test_email_previously_rejected_fires_when_true(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "email_previously_rejected_for_customer")
    ctx = base_ctx()
    ctx["email_previously_rejected"] = True
    assert rule.evaluate(ctx) is True
    assert rule.maturity_sensitive is True
    assert rule.weight == 0.60


def test_email_previously_rejected_no_fire_when_false(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "email_previously_rejected_for_customer")
    ctx = base_ctx()
    ctx["email_previously_rejected"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# phone_previously_rejected_for_customer (mirror)
# ---------------------------------------------------------------------------


def test_phone_previously_rejected_fires_when_true(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "phone_previously_rejected_for_customer")
    ctx = base_ctx()
    ctx["phone_previously_rejected"] = True
    assert rule.evaluate(ctx) is True
    assert rule.maturity_sensitive is True
    assert rule.weight == 0.60


def test_phone_previously_rejected_no_fire_when_false(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "phone_previously_rejected_for_customer")
    ctx = base_ctx()
    ctx["phone_previously_rejected"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# origin_previously_rejected_for_customer
# Condition: origin_previously_rejected
# Weight: 0.70, maturity-sensitive (higher than email/phone — physical
# address re-use is a stronger signal than contact-info re-use)
# ---------------------------------------------------------------------------


def test_origin_previously_rejected_fires_when_true(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "origin_previously_rejected_for_customer")
    ctx = base_ctx()
    ctx["origin_previously_rejected"] = True
    assert rule.evaluate(ctx) is True
    assert rule.maturity_sensitive is True
    assert rule.weight == 0.70


def test_origin_previously_rejected_no_fire_when_false(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "origin_previously_rejected_for_customer")
    ctx = base_ctx()
    ctx["origin_previously_rejected"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# ip_previously_rejected_for_customer (mirror of origin — same weight band)
# ---------------------------------------------------------------------------


def test_ip_previously_rejected_fires_when_true(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "ip_previously_rejected_for_customer")
    ctx = base_ctx()
    ctx["ip_previously_rejected"] = True
    assert rule.evaluate(ctx) is True
    assert rule.maturity_sensitive is True
    assert rule.weight == 0.70


def test_ip_previously_rejected_no_fire_when_false(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "ip_previously_rejected_for_customer")
    ctx = base_ctx()
    ctx["ip_previously_rejected"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# Cross-cutting invariants
# ---------------------------------------------------------------------------


def test_previously_rejected_rule_count(ruleset: RuleSet) -> None:
    """Pin the previously-rejected rule count at 4."""
    rule_names = {r.name for r in ruleset.rules if "previously_rejected" in r.name}
    expected = {
        "email_previously_rejected_for_customer",
        "phone_previously_rejected_for_customer",
        "origin_previously_rejected_for_customer",
        "ip_previously_rejected_for_customer",
    }
    assert rule_names == expected


def test_previously_rejected_rules_dormant_under_clean_baseline(
    ruleset: RuleSet,
) -> None:
    """With base_ctx defaults (all 4 fields=False — matches build_context
    populated against an empty/clean baseline), none of the 4 previously-
    rejected rules fire. Booking-flow invariant: a customer with no prior
    rejections never triggers these rules."""
    previously_rejected_rule_names = {
        "email_previously_rejected_for_customer",
        "phone_previously_rejected_for_customer",
        "origin_previously_rejected_for_customer",
        "ip_previously_rejected_for_customer",
    }
    ctx = base_ctx()
    fired = [
        rule.name
        for rule in ruleset.rules
        if rule.name in previously_rejected_rule_names and rule.evaluate(ctx)
    ]
    assert not fired, f"previously-rejected rules fired under clean defaults: {fired}"
