"""runtime roles: riskd_app_login WITH LOGIN INHERIT + GRANT chain

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-05

No RLS DDL is issued on auth tables anywhere in this chain — see
``0001_foundation.py`` module docstring for the auth-table RLS
chicken-and-egg rationale.

Password sourcing
-----------------
The ``riskd_app_login`` password is read at migration time from the
``DATABASE_URL`` env var the deploy migrate task already mounts (the
runtime app's connection DSN, secret-managed). Single source of truth:
the same secret value the app uses to connect is the value the role
is provisioned with — no separate ALTER ROLE rotation step is needed
after deploy.

For local docker-compose, ``DATABASE_URL`` embeds the dev password
``riskd_app_login_dev`` (see ``docker-compose.yml``); the fallback in
``_app_login_password`` returns that same literal when ``DATABASE_URL``
is unset (e.g. running migrations from a shell with only
``ALEMBIC_DATABASE_URL`` set), keeping the local round-trip stable.

The role-creation block uses ``EXCEPTION WHEN duplicate_object`` so a
re-run against an existing cluster rotates the password via ``ALTER
ROLE`` instead of failing — closes the drift loop where the role was
provisioned once and never resynced when the secret rotated.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from urllib.parse import unquote, urlsplit

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_login_password() -> str:
    # Parse the password component from the runtime DSN. urlsplit handles
    # the scheme + userinfo split; unquote reverses any percent-encoding the
    # DSN producer applied to URL-special characters in the password.
    # Local-dev fallback when DATABASE_URL is unset (operator-run alembic
    # with only ALEMBIC_DATABASE_URL set) keeps the docker-compose path
    # unchanged. NOTE: this fallback is a foot-gun if an operator runs
    # `alembic upgrade head` against a production cluster without
    # DATABASE_URL set — the prod role would be rotated to the dev
    # literal. The gated deploy path injects DATABASE_URL via
    # the migrate task definition's secrets block, so the production
    # pipeline cannot hit this branch. Operators running migrations
    # manually outside that pipeline must set DATABASE_URL explicitly.
    pw = unquote(urlsplit(os.environ.get("DATABASE_URL", "")).password or "")
    return pw or "riskd_app_login_dev"


def _sql_quote(s: str) -> str:
    # Postgres single-quoted literal: double any embedded single quotes.
    # Correct under `standard_conforming_strings = on` (Postgres 9.1+ default
    # and the RDS default). The DO block below issues SET LOCAL
    # standard_conforming_strings = on as a defensive belt-and-braces so this
    # remains safe even on a session that was misconfigured upstream.
    return s.replace("'", "''")


def upgrade() -> None:
    pw_sql = _sql_quote(_app_login_password())
    # Uniquely-tagged dollar quote ($mig$ ... $mig$) for the DO body so a
    # password containing the bare `$$` token cannot prematurely close the
    # block. Postgres parses $TAG$ ... $TAG$ as a dollar-quoted string; a
    # collision with $mig$ inside a password is implausible.
    sql = f"""
-- ===========================================================================
-- Runtime DB connection role. LOGIN INHERIT so the grants on riskd_app
-- propagate transparently.
-- Password is sourced at migration time from DATABASE_URL (see module
-- docstring). On re-run the EXCEPTION branch ALTERs the password so a
-- rotated secret takes effect without a manual ALTER ROLE step.
-- ===========================================================================
DO $mig$
BEGIN
    SET LOCAL standard_conforming_strings = on;
    CREATE ROLE riskd_app_login WITH LOGIN INHERIT PASSWORD '{pw_sql}';
EXCEPTION WHEN duplicate_object THEN
    ALTER ROLE riskd_app_login WITH LOGIN INHERIT PASSWORD '{pw_sql}';
END
$mig$;

GRANT riskd_app TO riskd_app_login;

COMMENT ON ROLE riskd_app_login IS
    'Runtime DB connection role. LOGIN INHERIT; receives all grants of riskd_app via the GRANT below. Password sourced from DATABASE_URL at migration time.';
"""
    try:
        op.execute(sql)
    except Exception:
        # psycopg surfaces the offending SQL in the exception body, which
        # would leak the embedded password into stdout / CloudWatch / log
        # shippers on failure. Re-raise with a redacted message and
        # `from None` to break the __cause__ chain so the original SQL
        # never reaches the traceback consumer.
        raise RuntimeError(
            "migration 0005 (runtime_roles) failed; SQL omitted to avoid leaking the riskd_app_login password into logs"
        ) from None


DOWNGRADE_SQL = """
REVOKE riskd_app FROM riskd_app_login;
DROP ROLE IF EXISTS riskd_app_login;
"""


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
