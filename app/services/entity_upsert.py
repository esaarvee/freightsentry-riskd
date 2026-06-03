"""Implicit-registration upserts for customer / enterprise / user.

Booking payloads carry optional metadata (registered_address,
business_name, enterprise_id, etc.) which populate the entity records on
first sight and update on subsequent bookings. Updates use COALESCE so a
None field in the payload leaves the existing DB value alone — only
non-None payload fields override.

Run inside the booking endpoint's transaction; the caller has already
set the RLS tenant context via `set_tenant_id`.
"""

from datetime import datetime

import asyncpg

from app.models import BookingRequest


async def upsert_enterprise(
    conn: asyncpg.Connection,
    tenant_id: int,
    external_id: str,
) -> int:
    """Insert or no-op-update; return enterprise id.

    The no-op `DO UPDATE SET external_id = EXCLUDED.external_id` forces
    RETURNING to fire on conflict; cheaper than the SELECT-then-INSERT
    alternative and avoids the race where two concurrent inserts both
    SELECT-miss.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO enterprises (tenant_id, external_id)
        VALUES ($1, $2)
        ON CONFLICT (tenant_id, external_id) DO UPDATE
            SET external_id = EXCLUDED.external_id
        RETURNING id
        """,
        tenant_id,
        external_id,
    )
    if row is None:
        msg = "enterprise upsert returned no row"
        raise RuntimeError(msg)
    return int(row["id"])


async def upsert_customer(
    conn: asyncpg.Connection,
    tenant_id: int,
    payload: BookingRequest,
) -> int:
    """Insert or update; return customer id.

    Enterprise (if present in payload) is upserted first so we have its
    id. Optional metadata fields (registered_address, business_name,
    is_api_partner) override on update only when the payload provides
    them — None means "leave existing".
    """
    enterprise_id: int | None = None
    if payload.enterprise is not None:
        enterprise_id = await upsert_enterprise(conn, tenant_id, payload.enterprise.external_id)

    c = payload.customer
    # Phase 6A.7: registered_country joins the COALESCE-on-update set so
    # that a payload supplying None does NOT overwrite an existing
    # operator-supplied (or earlier-payload-supplied) value. ISO 3166-1
    # alpha-2 validation is enforced at the Pydantic layer by
    # CustomerData.registered_country (Phase 6A.5).
    row = await conn.fetchrow(
        """
        INSERT INTO customers (
            tenant_id, enterprise_id, external_id,
            registered_address, business_name, is_api_partner,
            registered_country, first_seen
        )
        VALUES (
            $1, $2, $3, $4, $5, COALESCE($6, false),
            $7, COALESCE($8, now())
        )
        ON CONFLICT (tenant_id, external_id) DO UPDATE SET
            enterprise_id      = COALESCE(EXCLUDED.enterprise_id, customers.enterprise_id),
            registered_address = COALESCE(EXCLUDED.registered_address, customers.registered_address),
            business_name      = COALESCE(EXCLUDED.business_name, customers.business_name),
            is_api_partner     = COALESCE(EXCLUDED.is_api_partner, customers.is_api_partner),
            registered_country = COALESCE(EXCLUDED.registered_country, customers.registered_country)
        RETURNING id
        """,
        tenant_id,
        enterprise_id,
        c.external_id,
        c.registered_address,
        c.business_name,
        c.is_api_partner,
        c.registered_country,
        c.first_seen_at,
    )
    if row is None:
        msg = "customer upsert returned no row"
        raise RuntimeError(msg)
    return int(row["id"])


async def upsert_user(
    conn: asyncpg.Connection,
    tenant_id: int,
    customer_id: int,
    user_external_id: str,
    first_seen_at: datetime | None = None,
) -> int:
    """Insert or update last_seen; return user id."""
    row = await conn.fetchrow(
        """
        INSERT INTO users (tenant_id, customer_id, external_id, first_seen)
        VALUES ($1, $2, $3, COALESCE($4, now()))
        ON CONFLICT (tenant_id, customer_id, external_id) DO UPDATE
            SET last_seen = now()
        RETURNING id
        """,
        tenant_id,
        customer_id,
        user_external_id,
        first_seen_at,
    )
    if row is None:
        msg = "user upsert returned no row"
        raise RuntimeError(msg)
    return int(row["id"])
