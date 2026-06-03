"""Unit tests for Phase 6A.2 country_route_stats histogram population.

Pure in-memory exercises of CustomerBaseline.add_observation against the
new shipment_origin_country / shipment_destination_country parameters.
No DB. Confirms:
- Bump fires only when both countries non-null
- Composite key shape "{origin}||{destination}"
- Cap (COUNTRY_ROUTE_STATS_CAP) enforced on novel pairs; existing keys
  always bump regardless of cap
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.baseline import COUNTRY_ROUTE_STATS_CAP, CustomerBaseline


def _empty() -> CustomerBaseline:
    return CustomerBaseline.empty(tenant_id=1, customer_id=1)


def _observe(
    b: CustomerBaseline,
    *,
    shipment_origin_country: str | None,
    shipment_destination_country: str | None,
) -> None:
    """Minimal add_observation invocation — exercises the country pair path only."""
    b.add_observation(
        ts=datetime(2026, 6, 3, tzinfo=UTC),
        ip="1.1.1.1",
        ip_type=None,
        ip_netblock="1.1.1.0/24",
        ip_asn=None,
        ip_country=None,
        ip_lat=None,
        ip_lon=None,
        origin="orig",
        destination="dest",
        channel="web",
        value=100.0,
        shipment_origin_country=shipment_origin_country,
        shipment_destination_country=shipment_destination_country,
    )


def test_country_route_stats_empty_on_new_baseline() -> None:
    assert _empty().country_route_stats == {}


def test_country_route_stats_bump_on_both_countries_present() -> None:
    b = _empty()
    _observe(b, shipment_origin_country="CA", shipment_destination_country="US")
    assert "CA||US" in b.country_route_stats
    assert b.country_route_stats["CA||US"]["n"] == 1.0


def test_country_route_stats_increment_on_repeated_route() -> None:
    b = _empty()
    for _ in range(3):
        _observe(b, shipment_origin_country="CA", shipment_destination_country="US")
    assert b.country_route_stats["CA||US"]["n"] == 3.0


def test_country_route_stats_no_bump_when_origin_country_missing() -> None:
    b = _empty()
    _observe(b, shipment_origin_country=None, shipment_destination_country="US")
    assert b.country_route_stats == {}


def test_country_route_stats_no_bump_when_destination_country_missing() -> None:
    b = _empty()
    _observe(b, shipment_origin_country="CA", shipment_destination_country=None)
    assert b.country_route_stats == {}


def test_country_route_stats_no_bump_when_both_missing() -> None:
    b = _empty()
    _observe(b, shipment_origin_country=None, shipment_destination_country=None)
    assert b.country_route_stats == {}


def test_country_route_stats_composite_key_shape() -> None:
    b = _empty()
    _observe(b, shipment_origin_country="CA", shipment_destination_country="US")
    _observe(b, shipment_origin_country="US", shipment_destination_country="CA")
    assert "CA||US" in b.country_route_stats
    assert "US||CA" in b.country_route_stats
    assert b.country_route_stats["CA||US"]["n"] == 1.0
    assert b.country_route_stats["US||CA"]["n"] == 1.0


def test_country_route_stats_cap_blocks_novel_pair_beyond_cap() -> None:
    """When the histogram is at COUNTRY_ROUTE_STATS_CAP keys, new pairs
    are silently dropped (adversarial flood defense)."""
    b = _empty()
    # Seed cap distinct pairs (XX||00..XX||CC where XX is country index).
    for i in range(COUNTRY_ROUTE_STATS_CAP):
        origin = f"O{i:02d}"[:2].upper()
        dest = f"D{i:02d}"[:2].upper()
        # ensure unique pairs by encoding index into the key tail
        b.country_route_stats[f"{origin}||{dest}{i}"] = {"n": 1.0, "last": "2026-06-03"}
    assert len(b.country_route_stats) == COUNTRY_ROUTE_STATS_CAP
    _observe(b, shipment_origin_country="ZZ", shipment_destination_country="YY")
    # New pair was dropped because cap is reached.
    assert "ZZ||YY" not in b.country_route_stats
    assert len(b.country_route_stats) == COUNTRY_ROUTE_STATS_CAP


def test_country_route_stats_cap_still_bumps_existing_key() -> None:
    """An existing key bumps even when histogram is at cap — only novel
    pairs are blocked."""
    b = _empty()
    # Pre-seed the histogram up to cap with the to-be-bumped key included.
    for i in range(COUNTRY_ROUTE_STATS_CAP - 1):
        b.country_route_stats[f"K{i}"] = {"n": 1.0, "last": "2026-06-03"}
    b.country_route_stats["CA||US"] = {"n": 5.0, "last": "2026-06-03"}
    assert len(b.country_route_stats) == COUNTRY_ROUTE_STATS_CAP
    _observe(b, shipment_origin_country="CA", shipment_destination_country="US")
    # Existing key bumped: count is now 6.0; size unchanged.
    assert b.country_route_stats["CA||US"]["n"] == 6.0
    assert len(b.country_route_stats) == COUNTRY_ROUTE_STATS_CAP
