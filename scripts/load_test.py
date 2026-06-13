"""Locust load test harness.

Sends booking + modification + feedback traffic against the local
docker-compose stack with a synthetic mix approximating production
traffic:

  - 60% legitimate booking (case-2-shape: API channel, established
    cloud-IP customer)
  - 3% fraud booking (case-1-shape: high-risk fraud pattern)
  - 20% modification on a recently-booked request_id
  - 15% feedback (approved label) on a recently-booked request_id
  - ~2% idempotent replay (re-POST same booking/modification/feedback
    request_id) — exercises the cache hit path

Run against the live local stack (under the `riskd_app_login` role)
at 100 RPS sustained for 60+ seconds, then compare against the
measured baseline.

Usage:
    docker compose up -d
    locust -f scripts/load_test.py \\
        --host=http://localhost:8000 \\
        -u 100 -r 10 -t 60s \\
        --headless --csv=docs/load-test-phase-5

The harness expects 3-5 tenants seeded via `scripts/tenant_onboard.py`
and reads their tokens from env vars `LOAD_TEST_TOKEN_1`..._5. At
least one token is required; missing tokens are skipped.
"""

from __future__ import annotations

import os
import random
import uuid
from typing import Any

from locust import HttpUser, between, task


def _load_tokens() -> list[str]:
    """Read tenant API tokens from env. Required: LOAD_TEST_TOKEN_1.
    Optional: _2 through _5. Returns the list of non-empty tokens."""
    tokens: list[str] = []
    for i in range(1, 6):
        t = os.environ.get(f"LOAD_TEST_TOKEN_{i}")
        if t:
            tokens.append(t)
    if not tokens:
        msg = (
            "set LOAD_TEST_TOKEN_1 (and optionally _2.._5) before running "
            "this harness. Seed tokens via scripts/tenant_onboard.py."
        )
        raise RuntimeError(msg)
    return tokens


def _new_request_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _legitimate_booking(request_id: str, customer_external_id: str) -> dict[str, Any]:
    """Case-2-shape: established customer + API channel + cloud-IP
    origin (typical baseline-realistic legitimate traffic)."""
    return {
        "request_id": request_id,
        "customer": {"external_id": customer_external_id},
        "user": {"external_id": "load-user-legit"},
        "source_ip": "192.0.2.50",
        "shipment": {
            "origin": {"address": "1 Baseline Origin"},
            "destination": {"address": "2 Baseline Dest"},
            "value": 1500.00,
            "channel": "api",
        },
        "booking_ts": "2026-06-03T08:00:00Z",
    }


def _fraud_booking(request_id: str, customer_external_id: str) -> dict[str, Any]:
    """Case-1-shape: dashboard channel, fresh customer, high-value
    shipment + suspicious destination."""
    return {
        "request_id": request_id,
        "customer": {"external_id": customer_external_id},
        "user": {"external_id": "load-user-fraud"},
        "source_ip": "203.0.113.99",
        "shipment": {
            "origin": {"address": "1 Fraud Origin"},
            "destination": {"address": "999 Suspicious Lane"},
            "value": 14500.00,
            "channel": "dashboard",
        },
        "booking_ts": "2026-06-03T08:05:00Z",
    }


def _modification_body(request_id: str, original_request_id: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "original_request_id": original_request_id,
        "modification_ts": "2026-06-03T08:30:00Z",
        "modification_type": "value",
        "new_value": {"value": 1700},
    }


def _feedback_body(request_id: str, target_request_id: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "target_request_id": target_request_id,
        "label": "approved",
        "feedback_ts": "2026-06-03T08:45:00Z",
    }


class RiskdUser(HttpUser):
    """Locust user: picks a random tenant per request and exercises the
    booking/modification/feedback flow at the documented mix.

    `wait_time = between(0.05, 0.15)` targets ~100 RPS at the default
    user count (~10 users sustain ~100 req/s combined when running with
    the standard locust scheduler)."""

    wait_time = between(0.05, 0.15)

    def on_start(self) -> None:
        self.tokens = _load_tokens()
        self.recent_bookings: list[tuple[str, str]] = []  # (token, request_id)

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _record_booking(self, token: str, request_id: str) -> None:
        self.recent_bookings.append((token, request_id))
        if len(self.recent_bookings) > 200:
            self.recent_bookings.pop(0)

    def _pick_recent_booking(self) -> tuple[str, str] | None:
        if not self.recent_bookings:
            return None
        return random.choice(self.recent_bookings)

    @task(60)
    def legitimate_booking(self) -> None:
        token = random.choice(self.tokens)
        request_id = _new_request_id("legit-book")
        customer = f"load-cust-legit-{random.randint(1, 50)}"
        with self.client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_legitimate_booking(request_id, customer),
            headers=self._headers(token),
            catch_response=True,
            name="POST /booking/evaluate (legit)",
        ) as resp:
            if resp.status_code == 200:
                self._record_booking(token, request_id)
                resp.success()
            else:
                resp.failure(f"{resp.status_code} {resp.text[:200]}")

    @task(3)
    def fraud_booking(self) -> None:
        token = random.choice(self.tokens)
        request_id = _new_request_id("fraud-book")
        customer = f"load-cust-fraud-{random.randint(1, 20)}"
        with self.client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_fraud_booking(request_id, customer),
            headers=self._headers(token),
            catch_response=True,
            name="POST /booking/evaluate (fraud)",
        ) as resp:
            if resp.status_code == 200:
                self._record_booking(token, request_id)
                resp.success()
            else:
                resp.failure(f"{resp.status_code} {resp.text[:200]}")

    @task(20)
    def modification(self) -> None:
        recent = self._pick_recent_booking()
        if recent is None:
            return
        token, original = recent
        mod_request_id = _new_request_id("mod")
        with self.client.post(
            "/api/v1/shipments/modification/evaluate",
            json=_modification_body(mod_request_id, original),
            headers=self._headers(token),
            catch_response=True,
            name="POST /modification/evaluate",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"{resp.status_code} {resp.text[:200]}")

    @task(15)
    def feedback(self) -> None:
        recent = self._pick_recent_booking()
        if recent is None:
            return
        token, target = recent
        fb_request_id = _new_request_id("fb")
        with self.client.post(
            "/api/v1/shipments/feedback",
            json=_feedback_body(fb_request_id, target),
            headers=self._headers(token),
            catch_response=True,
            name="POST /feedback",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"{resp.status_code} {resp.text[:200]}")

    @task(2)
    def idempotent_replay(self) -> None:
        recent = self._pick_recent_booking()
        if recent is None:
            return
        token, request_id = recent
        with self.client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_legitimate_booking(request_id, f"load-cust-replay-{random.randint(1, 50)}"),
            headers=self._headers(token),
            catch_response=True,
            name="POST /booking/evaluate (replay)",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"{resp.status_code} {resp.text[:200]}")
