"""Drop RLS on api_tokens + app_users (auth-lookup tables).

Phase 5D.2 — runtime role switches to `riskd_app_login` (non-superuser).
Under RLS, `require_api_token` (app/auth.py) cannot resolve a bearer
token to a tenant: the auth dependency runs `SELECT FROM api_tokens
WHERE token_hash = $1` BEFORE the endpoint handler issues
`set_tenant_id` — there is no tenant to set yet because the tenant_id
is the result of the auth lookup. With `app.tenant_id` defaulting to
'0' (the pool-init sentinel), the RLS policy on api_tokens filters all
rows out. Auth fails.

The auth.py module docstring lines 15-18 anticipated this exact
chicken-and-egg and called it out as a Phase 5 follow-up. The
resolution here is to DROP the RLS policies + `ENABLE ROW LEVEL
SECURITY` on the two auth-lookup tables:

- api_tokens: lookup is keyed by `token_hash` (UNIQUE) and the secret
  is the unhashed token presented by the client. Knowing the token is
  itself the credential; once the SELECT returns a row, the
  `tenant_id` column on that row IS the authorized tenant. RLS on
  api_tokens was vestigial under the pre-5D superuser-bypass model
  and adds no meaningful isolation post-5D — every token row is
  protected by the secret in the token, not by the session tenant
  context.
- app_users: same shape — admin login (Phase 4) uses email + a hashed
  password against `app_users`; the lookup is keyed by `email` (unique
  per-tenant by FK) and the tenant_id is again the result, not the
  scope. v1 admin endpoints don't actively use app_users (admin role
  lives on api_tokens.role per Phase 4D.1); drop is preemptive
  cleanup so a future app_users-driven auth path doesn't hit the
  same chicken-and-egg.

Tables WITH RLS retained: enterprises, customers, users, shipments,
decisions, feedback, customer_baselines. These are all
business-data tables whose visibility IS tenant-scoped via session
context.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
DROP POLICY IF EXISTS tenant_isolation ON api_tokens;
ALTER TABLE api_tokens DISABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON app_users;
ALTER TABLE app_users DISABLE ROW LEVEL SECURITY;
"""

DOWNGRADE_SQL = """
ALTER TABLE app_users ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app_users
    USING (tenant_id = current_setting('app.tenant_id')::int);

ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON api_tokens
    USING (tenant_id = current_setting('app.tenant_id')::int);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
