"""Create `riskd_app_login` role for runtime DB connection.

Phase 5D.1: the legacy `riskd_app` role from 0001_initial.py is NOLOGIN
and exists only as a permissions container. To actually exercise RLS at
runtime, the connecting role must be non-superuser (the postgres
bootstrap superuser bypasses RLS by definition). This migration adds
`riskd_app_login` WITH LOGIN INHERIT and grants `riskd_app` to it, so
all of the RLS policies + table grants tied to `riskd_app` apply
transparently when `riskd_app_login` connects.

After this commit the role EXISTS but no connection uses it yet. 5D.2
switches the runtime `DATABASE_URL` to point at this role.

Local-dev password: hardcoded here for `docker compose up`. PRODUCTION
DEPLOYMENT (Phase 6) MUST rotate this password from AWS Secrets Manager
before exposing the service. The plain-text password in this migration
is acceptable ONLY for the local-dev convenience path; the production
deploy step in Phase 6 either recreates the role with a SecretsManager-
sourced password or runs `ALTER ROLE ... PASSWORD '...'` from the
deploy script. Tracked in `docs/security-audit-rls-phase-5.md` (5D.5).

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
-- LOCAL-DEV PASSWORD ONLY. Phase 6 deploy rotates from AWS Secrets Manager.
-- INHERIT (not NOINHERIT) so the grants on riskd_app propagate.
CREATE ROLE riskd_app_login WITH LOGIN INHERIT PASSWORD 'riskd_app_login_dev';
GRANT riskd_app TO riskd_app_login;

COMMENT ON ROLE riskd_app_login IS
    'Runtime DB connection role (Phase 5D). LOGIN INHERIT; receives all grants of riskd_app via the GRANT below. Local-dev password; production rotates from Secrets Manager.';
"""

DOWNGRADE_SQL = """
REVOKE riskd_app FROM riskd_app_login;
DROP ROLE riskd_app_login;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
