"""Unit tests pinning the DSL ALLOWED_CONTEXT_FIELDS whitelist.

The whitelist is the security boundary the DSL evaluator enforces —
only names in this set may be referenced from a rule condition. Any
field added to build_context must ALSO be added here, otherwise the
rule loader fails at lifespan startup.

This test file is intentionally narrow: a frozen-set type check, a
size pin, and explicit subset-membership probes for the historical
field-addition groups. Field-semantics testing lives in the
build_context integration tests (tests/integration/test_context.py).
"""

from __future__ import annotations

from app.rules import ALLOWED_CONTEXT_FIELDS

# Phase 2B added 11 fields. The set is pinned here as a subset-probe
# anchor — if any of these fields is ever silently removed from the
# whitelist (e.g., during a refactor that drops production code paths
# they back), the membership test below catches it. The Phase 2B
# attribution is historical context that survives in the constant name.
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


def test_whitelist_size_matches_current() -> None:
    """Pins the current whitelist size. A drift catches both accidental
    removal AND silent addition that bypasses operator + reviewer
    scrutiny. The current count (77) is the result of accumulated
    additions across the Phase 1 baseline (45) through Phase 7C
    (case-3b refactor + ASN-deviation signal); see docs/history.md
    for the full per-phase progression."""
    assert len(ALLOWED_CONTEXT_FIELDS) == 77


def test_whitelist_contains_pinned_baseline_additions() -> None:
    """Every field in the pinned ``_PHASE_2B_ADDITIONS`` group is present.
    A diff between build_context's ctx keys and this set is the single
    source of truth for the whitelist — these field names must match
    the production build_context keys exactly. Set size pinned too so
    accidental constant edits in this file are caught."""
    missing = _PHASE_2B_ADDITIONS - ALLOWED_CONTEXT_FIELDS
    assert not missing, f"Pinned baseline fields not in whitelist: {missing}"
    assert len(_PHASE_2B_ADDITIONS) == 11


def test_whitelist_contains_case_detection_signals() -> None:
    """Phase 6 and 7 case-3a/3b detection primitives must all be present.
    The symmetric ``customer_country_triangle_mismatch`` field
    (Phase 6A.5) was DELETED in Phase 7C.2 in favor of the asymmetric
    ``customer_destination_country_mismatch_outbound``; its absence is
    pinned alongside the present-field probes so a future accidental
    revive of the symmetric compound is caught."""
    expected_present = frozenset(
        {
            # Phase 6A.2 — case-3a route signals.
            "origin_via_carrier_dropoff",
            "shipment_route_unfamiliar_for_customer",
            # Phase 6A.5 retained + Phase 7C.2 case-3b primitives.
            "customer_registered_country",
            "customer_destination_country_mismatch_outbound",
            # Phase 6A.8 — case-3b sophisticated signal.
            "shipment_route_rare_for_tenant",
            # Phase 7C.6 — case-2 learning-based deviation.
            "unfamiliar_asn_for_customer",
        }
    )
    missing = expected_present - ALLOWED_CONTEXT_FIELDS
    assert not missing, f"Phase 6/7 fields not in whitelist: {missing}"
    assert "customer_country_triangle_mismatch" not in ALLOWED_CONTEXT_FIELDS
