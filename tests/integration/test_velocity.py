"""Integration tests for app/velocity.py — SQL-backed counters."""

from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address

import asyncpg
import pytest

from app.velocity import (
    count_ip_daily,
    count_ip_hourly,
    count_user_30d,
    count_user_daily,
    count_user_hourly,
)


@pytest.fixture
async def seeded_customer(db_conn: asyncpg.Connection, seeded_tenant: int) -> int:
    return await db_conn.fetchval(
        "INSERT INTO customers (tenant_id, external_id) VALUES ($1, 'vel-cust') RETURNING id",
        seeded_tenant,
    )


@pytest.fixture
async def seeded_user(db_conn: asyncpg.Connection, seeded_tenant: int, seeded_customer: int) -> int:
    return await db_conn.fetchval(
        "INSERT INTO users (tenant_id, customer_id, external_id) VALUES ($1, $2, 'vel-user') RETURNING id",
        seeded_tenant,
        seeded_customer,
    )


async def _seed_shipment(
    conn: asyncpg.Connection,
    tenant_id: int,
    customer_id: int,
    user_id: int,
    request_id: str,
    source_ip: str,
    booking_ts: datetime,
) -> None:
    await conn.execute(
        """
        INSERT INTO shipments (
            tenant_id, customer_id, user_id, request_id, source_ip,
            origin, destination, value, channel, booking_ts,
            destination_hmac
        )
        VALUES ($1, $2, $3, $4, $5::inet,
                '{}'::jsonb, '{}'::jsonb, 100, 'web', $6,
                'stub-hmac-velocity')
        """,
        tenant_id,
        customer_id,
        user_id,
        request_id,
        source_ip,
        booking_ts,
    )


async def test_user_hourly_counts_recent_only(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
    seeded_user: int,
) -> None:
    now = datetime.now(tz=UTC)
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "fresh-1",
        "192.0.2.50",
        now - timedelta(minutes=30),
    )
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "old-1",
        "192.0.2.50",
        now - timedelta(hours=2),
    )

    count = await count_user_hourly(db_conn, seeded_tenant, seeded_customer)
    assert count == 1  # only the fresh row is within 1 hour


async def test_user_daily_counts_24h_window(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
    seeded_user: int,
) -> None:
    now = datetime.now(tz=UTC)
    for i, delta_hours in enumerate((0.5, 12, 23.5, 25, 48)):
        await _seed_shipment(
            db_conn,
            seeded_tenant,
            seeded_customer,
            seeded_user,
            f"day-{i}",
            "192.0.2.51",
            now - timedelta(hours=delta_hours),
        )
    count = await count_user_daily(db_conn, seeded_tenant, seeded_customer)
    assert count == 3  # 0.5h, 12h, 23.5h — the 25h and 48h rows excluded


async def test_user_30d_counts_30day_window(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
    seeded_user: int,
) -> None:
    now = datetime.now(tz=UTC)
    for i, days in enumerate((1, 15, 29, 31, 60)):
        await _seed_shipment(
            db_conn,
            seeded_tenant,
            seeded_customer,
            seeded_user,
            f"30d-{i}",
            "192.0.2.52",
            now - timedelta(days=days),
        )
    count = await count_user_30d(db_conn, seeded_tenant, seeded_customer)
    assert count == 3  # 1, 15, 29 days


async def test_ip_hourly_filters_by_ip(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
    seeded_user: int,
) -> None:
    now = datetime.now(tz=UTC)
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "match-1",
        "192.0.2.60",
        now - timedelta(minutes=15),
    )
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "match-2",
        "192.0.2.60",
        now - timedelta(minutes=45),
    )
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "other-ip",
        "192.0.2.61",
        now - timedelta(minutes=10),
    )

    count = await count_ip_hourly(db_conn, seeded_tenant, IPv4Address("192.0.2.60"))
    assert count == 2


async def test_ip_daily_24h_window(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
    seeded_user: int,
) -> None:
    now = datetime.now(tz=UTC)
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "ip-fresh",
        "192.0.2.70",
        now - timedelta(hours=12),
    )
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "ip-stale",
        "192.0.2.70",
        now - timedelta(hours=30),
    )

    count = await count_ip_daily(db_conn, seeded_tenant, IPv4Address("192.0.2.70"))
    assert count == 1


async def test_velocity_scoped_to_tenant(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
    seeded_user: int,
) -> None:
    """A shipment under another tenant must NOT contribute to this
    tenant's velocity count — even with matching IP."""
    now = datetime.now(tz=UTC)
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "in-scope",
        "192.0.2.80",
        now - timedelta(minutes=10),
    )

    # Seed a different tenant
    other_tenant = await db_conn.fetchval(
        "INSERT INTO tenants (name) VALUES ('vel-other-tenant') RETURNING id"
    )
    other_customer = await db_conn.fetchval(
        "INSERT INTO customers (tenant_id, external_id) VALUES ($1, 'other-cust') RETURNING id",
        other_tenant,
    )
    other_user = await db_conn.fetchval(
        "INSERT INTO users (tenant_id, customer_id, external_id) VALUES ($1, $2, 'other-user') RETURNING id",
        other_tenant,
        other_customer,
    )
    await _seed_shipment(
        db_conn,
        other_tenant,
        other_customer,
        other_user,
        "out-of-scope",
        "192.0.2.80",
        now - timedelta(minutes=5),
    )

    try:
        count = await count_ip_hourly(db_conn, seeded_tenant, IPv4Address("192.0.2.80"))
        assert count == 1  # only the in-scope one
    finally:
        # Reuse the canonical cleanup helper rather than maintaining a
        # parallel inline DELETE list.
        from tests.conftest import _cleanup_tenant

        await _cleanup_tenant(db_conn, other_tenant)
