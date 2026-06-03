"""Integration tests for TenantConfig load wiring across endpoints (4A.6).

Confirms that every endpoint loads its own tenant's config per request,
cross-tenant isolation holds, and per-request fresh load returns
updated config between calls. Stored-corruption test verifies the
endpoint's failure mode when JSONB is invalid (no caching layer in
Phase 4 means subsequent retries also fail until the bad config is
fixed).

5B note: the spy-based tests below patch `load_tenant_config_cached`
directly with a spy that delegates to the underlying
`load_tenant_config`. This BYPASSES the 60s TTL cache so the original
"endpoint-loads-config" invariants still verify under each request.
Cache-staleness behavior (production contract) is covered by 5B unit
tests in `tests/unit/test_tenant_config_cache.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

import asyncpg
import pytest
from httpx import AsyncClient

from app import tenant_config as tenant_config_module
from app.auth import AuthContext, require_api_token
from app.main import app
from app.tenant_config import TenantConfig, load_tenant_config
from tests.conftest import _cleanup_tenant, set_test_tenant_id


async def _make_minimal_booking_payload() -> dict[str, object]:
    return {
        "request_id": "REQ-tc-1",
        "customer": {"external_id": "cust-tc-1"},
        "user": {"external_id": "user-tc-1"},
        "source_ip": "192.0.2.10",
        "shipment": {
            "origin": {"address": "1 Main St"},
            "destination": {"address": "2 Park Ave"},
            "value": "100",
            "channel": "web",
        },
        "booking_ts": datetime.now(UTC).isoformat(),
    }


async def _post_booking_under_tenant(unauth_client: AsyncClient, tenant_id: int, token: str) -> int:
    """POST a minimal booking under a specific tenant; return HTTP status."""
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=tenant_id, role="tenant"
    )
    try:
        payload = await _make_minimal_booking_payload()
        # Make the request_id unique per call so we don't trip idempotency.
        payload["request_id"] = f"REQ-tc-{tenant_id}-{datetime.now(UTC).timestamp()}"
        r = await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=payload,
        )
        return r.status_code
    finally:
        app.dependency_overrides.pop(require_api_token, None)


async def test_seeded_config_tenant_booking_succeeds(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Booking endpoint loads the seeded_tenant config successfully.

    Phase 6B: seeded_tenant fixture now seeds `allowed_currencies =
    ["USD", "CAD"]` (multi-currency convenience for the broader
    integration suite). This test asserts that the loader returns
    that exact list, plus the all-None overrides for the remaining
    optional fields."""
    status = await _post_booking_under_tenant(unauth_client, seeded_tenant, "x")
    assert status == 200

    tc = await load_tenant_config(db_conn, seeded_tenant)
    assert tc.maturity_age_days is None
    assert tc.allowed_currencies == ["USD", "CAD"]


async def test_custom_config_tenant_booking_succeeds(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Custom override config doesn't break the endpoint (4A doesn't consume yet).

    Phase 6B: seeded_tenant fixture seeds multi-currency by default,
    so the UPDATE here must preserve allowed_currencies (otherwise the
    USD booking payload gets rejected against the override config)."""
    await db_conn.execute(
        "UPDATE tenants SET config = $1::jsonb WHERE id = $2",
        json.dumps(
            {
                "maturity_age_days": 90,
                "cold_start_grace_days": 7,
                "allowed_currencies": ["USD", "CAD"],
            }
        ),
        seeded_tenant,
    )
    status = await _post_booking_under_tenant(unauth_client, seeded_tenant, "x")
    assert status == 200

    tc = await load_tenant_config(db_conn, seeded_tenant)
    assert tc.maturity_age_days == 90
    assert tc.cold_start_grace_days == 7


async def test_load_tenant_config_called_with_each_request_tenant_id(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Patch the loader, alternate tenants, confirm correct tenant_id passed each time."""

    # Create a second tenant (Phase 6B: multi-currency config to match
    # the seeded_tenant fixture default).
    other_tenant_id: int = await db_conn.fetchval(
        'INSERT INTO tenants (name, config) VALUES ($1, \'{"allowed_currencies": ["USD", "CAD"]}\'::jsonb) RETURNING id',
        "other-tc",
    )

    # Patch where the booking endpoint imports load_tenant_config.
    call_tenant_ids: list[int] = []
    real_loader = tenant_config_module.load_tenant_config

    async def spy(conn: asyncpg.Connection, tid: int) -> TenantConfig:
        call_tenant_ids.append(tid)
        return await real_loader(conn, tid)

    try:
        with patch("app.api.booking.load_tenant_config_cached", spy):
            await _post_booking_under_tenant(unauth_client, seeded_tenant, "x")
            await _post_booking_under_tenant(unauth_client, other_tenant_id, "x")
            await _post_booking_under_tenant(unauth_client, seeded_tenant, "x")
    finally:
        await set_test_tenant_id(db_conn, other_tenant_id)
        await _cleanup_tenant(db_conn, other_tenant_id)
        await set_test_tenant_id(db_conn, seeded_tenant)

    assert call_tenant_ids == [
        seeded_tenant,
        other_tenant_id,
        seeded_tenant,
    ], "Each request must load its own tenant's config; sequence must match request order"


async def test_per_request_fresh_load_reflects_db_update(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Update tenants.config between two requests; second load reflects
    change. The spy patches `load_tenant_config_cached` and delegates to
    the underlying `load_tenant_config`, BYPASSING the 60s TTL cache
    (5B). In production, the second request would hit the cache and
    return the stale config until the 60s window expires. This test
    verifies the underlying loader invariant; cache-staleness behavior
    is covered separately by 5B unit tests."""
    captured: list[TenantConfig] = []
    real_loader = tenant_config_module.load_tenant_config

    async def spy(conn: asyncpg.Connection, tid: int) -> TenantConfig:
        cfg = await real_loader(conn, tid)
        captured.append(cfg)
        return cfg

    with patch("app.api.booking.load_tenant_config_cached", spy):
        s1 = await _post_booking_under_tenant(unauth_client, seeded_tenant, "x")
        # Bump the tenant's config between requests.
        await db_conn.execute(
            "UPDATE tenants SET config = $1::jsonb, updated_at = now() WHERE id = $2",
            json.dumps({"cold_start_grace_days": 21, "allowed_currencies": ["USD", "CAD"]}),
            seeded_tenant,
        )
        s2 = await _post_booking_under_tenant(unauth_client, seeded_tenant, "x")

    # Both bookings must succeed — otherwise the test could silently pass on
    # loader-returned-correct-value while the endpoint is broken for other
    # reasons.
    assert s1 == 200
    assert s2 == 200
    assert len(captured) == 2
    assert captured[0].cold_start_grace_days == 0
    assert captured[1].cold_start_grace_days == 21


async def test_modification_endpoint_loads_tenant_config(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Modification endpoint also loads tenant_config — exercise the path
    by sending a modification for a non-existent prior booking (404 expected
    but the loader still ran first)."""
    seen: list[int] = []
    real_loader = tenant_config_module.load_tenant_config

    async def spy(conn: asyncpg.Connection, tid: int) -> TenantConfig:
        seen.append(tid)
        return await real_loader(conn, tid)

    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=seeded_tenant, role="tenant"
    )
    try:
        with patch("app.api.modification.load_tenant_config_cached", spy):
            r = await unauth_client.post(
                "/api/v1/shipments/modification/evaluate",
                json={
                    "request_id": "MOD-tc-1",
                    "original_request_id": "REQ-nonexistent",
                    "modification_ts": datetime.now(UTC).isoformat(),
                    "modification_type": "value",
                    "new_value": {"value": 500},
                },
            )
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    # 404 because original booking doesn't exist; but the loader must have
    # been called before the prior-lookup query.
    assert r.status_code == 404
    assert seen == [seeded_tenant]


async def test_feedback_endpoint_loads_tenant_config(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Feedback endpoint loads tenant_config (parked variable in 4A)."""
    seen: list[int] = []
    real_loader = tenant_config_module.load_tenant_config

    async def spy(conn: asyncpg.Connection, tid: int) -> TenantConfig:
        seen.append(tid)
        return await real_loader(conn, tid)

    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=seeded_tenant, role="tenant"
    )
    try:
        with patch("app.api.feedback.load_tenant_config_cached", spy):
            r = await unauth_client.post(
                "/api/v1/shipments/feedback",
                json={
                    "request_id": "FB-tc-1",
                    "target_request_id": "REQ-nonexistent",
                    "label": "approved",
                    "feedback_ts": datetime.now(UTC).isoformat(),
                },
            )
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    # 404 because target doesn't exist; loader still ran first.
    assert r.status_code == 404
    assert seen == [seeded_tenant]


async def test_invalid_stored_jsonb_propagates_validationerror(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Stored config corruption (value_caps missing tier keys) surfaces as
    pydantic.ValidationError. Phase 4A intentionally has no try/except
    around the loader — the error propagates through the ASGI transport.
    Phase 4D admin endpoints + Phase 5 hardening may translate this to a
    500 response with a structured log entry; for 4A the propagation IS
    the documented failure mode (stored-data corruption is a
    configuration error, not a client error)."""
    from pydantic import ValidationError

    await db_conn.execute(
        "UPDATE tenants SET config = $1::jsonb WHERE id = $2",
        json.dumps({"value_caps": {"USD": {"high": 10000}}}),  # missing 3 tiers
        seeded_tenant,
    )
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=seeded_tenant, role="tenant"
    )
    try:
        payload = await _make_minimal_booking_payload()
        payload["request_id"] = f"REQ-corrupt-{datetime.now(UTC).timestamp()}"
        with pytest.raises(ValidationError, match="value_caps"):
            await unauth_client.post(
                "/api/v1/shipments/booking/evaluate",
                json=payload,
            )
    finally:
        app.dependency_overrides.pop(require_api_token, None)


async def test_cross_tenant_isolation_via_endpoint_loads(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """tenant_a custom config + tenant_b custom config — each request loads its own."""
    # Tenant A: maturity_age_days=60
    await db_conn.execute(
        "UPDATE tenants SET config = $1::jsonb WHERE id = $2",
        json.dumps({"maturity_age_days": 60}),
        seeded_tenant,
    )
    # Tenant B: maturity_age_days=200
    other_tenant_id: int = await db_conn.fetchval(
        "INSERT INTO tenants (name, config) VALUES ($1, $2::jsonb) RETURNING id",
        "iso-other",
        json.dumps({"maturity_age_days": 200}),
    )

    captured: list[TenantConfig] = []
    real_loader = tenant_config_module.load_tenant_config

    async def spy(conn: asyncpg.Connection, tid: int) -> TenantConfig:
        cfg = await real_loader(conn, tid)
        captured.append(cfg)
        return cfg

    try:
        with patch("app.api.booking.load_tenant_config_cached", spy):
            await _post_booking_under_tenant(unauth_client, seeded_tenant, "x")
            await _post_booking_under_tenant(unauth_client, other_tenant_id, "x")
    finally:
        await set_test_tenant_id(db_conn, other_tenant_id)
        await _cleanup_tenant(db_conn, other_tenant_id)
        await set_test_tenant_id(db_conn, seeded_tenant)

    assert captured[0].tenant_id == seeded_tenant
    assert captured[0].maturity_age_days == 60
    assert captured[1].tenant_id == other_tenant_id
    assert captured[1].maturity_age_days == 200
