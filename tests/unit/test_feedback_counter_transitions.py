"""Exhaustive transition-matrix tests for the feedback counter helpers.

`_compute_counter_deltas` and `_label_stronger` together govern when a
feedback POST updates customers.flagged_count / fraud_confirmed_count
and when monotonicity blocks an attempted label downgrade. The matrix
is small (3 labels x 3 prior labels + None) but the correctness is
load-bearing: a regression here would silently double-count or skip
flags on production data. Each cell pinned.
"""

from __future__ import annotations

import pytest

from app.api.feedback import _compute_counter_deltas, _label_stronger

# ---------------------------------------------------------------------------
# _compute_counter_deltas — 9 logically-valid (prior, new) cells
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prior,new,expected_flag,expected_fraud",
    [
        # First-ever feedback (prior=None)
        (None, "approved", 0, 0),
        (None, "rejected", 1, 0),
        (None, "fraud_confirmed", 1, 1),
        # Upgrades from prior approved
        ("approved", "rejected", 1, 0),
        ("approved", "fraud_confirmed", 1, 1),
        # Upgrade from prior rejected
        ("rejected", "fraud_confirmed", 0, 1),
        # Same-label no-ops (would be blocked by _label_stronger upstream
        # but the helper itself must still compute 0/0 deterministically)
        ("approved", "approved", 0, 0),
        ("rejected", "rejected", 0, 0),
        ("fraud_confirmed", "fraud_confirmed", 0, 0),
    ],
)
def test_counter_deltas(
    prior: str | None, new: str, expected_flag: int, expected_fraud: int
) -> None:
    assert _compute_counter_deltas(prior, new) == (expected_flag, expected_fraud)


# ---------------------------------------------------------------------------
# _label_stronger — blocked downgrades + allowed upgrades + first-ever
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "new,prior",
    [
        # Strict downgrades — monotonicity must block
        ("approved", "rejected"),
        ("approved", "fraud_confirmed"),
        ("rejected", "fraud_confirmed"),
    ],
)
def test_label_monotonicity_blocks_downgrade(new: str, prior: str) -> None:
    assert _label_stronger(new=new, prior=prior) is False


@pytest.mark.parametrize(
    "new,prior",
    [
        # First-ever feedback always applies
        ("approved", None),
        ("rejected", None),
        ("fraud_confirmed", None),
        # Strict upgrades
        ("rejected", "approved"),
        ("fraud_confirmed", "approved"),
        ("fraud_confirmed", "rejected"),
    ],
)
def test_label_monotonicity_allows_upgrade_or_first(new: str, prior: str | None) -> None:
    assert _label_stronger(new=new, prior=prior) is True


@pytest.mark.parametrize("label", ["approved", "rejected", "fraud_confirmed"])
def test_label_monotonicity_same_label_does_not_apply(label: str) -> None:
    """Same-label POST is a no-op — neither an upgrade nor a downgrade.
    `_label_stronger` returns False so the endpoint short-circuits to
    applied=False without re-running baseline / counter writes."""
    assert _label_stronger(new=label, prior=label) is False
