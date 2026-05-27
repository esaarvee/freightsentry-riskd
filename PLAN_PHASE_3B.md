# Phase 3 — Batch 3B Plan — Feedback endpoint stack

> **Status (2026-05-27)**: Pending operator approval. Operator may defer approval until after 3A execution reports.
>
> **Scope decision absorbed (2026-05-27)**: Feedback table will be MIGRATED to the bootstrap-required shape (add `request_id`, `target_request_id`, `feedback_ts`, `note`, `operator_id`; keep `decision_id` as FK for query convenience; rename `reviewer_user_id` → `operator_id` via column rename). This gives per-POST idempotency via `request_id` AND label-monotonicity dedup via `(tenant_id, target_request_id)`. Operator-selected option from PLAN_PHASE_3 surface question.

Batch 3B lands the `POST /api/v1/shipments/feedback` endpoint end-to-end: alembic migration to extend the existing `feedback` table to the bootstrap shape, Pydantic request/response models, endpoint route with label-monotonicity + idempotency semantics, baseline writer wiring (reusing existing `add_rejected_observation` per verification), customer counter updates, 4 previously-rejected rules + supporting Context derivations + DSL whitelist fields, and the integration test demonstrating the full feedback → next-booking → previously-rejected-rule-fires chain.

Target: 7 commits.

---

## Decisions absorbed (batch-relevant subset)

| Decision | Value | Source |
|---|---|---|
| Endpoint URL | `POST /api/v1/shipments/feedback` | Phase 3 bootstrap |
| Feedback table schema | **DROP-AND-RECREATE** to pure bootstrap shape (operator decision 2026-05-27 follow-up). No `decision_id` FK; no FK to `app_users`. Final columns: `id, tenant_id, request_id, target_request_id, label, feedback_ts, note, operator_id, created_at`. `UNIQUE (tenant_id, request_id)`, `INDEX (tenant_id, target_request_id)`, CHECK constraint on `label`. RLS + tenant_isolation policy reapplied. Downgrade recreates Phase 1 shape. | Operator-selected (2026-05-27) |
| Existing feedback table location | `alembic/versions/0001_initial.py:154-162` (id, tenant_id, decision_id, label, reviewer_user_id, created_at). RLS enabled at `:296` + tenant_isolation policy at `:311-312` — preserved through migration. | Verification §1 |
| Idempotency model | Two-tier. First-tier: hard `UNIQUE (tenant_id, request_id)` — POST replay returns prior result without re-applying. Second-tier: label-monotonicity on `(tenant_id, target_request_id)` — a new POST with a *different* request_id but the *same* target_request_id applies only if the new label is "stronger". Strength order: `fraud_confirmed > rejected > approved`. | Phase 3 bootstrap |
| Label values | `Literal["approved", "rejected", "fraud_confirmed"]` | Phase 3 bootstrap |
| Baseline writer | Reuse `app/baseline.py::add_rejected_observation` at `app/baseline.py:418-426`. Signature: `(self, *, key_in: str, stat: str, ts: datetime) -> None`. Caller (the endpoint) must hold `SELECT FOR UPDATE` lock via `CustomerBaseline.load(conn, ..., for_update=True)` per Phase 1 pattern. | Verification §1 |
| Customer counter updates | `customers.flagged_count += 1` for `"rejected"` and `"fraud_confirmed"`; `customers.fraud_confirmed_count += 1` for `"fraud_confirmed"` only. Both columns exist and are writable per verification §2. Updates are idempotent under monotonicity: a no-op label re-application does NOT re-increment. | Phase 3 bootstrap + verification |
| Dimensions written | Per the booking's stored fields: IP (`ip_stats`), ASN, origin (`origin_stats`), destination (`dest_stats`), email_hmac (`rejected_email_hmacs`), phone_hmac (`rejected_phone_hmacs`). For `"approved"` label, NO `r_n` increment (just the audit row). For `"rejected"` and `"fraud_confirmed"`, increment `r_n` on each dimension present. | Phase 3 bootstrap |
| 4 previously-rejected rules | `email_previously_rejected_for_customer` (0.60), `phone_previously_rejected_for_customer` (0.60), `origin_previously_rejected_for_customer` (0.70), `ip_previously_rejected_for_customer` (0.70). Conditions check per-customer rejected dimension presence. All maturity-sensitive: `True`. | Verification §6 — freight_risk catalogue |
| Feedback transaction scope | All writes (feedback INSERT, baseline UPDATE, customer counter UPDATE) in ONE transaction. Mirrors booking persistence discipline. | Phase 3 bootstrap |
| Resolve target_request_id → decision/shipment | Via `WHERE tenant_id = $1 AND request_id = $2` on decisions table (tenant_id is the auth-context's; request_id is target_request_id from payload). decisions row's `shipment_id` FK gives us the prior shipment for context reconstruction. | Verification §4 |
| Per-customer rejected dict semantics | freight_risk uses per-customer dicts (not global); we mirror — `add_rejected_observation` writes to the customer's baseline only. | Verification §6 |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md. Pre-commit hooks active.
- Reviewer routing per CLAUDE.md triage gate:
  - 3B.1 (migration): **Never-Skip** (schema migration) → standard panel + db-reviewer.
  - 3B.2 (Pydantic feedback models): standard panel + test-reviewer.
  - 3B.3 (endpoint route): Never-Skip (new `.py` file under `app/`) → standard + db-reviewer (new INSERT/UPDATE paths on tenant-scoped tables) + test-reviewer.
  - 3B.4 (DSL whitelist + Context derivations): standard panel + security-auditor (DSL field-whitelist surface).
  - 3B.5 (4 previously-rejected rules + tests): Never-Skip (rule add/remove) → standard + test-reviewer.
  - 3B.6 (integration test): test-only — test-reviewer + senior-engineer + code-flow.
  - 3B.7 (concurrent-write integration test): test-only — test-reviewer + senior-engineer + code-flow.

- Reviewer-invocation slice template:
  > `Plan file: PLAN_PHASE_3B.md, current commit: 3B.N (<title>), upcoming commits: 3B.{N+1} through 3B.7 sections. Read only those sections.`

---

## Cross-batch dependencies

- **Consumes from Phase 1**: existing `feedback` table at `alembic/versions/0001_initial.py:154-162`; `customers.flagged_count` + `customers.fraud_confirmed_count` at `:65-83`; `app/baseline.py::add_rejected_observation` at `:418-426`; per-customer rejected dicts (`rejected_email_hmacs`, `rejected_phone_hmacs`) per `app/baseline.py` and schema docs.
- **Consumes from Phase 2**: 56-field Context (unchanged shape); `score()` scoring entry point.
- **Consumes from 3A**: `decisions.request_type` column — feedback can target both booking AND modification decisions via the same `(tenant_id, request_id)` lookup (since request_id is unique within tenant regardless of request_type).
- **Consumed by 3C**: New feedback endpoint adds queries to the inventory; the migration adds an index that must satisfy RLS audit policies.
- **Consumed by 3D**: Booking → feedback → next-booking-triggers-rule chain is the centerpiece of Phase 3D's integration sweep.

---

## 3B.1 — Migration: drop-and-recreate feedback (pure bootstrap shape) + add shipments.email_hmac/phone_hmac

**Theme**: Two related schema deltas in a single migration. (a) DROP and RECREATE `feedback` to the pure bootstrap shape (operator decision 2026-05-27: cleaner than the additive ALTER chain since pre-launch dev/staging tables are empty; no decision_id FK; operator_id is opaque text from the start). (b) Add `shipments.email_hmac` and `shipments.phone_hmac` NULLABLE columns so the feedback endpoint can resolve per-shipment HMACs into the customer's `rejected_email_hmacs` / `rejected_phone_hmacs` dicts (verification 2026-05-27 confirmed: shipments today stores no PII HMACs, only customer baselines do, and baselines accumulate across-shipments — so per-shipment lookup requires per-shipment storage).

**Files**:
- `alembic/versions/0004_feedback_phase3_shape.py` (NEW)

**Specifics**:

```python
"""Drop-and-recreate feedback table to Phase 3B bootstrap shape + add
shipments.email_hmac/phone_hmac for per-shipment PII HMAC lookup.

Drop-and-recreate rationale (operator decision 2026-05-27): pre-launch
the feedback table is empty in dev/staging. The additive ALTER chain
would otherwise be needed to preserve data, but with no rows to preserve
the cleaner final shape (no decision_id FK; pure bootstrap columns;
operator_id text from the start) wins.

Final feedback shape:
  id, tenant_id, request_id, target_request_id, label,
  feedback_ts, note, operator_id, created_at
  UNIQUE (tenant_id, request_id)
  INDEX  (tenant_id, target_request_id)
  RLS    tenant_isolation policy reapplied

Shipments additions:
  email_hmac text NULL — populated at booking-write time in 3B.3 patch
  phone_hmac text NULL — same

Revision ID: 0004
Revises: 0003
"""

from alembic import op


revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.execute(
        """
        -- Drop existing feedback table. CASCADE drops dependent FKs
        -- (none exist today). RLS policy and indexes are dropped
        -- automatically with the table.
        DROP TABLE IF EXISTS feedback CASCADE;

        -- Recreate with pure bootstrap shape. No decision_id (target
        -- resolution goes through decisions WHERE request_id = $1 at the
        -- endpoint layer). No FK to app_users (operator_id is opaque text
        -- from the start).
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
        -- pattern at 0001_initial.py:296 + :311-312).
        ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON feedback
            USING (tenant_id = current_setting('app.tenant_id')::int);

        -- Grant feedback table privileges to riskd_app (mirror Phase 1's
        -- GRANT on other tenant-scoped tables; required for Phase 5 role
        -- transition).
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
        """
    )

    # ----- shipments PII HMAC columns ---------------------------------------
    # Required by the feedback endpoint to populate
    # baseline.rejected_email_hmacs / rejected_phone_hmacs per Phase 3B rules.
    # NULLABLE because rows written before 3B.3 do not carry these — the
    # feedback endpoint skips the dimension if NULL. 3B.3 patches
    # booking.py INSERT to write these for new rows.
    op.execute(
        """
        ALTER TABLE shipments
            ADD COLUMN email_hmac text NULL,
            ADD COLUMN phone_hmac text NULL;

        COMMENT ON COLUMN shipments.email_hmac IS
            'HMAC of the email present on this shipment, computed via signal_helpers.hmac_hex at booking-write time. NULL on rows written before Phase 3B or when no email was supplied in the request.';
        COMMENT ON COLUMN shipments.phone_hmac IS
            'HMAC of the phone present on this shipment, computed via signal_helpers.hmac_hex at booking-write time. NULL on rows written before Phase 3B or when no phone was supplied in the request.';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE shipments
            DROP COLUMN IF EXISTS phone_hmac,
            DROP COLUMN IF EXISTS email_hmac;

        -- Drop the Phase 3 feedback table and recreate the Phase 1 shape
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
    )
```

**Validation**:
- `docker compose exec app alembic upgrade head` clean
- `docker compose exec app alembic downgrade -1 && docker compose exec app alembic upgrade head` round-trip clean
- `\d feedback` shows the new columns, unique constraint, new index, renamed column
- `pytest tests/ --asyncio-mode=auto -q` — existing 494 tests (post-3A) pass; RLS policy still active per Phase 1 ENABLE at `:296`.

**Risk**: **Medium**. Drop-and-recreate is destructive. Pre-launch dev/staging tables are empty so the data-loss risk is theoretical — but the test fixtures MUST be reset (`docker compose down -v && docker compose up -d && alembic upgrade head`) between sessions if any test data was seeded into the old feedback shape. Reviewer must confirm test fixture pattern handles the migration cleanly.

**Reversibility**: Medium. Downgrade recreates the Phase 1 feedback shape (id, tenant_id, decision_id, label, reviewer_user_id, created_at) + index + RLS + grants. **Data loss on downgrade**: any feedback rows written under the Phase 3B shape are unrecoverable on downgrade because the `decision_id` value is not present in the new schema. Acceptable pre-launch; if any production data exists at the time of downgrade, operator must export first (out of scope for migration).

**Pre-commit verification**: All gates green; no Python code touched.

**Observability**: Migration is logged via alembic; new columns will surface in Phase 5 structured logs.

**Test changes**: None in this commit — round-trip verified via alembic. Schema verification test added alongside endpoint route in 3B.3.

**Rollback plan**: `alembic downgrade -1`.

**Declared breaks**: None. The existing feedback table is empty in dev/staging (no code path writes to it pre-Phase-3); the migration is purely additive from a behavioral standpoint.

**Reviewer routing**: Never-Skip (migration). Standard panel + db-reviewer.

---

## 3B.2 — Pydantic feedback models

**Theme**: Add `FeedbackRequest` and `FeedbackResponse` Pydantic v2 models in `app/models.py`.

**Files**:
- `app/models.py` (EDIT — append after modification models from 3A.2)
- `tests/unit/test_models_feedback.py` (NEW)

**Specifics**:

```python
FeedbackLabel = Literal["approved", "rejected", "fraud_confirmed"]


class FeedbackRequest(BaseModel):
    """POST /api/v1/shipments/feedback payload."""
    request_id: str = Field(..., min_length=1, max_length=128)
    target_request_id: str = Field(..., min_length=1, max_length=128)
    label: FeedbackLabel
    feedback_ts: datetime
    note: str | None = Field(None, max_length=2048)
    operator_id: str | None = Field(None, max_length=128)


class FeedbackResponse(BaseModel):
    """POST /api/v1/shipments/feedback response."""
    applied: bool                       # False if monotonicity blocked or duplicate POST
    previous_label: FeedbackLabel | None  # None on first-ever feedback for target
    target_request_id: str
```

`tests/unit/test_models_feedback.py`:
1. `FeedbackRequest` rejects empty `request_id`, empty `target_request_id`.
2. `label` accepts only the 3 enumerated values.
3. `note` accepts up to 2048 chars, rejects longer.
4. `feedback_ts` accepts ISO timestamps.
5. `FeedbackResponse.applied` is required bool.

**Validation**:
- `pytest tests/unit/test_models_feedback.py -v` — 5 tests pass.
- `mypy app/` strict clean.

**Risk**: **Low**.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 5 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Standard panel + test-reviewer.

---

## 3B.3 — Feedback endpoint route + baseline + counter wiring

**Theme**: Add `app/api/feedback.py` with the endpoint route. Implements two-tier idempotency, label monotonicity, baseline writer (per-dimension `add_rejected_observation` for `rejected`/`fraud_confirmed`), customer counter updates, single-transaction discipline.

**Files**:
- `app/api/feedback.py` (NEW)
- `app/main.py` (EDIT — register router)
- `app/baseline.py` (EDIT — may need a small helper to enumerate which stats to mark; OR endpoint code does enumeration inline. See specifics.)
- `app/api/booking.py` (EDIT — INSERT at L165 adds `email_hmac`, `phone_hmac` columns; computed via `signal_helpers.hmac_hex(email)` / `hmac_hex(phone)` at write time, NULL if not supplied in request)
- `app/api/modification.py` (EDIT — modifications INSERT new shipments? **No** per 3A.6 — modifications do not create a new shipments row. The original shipment's HMACs are preserved. Edge case: a modification of `modification_type='recipient'` may include a new email/phone — these are NOT written to the immutable shipments row; if rejected, the new contact info is lost. **Logged to BUGS.md as Phase 4+ refinement opportunity**; for Phase 3, recipient-change modifications do not produce email/phone-rejection signal contributions.)
- `tests/unit/test_feedback_counter_transitions.py` (NEW — parametrized 3×3 prior-label × new-label transition matrix per operator feedback. Tests assertions on `flag_delta` and `fraud_delta` computations directly against the `_compute_counter_deltas` helper extracted from the endpoint body.)
- `tests/integration/test_feedback_endpoint.py` (NEW)

**Specifics**:

Label-strength helper (in `app/api/feedback.py` or `app/models.py`):
```python
_LABEL_RANK = {"approved": 0, "rejected": 1, "fraud_confirmed": 2}

def _label_stronger(new: str, prior: str | None) -> bool:
    """True if `new` should overwrite `prior` per monotonicity rules."""
    if prior is None:
        return True
    return _LABEL_RANK[new] > _LABEL_RANK[prior]
```

Endpoint shape (~250 lines, mirroring `app/api/booking.py` discipline):

1. **Auth + transaction**: `Depends(require_api_token)` → `AuthContext`; `async with get_conn() as conn, conn.transaction():`; `await set_tenant_id(conn, auth.tenant_id)`.

2. **First-tier idempotency check** (per-POST request_id dedup):
   ```sql
   SELECT label, target_request_id, created_at
     FROM feedback
    WHERE tenant_id = $1 AND request_id = $2
   ```
   If hit → return `FeedbackResponse(applied=False, previous_label=<that row's label>, target_request_id=<that row's target>)`. No baseline writes, no counter writes. This is the network-replay path.

3. **Resolve target_request_id → prior decision + shipment + customer**:
   ```sql
   SELECT d.id AS decision_id, d.shipment_id, d.request_type,
          s.customer_id, s.source_ip, s.origin, s.destination,
          s.destination_hmac, s.booking_ts,
          s.value, c.id AS customer_pk
     FROM decisions d
     JOIN shipments s ON s.id = d.shipment_id AND s.tenant_id = d.tenant_id
     JOIN customers c ON c.id = s.customer_id AND c.tenant_id = d.tenant_id
    WHERE d.tenant_id = $1 AND d.request_id = $2
   ```
   If no row → 404 "target_request_id not found".

4. **Second-tier monotonicity check** (per-target label upgrade):
   ```sql
   SELECT label FROM feedback
    WHERE tenant_id = $1 AND target_request_id = $2
    ORDER BY feedback_ts DESC, created_at DESC
    LIMIT 1
   ```
   If `_label_stronger(new=payload.label, prior=<that row's label>) == False`:
     - INSERT the feedback audit row (for trail) but skip baseline + counter writes.
     - Return `FeedbackResponse(applied=False, previous_label=<that label>, target_request_id=<…>)`.
   Else continue with full apply.

5. **Load customer baseline FOR UPDATE**:
   ```python
   baseline = await CustomerBaseline.load(conn, tenant_id=auth.tenant_id, customer_id=prior['customer_id'], for_update=True)
   ```

6. **Apply baseline writes** (only for `rejected` / `fraud_confirmed`):
   - For each dimension present on prior shipment:
     - `ip_stats` keyed by `str(prior['source_ip'])` — always written (source_ip is NOT NULL)
     - `origin_stats` keyed by `_origin_address_key(prior['origin'])` (same helper booking endpoint uses) — always written
     - `dest_stats` keyed by `_dest_address_key(prior['destination'])` — always written
     - `rejected_email_hmacs` keyed by `prior['email_hmac']` — only if NOT NULL (shipments rows from before 3B.1 migration have NULL email_hmac; new rows from 3B.3+ that supplied an email in the booking request will have it).
     - `rejected_phone_hmacs` keyed by `prior['phone_hmac']` — only if NOT NULL.
   - Call `baseline.add_rejected_observation(key_in=<k>, stat=<stat_name>, ts=payload.feedback_ts)` for each populated dimension.
   - `baseline.save(conn)` (existing helper).
   - **Dimension-skip logging**: when email_hmac or phone_hmac is NULL on prior, increment an observability counter `feedback.dimension_skipped` with label `email_hmac` or `phone_hmac`. Phase 5 surfaces this via structured log. Tests in 3B.6 cover the NULL-skip path.

7. **Update customer counters** via the extracted helper `_compute_counter_deltas(prior_label, new_label) -> (flag_delta, fraud_delta)`:

   Helper signature (placed in `app/api/feedback.py`, importable for unit test):
   ```python
   _REJECTED_SET = frozenset({"rejected", "fraud_confirmed"})

   def _compute_counter_deltas(
       prior_label: str | None, new_label: str
   ) -> tuple[int, int]:
       """Return (flag_delta, fraud_delta) for transitioning from
       prior_label to new_label under label monotonicity.

       Monotonicity guard upstream ensures new_label is "stronger" than
       prior_label; this helper only computes the counter deltas (not the
       monotonicity check itself).
       """
       prior_flagged = prior_label in _REJECTED_SET if prior_label else False
       new_flagged = new_label in _REJECTED_SET
       flag_delta = int(new_flagged) - int(prior_flagged)

       prior_fraud = prior_label == "fraud_confirmed"
       new_fraud = new_label == "fraud_confirmed"
       fraud_delta = int(new_fraud) - int(prior_fraud)

       return flag_delta, fraud_delta
   ```

   Concrete transitions (must match the parametrized test in `tests/unit/test_feedback_counter_transitions.py` — see below):

   | prior | new | flag_delta | fraud_delta | Notes |
   |---|---|---|---|---|
   | None | approved | 0 | 0 | First feedback, no risk signal |
   | None | rejected | +1 | 0 | First feedback flags |
   | None | fraud_confirmed | +1 | +1 | First feedback flags + confirms |
   | approved | rejected | +1 | 0 | Upgrade, flag now |
   | approved | fraud_confirmed | +1 | +1 | Upgrade, flag + confirm |
   | rejected | fraud_confirmed | 0 | +1 | Already flagged, now confirmed |
   | rejected | rejected | 0 | 0 | No-op (monotonicity allows same; counters unchanged) |
   | fraud_confirmed | fraud_confirmed | 0 | 0 | No-op |
   | approved | approved | 0 | 0 | No-op |

   Downgrades (rejected → approved, fraud_confirmed → rejected, fraud_confirmed → approved) are blocked at the upstream monotonicity check — `_compute_counter_deltas` is never called for them. The 3B.7 concurrent-write test covers the race where two upgrades arrive simultaneously (transaction-level isolation must serialize them via `SELECT FOR UPDATE` on the baseline; counters cannot double-increment).

   SQL:
   ```sql
   UPDATE customers SET
       flagged_count = flagged_count + $1,
       fraud_confirmed_count = fraud_confirmed_count + $2
    WHERE id = $3 AND tenant_id = $4
   ```
   Bind `$1 = flag_delta`, `$2 = fraud_delta`. Skip the UPDATE entirely if both deltas are 0 (cosmetic optimization; safe regardless).

8. **INSERT feedback audit row**:
   ```sql
   INSERT INTO feedback (tenant_id, request_id, target_request_id,
                         label, feedback_ts, note, operator_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
   ```
   No `decision_id` column on the pure bootstrap shape — target resolution at step 3 uses `decisions WHERE request_id = $2` which is unique within tenant; downstream queries that need decision details can re-resolve via the same lookup.

9. **Return** `FeedbackResponse(applied=True, previous_label=<prior_label or None>, target_request_id=<…>)`.

**Concurrency consideration**: The endpoint holds `SELECT FOR UPDATE` on the customer's baseline row. A concurrent booking for the same customer also takes that lock — the two serialize. **Test in 3B.7.**

`tests/unit/test_feedback_counter_transitions.py` (in this commit):
Parametrized exhaustive test against the 3-prior × 3-new = 9-cell matrix above (plus the `None` prior = 4-row × 3-col = 12 total cells). Each cell asserts `_compute_counter_deltas(prior, new) == expected`. Downgrade cells (where new is "weaker" than prior) are excluded from this test because they're blocked upstream; a separate test asserts `_label_stronger` returns False for those 3 downgrade cases.

```python
import pytest
from app.api.feedback import _compute_counter_deltas, _label_stronger

@pytest.mark.parametrize("prior,new,expected_flag,expected_fraud", [
    # (prior, new, flag_delta, fraud_delta)
    (None,             "approved",        0,  0),
    (None,             "rejected",        1,  0),
    (None,             "fraud_confirmed", 1,  1),
    ("approved",       "approved",        0,  0),
    ("approved",       "rejected",        1,  0),
    ("approved",       "fraud_confirmed", 1,  1),
    ("rejected",       "rejected",        0,  0),
    ("rejected",       "fraud_confirmed", 0,  1),
    ("fraud_confirmed","fraud_confirmed", 0,  0),
])
def test_counter_deltas(prior, new, expected_flag, expected_fraud):
    assert _compute_counter_deltas(prior, new) == (expected_flag, expected_fraud)

@pytest.mark.parametrize("new,prior_blocking", [
    # downgrade attempts that monotonicity must block
    ("approved", "rejected"),
    ("approved", "fraud_confirmed"),
    ("rejected", "fraud_confirmed"),
])
def test_label_monotonicity_blocks_downgrade(new, prior_blocking):
    assert _label_stronger(new=new, prior=prior_blocking) is False

@pytest.mark.parametrize("new,prior_allowing", [
    ("rejected", None), ("rejected", "approved"),
    ("fraud_confirmed", None), ("fraud_confirmed", "approved"), ("fraud_confirmed", "rejected"),
    ("approved", None),
])
def test_label_monotonicity_allows_upgrade_or_first(new, prior_allowing):
    assert _label_stronger(new=new, prior=prior_allowing) is True
```

That's 9 + 3 + 6 = 18 parametrized test cases — exhaustive coverage of the transition matrix.

`tests/integration/test_feedback_endpoint.py` (in this commit):
1. First feedback for a target with label='approved' → 200, applied=True, previous_label=None. Baseline unchanged. Counter unchanged.
2. First feedback for a target with label='rejected' → 200, applied=True. Baseline.rejected_email_hmacs / ip_stats / origin_stats etc. incremented appropriately. flagged_count += 1.
3. First feedback for a target with label='fraud_confirmed' → 200, applied=True. Same baseline writes. flagged_count += 1, fraud_confirmed_count += 1.
4. Replay same request_id → 200, applied=False, previous_label=<original>. NO double-increment.
5. Different request_id same target, label='rejected' → label='fraud_confirmed' upgrade → 200, applied=True, previous_label='rejected'. flagged_count unchanged (already +1), fraud_confirmed_count += 1.
6. Different request_id same target, label='rejected' → label='approved' downgrade → 200, applied=False, previous_label='rejected'. NO counter changes.
7. Non-existent target_request_id → 404.
8. Cross-tenant: tenant_b token attempting feedback on tenant_a target → 404.
9. Feedback for a modification's request_id (request_type='modification') → 200, applied=True (the resolution query at step 3 joins decisions regardless of request_type).

**Validation**:
- `pytest tests/unit/test_feedback_counter_transitions.py -v` — 18 parametrized cases pass.
- `pytest tests/integration/test_feedback_endpoint.py -v` — 9 tests pass.
- `pytest tests/ --asyncio-mode=auto -q` — full suite green.
- Smoke test booking endpoint: POST a booking with email → assert shipments row carries email_hmac populated (sanity for the new INSERT columns).

**Risk**: **High**. Complex endpoint touching multiple tables in one transaction. Two-tier idempotency interaction with label monotonicity is subtle. The patched booking endpoint INSERT changes a Phase-1 hot path (`app/api/booking.py:165`) — the addition of two new columns to the INSERT must not regress existing booking flow. Reviewer panel mandatory; db-reviewer pays special attention to the booking INSERT change.

**Reversibility**: Medium. Revert removes endpoint and router registration; `app/main.py` and `app/baseline.py` revert cleanly. Existing feedback rows from migration in 3B.1 remain queryable (data preserved).

**Pre-commit verification**: All gates green.

**Observability**: Endpoint emits structured log per request including `applied`, `label`, `previous_label`, `flag_delta`, `fraud_delta`, `dimensions_written` (count). Phase 5 wires to CloudWatch EMF.

**Test changes**: 9 integration tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Never-Skip (new `.py` file under `app/`) + db-reviewer (multi-table transaction) + test-reviewer.

---

## 3B.4 — DSL whitelist + Context derivations for previously-rejected signals

**Theme**: Add 4 new field names to `ALLOWED_CONTEXT_FIELDS` and populate them in `build_context` from the baseline's rejected dicts and stat-dict `r_n` values.

**Files**:
- `app/rules.py` (EDIT — append 4 fields)
- `app/context.py` (EDIT — populate 4 fields in `build_context`)
- `tests/unit/test_context_previously_rejected.py` (NEW)

**Specifics**:

`app/rules.py` additions (after 3A.3's modification fields):
```python
    # ---- Previously rejected (3B) ---------------------------------------
    "email_previously_rejected",   # bool — email_hmac present in baseline.rejected_email_hmacs
    "phone_previously_rejected",   # bool — phone_hmac present in baseline.rejected_phone_hmacs
    "origin_previously_rejected",  # bool — baseline.origin_stats[origin_key].r_n > 0
    "ip_previously_rejected",      # bool — baseline.ip_stats[ip].r_n > 0
```

Total whitelist: 62 (post-3A) + 4 = **66 fields**.

`app/context.py` change inside `build_context` (the existing function, not the modification variant):

After the email/phone Context fields populated around L195-L205 (per verification), add:
```python
ctx["email_previously_rejected"] = email_hmac in baseline.rejected_email_hmacs
ctx["phone_previously_rejected"] = phone_hmac in baseline.rejected_phone_hmacs
origin_entry = baseline.origin_stats.get(origin_key, {})
ctx["origin_previously_rejected"] = float(origin_entry.get("r_n", 0.0)) > 0.0
ip_entry = baseline.ip_stats.get(str(source_ip), {})
ctx["ip_previously_rejected"] = float(ip_entry.get("r_n", 0.0)) > 0.0
```

These derivations are pure dict lookups against the already-loaded baseline — **zero additional SQL**. Modification path (`build_modification_context`) inherits these via the `build_context` wrap.

`tests/unit/test_context_previously_rejected.py`:
1. Empty baseline → all 4 fields False.
2. `baseline.rejected_email_hmacs.add('abc...')` and `email_hmac='abc...'` → `email_previously_rejected=True`.
3. `baseline.origin_stats['123 Main, Boston, MA'] = {'n': 0, 'r_n': 2}` and `origin_key='123 Main, Boston, MA'` → `origin_previously_rejected=True`.
4. `baseline.ip_stats['1.2.3.4'] = {'n': 5, 'r_n': 1}` and `source_ip='1.2.3.4'` → `ip_previously_rejected=True`.
5. Stat entry with `r_n=0` (only approved observations) → False.
6. Stat entry missing from baseline → False (default).

**Validation**:
- `pytest tests/unit/test_context_previously_rejected.py -v` — 6 tests pass.
- `pytest tests/unit/test_dsl*.py tests/unit/test_rules_modification_whitelist.py` — still pass.
- Whitelist count: `assert len(ALLOWED_CONTEXT_FIELDS) == 66`.

**Risk**: **Low-medium**. Touches `build_context` (hot path) and the DSL whitelist. The derivations are pure-Python dict lookups; no SQL added.

**Reversibility**: Easy — revert removes the fields and the lookups.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 6 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**:
- **Scope**: 4 new whitelist fields are populated but no rules reference them yet. No-op until 3B.5.
- **Resolved in**: 3B.5 (4 rules added).

**Reviewer routing**: Standard + security-auditor (DSL field-whitelist + Context hot-path change).

---

## 3B.5 — 4 previously-rejected rules in `app/rules.yaml` + per-rule tests

**Theme**: Append 4 rules to `app/rules.yaml` mirroring freight_risk's catalogue. Per-rule unit tests assert fire / no-fire behavior.

**Files**:
- `app/rules.yaml` (EDIT — append 4 rules)
- `.ai/decisions.md` (EDIT — append "Phase 3B feedback rule rationale" subsection noting weights are sourced from freight_risk catalogue and reaffirming no-tuning-in-Phase-3 policy)
- `tests/unit/test_rules_previously_rejected.py` (NEW)

**Specifics**:

4 rules:

| Name | Condition | Weight | Maturity-sensitive | Source |
|---|---|---|---|---|
| `email_previously_rejected_for_customer` | `email_previously_rejected` | 0.60 | True | freight_risk |
| `phone_previously_rejected_for_customer` | `phone_previously_rejected` | 0.60 | True | freight_risk |
| `origin_previously_rejected_for_customer` | `origin_previously_rejected` | 0.70 | True | freight_risk |
| `ip_previously_rejected_for_customer` | `ip_previously_rejected` | 0.70 | True | freight_risk |

YAML excerpt:
```yaml
  # ---- Previously rejected (Phase 3B) ---------------------------------
  - name: email_previously_rejected_for_customer
    description: "Email matches a previously-rejected contact for this customer"
    condition: "email_previously_rejected"
    weight: 0.60
    maturity_sensitive: true

  - name: phone_previously_rejected_for_customer
    description: "Phone matches a previously-rejected contact for this customer"
    condition: "phone_previously_rejected"
    weight: 0.60
    maturity_sensitive: true

  - name: origin_previously_rejected_for_customer
    description: "Origin address was previously rejected by a reviewer for this customer"
    condition: "origin_previously_rejected"
    weight: 0.70
    maturity_sensitive: true

  - name: ip_previously_rejected_for_customer
    description: "Source IP was previously rejected by a reviewer for this customer"
    condition: "ip_previously_rejected"
    weight: 0.70
    maturity_sensitive: true
```

`tests/unit/test_rules_previously_rejected.py` per rule:
- Fires when `<field>_previously_rejected = True`.
- Does NOT fire when False.
- Maturity downweight applied per Layer 2 (verified via composite test calling production scoring).

8 tests total (2 per rule).

**Validation**:
- `pytest tests/unit/test_rules_previously_rejected.py -v` — 8 tests pass.
- Rule count: `python -c "import yaml; assert len(yaml.safe_load(open('app/rules.yaml'))['rules']) == 79"` — sanity (75 post-3A + 4).

**Risk**: **Medium**. New rules can affect scores on existing fixtures via noisy-OR. Re-run case-1 / case-2 integration tests for regression.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: Triggered rules surface in response payload.

**Test changes**: 8 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Never-Skip (rule add) → standard + test-reviewer.

---

## 3B.6 — End-to-end integration test: booking → feedback → next-booking chain

**Theme**: Integration test demonstrating the feedback endpoint's effect on a subsequent booking — the canonical "fold feedback into baseline so next booking sees the signal" flow.

**Files**:
- `tests/integration/test_feedback_chain_e2e.py` (NEW)

**Specifics**:

Scenarios:

1. **Email rejection fires on next booking**:
   a. POST booking with email `e1@example.com` → ALLOW or REVIEW.
   b. POST feedback (label=rejected, target_request_id=booking.request_id).
   c. POST another booking by same customer with same email `e1@example.com` → triggered_rules contains `email_previously_rejected_for_customer`.

2. **IP rejection fires on next booking**:
   a. POST booking from IP `1.2.3.4`.
   b. POST feedback rejecting it.
   c. POST next booking from same `1.2.3.4` → rule fires.

3. **Origin rejection persists** across email change (origin-only persistence).

4. **fraud_confirmed → flagged_count + fraud_confirmed_count both updated** (queried directly via SELECT).

5. **Approved label does NOT trigger any previously-rejected rule on next booking** (baseline unchanged).

6. **Cross-tenant**: feedback rejecting tenant_a's email does NOT cause `email_previously_rejected` to fire on tenant_b's booking with same email (per-customer dict isolation).

7. **Monotonicity composite**: POST booking → POST feedback rejected (counter +1) → POST same target feedback fraud_confirmed (fraud_confirmed_count +1, flagged_count unchanged). Re-fetch customer row to assert.

8. **Modification of a booking that received feedback**: POST booking → POST feedback rejected → POST modification on the same booking. The modification's Context inherits `email_previously_rejected=True` via the shared build_context path → modification rule scoring includes the previously-rejected weight.

**Validation**:
- `pytest tests/integration/test_feedback_chain_e2e.py -v` — 8 tests pass.
- `pytest tests/ --asyncio-mode=auto -q` — full suite green.

**Risk**: **Medium**. Multi-endpoint chained test; setup is fragile if fixture helpers don't compose cleanly. Reuse Phase 2 helpers where possible.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 8 integration tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: test-only — test-reviewer + senior-engineer + code-flow.

---

## 3B.7 — Concurrent-write integration test: booking + feedback on same customer

**Theme**: Integration test that exercises the `SELECT FOR UPDATE` discipline on `customer_baselines` — concurrent booking POST and feedback POST for the same customer must serialize, not race.

**Files**:
- `tests/integration/test_concurrent_baseline_writes.py` (NEW)

**Specifics**:

1. **Setup**: One customer with a current baseline.
2. **Concurrent test**: Launch (via `asyncio.gather` in the test) a booking POST and a feedback POST that target the same customer. Both endpoints take `SELECT FOR UPDATE` on the customer's baseline row.
3. **Assertion**: Both requests succeed; final baseline state reflects BOTH writes (the booking's `n` increment AND the feedback's `r_n` increment). No deadlock, no lost write.
4. **Negative test** (sanity): without `FOR UPDATE`, the writes would race — but we're not testing that; we're asserting the lock serializes.
5. **Timing test** (loose): the second request waits roughly the duration of the first request's transaction (proves lock was held, not bypassed).

**Validation**:
- `pytest tests/integration/test_concurrent_baseline_writes.py -v` — 5 tests pass.
- Latency: each test under 5s.

**Risk**: **Low**. Test-only. Race conditions in tests can be flaky — use `asyncio.gather` with both tasks awaiting `set_event` or similar to coordinate timing. Fixture must guard against test pollution (clean up customer baseline after).

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 5 integration tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: test-only — test-reviewer + senior-engineer + code-flow.

---

## Batch 3B summary table

| Commit | Theme | Files | Tests added | Risk | Reviewer panel |
|---|---|---|---|---|---|
| 3B.1 | Feedback table migration | 1 new alembic | 0 (manual round-trip) | Medium | Never-Skip + db-reviewer |
| 3B.2 | Pydantic feedback models | `app/models.py`, 1 new test | 5 | Low | Standard + test-reviewer |
| 3B.3 | Endpoint route + wiring + booking INSERT patch | `app/api/feedback.py`, `app/main.py`, `app/baseline.py`, `app/api/booking.py`, 2 new tests | 9 integration + 18 parametrized unit = 27 | High | Never-Skip + db-reviewer + test-reviewer |
| 3B.4 | DSL whitelist + Context derivations | `app/rules.py`, `app/context.py`, 1 new test | 6 | Low-medium | Standard + security-auditor |
| 3B.5 | 4 previously-rejected rules + tests | `app/rules.yaml`, `.ai/decisions.md`, 1 new test | 8 | Medium | Never-Skip + test-reviewer |
| 3B.6 | E2E feedback chain integration test | 1 new test | 8 | Medium | test-reviewer + senior + code-flow |
| 3B.7 | Concurrent writes integration test | 1 new test | 5 | Low | test-reviewer + senior + code-flow |
| **Total** | | | **59 new tests** | | |

Expected test count at end of Batch 3B: **494 + 59 = 553 tests**.

Rule count at end of Batch 3B: **75 + 4 = 79 rules**.

ALLOWED_CONTEXT_FIELDS count at end of Batch 3B: **62 + 4 = 66 fields**.

Migrations count at end of Batch 3B: **4** (0001, 0002, 0003, 0004).
