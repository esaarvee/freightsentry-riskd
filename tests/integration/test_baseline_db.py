"""Integration tests for app/baseline.py — DB round-trip + SELECT FOR UPDATE.

Concurrency test verifies that two simultaneous baseline updates for the
same (tenant_id, customer_id) do not produce a lost update.
"""

import asyncio
from datetime import UTC, date, datetime

import asyncpg
import pytest

from app.baseline import IP_TYPE_CLOUD, CustomerBaseline


def _at(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def seeded_customer(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> int:
    return await db_conn.fetchval(
        """
        INSERT INTO customers (tenant_id, external_id)
        VALUES ($1, $2)
        RETURNING id
        """,
        seeded_tenant,
        "baseline-test-cust",
    )


async def test_load_returns_empty_when_no_row(
    db_conn: asyncpg.Connection, seeded_tenant: int, seeded_customer: int
) -> None:
    bl = await CustomerBaseline.load(db_conn, seeded_tenant, seeded_customer)
    assert bl.value_n == 0.0
    assert bl.decay_anchor_date is None
    assert bl.id is None


async def test_save_then_load_round_trip(
    db_conn: asyncpg.Connection, seeded_tenant: int, seeded_customer: int
) -> None:
    bl = CustomerBaseline.empty(seeded_tenant, seeded_customer)
    bl.add_observation(
        ts=_at(2026, 5, 26),
        ip="192.0.2.1",
        ip_type=IP_TYPE_CLOUD,
        ip_netblock="192.0.2.0",
        ip_asn="AS-Test",
        ip_country="CA",
        ip_lat=43.0,
        ip_lon=-79.0,
        origin="123 Main",
        destination="456 Oak",
        channel="web",
        value=100.50,
    )
    bl.decay_anchor_date = date(2026, 5, 26)
    await bl.save(db_conn)

    loaded = await CustomerBaseline.load(db_conn, seeded_tenant, seeded_customer)
    assert loaded.id is not None
    assert loaded.value_n == pytest.approx(1.0)
    assert loaded.value_mean == pytest.approx(100.50)
    assert loaded.ip_stats["192.0.2.1"]["n"] == pytest.approx(1.0)
    assert loaded.ip_stats["192.0.2.1"]["type"] == IP_TYPE_CLOUD
    assert loaded.lane_stats["123 Main||456 Oak"]["n"] == pytest.approx(1.0)
    assert loaded.hour_hist["12"] == pytest.approx(1.0)
    assert loaded.decay_anchor_date == date(2026, 5, 26)


async def test_save_upserts_on_repeat(
    db_conn: asyncpg.Connection, seeded_tenant: int, seeded_customer: int
) -> None:
    bl = CustomerBaseline.empty(seeded_tenant, seeded_customer)
    bl.value_n = 5.0
    bl.decay_anchor_date = date(2026, 5, 26)
    await bl.save(db_conn)

    bl2 = await CustomerBaseline.load(db_conn, seeded_tenant, seeded_customer)
    bl2.value_n += 1.0
    await bl2.save(db_conn)

    loaded = await CustomerBaseline.load(db_conn, seeded_tenant, seeded_customer)
    assert loaded.value_n == pytest.approx(6.0)


async def test_first_write_concurrency_no_lost_update_via_unique_constraint(
    _pool: asyncpg.Pool, seeded_tenant: int, seeded_customer: int
) -> None:
    """Two concurrent transactions both load an EMPTY baseline (no row
    exists yet), increment value_n, and save. The UNIQUE(tenant_id,
    customer_id) constraint serializes the first-write race: one
    transaction's INSERT succeeds, the other's hits ON CONFLICT and
    becomes an UPDATE. Final value_n must be 2.0 (no lost update).

    Complements test_select_for_update_blocks_concurrent_writers, which
    exercises the lock-on-existing-row path. This test covers the
    no-row-to-lock-yet path."""

    async def _increment_from_empty() -> None:
        async with _pool.acquire() as conn, conn.transaction():
            bl = await CustomerBaseline.load(
                conn, seeded_tenant, seeded_customer, for_update=True
            )
            # `load(for_update=True)` reserve-inserts an empty row if none
            # existed, so `bl.id` is always populated. The first-write
            # racing TX still sees `value_n == 0`; the second sees the
            # post-first-write value_n == 1 (lock-serialised).
            bl.value_n += 1.0
            bl.decay_anchor_date = date(2026, 5, 26)
            await asyncio.sleep(0.05)  # maximise overlap
            await bl.save(conn)

    await asyncio.gather(_increment_from_empty(), _increment_from_empty())

    async with _pool.acquire() as conn:
        final = await CustomerBaseline.load(conn, seeded_tenant, seeded_customer)
    assert final.value_n == pytest.approx(2.0), (
        "Lost-update on first-write path — UNIQUE constraint should have "
        "serialised the INSERTs into one INSERT + one ON CONFLICT UPDATE"
    )


async def test_select_for_update_blocks_concurrent_writers(
    _pool: asyncpg.Pool, seeded_tenant: int, seeded_customer: int
) -> None:
    """Two concurrent transactions both call `load(for_update=True)` then
    increment value_n by 1 and save. With FOR UPDATE the second
    transaction blocks until the first commits, so the final value_n is
    2 — not 1 (which would be a lost-update bug)."""
    # Seed an initial row so FOR UPDATE has something to lock.
    async with _pool.acquire() as setup_conn:
        bl0 = CustomerBaseline.empty(seeded_tenant, seeded_customer)
        bl0.value_n = 0.0
        bl0.decay_anchor_date = date(2026, 5, 26)
        await bl0.save(setup_conn)

    async def _increment_with_lock() -> None:
        async with _pool.acquire() as conn, conn.transaction():
            bl = await CustomerBaseline.load(
                conn, seeded_tenant, seeded_customer, for_update=True
            )
            bl.value_n += 1.0
            # Small await to maximise overlap with the sibling task.
            await asyncio.sleep(0.05)
            await bl.save(conn)

    await asyncio.gather(_increment_with_lock(), _increment_with_lock())

    async with _pool.acquire() as conn:
        final = await CustomerBaseline.load(conn, seeded_tenant, seeded_customer)
    assert final.value_n == pytest.approx(2.0), (
        "Lost-update detected — FOR UPDATE did not serialise writers"
    )
