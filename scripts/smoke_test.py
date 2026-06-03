#!/usr/bin/env python3
"""Phase 6D smoke test — post-deploy verification of /booking/evaluate.

Sends a known-good BookingRequest payload to the deployed ALB endpoint
with a tenant token, then asserts the response shape:

- HTTP 200
- JSON body contains `request_id` matching the submitted value
- `decision` ∈ {ALLOW, REVIEW, BLOCK}
- `score` ∈ [0.0, 1.0] (float-safe boundary)
- Latency < 5 seconds (sanity bound; cold-start tolerated)

Exit 0 on success, 1 on any assertion failure. Failure detail is
printed to stderr for the deploy.yml workflow log + ECS console
operator to triage.

Used by the Phase 6D.8 deploy workflow as the post-rollout
verification gate; also runnable locally for operator-side checks.

Usage:
    python scripts/smoke_test.py \\
        --base-url https://<your-alb-or-domain> \\
        --tenant-token $SMOKE_TENANT_TOKEN

The payload is currency=CAD per the Phase 6B project default. The
test tenant the operator provisioned at runbook step B.2 should
have `allowed_currencies` including CAD.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

_SMOKE_PAYLOAD: dict[str, Any] = {
    "request_id": "smoke-test",
    "customer": {
        "external_id": "smoke-customer",
        "business_name": "Smoke Test Co.",
    },
    "user": {"external_id": "smoke-user"},
    "source_ip": "192.0.2.1",
    "shipment": {
        "origin": {"address": "1 Main St"},
        "destination": {"address": "2 Park Ave"},
        "value": "100.00",
        "channel": "web",
        "currency": "CAD",
    },
    "booking_ts": "2026-06-03T12:00:00Z",
}

_DECISION_BANDS = {"ALLOW", "REVIEW", "BLOCK"}
_LATENCY_CEILING_SECONDS = 5.0


def _post_booking(base_url: str, token: str, request_id: str) -> tuple[int, float, dict[str, Any]]:
    """POST the smoke payload; return (status_code, elapsed_seconds, body_dict).

    Uses stdlib urllib.request to avoid any third-party dep (Phase 6D.1
    HEALTHCHECK pattern — the smoke test runs in the GitHub Actions
    runner which may have a minimal Python; stdlib-only keeps the
    workflow dependency-free).
    """
    payload = {**_SMOKE_PAYLOAD, "request_id": request_id}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/v1/shipments/booking/evaluate",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=_LATENCY_CEILING_SECONDS + 1) as resp:
            elapsed = time.monotonic() - start
            body_raw = resp.read().decode("utf-8")
            body: dict[str, Any] = json.loads(body_raw)
            return resp.status, elapsed, body
    except urllib.error.HTTPError as exc:
        elapsed = time.monotonic() - start
        body_raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(body_raw)
        except json.JSONDecodeError:
            body = {"raw": body_raw[:500]}
        return exc.code, elapsed, body


def assert_response(
    status: int,
    elapsed: float,
    body: dict[str, Any],
    *,
    expected_request_id: str,
) -> None:
    """Run all smoke assertions. Raises AssertionError on failure with
    a diagnostic message the deploy workflow log will surface."""
    assert status == 200, f"expected HTTP 200; got {status}; body={body}"
    assert elapsed < _LATENCY_CEILING_SECONDS, (
        f"latency {elapsed:.2f}s exceeded ceiling {_LATENCY_CEILING_SECONDS}s"
    )

    assert isinstance(body, dict), f"expected JSON object body; got {type(body).__name__}"
    assert body.get("request_id") == expected_request_id, (
        f"request_id mismatch: expected {expected_request_id!r}; got {body.get('request_id')!r}"
    )
    decision = body.get("decision")
    assert decision in _DECISION_BANDS, (
        f"decision must be one of {sorted(_DECISION_BANDS)}; got {decision!r}"
    )
    score = body.get("score")
    assert isinstance(score, (int, float)) and not isinstance(score, bool), (
        f"score must be a number; got {type(score).__name__}: {score!r}"
    )
    assert 0.0 <= float(score) <= 1.0, f"score must be in [0.0, 1.0]; got {score}"


def main(argv: list[str] | None = None) -> int:
    # Guard against -OO (strips docstrings) so the CLI description is
    # always defined.
    ap = argparse.ArgumentParser(description=(__doc__ or "smoke test").splitlines()[0])
    ap.add_argument(
        "--base-url",
        required=True,
        help="ALB / domain URL (e.g. https://app.example.com or the raw ALB DNS)",
    )
    ap.add_argument(
        "--tenant-token",
        required=True,
        help="Bearer token for a smoke-test tenant configured to accept CAD",
    )
    ap.add_argument(
        "--request-id",
        default=None,
        help="Override request_id (default: smoke-<unix_ts>)",
    )
    args = ap.parse_args(argv)

    request_id = args.request_id or f"smoke-{int(time.time())}"

    try:
        status, elapsed, body = _post_booking(args.base_url, args.tenant_token, request_id)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(
            f"smoke FAILED: connection error against {args.base_url} — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        assert_response(status, elapsed, body, expected_request_id=request_id)
    except AssertionError as exc:
        print(f"smoke FAILED: {exc}", file=sys.stderr)
        return 1

    # Success summary to stdout (conventional split: stdout on success,
    # stderr on failure) so deploy.yml can grep for "smoke OK" on the
    # run-success summary line independently of the failure stream.
    print(
        f"smoke OK: status={status} latency={elapsed:.3f}s "
        f"decision={body['decision']} score={body['score']:.3f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
