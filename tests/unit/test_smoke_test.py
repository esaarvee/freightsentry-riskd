"""Unit tests for the Phase 6D.5 smoke-test script (scripts/smoke_test.py).

Pure-Python exercises of the `assert_response` validation logic. The
network POST loop in `_post_booking` is integration-only (implicit
coverage during the deploy.yml workflow run + local operator runs);
the unit suite focuses on the assertion machinery that decides
success vs failure on a given (status, elapsed, body) tuple.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.smoke_test import (
    _DECISION_BANDS,
    _LATENCY_CEILING_SECONDS,
    _SMOKE_PAYLOAD,
    assert_response,
)


def _ok_body(request_id: str = "smoke-1") -> dict[str, Any]:
    return {
        "request_id": request_id,
        "decision": "ALLOW",
        "score": 0.1,
        "classification": "GREEN",
        "risk_level": "LOW",
        "triggered_rules": [],
        "risk_factors": [],
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_assert_response_accepts_canonical_allow_body() -> None:
    assert_response(
        200,
        0.150,
        _ok_body(),
        expected_request_id="smoke-1",
    )


def test_assert_response_accepts_each_decision_band() -> None:
    for band in _DECISION_BANDS:
        body = _ok_body()
        body["decision"] = band
        # Score must be valid for each band; tests use mid-range.
        body["score"] = 0.5
        assert_response(200, 0.150, body, expected_request_id="smoke-1")


# ---------------------------------------------------------------------------
# HTTP status
# ---------------------------------------------------------------------------


def test_assert_response_rejects_non_200() -> None:
    with pytest.raises(AssertionError, match="expected HTTP 200"):
        assert_response(401, 0.050, {"detail": "unauthorized"}, expected_request_id="smoke-1")


def test_assert_response_rejects_500() -> None:
    with pytest.raises(AssertionError, match="expected HTTP 200"):
        assert_response(500, 0.050, {"detail": "internal error"}, expected_request_id="smoke-1")


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------


def test_assert_response_rejects_latency_above_ceiling() -> None:
    with pytest.raises(AssertionError, match="latency"):
        assert_response(
            200, _LATENCY_CEILING_SECONDS + 0.5, _ok_body(), expected_request_id="smoke-1"
        )


def test_assert_response_accepts_latency_just_below_ceiling() -> None:
    # Just-below should pass; strict-less-than gate.
    assert_response(
        200, _LATENCY_CEILING_SECONDS - 0.001, _ok_body(), expected_request_id="smoke-1"
    )


def test_assert_response_rejects_latency_at_exactly_ceiling() -> None:
    """The assert is strict-less-than. Exactly at ceiling fails so we get
    a loud signal rather than a silent borderline pass."""
    with pytest.raises(AssertionError, match="latency"):
        assert_response(200, _LATENCY_CEILING_SECONDS, _ok_body(), expected_request_id="smoke-1")


# ---------------------------------------------------------------------------
# request_id echo
# ---------------------------------------------------------------------------


def test_assert_response_rejects_mismatched_request_id() -> None:
    with pytest.raises(AssertionError, match="request_id mismatch"):
        body = _ok_body(request_id="something-else")
        assert_response(200, 0.150, body, expected_request_id="smoke-1")


def test_assert_response_rejects_missing_request_id() -> None:
    body = _ok_body()
    del body["request_id"]
    with pytest.raises(AssertionError, match="request_id mismatch"):
        assert_response(200, 0.150, body, expected_request_id="smoke-1")


# ---------------------------------------------------------------------------
# decision
# ---------------------------------------------------------------------------


def test_assert_response_rejects_unknown_decision_band() -> None:
    body = _ok_body()
    body["decision"] = "MAYBE"
    with pytest.raises(AssertionError, match="decision must be one of"):
        assert_response(200, 0.150, body, expected_request_id="smoke-1")


def test_assert_response_rejects_missing_decision() -> None:
    body = _ok_body()
    del body["decision"]
    with pytest.raises(AssertionError, match="decision must be one of"):
        assert_response(200, 0.150, body, expected_request_id="smoke-1")


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


def test_assert_response_rejects_score_above_one() -> None:
    body = _ok_body()
    body["score"] = 1.5
    with pytest.raises(AssertionError, match=r"score must be in"):
        assert_response(200, 0.150, body, expected_request_id="smoke-1")


def test_assert_response_rejects_score_below_zero() -> None:
    body = _ok_body()
    body["score"] = -0.1
    with pytest.raises(AssertionError, match=r"score must be in"):
        assert_response(200, 0.150, body, expected_request_id="smoke-1")


def test_assert_response_rejects_non_numeric_score() -> None:
    body = _ok_body()
    body["score"] = "not-a-number"
    with pytest.raises(AssertionError, match="score must be a number"):
        assert_response(200, 0.150, body, expected_request_id="smoke-1")


def test_assert_response_rejects_bool_score() -> None:
    """Defensive: bool is a numeric type in Python (True == 1), but a
    boolean as a score would mask a backend regression."""
    body = _ok_body()
    body["score"] = True
    with pytest.raises(AssertionError, match="score must be a number"):
        assert_response(200, 0.150, body, expected_request_id="smoke-1")


def test_assert_response_accepts_score_at_zero_and_one() -> None:
    """Inclusive boundary at both ends."""
    for s in (0.0, 1.0):
        body = _ok_body()
        body["score"] = s
        assert_response(200, 0.150, body, expected_request_id="smoke-1")


# ---------------------------------------------------------------------------
# Body shape
# ---------------------------------------------------------------------------


def test_assert_response_rejects_non_dict_body() -> None:
    with pytest.raises(AssertionError, match="expected JSON object body"):
        assert_response(
            200,
            0.150,
            [1, 2, 3],  # type: ignore[arg-type]
            expected_request_id="smoke-1",
        )


# ---------------------------------------------------------------------------
# Payload sanity
# ---------------------------------------------------------------------------


def test_smoke_payload_currency_is_cad() -> None:
    """Phase 6B project default. Smoke tenant in the runbook is
    configured with allowed_currencies including CAD."""
    assert _SMOKE_PAYLOAD["shipment"]["currency"] == "CAD"


def test_smoke_payload_has_all_required_booking_request_fields() -> None:
    """Catches a payload regression that drops a required field."""
    assert {"request_id", "customer", "user", "source_ip", "shipment", "booking_ts"} <= set(
        _SMOKE_PAYLOAD
    )
    assert "external_id" in _SMOKE_PAYLOAD["customer"]
    assert "external_id" in _SMOKE_PAYLOAD["user"]
    shipment = _SMOKE_PAYLOAD["shipment"]
    assert {"origin", "destination", "value", "channel", "currency"} <= set(shipment)
