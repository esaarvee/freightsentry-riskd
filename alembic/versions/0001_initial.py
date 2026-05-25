"""initial schema: 12 tables, RLS policies, indexes, riskd_app role

Revision ID: 0001
Revises:
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Upgrade — all DDL as raw SQL for readability + atomicity.
# ---------------------------------------------------------------------------

UPGRADE_SQL = """
-- ===========================================================================
-- App role. NOLOGIN — Phase 1 connects directly as the postgres bootstrap
-- user (superuser, which bypasses RLS). Phase 5 hardening will introduce a
-- non-superuser login role with membership in riskd_app and switch the app
-- to connect as that role. RLS policies are created now so the structure is
-- in place; they will start enforcing once the connecting role is
-- non-superuser. See .claude/STATUS.md for the Phase 5 follow-up.
-- ===========================================================================
CREATE ROLE riskd_app NOLOGIN;

-- ===========================================================================
-- Tenants — the partitioning dimension. No RLS (tenants are not scoped to
-- themselves).
-- ===========================================================================
CREATE TABLE tenants (
    id         serial PRIMARY KEY,
    name       text NOT NULL,
    config     jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_seen timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now()
);

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
-- Customers — primary fraud-evaluation entity. Auto-created on first booking.
-- No shipment_volume_30d column per operator amendment 2026-05-25; 30-day
-- counts compute on demand from shipments.
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
    CONSTRAINT ux_customers_tenant_external UNIQUE (tenant_id, external_id)
);
CREATE INDEX ix_customers_tenant_id ON customers (tenant_id);
-- enterprise-level lookup index intentionally deferred; lands in Phase 4 with
-- the admin "list customers by enterprise" endpoint.

-- ===========================================================================
-- Users — actors within a customer. Auto-created on first booking.
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
-- No separate (tenant_id, customer_id) index — the UNIQUE constraint above
-- can serve any query whose WHERE clause leads with those columns.

-- ===========================================================================
-- Shipments — inbound booking events. INSERT-only. UNIQUE(tenant_id,
-- request_id) is the idempotency contract.
-- ===========================================================================
CREATE TABLE shipments (
    id          serial PRIMARY KEY,
    tenant_id   int NOT NULL REFERENCES tenants(id),
    customer_id int NOT NULL REFERENCES customers(id),
    user_id     int NOT NULL REFERENCES users(id),
    request_id  text NOT NULL,
    source_ip   inet NOT NULL,
    origin      jsonb NOT NULL,
    destination jsonb NOT NULL,
    value       numeric(14, 2) NOT NULL,
    channel     text NOT NULL,
    booking_ts  timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_shipments_tenant_request UNIQUE (tenant_id, request_id)
);
CREATE INDEX ix_shipments_tenant_customer_booking_ts
    ON shipments (tenant_id, customer_id, booking_ts);
CREATE INDEX ix_shipments_tenant_ip_booking_ts
    ON shipments (tenant_id, source_ip, booking_ts);

-- ===========================================================================
-- Decisions — persisted evaluation output. UNIQUE(tenant_id, request_id) is
-- the idempotency contract (retry returns the prior decision).
-- ===========================================================================
CREATE TABLE decisions (
    id              serial PRIMARY KEY,
    tenant_id       int NOT NULL REFERENCES tenants(id),
    shipment_id     int NOT NULL REFERENCES shipments(id),
    request_id      text NOT NULL,
    score           numeric(5, 4) NOT NULL,
    decision        text NOT NULL,
    classification  text NOT NULL,
    risk_level      text NOT NULL,
    triggered_rules text[] NOT NULL DEFAULT '{}'::text[],
    risk_factors    jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_decisions_tenant_request UNIQUE (tenant_id, request_id)
);
COMMENT ON COLUMN decisions.decision IS
    'One of ALLOW | REVIEW | BLOCK; final routing outcome from the scorer';
COMMENT ON COLUMN decisions.classification IS
    'One of GREEN | YELLOW | RED; presentation tier paired with decision';
COMMENT ON COLUMN decisions.risk_level IS
    'One of LOW | MEDIUM | HIGH | CRITICAL; score-band classification independent of decision';
CREATE INDEX ix_decisions_tenant_shipment ON decisions (tenant_id, shipment_id);

-- ===========================================================================
-- Feedback — operator-supplied outcomes for prior decisions.
-- ===========================================================================
CREATE TABLE feedback (
    id               serial PRIMARY KEY,
    tenant_id        int NOT NULL REFERENCES tenants(id),
    decision_id      int NOT NULL REFERENCES decisions(id),
    label            text NOT NULL,
    reviewer_user_id text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_feedback_tenant_decision ON feedback (tenant_id, decision_id);

-- ===========================================================================
-- Customer baselines — JSONB-heavy per-customer fraud baseline. One row per
-- customer. Stat-dict entries: {key: {n, r_n, last, type?}}; type only on
-- entries inside ip_stats. Per-IP-type decay applied on read.
-- ===========================================================================
CREATE TABLE customer_baselines (
    id                      serial PRIMARY KEY,
    tenant_id               int NOT NULL REFERENCES tenants(id),
    customer_id             int NOT NULL REFERENCES customers(id),
    origin_stats            jsonb NOT NULL DEFAULT '{}'::jsonb,
    dest_stats              jsonb NOT NULL DEFAULT '{}'::jsonb,
    lane_stats              jsonb NOT NULL DEFAULT '{}'::jsonb,
    ip_stats                jsonb NOT NULL DEFAULT '{}'::jsonb,
    ip_netblock_stats       jsonb NOT NULL DEFAULT '{}'::jsonb,
    ip_asn_stats            jsonb NOT NULL DEFAULT '{}'::jsonb,
    country_stats           jsonb NOT NULL DEFAULT '{}'::jsonb,
    origin_ip_country_stats jsonb NOT NULL DEFAULT '{}'::jsonb,
    email_hmacs             jsonb NOT NULL DEFAULT '{}'::jsonb,
    phone_hmacs             jsonb NOT NULL DEFAULT '{}'::jsonb,
    rejected_email_hmacs    jsonb NOT NULL DEFAULT '{}'::jsonb,
    rejected_phone_hmacs    jsonb NOT NULL DEFAULT '{}'::jsonb,
    email_domain_stats      jsonb NOT NULL DEFAULT '{}'::jsonb,
    phone_prefix_stats      jsonb NOT NULL DEFAULT '{}'::jsonb,
    ip_type_hist            jsonb NOT NULL DEFAULT '{}'::jsonb,
    hour_hist               jsonb NOT NULL DEFAULT '{}'::jsonb,
    weekday_hist            jsonb NOT NULL DEFAULT '{}'::jsonb,
    channel_hist            jsonb NOT NULL DEFAULT '{}'::jsonb,
    value_n                 numeric NOT NULL DEFAULT 0,
    value_mean              numeric NOT NULL DEFAULT 0,
    value_m2                numeric NOT NULL DEFAULT 0,
    cadence_n               numeric NOT NULL DEFAULT 0,
    cadence_mean_h          numeric NOT NULL DEFAULT 0,
    cadence_m2_h            numeric NOT NULL DEFAULT 0,
    last_booking_ts         timestamptz,
    last_booking_lat        numeric(8, 5),
    last_booking_lon        numeric(8, 5),
    last_booking_country    text,
    decay_anchor_date       date,
    first_seen              timestamptz NOT NULL DEFAULT now(),
    last_seen               timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_customer_baselines_tenant_customer UNIQUE (tenant_id, customer_id)
);
COMMENT ON COLUMN customer_baselines.value_n IS
    'Welford count; post-decay, exposed to rule conditions as customer_observations';
COMMENT ON COLUMN customer_baselines.ip_stats IS
    'Stat-dict {ip: {n, r_n, last, type}} where type is cloud|dc|residential';
COMMENT ON COLUMN customer_baselines.decay_anchor_date IS
    'Lazy-decay anchor date; advances on every successful baseline save';
COMMENT ON COLUMN customer_baselines.last_booking_country IS
    'ISO country code from MaxMind GeoLite2 lookup at last booking';

-- ===========================================================================
-- IP enrichment — global IP-level facts. NOT tenant-scoped. Lazy-cached
-- by app/enrich.py with 14-day freshness.
-- ===========================================================================
CREATE TABLE ip_enrichment (
    ip             inet PRIMARY KEY,
    country        text,
    region         text,
    city           text,
    lat            numeric(8, 5),
    lon            numeric(8, 5),
    asn_org        text,
    fh_level1      boolean NOT NULL DEFAULT false,
    fh_level2      boolean NOT NULL DEFAULT false,
    fh_lists       text,
    is_cloud       boolean NOT NULL DEFAULT false,
    cloud_provider text,
    is_datacenter  boolean NOT NULL DEFAULT false,
    is_proxy       boolean NOT NULL DEFAULT false,
    is_vpn         boolean NOT NULL DEFAULT false,
    is_tor         boolean NOT NULL DEFAULT false,
    proxy_type     text,
    threat         text,
    updated_at     timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE ip_enrichment IS
    'Intentionally global (no RLS): IP enrichment is shared across tenants';

-- ===========================================================================
-- API tokens — tenant-scoped token lookup. SHA-256 hash storage.
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

-- ===========================================================================
-- App users — Phase 4 admin principals.
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
-- Global blocked vectors — capability stub. Sharing disabled in v1.
-- ===========================================================================
CREATE TABLE global_blocked_vectors (
    id                    serial PRIMARY KEY,
    vector_type           text NOT NULL,
    vector_hash           text NOT NULL,
    created_by_tenant_id  int NOT NULL REFERENCES tenants(id),
    share_enabled         boolean NOT NULL DEFAULT false,
    created_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_global_blocked_vectors_type_hash UNIQUE (vector_type, vector_hash)
);
COMMENT ON TABLE global_blocked_vectors IS
    'Intentionally global (no RLS): capability stub for cross-tenant sharing; share_enabled=false in v1';

-- ===========================================================================
-- Row-Level Security policies. Tenant-scoped tables enable RLS and create a
-- tenant_isolation policy keyed on the session variable app.tenant_id.
-- ip_enrichment and global_blocked_vectors intentionally skip RLS.
-- ===========================================================================
ALTER TABLE enterprises         ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers           ENABLE ROW LEVEL SECURITY;
ALTER TABLE users               ENABLE ROW LEVEL SECURITY;
ALTER TABLE shipments           ENABLE ROW LEVEL SECURITY;
ALTER TABLE decisions           ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback            ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_baselines  ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_tokens          ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_users           ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON enterprises
    USING (tenant_id = current_setting('app.tenant_id')::int);
CREATE POLICY tenant_isolation ON customers
    USING (tenant_id = current_setting('app.tenant_id')::int);
CREATE POLICY tenant_isolation ON users
    USING (tenant_id = current_setting('app.tenant_id')::int);
CREATE POLICY tenant_isolation ON shipments
    USING (tenant_id = current_setting('app.tenant_id')::int);
CREATE POLICY tenant_isolation ON decisions
    USING (tenant_id = current_setting('app.tenant_id')::int);
CREATE POLICY tenant_isolation ON feedback
    USING (tenant_id = current_setting('app.tenant_id')::int);
CREATE POLICY tenant_isolation ON customer_baselines
    USING (tenant_id = current_setting('app.tenant_id')::int);
CREATE POLICY tenant_isolation ON api_tokens
    USING (tenant_id = current_setting('app.tenant_id')::int);
CREATE POLICY tenant_isolation ON app_users
    USING (tenant_id = current_setting('app.tenant_id')::int);

-- ===========================================================================
-- Grants. The app role receives data-plane privileges on every table; the
-- bootstrap user (superuser) handles schema management.
-- ===========================================================================
GRANT USAGE ON SCHEMA public TO riskd_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO riskd_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO riskd_app;
"""


# ---------------------------------------------------------------------------
# Downgrade — reverse-FK order. DROP TABLE ... CASCADE removes indexes and
# policies; explicit REVOKE clears grants; DROP ROLE last.
# ---------------------------------------------------------------------------

DOWNGRADE_SQL = """
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM riskd_app;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM riskd_app;
REVOKE ALL ON SCHEMA public FROM riskd_app;

DROP TABLE IF EXISTS global_blocked_vectors CASCADE;
DROP TABLE IF EXISTS app_users CASCADE;
DROP TABLE IF EXISTS api_tokens CASCADE;
DROP TABLE IF EXISTS ip_enrichment CASCADE;
DROP TABLE IF EXISTS customer_baselines CASCADE;
DROP TABLE IF EXISTS feedback CASCADE;
DROP TABLE IF EXISTS decisions CASCADE;
DROP TABLE IF EXISTS shipments CASCADE;
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
