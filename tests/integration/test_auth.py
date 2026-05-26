"""require_api_token — Bearer-token resolution + DB lookup."""

import hashlib

import pytest
from fastapi import HTTPException

from app.auth import AuthContext, _hash_token, require_api_token
from app.config import get_settings

# ---------------------------------------------------------------------------
# Hash-algorithm pin — locks SHA-256 so a future swap to bcrypt/sha512/etc.
# breaks this test instead of silently invalidating every persisted token.
# ---------------------------------------------------------------------------


def test_hash_token_uses_sha256() -> None:
    plaintext = "rsk_pinned_test_token"
    expected = "ccacb6e214a3bb72773e5d2d8f2e662e8995a7f449af728297b94f0816a0215b"
    assert _hash_token(plaintext) == expected
    assert _hash_token(plaintext) == hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Bearer-header parsing — error cases distinguished by detail message.
# ---------------------------------------------------------------------------


async def test_missing_header_returns_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_api_token(authorization=None)
    assert exc_info.value.status_code == 401
    assert "missing" in exc_info.value.detail.lower()


async def test_malformed_header_returns_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_api_token(authorization="NotBearer abc")
    assert exc_info.value.status_code == 401
    assert "bearer" in exc_info.value.detail.lower()


async def test_empty_bearer_returns_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_api_token(authorization="Bearer ")
    assert exc_info.value.status_code == 401
    assert "empty" in exc_info.value.detail.lower()


async def test_unknown_token_returns_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_api_token(authorization="Bearer this-token-does-not-exist")
    assert exc_info.value.status_code == 401
    assert "invalid" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Token-to-AuthContext resolution — happy path + role plumbing + case-insensitive
# scheme name.
# ---------------------------------------------------------------------------


async def test_valid_token_returns_auth_context(
    seeded_api_token: tuple[str, int],
) -> None:
    plaintext, tenant_id = seeded_api_token
    ctx = await require_api_token(authorization=f"Bearer {plaintext}")
    assert isinstance(ctx, AuthContext)
    assert ctx.tenant_id == tenant_id
    assert ctx.role == "tenant"


async def test_admin_token_resolves_admin_role(
    seeded_admin_token: tuple[str, int],
) -> None:
    """Role field is read from the DB row, not hardcoded — catch role-plumbing bugs."""
    plaintext, tenant_id = seeded_admin_token
    ctx = await require_api_token(authorization=f"Bearer {plaintext}")
    assert ctx.tenant_id == tenant_id
    assert ctx.role == "admin"


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "BeArEr"])
async def test_bearer_scheme_case_insensitive(
    seeded_api_token: tuple[str, int], scheme: str
) -> None:
    """RFC 7235 allows mixed-case scheme names; production uses .lower()
    to normalise. Removing the lower() would break "bearer foo" silently
    if this test didn't exist."""
    plaintext, tenant_id = seeded_api_token
    ctx = await require_api_token(authorization=f"{scheme} {plaintext}")
    assert ctx.tenant_id == tenant_id


# ---------------------------------------------------------------------------
# AUTH_ENABLED=false carve-out — local-dev synthetic AuthContext bypasses DB.
# ---------------------------------------------------------------------------


async def test_auth_disabled_returns_synthetic_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With AUTH_ENABLED=false, require_api_token returns AuthContext(1, "tenant")
    without consulting the DB or requiring an Authorization header."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    get_settings.cache_clear()
    try:
        ctx = await require_api_token(authorization=None)
        assert ctx.tenant_id == 1
        assert ctx.role == "tenant"
    finally:
        get_settings.cache_clear()
