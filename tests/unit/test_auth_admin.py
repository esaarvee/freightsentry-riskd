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


def test_require_admin_role_composes_with_require_api_token_specifically() -> None:
    """Walk the Annotated metadata on the `auth` parameter and assert the
    Depends marker wraps `require_api_token` BY IDENTITY — not just any
    dependency. The earlier loose check (`"Depends" in str(...)`) would
    pass even if a future refactor swapped Depends(require_api_token)
    with Depends(some_other_function), which the operator watch-point
    explicitly asks us to prevent."""
    from typing import get_args, get_type_hints

    from app.auth import require_api_token

    hints = get_type_hints(require_admin_role, include_extras=True)
    auth_hint = hints["auth"]
    # auth_hint is Annotated[AuthContext, Depends(require_api_token)].
    # get_args returns (AuthContext, Depends(require_api_token)).
    type_, *metadata = get_args(auth_hint)
    assert type_ is AuthContext
    # Find the Depends instance in the metadata and verify its dependency.
    depends_objs = [m for m in metadata if hasattr(m, "dependency")]
    assert len(depends_objs) == 1, f"expected exactly one Depends marker, got {metadata}"
    assert depends_objs[0].dependency is require_api_token, (
        "require_admin_role must depend specifically on require_api_token "
        "(operator watch-point — prevents silent auth-bypass via dependency swap)"
    )
