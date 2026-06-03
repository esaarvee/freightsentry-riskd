"""Unit tests pinning the DSL ALLOWED_CONTEXT_FIELDS whitelist.

Phase 2B.5 grows the whitelist from 45 (Phase 1) to 56 fields. The
whitelist is the security boundary the DSL evaluator enforces — only
names in this set may be referenced from a rule condition. Any field
added to build_context must ALSO be added here, otherwise rule loader
fails at lifespan startup.

This test file is intentionally narrow: a frozen-set type check, a
size pin, and an explicit per-field membership assertion for the
Phase 2B additions. Field-semantics testing lives in the build_context
integration tests (tests/integration/test_context.py).
"""

from __future__ import annotations

from app.rules import ALLOWED_CONTEXT_FIELDS

_PHASE_2B_ADDITIONS = frozenset(
    {
        "customer_locked_cloud_api",
        "customer_locked_web_only",
        "days_since_last_booking",
        "is_new_user",
        "ip_familiarity_tier",
        "ip_new_known_asn",
        "is_residential_asn",
        "ip2p_threat_any",
        "recipient_cross_customer_count",
        "customer_distinct_ips_30d",
        "impossible_travel",
    }
)


def test_whitelist_is_frozenset() -> None:
    """frozenset prevents accidental in-place mutation by importing
    code. The rule loader's whitelist check is the security boundary;
    mutability would let a rogue caller add fields at runtime."""
    assert isinstance(ALLOWED_CONTEXT_FIELDS, frozenset)


def test_whitelist_size_matches_phase_6a_total() -> None:
    """Phase 1 baseline = 45; Phase 2B adds 11 → 56; Phase 3A adds 6 → 62;
    Phase 3B adds 4 → 66; Phase 4B.4 adds 5 (shipment_currency +
    4 tier thresholds) → 71; Phase 6A.2 adds 2
    (origin_via_carrier_dropoff, shipment_route_unfamiliar_for_customer)
    → 73. A size drift catches both accidental removal AND silent
    addition that bypasses operator + reviewer scrutiny."""
    assert len(ALLOWED_CONTEXT_FIELDS) == 73


def test_whitelist_contains_phase_6a_2_additions() -> None:
    """Phase 6A.2 case-3a signals must both be in the whitelist."""
    phase_6a_2 = frozenset({"origin_via_carrier_dropoff", "shipment_route_unfamiliar_for_customer"})
    missing = phase_6a_2 - ALLOWED_CONTEXT_FIELDS
    assert not missing, f"Phase 6A.2 fields not in whitelist: {missing}"


def test_whitelist_contains_every_phase_2b_addition() -> None:
    """Each Phase 2B field is present. A diff between build_context's
    ctx keys and this set is the single source of truth for the
    whitelist — these field names must match the production build_context
    keys exactly."""
    missing = _PHASE_2B_ADDITIONS - ALLOWED_CONTEXT_FIELDS
    assert not missing, f"Phase 2B fields not in whitelist: {missing}"


def test_whitelist_phase_2b_additions_count_is_eleven() -> None:
    """Sanity: the pinned addition set is the documented 11 fields."""
    assert len(_PHASE_2B_ADDITIONS) == 11
