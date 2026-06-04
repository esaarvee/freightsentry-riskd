"""Phase 7C.6 — unit tests for the _asn_unfamiliar_for_customer
helper in app/context.py. The helper backs the
unfamiliar_asn_for_customer ctx field consumed by the
api_booking_from_unfamiliar_asn rule (case-2 detection).

Truth table covered:
- Novel asn + mature customer -> True (case-2 shape).
- Known asn + mature customer -> False (legitimate baseline match).
- Novel asn + cold-start customer -> False (gate excludes).
- None asn (enrichment gap) -> False.
- Boundary at exactly the gate threshold.
"""

from __future__ import annotations

from app.context import _asn_unfamiliar_for_customer


def test_novel_asn_mature_customer_fires() -> None:
    """Mature customer with established baseline; booking from an
    ASN they've never used - case-2 shape."""
    baseline = {"GOOGLE LLC": {"n": 100.0, "last": "2026-03-15"}}
    assert _asn_unfamiliar_for_customer("COMCAST CABLE", baseline, 50.0) is True


def test_known_asn_mature_customer_does_not_fire() -> None:
    """Mature customer booking from a familiar ASN - legitimate
    pattern; no signal."""
    baseline = {"GOOGLE LLC": {"n": 100.0, "last": "2026-03-15"}}
    assert _asn_unfamiliar_for_customer("GOOGLE LLC", baseline, 50.0) is False


def test_cold_start_customer_does_not_fire() -> None:
    """Customer below the cold-start gate; the rule must not fire
    even if the ASN is novel — the customer has not yet accumulated
    a meaningful baseline."""
    baseline = {"GOOGLE LLC": {"n": 1.0, "last": "2026-03-15"}}
    assert _asn_unfamiliar_for_customer("COMCAST CABLE", baseline, 5.0) is False


def test_none_asn_does_not_fire() -> None:
    """Enrichment gap (MaxMind miss / unavailable). The customer's
    familiarity baseline cannot meaningfully evaluate novelty without
    a current ASN to compare. Defensive no-signal."""
    baseline = {"GOOGLE LLC": {"n": 100.0, "last": "2026-03-15"}}
    assert _asn_unfamiliar_for_customer(None, baseline, 50.0) is False


def test_empty_baseline_mature_customer_fires_on_any_asn() -> None:
    """Mature customer (effective_observations counts via decayed
    accumulators; not strictly tied to ip_asn_stats population). When
    the customer's ip_asn_stats happens to be empty but their other
    observations push effective_observations past the gate, any ASN
    is novel. Edge case: in practice ip_asn_stats and effective_obs
    accumulate together via add_observation, so this state is rare."""
    assert _asn_unfamiliar_for_customer("ANY ASN", {}, 50.0) is True


def test_gate_boundary_at_exactly_ten_fires() -> None:
    """`customer_observations >= 10` is the gate (not strict >).
    Customer at exactly 10 observations is mature."""
    baseline = {"GOOGLE LLC": {"n": 10.0, "last": "2026-03-15"}}
    assert _asn_unfamiliar_for_customer("COMCAST CABLE", baseline, 10.0) is True


def test_gate_boundary_at_nine_does_not_fire() -> None:
    """Customer at 9 observations is still cold-start."""
    baseline = {"GOOGLE LLC": {"n": 9.0, "last": "2026-03-15"}}
    assert _asn_unfamiliar_for_customer("COMCAST CABLE", baseline, 9.0) is False


def test_custom_gate_threshold_honored() -> None:
    """Default gate is 10; the helper accepts a custom threshold so
    callers (e.g. integration tests, or future trust-tiered rules)
    can override it."""
    baseline = {"GOOGLE LLC": {"n": 25.0, "last": "2026-03-15"}}
    assert _asn_unfamiliar_for_customer("COMCAST CABLE", baseline, 20.0) is True
    assert (
        _asn_unfamiliar_for_customer("COMCAST CABLE", baseline, 20.0, gate_threshold=30.0) is False
    )


def test_asn_org_string_keys_not_numeric_ids() -> None:
    """The existing ip_asn_stats is keyed by enrichment.asn_org
    (string name like 'GOOGLE LLC'), not numeric ASN. Pin the key
    shape so future enrichment changes don't silently break the
    rule (e.g. if MaxMind began returning 'AS15169' or numeric IDs
    instead of org names)."""
    baseline_by_name = {"GOOGLE LLC": {"n": 100.0, "last": "2026-03-15"}}
    # The org name matches → familiar
    assert _asn_unfamiliar_for_customer("GOOGLE LLC", baseline_by_name, 50.0) is False
    # A numeric ASN string would NOT match the name-keyed baseline
    assert _asn_unfamiliar_for_customer("AS15169", baseline_by_name, 50.0) is True
