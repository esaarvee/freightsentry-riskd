"""decisions.request_type discriminator (booking | modification)

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-27

Phase 3A: adds a `request_type` discriminator to decisions so the single
table serves both booking and modification evaluations. Existing rows
backfill to 'booking'; the DEFAULT is retained as a safety net so the
booking endpoint INSERT continues to succeed unchanged between this
migration and the 3A.6 endpoint patch (which makes the discriminator
explicit at the call site).

Phase 1 / 2 has no production rows; the migration assumes a small
dev/staging dataset where backfill is trivial.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
ALTER TABLE decisions
    ADD COLUMN request_type text NOT NULL DEFAULT 'booking';

COMMENT ON COLUMN decisions.request_type IS
    'One of booking | modification; discriminates which evaluate endpoint produced this decision. DEFAULT booking preserved as safety net — endpoints supply request_type explicitly in 3A.6.';

-- Defensive no-op: ADD COLUMN ... NOT NULL DEFAULT already backfilled
-- existing rows during the ADD COLUMN step above; this UPDATE matches
-- zero rows by construction. Kept for audit clarity.
UPDATE decisions SET request_type = 'booking' WHERE request_type IS NULL;

ALTER TABLE decisions ADD CONSTRAINT ck_decisions_request_type
    CHECK (request_type IN ('booking', 'modification'));

-- (tenant_id, request_type, created_at) lets the 3A.5 modification-
-- velocity SQL seek into the (tenant, 'modification') slice and range-
-- scan the recency cutoff at the index leaf — matches the project
-- pattern used by ix_shipments_tenant_customer_booking_ts and
-- ix_shipments_tenant_ip_booking_ts in 0001_initial.py.
CREATE INDEX ix_decisions_tenant_request_type_created
    ON decisions (tenant_id, request_type, created_at);
"""

DOWNGRADE_SQL = """
DROP INDEX IF EXISTS ix_decisions_tenant_request_type_created;
ALTER TABLE decisions DROP CONSTRAINT IF EXISTS ck_decisions_request_type;
ALTER TABLE decisions DROP COLUMN IF EXISTS request_type;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
