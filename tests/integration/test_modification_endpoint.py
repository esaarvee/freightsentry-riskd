"""End-to-end integration tests for POST /api/v1/shipments/modification/evaluate.

Covers the endpoint contract surfaces:
- Happy path: prior booking → modification returns ALLOW/REVIEW/BLOCK
- Idempotency: replay of same request_id returns prior decision
- 404 when original_request_id has no prior booking
- 422 when original_request_id resolves to a prior modification
- Cross-tenant isolation: tenant_b token cannot modify tenant_a booking
- Persisted decision row carries request_type='modification'

Scoring semantics + rule-firing assertions defer to 3A.7 / 3A.8 — this
file pins endpoint behaviour, not score outcomes.

Note on idempotency assertions: decisions.score is persisted as
numeric(5,4), so the DB roundtrip rounds to 4 decimal places. The first
response returns the raw float result; the replay returns the
DB-roundtripped value. Equality assertions therefore round to 4 decimals
or compare the actionable fields (decision, classification, rules)
rather than expecting byte-equal envelopes.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from httpx import AsyncClient

from tests.conftest import create_tenant_with_token, set_test_tenant_id


def _assert_decisions_equivalent(first: dict[str, Any], second: dict[str, Any]) -> None:
    """Decisions are equivalent if their actionable fields match. Score is
    compared at 4-decimal precision (column type numeric(5,4))."""
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


def _booking_payload(request_id: str = "book-mod-001") -> dict[str, Any]:
    return {
        "request_id": request_id,
        "customer": {"external_id": "mod-cust-1"},
        "user": {"external_id": "mod-user-1"},
        "source_ip": "192.0.2.10",
        "shipment": {
            "origin": {"address": "10 Origin Lane"},
            "destination": {"address": "20 Destination Ave"},
            "value": 1000.00,
            "channel": "api",
        },
        "booking_ts": "2026-05-27T08:00:00Z",
    }


def _modification_payload(
    *,
    request_id: str = "mod-001",
    original_request_id: str = "book-mod-001",
    modification_type: str = "value",
    new_value: dict[str, Any] | None = None,
    modification_ts: str = "2026-05-27T08:30:00Z",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "original_request_id": original_request_id,
        "modification_ts": modification_ts,
        "modification_type": modification_type,
        "new_value": new_value or {"value": 1050},
    }


async def test_modification_happy_path_returns_decision_envelope(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    """Submit booking, then modification, verify the response envelope
    matches ModificationResponse shape with one of the three known
    decision values."""
    token, _ = seeded_api_token
    booking_resp = await unauth_client.post(
        _BOOKING_PATH, json=_booking_payload(), headers=_headers(token)
    )
    assert booking_resp.status_code == 200, booking_resp.text

    mod_resp = await unauth_client.post(
        _MOD_PATH, json=_modification_payload(), headers=_headers(token)
    )
    assert mod_resp.status_code == 200, mod_resp.text

    body = mod_resp.json()
    assert body["request_id"] == "mod-001"
    assert body["decision"] in ("ALLOW", "REVIEW", "BLOCK")
    assert 0.0 <= body["score"] <= 1.0
    assert body["classification"] in ("GREEN", "YELLOW", "RED")
    assert body["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert isinstance(body["triggered_rules"], list)
    assert isinstance(body["risk_factors"], list)


async def test_modification_persists_decision_with_request_type(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Confirm the persisted decision row carries request_type='modification'
    and that no new shipments row was created (modifications reference
    the prior shipment)."""
    token, tenant_id = seeded_api_token
    booking_resp = await unauth_client.post(
        _BOOKING_PATH, json=_booking_payload(), headers=_headers(token)
    )
    assert booking_resp.status_code == 200, booking_resp.text
    mod_resp = await unauth_client.post(
        _MOD_PATH, json=_modification_payload(), headers=_headers(token)
    )
    assert mod_resp.status_code == 200, mod_resp.text

    decisions = await db_conn.fetch(
        """
        SELECT request_id, request_type
          FROM decisions
         WHERE tenant_id = $1
         ORDER BY created_at
        """,
        tenant_id,
    )
    assert len(decisions) == 2
    assert decisions[0]["request_id"] == "book-mod-001"
    assert decisions[0]["request_type"] == "booking"
    assert decisions[1]["request_id"] == "mod-001"
    assert decisions[1]["request_type"] == "modification"

    # Only one shipments row (the original booking; modification did NOT
    # insert a new shipments row).
    shipment_count = await db_conn.fetchval(
        "SELECT count(*) FROM shipments WHERE tenant_id = $1", tenant_id
    )
    assert shipment_count == 1


async def test_modification_idempotency_returns_prior_decision(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """A replay of the same modification request_id must return the
    prior decision verbatim, NOT re-score, and NOT insert a second
    decisions row."""
    token, tenant_id = seeded_api_token
    await unauth_client.post(_BOOKING_PATH, json=_booking_payload(), headers=_headers(token))
    first = await unauth_client.post(
        _MOD_PATH, json=_modification_payload(), headers=_headers(token)
    )
    second = await unauth_client.post(
        _MOD_PATH, json=_modification_payload(), headers=_headers(token)
    )
    assert first.status_code == 200
    assert second.status_code == 200
    _assert_decisions_equivalent(first.json(), second.json())

    decision_count = await db_conn.fetchval(
        """
        SELECT count(*) FROM decisions
         WHERE tenant_id = $1 AND request_type = 'modification'
        """,
        tenant_id,
    )
    assert decision_count == 1


async def test_modification_with_unknown_original_returns_404(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    """No prior booking with original_request_id → 404."""
    token, _ = seeded_api_token
    resp = await unauth_client.post(
        _MOD_PATH,
        json=_modification_payload(
            request_id="mod-orphan",
            original_request_id="never-booked",
        ),
        headers=_headers(token),
    )
    assert resp.status_code == 404
    assert "Original booking not found" in resp.json()["detail"]


async def test_modification_of_modification_returns_422(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    """A modification whose original_request_id resolves to a prior
    modification (rather than a booking) must be rejected with 422.
    Phase 3 scope explicitly excludes modify-of-modification."""
    token, _ = seeded_api_token
    await unauth_client.post(_BOOKING_PATH, json=_booking_payload(), headers=_headers(token))
    first_mod = await unauth_client.post(
        _MOD_PATH, json=_modification_payload(request_id="mod-first"), headers=_headers(token)
    )
    assert first_mod.status_code == 200

    second_mod = await unauth_client.post(
        _MOD_PATH,
        json=_modification_payload(
            request_id="mod-of-mod",
            original_request_id="mod-first",  # points at the prior modification
        ),
        headers=_headers(token),
    )
    assert second_mod.status_code == 422
    detail = second_mod.json()["detail"]
    assert "non-booking" in detail
    assert "modification" in detail  # the actual prior request_type surfaces


async def test_modification_cross_tenant_returns_404(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Tenant B token attempting to modify Tenant A's booking → 404
    (the WHERE tenant_id filter scopes the lookup; the booking is
    invisible to tenant B)."""
    token_a, tenant_a = seeded_api_token
    await unauth_client.post(_BOOKING_PATH, json=_booking_payload(), headers=_headers(token_a))

    async with create_tenant_with_token(db_conn) as (token_b, _tenant_b):
        resp = await unauth_client.post(
            _MOD_PATH,
            json=_modification_payload(
                request_id="cross-tenant-mod",
                original_request_id="book-mod-001",  # tenant A's request_id
            ),
            headers=_headers(token_b),
        )
        assert resp.status_code == 404
    await set_test_tenant_id(db_conn, tenant_a)


async def test_modification_reusing_booking_request_id_now_succeeds(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
    db_conn: asyncpg.Connection,
) -> None:
    """Per 5A.7, the UNIQUE is `(tenant_id, request_type, request_id)`
    (migration 0007 / index ux_decisions_tenant_request_type). A
    modification whose request_id matches a prior booking's request_id
    now succeeds — they occupy separate namespaces. Asserts two distinct
    decisions rows persist (one per request_type)."""
    token, tenant_id = seeded_api_token
    booking_resp = await unauth_client.post(
        _BOOKING_PATH,
        json=_booking_payload(request_id="shared-id-001"),
        headers=_headers(token),
    )
    assert booking_resp.status_code == 200, booking_resp.text

    cross_type = await unauth_client.post(
        _MOD_PATH,
        json=_modification_payload(
            request_id="shared-id-001",
            original_request_id="shared-id-001",
        ),
        headers=_headers(token),
    )
    assert cross_type.status_code == 200, cross_type.text

    request_types = sorted(
        row["request_type"]
        for row in await db_conn.fetch(
            "SELECT request_type FROM decisions WHERE tenant_id = $1 AND request_id = $2",
            tenant_id,
            "shared-id-001",
        )
    )
    assert request_types == ["booking", "modification"]


async def test_modification_replay_with_different_payload_still_returns_prior(
    unauth_client: AsyncClient,
    seeded_api_token: tuple[str, int],
) -> None:
    """Idempotency is keyed on (tenant_id, request_id) of the modification
    itself; even if the second POST carries a different magnitude /
    modification_type, the prior decision wins. This is the canonical
    network-retry-safety semantic — the operator's retry MUST be a
    no-op even if the second payload diverges (e.g. a buggy retry that
    re-serialized with extra fields)."""
    token, _ = seeded_api_token
    await unauth_client.post(_BOOKING_PATH, json=_booking_payload(), headers=_headers(token))
    first = await unauth_client.post(
        _MOD_PATH, json=_modification_payload(new_value={"value": 1050}), headers=_headers(token)
    )
    second = await unauth_client.post(
        _MOD_PATH,
        json=_modification_payload(new_value={"value": 99999}),  # different payload, same id
        headers=_headers(token),
    )
    assert first.status_code == 200
    assert second.status_code == 200
    _assert_decisions_equivalent(first.json(), second.json())
