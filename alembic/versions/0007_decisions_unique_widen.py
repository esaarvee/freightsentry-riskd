"""Widen decisions UNIQUE to (tenant_id, request_type, request_id).

Phase 5A.7: resolves the .claude/BUGS.md mismatch where the idempotency
SELECTs in booking + modification endpoints scope by request_type but the
DB-level UNIQUE constraint did not include request_type. A booking and
a modification could legitimately share a `request_id` per the public
idempotency contract, but the flat UNIQUE on (tenant_id, request_id)
rejected the second INSERT.

Drops the legacy CONSTRAINT `ux_decisions_tenant_request` (added in
0001_initial.py:141) and replaces it with a UNIQUE INDEX
`ux_decisions_tenant_request_type` on (tenant_id, request_type,
request_id). UniqueViolationError fires identically against both shapes;
the try/except → 409 in booking.py + modification.py remains as
defense-in-depth (catches intra-type duplicate POSTs).

Downgrade caveat: the constraint can only be reinstated if no cross-type
request_id reuse has occurred. After any booking + modification sharing
a request_id legitimately, `alembic downgrade 0006` will fail.
The downgrade is safe to run immediately after upgrade (no data drift
in the gap) but the asymmetry is intentional and documented.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
ALTER TABLE decisions DROP CONSTRAINT ux_decisions_tenant_request;

CREATE UNIQUE INDEX ux_decisions_tenant_request_type
    ON decisions (tenant_id, request_type, request_id);

COMMENT ON INDEX ux_decisions_tenant_request_type IS
    'UNIQUE idempotency key. Replaces 0001 flat (tenant_id, request_id) constraint so booking and modification with the same request_id are valid (Phase 5A.7).';
"""

DOWNGRADE_SQL = """
DROP INDEX IF EXISTS ux_decisions_tenant_request_type;

ALTER TABLE decisions
    ADD CONSTRAINT ux_decisions_tenant_request UNIQUE (tenant_id, request_id);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
