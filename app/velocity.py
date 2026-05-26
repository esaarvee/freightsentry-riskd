"""SQL-backed velocity counters.

Five sliding-window counts read from `shipments`. Indexes on
`(tenant_id, customer_id, booking_ts)` and `(tenant_id, source_ip,
booking_ts)` cover the typical query shape (see migration 0001).

Counts are bounded by the SQL `booking_ts > now() - interval` clause —
no unbounded scans, no user-controlled window sizes (per CLAUDE.md
never-skip Pattern C3).
"""

from ipaddress import IPv4Address

import asyncpg


async def count_user_hourly(conn: asyncpg.Connection, tenant_id: int, customer_id: int) -> int:
    result: int = await conn.fetchval(
        """
        SELECT count(*)::int FROM shipments
         WHERE tenant_id = $1 AND customer_id = $2
           AND booking_ts > now() - interval '1 hour'
        """,
        tenant_id,
        customer_id,
    )
    return result


async def count_user_daily(conn: asyncpg.Connection, tenant_id: int, customer_id: int) -> int:
    result: int = await conn.fetchval(
        """
        SELECT count(*)::int FROM shipments
         WHERE tenant_id = $1 AND customer_id = $2
           AND booking_ts > now() - interval '24 hours'
        """,
        tenant_id,
        customer_id,
    )
    return result


async def count_user_30d(conn: asyncpg.Connection, tenant_id: int, customer_id: int) -> int:
    result: int = await conn.fetchval(
        """
        SELECT count(*)::int FROM shipments
         WHERE tenant_id = $1 AND customer_id = $2
           AND booking_ts > now() - interval '30 days'
        """,
        tenant_id,
        customer_id,
    )
    return result


async def count_ip_hourly(conn: asyncpg.Connection, tenant_id: int, source_ip: IPv4Address) -> int:
    result: int = await conn.fetchval(
        """
        SELECT count(*)::int FROM shipments
         WHERE tenant_id = $1 AND source_ip = $2::inet
           AND booking_ts > now() - interval '1 hour'
        """,
        tenant_id,
        str(source_ip),
    )
    return result


async def count_ip_daily(conn: asyncpg.Connection, tenant_id: int, source_ip: IPv4Address) -> int:
    result: int = await conn.fetchval(
        """
        SELECT count(*)::int FROM shipments
         WHERE tenant_id = $1 AND source_ip = $2::inet
           AND booking_ts > now() - interval '24 hours'
        """,
        tenant_id,
        str(source_ip),
    )
    return result


async def count_user_distinct_ips_30d(
    conn: asyncpg.Connection, tenant_id: int, customer_id: int
) -> int:
    """Distinct source IPs the customer booked from in the last 30 days.

    Proxy for IP-diversity — high values indicate the account is being
    accessed from many places, which combined with low trust suggests
    account-takeover. Tenant-scoped; bounded by the 30-day window per
    Pattern C3.
    """
    result: int = await conn.fetchval(
        """
        SELECT COUNT(DISTINCT source_ip)::int FROM shipments
         WHERE tenant_id = $1 AND customer_id = $2
           AND booking_ts > now() - interval '30 days'
        """,
        tenant_id,
        customer_id,
    )
    return result


async def count_recipient_distinct_customers_30d(
    conn: asyncpg.Connection, tenant_id: int, destination_hmac: str
) -> int:
    """Distinct customers within THIS tenant that shipped to the given
    destination HMAC in the last 30 days.

    SECURITY: tenant_id MUST appear in WHERE. Without it, the query
    leaks fraud-pattern information across tenants. See the cross-
    tenant integration test in tests/integration/test_tenant_isolation.py.

    Index `ix_shipments_tenant_dest_hmac_booking_ts` (added in 0002)
    covers the (tenant_id, destination_hmac, booking_ts) filter.
    """
    result: int = await conn.fetchval(
        """
        SELECT COUNT(DISTINCT customer_id)::int FROM shipments
         WHERE tenant_id = $1 AND destination_hmac = $2
           AND booking_ts > now() - interval '30 days'
        """,
        tenant_id,
        destination_hmac,
    )
    return result
