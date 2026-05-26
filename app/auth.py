"""API token validation + AuthContext.

Bearer-token authentication. Tokens are hashed via SHA-256 at issuance
and only the hash is stored in `api_tokens.token_hash`. Per-request, the
dependency:
  1. Extracts the Bearer token from the Authorization header.
  2. Hashes it.
  3. Looks it up in `api_tokens`.
  4. Returns an `AuthContext(tenant_id, role)`.

The dependency does NOT set the RLS session variable — that happens
inside each endpoint's transaction (so `SET LOCAL` survives only the
endpoint's own transactional scope).

In Phase 1, the connecting role is the postgres bootstrap superuser
(bypasses RLS), so the api_tokens lookup succeeds without an RLS-exempt
path. Phase 5 hardening will need either a SECURITY DEFINER function
for the lookup or an RLS policy exemption. Tracked in .claude/STATUS.md.

`AUTH_ENABLED=false` is the local-dev carve-out: the dependency returns
a synthetic AuthContext without touching the DB. Production deploys
must keep AUTH_ENABLED=true.
"""

import hashlib
from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import Header, HTTPException

from app.config import get_settings
from app.db import get_conn

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AuthContext:
    tenant_id: int
    role: str


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing authorization header")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="authorization must be Bearer <token>")
    token = parts[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="empty bearer token")
    return token


async def require_api_token(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext:
    settings = get_settings()
    if not settings.auth_enabled:
        return AuthContext(tenant_id=1, role="tenant")

    token = _extract_bearer(authorization)
    token_hash = _hash_token(token)

    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT tenant_id, role FROM api_tokens WHERE token_hash = $1",
            token_hash,
        )

    if row is None:
        _log.info("auth.invalid_token", token_hash_prefix=token_hash[:8], metric=True)
        raise HTTPException(status_code=401, detail="invalid api token")

    _log.info(
        "auth.success",
        tenant_id=row["tenant_id"],
        role=row["role"],
        token_hash_prefix=token_hash[:8],
        metric=True,
    )
    return AuthContext(tenant_id=row["tenant_id"], role=row["role"])
