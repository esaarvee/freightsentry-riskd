"""Unit tests for app/trust.py."""

import pytest

from app.trust import compute_trust_score


def _trust(
    *,
    age: int = 0,
    obs: float = 0.0,
    flagged: int = 0,
    fraud: int = 0,
) -> float:
    return compute_trust_score(
        account_age_days=age,
        effective_observations=obs,
        flagged_count=flagged,
        fraud_confirmed_count=fraud,
    )


def test_brand_new_customer_centres_above_half() -> None:
    """No activity, no tenure, no flags → sigmoid contributions are
    small-but-positive, total above 0.5."""
    value = _trust()
    assert 0.5 <= value <= 0.6


def test_mature_customer_approaches_one() -> None:
    """High activity + long tenure + no negatives → sigmoid both
    saturate near 1, total ≈ 0.5 + 0.3 + 0.2 = 1.0."""
    value = _trust(age=365, obs=200.0)
    assert value == pytest.approx(1.0, abs=0.01)


def test_single_flag_drops_by_point_four() -> None:
    """One prior flag subtracts 0.4 regardless of count beyond 1."""
    no_flag = _trust(age=365, obs=200.0)
    one_flag = _trust(age=365, obs=200.0, flagged=1)
    five_flag = _trust(age=365, obs=200.0, flagged=5)
    assert no_flag - one_flag == pytest.approx(0.4)
    assert one_flag == five_flag  # threshold not magnitude


def test_confirmed_fraud_drops_by_point_six() -> None:
    no_fraud = _trust(age=365, obs=200.0)
    one_fraud = _trust(age=365, obs=200.0, fraud=1)
    assert no_fraud - one_fraud == pytest.approx(0.6)


def test_compounded_negatives_clamp_to_zero() -> None:
    """Flag + fraud on a brand-new account: 0.5 + small + small - 0.4 -
    0.6 < 0 → clamped to 0."""
    assert _trust(flagged=1, fraud=1) == 0.0


def test_clamped_to_unit_interval() -> None:
    """Even absurdly high inputs stay in [0, 1]."""
    extreme = _trust(age=10_000, obs=1_000.0)
    assert 0.0 <= extreme <= 1.0
    assert extreme == pytest.approx(1.0, abs=0.0001)


def test_monotone_in_observations() -> None:
    """More observations (positive history) cannot decrease trust."""
    samples = [_trust(obs=x) for x in (0, 10, 20, 50, 100)]
    assert samples == sorted(samples)


def test_monotone_in_account_age() -> None:
    samples = [_trust(age=a) for a in (0, 30, 60, 120, 365)]
    assert samples == sorted(samples)


def test_flagged_overrides_positive_contributions_partially() -> None:
    """Mature + flagged: starts at ~1.0, loses 0.4 → ~0.6."""
    value = _trust(age=365, obs=200.0, flagged=1)
    assert value == pytest.approx(0.6, abs=0.01)


def test_fraud_dominates_flagged() -> None:
    """Confirmed fraud is the strongest negative signal."""
    flag_only = _trust(age=365, obs=200.0, flagged=1)
    fraud_only = _trust(age=365, obs=200.0, fraud=1)
    assert fraud_only < flag_only
