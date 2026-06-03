"""Unit tests for Phase 6A.7 tenant_route_baselines update path.

Pure-DB exercise of update_tenant_route_baseline against the project's
runtime role (riskd_app_login). Covers:
- single-row UPSERT inserts on first call
- repeated calls bump observation_count
- None on any country is a no-op (no INSERT, no exception)
- distinct triples insert as distinct rows

Uses the seeded_tenant fixture for tenant lifecycle + app.tenant_id +
cleanup (which now includes tenant_route_baselines per Phase 6A.6
cleanup-list addition).
"""

from __future__ import annotations

import asyncpg

from app.tenant_route_baselines import update_tenant_route_baseline


async def test_insert_creates_row_with_count_one(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    await update_tenant_route_baseline(db_conn, seeded_tenant, "CA", "CA", "US")
    row = await db_conn.fetchrow(
        """
        SELECT observation_count FROM tenant_route_baselines
        WHERE tenant_id = $1 AND customer_country = $2
          AND origin_country = $3 AND destination_country = $4
        """,
        seeded_tenant,
        "CA",
        "CA",
        "US",
    )
    assert row is not None
    assert row["observation_count"] == 1


async def test_repeated_calls_bump_count(db_conn: asyncpg.Connection, seeded_tenant: int) -> None:
    for _ in range(3):
        await update_tenant_route_baseline(db_conn, seeded_tenant, "CA", "US", "US")
    count = await db_conn.fetchval(
        """
        SELECT observation_count FROM tenant_route_baselines
        WHERE tenant_id = $1 AND customer_country = $2
          AND origin_country = $3 AND destination_country = $4
        """,
        seeded_tenant,
        "CA",
        "US",
        "US",
    )
    assert count == 3


async def test_none_customer_country_is_noop(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    await update_tenant_route_baseline(db_conn, seeded_tenant, None, "CA", "US")
    count = await db_conn.fetchval(
        "SELECT COUNT(*) FROM tenant_route_baselines WHERE tenant_id = $1",
        seeded_tenant,
    )
    assert count == 0


async def test_none_origin_country_is_noop(db_conn: asyncpg.Connection, seeded_tenant: int) -> None:
    await update_tenant_route_baseline(db_conn, seeded_tenant, "CA", None, "US")
    count = await db_conn.fetchval(
        "SELECT COUNT(*) FROM tenant_route_baselines WHERE tenant_id = $1",
        seeded_tenant,
    )
    assert count == 0


async def test_none_destination_country_is_noop(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    await update_tenant_route_baseline(db_conn, seeded_tenant, "CA", "US", None)
    count = await db_conn.fetchval(
        "SELECT COUNT(*) FROM tenant_route_baselines WHERE tenant_id = $1",
        seeded_tenant,
    )
    assert count == 0


async def test_distinct_triples_get_distinct_rows(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    await update_tenant_route_baseline(db_conn, seeded_tenant, "CA", "CA", "US")
    await update_tenant_route_baseline(db_conn, seeded_tenant, "CA", "US", "CA")
    rows = await db_conn.fetch(
        """
        SELECT customer_country, origin_country, destination_country, observation_count
        FROM tenant_route_baselines
        WHERE tenant_id = $1
        ORDER BY origin_country
        """,
        seeded_tenant,
    )
    assert len(rows) == 2
    triple_a = (
        rows[0]["customer_country"],
        rows[0]["origin_country"],
        rows[0]["destination_country"],
    )
    triple_b = (
        rows[1]["customer_country"],
        rows[1]["origin_country"],
        rows[1]["destination_country"],
    )
    assert triple_a == ("CA", "CA", "US")
    assert triple_b == ("CA", "US", "CA")
    assert rows[0]["observation_count"] == 1
    assert rows[1]["observation_count"] == 1
