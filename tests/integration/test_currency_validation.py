"""Request-time currency validation tests.

Covers both booking and modification endpoints — Pydantic enforces ISO 4217
shape at the model layer; allowed-list enforcement runs against
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
    request_id = f"REQ-curr-{datetime.now(UTC).timestamp()}"
    payload: dict[str, object] = {
        "request_id": request_id,
        "shipment_id": f"ship-{request_id}",
        "transaction_number": f"txn-{request_id}",
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
        "shipment_id": "ship-REQ-nonexistent",
        "transaction_number": "txn-REQ-nonexistent",
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
    from app import tenant_config_cache

    await db_conn.execute(
        "UPDATE tenants SET config = $1::jsonb WHERE id = $2",
        json.dumps({"allowed_currencies": currencies}),
        tenant_id,
    )
    # Cache invalidation: the tenants.config UPDATE is otherwise
    # invisible to the endpoint for up to 60s within a single test run.
    tenant_config_cache._reset_for_tests()


# ---------------------------------------------------------------------------
# Booking endpoint currency validation
# ---------------------------------------------------------------------------


async def test_booking_no_currency_field_succeeds_against_multi_currency_default_tenant(
    seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Backward compat: existing payloads without `currency` default to USD
    at the Pydantic layer. The seeded_tenant fixture seeds
    allowed_currencies=["USD","CAD"] so the USD payload default is in the
    allowed list. 200. (The project-default tenant config is CAD-only,
    but the fixture preserves USD acceptance.)"""
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


async def test_booking_cad_only_tenant_eur_currency_rejected_with_400(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """A tenant configured single-currency (CAD only) rejects a non-allowed
    payload currency (EUR) with 400 and the allowed-list in the message.

    The seeded_tenant fixture seeds multi-currency
    (USD + CAD) for the broader integration suite. This test explicitly
    overrides to CAD-only via _set_allowed_currencies to exercise the
    single-currency-tenant-rejects-non-allowed path: a CAD-only tenant
    rejects EUR (a currency the seeded_tenant never includes)."""
    await _set_allowed_currencies(db_conn, seeded_tenant, ["CAD"])
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=seeded_tenant, role="tenant"
    )
    try:
        r = await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_minimal_booking_payload(currency="EUR"),
        )
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    assert r.status_code == 400
    assert "EUR" in r.json()["detail"]
    assert "CAD" in r.json()["detail"]


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


async def test_booking_explicit_usd_against_multi_currency_default_tenant_succeeds(
    seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Explicit currency=USD works against the seeded_tenant fixture
    (which seeds USD+CAD)."""
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


async def test_modification_no_currency_succeeds_against_multi_currency_default_tenant(
    seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Modification payload without `currency` defaults to USD at the
    Pydantic layer; the seeded_tenant fixture (USD+CAD) allows
    USD. 404 because original booking doesn't exist (loader + currency
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


async def test_modification_multi_currency_default_tenant_eur_rejected_with_400(
    seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """seeded_tenant allows USD+CAD only. EUR modification → 400 because
    it's not in either currency list."""
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
    """Explicit currency=USD works against the seeded_tenant fixture
    (USD+CAD). 404 for missing original (currency check
    passes, loader path takes over)."""
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
