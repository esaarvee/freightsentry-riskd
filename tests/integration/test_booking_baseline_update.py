"""Integration tests for end-to-end booking → tenant_route_baselines.

Exercises the full /api/v1/shipments/booking/evaluate path with structured
country data + asserts the population baseline row materializes with the
correct count. Complements the unit tests in
tests/unit/test_tenant_route_baselines.py which exercise the UPSERT
helper directly.

Also covers the customer upsert COALESCE-preservation: a second booking
with `registered_country=None` does NOT clobber the previously-stored
value.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from httpx import AsyncClient


def _payload(
    *,
    request_id: str = "trb-req-1",
    customer_id: str = "trb-cust-1",
    user_id: str = "trb-user-1",
    customer_country: str | None = "CA",
    origin_country: str | None = "CA",
    destination_country: str | None = "US",
) -> dict[str, Any]:
    customer: dict[str, Any] = {"external_id": customer_id}
    if customer_country is not None:
        customer["registered_country"] = customer_country
    origin: dict[str, Any] = {"address": "100 Industrial Park"}
    if origin_country is not None:
        origin["country"] = origin_country
    destination: dict[str, Any] = {"address": "500 Distribution Center"}
    if destination_country is not None:
        destination["country"] = destination_country
    return {
        "request_id": request_id,
        "shipment_id": f"ship-{request_id}",
        "transaction_number": f"txn-{request_id}",
        "customer": customer,
        "user": {"external_id": user_id},
        "source_ip": "192.0.2.42",
        "shipment": {
            "origin": origin,
            "destination": destination,
            "value": 100.50,
            "channel": "web",
        },
        "booking_ts": "2026-05-26T10:00:00Z",
    }


async def test_booking_with_full_country_data_populates_baseline_row(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    token, tenant_id = seeded_api_token
    response = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate",
        json=_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    row = await db_conn.fetchrow(
        """
        SELECT observation_count FROM tenant_route_baselines
        WHERE tenant_id = $1
          AND customer_country = $2 AND origin_country = $3 AND destination_country = $4
        """,
        tenant_id,
        "CA",
        "CA",
        "US",
    )
    assert row is not None
    assert row["observation_count"] == 1


async def test_second_booking_same_triple_bumps_count(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    token, tenant_id = seeded_api_token
    for i in range(2):
        response = await unauth_client.post(
            "/api/v1/shipments/booking/evaluate",
            json=_payload(request_id=f"trb-bump-{i}"),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
    count = await db_conn.fetchval(
        """
        SELECT observation_count FROM tenant_route_baselines
        WHERE tenant_id = $1
          AND customer_country = $2 AND origin_country = $3 AND destination_country = $4
        """,
        tenant_id,
        "CA",
        "CA",
        "US",
    )
    assert count == 2


async def test_booking_with_none_customer_country_does_not_populate_baseline(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    token, tenant_id = seeded_api_token
    response = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate",
        json=_payload(customer_country=None),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    count = await db_conn.fetchval(
        "SELECT COUNT(*) FROM tenant_route_baselines WHERE tenant_id = $1",
        tenant_id,
    )
    assert count == 0


async def test_customer_upsert_coalesce_preserves_registered_country(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """First booking sets registered_country='CA'; second booking omits
    the field; customer row retains 'CA' (COALESCE-on-update)."""
    token, tenant_id = seeded_api_token
    r1 = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate",
        json=_payload(request_id="coalesce-1", customer_country="CA"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200
    r2 = await unauth_client.post(
        "/api/v1/shipments/booking/evaluate",
        json=_payload(request_id="coalesce-2", customer_country=None),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200
    stored = await db_conn.fetchval(
        "SELECT registered_country FROM customers WHERE tenant_id = $1 AND external_id = $2",
        tenant_id,
        "trb-cust-1",
    )
    assert stored == "CA"
