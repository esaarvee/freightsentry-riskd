"""Unit tests for app/baseline.py — pure-Python logic only (no DB).

Decay math, Welford updates, stat-dict bumping, familiarity tier rules.
DB round-trip + SELECT FOR UPDATE concurrency live in
tests/integration/test_baseline_db.py.
"""

from datetime import UTC, date, datetime

import pytest

from app.baseline import (
    HALF_LIFE_IP_CLOUD,
    HALF_LIFE_IP_RESIDENTIAL,
    HALF_LIFE_IP_UNKNOWN,
    IP_TYPE_CLOUD,
    CustomerBaseline,
    _decay_factor,
    _half_life_for_ip_type,
)


def _at(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Per-IP-type half-life mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ip_type", "expected"),
    [
        ("cloud", HALF_LIFE_IP_CLOUD),
        ("dc", HALF_LIFE_IP_CLOUD),  # cloud and dc both 365d
        ("residential", HALF_LIFE_IP_RESIDENTIAL),
        ("unknown_value", HALF_LIFE_IP_UNKNOWN),
        ("", HALF_LIFE_IP_UNKNOWN),
        (None, HALF_LIFE_IP_UNKNOWN),
    ],
)
def test_half_life_for_ip_type(ip_type: str | None, expected: float) -> None:
    assert _half_life_for_ip_type(ip_type) == expected


def test_decay_factor_zero_days_is_one() -> None:
    assert _decay_factor(0, 90.0) == 1.0


def test_decay_factor_one_half_life_is_one_half() -> None:
    """exp(-ln2 * H/H) = 1/2."""
    assert _decay_factor(90, 90.0) == pytest.approx(0.5)


def test_decay_factor_two_half_lives_is_one_quarter() -> None:
    assert _decay_factor(180, 90.0) == pytest.approx(0.25)


def test_decay_factor_negative_days_is_one() -> None:
    """Reverse-direction anchors should not amplify."""
    assert _decay_factor(-5, 90.0) == 1.0


# ---------------------------------------------------------------------------
# decay_to
# ---------------------------------------------------------------------------


def test_decay_to_first_call_sets_anchor() -> None:
    bl = CustomerBaseline.empty(tenant_id=1, customer_id=2)
    bl.decay_to(date(2026, 5, 26))
    assert bl.decay_anchor_date == date(2026, 5, 26)


def test_decay_to_no_op_if_anchor_in_future() -> None:
    bl = CustomerBaseline.empty(1, 2)
    bl.decay_anchor_date = date(2026, 6, 1)
    bl.value_n = 100.0
    bl.decay_to(date(2026, 5, 26))
    assert bl.value_n == 100.0  # unchanged


def test_decay_to_advances_anchor_and_scales_value_welford() -> None:
    bl = CustomerBaseline.empty(1, 2)
    bl.decay_anchor_date = date(2026, 1, 1)
    bl.value_n = 100.0
    bl.value_m2 = 2000.0
    bl.decay_to(date(2026, 4, 1))  # 90 days = exactly one default half-life
    assert bl.value_n == pytest.approx(50.0)
    assert bl.value_m2 == pytest.approx(1000.0)
    assert bl.decay_anchor_date == date(2026, 4, 1)


def test_decay_to_per_ip_type_half_lives() -> None:
    bl = CustomerBaseline.empty(1, 2)
    bl.decay_anchor_date = date(2026, 1, 1)
    bl.ip_stats = {
        "10.0.0.1": {"n": 100.0, "r_n": 0.0, "last": "2026-01-01", "type": "cloud"},
        "10.0.0.2": {"n": 100.0, "r_n": 0.0, "last": "2026-01-01", "type": "residential"},
        "10.0.0.3": {"n": 100.0, "r_n": 0.0, "last": "2026-01-01"},  # unknown
    }
    bl.decay_to(date(2026, 4, 1))  # 90 days
    # cloud: 365d half-life → 90 / 365 = ~0.247 half-lives → factor ~0.844
    assert bl.ip_stats["10.0.0.1"]["n"] == pytest.approx(
        100.0 * _decay_factor(90, 365.0), rel=0.001
    )
    # residential: 60d half-life → 1.5 half-lives → factor ~0.354
    assert bl.ip_stats["10.0.0.2"]["n"] == pytest.approx(
        100.0 * _decay_factor(90, 60.0), rel=0.001
    )
    # unknown: 180d half-life → 0.5 half-lives → factor ~0.707
    assert bl.ip_stats["10.0.0.3"]["n"] == pytest.approx(
        100.0 * _decay_factor(90, 180.0), rel=0.001
    )


def test_decay_to_uniform_default_for_non_ip_dicts() -> None:
    bl = CustomerBaseline.empty(1, 2)
    bl.decay_anchor_date = date(2026, 1, 1)
    bl.origin_stats = {"123 Main": {"n": 80.0, "r_n": 0.0, "last": "2026-01-01"}}
    bl.hour_hist = {"12": 100.0}
    bl.decay_to(date(2026, 4, 1))  # 90 days = one default half-life
    assert bl.origin_stats["123 Main"]["n"] == pytest.approx(40.0)
    assert bl.hour_hist["12"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# add_observation
# ---------------------------------------------------------------------------


def _add_minimal(bl: CustomerBaseline, ts: datetime, **overrides: object) -> None:
    kwargs = {
        "ts": ts,
        "ip": "192.0.2.1",
        "ip_type": IP_TYPE_CLOUD,
        "ip_netblock": "192.0.2.0",
        "ip_asn": "AS-Test",
        "ip_country": "CA",
        "ip_lat": 43.0,
        "ip_lon": -79.0,
        "origin": "123 Main",
        "destination": "456 Oak",
        "channel": "web",
        "value": 100.0,
    }
    kwargs.update(overrides)
    bl.add_observation(**kwargs)  # type: ignore[arg-type]


def test_add_observation_first_call_initialises_stats() -> None:
    bl = CustomerBaseline.empty(1, 2)
    _add_minimal(bl, _at(2026, 5, 26))
    assert bl.ip_stats["192.0.2.1"]["n"] == 1.0
    assert bl.ip_stats["192.0.2.1"]["type"] == IP_TYPE_CLOUD
    assert bl.origin_stats["123 Main"]["n"] == 1.0
    assert bl.lane_stats["123 Main||456 Oak"]["n"] == 1.0
    assert bl.value_n == 1.0
    assert bl.value_mean == 100.0
    assert bl.last_booking_ts == _at(2026, 5, 26)


def test_add_observation_welford_value_two_samples() -> None:
    """Welford for [100, 200]: mean=150, m2=5000 (variance=2500)."""
    bl = CustomerBaseline.empty(1, 2)
    _add_minimal(bl, _at(2026, 5, 26), value=100.0)
    _add_minimal(bl, _at(2026, 5, 27), value=200.0)
    assert bl.value_n == 2.0
    assert bl.value_mean == pytest.approx(150.0)
    assert bl.value_m2 == pytest.approx(5000.0)


def test_add_observation_cadence_requires_prior_booking() -> None:
    bl = CustomerBaseline.empty(1, 2)
    _add_minimal(bl, _at(2026, 5, 26))
    assert bl.cadence_n == 0.0  # no cadence on first booking

    _add_minimal(bl, _at(2026, 5, 27))
    assert bl.cadence_n == 1.0
    assert bl.cadence_mean_h == pytest.approx(24.0)


def test_add_observation_updates_histograms() -> None:
    bl = CustomerBaseline.empty(1, 2)
    ts = _at(2026, 5, 26, hour=14)  # Tuesday 14:00
    _add_minimal(bl, ts)
    assert bl.hour_hist["14"] == 1.0
    assert bl.weekday_hist[str(ts.weekday())] == 1.0
    assert bl.channel_hist["web"] == 1.0
    assert bl.ip_type_hist[IP_TYPE_CLOUD] == 1.0


def test_value_zscore_undefined_until_two_samples() -> None:
    bl = CustomerBaseline.empty(1, 2)
    _add_minimal(bl, _at(2026, 5, 26))
    assert bl.value_zscore(100.0) == 0.0  # n < 2


def test_value_zscore_after_known_distribution() -> None:
    """[100, 200] → mean=150, stddev=sqrt(2500)=50; z(250) = 2.0."""
    bl = CustomerBaseline.empty(1, 2)
    _add_minimal(bl, _at(2026, 5, 26), value=100.0)
    _add_minimal(bl, _at(2026, 5, 27), value=200.0)
    assert bl.value_zscore(250.0) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# ip_familiarity_tier (per verification §2.2: /24-only confers family-familiar)
# ---------------------------------------------------------------------------


def test_ip_familiarity_familiar_exact_match() -> None:
    bl = CustomerBaseline.empty(1, 2)
    _add_minimal(bl, _at(2026, 5, 26))
    assert (
        bl.ip_familiarity_tier("192.0.2.1", "192.0.2.0", "AS-Test")
        == "familiar"
    )


def test_ip_familiarity_family_via_netblock_match_only() -> None:
    """New IP, same /24 → family_familiar (not the previous ASN-only path)."""
    bl = CustomerBaseline.empty(1, 2)
    _add_minimal(bl, _at(2026, 5, 26))
    assert (
        bl.ip_familiarity_tier("192.0.2.99", "192.0.2.0", "Some-Other-ASN")
        == "family_familiar"
    )


def test_ip_familiarity_new_known_asn() -> None:
    """ASN match without /24 match → new_known_asn (was previously
    upgraded to family_familiar; now demoted per verification §2.2)."""
    bl = CustomerBaseline.empty(1, 2)
    _add_minimal(bl, _at(2026, 5, 26))
    assert (
        bl.ip_familiarity_tier("203.0.113.5", "203.0.113.0", "AS-Test")
        == "new_known_asn"
    )


def test_ip_familiarity_fully_new() -> None:
    bl = CustomerBaseline.empty(1, 2)
    _add_minimal(bl, _at(2026, 5, 26))
    assert (
        bl.ip_familiarity_tier("203.0.113.5", "203.0.113.0", "Unknown-ASN")
        == "fully_new"
    )


# ---------------------------------------------------------------------------
# add_rejected_observation
# ---------------------------------------------------------------------------


def test_add_rejected_observation_increments_r_n() -> None:
    bl = CustomerBaseline.empty(1, 2)
    bl.email_hmacs["abc"] = {"n": 1.0, "r_n": 0.0, "last": "2026-05-25"}
    bl.add_rejected_observation(
        key_in="abc", stat="email_hmacs", ts=_at(2026, 5, 26)
    )
    assert bl.email_hmacs["abc"]["r_n"] == 1.0
    assert bl.email_hmacs["abc"]["n"] == 1.0  # n unchanged
    assert bl.email_hmacs["abc"]["last"] == "2026-05-26"


# ---------------------------------------------------------------------------
# effective_observations
# ---------------------------------------------------------------------------


def test_effective_observations_tracks_value_n() -> None:
    bl = CustomerBaseline.empty(1, 2)
    assert bl.effective_observations == 0.0
    _add_minimal(bl, _at(2026, 5, 26))
    assert bl.effective_observations == 1.0
    _add_minimal(bl, _at(2026, 5, 27))
    assert bl.effective_observations == 2.0
