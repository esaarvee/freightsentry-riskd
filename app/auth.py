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
from fastapi import Depends, Header, HTTPException

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
        # Synthetic principal for local-dev only; production must keep
        # AUTH_ENABLED=true. Warning level so the wrong-environment case
        # is loud in non-local log sinks.
        _log.warning("auth.carveout_active", tenant_id=1, metric=True)
        return AuthContext(tenant_id=1, role="tenant")

    token = _extract_bearer(authorization)
    token_hash = _hash_token(token)

    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT id, tenant_id, role FROM api_tokens WHERE token_hash = $1",
            token_hash,
        )
        if row is not None:
            # Stamp last_used_at on the success path only. asyncpg connections
            # default to autocommit, so the write persists independent of the
            # downstream endpoint handler's transaction outcome (verified by
            # the 5A.5 integration tests).
            await conn.execute(
                "UPDATE api_tokens SET last_used_at = now() WHERE id = $1 AND token_hash = $2",
                row["id"],
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


async def require_admin_role(
    auth: Annotated[AuthContext, Depends(require_api_token)],
) -> AuthContext:
    """Authorization layer: principal must carry role='admin' from api_tokens.

    Composes with require_api_token. Returns the same AuthContext (the
    tenant_id is preserved for downstream tenant-scoped queries).

    403 is returned for authenticated-but-not-admin (auth.role != 'admin');
    401 is returned upstream by require_api_token for unauthenticated calls.

    The AUTH_ENABLED=false local-dev carve-out returns AuthContext with
    role='tenant', which fails this check — admin endpoints under local
    dev require AUTH_ENABLED=true. Local admin testing pattern: set
    AUTH_ENABLED=true and seed an admin api_token via the onboarding
    script (4A.5) with --rotate-token, then manually update
    api_tokens.role to 'admin' (the script does not yet issue admin
    tokens — out of scope per 4A.5 decisions).
    """
    if auth.role != "admin":
        _log.info(
            "auth.admin_required_denied",
            tenant_id=auth.tenant_id,
            role=auth.role,
            metric=True,
        )
        raise HTTPException(
            status_code=403,
            detail="admin role required",
        )
    return auth
