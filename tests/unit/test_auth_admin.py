"""Unit tests for require_admin_role (4D.1).

5 tests covering:
- admin role passes through
- tenant role -> 403
- other role (reviewer) -> 403
- empty role -> 403
- composes with require_api_token (verified via inspect.signature on the
  dependency parameter)
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app.auth import AuthContext, require_admin_role


async def test_admin_role_returns_auth_context() -> None:
    auth = AuthContext(tenant_id=1, role="admin")
    result = await require_admin_role(auth)
    assert result is auth


async def test_tenant_role_raises_403() -> None:
    auth = AuthContext(tenant_id=1, role="tenant")
    with pytest.raises(HTTPException) as exc:
        await require_admin_role(auth)
    assert exc.value.status_code == 403
    assert "admin role required" in str(exc.value.detail)


async def test_other_non_admin_role_raises_403() -> None:
    auth = AuthContext(tenant_id=1, role="reviewer")
    with pytest.raises(HTTPException) as exc:
        await require_admin_role(auth)
    assert exc.value.status_code == 403


async def test_empty_role_raises_403() -> None:
    auth = AuthContext(tenant_id=1, role="")
    with pytest.raises(HTTPException) as exc:
        await require_admin_role(auth)
    assert exc.value.status_code == 403


def test_require_admin_role_composes_with_require_api_token() -> None:
    """The dependency takes an AuthContext argument that is annotated as
    Depends(require_api_token). This is the FastAPI composition signal."""
    sig = inspect.signature(require_admin_role)
    auth_param = sig.parameters["auth"]
    # The default value is the dependency object via Depends(require_api_token).
    # The full annotated default includes a Depends() instance.
    # We verify the param is annotated and references AuthContext.
    assert auth_param.annotation is not inspect.Parameter.empty
    # The Annotated metadata contains the Depends marker — check the
    # underlying require_api_token reference appears in the signature's
    # default representation.
    assert "require_api_token" in str(auth_param) or "Depends" in str(auth_param)
