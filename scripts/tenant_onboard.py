#!/usr/bin/env python3
"""Tenant onboarding utility (Phase 4A.5).

Creates a tenant row + an initial API token + writes an initial
TenantConfig (optionally loaded from a JSON file). Idempotent —
re-runs on the same `--external-id` (interpreted as `tenants.name`
for the Phase 4 schema) update the config JSONB and surface the
existing tenant id without creating a duplicate.

Usage:
    python scripts/tenant_onboard.py \\
        --external-id tenant-alpha \\
        --display-name "Alpha Corp" \\
        [--config-json /path/to/config.json] \\
        [--rotate-token]

The script prints the token ONCE on stdout — operator must capture
it immediately. Subsequent runs without `--rotate-token` print only
the tenant id; no token is reprinted.

Phase 4 limitations:
- No FK to `app_users.role` here — admin onboarding via this script
  is out of scope. Operator can INSERT into `app_users` manually for
  admin principals; Phase 5+ may add an `--admin-user` flag.
- Token printed in plaintext to stdout. Production usage should pipe
  to a secret manager rather than store the stdout output.

Exit codes:
  0 — success
  1 — invalid arguments / config JSON
  2 — DB error / multi-row tenant collision
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from app.auth import _hash_token
from app.config import get_settings
from app.tenant_config import parse_config_jsonb


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tenant onboarding for freightsentry-riskd.")
    p.add_argument(
        "--external-id",
        required=True,
        help="Stable tenant identifier (used as tenants.name).",
    )
    p.add_argument(
        "--display-name",
        required=True,
        help="Human-readable tenant display name.",
    )
    p.add_argument(
        "--config-json",
        type=Path,
        default=None,
        help="Path to a JSON file containing initial TenantConfig override fields.",
    )
    p.add_argument(
        "--rotate-token",
        action="store_true",
        help="Issue a new API token even if the tenant already exists.",
    )
    return p.parse_args()


def _load_initial_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        print(f"error: --config-json path does not exist: {path}", file=sys.stderr)
        sys.exit(1)
    with path.open(encoding="utf-8") as f:
        try:
            raw = json.load(f)
        except json.JSONDecodeError as e:
            print(f"error: --config-json is not valid JSON: {e}", file=sys.stderr)
            sys.exit(1)
    if not isinstance(raw, dict):
        print(
            "error: --config-json must contain a JSON object at the top level",
            file=sys.stderr,
        )
        sys.exit(1)
    return raw


def _validate_initial_config(config_dict: dict[str, Any]) -> None:
    """Validate the override shape against TenantConfig before writing to DB.

    The `tenant_id=1` and synthetic timestamps here are PLACEHOLDERS —
    parse_config_jsonb's contract requires them but they're never
    written. Only the override fields inside `config_dict` round-trip
    to the DB. A future `TenantConfig.validate_overrides(raw)` helper
    would be a cleaner seam; for now the placeholder pattern reuses
    the existing validator without adding API surface.
    """
    now = datetime.now(UTC)
    try:
        parse_config_jsonb(config_dict, tenant_id=1, created_at=now, updated_at=now)
    except Exception as e:
        print(
            f"error: initial config fails TenantConfig validation: {e}",
            file=sys.stderr,
        )
        sys.exit(1)


async def _onboard(
    external_id: str,
    display_name: str,
    initial_config: dict[str, Any],
    rotate_token: bool,
) -> None:
    settings = get_settings()
    conn = await asyncpg.connect(settings.database_url)
    try:
        async with conn.transaction():
            # Serialize concurrent runs targeting the same external_id.
            # `tenants.name` has no UNIQUE constraint today, so the
            # SELECT-then-INSERT pattern below would race. An
            # xact-scoped advisory lock keyed on the hash of external_id
            # serializes concurrent transactions without affecting other
            # tenants' onboarding.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                external_id,
            )

            # UPSERT tenant by external_id (stored as `tenants.name`).
            # If multiple rows match (from a pre-existing duplicate
            # written before the advisory-lock was added), fail loudly.
            existing = await conn.fetch(
                "SELECT id FROM tenants WHERE name = $1",
                external_id,
            )
            if len(existing) > 1:
                print(
                    f"error: multiple tenants with name={external_id!r} found; "
                    "manual intervention required",
                    file=sys.stderr,
                )
                sys.exit(2)
            if len(existing) == 1:
                tenant_id = existing[0]["id"]
                await conn.execute(
                    """
                    UPDATE tenants
                       SET config = $1::jsonb,
                           updated_at = now()
                     WHERE id = $2
                    """,
                    json.dumps(initial_config),
                    tenant_id,
                )
                print(f"updated tenant id={tenant_id} name={external_id!r}")
            else:
                tenant_id = await conn.fetchval(
                    """
                    INSERT INTO tenants (name, config)
                    VALUES ($1, $2::jsonb)
                    RETURNING id
                    """,
                    external_id,
                    json.dumps(initial_config),
                )
                print(f"created tenant id={tenant_id} name={external_id!r}")

            # `api_tokens` is RLS-enforced (0001_initial.py:298, 315).
            # Set the session-scoped tenant_id BEFORE any api_tokens
            # query — without this the script fails under the
            # production non-superuser `riskd_app` role with
            # "unrecognized configuration parameter app.tenant_id".
            # set_config(..., is_local=true) is xact-scoped so we don't
            # need to RESET on exit.
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id),
            )

            # Issue token if new tenant OR --rotate-token requested.
            # `--rotate-token` ACTUALLY rotates: delete prior tokens for
            # this tenant in the same transaction, then INSERT a new
            # one. Operator using --rotate-token after a suspected
            # compromise expects the prior token to stop working.
            token_count = await conn.fetchval(
                "SELECT count(*) FROM api_tokens WHERE tenant_id = $1",
                tenant_id,
            )
            if rotate_token or token_count == 0:
                if rotate_token and token_count > 0:
                    await conn.execute(
                        "DELETE FROM api_tokens WHERE tenant_id = $1",
                        tenant_id,
                    )
                    print(f"revoked {token_count} prior api_token(s) for tenant_id={tenant_id}")
                plaintext = secrets.token_urlsafe(32)
                await conn.execute(
                    """
                    INSERT INTO api_tokens (tenant_id, token_hash, role)
                    VALUES ($1, $2, 'tenant')
                    """,
                    tenant_id,
                    _hash_token(plaintext),
                )
                print(f"display_name={display_name!r}")
                print(f"api_token={plaintext}  # CAPTURE NOW — not reprinted")
            else:
                print("api_token: existing (use --rotate-token to issue a new one)")
    finally:
        await conn.close()


def main() -> None:
    args = _parse_args()
    initial_config = _load_initial_config(args.config_json)
    _validate_initial_config(initial_config)
    asyncio.run(
        _onboard(
            external_id=args.external_id,
            display_name=args.display_name,
            initial_config=initial_config,
            rotate_token=args.rotate_token,
        )
    )


if __name__ == "__main__":
    main()
