"""baselines: customer_baselines, tenant_route_baselines + seed + RLS + grants

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-05

The ``tenant_route_baselines`` table lives here as part of the baselines
grouping; the ``customers.registered_country`` column belongs to the
foundation grouping and lives in ``0001_foundation.py`` instead.

Column ordering: ``country_route_stats`` is ordered last in the
``customer_baselines`` CREATE TABLE so the dump is byte-equivalent under
the canonical normalizer.

Seed migration. The ``INSERT INTO tenant_route_baselines ... SELECT``
back-fills the histogram from existing shipments/customers; it yields 0
rows on an empty DB. The seed runs as a separate ``op.execute`` call
after the schema additions so any error fails the migration loudly
rather than producing partial state.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
-- ===========================================================================
-- Customer baselines — one row per customer. Stat-dict entries:
-- {key: {n, r_n, last, type?}}; type only on entries inside ip_stats.
-- Per-IP-type decay applied on read.
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
    country_route_stats     jsonb NOT NULL DEFAULT '{}'::jsonb,
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
COMMENT ON COLUMN customer_baselines.country_route_stats IS
    'Per-customer (origin_country, destination_country) route-pair histogram. '
    'Keys are "{origin_country}||{destination_country}" composite strings; '
    'values are observation counts. Populated by baseline updater on shipment '
    'commit. Consumed by build_context to derive '
    'shipment_route_unfamiliar_for_customer (case-3a signal).';

ALTER TABLE customer_baselines ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON customer_baselines
    USING (tenant_id = current_setting('app.tenant_id')::int);

-- ===========================================================================
-- Tenant route baselines — per-tenant population frequency of
-- (customer_country, origin_country, destination_country) triples.
-- Composite PK doubles as the natural row-lookup index; the PK's
-- leading-column (tenant_id) BTREE serves the tenant-wide
-- total-observations sum the rarity derivation needs —
-- no separate single-column index needed.
-- ===========================================================================
CREATE TABLE tenant_route_baselines (
    tenant_id           int NOT NULL REFERENCES tenants(id),
    customer_country    varchar(2) NOT NULL,
    origin_country      varchar(2) NOT NULL,
    destination_country varchar(2) NOT NULL,
    observation_count   bigint NOT NULL DEFAULT 0,
    last_updated        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, customer_country, origin_country, destination_country)
);
COMMENT ON TABLE tenant_route_baselines IS
    'Per-tenant population frequency of (customer_country, origin_country, '
    'destination_country) triples. Populated synchronously on each booking '
    'commit (UPSERT) and consumed by derive_route_rarity '
    'to produce the shipment_route_rare_for_tenant signal used by the '
    'cold_start_population_baseline_rare_with_carrier_dropoff rule.';

ALTER TABLE tenant_route_baselines ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant_route_baselines
    USING (tenant_id = current_setting('app.tenant_id')::int);

-- ===========================================================================
-- Re-issue broad grants. Covers customer_baselines + customer_baselines_id_seq
-- + tenant_route_baselines (no sequence; composite PK).
-- ===========================================================================
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO riskd_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO riskd_app;
"""


SEED_SQL = """
-- This back-fills the histogram from the existing shipments/customers
-- join; it yields 0 rows on an empty DB. Reads structured columns + JSONB path
-- expressions on shipments.origin / shipments.destination. GROUP BY
-- ensures one row per distinct triple.
INSERT INTO tenant_route_baselines (
    tenant_id, customer_country, origin_country, destination_country, observation_count
)
SELECT
    s.tenant_id,
    c.registered_country  AS customer_country,
    s.origin->>'country'  AS origin_country,
    s.destination->>'country' AS destination_country,
    COUNT(*) AS observation_count
FROM shipments s
JOIN customers c
    ON c.id = s.customer_id
    AND c.tenant_id = s.tenant_id
WHERE c.registered_country IS NOT NULL
  AND s.origin->>'country' IS NOT NULL
  AND s.destination->>'country' IS NOT NULL
GROUP BY 1, 2, 3, 4;
"""


DOWNGRADE_SQL = """
DROP TABLE IF EXISTS tenant_route_baselines CASCADE;
DROP TABLE IF EXISTS customer_baselines CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)
    op.execute(SEED_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
