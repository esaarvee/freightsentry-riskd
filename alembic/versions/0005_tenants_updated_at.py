"""Add tenants.updated_at column for TenantConfig staleness tracking.

Phase 4A: load_tenant_config returns TenantConfig.updated_at sourced
from this column. Default `now()` for existing tenants; future writes
(scripts/tenant_onboard.py in 4A.5; admin write endpoints post-v1)
SHOULD update the column on every config change.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
ALTER TABLE tenants
    ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

COMMENT ON COLUMN tenants.updated_at IS
    'Last time the tenant row (including config JSONB) was modified. Populated by load_tenant_config (Phase 4A) and updated by scripts/tenant_onboard.py.';
"""

DOWNGRADE_SQL = """
ALTER TABLE tenants DROP COLUMN IF EXISTS updated_at;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
