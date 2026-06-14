# Refactor Report — Platform-Supplied `shipment_id` + `transaction_number`

Branch: `feat/refactor` · Plan: `REFACTOR_PLAN_platform-shipment-id.md` ·
Verification: `/tmp/platform-shipment-id-verification-01.md`

Commits:
- **`386c3ea`** — `feat(shipments): platform shipment_id as identity + transaction_number` (CORE, CRITICAL/never-skip)
- **(this commit)** — `docs(shipments): platform-identity system-of-record + boundary notes`

---

## Per-decision disposition

| # | Decision | Disposition |
|---|---|---|
| 1 | `shipment_id` + `transaction_number` on booking + modification payloads; `text`, `Field(..., min_length=1, max_length=128)` | ✅ `app/models.py` — added to `BookingRequest` and `ModificationRequest`. |
| 2 | Booking: `shipment_id` replaces the serial; drop `RETURNING id` | ✅ `app/api/booking.py` — INSERT supplies `id = payload.shipment_id`; `RETURNING id` removed; decisions INSERT uses `payload.shipment_id`. |
| 3 | Modification: `shipment_id` cross-check only; 422 on mismatch; no shipment row; no `transaction_number` persist | ✅ `app/api/modification.py` — 422 check after the prior-is-booking gate; no writes. |
| 4 | `shipments` PK → composite `(tenant_id, id)` | ✅ migration `0006`. |
| 5 | `decisions.shipment_id` int→text; composite FK | ✅ migration `0006`; admin response `shipment.id` ripples int→string (`app/api/admin.py` needed no code change). |
| 6 | §8 → option (a): modification cross-checks `transaction_number` vs stored; 422 | ✅ `app/api/modification.py` — second 422 check; `prior` SELECT gained `s.transaction_number`. |
| 7 | `transaction_number` unindexed by design; no timestamp index | ✅ no index added; documented in `.ai/schema.md` + `.ai/decisions.md` + the column comment. |
| 8 | Intentional 409 on duplicate `shipment_id` + different `request_id` | ✅ `app/api/booking.py` — `UniqueViolationError` discriminated on `constraint_name` (`shipments_pkey` → identity 409; `ux_shipments_tenant_request` → request_id-race 409). |
| 9 | `BookingResponse` echoes both; `ModificationResponse` echoes `shipment_id` | ✅ `app/models.py` + both endpoints (replay + final return paths). |
| 10 | Unchanged surfaces (idempotency, feedback resolution, modification cardinality, feedback models) | ✅ untouched; verified V3. |
| 11 | Admin dashboard / endpoint versioning / read endpoint out of scope | ✅ none built. |

**Operator decisions absorbed:** grouping **(a)** single coherent core commit; contract note in **schema.md**; **atomic** surrounding commits; V4 **proceed + disambiguation note**.

---

## V1–V5 verification outcomes

| V | Subject | Outcome |
|---|---|---|
| V1 | Type-opacity of `shipment_id` | PASS — opaque at every call site; only the admin response `shipment.id` shifts number→string (expected, #5/#9). No int casts/arithmetic/ordering/sequence dependence. |
| V2 | Tenant-predicate join audit (hard gate) | PASS — **all four** `decisions↔shipments` joins (modification, admin, feedback, velocity) already carried `s.tenant_id = d.tenant_id`. Zero tenant-less joins; **zero rewrites**. The composite FK formalizes an already-correct invariant. |
| V3 | Feedback independence | PASS — resolves via `target_request_id`; selects but never consumes `s.id`. `feedback.py` untouched. |
| V4 | `transaction_number` greenfield | PASS WITH NUANCE — greenfield in the riskd domain; the only existing references are the upstream `freight_risk` calibration source (confirmatory). Operator chose proceed + disambiguation note (landed in schema.md/decisions.md + migration comment). |
| V5 | Golden-schema + gate | PASS — regen delta enumerated and verified exactly (id/shipment_id→text; composite PK + FK; `transaction_number` added; 4 `shipments_id_seq` lines removed). Forward gate `0001→0006` on a fresh scratch DB succeeds; regenerated golden byte-matches the full-chain scratch schema. |

### Join-audit result (V2 detail)

| Location | Predicate | Verdict |
|---|---|---|
| `app/api/modification.py` `prior` SELECT | `JOIN shipments s ON s.id = d.shipment_id AND s.tenant_id = d.tenant_id` + `WHERE d.tenant_id = $1` | already composite-safe |
| `app/api/admin.py` decision lookup | same join + `WHERE d.tenant_id = $1` | already composite-safe |
| `app/api/feedback.py` target resolution | same join + `WHERE d.tenant_id = $1` | already composite-safe (selects `s.id` but never consumes it) |
| `app/velocity.py` `count_user_modifications_1h/24h` | same join + explicit `d.tenant_id = $1 AND s.tenant_id = $1` | already composite-safe |

No correction required; the change preserves these predicates (no regression).

---

## Reviewer-caught corrections

**Cycle 1 (full panel on the CORE commit):** db-reviewer SHIP IT, code-flow CLEAN, security-auditor LOW RISK/CLEAN, senior-engineer **NEEDS MINOR FIXES**, test-reviewer **ACCEPTABLE**. The senior-engineer and test-reviewer converged on one gap: the three net-new behaviors (the identity 409 + constraint-name discrimination + replay negative-control, and the two modification 422 cross-checks) shipped with only unit-level model tests — the endpoint-level negative paths the plan mandated were missing.

**Correction:** added four integration tests —
`test_duplicate_shipment_id_different_request_id_returns_409` and its
`test_same_request_id_reuse_is_idempotent_replay_not_identity_409` negative
control (`test_decisions_unique_widening.py`);
`test_modification_mismatched_shipment_id_returns_422` and
`test_modification_mismatched_transaction_number_returns_422`
(`test_modification_endpoint.py`). The two 422 tests isolate each cross-check by
ordering; the 409 test pins the `shipments_pkey` branch via the "already booked"
message and the replay negative control.

**Cycle 2 (scoped re-review):** senior-engineer **SHIP IT**, test-reviewer
**ACTUALLY GOOD**. Merge gate satisfied. The test-reviewer's stale-docstring nit
(the module header understated coverage) was fixed.

**Doc commit:** doc-reviewer **MINOR TWEAKS** — one dangling reference to this
report, resolved by creating it.

---

## Verification artifacts

- Forward migrate-then-deploy gate (`0001→0006` on scratch DB): pass.
- Golden schema (`tests/golden/schema.sql`) regenerated via the container `pg_dump` 16 (canonical/CI lineage) and byte-matches the full-chain scratch schema.
- Migration `0006` own round-trip (`0006↔0005`) on the main DB: clean.
- Test suite: 929 unit pass; integration pass except one **proven-pre-existing**
  failure (`test_case_2::test_unfamiliar_ip_against_established_customer_blocks_under_layer2`),
  reproduced identically on pure HEAD code + HEAD schema — logged to `.claude/BUGS.md`.
- Host `pg_dump` 18 vs container 16 golden skew — logged to `.claude/BUGS.md`.

---

## Out of scope — platform-team coordination (their scope)

The breaking payload-change coordination is the platform team's responsibility:
booking and modification payloads now carry two **required** fields
(`shipment_id`, `transaction_number`), and a second booking POST reusing a
`shipment_id` with a different `request_id` now returns **409**. The platform
team must version the endpoint or coordinate a cutover. The admin dashboard
(separate repo), any dashboard read endpoint, the `transaction_number` index, and
any timestamp/date-range index are **out of repo scope** and documented as
intentional absences in `.ai/decisions.md`.
