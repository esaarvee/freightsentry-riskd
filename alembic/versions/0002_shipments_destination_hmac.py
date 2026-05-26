"""shipments.destination_hmac column + (tenant_id, destination_hmac, booking_ts) index

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-26

Phase 2B.6: lands the destination_hmac column the recipient-overlap SQL
(count_recipient_distinct_customers_30d) requires. Index is the latency-
budget guarantee for the COUNT(DISTINCT customer_id) query on the request
hot path. Booking endpoint write of the HMAC value lands in the same
commit so no transitional state exists where the column is NOT NULL but
the writer is missing.

Phase 1 has no production rows; the migration assumes the shipments table
is empty when this runs. If non-empty (defensively), the NOT NULL alter
fails loud and the operator triages — no silent partial backfill.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
ALTER TABLE shipments ADD COLUMN destination_hmac text;
ALTER TABLE shipments ALTER COLUMN destination_hmac SET NOT NULL;
-- Plain CREATE INDEX is safe here only because Phase 1 has no production
-- rows (the table is empty when this migration runs). Future migrations
-- against a populated shipments table must use CREATE INDEX CONCURRENTLY
-- outside a transaction to avoid blocking writes.
CREATE INDEX ix_shipments_tenant_dest_hmac_booking_ts
    ON shipments (tenant_id, destination_hmac, booking_ts);
"""

DOWNGRADE_SQL = """
DROP INDEX IF EXISTS ix_shipments_tenant_dest_hmac_booking_ts;
ALTER TABLE shipments DROP COLUMN IF EXISTS destination_hmac;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
