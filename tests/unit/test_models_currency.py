"""Unit tests for the currency field on BookingRequest.shipment and
ModificationRequest. 11 tests (10 from plan + 1 backward-compat).

ISO 4217 shape validation (3 uppercase letters) is enforced at the Pydantic
layer; the allowed-list check against tenant_config.allowed_currencies runs
at request time in app/api/booking.py and app/api/modification.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from ipaddress import IPv4Address

import pytest
from pydantic import ValidationError

from app.models import (
    Address,
    BookingRequest,
    CustomerData,
    ModificationRequest,
    ShipmentData,
    UserData,
)


def _minimal_shipment(**overrides: object) -> ShipmentData:
    base: dict[str, object] = {
        "origin": Address(address="1 Main St"),
        "destination": Address(address="2 Park Ave"),
        "value": Decimal("100"),
        "channel": "web",
    }
    base.update(overrides)
    return ShipmentData(**base)  # type: ignore[arg-type]


def _minimal_modification(**overrides: object) -> ModificationRequest:
    base: dict[str, object] = {
        "request_id": "MOD-1",
        "original_request_id": "REQ-1",
        "modification_ts": datetime.now(UTC),
        "modification_type": "value",
        "new_value": {"value": 200},
    }
    base.update(overrides)
    return ModificationRequest(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ShipmentData currency
# ---------------------------------------------------------------------------


def test_shipment_currency_default_is_usd() -> None:
    s = _minimal_shipment()
    assert s.currency == "USD"


def test_shipment_currency_usd_accepted() -> None:
    s = _minimal_shipment(currency="USD")
    assert s.currency == "USD"


def test_shipment_currency_cad_accepted() -> None:
    s = _minimal_shipment(currency="CAD")
    assert s.currency == "CAD"


def test_shipment_currency_lowercase_rejected() -> None:
    with pytest.raises(ValidationError):
        _minimal_shipment(currency="usd")


def test_shipment_currency_two_letter_rejected() -> None:
    with pytest.raises(ValidationError):
        _minimal_shipment(currency="US")


def test_shipment_currency_four_letter_rejected() -> None:
    with pytest.raises(ValidationError):
        _minimal_shipment(currency="USDX")


def test_shipment_currency_digits_rejected() -> None:
    with pytest.raises(ValidationError):
        _minimal_shipment(currency="123")


# ---------------------------------------------------------------------------
# ModificationRequest currency
# ---------------------------------------------------------------------------


def test_modification_currency_default_is_usd() -> None:
    m = _minimal_modification()
    assert m.currency == "USD"


def test_modification_currency_eur_accepted() -> None:
    m = _minimal_modification(currency="EUR")
    assert m.currency == "EUR"


def test_modification_currency_lowercase_rejected() -> None:
    with pytest.raises(ValidationError):
        _minimal_modification(currency="eur")


# ---------------------------------------------------------------------------
# Backward-compat: payloads built without `currency` still construct a valid
# BookingRequest.
# ---------------------------------------------------------------------------


def test_booking_request_without_currency_uses_usd_default() -> None:
    payload = BookingRequest(
        request_id="REQ-x",
        customer=CustomerData(external_id="c"),
        user=UserData(external_id="u"),
        source_ip=IPv4Address("192.0.2.1"),
        shipment=_minimal_shipment(),  # no currency override
        booking_ts=datetime.now(UTC),
    )
    assert payload.shipment.currency == "USD"
