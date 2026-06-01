"""E2E integration tests for scripts/tenant_onboard.py (4A.6).

Drives the script's `_onboard` async entry directly (rather than via
subprocess) so we can share the existing asyncpg pool and DB cleanup
infrastructure with the rest of the test suite.

4 tests:
- Create new tenant → tenant row + token + token works against endpoint
- Re-run without --rotate-token → tenant unchanged, no new token
- Re-run with --rotate-token → new token issued AND prior tokens revoked
- Initial config file applied → tenants.config matches load_tenant_config output
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import asyncpg
from httpx import AsyncClient

from app.auth import _hash_token, require_api_token
from app.main import app
from app.tenant_config import load_tenant_config
from scripts.tenant_onboard import _onboard


def _extract_token(stdout: str) -> str:
    """Pull the token plaintext out of stdout (format: `api_token=<plaintext>  # ...`)."""
    for line in stdout.splitlines():
        if line.startswith("api_token="):
            tail = line[len("api_token=") :]
            # strip optional trailing comment
            return tail.split("  ", 1)[0].strip()
    msg = f"no api_token line in stdout: {stdout!r}"
    raise AssertionError(msg)


def _extract_tenant_id(stdout: str) -> int:
    """Pull the tenant id out of the created/updated line."""
    for line in stdout.splitlines():
        if line.startswith(("created tenant id=", "updated tenant id=")):
            return int(line.split("id=", 1)[1].split(" ", 1)[0])
    msg = f"no tenant-id line in stdout: {stdout!r}"
    raise AssertionError(msg)


async def _cleanup(db_conn: asyncpg.Connection, name: str) -> None:
    rows = await db_conn.fetch("SELECT id FROM tenants WHERE name = $1", name)
    for row in rows:
        tid = row["id"]
        await db_conn.execute("DELETE FROM api_tokens WHERE tenant_id = $1", tid)
        await db_conn.execute("DELETE FROM tenants WHERE id = $1", tid)


async def test_create_new_tenant_yields_working_token(
    db_conn: asyncpg.Connection, unauth_client: AsyncClient
) -> None:
    """Script creates tenant + token; token validates against /health-like flow."""
    external_id = "onboard-test-create"
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            await _onboard(
                external_id=external_id,
                display_name="Create Test",
                initial_config={},
                rotate_token=False,
            )
        stdout = buf.getvalue()
        assert "created tenant id=" in stdout
        tenant_id = _extract_tenant_id(stdout)
        token = _extract_token(stdout)

        # Token actually validates: the api_tokens row exists with the
        # expected hash, role=tenant, and our tenant_id.
        row = await db_conn.fetchrow(
            "SELECT tenant_id, role FROM api_tokens WHERE token_hash = $1",
            _hash_token(token),
        )
        assert row is not None
        assert row["tenant_id"] == tenant_id
        assert row["role"] == "tenant"

        # The endpoint accepts the token (use the real auth path, not the override).
        # Verify by exercising the require_api_token dependency directly.
        app.dependency_overrides.pop(require_api_token, None)
        r = await unauth_client.post(
            "/api/v1/shipments/feedback",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "request_id": "FB-onboard-test",
                "target_request_id": "REQ-nonexistent",
                "label": "approved",
                "feedback_ts": "2026-06-01T00:00:00Z",
            },
        )
        # 404 because target doesn't exist; importantly NOT 401.
        assert r.status_code == 404
    finally:
        await _cleanup(db_conn, external_id)


async def test_rerun_without_rotate_does_not_issue_new_token(
    db_conn: asyncpg.Connection,
) -> None:
    external_id = "onboard-test-norotate"
    try:
        with redirect_stdout(io.StringIO()) as first:
            await _onboard(
                external_id=external_id,
                display_name="No Rotate",
                initial_config={},
                rotate_token=False,
            )
        first_token = _extract_token(first.getvalue())
        first_tenant_id = _extract_tenant_id(first.getvalue())

        # Re-run without --rotate-token — no new token, no second created line.
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            await _onboard(
                external_id=external_id,
                display_name="No Rotate",
                initial_config={},
                rotate_token=False,
            )
        second_stdout = buf2.getvalue()
        assert "updated tenant id=" in second_stdout
        assert _extract_tenant_id(second_stdout) == first_tenant_id
        # Script prints `api_token: existing (use --rotate-token ...)` (colon)
        # on re-run; the equals-sign form `api_token=` only appears when a new
        # token is actually emitted.
        assert "api_token: existing" in second_stdout
        assert "api_token=" not in second_stdout

        # First token still works.
        row = await db_conn.fetchrow(
            "SELECT tenant_id FROM api_tokens WHERE token_hash = $1",
            _hash_token(first_token),
        )
        assert row is not None
        assert row["tenant_id"] == first_tenant_id

        # Exactly one token row for this tenant.
        n = await db_conn.fetchval(
            "SELECT count(*) FROM api_tokens WHERE tenant_id = $1", first_tenant_id
        )
        assert n == 1
    finally:
        await _cleanup(db_conn, external_id)


async def test_rotate_token_revokes_prior_token(
    db_conn: asyncpg.Connection,
) -> None:
    external_id = "onboard-test-rotate"
    try:
        with redirect_stdout(io.StringIO()) as first:
            await _onboard(
                external_id=external_id,
                display_name="Rotate Test",
                initial_config={},
                rotate_token=False,
            )
        first_token = _extract_token(first.getvalue())
        first_tenant_id = _extract_tenant_id(first.getvalue())

        # Rotate — prior token must be REVOKED (Phase 4A.5 cycle-2 fix).
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            await _onboard(
                external_id=external_id,
                display_name="Rotate Test",
                initial_config={},
                rotate_token=True,
            )
        second_stdout = buf2.getvalue()
        assert "revoked 1 prior api_token" in second_stdout
        new_token = _extract_token(second_stdout)
        assert new_token != first_token

        # Prior token is no longer present in api_tokens.
        old_row = await db_conn.fetchrow(
            "SELECT tenant_id FROM api_tokens WHERE token_hash = $1",
            _hash_token(first_token),
        )
        assert old_row is None

        # New token is present.
        new_row = await db_conn.fetchrow(
            "SELECT tenant_id FROM api_tokens WHERE token_hash = $1",
            _hash_token(new_token),
        )
        assert new_row is not None
        assert new_row["tenant_id"] == first_tenant_id
    finally:
        await _cleanup(db_conn, external_id)


async def test_initial_config_applied_visible_via_loader(
    db_conn: asyncpg.Connection,
) -> None:
    external_id = "onboard-test-config"
    initial = {
        "maturity_age_days": 120,
        "cold_start_grace_days": 30,
        "allowed_currencies": ["USD", "CAD"],
    }
    try:
        with redirect_stdout(io.StringIO()) as buf:
            await _onboard(
                external_id=external_id,
                display_name="Config Test",
                initial_config=initial,
                rotate_token=False,
            )
        tenant_id = _extract_tenant_id(buf.getvalue())

        # Loader returns the same shape.
        tc = await load_tenant_config(db_conn, tenant_id)
        assert tc.maturity_age_days == 120
        assert tc.cold_start_grace_days == 30
        assert tc.allowed_currencies == ["USD", "CAD"]

        # Raw column matches what we passed in.
        stored = await db_conn.fetchval("SELECT config FROM tenants WHERE id = $1", tenant_id)
        if isinstance(stored, str):
            stored = json.loads(stored)
        assert stored == initial
    finally:
        await _cleanup(db_conn, external_id)
