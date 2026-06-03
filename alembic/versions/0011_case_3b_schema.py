"""Phase 6A.6 case-3b schema: customers.registered_country column + tenant_route_baselines table.

Two additive schema changes co-landed because both target the case-3b
detection capability:

1. customers.registered_country VARCHAR(2) NULL — structured country
   signal supplied by platform integration on booking commits. The
   Pydantic field (CustomerData.registered_country in app/models.py)
   validates ISO 3166-1 alpha-2 at request ingress per Phase 6A.5.
   Customer-upsert COALESCE-preservation lands in Phase 6A.7 so
   payload nulls cannot overwrite operator-supplied values.

2. tenant_route_baselines table — population frequency of
   (customer_country, origin_country, destination_country) triples
   per tenant. Composite PK doubles as the natural row-lookup index;
   the PK's leading-column (tenant_id) BTREE serves the tenant-wide
   total-observations sum that the rarity derivation needs (Phase
   6A.8) — no separate single-column index needed. RLS enforces
   tenant isolation under riskd_app_login per the Phase 5D role
   transition. Migration explicitly GRANTs SELECT/INSERT/UPDATE/DELETE
   to riskd_app — Migration 0001's "ON ALL TABLES IN SCHEMA" grant
   is one-shot and does NOT cover future tables, and the chain has
   no ALTER DEFAULT PRIVILEGES, so each new table needs its own
   explicit grant (lesson from 6A.6 db-reviewer cycle 1).

Seed query (idempotent — runs once during migration):
INSERT...SELECT from the existing shipments/customers join. Reads
structured columns + jsonb path expressions (`s.origin->>'country'`).
No parse_country SQL function — Phase 6A's structured-field
architectural pattern. For prototype-stage data with no customers
having registered_country set, the seed yields 0 rows; the table
populates over time via the runtime UPSERT in Phase 6A.7 once
platform integration ships the structured field.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
-- 1. customers.registered_country column (nullable; populated by 6A.7 upsert)
ALTER TABLE customers ADD COLUMN registered_country VARCHAR(2);

COMMENT ON COLUMN customers.registered_country IS
    'ISO 3166-1 alpha-2 country code supplied by platform integration on '
    'booking commits. Drives case-3b detection via the '
    'customer_country_triangle_mismatch derivation (build_context) and the '
    'tenant_route_baselines population (6A.7 upsert). Pydantic enforces shape '
    'at ingress (CustomerData.registered_country, ^[A-Z]{2}$).';

-- 2. tenant_route_baselines table. Composite PK leads with tenant_id,
-- so it implicitly indexes WHERE tenant_id = $1 prefix lookups
-- (including the tenant-wide SUM in 6A.8). NO separate single-column
-- index — would be redundant write amplification on the hot booking
-- path with zero read benefit.
CREATE TABLE tenant_route_baselines (
    tenant_id           int NOT NULL REFERENCES tenants(id),
    customer_country    VARCHAR(2) NOT NULL,
    origin_country      VARCHAR(2) NOT NULL,
    destination_country VARCHAR(2) NOT NULL,
    observation_count   bigint NOT NULL DEFAULT 0,
    last_updated        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, customer_country, origin_country, destination_country)
);

COMMENT ON TABLE tenant_route_baselines IS
    'Per-tenant population frequency of (customer_country, origin_country, '
    'destination_country) triples. Populated synchronously on each booking '
    'commit (6A.7 UPSERT) and consumed by Phase 6A.8 derive_route_rarity '
    'to produce the shipment_route_rare_for_tenant signal used by the '
    'cold_start_population_baseline_rare_with_carrier_dropoff rule (6A.9).';

-- 3. RLS — active under riskd_app_login per Phase 5D role transition
ALTER TABLE tenant_route_baselines ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON tenant_route_baselines
    USING (tenant_id = current_setting('app.tenant_id')::int);

-- 4. GRANT runtime role access to the new table. Migration 0001's
-- "ON ALL TABLES IN SCHEMA public TO riskd_app" is a one-shot grant
-- at that point in time; it does NOT cover tables created by later
-- migrations. No ALTER DEFAULT PRIVILEGES exists in the chain, so
-- each new tenant-scoped table needs its own explicit grant. Without
-- this, riskd_app_login (LOGIN INHERIT of riskd_app) gets
-- "permission denied for table tenant_route_baselines" at the 6A.7
-- UPSERT and the booking commit path silently fails.
GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_route_baselines TO riskd_app;
"""

DOWNGRADE_SQL = """
REVOKE ALL ON tenant_route_baselines FROM riskd_app;
-- DROP TABLE removes RLS state automatically; no explicit DISABLE needed.
DROP TABLE IF EXISTS tenant_route_baselines;

ALTER TABLE customers DROP COLUMN registered_country;
"""


# Seed query — runs as a separate statement after the schema additions.
# Reads structured columns (customers.registered_country, just added)
# and jsonb path expressions on the existing shipments.origin /
# shipments.destination jsonb columns. For prototype data with no
# customers.registered_country populated, the WHERE filter yields 0
# rows; the table populates over time via the runtime UPSERT (6A.7).
#
# Idempotent at migration time (runs once); GROUP BY ensures one row
# per distinct triple.
SEED_SQL = """
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


def upgrade() -> None:
    op.execute(UPGRADE_SQL)
    op.execute(SEED_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
