"""Phase 6A.7 — tenant route population baseline UPSERT path.

Per-booking writer for `tenant_route_baselines`. Increments the
(customer_country, origin_country, destination_country) triple count
for the booking's tenant. No-op when any country is None — bookings
without ground-truth structured data don't pollute the baseline.

Called from `app/api/booking.py` after the shipment commit, inside the
same transaction that holds the baseline FOR UPDATE lock. Failure of
this UPSERT rolls back the booking; the latency cost is bounded by the
single-row PK lookup + insert (≈1 ms).

RLS: requires `app.tenant_id` to be set on the connection. The booking
endpoint sets this immediately after opening the transaction (Phase
5D pattern). GRANTs on the table to `riskd_app` are in migration 0011.
"""

from __future__ import annotations

import asyncpg


async def update_tenant_route_baseline(
    conn: asyncpg.Connection,
    tenant_id: int,
    customer_country: str | None,
    origin_country: str | None,
    destination_country: str | None,
) -> None:
    """Increment the population count for this route triple. No-op when
    any country is None.

    Pre-conditions:
    - `app.tenant_id` is set on `conn` (RLS policy will deny otherwise).
    - `customer_country` / `origin_country` / `destination_country` are
      either None or already ISO 3166-1 alpha-2 validated upstream
      (Pydantic — CustomerData.registered_country + Address.country
      per Phase 6A.5).

    On conflict (existing triple), bumps `observation_count` by 1 and
    advances `last_updated`.
    """
    if not (customer_country and origin_country and destination_country):
        return
    await conn.execute(
        """
        INSERT INTO tenant_route_baselines (
            tenant_id, customer_country, origin_country, destination_country,
            observation_count, last_updated
        )
        VALUES ($1, $2, $3, $4, 1, now())
        ON CONFLICT (tenant_id, customer_country, origin_country, destination_country)
        DO UPDATE SET
            observation_count = tenant_route_baselines.observation_count + 1,
            last_updated      = now()
        """,
        tenant_id,
        customer_country,
        origin_country,
        destination_country,
    )
