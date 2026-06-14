"""Validation tests for the platform-supplied identity fields.

``shipment_id`` and ``transaction_number`` are required text fields on both
the booking and modification request payloads (``min_length=1``,
``max_length=128``), and are echoed on the responses: booking echoes both,
modification echoes ``shipment_id``.
"""

from datetime import UTC, datetime
from decimal import Decimal
from ipaddress import IPv4Address

import pytest
from pydantic import ValidationError

from app.models import (
    Address,
    BookingRequest,
    BookingResponse,
    CustomerData,
    ModificationRequest,
    ModificationResponse,
    ShipmentData,
    UserData,
)


def _booking(**overrides: object) -> BookingRequest:
    base: dict[str, object] = {
        "request_id": "REQ-1",
        "shipment_id": "ship-1",
        "transaction_number": "txn-1",
        "customer": CustomerData(external_id="c"),
        "user": UserData(external_id="u"),
        "source_ip": IPv4Address("192.0.2.1"),
        "shipment": ShipmentData(
            origin=Address(address="1 Main St"),
            destination=Address(address="2 Park Ave"),
            value=Decimal("100"),
            channel="web",
        ),
        "booking_ts": datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return BookingRequest.model_validate(base)


def _modification(**overrides: object) -> ModificationRequest:
    base: dict[str, object] = {
        "request_id": "MOD-1",
        "original_request_id": "REQ-1",
        "shipment_id": "ship-1",
        "transaction_number": "txn-1",
        "modification_ts": datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC),
        "modification_type": "value",
        "new_value": {"value": 200},
    }
    base.update(overrides)
    return ModificationRequest.model_validate(base)


# ---------------------------------------------------------------------------
# BookingRequest
# ---------------------------------------------------------------------------


def test_booking_happy_path_carries_identity() -> None:
    req = _booking()
    assert req.shipment_id == "ship-1"
    assert req.transaction_number == "txn-1"


@pytest.mark.parametrize("field", ["shipment_id", "transaction_number"])
def test_booking_identity_field_required(field: str) -> None:
    base = {
        "request_id": "REQ-1",
        "shipment_id": "ship-1",
        "transaction_number": "txn-1",
        "customer": {"external_id": "c"},
        "user": {"external_id": "u"},
        "source_ip": "192.0.2.1",
        "shipment": {
            "origin": {"address": "1 Main St"},
            "destination": {"address": "2 Park Ave"},
            "value": 100,
            "channel": "web",
        },
        "booking_ts": "2026-05-27T12:00:00Z",
    }
    del base[field]
    with pytest.raises(ValidationError):
        BookingRequest.model_validate(base)


@pytest.mark.parametrize("field", ["shipment_id", "transaction_number"])
def test_booking_identity_field_rejects_empty(field: str) -> None:
    with pytest.raises(ValidationError):
        _booking(**{field: ""})


@pytest.mark.parametrize("field", ["shipment_id", "transaction_number"])
def test_booking_identity_field_at_max_length_accepted(field: str) -> None:
    req = _booking(**{field: "x" * 128})
    assert len(getattr(req, field)) == 128


@pytest.mark.parametrize("field", ["shipment_id", "transaction_number"])
def test_booking_identity_field_over_max_length_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        _booking(**{field: "x" * 129})


# ---------------------------------------------------------------------------
# ModificationRequest
# ---------------------------------------------------------------------------


def test_modification_happy_path_carries_identity() -> None:
    req = _modification()
    assert req.shipment_id == "ship-1"
    assert req.transaction_number == "txn-1"


@pytest.mark.parametrize("field", ["shipment_id", "transaction_number"])
def test_modification_identity_field_rejects_empty(field: str) -> None:
    with pytest.raises(ValidationError):
        _modification(**{field: ""})


@pytest.mark.parametrize("field", ["shipment_id", "transaction_number"])
def test_modification_identity_field_over_max_length_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        _modification(**{field: "x" * 129})


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


def test_booking_response_echoes_both_identity_fields() -> None:
    resp = BookingResponse(
        request_id="REQ-1",
        shipment_id="ship-1",
        transaction_number="txn-1",
        decision="ALLOW",
        score=0.1,
        classification="GREEN",
        risk_level="LOW",
        triggered_rules=[],
        risk_factors=[],
    )
    assert resp.shipment_id == "ship-1"
    assert resp.transaction_number == "txn-1"


def test_modification_response_echoes_shipment_id_only() -> None:
    resp = ModificationResponse(
        request_id="MOD-1",
        shipment_id="ship-1",
        decision="ALLOW",
        score=0.1,
        classification="GREEN",
        risk_level="LOW",
        triggered_rules=[],
        risk_factors=[],
    )
    assert resp.shipment_id == "ship-1"
    # transaction_number is intentionally NOT echoed on the modification
    # response (modification does not persist it; the cross-check guarantees it
    # matches the prior booking's stored value).
    assert not hasattr(resp, "transaction_number")
