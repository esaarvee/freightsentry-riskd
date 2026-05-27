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


async def count_user_modifications_1h(
    conn: asyncpg.Connection, tenant_id: int, customer_id: int
) -> int:
    """Count of modification decisions for this customer in the last 1h.

    Filters decisions WHERE request_type='modification' (added in 0003)
    and joins via shipments FK to scope by customer_id. Both legs of
    the join carry an explicit tenant_id = $1 in WHERE (per
    .ai/conventions.md tenant-scoping guidance: defense-in-depth +
    deterministic planner hint under the Phase 5 non-superuser RLS
    transition; the s.tenant_id = d.tenant_id join predicate is
    complementary, not redundant).

    Uses ix_decisions_tenant_request_type_created (0003) — the planner
    seeks into the (tenant, 'modification') slice and range-scans
    created_at at the index leaf. The shipments inner loop probes
    shipments_pkey via decisions.shipment_id (PK lookup; no separate
    ix_decisions_shipment_id needed).
    """
    result: int = await conn.fetchval(
        """
        SELECT count(*)::int
          FROM decisions d
          JOIN shipments s ON s.id = d.shipment_id AND s.tenant_id = d.tenant_id
         WHERE d.tenant_id = $1
           AND s.tenant_id = $1
           AND s.customer_id = $2
           AND d.request_type = 'modification'
           AND d.created_at > now() - interval '1 hour'
        """,
        tenant_id,
        customer_id,
    )
    return result


async def count_user_modifications_24h(
    conn: asyncpg.Connection, tenant_id: int, customer_id: int
) -> int:
    """Count of modification decisions for this customer in the last 24h.

    Same query shape as count_user_modifications_1h with a wider window;
    same explicit-tenant-on-both-legs discipline.
    """
    result: int = await conn.fetchval(
        """
        SELECT count(*)::int
          FROM decisions d
          JOIN shipments s ON s.id = d.shipment_id AND s.tenant_id = d.tenant_id
         WHERE d.tenant_id = $1
           AND s.tenant_id = $1
           AND s.customer_id = $2
           AND d.request_type = 'modification'
           AND d.created_at > now() - interval '24 hours'
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
