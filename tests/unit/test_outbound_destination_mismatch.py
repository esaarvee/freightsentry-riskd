"""Unit tests for the _outbound_destination_mismatch
helper in app/context.py. The helper backs the
customer_destination_country_mismatch_outbound ctx field consumed by
the cold_start_outbound_carrier_dropoff rule.

Truth table covered:
- Both inputs non-empty and differ -> True (case-3b shape).
- Both inputs equal -> False.
- Either input None -> False.
- Either input empty string -> False (defensive falsy check).
"""

from __future__ import annotations

from app.context import _outbound_destination_mismatch


def test_outbound_mismatch_canonical_case_3b_shape() -> None:
    """CA customer shipping to US — the Roulottes Lupien attack shape."""
    assert _outbound_destination_mismatch("CA", "US") is True


def test_outbound_mismatch_us_to_gb_also_fires() -> None:
    """Any non-equal pair of country codes fires."""
    assert _outbound_destination_mismatch("US", "GB") is True


def test_outbound_mismatch_same_country_returns_false() -> None:
    """Customer shipping within their declared country is NOT
    case-3b shape."""
    assert _outbound_destination_mismatch("CA", "CA") is False
    assert _outbound_destination_mismatch("US", "US") is False


def test_outbound_mismatch_none_customer_returns_false() -> None:
    """Customers without a declared registered country (tier-4
    fallback in the freight_risk export) must not trigger by accident."""
    assert _outbound_destination_mismatch(None, "US") is False


def test_outbound_mismatch_none_destination_returns_false() -> None:
    """Shipments without a structured destination country must not
    trigger by accident."""
    assert _outbound_destination_mismatch("CA", None) is False


def test_outbound_mismatch_both_none_returns_false() -> None:
    assert _outbound_destination_mismatch(None, None) is False


def test_outbound_mismatch_empty_customer_returns_false() -> None:
    """Empty string treated as no-signal via the falsy check;
    defensive symmetry with None. Pydantic blocks this case at
    ingress, but the helper's behavior on empty input is documented
    via this test."""
    assert _outbound_destination_mismatch("", "US") is False


def test_outbound_mismatch_empty_destination_returns_false() -> None:
    assert _outbound_destination_mismatch("CA", "") is False


def test_outbound_mismatch_both_empty_returns_false() -> None:
    assert _outbound_destination_mismatch("", "") is False
