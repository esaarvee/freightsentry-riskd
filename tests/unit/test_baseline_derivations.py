"""Unit tests for CustomerBaseline derivations (Phase 2B.3).

Pure-derivation properties + one date-arithmetic helper. No DB.

The derivations feed Phase 2C lock-in + dormancy rules via build_context
(wired in 2B.4). Each is a property/method on an in-memory CustomerBaseline
constructed with hand-set histogram fields.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.baseline import CustomerBaseline


def _empty() -> CustomerBaseline:
    return CustomerBaseline.empty(tenant_id=1, customer_id=1)


# ---------------------------------------------------------------------------
# cloud_share
# ---------------------------------------------------------------------------


def test_cloud_share_zero_for_empty_histogram() -> None:
    assert _empty().cloud_share == 0.0


def test_cloud_share_one_for_all_cloud() -> None:
    b = _empty()
    b.ip_type_hist = {"cloud": 10.0}
    assert b.cloud_share == 1.0


def test_cloud_share_mixed() -> None:
    b = _empty()
    b.ip_type_hist = {"cloud": 8.0, "residential": 2.0}
    assert b.cloud_share == 0.8


def test_cloud_share_stable_under_uniform_decay() -> None:
    """A flat decay factor scales both numerator and denominator, so
    the ratio is stable."""
    b = _empty()
    b.ip_type_hist = {"cloud": 4.0, "residential": 1.0}
    pre_decay = b.cloud_share
    # Simulate a half-life decay applied to both buckets.
    b.ip_type_hist = {"cloud": 2.0, "residential": 0.5}
    assert b.cloud_share == pre_decay == 0.8


def test_cloud_share_excludes_other_types() -> None:
    """Datacenter / residential / unknown contribute to the denominator
    but not the cloud numerator. Uses the production "dc" / "residential"
    tags from app/baseline.py (IP_TYPE_DC, IP_TYPE_RESIDENTIAL)."""
    b = _empty()
    b.ip_type_hist = {"cloud": 3.0, "dc": 1.0, "residential": 1.0}
    assert b.cloud_share == 0.6  # 3 / 5


# ---------------------------------------------------------------------------
# api_share
# ---------------------------------------------------------------------------


def test_api_share_zero_for_empty_histogram() -> None:
    assert _empty().api_share == 0.0


def test_api_share_one_for_pure_api() -> None:
    b = _empty()
    b.channel_hist = {"api": 5.0}
    assert b.api_share == 1.0


def test_api_share_mixed() -> None:
    b = _empty()
    b.channel_hist = {"api": 4.0, "web": 1.0}
    assert b.api_share == 0.8


# ---------------------------------------------------------------------------
# days_since_last_booking
# ---------------------------------------------------------------------------


def test_days_since_last_booking_none_for_first_booking() -> None:
    b = _empty()
    assert b.days_since_last_booking(datetime.now(tz=UTC)) is None


def test_days_since_last_booking_zero_for_same_day() -> None:
    b = _empty()
    b.last_booking_ts = datetime(2026, 5, 26, 9, 0, tzinfo=UTC)
    now = datetime(2026, 5, 26, 14, 30, tzinfo=UTC)
    assert b.days_since_last_booking(now) == 0


def test_days_since_last_booking_basic_arithmetic() -> None:
    b = _empty()
    b.last_booking_ts = datetime(2026, 4, 26, 10, 0, tzinfo=UTC)
    now = datetime(2026, 5, 26, 10, 0, tzinfo=UTC)
    assert b.days_since_last_booking(now) == 30


def test_days_since_last_booking_clamps_negative_to_zero() -> None:
    """If now_ts somehow precedes last_booking_ts (clock skew, replay),
    clamp to 0 — never return a negative day count."""
    b = _empty()
    b.last_booking_ts = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    earlier = b.last_booking_ts - timedelta(days=5)
    assert b.days_since_last_booking(earlier) == 0
