"""platform shipment_id as identity + transaction_number

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-14

Makes the upstream platform's shipment identifier the system of record:
``shipments.id`` changes from a riskd-minted serial to a platform-supplied
``text`` identity, the PK becomes composite ``(tenant_id, id)`` (defense
against a cross-tenant shipment_id collision leaking existence via a 409),
and a new operator-facing ``transaction_number text NOT NULL`` is stored
alongside it (unindexed by design). ``decisions.shipment_id`` retypes
int -> text and its FK becomes composite ``(tenant_id, shipment_id) ->
shipments(tenant_id, id)``.

Pre-launch change: tables are empty, so this is a clean redefinition with
no backfill. The composite FK formalizes an invariant the application
already enforces — every decisions<->shipments join already carries
``s.tenant_id = d.tenant_id``.

DDL ordering is load-bearing: the decisions FK must drop before either
side retypes or the shipments PK drops; the composite PK must exist before
the composite FK can reference it.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
-- The decisions FK references shipments(id); it must drop before either
-- side is retyped and before the shipments PK is dropped.
ALTER TABLE decisions DROP CONSTRAINT decisions_shipment_id_fkey;

-- shipments.id: serial -> text, PK -> composite (tenant_id, id).
ALTER TABLE shipments DROP CONSTRAINT shipments_pkey;          -- was PRIMARY KEY (id)
ALTER TABLE shipments ALTER COLUMN id DROP DEFAULT;            -- drop nextval default before retype
DROP SEQUENCE shipments_id_seq;                                -- owned serial seq, now unreferenced
ALTER TABLE shipments ALTER COLUMN id TYPE text USING id::text;
ALTER TABLE shipments ADD CONSTRAINT shipments_pkey PRIMARY KEY (tenant_id, id);

-- Operator-facing platform reference. UNINDEXED by design: not a riskd
-- query key; the external admin dashboard (separate repo) reads by date
-- range. NOT NULL is safe with no default because the table is empty
-- pre-launch.
ALTER TABLE shipments ADD COLUMN transaction_number text NOT NULL;

COMMENT ON COLUMN shipments.id IS
    'Platform-supplied shipment identity (system of record). Composite PK (tenant_id, id) guards against a cross-tenant shipment_id collision leaking existence via a 409. Replaces the former riskd-minted serial.';
COMMENT ON COLUMN shipments.transaction_number IS
    'Platform-supplied operator-facing reference. Stored UNINDEXED by design: not a riskd query key; the external admin dashboard (separate repo) reads by date range, and no riskd read endpoint exists. Same logical value as freight_risk.shipments.transaction_number (calibration source schema) — not a separate concept.';

-- decisions.shipment_id: int -> text; FK -> composite (tenant_id, shipment_id).
-- The retype rebuilds ix_decisions_tenant_shipment under the hood; the index
-- definition (by column name) is unchanged.
ALTER TABLE decisions ALTER COLUMN shipment_id TYPE text USING shipment_id::text;
ALTER TABLE decisions ADD CONSTRAINT decisions_shipment_id_fkey
    FOREIGN KEY (tenant_id, shipment_id) REFERENCES shipments(tenant_id, id);
"""


# NOTE: downgrade is ONE-WAY-AFTER-LAUNCH. The id text -> integer cast via
# USING id::integer throws the moment any non-numeric platform shipment_id
# exists — which is the entire point of going to text. It is vacuous on empty
# pre-launch tables and hard-fails once real platform IDs are present. Do NOT
# run this post-launch expecting it to round-trip.
DOWNGRADE_SQL = """
ALTER TABLE decisions DROP CONSTRAINT decisions_shipment_id_fkey;
ALTER TABLE decisions ALTER COLUMN shipment_id TYPE integer USING shipment_id::integer;

ALTER TABLE shipments DROP COLUMN transaction_number;
ALTER TABLE shipments DROP CONSTRAINT shipments_pkey;          -- composite (tenant_id, id)
ALTER TABLE shipments ALTER COLUMN id TYPE integer USING id::integer;
CREATE SEQUENCE shipments_id_seq AS integer OWNED BY shipments.id;
SELECT setval('shipments_id_seq', COALESCE((SELECT max(id) FROM shipments), 0) + 1, false);
ALTER TABLE shipments ALTER COLUMN id SET DEFAULT nextval('shipments_id_seq'::regclass);
ALTER TABLE shipments ADD CONSTRAINT shipments_pkey PRIMARY KEY (id);

ALTER TABLE decisions ADD CONSTRAINT decisions_shipment_id_fkey
    FOREIGN KEY (shipment_id) REFERENCES shipments(id);

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO riskd_app;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
