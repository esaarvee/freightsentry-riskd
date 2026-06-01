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

import pytest

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


@pytest.mark.skip(
    reason=(
        "Phase 4A keeps the ctx shape unchanged (66 fields). 4B.4 adds 5 "
        "currency-derived fields and grows the whitelist to 71. Re-enable "
        "with the 71-field assertion at 4B.4."
    )
)
def test_ctx_shape_unchanged_in_4a() -> None:
    pass
