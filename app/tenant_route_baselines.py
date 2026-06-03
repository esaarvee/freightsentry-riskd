"""Phase 6A.7 / 6A.8 — tenant route population baseline writer + reader.

Per-booking writer for `tenant_route_baselines` (6A.7):
- update_tenant_route_baseline UPSERTs the
  (customer_country, origin_country, destination_country) triple
  count for the booking's tenant.

Per-evaluation reader (6A.8):
- derive_route_rarity returns True iff the current triple is rare
  (<2%) in the tenant's population AND the tenant has accumulated
  >=100 observations. Drives the shipment_route_rare_for_tenant
  Context field used by the case-3b cold_start_population_baseline_
  rare_with_carrier_dropoff rule (6A.9).

Both functions:
- No-op / False on any None country (no signal without ground truth).
- Require `app.tenant_id` set on the connection (RLS policy enforces).
- Bound by the composite PK (writer) or PK leading-column prefix scan
  (reader); both O(1) at the database planner level.

GRANTs on the table to `riskd_app` are in migration 0011.
"""

from __future__ import annotations

from typing import Final

import asyncpg

# Phase 6A.8 — population baseline rarity thresholds. Initial values
# documented in .ai/decisions.md for post-launch tuning (the
# calibration backlog explicitly carries these).
#
# A triple firing as "rare" requires the tenant baseline to have
# accumulated at least this many observations across all triples.
# Below the minimum, the population estimate is too noisy to be
# useful — return False so the cold-start population-baseline rule
# doesn't fire on brand-new tenants.
RARITY_MIN_OBSERVATIONS: Final = 100

# Rarity threshold: a triple is "rare" iff its share of the total
# is strictly less than this fraction. Strictly less so that a
# triple at exactly 2% is NOT rare (the boundary is the most-common
# "real but rare" route — flagging it would over-fire).
RARITY_THRESHOLD: Final = 0.02


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


async def derive_route_rarity(
    conn: asyncpg.Connection,
    tenant_id: int,
    customer_country: str | None,
    origin_country: str | None,
    destination_country: str | None,
) -> bool:
    """Return True iff this (customer_country, origin_country,
    destination_country) triple is rare (<2% of total) for the tenant,
    AND the tenant has accumulated >=100 observations across all triples.

    Returns False when:
    - Any country argument is None (no signal without ground truth).
    - The tenant's total observation count is below RARITY_MIN_OBSERVATIONS
      (population estimate too noisy to be useful — cold-start tenants
      naturally fall here).
    - The triple's share of the total is >= RARITY_THRESHOLD.

    Single round-trip CTE: subquery 1 looks up the triple count via
    composite PK (O(1) index probe); subquery 2 aggregates the tenant-
    wide SUM via the PK's leading-column prefix scan. Both are O(rows
    for this tenant) — Phase 5 load test confirmed ~1ms p95 budget.

    Pre-conditions:
    - `app.tenant_id` is set on `conn` (RLS policy denies otherwise).
    """
    if not (customer_country and origin_country and destination_country):
        return False
    row = await conn.fetchrow(
        """
        WITH triple AS (
            SELECT observation_count AS triple_count
              FROM tenant_route_baselines
             WHERE tenant_id = $1
               AND customer_country = $2
               AND origin_country = $3
               AND destination_country = $4
        ),
        total AS (
            SELECT COALESCE(SUM(observation_count), 0) AS total_count
              FROM tenant_route_baselines
             WHERE tenant_id = $1
        )
        SELECT
            COALESCE((SELECT triple_count FROM triple), 0)::bigint AS triple_count,
            (SELECT total_count FROM total)::bigint AS total_count
        """,
        tenant_id,
        customer_country,
        origin_country,
        destination_country,
    )
    if row is None:
        return False
    total_count = int(row["total_count"])
    if total_count < RARITY_MIN_OBSERVATIONS:
        return False
    triple_count = int(row["triple_count"])
    return (triple_count / total_count) < RARITY_THRESHOLD
