"""require_api_token — Bearer-token resolution + DB lookup."""

import pytest
from fastapi import HTTPException

from app.auth import AuthContext, require_api_token


async def test_missing_header_returns_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_api_token(authorization=None)
    assert exc_info.value.status_code == 401
    assert "missing" in exc_info.value.detail.lower()


async def test_malformed_header_returns_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_api_token(authorization="NotBearer abc")
    assert exc_info.value.status_code == 401


async def test_empty_bearer_returns_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_api_token(authorization="Bearer ")
    assert exc_info.value.status_code == 401


async def test_unknown_token_returns_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_api_token(authorization="Bearer this-token-does-not-exist")
    assert exc_info.value.status_code == 401
    assert "invalid" in exc_info.value.detail.lower()


async def test_valid_token_returns_auth_context(
    seeded_api_token: tuple[str, int],
) -> None:
    plaintext, tenant_id = seeded_api_token
    ctx = await require_api_token(authorization=f"Bearer {plaintext}")
    assert isinstance(ctx, AuthContext)
    assert ctx.tenant_id == tenant_id
    assert ctx.role == "tenant"
