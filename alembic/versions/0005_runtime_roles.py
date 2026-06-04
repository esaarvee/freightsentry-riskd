"""runtime roles: riskd_app_login WITH LOGIN INHERIT + GRANT chain

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-05

Phase 8A squash. Final migration in the squashed chain. Folds in:

  - 0008_riskd_app_login.py — CREATE ROLE riskd_app_login WITH LOGIN
    INHERIT + GRANT riskd_app TO riskd_app_login (Phase 5D.1).

The original ``0009_drop_rls_on_auth_tables.py`` is NOT folded in here
because new ``0001_foundation.py`` never creates RLS on
``api_tokens`` or ``app_users`` in the first place. Final-state
schema is byte-equivalent to the pre-squash chain (see new
``0001_foundation.py`` module docstring for the full historical
context on the auth-table RLS chicken-and-egg).

Local-dev password (``riskd_app_login_dev``) is acceptable ONLY for
the local docker-compose path. PRODUCTION DEPLOYMENT MUST rotate
this password from AWS Secrets Manager before exposing the service.
The deploy step either recreates the role with a SecretsManager-
sourced password or runs ``ALTER ROLE ... PASSWORD '...'`` from the
deploy script. Tracked in ``docs/security-audit-rls-phase-5.md``.

Idempotent guard on role creation — a ``DO $$ ... duplicate_object``
block lets re-runs against an already-populated cluster succeed.
This matters for the local-dev path where ``docker compose up`` may
rerun ``alembic upgrade head`` against a volume that already has
roles.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
-- ===========================================================================
-- Runtime DB connection role. LOGIN INHERIT so the grants on riskd_app
-- propagate transparently. Idempotent guard for local-dev re-runs.
-- LOCAL-DEV PASSWORD ONLY — see module docstring for production rotation
-- requirements.
-- ===========================================================================
DO $$ BEGIN
    CREATE ROLE riskd_app_login WITH LOGIN INHERIT PASSWORD 'riskd_app_login_dev';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

GRANT riskd_app TO riskd_app_login;

COMMENT ON ROLE riskd_app_login IS
    'Runtime DB connection role (Phase 5D). LOGIN INHERIT; receives all grants of riskd_app via the GRANT below. Local-dev password; production rotates from Secrets Manager.';
"""


DOWNGRADE_SQL = """
REVOKE riskd_app FROM riskd_app_login;
DROP ROLE IF EXISTS riskd_app_login;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
