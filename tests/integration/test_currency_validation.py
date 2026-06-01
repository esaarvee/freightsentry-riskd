"""Request-time currency validation tests (4B.3).

Covers both booking and modification endpoints — Pydantic enforces ISO 4217
shape at the model layer; 4B.3 adds allowed-list enforcement against
tenant_config.allowed_currencies. 9 tests total (5 booking + 4 modification).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import asyncpg
from httpx import AsyncClient

from app.auth import AuthContext, require_api_token
from app.main import app


def _minimal_booking_payload(currency: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_id": f"REQ-curr-{datetime.now(UTC).timestamp()}",
        "customer": {"external_id": "cust-curr"},
        "user": {"external_id": "user-curr"},
        "source_ip": "192.0.2.20",
        "shipment": {
            "origin": {"address": "1 Main St"},
            "destination": {"address": "2 Park Ave"},
            "value": "100",
            "channel": "web",
        },
        "booking_ts": datetime.now(UTC).isoformat(),
    }
    if currency is not None:
        shipment = payload["shipment"]
        assert isinstance(shipment, dict)
        shipment["currency"] = currency
    return payload


def _minimal_modification_payload(currency: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_id": f"MOD-curr-{datetime.now(UTC).timestamp()}",
        "original_request_id": "REQ-nonexistent",
        "modification_ts": datetime.now(UTC).isoformat(),
        "modification_type": "value",
        "new_value": {"value": 200},
    }
    if currency is not None:
        payload["currency"] = currency
    return payload


async def _set_allowed_currencies(
    db_conn: asyncpg.Connection, tenant_id: int, currencies: list[str]
) -> None:
    await db_conn.execute(
        "UPDATE tenants SET config = $1::jsonb WHERE id = $2",
        json.dumps({"allowed_currencies": currencies}),
        tenant_id,
    )


# ---------------------------------------------------------------------------
# Booking endpoint currency validation
# ---------------------------------------------------------------------------


async def test_booking_default_usd_tenant_no_currency_field_succeeds(
    seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Backward compat: existing payloads without `currency` default to USD,
    and the default tenant config has allowed_currencies=["USD"]. 200."""
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=seeded_tenant, role="tenant"
    )
    try:
        r = await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_minimal_booking_payload(),
        )
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    assert r.status_code == 200


async def test_booking_default_usd_tenant_cad_currency_rejected_with_400(
    seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Default tenant only allows USD. CAD payload → 400 with allowed-list in message."""
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=seeded_tenant, role="tenant"
    )
    try:
        r = await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_minimal_booking_payload(currency="CAD"),
        )
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    assert r.status_code == 400
    assert "CAD" in r.json()["detail"]
    assert "USD" in r.json()["detail"]


async def test_booking_multi_currency_tenant_cad_accepted(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Tenant allows USD, CAD, EUR. CAD payload → 200."""
    await _set_allowed_currencies(db_conn, seeded_tenant, ["USD", "CAD", "EUR"])
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=seeded_tenant, role="tenant"
    )
    try:
        r = await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_minimal_booking_payload(currency="CAD"),
        )
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    assert r.status_code == 200


async def test_booking_multi_currency_tenant_unsupported_currency_rejected(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Tenant allows USD, CAD, EUR. GBP payload → 400."""
    await _set_allowed_currencies(db_conn, seeded_tenant, ["USD", "CAD", "EUR"])
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=seeded_tenant, role="tenant"
    )
    try:
        r = await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_minimal_booking_payload(currency="GBP"),
        )
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    assert r.status_code == 400


async def test_booking_explicit_usd_against_default_tenant_succeeds(
    seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Sending currency=USD explicitly works against the default tenant."""
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=seeded_tenant, role="tenant"
    )
    try:
        r = await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_minimal_booking_payload(currency="USD"),
        )
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Modification endpoint currency validation
# ---------------------------------------------------------------------------


async def test_modification_default_usd_tenant_no_currency_succeeds(
    seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Modification payload without `currency` defaults to USD; default tenant
    allows USD. 404 because original booking doesn't exist (loader + currency
    check pass first)."""
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=seeded_tenant, role="tenant"
    )
    try:
        r = await unauth_client.post(
            "/api/v1/shipments/modification/evaluate",
            json=_minimal_modification_payload(),
        )
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    # NOT 400 — currency validation passed; anchor the 404 to the post-
    # currency-check path so a fail-open regression cannot pass this test.
    assert r.status_code == 404
    assert "Original booking not found" in r.json()["detail"]


async def test_modification_default_usd_tenant_eur_rejected_with_400(
    seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Default tenant only allows USD. EUR modification → 400."""
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=seeded_tenant, role="tenant"
    )
    try:
        r = await unauth_client.post(
            "/api/v1/shipments/modification/evaluate",
            json=_minimal_modification_payload(currency="EUR"),
        )
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    assert r.status_code == 400
    assert "EUR" in r.json()["detail"]


async def test_modification_multi_currency_tenant_eur_accepted(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Tenant allows USD, EUR. EUR modification → 404 (currency check passes;
    original booking still missing)."""
    await _set_allowed_currencies(db_conn, seeded_tenant, ["USD", "EUR"])
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=seeded_tenant, role="tenant"
    )
    try:
        r = await unauth_client.post(
            "/api/v1/shipments/modification/evaluate",
            json=_minimal_modification_payload(currency="EUR"),
        )
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    assert r.status_code == 404
    assert "Original booking not found" in r.json()["detail"]


async def test_modification_explicit_usd_succeeds(
    seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Sending currency=USD explicitly works against default tenant. 404 for
    missing original."""
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=seeded_tenant, role="tenant"
    )
    try:
        r = await unauth_client.post(
            "/api/v1/shipments/modification/evaluate",
            json=_minimal_modification_payload(currency="USD"),
        )
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    assert r.status_code == 404
    assert "Original booking not found" in r.json()["detail"]
