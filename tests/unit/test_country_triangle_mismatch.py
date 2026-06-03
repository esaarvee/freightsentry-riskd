"""Unit tests for Phase 6A.5 customer_country_triangle_mismatch derivation.

Tests the PRODUCTION helper `app.context._triangle_mismatch` directly
(no parallel mirror — would be a false-pass test). The same helper
is invoked by `build_context` to populate
ctx["customer_country_triangle_mismatch"].

Returns True iff:
- customer_registered_country is not None
- shipment.origin.country is not None
- shipment.destination.country is not None
- customer_registered_country != shipment.origin.country
- customer_registered_country != shipment.destination.country
"""

from __future__ import annotations

from app.context import _triangle_mismatch


def test_mismatch_true_when_customer_country_differs_from_both() -> None:
    """The case-3b shape: CA customer shipping US → US."""
    assert _triangle_mismatch("CA", "US", "US") is True


def test_mismatch_true_when_customer_country_differs_from_both_different_dst() -> None:
    """CA customer shipping US → GB also triggers — both legs are outside CA."""
    assert _triangle_mismatch("CA", "US", "GB") is True


def test_mismatch_false_when_origin_matches_customer() -> None:
    """CA customer with CA origin — domestic outbound. No signal."""
    assert _triangle_mismatch("CA", "CA", "US") is False


def test_mismatch_false_when_destination_matches_customer() -> None:
    """CA customer with CA destination — domestic inbound. No signal."""
    assert _triangle_mismatch("CA", "US", "CA") is False


def test_mismatch_false_when_both_match_customer() -> None:
    """CA customer shipping domestically. No signal."""
    assert _triangle_mismatch("CA", "CA", "CA") is False


def test_mismatch_false_when_customer_country_none() -> None:
    """No customer country → no signal (corpora without ground truth)."""
    assert _triangle_mismatch(None, "US", "GB") is False


def test_mismatch_false_when_origin_country_none() -> None:
    assert _triangle_mismatch("CA", None, "GB") is False


def test_mismatch_false_when_destination_country_none() -> None:
    assert _triangle_mismatch("CA", "US", None) is False


def test_mismatch_false_when_all_none() -> None:
    assert _triangle_mismatch(None, None, None) is False
