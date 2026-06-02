"""Add api_tokens supporting index on (tenant_id, last_used_at DESC NULLS LAST).

Phase 5A.6: supports future "stale token" queries — `SELECT ... FROM
api_tokens WHERE tenant_id = $1 AND last_used_at < now() - interval 'N days'`
plans through this index. NULLS LAST orders never-used tokens at the tail
of any DESC scan, so the natural "least-recently-used / unused" query
ordering reads cleanly off the index.

The api_tokens.last_used_at column already exists from 0001_initial.py
line 253 (added NULL-default at table creation); the writer in 5A.5
populates it on each successful auth. This commit only adds the
supporting index — no row data changes.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
CREATE INDEX ix_api_tokens_tenant_last_used
    ON api_tokens (tenant_id, last_used_at DESC NULLS LAST);

COMMENT ON INDEX ix_api_tokens_tenant_last_used IS
    'Supports stale-token queries (least-recently-used / unused tokens per tenant). NULLS LAST so never-used tokens sort at the tail of DESC scans.';
"""

DOWNGRADE_SQL = """
DROP INDEX IF EXISTS ix_api_tokens_tenant_last_used;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
