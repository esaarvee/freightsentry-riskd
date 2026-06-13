"""enrichment + global: ip_enrichment, global_blocked_vectors (no RLS) + grants

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-05

Both tables intentionally skip RLS: ``ip_enrichment`` is a lazy-cached
IP-fact store shared across tenants (no tenant_id column); ``global_blocked_vectors``
is a capability stub for cross-tenant vector sharing (``share_enabled =
false`` in v1, but the architectural design retains the global scope).
The shared-data semantics are documented via ``COMMENT ON TABLE``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
-- ===========================================================================
-- IP enrichment — global IP-level facts. NOT tenant-scoped. Lazy-cached
-- by app/enrich.py with 14-day freshness. Primary key is the IP itself
-- (no surrogate id; UPSERT on conflict).
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
-- Global blocked vectors — capability stub. share_enabled=false in v1;
-- the architectural design retains cross-tenant scope so a future toggle
-- can opt enterprises into sharing without a schema migration.
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
-- Re-issue broad grants. Covers ip_enrichment (no sequence; inet PK) +
-- global_blocked_vectors + global_blocked_vectors_id_seq.
-- ===========================================================================
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO riskd_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO riskd_app;
"""


DOWNGRADE_SQL = """
DROP TABLE IF EXISTS global_blocked_vectors CASCADE;
DROP TABLE IF EXISTS ip_enrichment CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
