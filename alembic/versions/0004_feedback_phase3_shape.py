"""drop-and-recreate feedback table to Phase 3B bootstrap shape + add shipments PII HMAC columns

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-27

Phase 3B.1. Two related schema deltas in a single migration:

(a) feedback table — drop-and-recreate per operator decision 2026-05-27.
    Pre-launch the table is empty in dev/staging, so the data-loss
    risk of a destructive recreate is theoretical. The cleaner final
    shape (no decision_id FK; pure bootstrap columns; operator_id text
    from the start) wins over the additive ALTER chain. Final columns:
    id, tenant_id, request_id, target_request_id, label, feedback_ts,
    note, operator_id, created_at. UNIQUE (tenant_id, request_id),
    INDEX (tenant_id, target_request_id), CHECK on label. RLS +
    tenant_isolation reapplied.

(b) shipments.email_hmac + shipments.phone_hmac — nullable columns
    required by the feedback endpoint (3B.3) to resolve per-shipment
    HMACs into baseline.rejected_email_hmacs / rejected_phone_hmacs.
    Phase 1's shipments table stored no PII HMACs (only customer
    baselines do, and baselines accumulate across-shipments — so
    per-shipment lookup requires per-shipment storage). NULL on rows
    written before 3B.3; the feedback endpoint skips the dimension
    if NULL. Booking endpoint patched in 3B.3 to populate them.
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
-- (a) feedback — drop-and-recreate to bootstrap shape
-- ===========================================================================
-- CASCADE drops dependent FKs (none exist today). RLS policy + indexes
-- are dropped automatically with the table.
DROP TABLE IF EXISTS feedback CASCADE;

-- Recreate with pure bootstrap shape. No decision_id (target resolution
-- goes through decisions.request_id lookup at the endpoint layer in
-- 3B.3). No FK to app_users (operator_id is opaque tenant-supplied
-- text from the start).
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
    CONSTRAINT ck_feedback_label CHECK (label IN ('approved', 'rejected', 'fraud_confirmed'))
);
CREATE INDEX ix_feedback_tenant_target ON feedback (tenant_id, target_request_id);

-- Re-enable RLS + recreate tenant_isolation policy (mirror Phase 1
-- pattern at 0001_initial.py lines 296 and 311-312).
ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON feedback
    USING (tenant_id = current_setting('app.tenant_id')::int);

-- Grant feedback table privileges to riskd_app (mirror Phase 1 grants
-- on other tenant-scoped tables; required for Phase 5 role transition).
GRANT SELECT, INSERT, UPDATE, DELETE ON feedback TO riskd_app;
GRANT USAGE, SELECT ON SEQUENCE feedback_id_seq TO riskd_app;

COMMENT ON COLUMN feedback.request_id IS
    'Per-POST idempotency token. UNIQUE (tenant_id, request_id) prevents replay double-apply.';
COMMENT ON COLUMN feedback.target_request_id IS
    'request_id of the prior booking/modification this feedback targets. Indexed for monotonicity lookups.';
COMMENT ON COLUMN feedback.feedback_ts IS
    'Event time (operator-supplied). server-side created_at is the persistence timestamp.';
COMMENT ON COLUMN feedback.operator_id IS
    'Opaque tenant-supplied operator identifier (text). Not an FK; Phase 4 may layer validation via TenantConfig.';

-- ===========================================================================
-- (b) shipments PII HMAC columns
-- ===========================================================================
-- Required by the feedback endpoint to populate
-- baseline.rejected_email_hmacs / rejected_phone_hmacs per Phase 3B rules.
-- NULLABLE because rows written before 3B.3 do not carry these — the
-- feedback endpoint skips the dimension if NULL. 3B.3 patches the
-- booking endpoint INSERT to write these for new rows.
ALTER TABLE shipments
    ADD COLUMN email_hmac text NULL,
    ADD COLUMN phone_hmac text NULL;

COMMENT ON COLUMN shipments.email_hmac IS
    'HMAC of the email present on this shipment, computed via signal_helpers.hmac_hex at booking-write time. NULL on rows written before Phase 3B or when no email was supplied in the request.';
COMMENT ON COLUMN shipments.phone_hmac IS
    'HMAC of the phone present on this shipment, computed via signal_helpers.hmac_hex at booking-write time. NULL on rows written before Phase 3B or when no phone was supplied in the request.';
"""


DOWNGRADE_SQL = """
-- Reverse order — drop shipments columns first.
ALTER TABLE shipments
    DROP COLUMN IF EXISTS phone_hmac,
    DROP COLUMN IF EXISTS email_hmac;

-- Drop the Phase 3B feedback table and recreate the Phase 1 shape
-- so subsequent downgrades to 0003 / earlier remain valid.
DROP TABLE IF EXISTS feedback CASCADE;

CREATE TABLE feedback (
    id               serial PRIMARY KEY,
    tenant_id        int NOT NULL REFERENCES tenants(id),
    decision_id      int NOT NULL REFERENCES decisions(id),
    label            text NOT NULL,
    reviewer_user_id text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_feedback_tenant_decision ON feedback (tenant_id, decision_id);
ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON feedback
    USING (tenant_id = current_setting('app.tenant_id')::int);
GRANT SELECT, INSERT, UPDATE, DELETE ON feedback TO riskd_app;
GRANT USAGE, SELECT ON SEQUENCE feedback_id_seq TO riskd_app;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
