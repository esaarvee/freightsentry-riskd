"""Unit tests for case-3a Context derivations.

Two new Context fields:
- origin_via_carrier_dropoff (passthrough from payload.shipment)
- shipment_route_unfamiliar_for_customer (derived from
  baseline.country_route_stats via _derive_route_unfamiliar)

These tests exercise the pure _derive_route_unfamiliar helper directly
(no DB, no full build_context invocation). Integration coverage of the
passthrough lives in the booking-endpoint integration tests.
"""

from __future__ import annotations

from app.context import _derive_route_unfamiliar


def _hist(pairs: dict[str, float]) -> dict[str, dict[str, float]]:
    """Build a country_route_stats-shaped histogram from a {key: n} dict."""
    return {k: {"n": v} for k, v in pairs.items()}


def test_route_unfamiliar_below_maturity_returns_false() -> None:
    """Customer with histogram but below maturity gate (10 obs) → no signal."""
    h = _hist({"CA||CA": 50.0, "CA||US": 30.0})
    assert (
        _derive_route_unfamiliar(
            h,
            current_origin_country="CA",
            current_destination_country="GB",
            customer_observations=5.0,
        )
        is False
    )


def test_route_unfamiliar_empty_histogram_returns_false() -> None:
    """Mature customer with empty histogram → no signal (cold-start safe)."""
    assert (
        _derive_route_unfamiliar(
            {},
            current_origin_country="CA",
            current_destination_country="GB",
            customer_observations=50.0,
        )
        is False
    )


def test_route_unfamiliar_missing_origin_country_returns_false() -> None:
    """No structured origin country → no signal."""
    h = _hist({"CA||CA": 100.0})
    assert (
        _derive_route_unfamiliar(
            h,
            current_origin_country=None,
            current_destination_country="US",
            customer_observations=100.0,
        )
        is False
    )


def test_route_unfamiliar_missing_destination_country_returns_false() -> None:
    """No structured destination country → no signal."""
    h = _hist({"CA||CA": 100.0})
    assert (
        _derive_route_unfamiliar(
            h,
            current_origin_country="CA",
            current_destination_country=None,
            customer_observations=100.0,
        )
        is False
    )


def test_route_unfamiliar_current_in_top_n_returns_false() -> None:
    """Customer ships their familiar route → no signal."""
    h = _hist({"CA||CA": 50.0, "CA||US": 30.0, "CA||MX": 5.0})
    # Top-1 ("CA||CA": 50) covers 50/85 = 58.8%; top-2 (50+30 = 80) covers
    # 80/85 = 94%. So top-N prefix = {CA||CA, CA||US}.
    assert (
        _derive_route_unfamiliar(
            h,
            current_origin_country="CA",
            current_destination_country="CA",
            customer_observations=85.0,
        )
        is False
    )


def test_route_unfamiliar_current_not_in_top_n_returns_true() -> None:
    """Customer ships a route absent from top-N prefix → signal fires."""
    h = _hist({"CA||CA": 50.0, "CA||US": 30.0, "CA||MX": 5.0})
    # Top-N = {CA||CA, CA||US}; current = CA||GB (not in prefix).
    assert (
        _derive_route_unfamiliar(
            h,
            current_origin_country="CA",
            current_destination_country="GB",
            customer_observations=85.0,
        )
        is True
    )


def test_route_unfamiliar_current_in_long_tail_returns_true() -> None:
    """Current pair exists in histogram but only in the long tail (<20% of
    observations) → signal fires."""
    # Top-1 covers exactly 80%; long-tail pair is NOT in the top-N prefix.
    h = _hist({"CA||CA": 80.0, "CA||MX": 5.0, "CA||US": 5.0, "CA||GB": 10.0})
    # Total = 100; threshold = 80. Top-1 = {CA||CA: 80} hits threshold
    # exactly. CA||GB is in histogram but NOT in top-1 prefix.
    assert (
        _derive_route_unfamiliar(
            h,
            current_origin_country="CA",
            current_destination_country="GB",
            customer_observations=100.0,
        )
        is True
    )


def test_route_unfamiliar_maturity_boundary_at_ten() -> None:
    """Maturity gate is `<10` strict; exactly 10 observations passes."""
    h = _hist({"CA||CA": 10.0})
    # 10 obs, current pair = CA||US (not in top-N). Should fire.
    assert (
        _derive_route_unfamiliar(
            h,
            current_origin_country="CA",
            current_destination_country="US",
            customer_observations=10.0,
        )
        is True
    )
