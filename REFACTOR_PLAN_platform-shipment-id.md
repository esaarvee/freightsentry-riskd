# Refactor Plan — Platform-Supplied `shipment_id` + `transaction_number`

> Makes the upstream platform's shipment identifier the system of record:
> `shipments.id` becomes a platform-supplied `text` identity; a new
> operator-facing `transaction_number text` is stored alongside it.
> **Pre-launch** (no production data) → clean redefinition, no backfill.
> Lands on the migrate-then-deploy automation + golden-schema gate → the
> migration is the CRITICAL / never-skip path.

Verification: `/tmp/platform-shipment-id-verification-01.md` (Phase 1, all PASS).
Scope source: attached design report + locked decisions #1–#11.

---

## Decisions absorbed

| # | Decision | Source |
|---|---|---|
| Grouping | **(a) single coherent core commit** — migration + models + booking + modification + their tests co-land; suite stays green at every commit. | Operator (Phase-2 Q) |
| Contract doc | **schema.md note** (+ `.ai/decisions.md`); no new `.ai/contracts/` dir (it does not exist). | Operator (Phase-2 Q) |
| Commit strategy | **Atomic** for surrounding commits (core / docs separate). | Operator (Phase-2 Q) |
| V4 disposition | **Proceed + disambiguation note** — `transaction_number` is greenfield in riskd's domain; the only existing refs are the upstream `freight_risk` source schema (calibration ETL), which is confirmatory. Add a cross-reference note so the two are not conflated. | Operator (Phase-1 Q) |
| #1 | `shipment_id` + `transaction_number` on booking + modification payloads; `text`; `Field(..., min_length=1, max_length=128)`. | Report |
| #2 | Booking: `shipment_id` **is** the shipment identity; drop the `RETURNING id` round-trip. | Report |
| #3 | Modification: `shipment_id` **cross-check only** vs shipment resolved via `original_request_id`; 422 on mismatch; no new shipment row; no `transaction_number` persist. | Report |
| #4 | `shipments` PK → composite `(tenant_id, id)`. Indexes `shipment_id` for free. | Report |
| #5 | `decisions.shipment_id`: `int → text`; FK → `(tenant_id, shipment_id) → shipments(tenant_id, id)`. | Report |
| #6 | §8 → option (a): modification cross-checks `transaction_number` vs stored `shipments.transaction_number`; 422 on mismatch. | Report |
| #7 | `transaction_number` **unindexed by design**; no timestamp/date-range index. | Report |
| #8 | Intentional **409** on second booking POST with same `shipment_id` + different `request_id` (composite-PK collision) — clear message, not raw constraint error. | Report |
| #9 | `BookingResponse` echoes `shipment_id` **and** `transaction_number`; `ModificationResponse` echoes `shipment_id`. | Report |
| #10 | **Inviolable unchanged surfaces:** `request_id` idempotency; feedback `target_request_id` resolution + monotonicity + two-tier idempotency; modification `original_request_id` resolution; multi-mod cardinality; `FeedbackRequest`/`FeedbackResponse`. | Report |
| #11 | Out of repo scope: admin dashboard, platform endpoint versioning/cutover, dashboard read endpoint. | Report |

---

## Phase 1 verification — outcome (clean)

| V | Verdict | Plan impact |
|---|---|---|
| V1 type-opacity | PASS — `shipment_id` opaque everywhere; only the admin response `shipment.id` shifts number→string (#5/#9). | No int-dependence to remediate. |
| V2 tenant-join audit (hard gate) | PASS — **all 4** `decisions↔shipments` joins (modification, admin, feedback, velocity) **already** carry `s.tenant_id = d.tenant_id`, i.e. already join on the full `(tenant_id, shipment_id)` tuple. Zero tenant-less joins; zero rewrites. | Plan **preserves** these predicates (no regression); does not add new ones. The composite FK formalizes an already-correct invariant. |
| V3 feedback independence | PASS — resolves via `target_request_id`; selects but never consumes `s.id`; type-transparent. | `feedback.py` untouched (#10). |
| V4 `transaction_number` greenfield | PASS w/ nuance — greenfield in riskd; upstream `freight_risk` source refs are confirmatory. | Disambiguation cross-ref note (Commit 2). |
| V5 golden-schema + gate | PASS — regen delta enumerated; gate = `.github/workflows/test.yml` `alembic upgrade head` on scratch + golden test. | Golden regenerated **in the migration commit**. |

---

## Commit sequence (2 commits)

### Commit 1 — CORE (CRITICAL / never-skip)

**Title:** `feat(shipments): platform shipment_id as identity + transaction_number (#1–#9)`

Single coherent commit per grouping (a): schema + models + both endpoints + all
tests, so `pytest` is green at the commit boundary.

#### Changes

**1. Migration `alembic/versions/0006_platform_shipment_id.py`** (new; never-skip)

Clean redefinition (pre-launch, empty tables). Proposed DDL:

```sql
-- decisions FK must drop before retyping either side / dropping shipments PK
ALTER TABLE decisions DROP CONSTRAINT decisions_shipment_id_fkey;

-- shipments.id: serial -> text, composite PK
ALTER TABLE shipments DROP CONSTRAINT shipments_pkey;          -- was PRIMARY KEY (id)
ALTER TABLE shipments ALTER COLUMN id DROP DEFAULT;            -- drop nextval default
DROP SEQUENCE shipments_id_seq;                                -- owned seq, now unreferenced
ALTER TABLE shipments ALTER COLUMN id TYPE text USING id::text;
ALTER TABLE shipments ADD CONSTRAINT shipments_pkey PRIMARY KEY (tenant_id, id);

-- new operator-facing column, UNINDEXED by design (#7); NOT NULL ok on empty table
ALTER TABLE shipments ADD COLUMN transaction_number text NOT NULL;
COMMENT ON COLUMN shipments.transaction_number IS
  'Platform-supplied operator-facing reference. Stored unindexed by design (#7): '
  'not a riskd query key; the external admin dashboard (separate repo) reads by date '
  'range. Same logical value as freight_risk.shipments.transaction_number (calibration source).';
COMMENT ON COLUMN shipments.id IS
  'Platform-supplied shipment identity (system of record). Composite PK (tenant_id, id) '
  'guards against a cross-tenant shipment_id collision leaking existence via 409 (#4).';

-- decisions.shipment_id: int -> text, composite FK
ALTER TABLE decisions ALTER COLUMN shipment_id TYPE text USING shipment_id::text;
ALTER TABLE decisions ADD CONSTRAINT decisions_shipment_id_fkey
    FOREIGN KEY (tenant_id, shipment_id) REFERENCES shipments(tenant_id, id);
```

Preserve untouched: `ux_shipments_tenant_request (tenant_id, request_id)`,
`ux_decisions_tenant_request_type (tenant_id, request_type, request_id)`,
`ix_decisions_tenant_shipment`, all `ix_shipments_*`, RLS policies, grants.
**Refinement #5:** `ALTER COLUMN shipment_id TYPE text` rebuilds
`ix_decisions_tenant_shipment` under the hood, but pg_dump renders the index
definition by column name only — so the golden diff shows **only** the column-type
line (`shipment_id integer → text`) and the FK line changing; the index line stays
byte-identical. No phantom golden mismatch; the mid-pass checkpoint confirms the
index survives.

`downgrade()` reverses (recreate sequence, `id` text→int via `USING id::integer`,
restore `nextval` default + `PRIMARY KEY (id)`, drop `transaction_number`, restore
scalar FK). **Refinement #4:** the `USING id::integer` cast throws the moment any
non-numeric platform `shipment_id` exists — which is the entire point of going to
`text`. So downgrade is **one-way-after-launch**: vacuous on empty pre-launch
tables, hard-fails once real platform IDs are present. This warning lands as a
**comment inside the migration's `downgrade()`** (not just here in the plan), so
nobody runs it post-launch expecting it to round-trip.

**2. Golden schema `tests/golden/schema.sql`** — regenerate in THIS commit via the
docstring command in `tests/integration/test_schema_golden.py`. Expected delta
(enumerated in V5): remove the 4 `shipments_id_seq` lines; `shipments.id`/
`decisions.shipment_id` → `text`; `shipments_pkey` → `(tenant_id, id)`;
`decisions_shipment_id_fkey` → composite; add `transaction_number text NOT NULL`.

**3. `app/models.py`**
- `BookingRequest`: add `shipment_id` + `transaction_number`, both
  `str = Field(..., min_length=1, max_length=128)`.
- `ModificationRequest`: add the same two fields.
- `BookingResponse`: add `shipment_id: str` and `transaction_number: str` (#9).
- `ModificationResponse`: add `shipment_id: str` (#9).
- `FeedbackRequest` / `FeedbackResponse`: **untouched** (#10).

**4. `app/api/booking.py`**
- Shipments INSERT: add `id, transaction_number` columns; bind
  `payload.shipment_id`, `payload.transaction_number`. **Drop `RETURNING id`**;
  use `payload.shipment_id` for the decisions INSERT param (#2).
- Wrap the shipments INSERT in `try/except asyncpg.UniqueViolationError` and
  **discriminate on `exc.constraint_name`** (refinement #2): `shipments_pkey` →
  `HTTPException(409, "shipment_id already booked for this tenant")` (#8, the
  identity collision); `ux_shipments_tenant_request` → a distinct 409 naming a
  `request_id` idempotency race (the pre-insert replay SELECT missed a concurrent
  commit). A blanket `except → shipment_id message` is **too broad** for a table
  with two unique surfaces and would mislabel a request_id race as an identity
  collision. The pre-insert `request_id` replay SELECT (lines 79–106) is
  **unchanged** (#10, quality-constraint #1).
- Both `BookingResponse(...)` constructions (replay return + final return) echo
  `shipment_id=payload.shipment_id`, `transaction_number=payload.transaction_number`.
  Rationale: replay is keyed on `request_id`; the response echoes the request's
  identity fields without re-querying shipments, keeping the idempotency replay
  query untouched (#10). **Conscious replay-echo semantics (refinement #3):** on
  the replay path the response echoes the *request-supplied* identity, NOT the
  stored identity of the original booking. If a caller replays a `request_id` with
  different `shipment_id`/`transaction_number` (malformed retry), the echoed
  identity reflects the new payload while the decision reflects the original. This
  is **accepted and documented, not silently chosen** — identity drift on replay is
  not validated; the alternative (re-query the stored row) is rejected to keep the
  replay query untouched per #10. Documented in a code comment at the replay return
  AND in the `.ai/schema.md` contract note (so the dashboard-consistency motivation
  has a recorded answer if it ever surfaces). Flagged for reviewer scrutiny.

**5. `app/api/modification.py`**
- Add `s.transaction_number AS transaction_number` to the `prior` SELECT
  (additive column on the already-tenant-scoped, already-joined shipments leg;
  `original_request_id` resolution semantics unchanged — #10).
- After the prior-is-booking check, before scoring, add two cross-checks:
  - `payload.shipment_id != prior["shipment_id"]` → `HTTPException(422, …)` (#3)
  - `payload.transaction_number != prior["transaction_number"]` → `HTTPException(422, …)` (#6)
- No new shipments row; no `transaction_number` persist (#3).
- Both `ModificationResponse(...)` constructions echo `shipment_id=payload.shipment_id` (#9).

**6. `app/api/admin.py`** — no code change (already returns `row["shipment_id"]`
into `shipment.id`; now a string automatically). Verified, not edited.

**7. `app/api/feedback.py`, `app/velocity.py`** — no change. The 4 joins already
carry `s.tenant_id = d.tenant_id` (V2); preserved as-is.

#### Tests (co-land to keep suite green)

- **Shared fixtures:** `tests/fixtures/payloads/booking_full.json` +
  `booking_minimal.json` gain `shipment_id` + `transaction_number` (covers all
  `load_payload`-based tests in one edit each).
- **Inline payload dicts:** the ~25 integration files that build booking/
  modification JSON inline gain the two fields; the 3 `BookingRequest(...)` + 2
  `ModificationRequest(...)` model constructions gain them too. Prefer a shared
  helper where one already gates many tests; otherwise per-site.
- **Direct `INSERT INTO shipments` sites that bypass the API (refinement #1, grep-confirmed).**
  These omit `id` today, relying on the serial default — which the migration
  **removes**. After the migration they fail on **both** the missing `text` `id`
  **and** `transaction_number NOT NULL`. Each must supply an explicit `id` (text)
  **and** `transaction_number`:
  - `tests/integration/test_rls_enforcement_under_riskd_app.py:92`
  - `tests/integration/test_context_value_caps_fields.py:253`
  - `tests/integration/test_context.py:466` and `:497`
  - `tests/integration/test_velocity.py:52`

  (`tests/unit/test_export_from_freight_risk.py` INSERTs are the upstream
  `freight_risk` **source** schema — already carry `shipment_id`/`transaction_number`
  by name; NOT riskd's `public.shipments`; do not touch.)
- **Admin test** (`test_admin_endpoints.py`): assert `shipment.id` is now a
  **string** (int→string ripple, #5/#9).
- **New tests:**
  - 409: second booking POST, same `shipment_id`, **different** `request_id` →
    409 with the identity message; assert the `request_id` replay path is NOT
    taken (distinct from idempotency).
  - 409 negative control: same `request_id` replay still returns the prior
    decision (idempotency unchanged, #10).
  - 422: modification with mismatched `shipment_id` → 422 (#3).
  - 422: modification with mismatched `transaction_number` → 422 (#6).
  - Response-type: booking echoes `shipment_id`+`transaction_number` as strings;
    modification echoes `shipment_id` as string; admin `shipment.id` is a string.
- **Golden test** passes against the regenerated `schema.sql`.

#### Validation

```
# Pre-commit guard (refinement #1): no riskd-domain direct shipments INSERT
# left without id + transaction_number. Expect only the upstream-source
# test_export hits to remain.
grep -rn "INSERT INTO shipments" tests/ scripts/
ruff check app/ tests/
mypy app/
docker compose up -d
docker compose exec -T app alembic upgrade head          # scratch-DB migrate (gate)
docker compose exec -T app alembic downgrade base && docker compose exec -T app alembic upgrade head   # round-trip
pytest tests/integration/test_schema_golden.py -v        # golden gate FIRST
pytest tests/ -v --asyncio-mode=auto                     # full suite
```

#### Risk: **HIGH (justified).** PK + FK type change, breaking payload contract,
new 409 identity semantics, golden-gate regen. Mitigated by: pre-launch (no data),
single green commit, full reviewer panel, mandatory mid-pass checkpoint.

#### Reversibility: Code revert + `alembic downgrade` (empty tables → clean reverse).
Golden reverts with the commit.

#### Pre-commit verification: ruff + ruff-format + `mypy app/` + `pytest tests/unit/`.
Migration is a declared CRITICAL/never-skip path but introduces **no transitional
state** under grouping (a) → **no `--no-verify`**; all gates must pass before commit.

#### Migration considerations: ordering (drop FK → drop PK → drop default → drop
seq → retype → add composite PK → add column → retype decisions → add composite
FK) is load-bearing; composite FK requires the composite PK to exist first.
`USING id::text` / `::integer` are trivial on empty tables.

#### Rollback: `alembic downgrade -1`; git revert of the commit.

#### Reviewers (full panel + DB + test): db-reviewer, senior-engineer,
security-auditor, code-flow-reviewer, test-reviewer — PARALLEL.

#### Declared breaks: **none.** Grouping (a) lands the schema and all consumers +
tests together; no transitional state. (Per CLAUDE.md, omit the subsection — absence
is the signal. Listed here once only to state it explicitly for this plan.)

---

### Commit 2 — Docs (doc-only path)

**Title:** `docs(shipments): platform-identity system-of-record + boundary notes`

#### Changes
- **`.ai/schema.md`**: `shipments.id` (text, composite PK `(tenant_id, id)`),
  `shipments.transaction_number` (text, **unindexed by design** — operator-facing
  reference; dashboard reads by date range from a separate repo; no riskd read
  endpoint), `decisions.shipment_id` (text) + composite FK. **Contract note**
  (operator-chosen home): breaking platform payload change (new required
  `shipment_id`+`transaction_number`) + intentional **409** on duplicate
  `shipment_id` + the two modification **422** cross-checks — for the platform
  team (their cutover is out of scope, #11). **Replay-echo semantics note**
  (refinement #3): a `request_id` replay echoes the *request-supplied*
  `shipment_id`/`transaction_number`, not the stored identity of the original
  booking; identity drift on replay is not validated (conscious tradeoff to keep
  the idempotency replay query untouched, #10). **Disambiguation cross-ref** (V4):
  riskd's `shipments.transaction_number` is the same logical value as
  `freight_risk.shipments.transaction_number` (calibration source schema), not a
  separate concept.
- **`.ai/decisions.md`**: platform shipment identity is the system of record;
  `transaction_number` unindexed by design; admin dashboard external/separate-repo;
  no riskd read endpoint; no timestamp/date-range index (intentional absence so a
  dead-capability audit doesn't "fix" it).
- **`docs/history.md`**: narrative entry for this pass.

#### Tests: none (docs).
#### Validation: `ruff`/`mypy` unaffected; doc-reviewer reads the diff.
#### Risk: **LOW.** Reversibility: trivial git revert.
#### Reviewers: doc-reviewer only (doc-only path). Note: `.ai/decisions.md` edits
are always standard-path-with-doc-reviewer per CLAUDE.md borderline rule — doc-reviewer satisfies it; if the reviewer flags architectural-intent depth, escalate to the standard panel.
#### Declared breaks: none.

---

## Mid-pass checkpoint (mandatory)

After Commit 1's **validate** step and before finalizing the commit, confirm on a
scratch DB:
1. `alembic upgrade head` applies cleanly; `downgrade base && upgrade head`
   round-trips.
2. `tests/golden/schema.sql` regenerated and `test_schema_matches_golden` passes
   (the migrate-then-deploy gate).
3. Full `pytest` green.

If any fail twice on the same commit, stop and append to `.claude/STATUS.md`
`Unforeseen / checkpoints` per CLAUDE.md autonomous-execution rule 2.

---

## DO NOT (Phase-3 guardrails)
- Touch `request_id` idempotency, feedback `target_request_id` resolution,
  `original_request_id` resolution, multi-mod cardinality, feedback
  monotonicity/two-tier idempotency, or `FeedbackRequest`/`FeedbackResponse`.
- Index `transaction_number` or add any timestamp/date-range index.
- Build any dashboard read endpoint / admin query surface, or add backfill.
- Let any `decisions↔shipments` join resolve on `shipment_id` without the tenant
  predicate (preserve `s.tenant_id = d.tenant_id` on all 4).
- Conflate the 409 (shipment identity) path with the `request_id` replay
  (idempotency) path.

## DO
- Composite PK `(tenant_id, id)`; composite FK `(tenant_id, shipment_id)`; golden
  regen in the migration commit.
- Surface the PK collision as a clear 409.
- 422 on both modification cross-checks (option a).
- Land the `.ai/decisions.md` boundary note + the V4 disambiguation cross-ref.
- Keep the suite green per commit; one-line commit summary referencing decision IDs.

## Final deliverable
After both commits land: `REFACTOR_REPORT_platform-shipment-id.md` — per-decision
disposition, V1–V5 outcomes, the join-audit result, reviewer-caught corrections,
and an explicit note that platform-team payload-cutover coordination is their scope.

---

**Do not execute until approved.**
