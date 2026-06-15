"""Migration 0007 — `ux_decisions_tenant_request_type` UNIQUE shape.

Contracts under test:

- Cross-type: a booking + modification with the same `request_id` legitimately
  coexist; two distinct rows persist in `decisions` distinguished by
  `request_type` (asserted both via HTTP 200 and a direct DB SELECT).
- Same-type duplicate booking: returns 200 with the prior decision envelope
  via the endpoint's SELECT-then-INSERT idempotency replay. The full response
  envelope is byte-equal (deterministic for same payload).
- Same-type duplicate modification: same SELECT-idempotency replay shape.

The shipments composite-PK (tenant_id, id) identity 409 — a second booking
reusing a shipment_id with a different request_id — IS serially exercisable and
is covered here (test_duplicate_shipment_id_different_request_id_returns_409),
paired with a replay negative control. The remaining same-request_id
UniqueViolation backstop (ux_shipments_tenant_request / ux_decisions_tenant_request_type)
fires only on the concurrent-race case (two writers SELECT-miss in parallel,
then race the INSERT); serial test flow cannot exercise that branch — logged as
a gap to .claude/BUGS.md for a future concurrent-race test follow-up.
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
        "shipment_id": f"ship-{request_id}",
        "transaction_number": f"txn-{request_id}",
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
        "shipment_id": f"ship-{original_request_id}",
        "transaction_number": f"txn-{original_request_id}",
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


async def test_duplicate_shipment_id_different_request_id_returns_409(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    """A second booking POST reusing a shipment_id already booked for this
    tenant — but with a DIFFERENT request_id — collides on the composite PK
    (tenant_id, id) and is surfaced as a clear 409 identity message (#8), NOT
    a raw constraint error. This is the platform-facing contract change: the
    shipment_id is the shipment identity and is single-use per tenant.

    The discriminated message ("already booked") distinguishes this from the
    request_id idempotency-race 409 — the two uniqueness surfaces must not be
    conflated (quality-constraint #1)."""
    token, _ = seeded_api_token
    first = await unauth_client.post(
        _BOOKING_PATH,
        json=_booking_payload(request_id="id409-book-a"),
        headers=_headers(token),
    )
    assert first.status_code == 200, first.text

    # Different request_id (so the pre-insert request_id replay SELECT misses),
    # but the SAME shipment_id as the first booking.
    collide = _booking_payload(request_id="id409-book-b")
    collide["shipment_id"] = "ship-id409-book-a"
    second = await unauth_client.post(_BOOKING_PATH, json=collide, headers=_headers(token))
    assert second.status_code == 409, second.text
    assert "already booked" in second.json()["detail"]


async def test_same_request_id_reuse_is_idempotent_replay_not_identity_409(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    """Negative control for the identity 409: a second booking POST with the
    SAME request_id (a genuine network-retry replay) returns the prior decision
    via the request_id idempotency path — 200, NOT a 409. This proves the
    identity-409 path (shipment_id collision) does not fire on, or short-circuit,
    the request_id replay path (#10, quality-constraint #1)."""
    token, _ = seeded_api_token
    payload = _booking_payload(request_id="id409-replay")
    first = await unauth_client.post(_BOOKING_PATH, json=payload, headers=_headers(token))
    assert first.status_code == 200, first.text
    second = await unauth_client.post(_BOOKING_PATH, json=payload, headers=_headers(token))
    assert second.status_code == 200, second.text
    _assert_decisions_equivalent(first.json(), second.json())


async def test_replay_with_divergent_identity_echoes_request_not_stored(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    """Documented replay-echo edge (#9 + #10): on a request_id idempotency
    replay the response echoes the REQUEST-supplied shipment_id /
    transaction_number, NOT the stored identity of the original booking. A
    malformed retry that reuses request_id with different identity fields echoes
    the new payload while returning the ORIGINAL decision — identity drift on
    replay is intentionally NOT validated (keeps the replay query untouched,
    #10). This pins the behavior as chosen, not implicit.

    Note: the divergent shipment_id is never persisted — the replay SELECT
    short-circuits before the shipments INSERT, so no composite-PK 409 fires."""
    token, _ = seeded_api_token
    first = await unauth_client.post(
        _BOOKING_PATH,
        json=_booking_payload(request_id="echo-edge"),
        headers=_headers(token),
    )
    assert first.status_code == 200, first.text

    # Same request_id (-> idempotent replay) but DIVERGENT identity fields.
    divergent = _booking_payload(request_id="echo-edge")
    divergent["shipment_id"] = "ship-DIVERGENT"
    divergent["transaction_number"] = "txn-DIVERGENT"
    replay = await unauth_client.post(_BOOKING_PATH, json=divergent, headers=_headers(token))
    assert replay.status_code == 200, replay.text

    body = replay.json()
    # Echo reflects the REQUEST payload, not the stored original.
    assert body["shipment_id"] == "ship-DIVERGENT"
    assert body["transaction_number"] == "txn-DIVERGENT"
    # ...while the decision envelope is the ORIGINAL (replay returns prior decision).
    first_body = first.json()
    assert body["decision"] == first_body["decision"]
    assert body["score"] == pytest.approx(first_body["score"], abs=1e-4)
    assert body["triggered_rules"] == first_body["triggered_rules"]
