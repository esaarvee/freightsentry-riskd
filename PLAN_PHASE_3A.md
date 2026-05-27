# Phase 3 — Batch 3A Plan — Modification endpoint stack

> **Status (2026-05-27)**: Pending operator approval before any 3A execution. Operator has approved 4-plans-upfront delivery and the substantive scope decisions absorbed below (feedback-schema migration deferred to 3B; modification persistence shares decisions table via request_type discriminator).

Batch 3A lands the `POST /api/v1/shipments/modification/evaluate` endpoint end-to-end: schema delta on `decisions` to add the `request_type` discriminator, Pydantic request/response models, four modification-specific Context signals, modification-velocity SQL helper, the endpoint route mirroring booking's transaction discipline, eight modification-specific rules with per-rule unit tests, and an end-to-end integration test of the modification path.

Target: 8 commits.

---

## Decisions absorbed (batch-relevant subset)

| Decision | Value | Source |
|---|---|---|
| Endpoint URL | `POST /api/v1/shipments/modification/evaluate` | Phase 3 bootstrap |
| Decision space | ALLOW / REVIEW / BLOCK only — REVERT/CANCEL deferred to v2+ | Phase 3 bootstrap |
| Persistence | Single `decisions` table with `request_type TEXT NOT NULL DEFAULT 'booking'` discriminator; existing rows backfill to `'booking'`. **Not** a parallel `modifications` table. | Phase 3 bootstrap |
| Decisions table current schema | `id, tenant_id, shipment_id, request_id, score, decision, classification, risk_level, triggered_rules, risk_factors, created_at` with `UNIQUE (tenant_id, request_id)` at `alembic/versions/0001_initial.py:129-149`. No `request_type` column today (grep confirms). | Verification §3 |
| Idempotency | On `(tenant_id, request_id)` of the modification's own request_id (NOT the original booking's). Mirrors booking endpoint's idempotency check at `app/api/booking.py:50-75`. | Phase 3 bootstrap |
| Modification signal shape | 4 new fields populated as part of a `build_context` variant — `build_modification_context` — that takes a `prior_shipment_row` + `prior_decision_row` and adds the 4 modification fields on top of the standard 56. NOT a re-implementation of `build_context`; calls it then layers modification-specific signals. | Phase 3 bootstrap + verification §2 |
| Modification rule count | 8 rules (within bootstrap's 8-12 target; trimmed for landability) | Phase 3 bootstrap |
| Modification rule weights | Operator judgment with explicit rationale notes in `.ai/decisions.md`. **No tuning in Phase 3** — Phase 6 staging replay calibrates. | Phase 3 bootstrap + memory entry `feedback_no_weight_tuning_phase2` |
| Modification velocity SQL | One additional sequential await on the txn connection — Context build runs 9 awaits today (verification §2), modification path runs 10. Latency-budget watch noted; no asyncio.gather refactor in Phase 3. | Watch points |
| Modification reuses `score()` unchanged | `app/scoring.py::score` is invariant in Phase 3; modification endpoint passes the modification-Context dict into the same scoring entry point. | Phase 3 DO NOT |
| DSL extension | New field names added to `ALLOWED_CONTEXT_FIELDS` at `app/rules.py:30-97`. AST node whitelist at `app/dsl.py:35-90` is unchanged. | Phase 3 DO |
| No FreightCom / freight_risk modification reference | Grep of `/Users/drshott/PycharmProjects/github_fc/freightcom-risk` and `/Users/drshott/PycharmProjects/miscProj/freight_risk` returned no modification-related rules or signals (verification §7, §8). Greenfield design. | Verification |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md. Pre-commit hooks active (`ruff check --fix`, `ruff format`, `mypy app/`, `pytest tests/unit/ -x --no-header -q`).
- Reviewer routing per CLAUDE.md triage gate:
  - 3A.1 (migration, `decisions.request_type`): **Never-Skip** category (schema/migration) → standard panel including db-reviewer.
  - 3A.2 (Pydantic models): standard panel (new Pydantic classes are not trivial; touch `app/models.py`).
  - 3A.3 (`ALLOWED_CONTEXT_FIELDS` extension + DSL test): standard panel including security-auditor (DSL evaluator is sandbox; Never-Skip via "any change to the DSL evaluator" — but here we touch the field whitelist, not the AST evaluator. Treat as standard.)
  - 3A.4 (`build_modification_context`): standard panel; new code path in `app/context.py`.
  - 3A.5 (modification velocity SQL helper): standard panel; new SQL touching `shipments` table.
  - 3A.6 (endpoint route): standard panel; **new `.py` file under `app/`** → Never-Skip per "any commit that introduces a new `.py` file under `app/`".
  - 3A.7 (8 modification rules in `app/rules.yaml` + tests): Never-Skip per "any change to `app/rules.yaml` weights, thresholds, or conditions that adds or removes a rule" → standard panel + test-reviewer.
  - 3A.8 (integration test): test-only → test-reviewer + senior-engineer + code-flow (lightweight per triage gate "ONLY test file additions").

- Reviewer-invocation slice template:
  > `Plan file: PLAN_PHASE_3A.md, current commit: 3A.N (<title>), upcoming commits: 3A.{N+1} through 3A.8 sections. Read only those sections.`

---

## Cross-batch dependencies

- **Consumes from Phase 1**: `decisions` table at `alembic/versions/0001_initial.py:129-149`; `shipments` table at `:105-123` (+ `destination_hmac` added in `alembic/versions/0002_shipments_destination_hmac.py:31-39`); auth machinery at `app/auth.py`; DB connection + tenant-context machinery at `app/db.py`.
- **Consumes from Phase 2**: `app/context.py::build_context` (9-await Context builder) and `app/scoring.py::score()` (unchanged in Phase 3); `app/baseline.py::CustomerBaseline.load(...)` row-lock pattern.
- **Consumed by 3B**: `decisions.request_type` column makes `(decisions WHERE request_type='modification')` queryable; the feedback endpoint resolves any prior decision (booking OR modification) via the same shipment_id FK.
- **Consumed by 3C**: New endpoint and any new query callsites are inputs to the RLS audit query inventory.
- **Consumed by 3D**: Modification chain (`booking → modification → feedback`) integration test composes 3A + 3B endpoints.

---

## 3A.1 — Migration: `decisions.request_type` discriminator

**Theme**: Additive migration adding `decisions.request_type TEXT NOT NULL DEFAULT 'booking'` with explicit backfill of existing rows. Pre-Phase-5 the only running app is the booking endpoint, so default-and-backfill is concurrency-safe.

**Files**:
- `alembic/versions/0003_decisions_request_type.py` (NEW)

**Specifics**:

```python
"""Add decisions.request_type discriminator for booking vs modification

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-27
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE decisions
            ADD COLUMN request_type text NOT NULL DEFAULT 'booking';

        COMMENT ON COLUMN decisions.request_type IS
            'One of booking | modification; discriminates which evaluate endpoint produced this decision. DEFAULT booking preserved as a safety net — both endpoints supply request_type explicitly in 3A.6.';

        -- Existing rows pick up the DEFAULT in the ADD COLUMN step. The
        -- second statement is an explicit no-op-for-existing-rows but
        -- documents the intent for audit.
        UPDATE decisions SET request_type = 'booking' WHERE request_type IS NULL;

        -- DEFAULT 'booking' INTENTIONALLY RETAINED. Rationale: with only
        -- two code paths (booking and modification endpoints) and both
        -- supplying request_type explicitly per 3A.6, the silent-omission
        -- risk is theoretical. Retaining the DEFAULT means this migration
        -- is independently safe to land before 3A.6 — booking endpoint
        -- INSERTs continue to succeed using the column default. No
        -- declared break required between 3A.1 and 3A.6.

        -- Constraint: only the two known values.
        ALTER TABLE decisions ADD CONSTRAINT ck_decisions_request_type
            CHECK (request_type IN ('booking', 'modification'));
        """
    )
    op.execute(
        "CREATE INDEX ix_decisions_tenant_request_type ON decisions (tenant_id, request_type);"
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS ix_decisions_tenant_request_type;
        ALTER TABLE decisions DROP CONSTRAINT IF EXISTS ck_decisions_request_type;
        ALTER TABLE decisions DROP COLUMN IF EXISTS request_type;
        """
    )
```

**Validation**:
- `docker compose exec app alembic upgrade head` clean
- `docker compose exec app alembic downgrade -1 && docker compose exec app alembic upgrade head` round-trip clean
- `docker compose exec postgres psql -U riskd -d riskd -c '\d decisions'` shows the new column, CHECK constraint, index
- `pytest tests/ --asyncio-mode=auto -q` — existing 432 tests pass (the booking endpoint INSERT does NOT yet write `request_type` — see Declared breaks).

**Risk**: **Low-medium**. Additive migration with backfill in single transaction; safe because pre-launch with no concurrent app. The booking endpoint's existing INSERT at `app/api/booking.py:194-211` does not supply `request_type` but the retained DEFAULT 'booking' covers it — INSERTs continue to succeed unchanged between 3A.1 and 3A.6.

**Reversibility**: Easy — `downgrade()` drops the column, constraint, index. No data loss because existing rows simply lose the `'booking'` label (recoverable from `created_at` ordering since pre-Phase-3 all rows were bookings).

**Pre-commit verification**: `ruff check`, `ruff format`, `mypy app/`, unit tests pass — booking endpoint INSERTs still succeed via the column DEFAULT.

**Observability**: N/A — schema only. Phase 5 will surface request_type into structured logs.

**Test changes**: None in this commit (round-trip verified manually via `alembic upgrade/downgrade`). Schema verification test added in 3A.6's commit alongside the endpoint update.

**Rollback plan**: `alembic downgrade -1` + `git revert`. Coupled-reversibility concern from prior plan version is removed — 3A.1 and 3A.6 are now independently revertible.

**Declared breaks**: None. The retained DEFAULT 'booking' makes 3A.1 independently safe to land before 3A.6. (Earlier plan version dropped the DEFAULT after backfill, which created a declared break and potential `--no-verify` requirement; revised per operator feedback to preserve the DEFAULT as a safety net.)

**Reviewer routing**: Never-Skip (migration). Standard panel + db-reviewer.

---

## 3A.2 — Pydantic models for modification request/response

**Theme**: Add `ModificationRequest` and `ModificationResponse` Pydantic v2 models in `app/models.py`. Response shape mirrors `BookingResponse` (same field set; the scoring entry point is shared so the response shape is identical).

**Files**:
- `app/models.py` (EDIT)
- `tests/unit/test_models_modification.py` (NEW)

**Specifics**:

After existing `BookingResponse` at `app/models.py:90-97`, append:

```python
ModificationType = Literal["destination", "value", "recipient", "service_level", "pickup_time"]


class ModificationUser(BaseModel):
    """Modification-time user — may differ from original booking's user."""
    external_id: str = Field(..., min_length=1, max_length=128)


class ModificationRequest(BaseModel):
    """POST /api/v1/shipments/modification/evaluate payload."""
    request_id: str = Field(..., min_length=1, max_length=128)
    original_request_id: str = Field(..., min_length=1, max_length=128)
    modification_ts: datetime
    modification_type: ModificationType
    new_value: dict[str, Any]  # shape depends on modification_type; validated downstream
    source_ip: IPvAnyAddress | None = None
    user: ModificationUser | None = None
    reason: str | None = Field(None, max_length=512)


class ModificationResponse(BaseModel):
    """Same shape as BookingResponse — scoring infrastructure shared."""
    request_id: str
    decision: Literal["ALLOW", "REVIEW", "BLOCK"]
    score: float = Field(..., ge=0.0, le=1.0)
    classification: Literal["GREEN", "YELLOW", "RED"]
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    triggered_rules: list[str]
    risk_factors: list[RiskFactor]
    # Layer 2 / maturity disclosure mirrors BookingResponse internal fields if exposed there
    account_prior: float | None = None
    signal_score: float | None = None
    maturity: float | None = None
```

`tests/unit/test_models_modification.py` covers:
1. `ModificationRequest` rejects empty `request_id`, empty `original_request_id`.
2. `modification_type` accepts only the 5 enumerated values.
3. `source_ip` accepts IPv4 and IPv6, rejects garbage.
4. `new_value` parses an empty dict, parses dicts with arbitrary keys (downstream validates shape).
5. `ModificationResponse.decision` rejects values outside the 3-tuple.

**Validation**:
- `pytest tests/unit/test_models_modification.py -v` — all 5 tests pass.
- `mypy app/` strict clean.
- `ruff check app/ tests/` clean.

**Risk**: **Low**. Pydantic model additions; no behavioral change. Validators are standard Pydantic v2 idioms.

**Reversibility**: Easy — revert removes the classes.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 5 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Standard panel + test-reviewer (new test file).

---

## 3A.3 — Extend `ALLOWED_CONTEXT_FIELDS` with 4 modification fields

**Theme**: Add the 4 modification signal names to the DSL field whitelist at `app/rules.py:30-97`. AST whitelist at `app/dsl.py:35-90` is untouched. Adds a smoke test that loads rules.yaml under the extended whitelist without error.

**Files**:
- `app/rules.py` (EDIT — add 4 fields to the `ALLOWED_CONTEXT_FIELDS` frozenset)
- `tests/unit/test_rules_modification_whitelist.py` (NEW)

**Specifics**:

After the existing email/phone block at `app/rules.py:~95`, add:

```python
    # ---- Modification (3A) ---------------------------------------------
    "modification_time_since_booking",   # Literal["within_30_min", "within_1_hour", "within_24_hours", "1_to_7_days", "over_7_days"]
    "modification_magnitude",            # float in [0.0, +inf); fraction for value, 0/1 for categorical
    "modification_direction",            # Literal["familiar", "unfamiliar", "blocked", "unknown"]
    "modification_velocity_1h",          # int — modifications this customer made in last 1h
    "modification_velocity_24h",         # int — modifications this customer made in last 24h
    "modification_type",                 # Literal[5 values] — exposed for rule conditions like 'modification_type == "destination"'
```

Note: 6 new fields (4 modification semantics + the 2 velocity windows breaking out the bootstrap's single `modification_velocity` into the 1h/24h pair the rules will actually condition on + the type literal). Total whitelist: 56 → 62.

`tests/unit/test_rules_modification_whitelist.py`:
1. Load `app/rules.yaml` via `app.rules.load_rules` — succeeds (no unknown-field errors).
2. Whitelist contains each of the 6 new field names.
3. DSL parses a synthetic rule string referencing each new field — succeeds.
4. DSL rejects a synthetic rule referencing `modification_nonsense_field` — fails with `UnknownField` (or whatever the loader's error class is).

**Validation**:
- `pytest tests/unit/test_rules_modification_whitelist.py -v` — 4 tests pass.
- `pytest tests/unit/test_dsl*.py -v` (existing DSL tests) — still pass; AST is unchanged.
- `python -c "from app.rules import ALLOWED_CONTEXT_FIELDS; assert len(ALLOWED_CONTEXT_FIELDS) == 62"` — sanity.

**Risk**: **Low**. Additive frozenset entries. The risk is a typo (e.g. extra trailing comma in the wrong place would change tuple/set syntax). `mypy` catches.

**Reversibility**: Easy — revert removes the entries.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 4 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**:
- **Scope**: The 6 new whitelist fields are not yet populated by any `build_context` variant — they're declared in the whitelist but no Context dict carries them yet. Rules referencing these names would fail at evaluation with "field not in context".
- **Resolved in**: 3A.4 (`build_modification_context` populates `modification_time_since_booking`, `modification_magnitude`, `modification_direction`, `modification_type`) + 3A.5 (velocity helpers populate `modification_velocity_1h`, `modification_velocity_24h`). 3A.7 adds the rules that reference them.
- **Mitigation**: No production rules reference these fields until 3A.7. Pre-commit unit tests on the DSL parse the synthetic rule strings introduced in this commit's tests, not production rules.

**Reviewer routing**: Standard panel. Note: this commit touches the DSL field-whitelist surface which is Never-Skip-adjacent (the DSL evaluator itself per `app/dsl.py` is Never-Skip; the field whitelist is a related surface). Security-auditor must confirm the new field names cannot be used to exfiltrate data or bypass the field allowlist mechanism.

---

## 3A.4 — `build_modification_context` — modification signal derivations

**Theme**: Add a `build_modification_context` function in `app/context.py` that wraps `build_context` and layers on the 4 non-SQL modification fields. The function takes a `prior_shipment_row` and `prior_decision_row` (already loaded by the endpoint) plus the modification payload, computes time-bucket, magnitude, direction, and modification_type, and returns the extended Context dict. Velocity SQL deferred to 3A.5.

**Files**:
- `app/context.py` (EDIT — add new function after existing `build_context` at L54-L207)
- `tests/unit/test_context_modification.py` (NEW)

**Specifics**:

```python
# After existing build_context, append:

MODIFICATION_TIME_BUCKETS: Final = (
    (timedelta(minutes=30), "within_30_min"),
    (timedelta(hours=1), "within_1_hour"),
    (timedelta(hours=24), "within_24_hours"),
    (timedelta(days=7), "1_to_7_days"),
)


def _modification_time_bucket(*, booking_ts: datetime, modification_ts: datetime) -> str:
    delta = modification_ts - booking_ts
    if delta < timedelta(0):
        # Modification timestamp earlier than original booking — anomalous;
        # treat as 'within_30_min' (most suspicious bucket) and let rules decide.
        return "within_30_min"
    for threshold, label in MODIFICATION_TIME_BUCKETS:
        if delta <= threshold:
            return label
    return "over_7_days"


def _modification_magnitude(*, modification_type: str, new_value: dict[str, Any], prior_shipment: asyncpg.Record) -> float:
    if modification_type == "value":
        old = float(prior_shipment["value"])
        new = float(new_value.get("value", old))
        if old <= 0:
            return 0.0
        return abs(new - old) / old
    if modification_type == "destination":
        # 1.0 if destination_hmac changes, 0.0 otherwise.
        old_hmac = prior_shipment["destination_hmac"]
        new_addr = new_value.get("destination") or {}
        # The HMAC computation lives in app/signal_helpers; reuse rather than
        # re-implement (per Phase 2 false-pass-test lesson).
        from app.signal_helpers import hmac_destination
        new_hmac = hmac_destination(new_addr)
        return 1.0 if new_hmac != old_hmac else 0.0
    # recipient / service_level / pickup_time: categorical change, magnitude=1.0 if any change.
    return 1.0


def _modification_direction(*, modification_type: str, new_value: dict[str, Any], baseline: CustomerBaseline) -> str:
    if modification_type != "destination":
        return "unknown"
    new_addr = new_value.get("destination") or {}
    from app.signal_helpers import hmac_destination
    new_hmac = hmac_destination(new_addr)
    # Check destination familiarity against this customer's baseline.dest_stats
    if new_hmac in baseline.dest_stats:
        return "familiar"
    # global_blocked_vectors is the Phase 6+ table — for Phase 3 always treat
    # as not-blocked. Stub returns False.
    return "unfamiliar"


async def build_modification_context(
    conn: asyncpg.Connection,
    *,
    tenant_id: int,
    customer_id: int,
    customer_row: asyncpg.Record,
    enricher: Enricher,
    payload: ModificationRequest,
    prior_shipment_row: asyncpg.Record,
    prior_decision_row: asyncpg.Record,
    as_of: date | None = None,
) -> tuple[dict[str, Any], CustomerBaseline, EnrichmentRow]:
    """Build context for a modification request.

    Synthesizes a booking-shaped payload from prior_shipment_row, calls
    build_context to populate the standard 56 fields, then layers on the
    6 modification-specific fields.

    Modification velocity fields are populated by build_modification_context
    via the new app.velocity.count_user_modifications_1h / _24h calls (3A.5).
    """
    # Reuse build_context by synthesizing a BookingRequest from prior_shipment_row + payload.source_ip
    synthetic_booking = _booking_from_prior_shipment(prior_shipment_row, override_source_ip=payload.source_ip)
    destination_hmac = prior_shipment_row["destination_hmac"]  # destination of the ORIGINAL booking
    ctx, baseline, enrichment = await build_context(
        conn,
        tenant_id=tenant_id,
        customer_id=customer_id,
        customer_row=customer_row,
        enricher=enricher,
        payload=synthetic_booking,
        destination_hmac=destination_hmac,
        as_of=as_of,
    )

    booking_ts = prior_shipment_row["booking_ts"]
    ctx["modification_time_since_booking"] = _modification_time_bucket(
        booking_ts=booking_ts, modification_ts=payload.modification_ts,
    )
    ctx["modification_magnitude"] = _modification_magnitude(
        modification_type=payload.modification_type,
        new_value=payload.new_value,
        prior_shipment=prior_shipment_row,
    )
    ctx["modification_direction"] = _modification_direction(
        modification_type=payload.modification_type,
        new_value=payload.new_value,
        baseline=baseline,
    )
    ctx["modification_type"] = payload.modification_type
    # Velocity fields populated by 3A.5 (placeholder None — rules guard via condition)
    ctx["modification_velocity_1h"] = 0
    ctx["modification_velocity_24h"] = 0
    return ctx, baseline, enrichment


def _booking_from_prior_shipment(
    shipment: asyncpg.Record,
    *,
    override_source_ip: IPv4Address | IPv6Address | None,
) -> BookingRequest:
    """Reconstruct a BookingRequest-shaped object from a prior shipments row."""
    # Implementation detail: extract fields from shipment record and build a
    # BookingRequest. Used only for the build_context call; not persisted.
    ...
```

`tests/unit/test_context_modification.py`:
1. `_modification_time_bucket` boundary: exactly 30 min → `"within_30_min"`; 31 min → `"within_1_hour"`.
2. Boundary: exactly 24h → `"within_24_hours"`; 7d+1s → `"over_7_days"`.
3. Negative delta (modification_ts < booking_ts) → `"within_30_min"` (anomalous-but-bucketed).
4. `_modification_magnitude` for `value`: old=1000, new=1500 → 0.5; old=0 → 0.0 (no divide-by-zero).
5. `_modification_magnitude` for `destination`: HMAC change → 1.0; no change → 0.0.
6. `_modification_direction`: destination HMAC in `baseline.dest_stats` → `"familiar"`; not present → `"unfamiliar"`; non-destination type → `"unknown"`.
7. Time-bucket cross-TZ test: production code uses `datetime.now(UTC)` consistently; test uses `datetime.now(UTC)`. **Watch point applied** — no `date.today()` / `current_date` mixing (Phase 2 lesson).

**Validation**:
- `pytest tests/unit/test_context_modification.py -v` — 7 tests pass.
- `mypy app/context.py` — strict clean.
- `ruff check app/context.py tests/unit/test_context_modification.py` — clean.

**Risk**: **Medium**. New function in a hot-path module; integration with `build_context` must not break the booking flow. Tests for build_context proper are unchanged and must still pass. Synthesizing a BookingRequest from a stored shipments row is the risky part — the test data shape must match production-stored shape.

**Reversibility**: Easy — revert removes the new functions; booking flow untouched.

**Pre-commit verification**: All gates green.

**Observability**: N/A in this commit; modification endpoint route in 3A.6 adds structured-log emission.

**Test changes**: 7 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**:
- **Scope**: `modification_velocity_1h` and `modification_velocity_24h` are populated with placeholder `0`. Rules conditioning on these would never fire.
- **Resolved in**: 3A.5 wires the real velocity SQL.

**Reviewer routing**: Standard panel.

---

## 3A.5 — Modification velocity SQL helpers

**Theme**: Add `count_user_modifications_1h` and `count_user_modifications_24h` to `app/velocity.py`. Both query `decisions WHERE request_type='modification' AND customer_id=... AND created_at > ...`. Wire them into `build_modification_context`.

**Files**:
- `app/velocity.py` (EDIT — append two new functions)
- `app/context.py` (EDIT — replace placeholder `0` with actual SQL calls)
- `tests/unit/test_velocity_modifications.py` (NEW)

**Specifics**:

`app/velocity.py` additions (matching pattern at L18-L70):

```python
async def count_user_modifications_1h(
    conn: asyncpg.Connection,
    *,
    tenant_id: int,
    customer_id: int,
    as_of: datetime | None = None,
) -> int:
    """Count modification decisions for this customer in the last 1h.

    Uses decisions.request_type='modification' as the discriminator. Joins
    via the shipments FK to filter by customer_id.
    """
    as_of = as_of or datetime.now(UTC)
    cutoff = as_of - timedelta(hours=1)
    return await conn.fetchval(
        """
        SELECT count(*)
          FROM decisions d
          JOIN shipments s ON s.id = d.shipment_id AND s.tenant_id = d.tenant_id
         WHERE d.tenant_id = $1
           AND s.customer_id = $2
           AND d.request_type = 'modification'
           AND d.created_at > $3
        """,
        tenant_id, customer_id, cutoff,
    )


async def count_user_modifications_24h(
    conn: asyncpg.Connection,
    *,
    tenant_id: int,
    customer_id: int,
    as_of: datetime | None = None,
) -> int:
    """Count modification decisions for this customer in the last 24h."""
    as_of = as_of or datetime.now(UTC)
    cutoff = as_of - timedelta(hours=24)
    return await conn.fetchval(
        """
        SELECT count(*)
          FROM decisions d
          JOIN shipments s ON s.id = d.shipment_id AND s.tenant_id = d.tenant_id
         WHERE d.tenant_id = $1
           AND s.customer_id = $2
           AND d.request_type = 'modification'
           AND d.created_at > $3
        """,
        tenant_id, customer_id, cutoff,
    )
```

`app/context.py` change inside `build_modification_context`: replace the placeholder lines with two sequential `await` calls (this brings total awaits in the modification path to **11** — 9 inherited from build_context + 2 modification velocity. Latency-budget noted; per Phase 3 watch point, the fix path is separate-pool connections in Phase 5, not asyncio.gather on the same connection.).

`tests/unit/test_velocity_modifications.py`:
1. With no prior modification decisions, count_user_modifications_1h returns 0.
2. With 3 modification decisions for this customer in last 30min, returns 3.
3. With 1 modification decision for a DIFFERENT customer in last 1h, returns 0 (customer scope).
4. With 1 modification decision for this customer in a DIFFERENT tenant, returns 0 (tenant scope — explicit `WHERE tenant_id`).
5. With 5 modification decisions for this customer in last 25h, count_user_modifications_24h returns 5; count_user_modifications_1h returns 0.
6. Cross-TZ: as_of in UTC, decisions.created_at stored in UTC, cutoff arithmetic in UTC. **Watch point applied.**
7. INDEX HINT: query uses `ix_decisions_tenant_request_type_created` (introduced in 3A.1 per db-reviewer suggestion; 3-column index `(tenant_id, request_type, created_at)` for range scan on the recency cutoff) — EXPLAIN ANALYZE in a separate manual-verify step (not asserted in unit test, but noted in commit message).

**Validation**:
- `pytest tests/unit/test_velocity_modifications.py -v` — 6 tests pass.
- `mypy app/` strict clean.
- `EXPLAIN ANALYZE` on the modification velocity queries — manual verify uses `ix_decisions_tenant_request_type_created` (logged in commit message).

**Risk**: **Medium**. New SQL joining 2 tables. Performance depends on the index added in 3A.1. If integration tests flag latency, the index strategy may need revision.

**Reversibility**: Easy — revert removes the helpers; `build_modification_context` reverts to placeholder.

**Pre-commit verification**: All gates green.

**Observability**: N/A in this commit.

**Test changes**: 6 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Standard panel + db-reviewer (new SQL touching tenant-scoped tables; db-reviewer confirms index use, tenant filter, query plan reasonableness).

---

## 3A.6 — Modification endpoint route + booking endpoint `request_type` fix

**Theme**: Add `app/api/modification.py` with the `POST /api/v1/shipments/modification/evaluate` route mirroring booking endpoint discipline (auth, transaction, idempotency, build context, score, persist, return). Also patches `app/api/booking.py` to supply `request_type='booking'` in its INSERT (resolves the 3A.1 declared break). Registers the new router in `app/main.py`.

**Files**:
- `app/api/modification.py` (NEW)
- `app/api/booking.py` (EDIT — INSERT statement adds `request_type` column)
- `app/main.py` (EDIT — register new router)
- `tests/integration/test_modification_endpoint.py` (NEW)

**Specifics**:

`app/api/modification.py` (~150 lines) mirrors `app/api/booking.py` structure:

1. **Auth**: `Depends(require_api_token)` → `AuthContext`.
2. **Transaction**: `async with get_conn() as conn, conn.transaction():`
3. **Tenant context**: `await set_tenant_id(conn, auth.tenant_id)`.
4. **Idempotency check**: `SELECT decision, score, ... FROM decisions WHERE tenant_id = $1 AND request_id = $2 AND request_type = 'modification'`. If hit, return prior decision.
5. **Load prior decision + shipment**: `SELECT d.*, s.* FROM decisions d JOIN shipments s ON ... WHERE d.tenant_id = $1 AND d.request_id = $2 AND d.request_type = 'booking'`. If no row, 404 "Original booking not found". If found but `request_type='modification'`, 422 "Cannot modify a modification" (per scope: "Modification of modifications — explicitly out").
6. **Load customer + baseline**: same pattern as booking, `SELECT FOR UPDATE` on baseline.
7. **Build modification context**: `await build_modification_context(...)`.
8. **Score**: `result = score(rules=loaded_rules, ctx=ctx, baseline=baseline)` — unchanged scoring entry point.
9. **Persist**: INSERT shipment-shaped record? **No** — modifications do NOT create a new shipments row; they reference the prior shipment via the decision's shipment_id. Decision INSERT references `shipment_id = prior_shipment_row['id']` and sets `request_type='modification'`.
10. **Update customer counters**: same pattern as booking (`flagged_count` if REVIEW, `fraud_confirmed_count` not touched here — that's feedback's job).
11. **Return** `ModificationResponse`.

`app/api/booking.py` patch — INSERT at L194-L211 adds `request_type` column explicitly (no longer relying on the migration DEFAULT for correctness; explicit-write makes the intent visible at the call site):
```python
await conn.execute(
    """
    INSERT INTO decisions (
        tenant_id, shipment_id, request_id, request_type,
        score, decision, classification, risk_level,
        triggered_rules, risk_factors
    ) VALUES ($1, $2, $3, 'booking', $4, $5, $6, $7, $8, $9)
    """,
    ...
)
```

The 3A.1 migration's DEFAULT 'booking' remains in place as a safety net. The explicit `request_type='booking'` literal in this INSERT makes the discriminator visible at the call site, parallel to the modification endpoint's `'modification'` literal.

`app/main.py` change: add `app.include_router(modification.router)` near the booking router registration.

`tests/integration/test_modification_endpoint.py` covers (test-only, full DB integration):
1. Modification of an existing booking returns ALLOW for a low-risk modification (small value change, familiar destination).
2. Modification with high-magnitude value increase + within_30_min triggers REVIEW or BLOCK.
3. Modification of a non-existent original returns 404.
4. Modification with `request_id` of a prior modification (modify-a-modification) returns 422.
5. Replay of the same `request_id` returns the prior decision (idempotency).
6. Cross-tenant: tenant_b's token attempting to modify tenant_a's booking returns 404 (per Phase 2B.6 isolation pattern; **explicit cross-tenant test included** per Phase 3 DO).
7. Modification velocity rule (planned in 3A.7) does NOT fire here — this test asserts the endpoint flow, not the rule set.

**Validation**:
- `pytest tests/integration/test_modification_endpoint.py -v` — 7 tests pass.
- `pytest tests/ --asyncio-mode=auto -q` — full suite green; booking integration tests now pass because INSERT supplies `request_type`.
- `curl localhost:8000/api/v1/shipments/modification/evaluate -H 'Authorization: Bearer ...' -d '{...}'` — smoke test in dev (manual; logged in commit message).

**Risk**: **Medium-high**. New endpoint touching tenant-scoped tables + new code path in `app/api/`. Reviewer panel mandatory.

**Reversibility**: Easy. Revert removes the endpoint and the booking endpoint patch. The booking endpoint reverts to NOT supplying `request_type` and continues to succeed via the 3A.1 migration's retained DEFAULT 'booking'. 3A.1 and 3A.6 are now independently revertible.

**Pre-commit verification**: All gates green (after the fix to booking, unit tests fully pass).

**Observability**: Modification endpoint emits the same structured log as booking with `request_type=modification` added to the log fields. Phase 5 will wire to CloudWatch EMF; for now, JSON log only.

**Test changes**: 7 integration tests.

**Rollback plan**: `git revert 3A.6`. Booking endpoint reverts to relying on the column DEFAULT (still safe).

**Declared breaks**: None.

**Reviewer routing**: Never-Skip (new `.py` file under `app/`). Standard panel including db-reviewer (new INSERT path) + test-reviewer (integration test).

---

## 3A.7 — 8 modification rules in `app/rules.yaml` + per-rule unit tests

**Theme**: Add 8 modification-specific rules to `app/rules.yaml`. Each rule's weight is chosen from operator judgment, anchored to Phase 2 weight bands for similar-severity rules. Rationale documented inline as YAML comment and in `.ai/decisions.md`. Per-rule unit tests assert the rule fires under expected conditions and doesn't fire otherwise.

**Files**:
- `app/rules.yaml` (EDIT — append 8 rules after existing 67)
- `.ai/decisions.md` (EDIT — document modification weight rationale)
- `tests/unit/test_rules_modification.py` (NEW — uses the existing `tests/unit/conftest.py` rule-test helpers extracted in 2C)

**Specifics**:

8 modification rules (final names, weights, conditions):

| # | Name | Condition | Weight | Maturity-sensitive | Rationale |
|---|---|---|---|---|---|
| 1 | `modification_within_30_min_value_increase` | `modification_type == "value" AND modification_time_since_booking == "within_30_min" AND modification_magnitude > 0.2` | 0.65 | False | Hard signal — value jacking immediately after booking is a known fraud pattern. Weight band: similar to `vpn_high_value` (0.55-0.65 range). |
| 2 | `modification_destination_change_pre_pickup` | `modification_type == "destination" AND modification_time_since_booking == "within_24_hours" AND modification_direction == "unfamiliar"` | 0.55 | True | Re-routing pre-pickup to an unknown address is the classic re-shipping fraud. Maturity-sensitive because dormant-but-legit customers may correct addresses. |
| 3 | `modification_high_velocity_1h` | `modification_velocity_1h > 3` | 0.70 | False | High-frequency modifications within 1h are a campaign signal regardless of customer age. |
| 4 | `modification_high_velocity_24h` | `modification_velocity_24h > 10` | 0.45 | True | 24h is a softer band; maturity-sensitive because some operators batch-edit. |
| 5 | `modification_low_trust_customer` | `trust_score < 0.3 AND modification_type == "destination"` | 0.55 | False | Low-trust customers changing destination is a compounding signal. |
| 6 | `modification_dormant_customer` | `is_abnormally_dormant AND modification_type == "destination"` | 0.60 | True | Dormancy window + destination change is the case-1 ATO pattern applied to modification. |
| 7 | `modification_recipient_change_to_unfamiliar` | `modification_type == "recipient" AND modification_direction == "unfamiliar"` | 0.40 | True | Recipient-change-to-stranger is a soft signal; maturity-sensitive. |
| 8 | `modification_destination_change_residential_asn` | `modification_type == "destination" AND is_residential_asn` | 0.35 | True | Compounds destination change with ASN signal. |

`.ai/decisions.md` addition: subsection under "Rule catalogue target" titled "Modification rule weight rationale (Phase 3A)". Documents that these weights are operator-judgment-based; calibration deferred to Phase 6 staging replay. Records the weight bands and the noisy-OR considerations (e.g., destination_change rules can compound with familiarity rules from Phase 2C — weighted accordingly to avoid double-counting).

`tests/unit/test_rules_modification.py` (uses 2C's extracted helpers in `tests/unit/conftest.py`):

For each of the 8 rules:
- One test that fires the rule with conditions met.
- One test that does NOT fire with conditions just below threshold.
- One test that does NOT fire with a different `modification_type`.

That's 24 tests. Tests call the production rule evaluator (NOT inline re-implementations of conditions — **Phase 2 false-pass-test lesson applied**).

Sample test:
```python
def test_modification_within_30_min_value_increase_fires(loaded_rules):
    ctx = {
        "modification_type": "value",
        "modification_time_since_booking": "within_30_min",
        "modification_magnitude": 0.25,
        # ... rest of Context defaults (helper provides)
    }
    result = evaluate_rules(loaded_rules, ctx)
    assert "modification_within_30_min_value_increase" in result.triggered_rule_names

def test_modification_within_30_min_value_increase_no_fire_below_threshold(loaded_rules):
    ctx = {
        "modification_type": "value",
        "modification_time_since_booking": "within_30_min",
        "modification_magnitude": 0.15,  # below 0.2
        # ...
    }
    result = evaluate_rules(loaded_rules, ctx)
    assert "modification_within_30_min_value_increase" not in result.triggered_rule_names

def test_modification_within_30_min_value_increase_no_fire_wrong_type(loaded_rules):
    ctx = {
        "modification_type": "destination",  # not value
        "modification_time_since_booking": "within_30_min",
        "modification_magnitude": 0.50,
        # ...
    }
    result = evaluate_rules(loaded_rules, ctx)
    assert "modification_within_30_min_value_increase" not in result.triggered_rule_names
```

**Validation**:
- `pytest tests/unit/test_rules_modification.py -v` — 24 tests pass.
- `python -c "import yaml; rules = yaml.safe_load(open('app/rules.yaml'))['rules']; assert len(rules) == 75"` — sanity (67 + 8).
- `pytest tests/ --asyncio-mode=auto -q` — full suite green; existing 67-rule tests still pass.

**Risk**: **Medium**. New rules are additive but can affect scores on existing test fixtures via noisy-OR compounding. Verify case-1 and case-2 still reach BLOCK after these rules land (re-run their integration tests).

**Reversibility**: Easy — revert the YAML + rules + decisions.md addition.

**Pre-commit verification**: All gates green.

**Observability**: Triggered rules surface in `triggered_rules` field of `ModificationResponse`; `.risk_factors` carries per-rule weight contribution.

**Test changes**: 24 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Never-Skip (rule add/remove in `app/rules.yaml`). Standard panel + test-reviewer.

---

## 3A.8 — End-to-end integration test: full modification flow

**Theme**: Integration test that exercises the complete modification path: setup tenant + customer + prior booking → modify with various scenarios → assert end-to-end response shape, scoring, persistence, idempotency, cross-tenant isolation. Re-confirms case-1 and case-2 BLOCK assertions still hold (no Phase 2 regression).

**Files**:
- `tests/integration/test_modification_flow_e2e.py` (NEW)

**Specifics**:

Test scenarios:

1. **Happy-path low-risk modification**: Customer with mature baseline (~50 historical bookings to a familiar destination). Modifies value by +5% within 24h. Expected: ALLOW.
2. **High-risk pre-pickup destination change**: Customer with established baseline. Modifies destination to an unfamiliar address within 30 minutes. Expected: at least REVIEW; likely BLOCK if multiple rules compound.
3. **Modification velocity attack**: Same customer issues 5 modifications within 1h. Expected: 4th+ modification triggers `modification_high_velocity_1h` → at least REVIEW.
4. **Modification idempotency**: Same `request_id` POSTed twice returns the same decision (verified via `id` of the decisions row not changing).
5. **Modification of a modification rejected**: POST modification with `original_request_id` pointing to a prior modification's `request_id` → 422.
6. **Cross-tenant isolation**: Tenant B token attempts to modify Tenant A's booking → 404 (per Phase 2B.6 pattern; the endpoint resolves the original via WHERE tenant_id filter).
7. **Phase 2 regression check (case-1)**: re-run case-1 fixture through booking endpoint → still BLOCK. Then issue a modification on the most recent case-1 booking → asserted behavior.
8. **Phase 2 regression check (case-2)**: re-run case-2 fixture through booking endpoint → still BLOCK.
9. **Persisted modification's `request_type`** column equals `'modification'` (query directly).

**Validation**:
- `pytest tests/integration/test_modification_flow_e2e.py -v` — 9 tests pass.
- `pytest tests/ --asyncio-mode=auto -q` — full suite green (432 + 5 + 4 + 7 + 6 + 24 + 9 = ~487 tests; final count drift TBD).

**Risk**: **Low-medium**. Test-only commit. Risk is fixture-shape errors or assertion drift; reviewer test-reviewer catches.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 9 integration tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Test-only — test-reviewer + senior-engineer + code-flow (per triage gate "ONLY test file additions").

---

## Batch 3A summary table

| Commit | Theme | Files | Tests added | Risk | Reviewer panel |
|---|---|---|---|---|---|
| 3A.1 | `decisions.request_type` migration | 1 new alembic | 0 (manual round-trip) | Low-medium | Never-Skip + db-reviewer |
| 3A.2 | Pydantic modification models | `app/models.py`, 1 new test | 5 | Low | Standard + test-reviewer |
| 3A.3 | DSL field whitelist extension | `app/rules.py`, 1 new test | 4 | Low | Standard + security-auditor |
| 3A.4 | `build_modification_context` | `app/context.py`, 1 new test | 7 | Medium | Standard |
| 3A.5 | Modification velocity SQL | `app/velocity.py`, `app/context.py`, 1 new test | 6 | Medium | Standard + db-reviewer |
| 3A.6 | Endpoint route + booking fix | `app/api/modification.py`, `app/api/booking.py`, `app/main.py`, 1 new test | 7 | Medium-high | Never-Skip + db-reviewer + test-reviewer |
| 3A.7 | 8 modification rules + tests | `app/rules.yaml`, `.ai/decisions.md`, 1 new test | 24 | Medium | Never-Skip + test-reviewer |
| 3A.8 | E2E integration test | 1 new test | 9 | Low-medium | test-reviewer + senior + code-flow |
| **Total** | | | **62 new tests** | | |

Expected test count at end of Batch 3A: **432 + 62 = 494 tests**.

Rule count at end of Batch 3A: **67 + 8 = 75 rules**.

ALLOWED_CONTEXT_FIELDS count at end of Batch 3A: **56 + 6 = 62 fields**.

Migrations count at end of Batch 3A: **3** (0001, 0002, 0003).
