# PLAN_PHASE_7C — Apply chosen variant + case-3b redesign + cleanup

> **Phase 7, Batch C.** Lands the actual rule changes after Phase 7B's operator-selected variant. Adds the new case-3b compound matching the asymmetric attack shape, deletes the original symmetric triangle compound and its derivation, and documents the calibration changes in load-bearing docs.

## Decisions absorbed

Inherited from PLAN_PHASE_7A.md and PLAN_PHASE_7B.md. Phase 7C-specific:

| Decision | Value | Source |
|---|---|---|
| Chosen variant | Determined by operator at the 7B.1 mid-pass checkpoint; the four-variant table in `docs/replay-validation.md` drives the pick | Operator at 7B closeout |
| Case-3b rule shape | NEW derivation `_outbound_destination_mismatch(customer_country, destination_country)` returns True iff both non-None and differ; rule condition `customer_destination_country_mismatch_outbound AND origin_via_carrier_dropoff AND customer_observations < 10`; weight 0.65; `maturity_sensitive: false` | Operator AskUserQuestion 2026-06-04 (option 1 — derived bool pattern) |
| Field swap | DROP `customer_country_triangle_mismatch` (7C.3); ADD `customer_destination_country_mismatch_outbound` (7C.2). Net ALLOWED_CONTEXT_FIELDS size: 76 → 76. | V-7 + operator AskUserQuestion |
| Delete order | 7C.2 adds new compound + derivation BEFORE 7C.3 deletes triangle compound + derivation. This ensures case-3b detection coverage is non-zero across the 7C.2 → 7C.3 boundary. | Atomic-commit discipline (no broken intermediate state) |
| Decisions amendment | `.ai/decisions.md` new Phase 7 section documents the "no tuning in Phase 6" → "Phase 7 IS calibration" distinction, the chosen variant, the case-3b redesign rationale, and the structured-field architectural pattern preserved | Phase 7 prompt |
| Reviewer panel | Full per-commit panel; both 7C.1 and 7C.2 also trigger db-reviewer if any model file (none expected, but model.py untouched by Phase 7) changes — IT WON'T, but the routing rule applies | CLAUDE.md routing |

## Batch composition

| Commit | Title | Risk | Reviewer panel |
|---|---|---|---|
| 7C.1 | Apply chosen variant to `app/rules.yaml` | LOW | senior-engineer + security-auditor + code-flow + test-reviewer |
| 7C.2 | Add `cold_start_outbound_carrier_dropoff` rule + derivation + field + tests | LOW | senior-engineer + security-auditor + code-flow + test-reviewer |
| 7C.3 | Delete `cold_start_country_triangle_with_carrier_dropoff` + derivation + field + tests | LOW | senior-engineer + security-auditor + code-flow + test-reviewer |
| 7C.4 | `.ai/decisions.md` Phase 7 amendment + `docs/replay-validation.md` Phase 7C section | TRIVIAL (doc-only) | doc-reviewer + senior-engineer |

---

## Commit 7C.1 — Apply chosen variant

**Theme**: Whichever variant the operator selected at 7B closeout, apply its rule changes to `app/rules.yaml`. Update any tests that exercise the two modified rules.

**Files modified**:

- `app/rules.yaml` — update the two rule blocks per the chosen variant (A, B, C, or D).
- `tests/unit/test_rules_familiarity_and_diversity.py` — adjust any tests that exercise the modified rules' specific condition shape or weight. Existing tests assert behavior at specific maturity thresholds; if Variant A or C tightens to `>= 30`, tests covering the `>= 10` boundary may need extension or rewrite. Variant D introduces a new conjunct; tests must cover the new compound paths.

**Per-variant test impact** (only the chosen variant's impact applies):

- **Variant A** (gate `>= 30`): existing tests at `observations = 10, 11, 25` may flip from "rule fires" to "rule does not fire". Add boundary tests at `observations = 29, 30, 31`.
- **Variant B** (weights halved): no condition change; weight assertions in `test_rules_loader.py` (if any) or scoring tests (if any) need the new values.
- **Variant C** (combined): both A and B impacts.
- **Variant D** (compound conjunct): tests cover all combinations of the secondary signal (5 disjuncts for the first rule, 1 conjunct for the second). The DSL truth table for the new conditions must be exercised end-to-end via a small fixture-driven test set.

**Validation**:

- `pytest tests/unit/ -v -k "rule" --asyncio-mode=auto` — rule-touching tests pass.
- `pytest tests/ --asyncio-mode=auto` — full suite passes.
- `ruff check app/ tests/` clean.
- `mypy app/` strict-mode clean.

**Reviewer routing**: senior-engineer + security-auditor + code-flow + test-reviewer.

- security-auditor: rule weight changes don't unintentionally degrade case-2 / case-3b protection. (Final validation is 7D's job; 7C.1's reviewer scope is the change-shape, not the empirical measurement.)
- test-reviewer: the test updates cover the new condition shape thoroughly, not just the happy path.

**Risk**: LOW. Tightly-scoped rule update with corresponding test coverage.

---

## Commit 7C.2 — Add `cold_start_outbound_carrier_dropoff` rule + derivation + field + tests

**Theme**: Add the new case-3b compound matching the Roulottes Lupien asymmetric attack shape. Introduces a new ALLOWED_CONTEXT_FIELDS entry and a new derivation helper. Does NOT touch the existing triangle compound (that's 7C.3).

**Files modified**:

- `app/rules.yaml` — append new rule block (placed after `cold_start_country_triangle_with_carrier_dropoff` for narrative grouping; the triangle compound is deleted in 7C.3):

```yaml
- name: cold_start_outbound_carrier_dropoff
  description: |
    Phase 7C.2 — case-3b asymmetric compound (brand-new-customer fraud).
    Brand-new customer (< 10 obs) ships to outside their declared
    country via carrier dropoff. Targets the Roulottes Lupien attack
    shape: customer ships from their declared country (origin may
    match customer country) to outside-country with carrier-facility
    drop-off. Each individual signal can be legitimate; the
    combination is the case-3b fingerprint.

    Predicates:
    - customer_destination_country_mismatch_outbound: True iff both
      customer.registered_country and shipment.destination.country
      are non-null AND differ. Null-safety encapsulated in the
      derivation helper `_outbound_destination_mismatch`.
    - origin_via_carrier_dropoff: structured-field passthrough.
    - customer_observations < 10: cold-start gate.

    Weight 0.65 sits below BLOCK band 0.80 standalone — lands in
    REVIEW by itself. Composes with IP-quality / value-tier
    cold-start rules to reach BLOCK when multiple signals fire.
    maturity_sensitive false (gate inside the condition).
  condition: "customer_destination_country_mismatch_outbound AND origin_via_carrier_dropoff AND customer_observations < 10"
  weight: 0.65
  maturity_sensitive: false
```

- `app/rules.py` — `ALLOWED_CONTEXT_FIELDS`: add `"customer_destination_country_mismatch_outbound"`. Adjacent to the existing Phase 6A.5 entries; comment block updated to describe the new field's null-safe semantics.

- `app/context.py` — new derivation helper next to `_triangle_mismatch`:

```python
def _outbound_destination_mismatch(
    customer_country: str | None,
    destination_country: str | None,
) -> bool:
    """Phase 7C.2 case-3b asymmetric mismatch derivation.

    Returns True iff both inputs are truthy (non-None, non-empty)
    AND differ. The Roulottes Lupien attack shape is customer ships
    outside their declared country in the DESTINATION only (origin
    can match customer country). The symmetric triangle-mismatch
    (deleted in 7C.3) required customer_country to differ from BOTH
    origin and destination — too narrow for the empirical attack.

    Null/empty handling: returns False when either input is None or
    empty string. Customers without a declared registered country
    (tier-4 fallback in the freight_risk export) and shipments
    without a structured destination country cannot trigger this
    rule by accident. The empty-string special-case is defensive:
    Pydantic enforces the 2-letter regex at ingress so empty string
    can't reach the derivation through normal booking flow, but
    treating it as no-signal symmetric with None eliminates a class
    of "what if the model loosens" questions. The falsy check
    handles both cases in one expression.

    Pure boolean; no I/O, no exceptions.
    """
    if not customer_country or not destination_country:
        return False
    return customer_country != destination_country
```

- `app/context.py` — add to the ctx dict assembly (next to existing case-3b signals):

```python
"customer_destination_country_mismatch_outbound": _outbound_destination_mismatch(
    payload.customer.registered_country,
    shipment_destination_country,
),
```

(The local intermediate `shipment_destination_country` already exists at line 212; no re-derivation needed.)

- `tests/unit/conftest.py` — add `"customer_destination_country_mismatch_outbound": False` to the `base_ctx` fixture (next to the existing `customer_country_triangle_mismatch` entry, which is removed in 7C.3).

- `tests/unit/test_outbound_destination_mismatch.py` — NEW. Truth-table tests for the derivation helper:
  - `_outbound_destination_mismatch("CA", "US")` → True (case-3b asymmetric — Roulottes Lupien shape).
  - `_outbound_destination_mismatch("CA", "CA")` → False (domestic; not case-3b).
  - `_outbound_destination_mismatch("US", "US")` → False.
  - `_outbound_destination_mismatch(None, "US")` → False (no declared country).
  - `_outbound_destination_mismatch("CA", None)` → False (no destination country).
  - `_outbound_destination_mismatch(None, None)` → False.
  - `_outbound_destination_mismatch("", "US")` → False (empty-string treated as no-signal via falsy check; symmetric with the None case).
  - `_outbound_destination_mismatch("CA", "")` → False (same, on the destination side).

  **Edge-case note**: Pydantic `Customer.registered_country` enforces a 2-letter uppercase regex at ingress (`min_length=2, max_length=2, pattern=r"^[A-Z]{2}$"`); empty string cannot reach the derivation through normal booking flow. The helper's empty-string-as-no-signal behavior is documented for completeness and exercised only through direct test fixtures. This is a defensive deviation from the deleted `_triangle_mismatch`, which used a plain `is not None` check and would have returned True on an empty-string country had one ever reached it.

- `tests/unit/test_rule_cold_start_outbound_carrier_dropoff.py` — NEW. Rule-level tests:
  - All three conjuncts True → rule fires.
  - One conjunct False (each, independently) → rule does not fire.
  - With registered_country=None → derivation returns False → rule does not fire (regression: must not fire on null-country customers).
  - With destination.country=None → same: rule does not fire.
  - With customer_observations=10 (boundary) → does not fire (cold-start gate excludes).
  - With customer_observations=9 → fires (cold-start gate includes).
  - End-to-end booking test: post a booking matching the Roulottes Lupien shape (CA customer, CA→US, carrier dropoff, customer_observations=2) and assert the rule appears in `triggered_rules` AND the decision lands in REVIEW or BLOCK band (depending on co-firing rules' contribution).

- `tests/unit/test_rules_whitelist.py` — field count assertion: `assert len(ALLOWED_CONTEXT_FIELDS) == 76` (was 76; ADD +1 for new field; the DROP -1 comes in 7C.3). **At end of 7C.2 the count is 77 — Declared break below.**

- `tests/unit/test_context_tenant_config_passthrough.py` — same field-count assertion update. **At end of 7C.2 the count is 77 — Declared break below.**

**Declared breaks**:

- **Scope**: `tests/unit/test_rules_whitelist.py:53` and `tests/unit/test_context_tenant_config_passthrough.py:45` field-count assertions sit at 77 at end of 7C.2 (was 76; +1 added). The intended steady state is 76 (after 7C.3 drops `customer_country_triangle_mismatch`).
- **Resolved in**: 7C.3 — deletes the triangle field and restores the count to 76 via the same two assertions.

- **Scope**: `app/rules.yaml` contains BOTH the old triangle rule (`cold_start_country_triangle_with_carrier_dropoff`) and the new outbound rule (`cold_start_outbound_carrier_dropoff`) at end of 7C.2. They overlap on case-3b coverage. Tests written in 7C.2 still expect the old triangle rule to fire on the symmetric pattern.
- **Resolved in**: 7C.3 — deletes the triangle rule. After 7C.3, only the new outbound rule remains.

The combined declared break means 7C.2's pre-commit will fail on the field-count assertion (77 != 76). Pre-commit bypass justified by the declared break and named in the commit message: `Review: declared-break-7c.2; field-count-assertion-temp-77; resolves-in-7c.3`. The next commit (7C.3) restores the gate.

**Validation**:

- `pytest tests/unit/test_outbound_destination_mismatch.py -v` — all pass.
- `pytest tests/unit/test_rule_cold_start_outbound_carrier_dropoff.py -v` — all pass.
- `pytest tests/unit/test_rules_whitelist.py -v -k 'whitelist'` — **FAILS** on the count assertion (declared break; resolves in 7C.3). Specifically the test fails with `assert 77 == 76` — surface this in the commit message.
- `pytest tests/unit/test_rule_cold_start_country_triangle.py -v` — still passes (triangle rule still in `app/rules.yaml`; declared break notes this is transient).
- `ruff check app/ tests/` clean.
- `mypy app/` strict-mode clean.

**Reviewer routing**: senior-engineer + security-auditor + code-flow + test-reviewer.

- security-auditor: new derivation can NEVER raise exceptions; the null-safety property is verified by the truth-table tests.
- senior-engineer: the field-count temp-77 declared break is fully scoped + temporally bounded.
- test-reviewer: truth-table coverage on the derivation; integration test covers the Roulottes Lupien booking shape end-to-end.

**Risk**: LOW. Additive only; declared break is explicit and resolves in the immediate next commit.

---

## Commit 7C.3 — Delete `cold_start_country_triangle_with_carrier_dropoff` + derivation + field + tests

**Theme**: Remove the symmetric triangle compound (0% empirical detection on case-3b census per Phase 6C) and its supporting derivation, ALLOWED_CONTEXT_FIELDS entry, and tests. Restores the field-count assertion to 76.

**Files modified**:

- `app/rules.yaml` — REMOVE the entire `cold_start_country_triangle_with_carrier_dropoff` rule block (lines 582-612 in the pre-7C state, including the comment block above it).
- `app/rules.py` — `ALLOWED_CONTEXT_FIELDS`: REMOVE `"customer_country_triangle_mismatch"`. Update adjacent comment block (lines 122-128) to reflect the field removal — the case-3b retained signals are `customer_registered_country` (retained), `customer_destination_country_mismatch_outbound` (added in 7C.2), `shipment_route_rare_for_tenant` (retained).
- `app/context.py` — REMOVE `_triangle_mismatch` function (lines 123-144 in pre-7C state). REMOVE the `"customer_country_triangle_mismatch": _triangle_mismatch(...)` entry from the ctx dict (lines 316-320). Update the comment block (lines 306-315) to reflect the case-3b signals now consist of `customer_registered_country` + `customer_destination_country_mismatch_outbound`.
- `tests/unit/conftest.py` — REMOVE `"customer_country_triangle_mismatch": False` from `base_ctx` (line 130 in pre-7C state).
- `tests/unit/test_country_triangle_mismatch.py` — DELETE entirely (tests the deleted derivation).
- `tests/unit/test_rule_cold_start_country_triangle.py` — DELETE entirely (tests the deleted rule).
- `tests/unit/test_rules_whitelist.py` — UPDATE comment at line 49 to reflect the field swap; UPDATE the `phase_6a_5` frozenset at line 65 to drop `customer_country_triangle_mismatch` (retain `customer_registered_country`); UPDATE field-count assertion at line 53 back to `== 76`.
- `tests/unit/test_context_tenant_config_passthrough.py` — REMOVE the `assert "customer_country_triangle_mismatch" in ALLOWED_CONTEXT_FIELDS` assertion at line 57; ADD `assert "customer_destination_country_mismatch_outbound" in ALLOWED_CONTEXT_FIELDS` to mirror the 6A.5-style coverage; UPDATE field-count assertion at line 45 back to `== 76`.

**Cross-check before commit**: `git grep -E 'customer_country_triangle_mismatch|_triangle_mismatch'` after the changes returns ONLY references in:
- Plan/report markdown files (PLAN_PHASE_*.md, REPORT_PHASE_*.md) — historical narrative; leave intact.
- `docs/replay-validation.md` — Phase 6C measurement narrative; leave intact (describes the historical Phase 6C state).
- `docs/calibration-backlog.md:117` — historical reference in item 6's deferred-action narrative; the 7E.1 commit marks items 1, 2, 6 RESOLVED. Touched there, not in 7C.3.
- `.ai/decisions.md` — historical Phase 6A.5 amendment text. Leave intact; 7C.4 adds the Phase 7 amendment, not removes the 6A.5 record.

NO references should remain in `app/`, `tests/unit/` (other than deletion list above), or `scripts/`.

**Validation**:

- `git grep -E 'customer_country_triangle_mismatch'` returns NO matches in `app/`, `tests/unit/`, or `scripts/`.
- `git grep -E '_triangle_mismatch'` returns NO matches in `app/` or `tests/unit/`.
- `pytest tests/unit/test_rules_whitelist.py -v` — passes (field count back to 76, declared break from 7C.2 resolved).
- `pytest tests/unit/test_context_tenant_config_passthrough.py -v` — passes.
- `pytest tests/ --asyncio-mode=auto -q` — full suite passes.
- `ruff check app/ tests/` clean.
- `mypy app/` strict-mode clean.

**Reviewer routing**: senior-engineer + security-auditor + code-flow + test-reviewer.

- security-auditor: ensure no lingering reference in the production code path; the case-3b detection capability is preserved via 7C.2's new rule.
- code-flow: confirm `cold_start_population_baseline_rare_with_carrier_dropoff` (Phase 6A.9) is untouched and continues to provide its independent signal class.
- test-reviewer: deletions are clean; no test file imports symbols from deleted modules.

**Risk**: LOW. Deletion scope is fully enumerated; tests that exercised the deleted code are deleted with the code.

---

## Commit 7C.4 — `.ai/decisions.md` Phase 7 amendment + `docs/replay-validation.md` Phase 7C section

**Theme**: Document the calibration changes and the case-3b redesign rationale in load-bearing project docs. This is the architectural-record commit for Phase 7.

**Files modified**:

- `.ai/decisions.md` — append new dated section "Phase 7 — Pre-launch calibration":
  - **Scope distinction**: Document the "no tuning in Phase 6" → "Phase 7 IS calibration" override. Phase 6's no-tuning discipline was specific to Phase 6 (defer calibration to post-launch real-data observation). Phase 7 explicitly overrides because Phase 6C surfaced a launch-blocker (41% REVIEW rate on the approved corpus) that cannot reach production.
  - **Chosen variant**: Name the variant (A/B/C/D), summarize the empirical comparison that drove the pick. Reference the Phase 7B section of `docs/replay-validation.md` for the full data.
  - **Case-3b redesign rationale**: The symmetric triangle compound was designed for a threat model the empirical Phase 6C data did not validate (0/95 on the Roulottes Lupien census). The new asymmetric compound matches the observed attack shape (customer ships from declared country to outside-country with carrier dropoff). The design preserves the structured-field architectural pattern — no address parsing in production code; the export script's address-regex parsing is offline corpus-shaping only.
  - **Field swap**: `customer_country_triangle_mismatch` → `customer_destination_country_mismatch_outbound`. Net ALLOWED_CONTEXT_FIELDS size unchanged at 76.
  - **Population baseline compound retained**: `cold_start_population_baseline_rare_with_carrier_dropoff` (Phase 6A.9) continues to provide an independent signal class anchored in tenant population baselines. Its 0% fire rate on the Phase 6C case-3b census was due to the empty `tenant_route_baselines` on the replay tenant — a measurement artifact, not a rule-design defect.
  - **Triangle compound deletion**: `cold_start_country_triangle_with_carrier_dropoff` (Phase 6A.5) deleted along with its derivation `_triangle_mismatch`, its ALLOWED_CONTEXT_FIELDS entry, and its tests. The historical record of why the rule was introduced remains in Phase 6A's section of `.ai/decisions.md`; Phase 7's section documents the supersession.

- `docs/replay-validation.md` — append "## Phase 7C rule catalogue state" section:
  - One-paragraph summary of the chosen variant.
  - One-paragraph summary of the case-3b redesign.
  - Reference to `.ai/decisions.md` Phase 7 section for the architectural rationale.
  - No per-record content.

**Validation**:

- doc-reviewer reads end-to-end. The decisions.md amendment is a load-bearing architectural record.
- Phase 7C section in `docs/replay-validation.md` contains aggregate stats only; `grep -E '"request_id"' docs/replay-validation.md` returns empty.
- Markdown lint (if pre-commit has a markdown linter) clean.

**Reviewer routing**: doc-reviewer + senior-engineer.

- doc-reviewer: narrative clarity, accuracy of measurement citations, no per-record content leakage, the scope-distinction paragraph is unambiguous.
- senior-engineer: architectural rationale matches the actual code changes in 7C.1-7C.3.

**Risk**: TRIVIAL. Doc-only. No code execution path affected.

---

## Batch 7C acceptance criteria

1. `app/rules.yaml` reflects the chosen variant AND the new outbound rule AND the absence of the triangle rule.
2. `ALLOWED_CONTEXT_FIELDS` size is 76; `customer_country_triangle_mismatch` removed, `customer_destination_country_mismatch_outbound` added.
3. `app/context.py` has the new `_outbound_destination_mismatch` helper and no longer has `_triangle_mismatch`.
4. `pytest tests/ --asyncio-mode=auto` — full suite passes.
5. `.ai/decisions.md` carries the Phase 7 calibration amendment.
6. `docs/replay-validation.md` carries the Phase 7C catalogue state section.
7. `git grep -E 'customer_country_triangle_mismatch|_triangle_mismatch'` returns ONLY historical-narrative markdown matches; no code matches.

Operator checkpoint after 7C.4 completes: proceed to PLAN_PHASE_7D.md (final validation).
