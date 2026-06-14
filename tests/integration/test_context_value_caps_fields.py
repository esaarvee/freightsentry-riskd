"""Integration tests for the 5 currency-derived Context fields populated by
build_context.

DB-backed because build_context loads the baseline + enricher inside an
asyncpg transaction. Although these could read as unit tests,
per .ai/conventions.md:207-208, DB-touching tests live under
tests/integration/ (pre-commit pytest hook runs unit only — DB-backed
tests under unit/ would break the fast-commit contract).
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, date, datetime
from decimal import Decimal
from ipaddress import IPv4Address
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.context import build_context, build_modification_context
from app.enrich import Enricher, EnrichmentRow
from app.models import (
    Address,
    BookingRequest,
    CustomerData,
    ModificationRequest,
    ShipmentData,
    UserData,
)
from app.signal_helpers import hmac_hex
from app.tenant_config import DEFAULT_VALUE_CAPS, TenantConfig
from tests.conftest import make_default_tenant_config


async def _seed_minimal_customer_and_baseline(
    conn: asyncpg.Connection, tenant_id: int, external_id: str
) -> tuple[int, asyncpg.Record]:
    cust_id: int = await conn.fetchval(
        "INSERT INTO customers (tenant_id, external_id) VALUES ($1, $2) RETURNING id",
        tenant_id,
        external_id,
    )
    await conn.execute(
        """
        INSERT INTO customer_baselines (tenant_id, customer_id, decay_anchor_date)
        VALUES ($1, $2, $3)
        """,
        tenant_id,
        cust_id,
        date.today(),
    )
    row = await conn.fetchrow(
        "SELECT * FROM customers WHERE id = $1 AND tenant_id = $2",
        cust_id,
        tenant_id,
    )
    assert row is not None
    return cust_id, row


def _make_payload(currency: str = "USD", value: str = "100") -> BookingRequest:
    request_id = f"REQ-vc-{secrets.token_hex(4)}"
    return BookingRequest(
        request_id=request_id,
        shipment_id=f"ship-{request_id}",
        transaction_number=f"txn-{request_id}",
        customer=CustomerData(external_id="vc"),
        user=UserData(external_id="vc-u"),
        source_ip=IPv4Address("192.0.2.50"),
        shipment=ShipmentData(
            origin=Address(address="1 Main St"),
            destination=Address(address="2 Park Ave"),
            value=Decimal(value),
            channel="web",
            currency=currency,
        ),
        booking_ts=datetime.now(UTC),
    )


def _enricher_stub(row: EnrichmentRow) -> Enricher:
    e = Enricher.__new__(Enricher)
    e.enrich = AsyncMock(return_value=row)  # type: ignore[method-assign]
    return e


def _tc(value_caps: dict[str, dict[str, float]] | None = None) -> TenantConfig:
    if value_caps is None:
        return make_default_tenant_config()
    now = datetime.now(UTC)
    return TenantConfig(tenant_id=1, value_caps=value_caps, created_at=now, updated_at=now)


# ---------------------------------------------------------------------------
# 6 tests
# ---------------------------------------------------------------------------


async def test_empty_value_caps_usd_uses_default_thresholds(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    cust_id, row = await _seed_minimal_customer_and_baseline(db_conn, seeded_tenant, "vc-1")
    payload = _make_payload(currency="USD")
    dest_hmac = hmac_hex("2 Park Ave", b"secret")
    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=_enricher_stub(EnrichmentRow.empty("192.0.2.50")),
        payload=payload,
        destination_hmac=dest_hmac,
        tenant_config=_tc(value_caps=None),
    )
    assert ctx["shipment_currency"] == "USD"
    assert ctx["shipment_value_threshold_high"] == 10000.0
    assert ctx["shipment_value_threshold_new_user"] == 5000.0
    assert ctx["shipment_value_threshold_medium"] == 2000.0
    assert ctx["shipment_value_threshold_low"] == 1000.0


async def test_custom_value_caps_usd_overrides_all_four_tiers(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    cust_id, row = await _seed_minimal_customer_and_baseline(db_conn, seeded_tenant, "vc-2")
    custom = {"USD": {"high": 99999.0, "new_user": 50000.0, "medium": 20000.0, "low": 10000.0}}
    payload = _make_payload(currency="USD")
    dest_hmac = hmac_hex("2 Park Ave", b"secret")
    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=_enricher_stub(EnrichmentRow.empty("192.0.2.50")),
        payload=payload,
        destination_hmac=dest_hmac,
        tenant_config=_tc(value_caps=custom),
    )
    # All 4 tiers must populate from the custom dict — catches a copy-paste
    # bug where build_context populated, e.g., caps["high"] four times.
    assert ctx["shipment_value_threshold_high"] == 99999.0
    assert ctx["shipment_value_threshold_new_user"] == 50000.0
    assert ctx["shipment_value_threshold_medium"] == 20000.0
    assert ctx["shipment_value_threshold_low"] == 10000.0


async def test_custom_cad_caps_used_when_payload_is_cad(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    cust_id, row = await _seed_minimal_customer_and_baseline(db_conn, seeded_tenant, "vc-3")
    cad_caps = {"high": 12500.0, "new_user": 6250.0, "medium": 2500.0, "low": 1250.0}
    payload = _make_payload(currency="CAD")
    dest_hmac = hmac_hex("2 Park Ave", b"secret")
    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=_enricher_stub(EnrichmentRow.empty("192.0.2.50")),
        payload=payload,
        destination_hmac=dest_hmac,
        tenant_config=_tc(value_caps={"CAD": cad_caps}),
    )
    assert ctx["shipment_currency"] == "CAD"
    assert ctx["shipment_value_threshold_high"] == 12500.0
    assert ctx["shipment_value_threshold_low"] == 1250.0


async def test_currency_with_no_caps_falls_back_to_cad_default_with_warning(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """Operator misconfig: CAD-only value_caps but payload currency is USD.
    resolve_value_caps falls back to DEFAULT_VALUE_CAPS["CAD"] AND emits a
    `tenant_config.value_caps.fallback` warning with metric=True.

    The fallback target is DEFAULT_VALUE_CAPS["CAD"]; this test
    exercises the CAD-fallback path."""
    cust_id, row = await _seed_minimal_customer_and_baseline(db_conn, seeded_tenant, "vc-4")
    only_cad = {"CAD": {"high": 1.0, "new_user": 2.0, "medium": 3.0, "low": 4.0}}
    payload = _make_payload(currency="USD")
    dest_hmac = hmac_hex("2 Park Ave", b"secret")
    with patch("app.tenant_config._log") as mock_log:
        ctx, _b, _e = await build_context(
            db_conn,
            tenant_id=seeded_tenant,
            customer_id=cust_id,
            customer_row=row,
            enricher=_enricher_stub(EnrichmentRow.empty("192.0.2.50")),
            payload=payload,
            destination_hmac=dest_hmac,
            tenant_config=_tc(value_caps=only_cad),
        )
    assert ctx["shipment_currency"] == "USD"
    # CAD-default fallback values, NOT the custom CAD ones (which would
    # apply if currency were CAD).
    assert ctx["shipment_value_threshold_high"] == DEFAULT_VALUE_CAPS["CAD"]["high"]
    # Warning emission pins the misconfig signal so it can't silently regress.
    mock_log.warning.assert_called_once()
    call_args = mock_log.warning.call_args
    assert call_args.args[0] == "tenant_config.value_caps.fallback"
    assert call_args.kwargs["currency"] == "USD"
    assert call_args.kwargs["metric"] is True


@pytest.mark.parametrize("currency", ["CAD", "EUR"])
async def test_currency_round_trip_with_fallback_thresholds(
    db_conn: asyncpg.Connection, seeded_tenant: int, currency: str
) -> None:
    """With value_caps=None the resolver falls back to CAD-default thresholds
    for ANY currency. Assert both the currency round-trip AND the resolved
    threshold values to make the test meaningful.

    Parametrize is ["CAD", "EUR"] because the fallback target is
    CAD-default; a USD-keyed fallback would be self-referential.
    """
    cust_id, row = await _seed_minimal_customer_and_baseline(
        db_conn, seeded_tenant, f"vc-rt-{currency}"
    )
    payload = _make_payload(currency=currency)
    dest_hmac = hmac_hex("2 Park Ave", b"secret")
    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=_enricher_stub(EnrichmentRow.empty("192.0.2.50")),
        payload=payload,
        destination_hmac=dest_hmac,
        tenant_config=_tc(value_caps=None),
    )
    assert ctx["shipment_currency"] == currency
    # value_caps=None means CAD-default is used regardless of payload currency
    assert ctx["shipment_value_threshold_high"] == DEFAULT_VALUE_CAPS["CAD"]["high"]
    assert ctx["shipment_value_threshold_low"] == DEFAULT_VALUE_CAPS["CAD"]["low"]


async def test_modification_synthetic_booking_uses_modifications_currency(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """build_modification_context overrides the synthesized booking's currency
    to reflect the MODIFICATION's currency, not the prior shipment's. Pins
    the model_copy override in app/context.py."""
    cust_id, row = await _seed_minimal_customer_and_baseline(db_conn, seeded_tenant, "vc-mod")
    user_id: int = await db_conn.fetchval(
        "INSERT INTO users (tenant_id, customer_id, external_id) VALUES ($1, $2, $3) RETURNING id",
        seeded_tenant,
        cust_id,
        "u-mod",
    )

    shipment_row = await db_conn.fetchrow(
        """
        INSERT INTO shipments (
            id, tenant_id, customer_id, user_id, request_id, source_ip,
            origin, destination, value, channel, booking_ts, destination_hmac,
            transaction_number
        )
        VALUES (
            $4, $1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9, $10, $11, 'tx-' || $4
        )
        RETURNING *
        """,
        seeded_tenant,
        cust_id,
        user_id,
        "REQ-prior-mod",
        "192.0.2.50",
        json.dumps({"address": "1 Main St"}),
        json.dumps({"address": "2 Park Ave"}),
        Decimal("100"),
        "web",
        datetime.now(UTC),
        hmac_hex("2 Park Ave", b"secret"),
    )
    assert shipment_row is not None

    # Modification carries CAD; synthesized booking must reflect CAD, not the
    # prior shipment's (USD) currency.
    mod_payload = ModificationRequest(
        request_id="MOD-cad-1",
        original_request_id="REQ-prior-mod",
        shipment_id="REQ-prior-mod",
        transaction_number="tx-REQ-prior-mod",
        modification_ts=datetime.now(UTC),
        modification_type="value",
        new_value={"value": 250},
        currency="CAD",
    )

    ctx, _b, _e = await build_modification_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=_enricher_stub(EnrichmentRow.empty("192.0.2.50")),
        payload=mod_payload,
        prior_shipment_row=shipment_row,
        customer_external_id="vc-mod",
        user_external_id="u-mod",
        hmac_secret=b"secret",
        tenant_config=_tc(value_caps=None),
    )

    # The synthesized booking's currency was overridden — ctx reflects CAD,
    # not the prior shipment's USD default.
    assert ctx["shipment_currency"] == "CAD"


async def test_all_five_currency_fields_present_in_ctx(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    cust_id, row = await _seed_minimal_customer_and_baseline(db_conn, seeded_tenant, "vc-all")
    payload = _make_payload(currency="USD")
    dest_hmac = hmac_hex("2 Park Ave", b"secret")
    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=_enricher_stub(EnrichmentRow.empty("192.0.2.50")),
        payload=payload,
        destination_hmac=dest_hmac,
        tenant_config=_tc(),
    )
    expected = {
        "shipment_currency",
        "shipment_value_threshold_high",
        "shipment_value_threshold_new_user",
        "shipment_value_threshold_medium",
        "shipment_value_threshold_low",
    }
    assert expected.issubset(ctx.keys())
