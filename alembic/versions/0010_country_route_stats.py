"""Add customer_baselines.country_route_stats jsonb column for case-3a route-baseline derivation.

Phase 6A.1 — case-3a (established-customer compromise) detection requires a
per-customer histogram of (origin_country, destination_country) route pairs.
The histogram is populated by the baseline updater on each shipment commit
(Phase 6A.2) and consumed by the build_context derivation that sets
`shipment_route_unfamiliar_for_customer` (Phase 6A.2) which feeds the
`case_3_compound` rule (Phase 6A.3).

Default `'{}'::jsonb NOT NULL` so existing rows have a usable empty
histogram immediately; no data backfill needed (Phase 6 has no production
data and the prototype rows simply cold-start empty). The maturity gate
in build_context (`customer_observations >= 10`) ensures the signal does
not fire until the customer has accumulated sufficient legitimate
history.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
ALTER TABLE customer_baselines
    ADD COLUMN country_route_stats jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN customer_baselines.country_route_stats IS
    'Per-customer (origin_country, destination_country) route-pair histogram. '
    'Keys are "{origin_country}||{destination_country}" composite strings; '
    'values are observation counts. Populated by baseline updater on shipment '
    'commit. Consumed by build_context to derive '
    'shipment_route_unfamiliar_for_customer (case-3a signal).';
"""

DOWNGRADE_SQL = """
ALTER TABLE customer_baselines DROP COLUMN country_route_stats;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
