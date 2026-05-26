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


async def count_user_hourly(
    conn: asyncpg.Connection, tenant_id: int, customer_id: int
) -> int:
    result: int = await conn.fetchval(
        """
        SELECT count(*)::int FROM shipments
         WHERE tenant_id = $1 AND customer_id = $2
           AND booking_ts > now() - interval '1 hour'
        """,
        tenant_id, customer_id,
    )
    return result


async def count_user_daily(
    conn: asyncpg.Connection, tenant_id: int, customer_id: int
) -> int:
    result: int = await conn.fetchval(
        """
        SELECT count(*)::int FROM shipments
         WHERE tenant_id = $1 AND customer_id = $2
           AND booking_ts > now() - interval '24 hours'
        """,
        tenant_id, customer_id,
    )
    return result


async def count_user_30d(
    conn: asyncpg.Connection, tenant_id: int, customer_id: int
) -> int:
    result: int = await conn.fetchval(
        """
        SELECT count(*)::int FROM shipments
         WHERE tenant_id = $1 AND customer_id = $2
           AND booking_ts > now() - interval '30 days'
        """,
        tenant_id, customer_id,
    )
    return result


async def count_ip_hourly(
    conn: asyncpg.Connection, tenant_id: int, source_ip: IPv4Address
) -> int:
    result: int = await conn.fetchval(
        """
        SELECT count(*)::int FROM shipments
         WHERE tenant_id = $1 AND source_ip = $2::inet
           AND booking_ts > now() - interval '1 hour'
        """,
        tenant_id, str(source_ip),
    )
    return result


async def count_ip_daily(
    conn: asyncpg.Connection, tenant_id: int, source_ip: IPv4Address
) -> int:
    result: int = await conn.fetchval(
        """
        SELECT count(*)::int FROM shipments
         WHERE tenant_id = $1 AND source_ip = $2::inet
           AND booking_ts > now() - interval '24 hours'
        """,
        tenant_id, str(source_ip),
    )
    return result
