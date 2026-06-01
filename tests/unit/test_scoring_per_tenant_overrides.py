"""Unit tests for per-tenant maturity overrides in score() (4C.1).

Covers:
- Empty tenant_config produces identical output to pre-4C scoring
- maturity_age_days override changes maturity computation
- maturity_shipments override changes maturity computation
- maturity_k override changes the Layer 3 downweight magnitude
- All three overrides composed together
- Layer 1 BLOCK short-circuit does NOT consult tenant_config
- Layer 2 base_prior reflects the resolved m
- trust_contribution and flag_prior are independent of maturity overrides
- maturity_sensitive rule downweight uses the resolved k
- Non-maturity-sensitive rule weight is unchanged regardless of overrides
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from app.rules import Rule, RuleSet, Thresholds
from app.scoring import CustomerState, _resolved_maturity_constants, score
from app.scoring_constants import MATURITY_AGE_DAYS, MATURITY_K, MATURITY_SHIPMENTS
from app.tenant_config import TenantConfig
from tests.conftest import make_default_tenant_config


def _tc(
    *,
    maturity_age_days: int | None = None,
    maturity_shipments: int | None = None,
    maturity_k: float | None = None,
) -> TenantConfig:
    now = datetime.now(UTC)
    return TenantConfig(
        tenant_id=1,
        maturity_age_days=maturity_age_days,
        maturity_shipments=maturity_shipments,
        maturity_k=maturity_k,
        created_at=now,
        updated_at=now,
    )


def _ruleset_with(rules: list[Rule]) -> RuleSet:
    return RuleSet(rules=tuple(rules), thresholds=Thresholds())


def _always_true_rule(name: str, weight: float, *, maturity_sensitive: bool) -> Rule:
    return Rule(
        name=name,
        description=name,
        condition="True",
        weight=weight,
        action="",
        maturity_sensitive=maturity_sensitive,
        evaluator=lambda _ctx: True,
    )


def _block_rule(name: str) -> Rule:
    return Rule(
        name=name,
        description=name,
        condition="True",
        weight=1.0,
        action="BLOCK",
        maturity_sensitive=False,
        evaluator=lambda _ctx: True,
    )


# ---------------------------------------------------------------------------


def test_resolved_constants_returns_defaults_for_empty_config() -> None:
    age, ship, k = _resolved_maturity_constants(_tc())
    assert age == MATURITY_AGE_DAYS
    assert ship == MATURITY_SHIPMENTS
    assert k == MATURITY_K


def test_resolved_constants_returns_overrides_when_set() -> None:
    age, ship, k = _resolved_maturity_constants(
        _tc(maturity_age_days=90, maturity_shipments=20, maturity_k=0.10)
    )
    assert age == 90
    assert ship == 20
    assert k == 0.10


def test_empty_tenant_config_identical_to_pre_4c() -> None:
    """A booking under empty tenant_config produces the same maturity value
    as the standalone maturity() helper does for the same customer."""
    from app.scoring_constants import maturity as project_maturity

    ruleset = _ruleset_with([])
    cs = CustomerState(trust_score=0.8, account_age_days=180, total_shipments=50, flagged_count=0)
    result = score(ruleset, {}, customer_state=cs, tenant_config=make_default_tenant_config())
    assert result.maturity == project_maturity(cs.account_age_days, cs.total_shipments)
    assert result.maturity == 1.0  # mature


def test_maturity_age_days_override_changes_maturity() -> None:
    ruleset = _ruleset_with([])
    cs = CustomerState(trust_score=0.8, account_age_days=60, total_shipments=50, flagged_count=0)
    default = score(ruleset, {}, customer_state=cs, tenant_config=_tc())
    override = score(ruleset, {}, customer_state=cs, tenant_config=_tc(maturity_age_days=90))
    # Default (180): m = (60/180)*(50/50) = 0.333
    # Override (90):  m = (60/90)*(50/50) = 0.667
    assert default.maturity < override.maturity
    assert abs(override.maturity - (60 / 90) * 1.0) < 1e-9


def test_maturity_shipments_override_changes_maturity() -> None:
    ruleset = _ruleset_with([])
    cs = CustomerState(trust_score=0.8, account_age_days=180, total_shipments=10, flagged_count=0)
    default = score(ruleset, {}, customer_state=cs, tenant_config=_tc())
    override = score(ruleset, {}, customer_state=cs, tenant_config=_tc(maturity_shipments=10))
    # Default (50): m = 1.0 * 10/50 = 0.2
    # Override (10): m = 1.0 * 1.0 = 1.0
    assert default.maturity < override.maturity
    assert override.maturity == 1.0


def test_maturity_k_override_changes_layer_3_downweight() -> None:
    """For a maturity-sensitive rule weight=1.0 at m=0.5:
      effective = 1.0 * (1 - k * (1 - 0.5)) = 1.0 * (1 - 0.5*k)
    Default k=0.30: effective = 1.0 * 0.85 = 0.85
    Override k=0.10: effective = 1.0 * 0.95 = 0.95
    Lower K = less aggressive downweight = higher effective weight = higher
    signal_score for the same firing rule.
    """
    rule = _always_true_rule("test", 1.0, maturity_sensitive=True)
    ruleset = _ruleset_with([rule])
    cs = CustomerState(trust_score=0.8, account_age_days=90, total_shipments=25, flagged_count=0)
    # m = (90/180) * (25/50) = 0.25 — both runs produce m=0.25 because
    # only maturity_k is overridden, not the maturity-formula thresholds.
    default = score(ruleset, {}, customer_state=cs, tenant_config=_tc())
    override = score(ruleset, {}, customer_state=cs, tenant_config=_tc(maturity_k=0.10))
    # Override has SMALLER k → less downweight → HIGHER signal_score.
    assert override.signal_score > default.signal_score


def test_all_three_overrides_composed() -> None:
    rule = _always_true_rule("test", 0.6, maturity_sensitive=True)
    ruleset = _ruleset_with([rule])
    cs = CustomerState(trust_score=0.8, account_age_days=60, total_shipments=20, flagged_count=0)
    tc = _tc(maturity_age_days=90, maturity_shipments=20, maturity_k=0.20)
    result = score(ruleset, {}, customer_state=cs, tenant_config=tc)
    # m = (60/90) * (20/20) = 0.667
    expected_m = (60 / 90) * 1.0
    assert abs(result.maturity - expected_m) < 1e-9


def test_layer_1_short_circuit_does_not_consult_tenant_config() -> None:
    """Layer 1 BLOCK rule fires → score returns 1.0 BEFORE Layer 2/3.
    tenant_config should NOT be consulted on the fast-path. Pinned via
    a patch on _resolved_maturity_constants to assert it is NEVER called.
    """
    ruleset = _ruleset_with([_block_rule("hard_block")])
    cs = CustomerState(trust_score=0.8, account_age_days=180, total_shipments=50, flagged_count=0)
    with patch("app.scoring._resolved_maturity_constants") as mock_resolver:
        result = score(ruleset, {}, customer_state=cs, tenant_config=_tc(maturity_age_days=999))
    assert result.decision == "BLOCK"
    assert result.score == 1.0
    mock_resolver.assert_not_called()


def test_layer_2_base_prior_uses_resolved_m() -> None:
    """base_prior = MAX_NEW_ACCOUNT * (1 - m). A lower threshold means a
    HIGHER m, which means a LOWER base_prior, which means a LOWER
    account_prior for the same customer/trust/flags."""
    ruleset = _ruleset_with([])
    cs = CustomerState(trust_score=0.8, account_age_days=30, total_shipments=50, flagged_count=0)
    default = score(ruleset, {}, customer_state=cs, tenant_config=_tc())
    override = score(ruleset, {}, customer_state=cs, tenant_config=_tc(maturity_age_days=90))
    # Override has shorter age threshold → higher m → lower (1-m) → lower base_prior.
    assert override.account_prior < default.account_prior


def test_trust_contribution_and_flag_prior_independent_of_maturity_overrides() -> None:
    """When customer is fully mature under both default and override
    (m=1.0 in both runs), account_prior matches because trust + flag terms
    are independent of m and base_prior=0 at m=1."""
    ruleset = _ruleset_with([])
    cs = CustomerState(trust_score=0.2, account_age_days=365, total_shipments=200, flagged_count=4)
    default = score(ruleset, {}, customer_state=cs, tenant_config=_tc())
    override = score(ruleset, {}, customer_state=cs, tenant_config=_tc(maturity_k=0.50))
    assert default.account_prior == override.account_prior


def test_non_maturity_sensitive_rule_weight_unchanged_by_overrides() -> None:
    """A non-maturity-sensitive rule contributes its raw weight regardless
    of maturity_k overrides."""
    rule = _always_true_rule("flat", 0.4, maturity_sensitive=False)
    ruleset = _ruleset_with([rule])
    cs = CustomerState(trust_score=0.8, account_age_days=30, total_shipments=10, flagged_count=0)
    default = score(ruleset, {}, customer_state=cs, tenant_config=_tc())
    override = score(ruleset, {}, customer_state=cs, tenant_config=_tc(maturity_k=0.10))
    # Signal score uses the raw weight → identical between runs.
    assert default.signal_score == override.signal_score


def test_multiple_maturity_sensitive_rules_use_same_resolved_k() -> None:
    """When multiple maturity_sensitive rules fire, all of them use the
    same resolved k value (per call, not per rule)."""
    rules = [
        _always_true_rule("a", 0.3, maturity_sensitive=True),
        _always_true_rule("b", 0.5, maturity_sensitive=True),
    ]
    ruleset = _ruleset_with(rules)
    cs = CustomerState(trust_score=0.8, account_age_days=90, total_shipments=25, flagged_count=0)
    tc = _tc(maturity_k=0.10)
    result = score(ruleset, {}, customer_state=cs, tenant_config=tc)
    # Both rules' effective weights should reflect k=0.10 and m=0.25:
    #   a: 0.3 * (1 - 0.10 * 0.75) = 0.3 * 0.925 = 0.2775
    #   b: 0.5 * (1 - 0.10 * 0.75) = 0.5 * 0.925 = 0.4625
    weights = {rf.name: rf.weight for rf in result.risk_factors}
    assert abs(weights["a"] - 0.3 * (1 - 0.10 * 0.75)) < 1e-9
    assert abs(weights["b"] - 0.5 * (1 - 0.10 * 0.75)) < 1e-9
