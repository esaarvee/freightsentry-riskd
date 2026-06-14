"""Integration tests verifying that each metric=True event fired by
production endpoints produces a well-formed EMF block when run through
the configured emf_processor.

structlog's `capture_logs()` replaces the processor chain entirely with
a capture-only processor — emf_processor does NOT run inside it. We
test the end-to-end shape by:
  1. Hitting the production endpoint (real code path; no inline log
     recreation per the false-pass-test lesson).
  2. Capturing the structured event via `structlog.testing.capture_logs`.
  3. Running emf_processor on the captured event.
  4. Asserting the EMF block matches the production contract.

If a future regression removes a required field from a structured-log
call (e.g. drops `decision` from `risk.evaluation`), this test catches
it because the EMF block will not contain the expected dimension.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg
import structlog
from httpx import AsyncClient

from app.observability import EMF_NAMESPACE, emf_processor


def _booking_payload(request_id: str = "EMF-001") -> dict[str, Any]:
    return {
        "request_id": request_id,
        "shipment_id": f"ship-{request_id}",
        "transaction_number": f"txn-{request_id}",
        "customer": {"external_id": "emf-cust-1"},
        "user": {"external_id": "emf-user-1"},
        "source_ip": "192.0.2.30",
        "shipment": {
            "origin": {"address": "1 EMF Lane"},
            "destination": {"address": "2 EMF Ave"},
            "value": 1100.00,
            "channel": "api",
        },
        "booking_ts": "2026-06-02T08:00:00Z",
    }


def _modification_payload(
    *,
    request_id: str = "EMF-MOD-001",
    original_request_id: str = "EMF-001",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "original_request_id": original_request_id,
        "shipment_id": f"ship-{original_request_id}",
        "transaction_number": f"txn-{original_request_id}",
        "modification_ts": "2026-06-02T08:30:00Z",
        "modification_type": "value",
        "new_value": {"value": 1200},
    }


def _find_event(captured: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for ev in captured:
        if ev.get("event") == name:
            return ev
    return None


def _to_emf(event: dict[str, Any]) -> dict[str, Any]:
    """Apply emf_processor to a captured event_dict. Mirrors the
    production processor chain step that capture_logs bypasses."""
    return dict(emf_processor(None, "info", event))


async def test_risk_evaluation_event_carries_emf_block(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    token, _tenant_id = seeded_api_token
    with structlog.testing.capture_logs() as captured:
        resp = await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_booking_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text

    event = _find_event(captured, "risk.evaluation")
    assert event is not None, "production booking endpoint did not emit risk.evaluation"
    assert event.get("metric") is True

    emf = _to_emf(event)
    cw = emf["_aws"]["CloudWatchMetrics"][0]
    assert cw["Namespace"] == EMF_NAMESPACE
    assert cw["Dimensions"] == [["tenant_id", "decision"]]
    metric_names = {m["Name"] for m in cw["Metrics"]}
    assert {
        "score",
        "account_prior",
        "signal_score",
        "maturity",
        "trust_score",
        "flagged_count",
        "triggered_rule_count",
        "count",
    } <= metric_names


async def test_modification_evaluation_event_carries_emf_block(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    token, _ = seeded_api_token
    headers = {"Authorization": f"Bearer {token}"}
    booking = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate", json=_booking_payload(), headers=headers
    )
    assert booking.status_code == 200, booking.text

    with structlog.testing.capture_logs() as captured:
        mod = await unauth_client.post(
            "/api/v1/shipments/modification/evaluate",
            json=_modification_payload(),
            headers=headers,
        )
    assert mod.status_code == 200, mod.text

    event = _find_event(captured, "modification.evaluation")
    assert event is not None

    emf = _to_emf(event)
    cw = emf["_aws"]["CloudWatchMetrics"][0]
    assert cw["Dimensions"] == [["tenant_id", "decision", "modification_type"]]
    metric_names = {m["Name"] for m in cw["Metrics"]}
    assert {
        "score",
        "account_prior",
        "signal_score",
        "maturity",
        "modification_velocity_1h",
        "modification_velocity_24h",
        "triggered_rule_count",
        "count",
    } <= metric_names


async def test_auth_success_event_carries_emf_block(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    """Any authenticated request exercises require_api_token's success
    branch, which emits auth.success metric=True."""
    token, _ = seeded_api_token
    with structlog.testing.capture_logs() as captured:
        await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_booking_payload(request_id="EMF-auth-001"),
            headers={"Authorization": f"Bearer {token}"},
        )

    event = _find_event(captured, "auth.success")
    assert event is not None
    emf = _to_emf(event)
    cw = emf["_aws"]["CloudWatchMetrics"][0]
    assert cw["Dimensions"] == [["tenant_id", "role"]]
    assert [m["Name"] for m in cw["Metrics"]] == ["count"]


async def test_auth_invalid_token_event_carries_emf_block(
    unauth_client: AsyncClient,
) -> None:
    with structlog.testing.capture_logs() as captured:
        resp = await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_booking_payload(request_id="EMF-bad-token"),
            headers={"Authorization": "Bearer not-a-real-token"},
        )
    assert resp.status_code == 401

    event = _find_event(captured, "auth.invalid_token")
    assert event is not None
    emf = _to_emf(event)
    cw = emf["_aws"]["CloudWatchMetrics"][0]
    assert cw["Dimensions"] == [[]]
    assert [m["Name"] for m in cw["Metrics"]] == ["count"]


async def test_tenant_config_cache_miss_event_carries_emf_block(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    """First request for a tenant after the cache reset is a miss; the
    autouse fixture in conftest.py invalidates the cache before each
    test so the first booking POST will fire tenant_config.cache.miss."""
    token, _ = seeded_api_token
    with structlog.testing.capture_logs() as captured:
        await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_booking_payload(request_id="EMF-cache-miss-001"),
            headers={"Authorization": f"Bearer {token}"},
        )

    event = _find_event(captured, "tenant_config.cache.miss")
    assert event is not None
    emf = _to_emf(event)
    cw = emf["_aws"]["CloudWatchMetrics"][0]
    assert cw["Dimensions"] == [["tenant_id"]]
    names = {m["Name"] for m in cw["Metrics"]}
    assert {"cache_size", "count"} <= names


async def test_tenant_config_cache_hit_event_carries_emf_block(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    """Second request for the same tenant in the same test produces a
    cache hit (autouse fixture cleared at test start, so first call is
    a miss and second is a hit)."""
    token, _ = seeded_api_token
    headers = {"Authorization": f"Bearer {token}"}
    await unauth_client.post(
        "/api/v1/shipments/booking/evaluate",
        json=_booking_payload(request_id="EMF-cache-hit-warmup"),
        headers=headers,
    )
    with structlog.testing.capture_logs() as captured:
        await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_booking_payload(request_id="EMF-cache-hit-second"),
            headers=headers,
        )

    event = _find_event(captured, "tenant_config.cache.hit")
    assert event is not None
    emf = _to_emf(event)
    cw = emf["_aws"]["CloudWatchMetrics"][0]
    assert cw["Dimensions"] == [["tenant_id"]]
    assert [m["Name"] for m in cw["Metrics"]] == ["count"]


async def test_feedback_applied_event_carries_emf_block(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    token, _ = seeded_api_token
    headers = {"Authorization": f"Bearer {token}"}
    booking = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate",
        json=_booking_payload(request_id="EMF-fb-base-001"),
        headers=headers,
    )
    assert booking.status_code == 200

    with structlog.testing.capture_logs() as captured:
        fb = await unauth_client.post(
            "/api/v1/shipments/feedback",
            json={
                "request_id": "EMF-fb-001",
                "target_request_id": "EMF-fb-base-001",
                "label": "approved",
                "feedback_ts": datetime.now(UTC).isoformat(),
            },
            headers=headers,
        )
    assert fb.status_code == 200, fb.text

    event = _find_event(captured, "feedback.applied")
    assert event is not None
    emf = _to_emf(event)
    cw = emf["_aws"]["CloudWatchMetrics"][0]
    assert cw["Dimensions"] == [["tenant_id", "label"]]
    names = {m["Name"] for m in cw["Metrics"]}
    assert {"flag_delta", "fraud_delta", "dimensions_written", "count"} <= names


async def test_booking_idempotent_replay_carries_emf_block(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    token, _ = seeded_api_token
    headers = {"Authorization": f"Bearer {token}"}
    await unauth_client.post(
        "/api/v1/shipments/booking/evaluate",
        json=_booking_payload(request_id="EMF-replay-001"),
        headers=headers,
    )
    with structlog.testing.capture_logs() as captured:
        await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_booking_payload(request_id="EMF-replay-001"),
            headers=headers,
        )

    event = _find_event(captured, "booking.idempotent_replay")
    assert event is not None
    emf = _to_emf(event)
    cw = emf["_aws"]["CloudWatchMetrics"][0]
    assert cw["Dimensions"] == [["tenant_id"]]
    assert [m["Name"] for m in cw["Metrics"]] == ["count"]


def test_non_metric_log_passes_through_unchanged() -> None:
    """Sanity check at the integration tier: a non-metric structured log
    event flows through emf_processor without an _aws block. Confirms
    the pass-through contract that production relies on for non-metric
    operational logs."""
    event: dict[str, Any] = {"event": "lifespan.pool_initialised", "min_size": 2}
    result = emf_processor(None, "info", event)
    assert "_aws" not in result


async def test_request_id_never_appears_in_dimensions_at_runtime(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    """High-cardinality guard exercised end-to-end. Hit booking with a
    distinctive request_id; capture risk.evaluation; verify the EMF
    Dimensions array does not contain `request_id` as a key."""
    token, _ = seeded_api_token
    distinctive = "EMF-must-not-be-a-dimension-XYZ-12345"
    with structlog.testing.capture_logs() as captured:
        await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_booking_payload(request_id=distinctive),
            headers={"Authorization": f"Bearer {token}"},
        )
    event = _find_event(captured, "risk.evaluation")
    assert event is not None
    assert event["request_id"] == distinctive, "regular field intact in log line"

    emf = _to_emf(event)
    cw = emf["_aws"]["CloudWatchMetrics"][0]
    assert "request_id" not in cw["Dimensions"][0]
