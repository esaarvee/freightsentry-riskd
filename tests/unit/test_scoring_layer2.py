"""Unit tests for Layer 2 (account prior + trust contribution + flag prior)
and Layer 3 maturity downweight.

These tests use stub `Rule` objects to isolate the scoring formula from
the DSL + rule loader. The CustomerState argument drives Layer 2; the
ruleset drives Layer 3.
"""

from __future__ import annotations

import pytest

from app.rules import Rule, RuleSet, Thresholds
from app.scoring import CustomerState, score


def _rule(
    name: str,
    fires: bool,
    *,
    weight: float,
    maturity_sensitive: bool = False,
    action: str = "",
) -> Rule:
    return Rule(
        name=name,
        description=f"test rule {name}",
        condition=f"<test fixture: {name}>",
        weight=weight,
        action=action,  # type: ignore[arg-type]
        maturity_sensitive=maturity_sensitive,
        evaluator=lambda _ctx: fires,
    )


def _ruleset(rules: list[Rule]) -> RuleSet:
    return RuleSet(rules=tuple(rules), thresholds=Thresholds())


_brand_new = CustomerState(
    trust_score=0.5,
    account_age_days=0,
    total_shipments=0,
    flagged_count=0,
)

_established_clean = CustomerState(
    trust_score=0.95,
    account_age_days=365,
    total_shipments=100,
    flagged_count=0,
)


# ---------------------------------------------------------------------------
# Layer 1 short-circuit must bypass Layer 2 entirely
# ---------------------------------------------------------------------------


def test_layer1_short_circuit_skips_layer2() -> None:
    rs = _ruleset([_rule("blocked", True, weight=1.0, action="BLOCK")])
    result = score(rs, {}, customer_state=_brand_new)
    assert result.score == 1.0
    assert result.decision == "BLOCK"
    # Layer 2 must NOT have been computed — account_prior reported as 0.
    assert result.account_prior == 0.0
    assert result.maturity == 0.0
    assert result.signal_score == 0.0


# ---------------------------------------------------------------------------
# Account prior boundary cases
# ---------------------------------------------------------------------------


def test_brand_new_customer_account_prior_above_zero() -> None:
    rs = _ruleset([_rule("r1", False, weight=0.5)])  # no signals fire
    result = score(rs, {}, customer_state=_brand_new)
    # maturity=0; base_prior=0.10; trust_risk=(0.5-0.5)/0.5=0; flag_prior=0
    # → account_prior = noisyOR(0.10, 0, 0) = 0.10
    assert result.account_prior == pytest.approx(0.10)
    assert result.signal_score == 0.0
    assert result.score == pytest.approx(0.10)
    assert result.decision == "ALLOW"


def test_established_customer_account_prior_collapses() -> None:
    rs = _ruleset([_rule("r1", False, weight=0.5)])
    result = score(rs, {}, customer_state=_established_clean)
    # maturity=1 → base_prior=0; trust_risk=0; flag_prior=0
    assert result.account_prior == 0.0
    assert result.score == 0.0
    assert result.decision == "ALLOW"
    assert result.risk_level == "LOW"


def test_low_trust_drives_trust_contribution() -> None:
    state = CustomerState(
        trust_score=0.1,
        account_age_days=0,
        total_shipments=0,
        flagged_count=0,
    )
    rs = _ruleset([_rule("r1", False, weight=0.5)])
    result = score(rs, {}, customer_state=state)
    # trust_risk = (0.5 - 0.1) / 0.5 = 0.8
    # trust_contribution = 0.8 * 0.25 = 0.20
    # base_prior = 0.10; flag_prior = 0
    # noisyOR(0.10, 0.20, 0) = 1 - 0.9 * 0.8 = 0.28
    assert result.account_prior == pytest.approx(0.28)


def test_high_trust_zeros_trust_contribution() -> None:
    state = CustomerState(
        trust_score=0.95,
        account_age_days=0,
        total_shipments=0,
        flagged_count=0,
    )
    rs = _ruleset([_rule("r1", False, weight=0.5)])
    result = score(rs, {}, customer_state=state)
    # trust_risk clamps at 0 (max(0, -0.9) = 0)
    # → account_prior = noisyOR(0.10, 0, 0) = 0.10
    assert result.account_prior == pytest.approx(0.10)


def test_flag_tier_lookup_zero_flags() -> None:
    state = CustomerState(
        trust_score=1.0, account_age_days=365, total_shipments=100, flagged_count=0
    )
    rs = _ruleset([_rule("r1", False, weight=0.0)])
    result = score(rs, {}, customer_state=state)
    assert result.account_prior == 0.0


def test_flag_tier_lookup_one_flag() -> None:
    state = CustomerState(
        trust_score=1.0, account_age_days=365, total_shipments=100, flagged_count=1
    )
    rs = _ruleset([_rule("r1", False, weight=0.0)])
    result = score(rs, {}, customer_state=state)
    # flag tier 1 → 0.15; base_prior = 0 (mature); trust_risk = 0
    assert result.account_prior == pytest.approx(0.15)


def test_flag_tier_lookup_mid_tier() -> None:
    state = CustomerState(
        trust_score=1.0, account_age_days=365, total_shipments=100, flagged_count=4
    )
    rs = _ruleset([_rule("r1", False, weight=0.0)])
    result = score(rs, {}, customer_state=state)
    # flag tier 2 → 0.25
    assert result.account_prior == pytest.approx(0.25)


def test_flag_tier_lookup_top_tier() -> None:
    state = CustomerState(
        trust_score=1.0, account_age_days=365, total_shipments=100, flagged_count=20
    )
    rs = _ruleset([_rule("r1", False, weight=0.0)])
    result = score(rs, {}, customer_state=state)
    # flag tier 3 → 0.35
    assert result.account_prior == pytest.approx(0.35)


# ---------------------------------------------------------------------------
# Maturity downweight on Layer 3
# ---------------------------------------------------------------------------


def test_maturity_downweight_on_sensitive_rule_brand_new() -> None:
    rs = _ruleset([_rule("r1", True, weight=0.40, maturity_sensitive=True)])
    result = score(rs, {}, customer_state=_brand_new)
    # maturity = 0 → effective weight = 0.40 * (1 - 0.30 * 1) = 0.40 * 0.70 = 0.28
    assert result.signal_score == pytest.approx(0.28)
    assert result.risk_factors[0].weight == pytest.approx(0.28)


def test_maturity_downweight_on_sensitive_rule_mature() -> None:
    rs = _ruleset([_rule("r1", True, weight=0.40, maturity_sensitive=True)])
    result = score(rs, {}, customer_state=_established_clean)
    # maturity = 1 → effective weight = 0.40 * (1 - 0.30 * 0) = 0.40
    assert result.signal_score == pytest.approx(0.40)
    assert result.risk_factors[0].weight == pytest.approx(0.40)


def test_maturity_not_applied_when_flag_false() -> None:
    rs = _ruleset([_rule("r1", True, weight=0.40, maturity_sensitive=False)])
    result = score(rs, {}, customer_state=_brand_new)
    # maturity_sensitive=False → no downweight even with maturity=0
    assert result.signal_score == pytest.approx(0.40)
    assert result.risk_factors[0].weight == pytest.approx(0.40)


# ---------------------------------------------------------------------------
# Layer 2 / Layer 3 composition
# ---------------------------------------------------------------------------


def test_layer2_layer3_compose_via_noisy_or() -> None:
    rs = _ruleset([_rule("r1", True, weight=0.40, maturity_sensitive=False)])
    result = score(rs, {}, customer_state=_brand_new)
    # account_prior = 0.10 (brand-new, neutral trust, no flags)
    # signal_score = 0.40 (rule fires, not maturity_sensitive)
    # final = noisyOR(0.10, 0.40) = 1 - 0.9 * 0.6 = 0.46
    assert result.account_prior == pytest.approx(0.10)
    assert result.signal_score == pytest.approx(0.40)
    assert result.score == pytest.approx(0.46)


def test_no_layer3_rules_fired_uses_account_prior_only() -> None:
    rs = _ruleset([_rule("r1", False, weight=0.50)])
    result = score(rs, {}, customer_state=_brand_new)
    assert result.signal_score == 0.0
    assert result.score == pytest.approx(0.10)
    assert result.decision == "ALLOW"


def test_no_account_prior_and_no_rules_returns_zero() -> None:
    rs = _ruleset([_rule("r1", False, weight=0.50)])
    result = score(rs, {}, customer_state=_established_clean)
    assert result.account_prior == 0.0
    assert result.signal_score == 0.0
    assert result.score == 0.0
    assert result.decision == "ALLOW"
    assert result.risk_level == "LOW"


def test_highly_flagged_low_trust_brand_new_pushes_account_prior_high() -> None:
    state = CustomerState(
        trust_score=0.1,
        account_age_days=0,
        total_shipments=0,
        flagged_count=10,
    )
    rs = _ruleset([_rule("r1", False, weight=0.5)])
    result = score(rs, {}, customer_state=state)
    # base_prior = 0.10
    # trust_contribution = 0.8 * 0.25 = 0.20
    # flag_prior = 0.35 (tier 3)
    # noisyOR(0.10, 0.20, 0.35) = 1 - 0.9 * 0.8 * 0.65 = 1 - 0.468 = 0.532
    assert result.account_prior == pytest.approx(0.532)
    # Final score equals account_prior since no Layer 3 rule fires.
    assert result.score == pytest.approx(0.532)
    # Below allow_max (0.60) → ALLOW band still
    assert result.decision == "ALLOW"
    # Account prior alone doesn't tip a customer to REVIEW even at extremes
    # (the tip happens when signals compound). Sanity check.


def test_maturity_field_exposed_on_result() -> None:
    rs = _ruleset([_rule("r1", False, weight=0.0)])
    result_new = score(rs, {}, customer_state=_brand_new)
    result_mature = score(rs, {}, customer_state=_established_clean)
    assert result_new.maturity == 0.0
    assert result_mature.maturity == pytest.approx(1.0)
