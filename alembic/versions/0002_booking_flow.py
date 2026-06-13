"""booking flow: shipments, decisions, feedback + RLS + grants

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-05

Column ordering — each CREATE TABLE orders ALTER-appended columns last so
the dump is byte-equivalent under the canonical normalizer: ``shipments``
ends with ``destination_hmac``, ``email_hmac``, ``phone_hmac``;
``decisions`` ends with ``request_type``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
-- ===========================================================================
-- Shipments — inbound booking events. INSERT-only. Idempotency contract:
-- UNIQUE (tenant_id, request_id). PII HMAC columns nullable; populated
-- by the booking endpoint at write time when the request supplies the
-- corresponding fields.
-- ===========================================================================
CREATE TABLE shipments (
    id               serial PRIMARY KEY,
    tenant_id        int NOT NULL REFERENCES tenants(id),
    customer_id      int NOT NULL REFERENCES customers(id),
    user_id          int NOT NULL REFERENCES users(id),
    request_id       text NOT NULL,
    source_ip        inet NOT NULL,
    origin           jsonb NOT NULL,
    destination      jsonb NOT NULL,
    value            numeric(14, 2) NOT NULL,
    channel          text NOT NULL,
    booking_ts       timestamptz NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    destination_hmac text NOT NULL,
    email_hmac       text NULL,
    phone_hmac       text NULL,
    CONSTRAINT ux_shipments_tenant_request UNIQUE (tenant_id, request_id)
);
CREATE INDEX ix_shipments_tenant_customer_booking_ts
    ON shipments (tenant_id, customer_id, booking_ts);
CREATE INDEX ix_shipments_tenant_ip_booking_ts
    ON shipments (tenant_id, source_ip, booking_ts);
CREATE INDEX ix_shipments_tenant_dest_hmac_booking_ts
    ON shipments (tenant_id, destination_hmac, booking_ts);
COMMENT ON COLUMN shipments.email_hmac IS
    'HMAC of the email present on this shipment, computed via signal_helpers.hmac_hex at booking-write time. NULL when no email was supplied in the request.';
COMMENT ON COLUMN shipments.phone_hmac IS
    'HMAC of the phone present on this shipment, computed via signal_helpers.hmac_hex at booking-write time. NULL when no phone was supplied in the request.';

-- ===========================================================================
-- Decisions — persisted evaluation output. UNIQUE idempotency is the
-- (tenant_id, request_type, request_id) index so a booking and a
-- modification can legitimately share a ``request_id`` per the public
-- idempotency contract.
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
    request_type    text NOT NULL DEFAULT 'booking',
    CONSTRAINT ck_decisions_request_type
        CHECK (request_type IN ('booking', 'modification'))
);
COMMENT ON COLUMN decisions.decision IS
    'One of ALLOW | REVIEW | BLOCK; final routing outcome from the scorer';
COMMENT ON COLUMN decisions.classification IS
    'One of GREEN | YELLOW | RED; presentation tier paired with decision';
COMMENT ON COLUMN decisions.risk_level IS
    'One of LOW | MEDIUM | HIGH | CRITICAL; score-band classification independent of decision';
COMMENT ON COLUMN decisions.request_type IS
    'One of booking | modification; discriminates which evaluate endpoint produced this decision. DEFAULT booking preserved as safety net — endpoints supply request_type explicitly.';
CREATE INDEX ix_decisions_tenant_shipment ON decisions (tenant_id, shipment_id);
CREATE INDEX ix_decisions_tenant_request_type_created
    ON decisions (tenant_id, request_type, created_at);
CREATE UNIQUE INDEX ux_decisions_tenant_request_type
    ON decisions (tenant_id, request_type, request_id);
COMMENT ON INDEX ux_decisions_tenant_request_type IS
    'UNIQUE idempotency key over (tenant_id, request_type, request_id) so a booking and a modification with the same request_id are both valid.';

-- ===========================================================================
-- Feedback — operator-supplied outcomes for prior decisions. Bootstrap
-- shape: no decision_id FK (target resolution goes through
-- decisions.request_id lookup at the endpoint layer); no FK to app_users
-- (operator_id is opaque tenant-supplied text from the start).
-- ===========================================================================
CREATE TABLE feedback (
    id                serial PRIMARY KEY,
    tenant_id         int NOT NULL REFERENCES tenants(id),
    request_id        text NOT NULL,
    target_request_id text NOT NULL,
    label             text NOT NULL,
    feedback_ts       timestamptz NOT NULL,
    note              text NULL,
    operator_id       text NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ux_feedback_tenant_request UNIQUE (tenant_id, request_id),
    CONSTRAINT ck_feedback_label
        CHECK (label IN ('approved', 'rejected', 'fraud_confirmed'))
);
CREATE INDEX ix_feedback_tenant_target ON feedback (tenant_id, target_request_id);
COMMENT ON COLUMN feedback.request_id IS
    'Per-POST idempotency token. UNIQUE (tenant_id, request_id) prevents replay double-apply.';
COMMENT ON COLUMN feedback.target_request_id IS
    'request_id of the prior booking/modification this feedback targets. Indexed for monotonicity lookups.';
COMMENT ON COLUMN feedback.feedback_ts IS
    'Event time (operator-supplied). server-side created_at is the persistence timestamp.';
COMMENT ON COLUMN feedback.operator_id IS
    'Opaque tenant-supplied operator identifier (text). Not an FK; validation may later be layered via TenantConfig.';

-- ===========================================================================
-- Row-Level Security policies.
-- ===========================================================================
ALTER TABLE shipments ENABLE ROW LEVEL SECURITY;
ALTER TABLE decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback  ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON shipments
    USING (tenant_id = current_setting('app.tenant_id')::int);
CREATE POLICY tenant_isolation ON decisions
    USING (tenant_id = current_setting('app.tenant_id')::int);
CREATE POLICY tenant_isolation ON feedback
    USING (tenant_id = current_setting('app.tenant_id')::int);

-- ===========================================================================
-- Re-issue broad grants — covers shipments, decisions, feedback and their
-- sequences. Re-grants on previously-covered tables are no-ops.
-- ===========================================================================
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO riskd_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO riskd_app;
"""


DOWNGRADE_SQL = """
DROP TABLE IF EXISTS feedback CASCADE;
DROP TABLE IF EXISTS decisions CASCADE;
DROP TABLE IF EXISTS shipments CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
