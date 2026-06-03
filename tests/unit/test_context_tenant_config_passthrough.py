"""Unit tests for 4A.3 — `tenant_config` parameter on build_context.

The 4A.3 commit extends build_context and build_modification_context to
accept `tenant_config: TenantConfig` as a required keyword arg. 4A.4
wires it in at the endpoint call sites. In 4A no rule consumes the
config — 4B/4C are the consumers.

3 tests:
1. build_context REQUIRES tenant_config (TypeError if omitted)
2. build_modification_context REQUIRES tenant_config
3. ctx shape is unchanged from Phase 3 — no new keys yet (4B adds 5)
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
    """4A starts at 66; 4B.4 adds 5 currency-derived fields → 71;
    6A.2 adds 2 case-3a signals → 73; 6A.5 adds 2 case-3b signals → 75;
    6A.8 adds 1 case-3b sophisticated signal → 76."""
    from app.rules import ALLOWED_CONTEXT_FIELDS

    assert len(ALLOWED_CONTEXT_FIELDS) == 76
    # The 5 Phase 4B fields must remain present in the whitelist.
    assert "shipment_currency" in ALLOWED_CONTEXT_FIELDS
    assert "shipment_value_threshold_high" in ALLOWED_CONTEXT_FIELDS
    assert "shipment_value_threshold_new_user" in ALLOWED_CONTEXT_FIELDS
    assert "shipment_value_threshold_medium" in ALLOWED_CONTEXT_FIELDS
    assert "shipment_value_threshold_low" in ALLOWED_CONTEXT_FIELDS
    # The 2 Phase 6A.2 fields must be present.
    assert "origin_via_carrier_dropoff" in ALLOWED_CONTEXT_FIELDS
    assert "shipment_route_unfamiliar_for_customer" in ALLOWED_CONTEXT_FIELDS
    # The 2 Phase 6A.5 fields must be present.
    assert "customer_registered_country" in ALLOWED_CONTEXT_FIELDS
    assert "customer_country_triangle_mismatch" in ALLOWED_CONTEXT_FIELDS
    # The Phase 6A.8 sophisticated signal must be present.
    assert "shipment_route_rare_for_tenant" in ALLOWED_CONTEXT_FIELDS
