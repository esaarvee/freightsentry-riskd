"""foundation: tenants, enterprises, customers, users, app_users, api_tokens + riskd_app role + RLS

Revision ID: 0001
Revises:
Create Date: 2026-06-05

The ``customers.registered_country`` column lives here as part of the
foundation grouping; the ``tenant_route_baselines`` table belongs to the
baselines grouping and lives in ``0003_baselines.py`` instead.

Auth-table RLS. ``api_tokens`` and ``app_users`` intentionally have no
RLS. The auth dependency in ``app/auth.py`` runs
``SELECT FROM api_tokens WHERE token_hash = $1`` BEFORE the endpoint
handler issues ``set_tenant_id`` — there is no tenant to set yet
because the tenant_id IS the result of the auth lookup. An RLS policy
keyed on ``app.tenant_id`` (default sentinel ``'0'``) would filter all
rows out and break auth. Each token's secret is itself the credential
(UNIQUE ``token_hash``), so table-level RLS would be vestigial anyway.

No migration in this chain issues RLS DDL against ``api_tokens`` or
``app_users``. Final-state schema is byte-equivalent under the canonical
normalizer (verified via ``tests/integration/test_schema_golden.py``).
Cross-reference ``docs/security-audit-rls-phase-5.md`` for the full
architectural reasoning.

Idempotent guards on role creation: a ``DO $$ ... duplicate_object``
block lets re-runs against an already-populated cluster succeed. This
matters for the local-dev path where ``docker compose up`` may rerun
``alembic upgrade head`` against a volume that already has roles
(e.g., after a partial recreate).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
-- ===========================================================================
-- App role. NOLOGIN — permissions container. The LOGIN companion
-- ``riskd_app_login`` is created in migration 0005. Idempotent
-- guard for local-dev re-runs against existing volumes.
-- ===========================================================================
DO $$ BEGIN
    CREATE ROLE riskd_app NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ===========================================================================
-- Tenants — the partitioning dimension. No RLS (tenants are not scoped to
-- themselves).
-- ===========================================================================
CREATE TABLE tenants (
    id         serial PRIMARY KEY,
    name       text NOT NULL,
    config     jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_seen timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON COLUMN tenants.updated_at IS
    'Last time the tenant row (including config JSONB) was modified. Populated by load_tenant_config and updated by scripts/tenant_onboard.py.';

-- ===========================================================================
-- Enterprises — optional corporate-account grouping within a tenant.
-- ===========================================================================
CREATE TABLE enterprises (
    id          serial PRIMARY KEY,
    tenant_id   int NOT NULL REFERENCES tenants(id),
    external_id text NOT NULL,
    first_seen  timestamptz NOT NULL DEFAULT now(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_enterprises_tenant_external UNIQUE (tenant_id, external_id)
);
CREATE INDEX ix_enterprises_tenant_id ON enterprises (tenant_id);

-- ===========================================================================
-- Customers — primary fraud-evaluation entity. ``registered_country`` is
-- ordered last so the dump is byte-equivalent under the canonical normalizer.
-- ===========================================================================
CREATE TABLE customers (
    id                    serial PRIMARY KEY,
    tenant_id             int NOT NULL REFERENCES tenants(id),
    enterprise_id         int REFERENCES enterprises(id),
    external_id           text NOT NULL,
    registered_address    text,
    business_name         text,
    is_api_partner        boolean NOT NULL DEFAULT false,
    first_seen            timestamptz NOT NULL DEFAULT now(),
    last_seen             timestamptz NOT NULL DEFAULT now(),
    flagged_count         int NOT NULL DEFAULT 0,
    fraud_confirmed_count int NOT NULL DEFAULT 0,
    total_shipments       int NOT NULL DEFAULT 0,
    created_at            timestamptz NOT NULL DEFAULT now(),
    registered_country    varchar(2),
    CONSTRAINT ux_customers_tenant_external UNIQUE (tenant_id, external_id)
);
CREATE INDEX ix_customers_tenant_id ON customers (tenant_id);
COMMENT ON COLUMN customers.registered_country IS
    'ISO 3166-1 alpha-2 country code supplied by platform integration on '
    'booking commits. Drives case-3b detection via the '
    'customer_destination_country_mismatch_outbound derivation (build_context) and the '
    'tenant_route_baselines population (upsert). Pydantic enforces shape '
    'at ingress (CustomerData.registered_country, ^[A-Z]{2}$).';

-- ===========================================================================
-- Users — actors within a customer.
-- ===========================================================================
CREATE TABLE users (
    id          serial PRIMARY KEY,
    tenant_id   int NOT NULL REFERENCES tenants(id),
    customer_id int NOT NULL REFERENCES customers(id),
    external_id text NOT NULL,
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_users_tenant_customer_external UNIQUE (tenant_id, customer_id, external_id)
);

-- ===========================================================================
-- API tokens — bearer-token lookup. NO RLS — see module docstring for the
-- auth chicken-and-egg rationale. ``ix_api_tokens_tenant_last_used`` is
-- created here at table-create time.
-- ===========================================================================
CREATE TABLE api_tokens (
    id           serial PRIMARY KEY,
    tenant_id    int NOT NULL REFERENCES tenants(id),
    token_hash   text NOT NULL,
    role         text NOT NULL DEFAULT 'tenant',
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    CONSTRAINT ux_api_tokens_token_hash UNIQUE (token_hash)
);
CREATE INDEX ix_api_tokens_tenant ON api_tokens (tenant_id);
CREATE INDEX ix_api_tokens_tenant_last_used
    ON api_tokens (tenant_id, last_used_at DESC NULLS LAST);
COMMENT ON INDEX ix_api_tokens_tenant_last_used IS
    'Supports stale-token queries (least-recently-used / unused tokens per tenant). NULLS LAST so never-used tokens sort at the tail of DESC scans.';

-- ===========================================================================
-- App users — admin principals. NO RLS — same auth-lookup
-- rationale as api_tokens (see module docstring).
-- ===========================================================================
CREATE TABLE app_users (
    id          serial PRIMARY KEY,
    tenant_id   int NOT NULL REFERENCES tenants(id),
    external_id text NOT NULL,
    role        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_app_users_tenant_external UNIQUE (tenant_id, external_id)
);
CREATE INDEX ix_app_users_tenant ON app_users (tenant_id);

-- ===========================================================================
-- Row-Level Security policies on business-data tables in this migration.
-- ``api_tokens`` and ``app_users`` intentionally skip RLS — see module
-- docstring.
-- ===========================================================================
ALTER TABLE enterprises ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers   ENABLE ROW LEVEL SECURITY;
ALTER TABLE users       ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON enterprises
    USING (tenant_id = current_setting('app.tenant_id')::int);
CREATE POLICY tenant_isolation ON customers
    USING (tenant_id = current_setting('app.tenant_id')::int);
CREATE POLICY tenant_isolation ON users
    USING (tenant_id = current_setting('app.tenant_id')::int);

-- ===========================================================================
-- Grants. ``ON ALL TABLES IN SCHEMA public`` covers every table that
-- exists at this point in the chain — including ``alembic_version`` which
-- alembic created before this migration's upgrade SQL ran. Subsequent
-- migrations re-issue the broad grant to cover their newly-created
-- tables (idempotent on already-granted ones).
-- ===========================================================================
GRANT USAGE ON SCHEMA public TO riskd_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO riskd_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO riskd_app;
"""


DOWNGRADE_SQL = """
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM riskd_app;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM riskd_app;
REVOKE ALL ON SCHEMA public FROM riskd_app;

DROP TABLE IF EXISTS app_users CASCADE;
DROP TABLE IF EXISTS api_tokens CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS enterprises CASCADE;
DROP TABLE IF EXISTS tenants CASCADE;

DROP ROLE IF EXISTS riskd_app;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
