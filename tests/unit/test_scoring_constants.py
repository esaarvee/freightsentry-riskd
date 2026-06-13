"""Unit tests for Layer 2 scoring constants + maturity helper.

Boundary cases against the formula recorded in `.ai/decisions.md` §
Scoring architecture. The maturity formula is
intentionally multiplicative (Design Context) rather than min-of-fractions
(FreightSentry scorer.go) — the test_maturity_multiplicative_form case
documents the divergence.
"""

from __future__ import annotations

from app.scoring_constants import (
    FLAG_WEIGHTS,
    MATURITY_AGE_DAYS,
    MATURITY_K,
    MATURITY_SHIPMENTS,
    MAX_NEW_ACCOUNT,
    TRUST_FACTOR,
    flagged_count_tier,
    maturity,
)


def test_maturity_zero_when_brand_new() -> None:
    assert maturity(0, 0) == 0.0


def test_maturity_one_when_saturated() -> None:
    assert maturity(MATURITY_AGE_DAYS, MATURITY_SHIPMENTS) == 1.0


def test_maturity_one_when_over_saturated() -> None:
    assert maturity(MATURITY_AGE_DAYS * 2, MATURITY_SHIPMENTS * 4) == 1.0


def test_maturity_clamps_negative_inputs() -> None:
    assert maturity(-10, -5) == 0.0


def test_maturity_multiplicative_form() -> None:
    # age_frac = 90/180 = 0.5; ship_frac = 25/50 = 0.5
    # Multiplicative product: 0.5 * 0.5 = 0.25
    # (FreightSentry's min-form would return 0.5; we explicitly differ.)
    assert maturity(90, 25) == 0.25


def test_maturity_dominated_by_lesser_factor() -> None:
    # age saturated at 1.0, ship_frac = 10/50 = 0.2 → product = 0.2
    assert maturity(MATURITY_AGE_DAYS, 10) == 0.2


def test_flagged_count_tier_boundaries() -> None:
    assert flagged_count_tier(0) == 0
    assert flagged_count_tier(1) == 1
    assert flagged_count_tier(2) == 1
    assert flagged_count_tier(3) == 2
    assert flagged_count_tier(5) == 2
    assert flagged_count_tier(6) == 3
    assert flagged_count_tier(1000) == 3


def test_flagged_count_tier_negative_clamps_to_zero() -> None:
    assert flagged_count_tier(-5) == 0


def test_flag_weights_table_length_matches_tier_count() -> None:
    assert len(FLAG_WEIGHTS) == 4


def test_constants_locked_to_design_context() -> None:
    """Sanity-check the locked constants match `.ai/decisions.md`. A
    change here is a Design Context amendment, never a silent edit."""
    assert MAX_NEW_ACCOUNT == 0.10
    assert TRUST_FACTOR == 0.25
    assert MATURITY_AGE_DAYS == 180
    assert MATURITY_SHIPMENTS == 50
    assert MATURITY_K == 0.30
    assert FLAG_WEIGHTS == (0.00, 0.15, 0.25, 0.35)


def test_flag_weights_immutable_tuple() -> None:
    assert isinstance(FLAG_WEIGHTS, tuple)
