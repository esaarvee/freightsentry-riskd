#!/usr/bin/env python3
"""End-to-end correctness verification for the local freightsentry-riskd stack.

Exercises booking → modification → feedback → admin → baseline-update against
a running stack and asserts decisions match expected shapes for legitimate,
case-2, and case-3b threat templates. Suitable for invocation by `make verify`
after `make up`.

This is a correctness smoke test, NOT a performance benchmark — perf work
lives in scripts/load_test.py.

Test-data discipline:
- Synthetic payloads only; never sourced from freight_risk.db.
- Customer external_ids prefixed `e2e_verify_`; request_ids `e2e-verify-`.
- Step 10 cleanup target removes all prefixed rows; --cleanup-only runs
  cleanup without exercising the API.

Exit 0 on full pass (incl. warnings); exit 1 on any hard failure.

Usage:
    python scripts/e2e_verify.py \\
        --host http://localhost:8000 \\
        --token-file .tokens/e2e-test.txt \\
        [--admin-token-file .tokens/e2e-test-admin.txt] \\
        [--legitimate-count 20] \\
        [--case2-count 5] \\
        [--case3b-count 3] \\
        [--cleanup-only] \\
        [--no-cleanup] \\
        [--verbose]

Host dependencies: httpx + asyncpg (both ship under pyproject [test] extras;
operator running this from the host venv already has them installed for
pytest).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx

# =============================================================================
# Synthetic payload constants — tune at the top of the file.
# =============================================================================

# Google Cloud IP ranges (legitimate enterprise traffic shape).
_CLOUD_IPS: tuple[str, ...] = (
    "34.102.136.180",
    "35.190.247.13",
    "34.117.118.44",
)
# Residential / mobile carrier ranges (case-2 attack shape).
_RESIDENTIAL_IPS: tuple[str, ...] = (
    "99.224.10.42",
    "70.27.5.18",
)

# Carrier-dropoff origin (case-3 attack shape).
_CARRIER_DROPOFF_ORIGIN: dict[str, Any] = {
    "address": "FedEx Office Print & Ship Center #1234",
    "city": "Toronto",
    "country": "CA",
}
# Cross-border destination for case-3b (customer registered_country differs).
_CROSS_BORDER_DESTINATION: dict[str, Any] = {
    "address": "500 Boylston Street",
    "city": "Boston",
    "country": "US",
}

_DEFAULT_CURRENCY = "USD"
_DEFAULT_VALUE = "850.00"
_DEFAULT_CHANNEL = "api"

_LEGIT_DESTINATIONS: tuple[dict[str, Any], ...] = (
    {"address": "100 Universal Plaza", "city": "Los Angeles", "country": "US"},
    {"address": "200 Pine Street", "city": "Seattle", "country": "US"},
    {"address": "300 Market Street", "city": "San Francisco", "country": "US"},
)
_LEGIT_ORIGIN: dict[str, Any] = {
    "address": "1 Apollo Way",
    "city": "Houston",
    "country": "US",
}

# Allowed pass-rate thresholds (per spec).
_LEGIT_ALLOW_TARGET = 0.80
_CASE2_DETECT_TARGET = 0.80
_CASE3B_DETECT_TARGET = 0.85

# Step 4 cold-start warmup ramp size.
_CASE2_WARMUP_COUNT = 12
# Minimum ALLOW observations required for case-2 attack-booking assertion.
_CASE2_MIN_BASELINE_N = 10

# Concurrency cap for per-step parallel POSTs (keep load light; this is a
# correctness probe, not a load test).
_CONCURRENCY = 5

# Latency ceiling for individual HTTP calls (sanity bound; cold-start tolerated).
_HTTP_TIMEOUT_SECONDS = 15.0


# =============================================================================
# Step result accumulator.
# =============================================================================


@dataclass
class StepResult:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str
    elapsed_ms: float = 0.0


@dataclass
class RunState:
    """Mutable accumulator shared across steps so cleanup + Step 9 can
    reference IDs produced earlier in the run."""

    results: list[StepResult] = field(default_factory=list)
    # external_ids of customers created during this run (for Step 9 baseline
    # lookup and Step 10 cleanup).
    legit_customer_ids: list[str] = field(default_factory=list)
    case2_customer_ids: list[str] = field(default_factory=list)
    case3b_customer_ids: list[str] = field(default_factory=list)
    # request_ids produced by each step (for Step 8 admin lookup).
    sample_booking_request_id: str | None = None
    feedback_target_request_id: str | None = None

    def record(self, result: StepResult) -> None:
        self.results.append(result)


# =============================================================================
# Payload builders.
# =============================================================================


def _booking_payload(
    *,
    request_id: str,
    customer_external_id: str,
    source_ip: str,
    origin: dict[str, Any],
    destination: dict[str, Any],
    value: str = _DEFAULT_VALUE,
    currency: str = _DEFAULT_CURRENCY,
    channel: str = _DEFAULT_CHANNEL,
    registered_country: str | None = None,
    origin_via_carrier_dropoff: bool = False,
    booking_ts: datetime | None = None,
) -> dict[str, Any]:
    ts = booking_ts or datetime.now(UTC)
    customer: dict[str, Any] = {
        "external_id": customer_external_id,
        "business_name": "E2E Verify Synthetic Co.",
    }
    if registered_country is not None:
        customer["registered_country"] = registered_country
    shipment: dict[str, Any] = {
        "origin": origin,
        "destination": destination,
        "value": value,
        "channel": channel,
        "currency": currency,
        "origin_via_carrier_dropoff": origin_via_carrier_dropoff,
    }
    return {
        "request_id": request_id,
        "customer": customer,
        "user": {"external_id": f"{customer_external_id}-user"},
        "source_ip": source_ip,
        "shipment": shipment,
        "booking_ts": ts.isoformat().replace("+00:00", "Z"),
    }


def _modification_payload(
    *,
    request_id: str,
    original_request_id: str,
    new_value_amount: int = 1250,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "original_request_id": original_request_id,
        "modification_ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "modification_type": "value",
        "new_value": {"value": new_value_amount},
    }


def _feedback_payload(
    *,
    request_id: str,
    target_request_id: str,
    label: str = "approved",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "target_request_id": target_request_id,
        "label": label,
        "feedback_ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "operator_id": "e2e-verify-operator",
    }


# =============================================================================
# HTTP helpers.
# =============================================================================


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _post_booking(
    client: httpx.AsyncClient, token: str, payload: dict[str, Any]
) -> httpx.Response:
    return await client.post(
        "/api/v1/shipments/booking/evaluate",
        json=payload,
        headers=_bearer(token),
        timeout=_HTTP_TIMEOUT_SECONDS,
    )


async def _post_modification(
    client: httpx.AsyncClient, token: str, payload: dict[str, Any]
) -> httpx.Response:
    return await client.post(
        "/api/v1/shipments/modification/evaluate",
        json=payload,
        headers=_bearer(token),
        timeout=_HTTP_TIMEOUT_SECONDS,
    )


async def _post_feedback(
    client: httpx.AsyncClient, token: str, payload: dict[str, Any]
) -> httpx.Response:
    return await client.post(
        "/api/v1/shipments/feedback",
        json=payload,
        headers=_bearer(token),
        timeout=_HTTP_TIMEOUT_SECONDS,
    )


# =============================================================================
# Step implementations.
# =============================================================================


async def step_1_health(client: httpx.AsyncClient, state: RunState) -> None:
    start = time.monotonic()
    try:
        resp = await client.get("/health/", timeout=_HTTP_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        state.record(
            StepResult(
                name="Step 1: Health check",
                status="fail",
                detail=f"connection error: {type(exc).__name__}: {exc}",
                elapsed_ms=(time.monotonic() - start) * 1000,
            )
        )
        return
    elapsed_ms = (time.monotonic() - start) * 1000
    body = _safe_json(resp)
    if resp.status_code == 200 and isinstance(body, dict) and body.get("ok") is True:
        state.record(
            StepResult(
                name="Step 1: Health check",
                status="ok",
                detail=f"{resp.status_code} OK in {elapsed_ms:.0f}ms",
                elapsed_ms=elapsed_ms,
            )
        )
    else:
        state.record(
            StepResult(
                name="Step 1: Health check",
                status="fail",
                detail=f"status={resp.status_code} body={body!r}",
                elapsed_ms=elapsed_ms,
            )
        )


async def step_2_auth(client: httpx.AsyncClient, token: str, state: RunState) -> None:
    """Confirm auth works: empty-payload POST should NOT 401. 400/422 OK."""
    start = time.monotonic()
    try:
        resp = await client.post(
            "/api/v1/shipments/booking/evaluate",
            json={},
            headers=_bearer(token),
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        state.record(
            StepResult(
                name="Step 2: Auth verification",
                status="fail",
                detail=f"connection error: {type(exc).__name__}: {exc}",
                elapsed_ms=(time.monotonic() - start) * 1000,
            )
        )
        return
    elapsed_ms = (time.monotonic() - start) * 1000
    if resp.status_code == 401:
        state.record(
            StepResult(
                name="Step 2: Auth verification",
                status="fail",
                detail="received 401 — token rejected by server",
                elapsed_ms=elapsed_ms,
            )
        )
    elif resp.status_code in (400, 422):
        state.record(
            StepResult(
                name="Step 2: Auth verification",
                status="ok",
                detail=f"auth passed (got expected {resp.status_code} for empty payload)",
                elapsed_ms=elapsed_ms,
            )
        )
    else:
        state.record(
            StepResult(
                name="Step 2: Auth verification",
                status="warn",
                detail=f"unexpected status {resp.status_code} (auth-pass inferred); body={_safe_json(resp)!r}",
                elapsed_ms=elapsed_ms,
            )
        )


async def step_3_legitimate(
    client: httpx.AsyncClient,
    token: str,
    state: RunState,
    *,
    count: int,
) -> None:
    start = time.monotonic()
    payloads: list[dict[str, Any]] = []
    for i in range(count):
        cid = f"e2e_verify_legit_{i:03d}_{uuid.uuid4().hex[:8]}"
        state.legit_customer_ids.append(cid)
        payloads.append(
            _booking_payload(
                request_id=f"e2e-verify-legit-{i:03d}-{uuid.uuid4().hex[:8]}",
                customer_external_id=cid,
                source_ip=_CLOUD_IPS[i % len(_CLOUD_IPS)],
                origin=_LEGIT_ORIGIN,
                destination=_LEGIT_DESTINATIONS[i % len(_LEGIT_DESTINATIONS)],
                registered_country="US",
            )
        )
    decisions = await _post_batched(client, token, payloads, _post_booking)
    allow = sum(1 for d in decisions if d == "ALLOW")
    review = sum(1 for d in decisions if d == "REVIEW")
    block = sum(1 for d in decisions if d == "BLOCK")
    err = sum(1 for d in decisions if d is None)
    total = max(1, len(decisions))
    pct = allow / total
    elapsed_ms = (time.monotonic() - start) * 1000
    detail = (
        f"{allow} ALLOW / {review} REVIEW / {block} BLOCK"
        + (f" / {err} ERR" if err else "")
        + f"   ({pct * 100:.0f}% ALLOW; target >= {int(_LEGIT_ALLOW_TARGET * 100)}%)"
    )
    if block > 0 or err > 0:
        status = "fail"
    elif pct >= _LEGIT_ALLOW_TARGET:
        status = "ok"
    else:
        status = "warn"
    state.record(
        StepResult(
            name="Step 3: Legitimate bookings",
            status=status,
            detail=detail,
            elapsed_ms=elapsed_ms,
        )
    )


async def step_4_case2(
    client: httpx.AsyncClient,
    token: str,
    state: RunState,
    *,
    count: int,
) -> None:
    """Warm each case-2 customer with cloud-IP bookings, then attack from
    residential IP. Detection = REVIEW or BLOCK on the attack booking.

    Skip-with-warning if a customer's baseline doesn't reach the
    _CASE2_MIN_BASELINE_N threshold (some warmup bookings may land REVIEW
    for unrelated cold-start reasons; baseline gates on ALLOW only per
    Phase 7C.11).
    """
    start = time.monotonic()
    detections = 0
    skipped = 0
    insufficient_baseline: list[str] = []
    for i in range(count):
        cid = f"e2e_verify_case2_{i:03d}_{uuid.uuid4().hex[:8]}"
        warmup_payloads = [
            _booking_payload(
                request_id=f"e2e-verify-case2-warm-{i:03d}-{j:02d}-{uuid.uuid4().hex[:6]}",
                customer_external_id=cid,
                source_ip=_CLOUD_IPS[j % len(_CLOUD_IPS)],
                origin=_LEGIT_ORIGIN,
                destination=_LEGIT_DESTINATIONS[j % len(_LEGIT_DESTINATIONS)],
                registered_country="US",
            )
            for j in range(_CASE2_WARMUP_COUNT)
        ]
        warmup_decisions = await _post_batched(client, token, warmup_payloads, _post_booking)
        allow_n = sum(1 for d in warmup_decisions if d == "ALLOW")
        if allow_n < _CASE2_MIN_BASELINE_N:
            # Customer not added to state.case2_customer_ids — Step 9
            # picks case2_customer_ids[0] expecting a fully-warmed
            # baseline. Cleanup still picks the customer up via the
            # e2e_verify_case2_ external_id LIKE prefix.
            insufficient_baseline.append(f"{cid}={allow_n}/{_CASE2_WARMUP_COUNT}")
            skipped += 1
            continue
        state.case2_customer_ids.append(cid)
        attack_payload = _booking_payload(
            request_id=f"e2e-verify-case2-attack-{i:03d}-{uuid.uuid4().hex[:8]}",
            customer_external_id=cid,
            source_ip=_RESIDENTIAL_IPS[i % len(_RESIDENTIAL_IPS)],
            origin=_LEGIT_ORIGIN,
            destination=_LEGIT_DESTINATIONS[i % len(_LEGIT_DESTINATIONS)],
            registered_country="US",
        )
        resp = await _post_booking(client, token, attack_payload)
        decision = _decision_or_none(resp)
        if decision in ("REVIEW", "BLOCK"):
            detections += 1
    elapsed_ms = (time.monotonic() - start) * 1000
    evaluated = count - skipped
    if evaluated == 0:
        state.record(
            StepResult(
                name="Step 4: Case-2 detection",
                status="warn",
                detail=(
                    f"all {count} customers had insufficient baseline; skipped. "
                    f"detail: {', '.join(insufficient_baseline)}"
                ),
                elapsed_ms=elapsed_ms,
            )
        )
        return
    pct = detections / evaluated
    detail = (
        f"{detections}/{evaluated} REVIEW or BLOCK   "
        f"({pct * 100:.0f}%; target >= {int(_CASE2_DETECT_TARGET * 100)}%)"
    )
    if skipped:
        detail += f"   [{skipped} skipped — insufficient baseline]"
    status = "ok" if pct >= _CASE2_DETECT_TARGET else "warn"
    state.record(
        StepResult(
            name="Step 4: Case-2 detection",
            status=status,
            detail=detail,
            elapsed_ms=elapsed_ms,
        )
    )


async def step_5_case3b(
    client: httpx.AsyncClient,
    token: str,
    state: RunState,
    *,
    count: int,
) -> None:
    start = time.monotonic()
    payloads: list[dict[str, Any]] = []
    for i in range(count):
        cid = f"e2e_verify_case3b_{i:03d}_{uuid.uuid4().hex[:8]}"
        state.case3b_customer_ids.append(cid)
        payloads.append(
            _booking_payload(
                request_id=f"e2e-verify-case3b-{i:03d}-{uuid.uuid4().hex[:8]}",
                customer_external_id=cid,
                source_ip=_RESIDENTIAL_IPS[i % len(_RESIDENTIAL_IPS)],
                origin=_CARRIER_DROPOFF_ORIGIN,
                destination=_CROSS_BORDER_DESTINATION,
                registered_country="CA",
                origin_via_carrier_dropoff=True,
                value="2500.00",
            )
        )
    decisions = await _post_batched(client, token, payloads, _post_booking)
    detections = sum(1 for d in decisions if d in ("REVIEW", "BLOCK"))
    err = sum(1 for d in decisions if d is None)
    total = max(1, len(decisions))
    pct = detections / total
    elapsed_ms = (time.monotonic() - start) * 1000
    detail = (
        f"{detections}/{total} REVIEW or BLOCK"
        + (f" / {err} ERR" if err else "")
        + f"   ({pct * 100:.0f}%; target >= {int(_CASE3B_DETECT_TARGET * 100)}%)"
    )
    if err > 0:
        status = "fail"
    elif pct >= _CASE3B_DETECT_TARGET:
        status = "ok"
    else:
        status = "warn"
    state.record(
        StepResult(
            name="Step 5: Case-3b detection",
            status=status,
            detail=detail,
            elapsed_ms=elapsed_ms,
        )
    )


async def step_6_modification(client: httpx.AsyncClient, token: str, state: RunState) -> None:
    start = time.monotonic()
    cid = f"e2e_verify_mod_{uuid.uuid4().hex[:8]}"
    state.legit_customer_ids.append(cid)
    book_req_id = f"e2e-verify-mod-book-{uuid.uuid4().hex[:8]}"
    book_resp = await _post_booking(
        client,
        token,
        _booking_payload(
            request_id=book_req_id,
            customer_external_id=cid,
            source_ip=_CLOUD_IPS[0],
            origin=_LEGIT_ORIGIN,
            destination=_LEGIT_DESTINATIONS[0],
            registered_country="US",
        ),
    )
    if book_resp.status_code != 200:
        state.record(
            StepResult(
                name="Step 6: Modification flow",
                status="fail",
                detail=f"booking failed: status={book_resp.status_code} body={_safe_json(book_resp)!r}",
                elapsed_ms=(time.monotonic() - start) * 1000,
            )
        )
        return
    mod_req_id = f"e2e-verify-mod-mod-{uuid.uuid4().hex[:8]}"
    mod_resp = await _post_modification(
        client,
        token,
        _modification_payload(request_id=mod_req_id, original_request_id=book_req_id),
    )
    elapsed_ms = (time.monotonic() - start) * 1000
    if mod_resp.status_code != 200:
        state.record(
            StepResult(
                name="Step 6: Modification flow",
                status="fail",
                detail=f"modification failed: status={mod_resp.status_code} body={_safe_json(mod_resp)!r}",
                elapsed_ms=elapsed_ms,
            )
        )
        return
    mod_body = _safe_json(mod_resp)
    if not isinstance(mod_body, dict) or mod_body.get("decision") not in (
        "ALLOW",
        "REVIEW",
        "BLOCK",
    ):
        state.record(
            StepResult(
                name="Step 6: Modification flow",
                status="fail",
                detail=f"modification response missing decision: {mod_body!r}",
                elapsed_ms=elapsed_ms,
            )
        )
        return
    state.record(
        StepResult(
            name="Step 6: Modification flow",
            status="ok",
            detail=f"booking + modification both 200; mod decision={mod_body.get('decision')}",
            elapsed_ms=elapsed_ms,
        )
    )


async def step_7_feedback(client: httpx.AsyncClient, token: str, state: RunState) -> None:
    start = time.monotonic()
    cid = f"e2e_verify_fb_{uuid.uuid4().hex[:8]}"
    state.legit_customer_ids.append(cid)
    book_req_id = f"e2e-verify-fb-book-{uuid.uuid4().hex[:8]}"
    book_resp = await _post_booking(
        client,
        token,
        _booking_payload(
            request_id=book_req_id,
            customer_external_id=cid,
            source_ip=_CLOUD_IPS[0],
            origin=_LEGIT_ORIGIN,
            destination=_LEGIT_DESTINATIONS[0],
            registered_country="US",
        ),
    )
    if book_resp.status_code != 200:
        state.record(
            StepResult(
                name="Step 7: Feedback flow",
                status="fail",
                detail=f"booking failed: status={book_resp.status_code}",
                elapsed_ms=(time.monotonic() - start) * 1000,
            )
        )
        return
    fb_req_id = f"e2e-verify-fb-fb-{uuid.uuid4().hex[:8]}"
    fb_resp = await _post_feedback(
        client,
        token,
        _feedback_payload(request_id=fb_req_id, target_request_id=book_req_id, label="approved"),
    )
    elapsed_ms = (time.monotonic() - start) * 1000
    if fb_resp.status_code != 200:
        state.record(
            StepResult(
                name="Step 7: Feedback flow",
                status="fail",
                detail=f"feedback failed: status={fb_resp.status_code} body={_safe_json(fb_resp)!r}",
                elapsed_ms=elapsed_ms,
            )
        )
        return
    fb_body = _safe_json(fb_resp)
    if not isinstance(fb_body, dict) or "applied" not in fb_body:
        state.record(
            StepResult(
                name="Step 7: Feedback flow",
                status="fail",
                detail=f"feedback response missing 'applied': {fb_body!r}",
                elapsed_ms=elapsed_ms,
            )
        )
        return
    # Save the target_request_id for Step 8 admin lookup.
    state.feedback_target_request_id = book_req_id
    state.sample_booking_request_id = book_req_id
    state.record(
        StepResult(
            name="Step 7: Feedback flow",
            status="ok",
            detail=f"feedback applied={fb_body.get('applied')} previous_label={fb_body.get('previous_label')}",
            elapsed_ms=elapsed_ms,
        )
    )


async def step_8_admin(client: httpx.AsyncClient, admin_token: str | None, state: RunState) -> None:
    start = time.monotonic()
    if admin_token is None:
        state.record(
            StepResult(
                name="Step 8: Admin endpoints",
                status="warn",
                detail="skipped — no admin token provided (use --admin-token or --admin-token-file)",
                elapsed_ms=0.0,
            )
        )
        return
    if state.sample_booking_request_id is None or not state.legit_customer_ids:
        state.record(
            StepResult(
                name="Step 8: Admin endpoints",
                status="warn",
                detail="skipped — prior steps produced no request_id / customer_id to query",
                elapsed_ms=0.0,
            )
        )
        return
    headers = _bearer(admin_token)
    dec_req_id = state.sample_booking_request_id
    dec_resp = await client.get(
        f"/api/v1/admin/decisions/{dec_req_id}", headers=headers, timeout=_HTTP_TIMEOUT_SECONDS
    )
    cust_id = state.legit_customer_ids[0]
    bl_resp = await client.get(
        f"/api/v1/admin/customers/{cust_id}/baseline",
        headers=headers,
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    elapsed_ms = (time.monotonic() - start) * 1000
    if dec_resp.status_code != 200:
        state.record(
            StepResult(
                name="Step 8: Admin endpoints",
                status="fail",
                detail=f"GET /admin/decisions/{dec_req_id}: status={dec_resp.status_code} body={_safe_json(dec_resp)!r}",
                elapsed_ms=elapsed_ms,
            )
        )
        return
    if bl_resp.status_code != 200:
        state.record(
            StepResult(
                name="Step 8: Admin endpoints",
                status="fail",
                detail=f"GET /admin/customers/{cust_id}/baseline: status={bl_resp.status_code} body={_safe_json(bl_resp)!r}",
                elapsed_ms=elapsed_ms,
            )
        )
        return
    state.record(
        StepResult(
            name="Step 8: Admin endpoints",
            status="ok",
            detail=f"decision lookup + baseline lookup both 200 (customer={cust_id})",
            elapsed_ms=elapsed_ms,
        )
    )


async def step_9_baseline(
    client: httpx.AsyncClient, admin_token: str | None, state: RunState
) -> None:
    """Inspect a case-2 customer baseline (post-warmup) via admin endpoint;
    case-2 customers had 12 cloud-IP ALLOW bookings, so value_n + asn + lane
    should all be populated."""
    start = time.monotonic()
    if admin_token is None:
        state.record(
            StepResult(
                name="Step 9: Baseline update",
                status="warn",
                detail="skipped — no admin token (baseline state can only be inspected via admin endpoint)",
                elapsed_ms=0.0,
            )
        )
        return
    if not state.case2_customer_ids:
        state.record(
            StepResult(
                name="Step 9: Baseline update",
                status="warn",
                detail="skipped — Step 4 produced no case-2 customers",
                elapsed_ms=0.0,
            )
        )
        return
    cust_id = state.case2_customer_ids[0]
    resp = await client.get(
        f"/api/v1/admin/customers/{cust_id}/baseline",
        headers=_bearer(admin_token),
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    elapsed_ms = (time.monotonic() - start) * 1000
    if resp.status_code != 200:
        state.record(
            StepResult(
                name="Step 9: Baseline update",
                status="fail",
                detail=f"GET baseline: status={resp.status_code} body={_safe_json(resp)!r}",
                elapsed_ms=elapsed_ms,
            )
        )
        return
    body = _safe_json(resp)
    if not isinstance(body, dict) or not isinstance(body.get("baseline"), dict):
        state.record(
            StepResult(
                name="Step 9: Baseline update",
                status="warn",
                detail=f"baseline payload missing: {body!r}",
                elapsed_ms=elapsed_ms,
            )
        )
        return
    bl = body["baseline"]
    value_n = float(bl.get("value_n", 0))
    asn_count = int(bl.get("ip_asn_stats", {}).get("total_count", 0))
    lane_count = int(bl.get("lane_stats", {}).get("total_count", 0))
    detail = (
        f"value_n={value_n:.0f}, asn_stats={asn_count} entries, lane_stats={lane_count} entries"
    )
    status = "ok" if value_n >= 10 and asn_count >= 1 and lane_count >= 1 else "warn"
    state.record(
        StepResult(
            name="Step 9: Baseline update",
            status=status,
            detail=detail,
            elapsed_ms=elapsed_ms,
        )
    )


async def step_10_cleanup(state: RunState, db_url: str | None) -> None:
    """Remove all rows whose external_id / request_id matches the
    e2e_verify_/e2e-verify- prefixes. Connects directly via asyncpg using
    a privileged DB URL (typically ALEMBIC_DATABASE_URL) to bypass RLS.
    """
    start = time.monotonic()
    if db_url is None:
        state.record(
            StepResult(
                name="Step 10: Cleanup",
                status="warn",
                detail="skipped — no --db-url and ALEMBIC_DATABASE_URL not set",
                elapsed_ms=0.0,
            )
        )
        return
    try:
        conn = await asyncpg.connect(_normalize_db_url(db_url))
    except (OSError, asyncpg.PostgresError) as exc:
        state.record(
            StepResult(
                name="Step 10: Cleanup",
                status="warn",
                detail=f"DB connect failed: {type(exc).__name__}: {exc}",
                elapsed_ms=(time.monotonic() - start) * 1000,
            )
        )
        return
    try:
        async with conn.transaction():
            # FK order: feedback first (no FK to shipments/decisions, but
            # filter on request_id prefixes for completeness), then
            # decisions → shipments → users → customer_baselines → customers.
            fb = await conn.execute(
                "DELETE FROM feedback WHERE request_id LIKE 'e2e-verify-%' "
                "OR target_request_id LIKE 'e2e-verify-%'"
            )
            dec = await conn.execute("DELETE FROM decisions WHERE request_id LIKE 'e2e-verify-%'")
            sh = await conn.execute("DELETE FROM shipments WHERE request_id LIKE 'e2e-verify-%'")
            usr = await conn.execute(
                "DELETE FROM users WHERE customer_id IN "
                "(SELECT id FROM customers WHERE external_id LIKE 'e2e_verify_%')"
            )
            bl = await conn.execute(
                "DELETE FROM customer_baselines WHERE customer_id IN "
                "(SELECT id FROM customers WHERE external_id LIKE 'e2e_verify_%')"
            )
            cust = await conn.execute("DELETE FROM customers WHERE external_id LIKE 'e2e_verify_%'")
    finally:
        await conn.close()
    elapsed_ms = (time.monotonic() - start) * 1000
    state.record(
        StepResult(
            name="Step 10: Cleanup",
            status="ok",
            detail=(
                f"removed: {_count(cust)} customers / {_count(bl)} baselines / "
                f"{_count(usr)} users / {_count(sh)} shipments / "
                f"{_count(dec)} decisions / {_count(fb)} feedback"
            ),
            elapsed_ms=elapsed_ms,
        )
    )


# =============================================================================
# Helpers.
# =============================================================================


async def _post_batched(
    client: httpx.AsyncClient,
    token: str,
    payloads: list[dict[str, Any]],
    poster: Any,
) -> list[str | None]:
    """Run `poster(client, token, payload)` over payloads with bounded
    concurrency. Returns decision strings in submission order; None on
    non-200."""
    sem = asyncio.Semaphore(_CONCURRENCY)
    results: list[str | None] = [None] * len(payloads)

    async def _one(i: int, p: dict[str, Any]) -> None:
        async with sem:
            try:
                resp = await poster(client, token, p)
            except httpx.HTTPError:
                results[i] = None
                return
            results[i] = _decision_or_none(resp)

    await asyncio.gather(*(_one(i, p) for i, p in enumerate(payloads)))
    return results


def _decision_or_none(resp: httpx.Response) -> str | None:
    if resp.status_code != 200:
        return None
    body = _safe_json(resp)
    if isinstance(body, dict):
        d = body.get("decision")
        if isinstance(d, str) and d in ("ALLOW", "REVIEW", "BLOCK"):
            return d
    return None


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:500]}


def _count(execute_result: str) -> int:
    """Parse `DELETE N` (or `INSERT 0 N`) into the row count."""
    parts = execute_result.split()
    if not parts:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0


def _normalize_db_url(url: str) -> str:
    """asyncpg rejects the `postgresql+driver://` SQLAlchemy form and the
    `postgresql://` form with `?sslmode=...` query parameters in older
    versions. Trim the `+driver` segment if present.
    """
    if "://" in url and "+" in url.split("://", 1)[0]:
        scheme, rest = url.split("://", 1)
        scheme = scheme.split("+", 1)[0]
        return f"{scheme}://{rest}"
    return url


def _resolve_token(token_arg: str | None, token_file_arg: str | None) -> str | None:
    if token_arg:
        return token_arg.strip()
    if token_file_arg:
        if not os.path.exists(token_file_arg):
            return None
        with open(token_file_arg, encoding="utf-8") as f:
            return f.read().strip() or None
    return None


# =============================================================================
# Output rendering + main.
# =============================================================================


def _render_summary(state: RunState) -> None:
    print()
    print("=" * 72)
    print("E2E VERIFY — RESULTS")
    print("=" * 72)
    n_ok = 0
    n_warn = 0
    n_fail = 0
    for r in state.results:
        tag = {"ok": "[ok]", "warn": "[!!]", "fail": "[FAIL]"}.get(r.status, "[?]")
        if r.status == "ok":
            n_ok += 1
        elif r.status == "warn":
            n_warn += 1
        elif r.status == "fail":
            n_fail += 1
        print(f"{tag:>6}  {r.name:<32}  {r.detail}")
    total = len(state.results)
    verdict = f"VERDICT: {n_ok}/{total} steps passed"
    if n_warn:
        verdict += f" ({n_warn} warning{'s' if n_warn != 1 else ''})"
    if n_fail:
        verdict += f" — {n_fail} FAILURE{'S' if n_fail != 1 else ''}"
    print("-" * 72)
    print(verdict)
    print("=" * 72)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="End-to-end correctness smoke test for local riskd stack.",
    )
    p.add_argument(
        "--host", default="http://localhost:8000", help="Base URL of running riskd stack."
    )
    p.add_argument("--token", default=None, help="Tenant bearer token (overrides --token-file).")
    p.add_argument(
        "--token-file", default=".tokens/e2e-test.txt", help="Path to tenant-token file."
    )
    p.add_argument(
        "--admin-token", default=None, help="Admin bearer token (overrides --admin-token-file)."
    )
    p.add_argument(
        "--admin-token-file",
        default=".tokens/e2e-test-admin.txt",
        help="Path to admin-token file (Steps 8/9 skip with warning if absent).",
    )
    p.add_argument(
        "--db-url",
        default=None,
        help="DB URL for cleanup (defaults to env ALEMBIC_DATABASE_URL).",
    )
    p.add_argument("--legitimate-count", type=int, default=20, help="Legitimate bookings to send.")
    p.add_argument("--case2-count", type=int, default=5, help="Case-2 customers to test.")
    p.add_argument("--case3b-count", type=int, default=3, help="Case-3b bookings to send.")
    p.add_argument("--cleanup-only", action="store_true", help="Run only Step 10 (cleanup).")
    p.add_argument(
        "--no-cleanup", action="store_true", help="Skip Step 10 (leave test data in place)."
    )
    p.add_argument("--verbose", action="store_true", help="Verbose progress output.")
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    state = RunState()
    db_url = args.db_url or os.environ.get("ALEMBIC_DATABASE_URL")

    if args.cleanup_only:
        await step_10_cleanup(state, db_url)
        _render_summary(state)
        return 1 if any(r.status == "fail" for r in state.results) else 0

    token = _resolve_token(args.token, args.token_file)
    if token is None:
        print(
            f"FATAL: no tenant token found (checked --token and --token-file={args.token_file!r}); "
            "run `make seed` first",
            file=sys.stderr,
        )
        return 1
    admin_token = _resolve_token(args.admin_token, args.admin_token_file)

    async with httpx.AsyncClient(base_url=args.host.rstrip("/")) as client:
        await step_1_health(client, state)
        if state.results[-1].status == "fail":
            _render_summary(state)
            return 1
        await step_2_auth(client, token, state)
        if state.results[-1].status == "fail":
            _render_summary(state)
            return 1
        await step_3_legitimate(client, token, state, count=args.legitimate_count)
        await step_4_case2(client, token, state, count=args.case2_count)
        await step_5_case3b(client, token, state, count=args.case3b_count)
        await step_6_modification(client, token, state)
        await step_7_feedback(client, token, state)
        await step_8_admin(client, admin_token, state)
        await step_9_baseline(client, admin_token, state)

    if not args.no_cleanup:
        await step_10_cleanup(state, db_url)
    else:
        state.record(
            StepResult(
                name="Step 10: Cleanup",
                status="warn",
                detail="skipped — --no-cleanup supplied (test data left in DB)",
            )
        )

    _render_summary(state)
    return 1 if any(r.status == "fail" for r in state.results) else 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
