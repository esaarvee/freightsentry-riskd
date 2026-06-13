"""Unit tests for the `tenant_config` parameter on build_context.

build_context and build_modification_context accept
`tenant_config: TenantConfig` as a required keyword arg, wired in at the
endpoint call sites. The currency-derived fields are the consumers.

3 tests:
1. build_context REQUIRES tenant_config (TypeError if omitted)
2. build_modification_context REQUIRES tenant_config
3. ctx shape carries no new keys from the bare-context baseline
"""

from __future__ import annotations

import inspect

from app.context import build_context, build_modification_context


def test_build_context_signature_requires_tenant_config() -> None:
    sig = inspect.signature(build_context)
    assert "tenant_config" in sig.parameters
    param = sig.parameters["tenant_config"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty, (
        "tenant_config must be a required keyword arg; 4B/4C consumers depend on it being present"
    )


def test_build_modification_context_signature_requires_tenant_config() -> None:
    sig = inspect.signature(build_modification_context)
    assert "tenant_config" in sig.parameters
    param = sig.parameters["tenant_config"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


def test_allowed_context_fields_count_is_76_after_6a8() -> None:
    """Base whitelist starts at 66; 5 currency-derived fields → 71;
    2 case-3a signals → 73; 2 case-3b signals → 75;
    1 case-3b sophisticated signal → 76. The symmetric
    triangle-mismatch field is swapped for the asymmetric
    outbound-destination-mismatch field — net unchanged at 76.
    unfamiliar_asn_for_customer → 77."""
    from app.rules import ALLOWED_CONTEXT_FIELDS

    assert len(ALLOWED_CONTEXT_FIELDS) == 77
    # The 5 currency-derived fields must remain present in the whitelist.
    assert "shipment_currency" in ALLOWED_CONTEXT_FIELDS
    assert "shipment_value_threshold_high" in ALLOWED_CONTEXT_FIELDS
    assert "shipment_value_threshold_new_user" in ALLOWED_CONTEXT_FIELDS
    assert "shipment_value_threshold_medium" in ALLOWED_CONTEXT_FIELDS
    assert "shipment_value_threshold_low" in ALLOWED_CONTEXT_FIELDS
    # The 2 case-3a fields must be present.
    assert "origin_via_carrier_dropoff" in ALLOWED_CONTEXT_FIELDS
    assert "shipment_route_unfamiliar_for_customer" in ALLOWED_CONTEXT_FIELDS
    # customer_registered_country is retained; the symmetric
    # triangle-mismatch is replaced with the asymmetric
    # outbound mismatch.
    assert "customer_registered_country" in ALLOWED_CONTEXT_FIELDS
    assert "customer_destination_country_mismatch_outbound" in ALLOWED_CONTEXT_FIELDS
    assert "customer_country_triangle_mismatch" not in ALLOWED_CONTEXT_FIELDS
    # The sophisticated signal must be present.
    assert "shipment_route_rare_for_tenant" in ALLOWED_CONTEXT_FIELDS
