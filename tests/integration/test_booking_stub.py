"""POST /api/v1/shipments/booking/evaluate — stub endpoint (1C.1).

Phase 1 returns ALLOW 0.0 for every well-formed payload. These tests cover:
- Happy path returns 200 ALLOW 0.0
- Payload validation (422 on invalid input, IPv6 rejection)
- Idempotency (duplicate request_id returns the same response)
- Implicit registration (first booking creates customer / enterprise / user)
"""

from typing import Any

import asyncpg
import pytest
from httpx import AsyncClient


def _payload(request_id: str = "test-req-1", customer_id: str = "cust-1") -> dict[str, Any]:
    """Minimal valid booking payload."""
    return {
        "request_id": request_id,
        "customer": {"external_id": customer_id},
        "user": {"external_id": "user-1"},
        "source_ip": "192.0.2.42",
        "shipment": {
            "origin": {"address": "123 Main St"},
            "destination": {"address": "456 Oak Rd"},
            "value": 100.50,
            "channel": "web",
        },
        "booking_ts": "2026-05-26T10:00:00Z",
    }


async def test_valid_booking_returns_allow_0_0(
    unauth_client: AsyncClient, seeded_api_token: tuple[str, int]
) -> None:
    token, _ = seeded_api_token
    response = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate",
        json=_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "ALLOW"
    # Phase 2: brand-new customer's first booking gets account_prior = 0.10
    # (base_prior = MAX_NEW_ACCOUNT * (1 - maturity=0) = 0.10) with no
    # Layer 3 rules firing. Pre-Phase-2 this asserted 0.0.
    assert body["score"] == pytest.approx(0.10)
    assert body["classification"] == "GREEN"
    assert body["risk_level"] == "LOW"
    assert body["triggered_rules"] == []
    assert body["risk_factors"] == []
    assert body["request_id"] == "test-req-1"


async def test_invalid_payload_returns_422(
    unauth_client: AsyncClient, seeded_api_token: tuple[str, int]
) -> None:
    """Pydantic validation rejects missing required fields."""
    token, _ = seeded_api_token
    bad = _payload()
    del bad["customer"]
    response = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate",
        json=bad,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any("customer" in str(err).lower() for err in detail)


async def test_missing_origin_address_returns_422(
    unauth_client: AsyncClient, seeded_api_token: tuple[str, int]
) -> None:
    token, _ = seeded_api_token
    bad = _payload()
    del bad["shipment"]["origin"]["address"]
    response = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate",
        json=bad,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


async def test_ipv6_source_ip_rejected(
    unauth_client: AsyncClient, seeded_api_token: tuple[str, int]
) -> None:
    """v1 is IPv4-only per .ai/decisions.md."""
    token, _ = seeded_api_token
    bad = _payload()
    bad["source_ip"] = "2001:db8::1"
    response = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate",
        json=bad,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


async def test_negative_shipment_value_returns_422(
    unauth_client: AsyncClient, seeded_api_token: tuple[str, int]
) -> None:
    token, _ = seeded_api_token
    bad = _payload()
    bad["shipment"]["value"] = -10
    response = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate",
        json=bad,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


async def test_duplicate_request_id_returns_idempotent(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Replaying the same (tenant_id, request_id) returns the prior decision
    without re-persisting (shipments + decisions UNIQUE on (tenant_id, request_id))."""
    token, tenant_id = seeded_api_token
    headers = {"Authorization": f"Bearer {token}"}
    payload = _payload(request_id="idempotent-test-1")

    r1 = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate", json=payload, headers=headers
    )
    assert r1.status_code == 200

    r2 = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate", json=payload, headers=headers
    )
    assert r2.status_code == 200
    # Decision content matches; score may differ by float-precision after
    # the DB round-trip (NUMERIC column rounds `0.09999999999999998` to
    # `0.1`). Phase 2's account_prior of 0.10 surfaces the precision
    # boundary the Phase 1 clean-baseline 0.0 case never reached.
    r1_body, r2_body = r1.json(), r2.json()
    assert r1_body["decision"] == r2_body["decision"]
    assert r1_body["classification"] == r2_body["classification"]
    assert r1_body["risk_level"] == r2_body["risk_level"]
    assert r1_body["triggered_rules"] == r2_body["triggered_rules"]
    assert r1_body["risk_factors"] == r2_body["risk_factors"]
    assert r1_body["score"] == pytest.approx(r2_body["score"], abs=1e-9)

    # Exactly one shipment row persisted.
    count = await db_conn.fetchval(
        "SELECT count(*) FROM shipments WHERE tenant_id = $1 AND request_id = $2",
        tenant_id,
        "idempotent-test-1",
    )
    assert count == 1


async def test_first_booking_creates_customer(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    token, tenant_id = seeded_api_token
    headers = {"Authorization": f"Bearer {token}"}
    payload = _payload(request_id="create-test-1", customer_id="brand-new-cust")
    payload["customer"]["registered_address"] = "1 Test Plaza"

    response = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate", json=payload, headers=headers
    )
    assert response.status_code == 200

    row = await db_conn.fetchrow(
        "SELECT id, registered_address, total_shipments FROM customers "
        "WHERE tenant_id = $1 AND external_id = $2",
        tenant_id,
        "brand-new-cust",
    )
    assert row is not None
    assert row["registered_address"] == "1 Test Plaza"
    assert row["total_shipments"] == 1


async def test_booking_with_enterprise_links_customer(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    token, tenant_id = seeded_api_token
    headers = {"Authorization": f"Bearer {token}"}
    payload = _payload(request_id="enterprise-test-1", customer_id="ent-cust")
    payload["enterprise"] = {"external_id": "ent-001"}

    response = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate", json=payload, headers=headers
    )
    assert response.status_code == 200

    customer_row = await db_conn.fetchrow(
        "SELECT id, enterprise_id FROM customers WHERE tenant_id = $1 AND external_id = $2",
        tenant_id,
        "ent-cust",
    )
    assert customer_row is not None
    assert customer_row["enterprise_id"] is not None

    enterprise_row = await db_conn.fetchrow(
        "SELECT external_id FROM enterprises WHERE id = $1",
        customer_row["enterprise_id"],
    )
    assert enterprise_row is not None
    assert enterprise_row["external_id"] == "ent-001"


async def test_missing_auth_returns_401(unauth_client: AsyncClient) -> None:
    """Booking endpoint requires Bearer token."""
    response = await unauth_client.post("/api/v1/shipments/booking/evaluate", json=_payload())
    assert response.status_code == 401
