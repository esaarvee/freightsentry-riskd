"""Pydantic validation tests for the modification endpoint request/response.

Covers field-level constraints (length bounds, enum membership, IP shape)
and the structural invariants the endpoint relies on at the validation
boundary.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models import ModificationRequest, ModificationResponse


def _base_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_id": "mod-001",
        "original_request_id": "book-001",
        "shipment_id": "ship-001",
        "transaction_number": "txn-001",
        "modification_ts": datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC),
        "modification_type": "value",
        "new_value": {"value": 1500},
    }
    payload.update(overrides)
    return payload


def test_modification_request_minimum_required_fields() -> None:
    req = ModificationRequest.model_validate(_base_payload())
    assert req.request_id == "mod-001"
    assert req.original_request_id == "book-001"
    assert req.source_ip is None
    assert req.user is None


def test_modification_request_rejects_empty_request_id() -> None:
    with pytest.raises(ValidationError):
        ModificationRequest.model_validate(_base_payload(request_id=""))


def test_modification_request_rejects_empty_original_request_id() -> None:
    with pytest.raises(ValidationError):
        ModificationRequest.model_validate(_base_payload(original_request_id=""))


@pytest.mark.parametrize(
    "mod_type",
    ["destination", "value", "recipient", "service_level", "pickup_time"],
)
def test_modification_type_accepts_enumerated_values(mod_type: str) -> None:
    req = ModificationRequest.model_validate(_base_payload(modification_type=mod_type))
    assert req.modification_type == mod_type


@pytest.mark.parametrize("mod_type", ["", "delete", "destintion", "VALUE", "address"])
def test_modification_type_rejects_other_values(mod_type: str) -> None:
    with pytest.raises(ValidationError):
        ModificationRequest.model_validate(_base_payload(modification_type=mod_type))


def test_source_ip_accepts_ipv4_string() -> None:
    req = ModificationRequest.model_validate(_base_payload(source_ip="1.2.3.4"))
    assert str(req.source_ip) == "1.2.3.4"


def test_source_ip_rejects_garbage() -> None:
    with pytest.raises(ValidationError):
        ModificationRequest.model_validate(_base_payload(source_ip="not-an-ip"))


def test_new_value_accepts_empty_dict() -> None:
    req = ModificationRequest.model_validate(_base_payload(new_value={}))
    assert req.new_value == {}


def test_new_value_accepts_arbitrary_keys() -> None:
    payload = _base_payload(new_value={"foo": 1, "bar": "x", "nested": {"y": 2}})
    req = ModificationRequest.model_validate(payload)
    assert req.new_value["nested"] == {"y": 2}


def test_reason_max_length_enforced() -> None:
    with pytest.raises(ValidationError):
        ModificationRequest.model_validate(_base_payload(reason="x" * 513))


def test_reason_at_max_length_accepted() -> None:
    req = ModificationRequest.model_validate(_base_payload(reason="x" * 512))
    assert req.reason is not None and len(req.reason) == 512


def test_user_external_id_required_when_user_present() -> None:
    with pytest.raises(ValidationError):
        ModificationRequest.model_validate(_base_payload(user={"external_id": ""}))


def test_extra_fields_forbidden_on_request() -> None:
    with pytest.raises(ValidationError):
        ModificationRequest.model_validate(_base_payload(unknown_field="x"))


def test_request_id_at_max_length_accepted() -> None:
    req = ModificationRequest.model_validate(_base_payload(request_id="x" * 128))
    assert len(req.request_id) == 128


def test_request_id_over_max_length_rejected() -> None:
    with pytest.raises(ValidationError):
        ModificationRequest.model_validate(_base_payload(request_id="x" * 129))


def test_original_request_id_at_max_length_accepted() -> None:
    req = ModificationRequest.model_validate(_base_payload(original_request_id="x" * 128))
    assert len(req.original_request_id) == 128


def test_original_request_id_over_max_length_rejected() -> None:
    with pytest.raises(ValidationError):
        ModificationRequest.model_validate(_base_payload(original_request_id="x" * 129))


def test_modification_ts_required() -> None:
    payload = _base_payload()
    payload.pop("modification_ts")
    with pytest.raises(ValidationError):
        ModificationRequest.model_validate(payload)


def test_modification_ts_rejects_non_datetime_string() -> None:
    with pytest.raises(ValidationError):
        ModificationRequest.model_validate(_base_payload(modification_ts="not-a-date"))


def test_user_happy_path_roundtrip() -> None:
    req = ModificationRequest.model_validate(_base_payload(user={"external_id": "user-abc-123"}))
    assert req.user is not None
    assert req.user.external_id == "user-abc-123"


@pytest.mark.parametrize("decision", ["ALLOW", "REVIEW", "BLOCK"])
def test_modification_response_decision_accepts_known_values(decision: str) -> None:
    resp = ModificationResponse.model_validate(
        {
            "request_id": "mod-001",
            "shipment_id": "ship-001",
            "decision": decision,
            "score": 0.5,
            "classification": "YELLOW",
            "risk_level": "MEDIUM",
            "triggered_rules": [],
            "risk_factors": [],
        }
    )
    assert resp.decision == decision
    assert resp.shipment_id == "ship-001"


def test_modification_response_decision_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        ModificationResponse.model_validate(
            {
                "request_id": "mod-001",
                "decision": "REVERT",
                "score": 0.5,
                "classification": "YELLOW",
                "risk_level": "MEDIUM",
                "triggered_rules": [],
                "risk_factors": [],
            }
        )


@pytest.mark.parametrize("score", [-0.01, 1.01, 2.0])
def test_modification_response_score_must_be_in_unit_interval(score: float) -> None:
    with pytest.raises(ValidationError):
        ModificationResponse.model_validate(
            {
                "request_id": "mod-001",
                "decision": "ALLOW",
                "score": score,
                "classification": "GREEN",
                "risk_level": "LOW",
                "triggered_rules": [],
                "risk_factors": [],
            }
        )
