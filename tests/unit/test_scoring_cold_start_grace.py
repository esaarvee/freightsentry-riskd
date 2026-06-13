"""Unit tests for the cold-start grace mechanism in scoring.

8 tests covering:
- grace=0 returns maturity unchanged
- grace=7, created 3 days ago: returns maturity * 0.5
- grace=7, created exactly 7 days ago: returns maturity unchanged (boundary)
- grace=7, created 8 days ago: returns maturity unchanged (past window)
- grace=30, created 1 day ago: returns maturity * 0.5
- maturity=0.0 + grace active: still 0.0
- maturity=1.0 + grace active: 0.5
- Integration with score(): grace-active tenant + mature customer →
  ScoringResult.maturity is halved → base_prior elevated
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.rules import RuleSet, Thresholds
from app.scoring import (
    CustomerState,
    _apply_cold_start_grace,
    score,
)
from app.scoring_constants import MAX_NEW_ACCOUNT
from app.tenant_config import TenantConfig


def _tc(*, grace: int, created_days_ago: int) -> TenantConfig:
    now = datetime.now(UTC)
    return TenantConfig(
        tenant_id=1,
        cold_start_grace_days=grace,
        created_at=now - timedelta(days=created_days_ago),
        updated_at=now,
    )


def _fixed_now() -> datetime:
    return datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def test_grace_zero_returns_maturity_unchanged() -> None:
    tc = _tc(grace=0, created_days_ago=0)
    assert _apply_cold_start_grace(0.8, tc, now=_fixed_now()) == 0.8


def test_grace_active_within_window_halves_maturity() -> None:
    tc = TenantConfig(
        tenant_id=1,
        cold_start_grace_days=7,
        created_at=_fixed_now() - timedelta(days=3),
        updated_at=_fixed_now(),
    )
    assert _apply_cold_start_grace(0.8, tc, now=_fixed_now()) == 0.4


def test_grace_boundary_exact_days_returns_unchanged() -> None:
    """elapsed == grace -> NOT within window (strict <)."""
    tc = TenantConfig(
        tenant_id=1,
        cold_start_grace_days=7,
        created_at=_fixed_now() - timedelta(days=7),
        updated_at=_fixed_now(),
    )
    assert _apply_cold_start_grace(0.8, tc, now=_fixed_now()) == 0.8


def test_grace_past_window_returns_unchanged() -> None:
    tc = TenantConfig(
        tenant_id=1,
        cold_start_grace_days=7,
        created_at=_fixed_now() - timedelta(days=8),
        updated_at=_fixed_now(),
    )
    assert _apply_cold_start_grace(0.8, tc, now=_fixed_now()) == 0.8


def test_grace_30_days_active_at_day_1() -> None:
    tc = TenantConfig(
        tenant_id=1,
        cold_start_grace_days=30,
        created_at=_fixed_now() - timedelta(days=1),
        updated_at=_fixed_now(),
    )
    assert _apply_cold_start_grace(1.0, tc, now=_fixed_now()) == 0.5


def test_grace_zero_maturity_remains_zero() -> None:
    tc = TenantConfig(
        tenant_id=1,
        cold_start_grace_days=7,
        created_at=_fixed_now() - timedelta(days=1),
        updated_at=_fixed_now(),
    )
    assert _apply_cold_start_grace(0.0, tc, now=_fixed_now()) == 0.0


def test_grace_full_maturity_becomes_half() -> None:
    tc = TenantConfig(
        tenant_id=1,
        cold_start_grace_days=7,
        created_at=_fixed_now() - timedelta(days=2),
        updated_at=_fixed_now(),
    )
    assert _apply_cold_start_grace(1.0, tc, now=_fixed_now()) == 0.5


def test_score_integration_grace_active_lowers_mature_customer_maturity() -> None:
    """A normally-mature customer (age=180, shipments=50) at a grace-active
    tenant scores with maturity=0.5, not 1.0. base_prior =
    MAX_NEW_ACCOUNT * (1 - 0.5) = 0.05 (vs 0.0 without grace).
    """
    ruleset = RuleSet(rules=tuple(), thresholds=Thresholds())
    cs = CustomerState(trust_score=0.8, account_age_days=180, total_shipments=50, flagged_count=0)
    # Construct a grace-active tenant (created 5 days ago, grace=14).
    now = datetime.now(UTC)
    tc = TenantConfig(
        tenant_id=1,
        cold_start_grace_days=14,
        created_at=now - timedelta(days=5),
        updated_at=now,
    )
    result = score(ruleset, {}, customer_state=cs, tenant_config=tc)
    # Maturity = 1.0 * 0.5 = 0.5
    assert result.maturity == 0.5
    # base_prior = MAX_NEW_ACCOUNT * 0.5 = 0.05
    # trust_contribution at trust_score=0.8: max(0, (0.5-0.8)/0.5) = 0 → 0
    # flag_prior at flagged_count=0: 0.0
    # account_prior = noisy_or([0.05, 0, 0]) = 0.05
    assert abs(result.account_prior - MAX_NEW_ACCOUNT * 0.5) < 1e-9
