"""Pydantic validation tests for the feedback endpoint request/response.

Covers field-level constraints (length bounds, enum membership, datetime
coercion, extra="forbid") that the endpoint relies on at the validation
boundary. Two-tier idempotency + label-monotonicity semantics belong on
the endpoint (3B.3), not the model.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models import FeedbackRequest, FeedbackResponse


def _base_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_id": "fb-001",
        "target_request_id": "book-001",
        "label": "rejected",
        "feedback_ts": datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC),
    }
    payload.update(overrides)
    return payload


def test_feedback_request_minimum_required_fields() -> None:
    req = FeedbackRequest.model_validate(_base_payload())
    assert req.request_id == "fb-001"
    assert req.target_request_id == "book-001"
    assert req.label == "rejected"
    assert req.note is None
    assert req.operator_id is None


def test_feedback_request_rejects_empty_request_id() -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(_base_payload(request_id=""))


def test_feedback_request_rejects_empty_target_request_id() -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(_base_payload(target_request_id=""))


@pytest.mark.parametrize("label", ["approved", "rejected", "fraud_confirmed"])
def test_label_accepts_enumerated_values(label: str) -> None:
    req = FeedbackRequest.model_validate(_base_payload(label=label))
    assert req.label == label


@pytest.mark.parametrize("label", ["", "REJECTED", "fraud", "denied", "approve"])
def test_label_rejects_other_values(label: str) -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(_base_payload(label=label))


def test_request_id_at_max_length_accepted() -> None:
    req = FeedbackRequest.model_validate(_base_payload(request_id="x" * 128))
    assert len(req.request_id) == 128


def test_request_id_over_max_length_rejected() -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(_base_payload(request_id="x" * 129))


def test_target_request_id_at_max_length_accepted() -> None:
    req = FeedbackRequest.model_validate(_base_payload(target_request_id="x" * 128))
    assert len(req.target_request_id) == 128


def test_target_request_id_over_max_length_rejected() -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(_base_payload(target_request_id="x" * 129))


def test_note_at_max_length_accepted() -> None:
    req = FeedbackRequest.model_validate(_base_payload(note="x" * 2048))
    assert req.note is not None and len(req.note) == 2048


def test_note_over_max_length_rejected() -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(_base_payload(note="x" * 2049))


def test_operator_id_optional() -> None:
    req = FeedbackRequest.model_validate(_base_payload(operator_id="ops-mary"))
    assert req.operator_id == "ops-mary"


def test_operator_id_over_max_length_rejected() -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(_base_payload(operator_id="x" * 129))


def test_feedback_ts_required() -> None:
    payload = _base_payload()
    payload.pop("feedback_ts")
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(payload)


def test_feedback_ts_rejects_non_datetime_string() -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(_base_payload(feedback_ts="not-a-date"))


def test_extra_fields_forbidden_on_request() -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(_base_payload(unknown_field="x"))


def test_response_applied_required_bool() -> None:
    resp = FeedbackResponse.model_validate(
        {"applied": True, "previous_label": None, "target_request_id": "book-001"}
    )
    assert resp.applied is True
    assert resp.previous_label is None


def test_response_previous_label_accepts_enum_or_none() -> None:
    for label in (None, "approved", "rejected", "fraud_confirmed"):
        resp = FeedbackResponse.model_validate(
            {"applied": False, "previous_label": label, "target_request_id": "book-001"}
        )
        assert resp.previous_label == label


def test_response_previous_label_rejects_other_strings() -> None:
    with pytest.raises(ValidationError):
        FeedbackResponse.model_validate(
            {"applied": True, "previous_label": "unknown", "target_request_id": "book-001"}
        )
