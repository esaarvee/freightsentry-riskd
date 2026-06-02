"""require_api_token writes api_tokens.last_used_at on success.

Four contracts under test:

- Success path stamps last_used_at; a second call's stamp >= the first.
- Invalid-token path raises 401 AND does not update any row.
- AUTH_ENABLED=false synthetic-principal path does not touch the DB.
- The UPDATE is autocommit on the auth-dependency's own pooled connection,
  so the write is visible across pool connections (no enclosing tx).
"""

import asyncpg
import pytest
from fastapi import HTTPException

from app.auth import _hash_token, require_api_token
from app.config import get_settings


async def _fetch_last_used_at(
    db_conn: asyncpg.Connection, token_hash: str
) -> asyncpg.Record | None:
    return await db_conn.fetchrow(
        "SELECT last_used_at FROM api_tokens WHERE token_hash = $1",
        token_hash,
    )


async def test_successful_auth_stamps_last_used_at(
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    plaintext, _ = seeded_api_token
    token_hash = _hash_token(plaintext)

    initial = await _fetch_last_used_at(db_conn, token_hash)
    assert initial is not None
    assert initial["last_used_at"] is None

    await require_api_token(authorization=f"Bearer {plaintext}")
    first = await _fetch_last_used_at(db_conn, token_hash)
    assert first is not None
    assert first["last_used_at"] is not None

    await require_api_token(authorization=f"Bearer {plaintext}")
    second = await _fetch_last_used_at(db_conn, token_hash)
    assert second is not None
    assert second["last_used_at"] is not None
    assert second["last_used_at"] >= first["last_used_at"]


async def test_invalid_token_raises_401_and_does_not_update(
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """The invalid-token branch must raise 401 (not silently succeed) AND
    must not touch last_used_at on any unrelated row."""
    plaintext, _ = seeded_api_token
    token_hash = _hash_token(plaintext)

    pre = await _fetch_last_used_at(db_conn, token_hash)
    assert pre is not None
    assert pre["last_used_at"] is None

    with pytest.raises(HTTPException) as exc:
        await require_api_token(authorization="Bearer not-the-seeded-token")
    assert exc.value.status_code == 401
    assert "invalid" in exc.value.detail.lower()

    post = await _fetch_last_used_at(db_conn, token_hash)
    assert post is not None
    assert post["last_used_at"] is None, (
        "invalid-token path must not update last_used_at on unrelated rows"
    )


async def test_auth_disabled_does_not_touch_database(
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUTH_ENABLED=false returns a synthetic AuthContext without any DB
    call. last_used_at on the seeded token must stay NULL — proves the
    carve-out branch never falls through to the writer."""
    plaintext, _ = seeded_api_token
    token_hash = _hash_token(plaintext)

    monkeypatch.setenv("AUTH_ENABLED", "false")
    get_settings.cache_clear()
    try:
        await require_api_token(authorization=None)
        await require_api_token(authorization=f"Bearer {plaintext}")
    finally:
        get_settings.cache_clear()

    row = await _fetch_last_used_at(db_conn, token_hash)
    assert row is not None
    assert row["last_used_at"] is None, "AUTH_ENABLED=false carve-out must not stamp last_used_at"


async def test_last_used_at_visible_across_pool_connections(
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """asyncpg autocommit contract: the auth-dependency UPDATE rides its
    own pooled connection with no enclosing transaction, so the write is
    immediately visible from a different pool connection (db_conn). If the
    UPDATE were instead inside an uncommitted transaction, db_conn would
    observe NULL until that tx committed — which would never happen since
    the auth dependency does not commit explicitly. Cross-connection
    visibility = proof of autocommit; that proof is the load-bearing
    contract for last_used_at observability."""
    plaintext, _ = seeded_api_token
    token_hash = _hash_token(plaintext)

    await require_api_token(authorization=f"Bearer {plaintext}")

    visible_via_other_conn = await _fetch_last_used_at(db_conn, token_hash)
    assert visible_via_other_conn is not None
    assert visible_via_other_conn["last_used_at"] is not None, (
        "last_used_at write must be visible from a separate pool connection"
    )
