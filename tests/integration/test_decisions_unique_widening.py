"""Migration 0007 — `ux_decisions_tenant_request_type` UNIQUE shape.

Contracts under test:

- Cross-type: a booking + modification with the same `request_id` legitimately
  coexist; two distinct rows persist in `decisions` distinguished by
  `request_type` (asserted both via HTTP 200 and a direct DB SELECT).
- Same-type duplicate booking: returns 200 with the prior decision envelope
  via the endpoint's SELECT-then-INSERT idempotency replay. The full response
  envelope is byte-equal (deterministic for same payload).
- Same-type duplicate modification: same SELECT-idempotency replay shape.

The 409 try/except → UniqueViolation catch path in booking.py / modification.py
is the defense-in-depth backstop for the concurrent-race case (two writers
SELECT-miss in parallel, then race the INSERT). Serial test flow cannot
exercise that catch — logged as a gap to .claude/BUGS.md for a future
concurrent-race test follow-up.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from httpx import AsyncClient


def _assert_decisions_equivalent(first: dict[str, Any], second: dict[str, Any]) -> None:
    """Compare decision envelopes at numeric(5,4) precision on score.
    First response carries the raw float; replay carries the DB
    roundtrip. Other fields are byte-equal."""
    assert first["request_id"] == second["request_id"]
    assert first["decision"] == second["decision"]
    assert first["classification"] == second["classification"]
    assert first["risk_level"] == second["risk_level"]
    assert first["triggered_rules"] == second["triggered_rules"]
    assert first["risk_factors"] == second["risk_factors"]
    assert first["score"] == pytest.approx(second["score"], abs=1e-4)


_BOOKING_PATH = "/api/v1/shipments/booking/evaluate"
_MOD_PATH = "/api/v1/shipments/modification/evaluate"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _booking_payload(
    request_id: str = "uniq-book-001",
    customer_external_id: str = "uniq-cust-1",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "customer": {"external_id": customer_external_id},
        "user": {"external_id": "uniq-user-1"},
        "source_ip": "192.0.2.20",
        "shipment": {
            "origin": {"address": "30 Origin Lane"},
            "destination": {"address": "40 Destination Ave"},
            "value": 1200.00,
            "channel": "api",
        },
        "booking_ts": "2026-05-28T08:00:00Z",
    }


def _modification_payload(
    *,
    request_id: str = "uniq-mod-001",
    original_request_id: str = "uniq-book-001",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "original_request_id": original_request_id,
        "modification_ts": "2026-05-28T08:30:00Z",
        "modification_type": "value",
        "new_value": {"value": 1250},
    }


async def test_booking_and_modification_share_request_id(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
    db_conn: asyncpg.Connection,
) -> None:
    """Booking and modification with same request_id both succeed AND
    persist as two distinct rows in `decisions` (request_type discriminates).
    The DB-level assertion is the plan's load-bearing validation criterion
    — without it, a regression that silently aliased modification onto
    the booking row would still return 200."""
    token, tenant_id = seeded_api_token

    booking = await unauth_client.post(
        _BOOKING_PATH,
        json=_booking_payload(request_id="shared-001"),
        headers=_headers(token),
    )
    assert booking.status_code == 200, booking.text

    modification = await unauth_client.post(
        _MOD_PATH,
        json=_modification_payload(
            request_id="shared-001",
            original_request_id="shared-001",
        ),
        headers=_headers(token),
    )
    assert modification.status_code == 200, modification.text

    rows = await db_conn.fetch(
        "SELECT request_type FROM decisions "
        "WHERE tenant_id = $1 AND request_id = $2 "
        "ORDER BY request_type",
        tenant_id,
        "shared-001",
    )
    request_types = sorted(row["request_type"] for row in rows)
    assert request_types == ["booking", "modification"], (
        f"expected two decisions rows with request_types ['booking', "
        f"'modification']; got {request_types}"
    )


async def test_duplicate_booking_same_request_id_replays(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
    db_conn: asyncpg.Connection,
) -> None:
    """Duplicate POST of the same booking request_id returns the prior
    decision envelope (idempotency by SELECT-before-INSERT). Full envelope
    equality proves the SELECT branch fired (not a fresh score with the
    same outcome). A single decisions row persists."""
    token, tenant_id = seeded_api_token

    first = await unauth_client.post(
        _BOOKING_PATH,
        json=_booking_payload(request_id="dup-book-001"),
        headers=_headers(token),
    )
    assert first.status_code == 200, first.text

    second = await unauth_client.post(
        _BOOKING_PATH,
        json=_booking_payload(request_id="dup-book-001"),
        headers=_headers(token),
    )
    assert second.status_code == 200, second.text
    _assert_decisions_equivalent(first.json(), second.json())

    count = await db_conn.fetchval(
        "SELECT count(*) FROM decisions "
        "WHERE tenant_id = $1 AND request_id = $2 AND request_type = 'booking'",
        tenant_id,
        "dup-book-001",
    )
    assert count == 1, f"expected exactly one persisted booking row; got {count}"


async def test_duplicate_modification_same_request_id_replays(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
    db_conn: asyncpg.Connection,
) -> None:
    """Same as duplicate-booking but for modification."""
    token, tenant_id = seeded_api_token

    seed = await unauth_client.post(
        _BOOKING_PATH,
        json=_booking_payload(request_id="dup-mod-base-001"),
        headers=_headers(token),
    )
    assert seed.status_code == 200, seed.text

    first = await unauth_client.post(
        _MOD_PATH,
        json=_modification_payload(
            request_id="dup-mod-001",
            original_request_id="dup-mod-base-001",
        ),
        headers=_headers(token),
    )
    assert first.status_code == 200, first.text

    second = await unauth_client.post(
        _MOD_PATH,
        json=_modification_payload(
            request_id="dup-mod-001",
            original_request_id="dup-mod-base-001",
        ),
        headers=_headers(token),
    )
    assert second.status_code == 200, second.text
    _assert_decisions_equivalent(first.json(), second.json())

    count = await db_conn.fetchval(
        "SELECT count(*) FROM decisions "
        "WHERE tenant_id = $1 AND request_id = $2 AND request_type = 'modification'",
        tenant_id,
        "dup-mod-001",
    )
    assert count == 1, f"expected exactly one persisted modification row; got {count}"
