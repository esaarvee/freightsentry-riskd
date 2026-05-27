"""Unit tests for the pure helpers in build_modification_context (3A.4).

The async build_modification_context itself is exercised by integration
tests in 3A.6 (endpoint flow); these tests pin the three pure helpers
that compute the modification-specific signals against a Context.

Cross-TZ discipline (per Phase 2 lesson): production code uses Python
datetime.now(UTC) consistently. These tests use tz-aware datetimes
throughout — no date.today() / current_date mixing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.baseline import CustomerBaseline
from app.context import (
    _modification_direction,
    _modification_magnitude,
    _modification_time_bucket,
)
from app.signal_helpers import hmac_hex

_BOOKING = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
_SECRET = b"unit-test-hmac-secret"


# ---- _modification_time_bucket ----------------------------------------------


@pytest.mark.parametrize(
    "delta,expected",
    [
        (timedelta(seconds=1), "within_30_min"),
        (timedelta(minutes=29), "within_30_min"),
        (timedelta(minutes=30), "within_30_min"),  # boundary inclusive
        (timedelta(minutes=31), "within_1_hour"),
        (timedelta(minutes=59, seconds=59), "within_1_hour"),
        (timedelta(hours=1), "within_1_hour"),  # boundary inclusive
        (timedelta(hours=1, seconds=1), "within_24_hours"),
        (timedelta(hours=23, minutes=59), "within_24_hours"),
        (timedelta(hours=24), "within_24_hours"),  # boundary inclusive
        (timedelta(hours=24, seconds=1), "1_to_7_days"),
        (timedelta(days=6, hours=23), "1_to_7_days"),
        (timedelta(days=7), "1_to_7_days"),  # boundary inclusive
        (timedelta(days=7, seconds=1), "over_7_days"),
        (timedelta(days=30), "over_7_days"),
    ],
)
def test_time_bucket_boundaries(delta: timedelta, expected: str) -> None:
    bucket = _modification_time_bucket(booking_ts=_BOOKING, modification_ts=_BOOKING + delta)
    assert bucket == expected


def test_time_bucket_negative_delta_treated_as_within_30_min() -> None:
    """Modification timestamp earlier than booking is anomalous; bucket
    as most suspicious so rules conditioning on tight window catch it."""
    bucket = _modification_time_bucket(
        booking_ts=_BOOKING, modification_ts=_BOOKING - timedelta(hours=1)
    )
    assert bucket == "within_30_min"


# ---- _modification_magnitude ------------------------------------------------


def _shipment_record(value: float, destination_hmac: str = "old-hmac") -> Any:
    """Minimal record stub — only the fields _modification_magnitude reads."""
    rec = MagicMock()
    rec.__getitem__.side_effect = lambda key: {
        "value": value,
        "destination_hmac": destination_hmac,
    }[key]
    return rec


def test_magnitude_value_increase_fraction() -> None:
    mag = _modification_magnitude(
        modification_type="value",
        new_value={"value": 1500},
        prior_shipment=_shipment_record(value=1000),
        hmac_secret=_SECRET,
    )
    assert mag == pytest.approx(0.5)


def test_magnitude_value_decrease_uses_absolute() -> None:
    mag = _modification_magnitude(
        modification_type="value",
        new_value={"value": 500},
        prior_shipment=_shipment_record(value=1000),
        hmac_secret=_SECRET,
    )
    assert mag == pytest.approx(0.5)


def test_magnitude_value_no_divide_by_zero_when_old_zero() -> None:
    mag = _modification_magnitude(
        modification_type="value",
        new_value={"value": 100},
        prior_shipment=_shipment_record(value=0),
        hmac_secret=_SECRET,
    )
    assert mag == 0.0


def test_magnitude_value_no_change_returns_zero() -> None:
    mag = _modification_magnitude(
        modification_type="value",
        new_value={"value": 1000},
        prior_shipment=_shipment_record(value=1000),
        hmac_secret=_SECRET,
    )
    assert mag == 0.0


def test_magnitude_destination_hmac_change_returns_one() -> None:
    new_addr = {"address": "456 Oak St, Boston, MA"}
    new_hmac = hmac_hex(new_addr["address"], _SECRET)
    # Set prior's destination_hmac to something different so the magnitude
    # detects the change.
    assert new_hmac != "stale-prior-hmac"
    mag = _modification_magnitude(
        modification_type="destination",
        new_value={"destination": new_addr},
        prior_shipment=_shipment_record(value=1000, destination_hmac="stale-prior-hmac"),
        hmac_secret=_SECRET,
    )
    assert mag == 1.0


def test_magnitude_destination_no_change_returns_zero() -> None:
    addr = "123 Main St, Boston, MA"
    addr_hmac = hmac_hex(addr, _SECRET)
    mag = _modification_magnitude(
        modification_type="destination",
        new_value={"destination": {"address": addr}},
        prior_shipment=_shipment_record(value=1000, destination_hmac=addr_hmac),
        hmac_secret=_SECRET,
    )
    assert mag == 0.0


def test_magnitude_destination_missing_address_returns_zero() -> None:
    mag = _modification_magnitude(
        modification_type="destination",
        new_value={},
        prior_shipment=_shipment_record(value=1000),
        hmac_secret=_SECRET,
    )
    assert mag == 0.0


@pytest.mark.parametrize("mod_type", ["recipient", "service_level", "pickup_time"])
def test_magnitude_other_types_one_when_new_value_nonempty(mod_type: str) -> None:
    mag = _modification_magnitude(
        modification_type=mod_type,
        new_value={"key": "anything"},
        prior_shipment=_shipment_record(value=1000),
        hmac_secret=_SECRET,
    )
    assert mag == 1.0


@pytest.mark.parametrize("mod_type", ["recipient", "service_level", "pickup_time"])
def test_magnitude_other_types_zero_when_new_value_empty(mod_type: str) -> None:
    mag = _modification_magnitude(
        modification_type=mod_type,
        new_value={},
        prior_shipment=_shipment_record(value=1000),
        hmac_secret=_SECRET,
    )
    assert mag == 0.0


# ---- _modification_direction ------------------------------------------------


def _baseline_with_dest(addresses: list[str]) -> CustomerBaseline:
    """Build a CustomerBaseline with the given plaintext destination
    addresses already familiar (n=1 each)."""
    baseline = CustomerBaseline.empty(tenant_id=1, customer_id=1)
    for addr in addresses:
        baseline.dest_stats[addr] = {"n": 1.0, "r_n": 0.0, "last": "2026-05-01"}
    return baseline


def test_direction_destination_familiar() -> None:
    baseline = _baseline_with_dest(["123 Main St, Boston, MA"])
    direction = _modification_direction(
        modification_type="destination",
        new_value={"destination": {"address": "123 Main St, Boston, MA"}},
        baseline=baseline,
    )
    assert direction == "familiar"


def test_direction_destination_unfamiliar() -> None:
    baseline = _baseline_with_dest(["123 Main St, Boston, MA"])
    direction = _modification_direction(
        modification_type="destination",
        new_value={"destination": {"address": "999 Unknown Ave, Nowhere, AK"}},
        baseline=baseline,
    )
    assert direction == "unfamiliar"


def test_direction_destination_no_address_returns_unknown() -> None:
    baseline = _baseline_with_dest([])
    direction = _modification_direction(
        modification_type="destination",
        new_value={},
        baseline=baseline,
    )
    assert direction == "unknown"


@pytest.mark.parametrize("mod_type", ["value", "recipient", "service_level", "pickup_time"])
def test_direction_non_destination_returns_unknown(mod_type: str) -> None:
    baseline = _baseline_with_dest(["123 Main St"])
    direction = _modification_direction(
        modification_type=mod_type,
        new_value={"value": 1500},
        baseline=baseline,
    )
    assert direction == "unknown"


# ---- Additional coverage per cycle-1 test-reviewer feedback -----------------


def test_magnitude_value_empty_payload_returns_zero() -> None:
    """Malformed value-type payload (missing 'value' key) defaults to
    no-change rather than raising — consistent with the test-reviewer
    pin for input robustness."""
    mag = _modification_magnitude(
        modification_type="value",
        new_value={},
        prior_shipment=_shipment_record(value=1000),
        hmac_secret=_SECRET,
    )
    assert mag == 0.0


def test_magnitude_value_non_numeric_payload_returns_zero() -> None:
    """Malformed value-type payload (non-numeric value) returns 0.0
    rather than raising TypeError/ValueError. Security-auditor flag —
    the helper is the validation layer for new_value: dict[str, Any]."""
    mag = _modification_magnitude(
        modification_type="value",
        new_value={"value": "not-a-number"},
        prior_shipment=_shipment_record(value=1000),
        hmac_secret=_SECRET,
    )
    assert mag == 0.0
