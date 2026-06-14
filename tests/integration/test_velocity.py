"""Integration tests for app/velocity.py — SQL-backed counters."""

from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address

import asyncpg
import pytest

from app.velocity import (
    count_ip_daily,
    count_ip_hourly,
    count_recipient_distinct_customers_30d,
    count_user_30d,
    count_user_daily,
    count_user_distinct_ips_30d,
    count_user_hourly,
    count_user_modifications_1h,
    count_user_modifications_24h,
)
from tests.conftest import create_tenant_with_token, set_test_tenant_id, with_test_tenant_context


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
    destination_hmac: str = "stub-hmac-velocity",
) -> None:
    await conn.execute(
        """
        INSERT INTO shipments (
            id, tenant_id, customer_id, user_id, request_id, source_ip,
            origin, destination, value, channel, booking_ts,
            destination_hmac, transaction_number
        )
        VALUES ($4, $1, $2, $3, $4, $5::inet,
                '{}'::jsonb, '{}'::jsonb, 100, 'web', $6,
                $7, 'tx-' || $4)
        """,
        tenant_id,
        customer_id,
        user_id,
        request_id,
        source_ip,
        booking_ts,
        destination_hmac,
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
    await set_test_tenant_id(db_conn, other_tenant)
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
        # Switch back to seeded_tenant so RLS scope matches the WHERE-filtered query.
        await set_test_tenant_id(db_conn, seeded_tenant)
        count = await count_ip_hourly(db_conn, seeded_tenant, IPv4Address("192.0.2.80"))
        assert count == 1  # only the in-scope one
    finally:
        # Reuse the canonical cleanup helper rather than maintaining a
        # parallel inline DELETE list.
        from tests.conftest import _cleanup_tenant

        await set_test_tenant_id(db_conn, other_tenant)
        await _cleanup_tenant(db_conn, other_tenant)
        await set_test_tenant_id(db_conn, seeded_tenant)


# ---------------------------------------------------------------------------
# Helpers: distinct-IP diversity + recipient cross-customer overlap
# ---------------------------------------------------------------------------


async def test_count_user_distinct_ips_30d_counts_unique_ips(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
    seeded_user: int,
) -> None:
    """3 bookings from 2 distinct IPs → 2. Repeats of the same IP do
    not inflate the count (DISTINCT semantics)."""
    now = datetime.now(tz=UTC)
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "diversity-1",
        "203.0.113.1",
        now - timedelta(days=1),
    )
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "diversity-2",
        "203.0.113.2",
        now - timedelta(days=2),
    )
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "diversity-3",
        "203.0.113.1",
        now - timedelta(days=3),
    )

    count = await count_user_distinct_ips_30d(db_conn, seeded_tenant, seeded_customer)
    assert count == 2


async def test_count_user_distinct_ips_30d_empty(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
) -> None:
    """Customer with no shipments in the 30-day window returns 0."""
    count = await count_user_distinct_ips_30d(db_conn, seeded_tenant, seeded_customer)
    assert count == 0


async def test_count_user_distinct_ips_30d_excludes_window(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
    seeded_user: int,
) -> None:
    """A 60-day-old shipment from another IP does not contribute — the
    30-day window is enforced at SQL."""
    now = datetime.now(tz=UTC)
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "windowed-1",
        "203.0.113.10",
        now - timedelta(days=5),
    )
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "windowed-2",
        "203.0.113.20",
        now - timedelta(days=60),
    )

    count = await count_user_distinct_ips_30d(db_conn, seeded_tenant, seeded_customer)
    assert count == 1


async def test_count_user_distinct_ips_30d_excludes_cross_tenant(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
    seeded_user: int,
) -> None:
    """SECURITY: tenant scoping is the boundary. Seed shipments under
    both tenants from the same IP; the count for seeded_tenant must
    only include seeded_tenant rows. Symmetric to the recipient cross-
    tenant test — both helpers share the same tenant-isolation risk
    class."""
    now = datetime.now(tz=UTC)
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "ip-iso-a",
        "203.0.113.50",
        now - timedelta(days=1),
    )

    async with create_tenant_with_token(db_conn) as (_token_b, tenant_b):
        b_customer: int = await db_conn.fetchval(
            "INSERT INTO customers (tenant_id, external_id) VALUES ($1, $2) RETURNING id",
            tenant_b,
            "ip-iso-other-cust",
        )
        b_user_id: int = await db_conn.fetchval(
            "INSERT INTO users (tenant_id, customer_id, external_id) VALUES ($1, $2, $3) RETURNING id",
            tenant_b,
            b_customer,
            "ip-iso-other-user",
        )
        await _seed_shipment(
            db_conn,
            tenant_b,
            b_customer,
            b_user_id,
            "ip-iso-b",
            "203.0.113.50",
            now - timedelta(days=1),
        )

        # seeded_tenant query must NOT see tenant_b's row, even though
        # both share IP 203.0.113.50.
        async with with_test_tenant_context(db_conn, seeded_tenant):
            count_a = await count_user_distinct_ips_30d(db_conn, seeded_tenant, seeded_customer)
        assert count_a == 1, f"seeded_tenant should see 1 distinct IP, got {count_a}"
    await set_test_tenant_id(db_conn, seeded_tenant)


async def test_count_recipient_distinct_customers_30d_within_tenant(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_user: int,
) -> None:
    """3 different customers in the same tenant shipping to the same
    destination_hmac → 3."""
    now = datetime.now(tz=UTC)
    shared_hmac = "recipient-distinct-within"
    for i in range(3):
        cid: int = await db_conn.fetchval(
            "INSERT INTO customers (tenant_id, external_id) VALUES ($1, $2) RETURNING id",
            seeded_tenant,
            f"recip-cust-{i}",
        )
        await _seed_shipment(
            db_conn,
            seeded_tenant,
            cid,
            seeded_user,
            f"recip-w-{i}",
            f"203.0.113.{100 + i}",
            now - timedelta(days=1),
            destination_hmac=shared_hmac,
        )

    count = await count_recipient_distinct_customers_30d(db_conn, seeded_tenant, shared_hmac)
    assert count == 3


async def test_count_recipient_distinct_customers_30d_excludes_cross_tenant(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_user: int,
) -> None:
    """SECURITY-LOAD-BEARING. 2 customers in tenant_a + 2 customers in
    tenant_b all shipping to the same destination_hmac. Query for
    tenant_a returns 2 (NOT 4)."""
    now = datetime.now(tz=UTC)
    shared_hmac = "recipient-distinct-cross-tenant"

    # Tenant A customers under seeded_tenant + seeded_user.
    for i in range(2):
        cid: int = await db_conn.fetchval(
            "INSERT INTO customers (tenant_id, external_id) VALUES ($1, $2) RETURNING id",
            seeded_tenant,
            f"recip-a-{i}",
        )
        await _seed_shipment(
            db_conn,
            seeded_tenant,
            cid,
            seeded_user,
            f"recip-cx-a-{i}",
            f"203.0.113.{200 + i}",
            now - timedelta(days=1),
            destination_hmac=shared_hmac,
        )

    async with create_tenant_with_token(db_conn) as (_token_b, tenant_b):
        # Tenant B: bootstrap a customer + user (FK requirements for the
        # tenant_b shipments below) — neither contributes to the recipient
        # count since the bootstrap customer doesn't ship anywhere.
        b_bootstrap_customer: int = await db_conn.fetchval(
            "INSERT INTO customers (tenant_id, external_id) VALUES ($1, $2) RETURNING id",
            tenant_b,
            "recip-b-bootstrap-cust",
        )
        b_user_id: int = await db_conn.fetchval(
            "INSERT INTO users (tenant_id, customer_id, external_id) VALUES ($1, $2, $3) RETURNING id",
            tenant_b,
            b_bootstrap_customer,
            "recip-b-bootstrap-user",
        )
        for i in range(2):
            cid: int = await db_conn.fetchval(
                "INSERT INTO customers (tenant_id, external_id) VALUES ($1, $2) RETURNING id",
                tenant_b,
                f"recip-b-{i}",
            )
            await _seed_shipment(
                db_conn,
                tenant_b,
                cid,
                b_user_id,
                f"recip-cx-b-{i}",
                f"203.0.113.{210 + i}",
                now - timedelta(days=1),
                destination_hmac=shared_hmac,
            )

        async with with_test_tenant_context(db_conn, seeded_tenant):
            count_a = await count_recipient_distinct_customers_30d(
                db_conn, seeded_tenant, shared_hmac
            )
        assert count_a == 2, f"tenant_a should see 2 customers, got {count_a}"

        async with with_test_tenant_context(db_conn, tenant_b):
            count_b = await count_recipient_distinct_customers_30d(db_conn, tenant_b, shared_hmac)
        # tenant_b has 2 customers shipping to D plus the 1 bootstrap
        # customer (not shipping anywhere) — count_b counts only the 2
        # who actually shipped to the destination.
        assert count_b == 2, f"tenant_b should see 2 customers, got {count_b}"
    await set_test_tenant_id(db_conn, seeded_tenant)


async def test_count_recipient_distinct_customers_30d_excludes_window(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_user: int,
) -> None:
    """Customers whose only matching shipment is 60+ days old don't
    contribute. The 30-day window cap is the DoS bound (Pattern C3)."""
    now = datetime.now(tz=UTC)
    shared_hmac = "recipient-distinct-stale"
    for i in range(3):
        cid: int = await db_conn.fetchval(
            "INSERT INTO customers (tenant_id, external_id) VALUES ($1, $2) RETURNING id",
            seeded_tenant,
            f"recip-stale-{i}",
        )
        await _seed_shipment(
            db_conn,
            seeded_tenant,
            cid,
            seeded_user,
            f"recip-stale-{i}",
            f"203.0.113.{220 + i}",
            now - timedelta(days=45),
            destination_hmac=shared_hmac,
        )

    count = await count_recipient_distinct_customers_30d(db_conn, seeded_tenant, shared_hmac)
    assert count == 0


# ============================================================================
# Modification velocity — counts decisions, not shipments
# ============================================================================


async def _seed_modification_decision(
    conn: asyncpg.Connection,
    tenant_id: int,
    shipment_id: int,
    request_id: str,
    created_at: datetime,
) -> None:
    """Seed a decisions row with request_type='modification' at a given
    created_at. Forces the created_at via an explicit SET to override
    the now() DEFAULT — modification-velocity tests need control over
    timestamps for window-boundary verification."""
    await conn.execute(
        """
        INSERT INTO decisions (
            tenant_id, shipment_id, request_id, request_type,
            score, decision, classification, risk_level,
            triggered_rules, risk_factors, created_at
        ) VALUES ($1, $2, $3, 'modification',
                  0.5, 'REVIEW', 'YELLOW', 'MEDIUM',
                  '{}'::text[], '[]'::jsonb, $4)
        """,
        tenant_id,
        shipment_id,
        request_id,
        created_at,
    )


@pytest.fixture
async def seeded_shipment_id(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
    seeded_user: int,
) -> int:
    """A single shipment to anchor modification decisions via FK."""
    now = datetime.now(tz=UTC)
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        seeded_customer,
        seeded_user,
        "mod-vel-anchor",
        "203.0.113.100",
        now,
    )
    return await db_conn.fetchval(
        "SELECT id FROM shipments WHERE tenant_id = $1 AND request_id = $2",
        seeded_tenant,
        "mod-vel-anchor",
    )


async def test_modifications_1h_empty_returns_zero(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
) -> None:
    count = await count_user_modifications_1h(db_conn, seeded_tenant, seeded_customer)
    assert count == 0


async def test_modifications_1h_counts_recent_only(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
    seeded_shipment_id: int,
) -> None:
    now = datetime.now(tz=UTC)
    # 3 modifications within the last 30 minutes
    for i in range(3):
        await _seed_modification_decision(
            db_conn,
            seeded_tenant,
            seeded_shipment_id,
            f"mod-recent-{i}",
            now - timedelta(minutes=10 + i),
        )
    # 1 modification 2 hours ago (outside window)
    await _seed_modification_decision(
        db_conn,
        seeded_tenant,
        seeded_shipment_id,
        "mod-stale",
        now - timedelta(hours=2),
    )

    count = await count_user_modifications_1h(db_conn, seeded_tenant, seeded_customer)
    assert count == 3


async def test_modifications_1h_ignores_other_customers(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
    seeded_user: int,
    seeded_shipment_id: int,
) -> None:
    """A modification for a DIFFERENT customer in the same tenant must
    not count toward this customer's velocity."""
    other_customer = await db_conn.fetchval(
        "INSERT INTO customers (tenant_id, external_id) VALUES ($1, 'vel-other-cust') RETURNING id",
        seeded_tenant,
    )
    other_user = await db_conn.fetchval(
        "INSERT INTO users (tenant_id, customer_id, external_id) VALUES ($1, $2, 'vel-other-user') RETURNING id",
        seeded_tenant,
        other_customer,
    )
    now = datetime.now(tz=UTC)
    await _seed_shipment(
        db_conn,
        seeded_tenant,
        other_customer,
        other_user,
        "other-shipment",
        "203.0.113.101",
        now,
    )
    other_shipment_id: int = await db_conn.fetchval(
        "SELECT id FROM shipments WHERE tenant_id = $1 AND request_id = $2",
        seeded_tenant,
        "other-shipment",
    )
    await _seed_modification_decision(
        db_conn,
        seeded_tenant,
        other_shipment_id,
        "mod-other-customer",
        now - timedelta(minutes=10),
    )

    count = await count_user_modifications_1h(db_conn, seeded_tenant, seeded_customer)
    assert count == 0


async def test_modifications_1h_ignores_other_tenants(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
) -> None:
    """A modification for this customer's external_id in a DIFFERENT
    tenant must not count — explicit WHERE tenant_id filter at
    app/velocity.py."""
    async with create_tenant_with_token(db_conn) as (_token_b, tenant_b):
        # Seed a customer in tenant_b with the same external_id
        cust_b: int = await db_conn.fetchval(
            "INSERT INTO customers (tenant_id, external_id) VALUES ($1, 'vel-cust') RETURNING id",
            tenant_b,
        )
        user_b: int = await db_conn.fetchval(
            "INSERT INTO users (tenant_id, customer_id, external_id) VALUES ($1, $2, 'vel-user') RETURNING id",
            tenant_b,
            cust_b,
        )
        now = datetime.now(tz=UTC)
        await _seed_shipment(
            db_conn, tenant_b, cust_b, user_b, "mod-cross-tenant", "203.0.113.102", now
        )
        ship_b: int = await db_conn.fetchval(
            "SELECT id FROM shipments WHERE tenant_id = $1 AND request_id = $2",
            tenant_b,
            "mod-cross-tenant",
        )
        await _seed_modification_decision(
            db_conn,
            tenant_b,
            ship_b,
            "mod-cross-tenant-decision",
            now - timedelta(minutes=10),
        )

        # Querying tenant_a's count must NOT see tenant_b's modification
        async with with_test_tenant_context(db_conn, seeded_tenant):
            count_a = await count_user_modifications_1h(db_conn, seeded_tenant, seeded_customer)
        assert count_a == 0
    await set_test_tenant_id(db_conn, seeded_tenant)


async def test_modifications_24h_wider_window(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
    seeded_shipment_id: int,
) -> None:
    """1h shows none; 24h shows all recent within the wider window."""
    now = datetime.now(tz=UTC)
    # 5 modifications between 2h and 23h ago (outside 1h, inside 24h)
    for i in range(5):
        await _seed_modification_decision(
            db_conn,
            seeded_tenant,
            seeded_shipment_id,
            f"mod-mid-{i}",
            now - timedelta(hours=2 + i * 4),
        )
    # 1 modification 25 hours ago (outside 24h)
    await _seed_modification_decision(
        db_conn,
        seeded_tenant,
        seeded_shipment_id,
        "mod-too-old",
        now - timedelta(hours=25),
    )

    count_1h = await count_user_modifications_1h(db_conn, seeded_tenant, seeded_customer)
    assert count_1h == 0
    count_24h = await count_user_modifications_24h(db_conn, seeded_tenant, seeded_customer)
    assert count_24h == 5


async def test_modifications_ignores_booking_decisions(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    seeded_customer: int,
    seeded_shipment_id: int,
) -> None:
    """request_type='booking' decisions must not count toward modification
    velocity. _seed_shipment inserts only a shipment row (no decision),
    so we insert a booking decision explicitly to verify the
    discriminator filter."""
    now = datetime.now(tz=UTC)
    await db_conn.execute(
        """
        INSERT INTO decisions (
            tenant_id, shipment_id, request_id, request_type,
            score, decision, classification, risk_level,
            triggered_rules, risk_factors, created_at
        ) VALUES ($1, $2, $3, 'booking',
                  0.5, 'REVIEW', 'YELLOW', 'MEDIUM',
                  '{}'::text[], '[]'::jsonb, $4)
        """,
        seeded_tenant,
        seeded_shipment_id,
        "booking-not-modification",
        now - timedelta(minutes=5),
    )

    count = await count_user_modifications_1h(db_conn, seeded_tenant, seeded_customer)
    assert count == 0
