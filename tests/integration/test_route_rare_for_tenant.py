"""Unit tests for the derive_route_rarity helper.

Pure-DB exercise against the project's runtime role (riskd_app_login)
using the seeded_tenant fixture. Covers:
- empty baseline returns False (no observation data)
- sparse baseline (<100 observations) returns False (cold-start)
- mature baseline + rare triple returns True
- mature baseline + common triple returns False
- mature baseline + triple absent from histogram returns True (0% < 2%)
- boundary at exactly 2% rarity returns False (strict-less-than predicate)
- boundary just below 100 observations returns False (cold-start gate)
- boundary at exactly 100 observations returns True for a rare triple
  (pair with the just-below case pins the strict-less-than gate semantics
  and would catch a `<` → `<=` regression on the cold-start gate)
- None on any country returns False (no signal without data)
"""

from __future__ import annotations

import asyncpg

from app.tenant_route_baselines import (
    RARITY_MIN_OBSERVATIONS,
    RARITY_THRESHOLD,
    derive_route_rarity,
)


async def _seed_baseline(
    conn: asyncpg.Connection,
    tenant_id: int,
    triples: dict[tuple[str, str, str], int],
) -> None:
    """Insert (customer_country, origin_country, destination_country) triples
    with explicit counts. Uses raw INSERT (not the UPSERT helper) so the
    tests are independent of the writer's behavior."""
    for (cust, orig, dest), count in triples.items():
        await conn.execute(
            """
            INSERT INTO tenant_route_baselines (
                tenant_id, customer_country, origin_country, destination_country,
                observation_count, last_updated
            )
            VALUES ($1, $2, $3, $4, $5, now())
            """,
            tenant_id,
            cust,
            orig,
            dest,
            count,
        )


async def test_empty_baseline_returns_false(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    assert await derive_route_rarity(db_conn, seeded_tenant, "CA", "CA", "US") is False


async def test_sparse_baseline_below_minimum_returns_false(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """Total observations = 50 < RARITY_MIN_OBSERVATIONS — even a rare
    triple does not fire because the population estimate is too noisy."""
    await _seed_baseline(db_conn, seeded_tenant, {("CA", "CA", "CA"): 50})
    assert await derive_route_rarity(db_conn, seeded_tenant, "CA", "CA", "US") is False


async def test_mature_baseline_rare_triple_returns_true(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """200 total observations, current triple at 1/200 = 0.5% (< 2%)."""
    await _seed_baseline(
        db_conn,
        seeded_tenant,
        {("CA", "CA", "CA"): 199, ("CA", "CA", "US"): 1},
    )
    assert await derive_route_rarity(db_conn, seeded_tenant, "CA", "CA", "US") is True


async def test_mature_baseline_common_triple_returns_false(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """Current triple at 199/200 = 99.5% (>> 2%)."""
    await _seed_baseline(
        db_conn,
        seeded_tenant,
        {("CA", "CA", "CA"): 199, ("CA", "CA", "US"): 1},
    )
    assert await derive_route_rarity(db_conn, seeded_tenant, "CA", "CA", "CA") is False


async def test_mature_baseline_absent_triple_returns_true(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """A triple not in the histogram has triple_count=0 → 0/total = 0% < 2%."""
    await _seed_baseline(db_conn, seeded_tenant, {("CA", "CA", "CA"): 200})
    assert await derive_route_rarity(db_conn, seeded_tenant, "CA", "CA", "GB") is True


async def test_boundary_just_below_minimum_observations_returns_false(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """RARITY_MIN_OBSERVATIONS - 1 (99) observations: total_count < 100 → False
    even for a triple that would otherwise be rare. The cold-start gate
    short-circuits before any rarity computation."""
    await _seed_baseline(
        db_conn,
        seeded_tenant,
        {("CA", "CA", "CA"): RARITY_MIN_OBSERVATIONS - 1},
    )
    assert await derive_route_rarity(db_conn, seeded_tenant, "CA", "CA", "US") is False


async def test_boundary_at_exactly_minimum_observations_returns_true_when_rare(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """At exactly RARITY_MIN_OBSERVATIONS (100), the cold-start gate is
    `total_count < 100` which is False at equality — so the gate PASSES
    and rarity is computed normally. Pairs with the just-below case to
    pin the strict-less-than semantics of the gate.

    A regression from `total_count < RARITY_MIN_OBSERVATIONS` to
    `total_count <= RARITY_MIN_OBSERVATIONS` would flip this test
    from True to False, catching the off-by-one.
    """
    # 100 total observations split 99 + 1 so the current ("CA", "US", "US")
    # triple is rare (1/100 = 1% < 2%).
    await _seed_baseline(
        db_conn,
        seeded_tenant,
        {("CA", "CA", "CA"): 99, ("CA", "US", "US"): 1},
    )
    assert await derive_route_rarity(db_conn, seeded_tenant, "CA", "US", "US") is True


async def test_boundary_at_rarity_threshold_strict_less_than(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """Triple share = exactly RARITY_THRESHOLD (2%) — the predicate is
    `share < threshold` so the boundary case returns False."""
    # 100 total observations, current triple at 2 (= 2.0%).
    # 2/100 = 0.02 is NOT < 0.02 → False.
    await _seed_baseline(
        db_conn,
        seeded_tenant,
        {("CA", "CA", "CA"): 98, ("CA", "CA", "US"): 2},
    )
    # Sanity-check the assumption inline so the test fails loudly if
    # RARITY_THRESHOLD is ever changed.
    assert RARITY_THRESHOLD == 0.02
    assert await derive_route_rarity(db_conn, seeded_tenant, "CA", "CA", "US") is False


async def test_none_customer_country_returns_false(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    await _seed_baseline(db_conn, seeded_tenant, {("CA", "CA", "US"): 200})
    assert await derive_route_rarity(db_conn, seeded_tenant, None, "CA", "US") is False


async def test_none_origin_country_returns_false(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    await _seed_baseline(db_conn, seeded_tenant, {("CA", "CA", "US"): 200})
    assert await derive_route_rarity(db_conn, seeded_tenant, "CA", None, "US") is False


async def test_none_destination_country_returns_false(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    await _seed_baseline(db_conn, seeded_tenant, {("CA", "CA", "US"): 200})
    assert await derive_route_rarity(db_conn, seeded_tenant, "CA", "US", None) is False
