"""Unit tests for the Phase 2C.7 closing-batch rule additions.

Five rules: 3 IP-familiarity-tier-conditioned, value_novelty_compound,
and a narrower locked-customer compound. The canonical Phase-2-end
total-rule-count audit also lives here (test_phase2_end_total_rule_count).

Triaged from this commit (per plan, with reason in YAML comment):
- unknown_email_for_customer / unknown_phone_for_customer: require
  proxy-field adaptation; defer to Phase 3+
- user_ip_rotation_elevated / user_ip_rotation_high: semantic mismatch
  between customer_distinct_ips_30d (all IPs, 30d) and freight_risk's
  user_unique_non_cloud_ips_daily; defer
"""

from __future__ import annotations

from app.rules import RuleSet
from tests.unit.conftest import base_ctx, find_rule

# ---------------------------------------------------------------------------
# ip_family_familiar_cloud — ip_family_familiar AND is_cloud_ip
# AND customer_observations >= 10. Low-weight signal (0.05) — known
# /24, known infrastructure.
# ---------------------------------------------------------------------------


def test_ip_family_familiar_cloud_three_clauses(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "ip_family_familiar_cloud")
    ctx = base_ctx()
    ctx["ip_family_familiar"] = True
    ctx["is_cloud_ip"] = True
    ctx["customer_observations"] = 15.0
    assert rule.evaluate(ctx) is True
    # Each gate broken individually
    ctx["ip_family_familiar"] = False
    assert rule.evaluate(ctx) is False
    ctx["ip_family_familiar"] = True
    ctx["is_cloud_ip"] = False
    assert rule.evaluate(ctx) is False
    ctx["is_cloud_ip"] = True
    ctx["customer_observations"] = 9.0
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# ip_family_familiar_residential — same shape but NOT cloud
# ---------------------------------------------------------------------------


def test_ip_family_familiar_residential_three_clauses(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "ip_family_familiar_residential")
    ctx = base_ctx()
    ctx["ip_family_familiar"] = True
    ctx["is_cloud_ip"] = False
    ctx["customer_observations"] = 15.0
    assert rule.evaluate(ctx) is True
    # Cloud IP flips off the firing (NOT is_cloud_ip in condition)
    ctx["is_cloud_ip"] = True
    assert rule.evaluate(ctx) is False
    # Observations gate
    ctx["is_cloud_ip"] = False
    ctx["customer_observations"] = 9.0
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# ip_new_known_asn_rule — ip_new_known_asn AND customer_observations >= 20
# ---------------------------------------------------------------------------


def test_ip_new_known_asn_rule_requires_observations_gate(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "ip_new_known_asn_rule")
    ctx = base_ctx()
    ctx["ip_new_known_asn"] = True
    ctx["customer_observations"] = 20.0
    assert rule.evaluate(ctx) is True
    # Strict >= 20 boundary (19 fails, 20 fires)
    ctx["customer_observations"] = 19.0
    assert rule.evaluate(ctx) is False
    # Without the tier flag
    ctx["customer_observations"] = 25.0
    ctx["ip_new_known_asn"] = False
    assert rule.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# value_novelty_compound — value_zscore > 1.5 AND ip_fully_new
# AND customer_observations >= 20
# ---------------------------------------------------------------------------


def test_value_novelty_compound_three_clauses(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "value_novelty_compound")

    def fires(**overrides: object) -> bool:
        ctx = base_ctx()
        ctx["value_zscore"] = 1.6
        ctx["ip_fully_new"] = True
        ctx["customer_observations"] = 25.0
        ctx.update(overrides)  # type: ignore[arg-type]
        return rule.evaluate(ctx)

    assert fires() is True
    # Strict > 1.5 — exactly 1.5 must NOT fire
    assert fires(value_zscore=1.5) is False
    assert fires(ip_fully_new=False) is False
    assert fires(customer_observations=19.0) is False


# ---------------------------------------------------------------------------
# locked_customer_new_ip_family — narrower variant of locked_customer_*
# ---------------------------------------------------------------------------


def test_locked_customer_new_ip_family_compound(ruleset: RuleSet) -> None:
    rule = find_rule(ruleset, "locked_customer_new_ip_family")

    def fires(**overrides: object) -> bool:
        ctx = base_ctx()
        ctx["customer_locked_cloud_api"] = True
        ctx["is_api_booking"] = True
        ctx["is_platform_booking"] = False
        ctx["ip_new_known_asn"] = True
        ctx["customer_observations"] = 25.0
        ctx.update(overrides)  # type: ignore[arg-type]
        return rule.evaluate(ctx)

    assert fires() is True
    assert fires(customer_locked_cloud_api=False) is False
    assert fires(is_api_booking=False, is_platform_booking=True) is False
    assert fires(ip_new_known_asn=False) is False
    assert fires(customer_observations=19.0) is False


# ---------------------------------------------------------------------------
# Set-level audit + canonical Phase 2 end total-count
# ---------------------------------------------------------------------------


def test_familiarity_diversity_rules_load(ruleset: RuleSet) -> None:
    """All 5 rules added in 2C.7 must be present after rule-loader runs."""
    expected = {
        "ip_family_familiar_cloud",
        "ip_family_familiar_residential",
        "ip_new_known_asn_rule",
        "value_novelty_compound",
        "locked_customer_new_ip_family",
    }
    actual = {r.name for r in ruleset.rules}
    missing = expected - actual
    assert not missing, f"missing 2C.7 rules: {missing}"


def test_phase2_end_total_rule_count(ruleset: RuleSet) -> None:
    """Canonical rule-count audit. Updated each phase to reflect cumulative
    additions; drift fails this test as the single source of truth that
    per-batch set-membership tests match the production catalogue.

    Phase 1 baseline:  14 rules
    2C.1 trust-conditioned:                       +7 =  21
    2C.2 dormancy + customer lock-in:             +5 =  26
    2C.3 residential ASN + IP-class diversity:    +6 =  32
    2C.4 recipient overlap:                       +2 =  34
    2C.5 velocity + identity-novelty:            +11 =  45
    2C.6 value-anomaly + geographic + threat:    +17 =  62
    2C.7 familiarity-tier + closing pieces:       +5 =  67
    3A.7 modification rules:                      +8 =  75
    3B.5 previously-rejected rules:               +4 =  79
    6A.3 case_3_compound:                         +1 =  80
    6A.5 cold_start_country_triangle:             +1 =  81
    6A.9 cold_start_population_baseline_rare:     +1 =  82
    7C.2 swap cold_start_country_triangle for
         cold_start_outbound_carrier_dropoff:     net 0 = 82
    7C.7 delete api_non_cloud_ip +
         non_cloud_established_account, add
         api_booking_from_unfamiliar_asn:         net -1 = 81
    """
    assert len(ruleset.rules) == 81


def test_phase2_end_no_duplicate_rule_names(ruleset: RuleSet) -> None:
    """No rule name appears more than once — catches accidental
    copy-paste duplication that the set-membership tests would silently
    miss."""
    names = [r.name for r in ruleset.rules]
    assert len(names) == len(set(names)), (
        f"duplicate rule names found: {[n for n in names if names.count(n) > 1]}"
    )
