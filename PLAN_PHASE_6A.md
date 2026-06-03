# PLAN_PHASE_6A — Case-3 detection code

> **Phase 6, Batch A.** Adds two Context fields and one compound rule for the case-3 fraud pattern. Code-only batch — measurement happens in 6C via replay.
>
> Companion plans: `PLAN_PHASE_6B.md` (CAD-default currency) → `PLAN_PHASE_6C.md` (replay validation, including case-3 census measurement) → `PLAN_PHASE_6D.md` (deployment artifacts) → `PLAN_PHASE_6E.md` (Phase 6 wrap).

---

## Pre-plan verification findings

Verification reads against current `feat/refactor` HEAD (`f7778cd`):

1. **`BookingRequest` carries no origin-handoff field.** `app/models.py:75-87` defines the request shape with `request_id`, `customer`, `user`, `source_ip`, `shipment: ShipmentData`, `booking_ts`, optional `enterprise`, optional `contact`. No `ship_from_type` / `pickup_type` / `origin_handoff_mode` analog exists. 6A adds a new optional field on `ShipmentData`.

2. **`build_context` lives at `app/context.py:68-264`.** `ALLOWED_CONTEXT_FIELDS` is defined at `app/rules.py:30-117` with exactly **71** fields confirmed by `tests/unit/test_rules_whitelist.py` (`assert len(ALLOWED_CONTEXT_FIELDS) == 71`). After 6A: **73** fields.

3. **`customer_baselines` has no country-route-pair histogram.** Migration `alembic/versions/0001_initial.py:169-205` defines: `origin_stats`, `dest_stats`, `lane_stats`, `ip_stats`, `country_stats`, `origin_ip_country_stats`, `email_hmacs`, `phone_hmacs`, etc. — none track `(origin_country, destination_country)` frequency pairs. **Decision absorbed (AskUserQuestion 2026-06-03)**: add `country_route_stats jsonb DEFAULT '{}'::jsonb NOT NULL` via migration 0010; populate from the baseline updater; signal maturity-gated (fires only when `customer_observations >= 10`).

4. **`ip_fully_new` exists as a Context field; `ip_fully_new_for_customer` is a rule name** (not a Context field). The rule at `app/rules.yaml:96-100` evaluates `ip_fully_new AND customer_observations >= 10` with weight 0.35. The case-3 compound rule references the same primitive predicates (`ip_fully_new AND customer_observations >= 10`), so **both rules fire together** on case-3 transactions and compose under noisy-OR.

5. **Scoring math.** `app/scoring.py:27-30` documents BLOCK threshold = 0.80, REVIEW = 0.60 (from `app/rules.yaml` thresholds). Picking `case_3_compound` weight 0.70 (maturity_sensitive): on a mature customer, signal_score = noisyOR(0.35, 0.70) = 0.805 → BLOCK. On a maturity-0.5 customer: ~0.65 → REVIEW. Behavior degrades to REVIEW for low-maturity customers, which is correct: don't BLOCK new customers on a compound that's a behavioral-deviation signal.

6. **Rule pattern.** `app/rules.yaml` uses `name: ... condition: "..." weight: ... maturity_sensitive: true|false`. Existing compound example: `cloud_api_customer_deviation_iptype` (weight 0.55). Total current rule count: 58 (verification finding; CLAUDE.md says "79 rules" which may include Phase 5D-era additions — confirmed via grep: actual count is 58 on `feat/refactor`).

7. **Case-1 + case-2 fixtures do not declare `origin_via_carrier_dropoff` or any route-baseline field.** Default `False` on the new field + empty `country_route_stats` on existing fixtures preserves their behavior. Case-1 fires VPN burst rules; case-2 fires the 6-rule compound around API ATO. Neither touches the case-3 compound surface.

8. **Test pattern.** `tests/integration/test_context.py:1-60` exercises rule conditions via `base_ctx()` + `find_rule()`; `tests/unit/test_rules_dormancy_lockin.py:20-36` shows the truth-table pattern (all boolean combinations).

---

## Decisions absorbed

| Decision | Value | Source |
|---|---|---|
| 6A scope (amended) | Case-3a (established-customer compromise) + case-3b (brand-new-customer fraud) detection. Five new Context fields + three new rules + new schema (one new column + one new table) + synchronous update path + structured `Customer.registered_country` field. 10 commits. | Phase 6 prompt + amendment 2026-06-03 |
| Case-3a rule | `case_3_compound` — fires on established-customer behavioral deviation (route + dropoff + new IP + maturity gate). Empirical validation deferred to post-launch. | Phase 6 prompt |
| Case-3b simple rule | `cold_start_country_triangle_with_carrier_dropoff` — fires on brand-new customer (`customer_observations < 10`) shipping outside registered country with carrier-dropoff origin. | Amendment 2026-06-03 |
| Case-3b sophisticated rule | `cold_start_population_baseline_rare_with_carrier_dropoff` — fires on brand-new customer shipping a `(customer_country, origin_country, destination_country)` triple that's <2% of the tenant's route population (≥100 observations baseline) with carrier-dropoff. | Amendment 2026-06-03 |
| New Pydantic field 1 | `BookingRequest.shipment.origin_via_carrier_dropoff: bool = False` | Phase 6 prompt |
| New Pydantic field 2 | `CustomerData.registered_country: str \| None = None` (ISO 3166-1 alpha-2 `^[A-Z]{2}$` validation when not None) | Amendment 2026-06-03 (structured field rejecting address-string parsing) |
| Customer country derivation | Structured field passthrough — `ctx["customer_registered_country"] = payload.customer.registered_country`. NO address-string parsing. | Amendment 2026-06-03 (Q1 reconsideration) |
| Shipment country derivation | Direct passthrough from existing `Address.country: str \| None` (intermediate Python values inside build_context, used by triangle-mismatch derivation; not exposed as standalone rule-DSL fields) | Amendment 2026-06-03 (Q2 option a) |
| Compound rule case_3_compound | weight 0.70, `maturity_sensitive: true`, condition `origin_via_carrier_dropoff AND shipment_route_unfamiliar_for_customer AND ip_fully_new AND customer_observations >= 10` | Plan-time derivation against BLOCK band 0.80 |
| Compound rule cold_start_country_triangle | weight 0.65, `maturity_sensitive: false`, condition `customer_country_triangle_mismatch AND origin_via_carrier_dropoff AND customer_observations < 10` | Amendment 2026-06-03 |
| Compound rule cold_start_population_baseline_rare | weight 0.70, `maturity_sensitive: false`, condition `shipment_route_rare_for_tenant AND origin_via_carrier_dropoff AND customer_observations < 10` | Amendment 2026-06-03 |
| Customer-route baseline storage (case-3a) | New `customer_baselines.country_route_stats jsonb` column via migration 0010; populated by baseline updater on shipment commit; top-N covering ≥80%; maturity-gated to `customer_observations >= 10` | AskUserQuestion 2026-06-03 (initial) |
| Tenant-population route baseline (case-3b) | New `tenant_route_baselines` table via migration 0011; PK `(tenant_id, customer_country, origin_country, destination_country)`; RLS enforced; synchronous UPSERT on every booking commit; rarity threshold 2% with ≥100-observation minimum | Amendment 2026-06-03 |
| Customer.registered_country column | Migration 0011 adds `customers.registered_country VARCHAR(2) NULL`. Upsert COALESCE-preserves existing values (payload nulls don't overwrite operator-supplied data). | Amendment 2026-06-03 |
| Migration 0011 seed query | Direct column reads + jsonb path expressions (`s.origin->>'country'`); zero rows for prototype data; populates via runtime UPSERT once platform integration ships structured fields. | Amendment 2026-06-03 (Q3 option a, simplified by Q1 correction — no SQL parser function) |
| Latency budget | Accept ~4ms combined overhead (1 UPSERT in 6A.7 + 1 SELECT in 6A.8). Phase 5 p95 ~12ms → post-amendment ~16ms. Well within 200ms ceiling. Watch in 6E launch checklist Day 1-7 monitoring. | Amendment 2026-06-03 (Q4 option a) |
| New Context field count | 71 → **76** total (+5: `origin_via_carrier_dropoff`, `shipment_route_unfamiliar_for_customer`, `customer_registered_country`, `customer_country_triangle_mismatch`, `shipment_route_rare_for_tenant`) | Amendment 2026-06-03 |
| Signals NOT added | IP-country-unfamiliar (FP on travelers); customer-static-IP-set mechanism; address-string-matching; **address-string parsing for country extraction** (same string-format-variation problem; structured field is the principled fix) | Phase 6 prompt + amendment 2026-06-03 |
| In-process rarity cache | REJECTED — invalidation complexity outweighs ~1ms savings | Amendment 2026-06-03 (Q4 option b rejected) |
| Async rarity derivation | REJECTED — eventual consistency defeats cold-start case-3b goal | Amendment 2026-06-03 (Q4 option c rejected) |
| Rule count | 58 → **61** total (+3) | Amendment 2026-06-03 |
| Migration scope | TWO additive migrations: 0010 (case-3a `country_route_stats`) + 0011 (case-3b `customers.registered_country` column + `tenant_route_baselines` table) | Amendment 2026-06-03 |
| NO weight tuning | Existing rule weights untouched. The three NEW rules are detection capability additions, not tuning. | Project-wide discipline |
| Regression gate | Case-1 + case-2 fixtures continue to fire same decisions (defaults `False`/`None` + empty histograms + new rules' cold-start gates preserve behavior) | Phase 6 prompt |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md.
- **Reviewer panel MANDATORY per code commit** — no batch-mode skip. Per triage gate:
  - **6A.1 (migration 0010 + ShipmentData field)**: Never-Skip — new migration + ORM/model edit → **db-reviewer + security-auditor + senior-engineer + code-flow**.
  - **6A.2 (case-3a baseline updater + build_context derivation)**: standard path → **senior-engineer + security-auditor + code-flow + test-reviewer** (new tests land).
  - **6A.3 (case_3_compound rule + DSL whitelist test)**: Never-Skip — `app/rules.yaml` adds a rule → **senior-engineer + security-auditor + code-flow + test-reviewer**.
  - **6A.5 (CustomerData.registered_country field + customer_country_triangle_mismatch field + cold_start_country_triangle_with_carrier_dropoff rule + tests)**: Never-Skip (new Pydantic field + new rule in `app/rules.yaml`) → **senior-engineer + security-auditor + code-flow + test-reviewer**.
  - **6A.6 (migration 0011: customers.registered_country column + tenant_route_baselines table + RLS + seed query)**: Never-Skip (migration + RLS) → **senior-engineer + security-auditor + code-flow + db-reviewer**.
  - **6A.7 (customer upsert with registered_country + tenant_route_baselines update path)**: Never-Skip (auth/tenant-isolation surface + new module under `app/`) → **senior-engineer + security-auditor + code-flow + db-reviewer + test-reviewer**.
  - **6A.8 (shipment_route_rare_for_tenant Context field with DB access)**: Never-Skip (new Context derivation querying tenant table) → **senior-engineer + security-auditor + code-flow + db-reviewer + test-reviewer**.
  - **6A.9 (cold_start_population_baseline_rare_with_carrier_dropoff rule)**: Never-Skip (new rule in `app/rules.yaml`) → **senior-engineer + security-auditor + code-flow + test-reviewer**.
  - **6A.10 (regression validation + `.ai/decisions.md` amendment; renumbered from 6A.4)**: `.ai/decisions.md` borderline → **senior-engineer + code-flow + doc-reviewer**.
- Pre-commit gates: ruff, ruff-format, mypy --strict, pytest unit. Never bypassed except for declared-break commits with explicit gate-naming.
- Plan file slice for reviewer invocation: each commit's invocation tells reviewers "Plan file: PLAN_PHASE_6A.md, current commit: 6A.X, upcoming commits: 6A.(X+1) through 6A.10 sections — read only those sections."

---

## Cross-batch dependencies

- **6A → 6C**: case-3a + case-3b rules + new Context fields + structured `Customer.registered_country` must land before 6C replay measures detection rate. 6C export script hardcodes `customer.registered_country: "CA"` for case-3 records (Roulottes Lupien ground truth) and `null` for case-2 + approved.
- **6A independent of 6B**: 6A touches models + migrations + rules; 6B touches DEFAULT_VALUE_CAPS + currency defaults. Zero overlap.
- **6A → 6D**: 6D's AWS runbook (Phase B) gains a launch-blocking dependency note that platform integration must supply `customer.registered_country` + `origin_via_carrier_dropoff` in production booking payloads for case-3b detection to fire.
- **6A → 6E**: Phase 6E aggregate report enumerates the 76-field Context state, the three new rules, the population baseline subsystem, and the structured-field architectural pattern among Phase 6 deliverables.

---

## Commits

### 6A.1 — Migration 0010: `customer_baselines.country_route_stats` column + `ShipmentData.origin_via_carrier_dropoff` field

**Theme**: Schema and request-model additions that unlock case-3 derivation. Code that consumes them lands in 6A.2; rule that references them lands in 6A.3.

**Files**:
- NEW `alembic/versions/0010_country_route_stats.py` — add `country_route_stats jsonb DEFAULT '{}'::jsonb NOT NULL` to `customer_baselines`.
- MODIFY `app/models.py` — add `origin_via_carrier_dropoff: bool = False` to `ShipmentData` (the inner model under `BookingRequest.shipment`).

**Specifics**:
- Migration uses `op.add_column` with server-default `'{}'::jsonb`. Backfill is the server-default; no data backfill statement needed because Phase 6 has no production rows.
- Downgrade drops the column.
- `ShipmentData.origin_via_carrier_dropoff: bool = Field(default=False, description="True if shipment was dropped at a carrier facility rather than picked up from the origin address. Case-3 signal: spoofed ship-from + carrier-dropoff handoff.")`.

**Validation**:
- `alembic upgrade head` → confirm column present via `\d customer_baselines`.
- `alembic downgrade -1 && alembic upgrade head` round-trip clean.
- `pytest tests/ --asyncio-mode=auto` — full suite passes (default `False` preserves all existing fixtures).

**Risk level**: low. Single additive column + single optional bool field with safe default.

**Reversibility**: full via downgrade.

**Pre-commit verification**: ruff + mypy + unit tests + alembic round-trip locally.

**Observability**: no new metric events. Baseline updater already emits cache-update metrics; nothing changes here.

**Test changes**: None in this commit. Existing tests confirm the model addition doesn't break anything (default `False`).

**Rollback plan**: `alembic downgrade -1` + revert commit.

**Declared breaks**:
- **Scope**: `customer_baselines.country_route_stats` column exists but no writer populates it yet.
  **Resolved in**: 6A.2 (baseline updater populates on shipment commit).
- **Scope**: `ShipmentData.origin_via_carrier_dropoff` field exists but no consumer reads it yet.
  **Resolved in**: 6A.2 (`build_context` reads it into Context).

**Reviewer routing**: Never-Skip (new migration) → **db-reviewer + senior-engineer + security-auditor + code-flow** (full standard panel + db-reviewer). No tests touched → no test-reviewer.

---

### 6A.2 — Baseline updater populates `country_route_stats`; `build_context` derives both new Context fields

**Theme**: Wire the producers and consumers of the two new fields. After this commit, the Context dict carries 73 fields. Rule references land in 6A.3.

**Files**:
- MODIFY `app/baseline_updater.py` (or wherever the shipment-commit baseline-write happens — verification finds the exact location) — add increment to `country_route_stats[f"{origin_country}||{destination_country}"]` on shipment record commit. Origin/destination country extracted from address parsing (existing logic in build_context — re-use the parser).
- MODIFY `app/context.py` — `build_context` reads `payload.shipment.origin_via_carrier_dropoff` into `ctx["origin_via_carrier_dropoff"]`. Reads `customer_baseline.country_route_stats` jsonb, derives top-N covering ≥80% of observations, sets `ctx["shipment_route_unfamiliar_for_customer"]` = True iff `(current_origin_country, current_destination_country)` pair is NOT in the top-N AND `customer_observations >= 10` (else False — maturity gate).
- MODIFY `app/rules.py` — `ALLOWED_CONTEXT_FIELDS` adds `"origin_via_carrier_dropoff"` and `"shipment_route_unfamiliar_for_customer"`. Count becomes 73.
- MODIFY `tests/unit/test_rules_whitelist.py` — update `assert len(ALLOWED_CONTEXT_FIELDS) == 71` → `== 73`; assert both new field names present.

**Specifics**:
- **Top-N derivation**: helper `_derive_unfamiliar_route(country_route_stats: dict[str, int], current_pair: str, total_obs: int) -> bool`. Sort histogram descending by count, cumulative until ≥80% of `total_obs`, return True if `current_pair` not in that prefix.
- **Edge cases**:
  - Empty histogram (`{}`) → return False (cold-start; no baseline). Maturity gate (customer_observations < 10) catches this redundantly.
  - Single-route customer who deviates → True (the prefix is one route; current pair not in it).
  - Country parsing failure → return False (no signal rather than false positive).
- **Maturity gate is enforced at build_context level**, not in the rule. The rule still has `customer_observations >= 10` in its condition for clarity/defense-in-depth.

**Validation**:
- `mypy app/` strict — passes (returns/types annotated).
- `pytest tests/unit/test_build_context*.py -v` — passes; existing tests untouched because Context dict expansion is additive.
- `pytest tests/ --asyncio-mode=auto` — full suite passes; case-1 + case-2 fixtures default to `False` for both new fields, no regression.

**Risk level**: medium. Touches the Context-build pipeline. Highest-risk surface is the country-parsing path inside `_derive_unfamiliar_route` — if address parsing returns inconsistent country codes (e.g., "CA" vs "Canada" vs ""), the histogram fragments and the signal becomes noisy. Mitigation: re-use the existing build_context country parser, don't add a parallel parser.

**Reversibility**: full via revert.

**Pre-commit verification**: ruff + mypy + unit tests.

**Observability**: no new metric events. (6C measurement validates the signal; not 6A.)

**Test changes**:
- NEW unit test file `tests/unit/test_build_context_case3.py`:
  - `test_origin_via_carrier_dropoff_passthrough`: payload with True → Context has True; payload with False → Context has False; payload omitted → Context has False (model default).
  - `test_route_unfamiliar_empty_histogram_returns_false`: empty `country_route_stats` → False.
  - `test_route_unfamiliar_below_maturity_returns_false`: histogram populated, `customer_observations = 5` → False (maturity gate).
  - `test_route_unfamiliar_current_in_top_n_returns_false`: customer with histogram `{"CA||CA": 50, "CA||US": 30, "CA||MX": 5}`, current pair "CA||CA" → False.
  - `test_route_unfamiliar_current_not_in_top_n_returns_true`: same histogram, current pair "CA||GB" → True.
  - `test_route_unfamiliar_top_n_covers_80_percent`: customer with `{"CA||CA": 100, "CA||US": 20, "CA||MX": 5}` (total 125, 80% = 100); top-1 covers 80%; current pair "CA||US" → True (not in top-1).
- MODIFY `tests/unit/test_rules_whitelist.py` — bump field count assertion 71 → 73.

**Rollback plan**: revert; `country_route_stats` column from 6A.1 remains (harmless empty column).

**Declared breaks**:
- **Scope**: Two new Context fields are derived and present in `ALLOWED_CONTEXT_FIELDS`, but no rule references them yet.
  **Resolved in**: 6A.3 (`case_3_compound` rule references both).

**Reviewer routing**: standard path — **senior-engineer + security-auditor + code-flow + test-reviewer** (tests change).

---

### 6A.3 — `case_3_compound` rule in `app/rules.yaml` + rule unit tests

**Theme**: Land the production rule that fires the compound signal. After this commit, case-3 detection is live in code.

**Files**:
- MODIFY `app/rules.yaml` — add `case_3_compound` rule (weight 0.70, maturity_sensitive: true, condition combines the new fields with `ip_fully_new` + maturity gate).
- NEW `tests/unit/test_rule_case_3_compound.py` — truth-table unit tests against the rule.

**Specifics**:

```yaml
- name: case_3_compound
  description: "Compound case-3 fraud pattern: carrier-dropoff origin with route deviation from customer baseline and previously-unseen IP. Requires established customer (≥10 observations) so cold-start doesn't trip."
  condition: "origin_via_carrier_dropoff AND shipment_route_unfamiliar_for_customer AND ip_fully_new AND customer_observations >= 10"
  weight: 0.70
  maturity_sensitive: true
```

- Weight rationale: BLOCK band 0.80. Existing `ip_fully_new_for_customer` rule (weight 0.35) co-fires on the same primitive predicates. signal_score at full maturity = noisyOR(0.35, 0.70) = 0.805 → BLOCK. At maturity 0.5 with `MATURITY_K = 0.5`: ~0.65 → REVIEW (degrades safely; don't BLOCK low-maturity customers).
- `maturity_sensitive: true` because it requires customer history.

**Validation**:
- DSL parser accepts the condition (whitelist check passes for `origin_via_carrier_dropoff`, `shipment_route_unfamiliar_for_customer`, `ip_fully_new`, `customer_observations` — all in ALLOWED_CONTEXT_FIELDS post-6A.2).
- `pytest tests/unit/test_rule_case_3_compound.py -v` — all pass.
- `pytest tests/ --asyncio-mode=auto` — full suite passes; case-1 + case-2 regression intact (compound rule doesn't fire on their fixtures because new fields default False).

**Risk level**: low. Single additive rule; weight derivation is conservative.

**Reversibility**: full via revert (rule removal).

**Pre-commit verification**: ruff + mypy + unit tests + YAML lint.

**Observability**: existing rule-fire EMF events emit `rule_name=case_3_compound` automatically when fired. No new event class.

**Test changes**:
- NEW `tests/unit/test_rule_case_3_compound.py`:
  - `test_all_three_signals_true_fires`: `origin_via_carrier_dropoff=True, shipment_route_unfamiliar_for_customer=True, ip_fully_new=True, customer_observations=15` → rule fires.
  - `test_dropoff_false_does_not_fire`: same but `origin_via_carrier_dropoff=False` → not fired.
  - `test_route_familiar_does_not_fire`: same but `shipment_route_unfamiliar_for_customer=False` → not fired.
  - `test_ip_known_does_not_fire`: same but `ip_fully_new=False` → not fired.
  - `test_low_maturity_does_not_fire`: same but `customer_observations=5` → not fired (maturity gate inside rule condition).
  - `test_weight_is_07`: assert the rule's weight literal is 0.70 (catches accidental edits in future).
  - `test_maturity_sensitive_flag_true`: assert maturity_sensitive is True.

**Rollback plan**: remove rule; revert tests.

**Declared breaks**: none. Self-contained.

**Reviewer routing**: Never-Skip (new rule added to `app/rules.yaml`) + test-reviewer (tests change) → **senior-engineer + security-auditor + code-flow + test-reviewer**.

---

### 6A.5 — `CustomerData.registered_country` Pydantic field + `customer_country_triangle_mismatch` Context field + `cold_start_country_triangle_with_carrier_dropoff` compound rule

**Theme**: Simple case-3b detection. Adds structured `Customer.registered_country` field, two new Context fields, and one compound rule. Standalone-firing surface narrowed by compound design.

**Files**:
- MODIFY `app/models.py` — add `registered_country: str | None = Field(default=None, min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")` to `CustomerData` (ISO 3166-1 alpha-2 validation when not None; defaults None for backward compat).
- MODIFY `app/context.py` — add two derivations in `build_context`:
  - `ctx["customer_registered_country"] = payload.customer.registered_country` (direct passthrough)
  - Compute Python intermediates `_origin_country = payload.shipment.origin.country` and `_dest_country = payload.shipment.destination.country` (NOT exposed in ctx — used only for triangle-mismatch derivation).
  - `ctx["customer_country_triangle_mismatch"] = (ctx["customer_registered_country"] is not None AND _origin_country is not None AND _dest_country is not None AND ctx["customer_registered_country"] != _origin_country AND ctx["customer_registered_country"] != _dest_country)`.
- MODIFY `app/rules.py` — `ALLOWED_CONTEXT_FIELDS` 73 → 75 (+2: `customer_registered_country`, `customer_country_triangle_mismatch`). `shipment_origin_country` / `shipment_destination_country` are NOT added to whitelist — they're Python intermediates inside build_context, never referenced by rule conditions.
- MODIFY `app/rules.yaml` — add `cold_start_country_triangle_with_carrier_dropoff` rule.
- NEW `tests/unit/test_customer_registered_country.py` — Pydantic model validation tests (valid ISO codes, invalid formats including lowercase, wrong length, non-alpha, None handling, model serialization round-trip).
- NEW `tests/unit/test_country_triangle_mismatch.py` — truth-table tests for the Context field (6-8 cases: all three countries set + all distinct → True; customer_country equal to origin → False; equal to destination → False; any country None → False).
- NEW `tests/unit/test_rule_cold_start_country_triangle.py` — truth-table tests for the compound rule (3 conditions × 2 values = 8 combinations; verify maturity gate at customer_observations boundary).
- MODIFY `tests/unit/test_rules_whitelist.py` — field count `73` → `75`; assert presence of both new field names.

**Specifics**:

Compound rule:
```yaml
- name: cold_start_country_triangle_with_carrier_dropoff
  description: |
    Brand-new customer (no transaction baseline) ships outside their declared country
    AND uses carrier-dropoff origin handoff. Each signal alone is benign-ish in some
    legitimate cases (cross-border freight forwarders, small businesses testing carrier
    dropoff). Combination is sharp: targets brand-new-customer fraud (case-3b) where
    customer transaction baseline cannot serve as deviation anchor.
  condition: "customer_country_triangle_mismatch AND origin_via_carrier_dropoff AND customer_observations < 10"
  weight: 0.65
  maturity_sensitive: false
```

Weight rationale: 0.65 sits just below BLOCK threshold (0.80) standalone — lands in REVIEW band by itself. Composes with other rules (IP-quality, value-tier, IP-novelty cold-start) to reach BLOCK when multiple signals fire. Maturity-insensitive because the cold-start gate (`< 10`) is in the condition.

Safety property: derivation returns False when ANY of the three countries is None. Corpora without ground-truth structured data (case-2, approved) cannot trigger the country-mismatch rule by accident.

**Validation**:
- `pytest tests/unit/test_customer_registered_country.py tests/unit/test_country_triangle_mismatch.py tests/unit/test_rule_cold_start_country_triangle.py -v` — all pass.
- `pytest tests/ --asyncio-mode=auto` — full suite passes; case-1 + case-2 regression intact.
- `ruff check app/ tests/` clean; `mypy app/` clean.
- Backward compat: existing payloads without `customer.registered_country` continue to work (field defaults None).

**Risk level**: low. Field + derivation + rule with truth-table coverage. No schema migration in this commit (that's 6A.6).

**Reversibility**: full via revert.

**Pre-commit verification**: ruff + mypy + unit tests.

**Observability**: rule-fire EMF event emits `rule_name=cold_start_country_triangle_with_carrier_dropoff` automatically when fired.

**Test changes**: 3 new test files (~20 tests total); 1 modified.

**Rollback plan**: revert.

**Declared breaks**: none. Self-contained.

**Reviewer routing**: Never-Skip (new Pydantic field + new rule in `app/rules.yaml`) → **senior-engineer + security-auditor + code-flow + test-reviewer**.

---

### 6A.6 — Migration 0011: `customers.registered_country` column + `tenant_route_baselines` table + seed

**Theme**: Schema foundations for case-3b detection. Two additive schema changes in one migration since both target the case-3b work.

**Files**:
- NEW `alembic/versions/0011_case_3b_schema.py`.

**Specifics**:

```sql
-- 1. customers.registered_country column (nullable; populated by platform integration via customer upsert in 6A.7)
ALTER TABLE customers ADD COLUMN registered_country VARCHAR(2);

-- 2. tenant_route_baselines table
CREATE TABLE tenant_route_baselines (
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    customer_country VARCHAR(2) NOT NULL,
    origin_country VARCHAR(2) NOT NULL,
    destination_country VARCHAR(2) NOT NULL,
    observation_count BIGINT NOT NULL DEFAULT 0,
    last_updated TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, customer_country, origin_country, destination_country)
);

CREATE INDEX ix_tenant_route_baselines_tenant ON tenant_route_baselines (tenant_id);

-- 3. RLS (active under riskd_app_login per Phase 5D)
ALTER TABLE tenant_route_baselines ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_route_baselines_isolation ON tenant_route_baselines
    USING (tenant_id = current_setting('app.tenant_id')::integer);
```

**Seed query** (within the same migration, after schema additions):
```sql
INSERT INTO tenant_route_baselines (
    tenant_id, customer_country, origin_country, destination_country, observation_count
)
SELECT
    s.tenant_id,
    c.registered_country AS customer_country,
    s.origin->>'country' AS origin_country,
    s.destination->>'country' AS destination_country,
    COUNT(*) AS observation_count
FROM shipments s
JOIN customers c ON c.id = s.customer_id AND c.tenant_id = s.tenant_id
WHERE c.registered_country IS NOT NULL
  AND s.origin->>'country' IS NOT NULL
  AND s.destination->>'country' IS NOT NULL
GROUP BY 1, 2, 3, 4;
```

Notes:
- Direct column reads + jsonb path expressions. No `parse_country` SQL function. No address-string parsing — structured-field architectural pattern.
- `s.origin->>'country'` extracts country from the existing jsonb `shipments.origin` column (Pydantic `Address.country: str | None` already structured).
- For prototype-stage repo with no customers having `registered_country` set, the seed yields 0 rows. The table populates via 6A.7 runtime UPSERT once platform integration starts supplying the field.

Downgrade:
```sql
DROP TABLE tenant_route_baselines;
ALTER TABLE customers DROP COLUMN registered_country;
```

**Validation**:
- `alembic upgrade head` clean; round-trip `alembic downgrade -1 && alembic upgrade head` clean.
- `\d tenant_route_baselines` confirms structure + RLS policy + index.
- `\d customers` confirms `registered_country` column.
- Cross-tenant query under `riskd_app_login` without `set_tenant_id` returns 0 rows (RLS enforces).
- `pytest tests/ --asyncio-mode=auto` — full suite passes.

**Risk level**: low. Two additive schema changes; seed query is idempotent.

**Reversibility**: full via downgrade.

**Pre-commit verification**: ruff + mypy + unit tests + alembic round-trip.

**Observability**: no new events.

**Test changes**: integration tests in 6A.7 + 6A.8 exercise the new schema; no test addition in this migration commit.

**Rollback plan**: `alembic downgrade -1` + revert commit.

**Declared breaks**:
- **Scope**: `customers.registered_country` column exists but no upsert writes it yet.
  **Resolved in**: 6A.7 (customer upsert with registered_country).
- **Scope**: `tenant_route_baselines` table exists but no writer populates it yet.
  **Resolved in**: 6A.7 (update path).
- **Scope**: no reader consumes `tenant_route_baselines` yet.
  **Resolved in**: 6A.8 (rarity derivation).

**Reviewer routing**: Never-Skip (migration + new column + new table + RLS) → **senior-engineer + security-auditor + code-flow + db-reviewer**.

---

### 6A.7 — Customer upsert with `registered_country` + synchronous `tenant_route_baselines` update path

**Theme**: Producer path. Wire platform-supplied `customer.registered_country` into the customer upsert; after each booking commit, UPSERT the `(customer_country, origin_country, destination_country)` triple count.

**Files**:
- MODIFY `app/customers.py` (or wherever customer upsert lives — verification at execution time finds exact path) — include `registered_country` in upsert column set with COALESCE preservation: `registered_country = COALESCE(EXCLUDED.registered_country, customers.registered_country)`. Operator-supplied values not overwritten by payload nulls.
- MODIFY `app/api/booking.py` — after shipment commit in the evaluate endpoint transaction, call `update_tenant_route_baseline(conn, tenant_id, customer.registered_country, shipment.origin.country, shipment.destination.country)`.
- NEW `app/tenant_route_baselines.py` — module with the `update_tenant_route_baseline` function (UPSERT).

**Specifics**:

```python
async def update_tenant_route_baseline(
    conn: asyncpg.Connection,
    tenant_id: int,
    customer_country: str | None,
    origin_country: str | None,
    destination_country: str | None,
) -> None:
    """Increment population count for this route triple. No-op if any country is None."""
    if not (customer_country and origin_country and destination_country):
        return
    await conn.execute(
        """
        INSERT INTO tenant_route_baselines (
            tenant_id, customer_country, origin_country, destination_country,
            observation_count, last_updated
        )
        VALUES ($1, $2, $3, $4, 1, now())
        ON CONFLICT (tenant_id, customer_country, origin_country, destination_country)
        DO UPDATE SET
            observation_count = tenant_route_baselines.observation_count + 1,
            last_updated = now()
        """,
        tenant_id, customer_country, origin_country, destination_country,
    )
```

Critical considerations:
- Same transaction as booking commit. Baseline-update failure rolls back the booking. Desirable for consistency; latency-impacting but ~1ms.
- RLS enforces — UPSERT requires `app.tenant_id` set on the connection (booking endpoint already does this per Phase 5D).
- No-op on any None country — bookings without structured data don't pollute the baseline.
- COALESCE pattern on customer upsert: payload nulls don't overwrite operator-supplied `registered_country`.

**Validation**:
- `pytest tests/unit/test_tenant_route_baselines.py -v` — pure-DB unit tests pass.
- `pytest tests/integration/test_booking_baseline_update.py -v` — integration test: POST booking with all three countries → query `tenant_route_baselines` for the row → assert count incremented. Second POST with same triple → count = 2. POST with `customer.registered_country=None` → baseline unchanged.
- `pytest tests/ --asyncio-mode=auto` — full suite passes; case-1 + case-2 regression intact.
- `ruff check app/ tests/` clean; `mypy app/` clean.

**Risk level**: medium. Synchronous write per booking — affects latency budget (+~1ms). Mitigated by PK index + UPSERT efficiency. RLS surface — db-reviewer + security-auditor cover.

**Reversibility**: full via revert.

**Pre-commit verification**: ruff + mypy + unit + integration tests.

**Observability**: existing booking endpoint emits decision events; this commit adds no new EMF events but the booking-commit latency metric naturally captures the new write overhead.

**Test changes**:
- NEW `tests/unit/test_tenant_route_baselines.py` (~5 tests for the update function: insert, increment, None handling, COALESCE behavior on customer upsert).
- NEW `tests/integration/test_booking_baseline_update.py` (~3 tests: end-to-end booking → baseline row asserted; second booking → count = 2; None-country booking → baseline unchanged).

**Rollback plan**: revert.

**Declared breaks**:
- **Scope**: baseline rows are being written but no Context derivation reads them yet.
  **Resolved in**: 6A.8 (rarity derivation).

**Reviewer routing**: Never-Skip (new module + auth-adjacent transaction surface + new RLS-table writer) → **senior-engineer + security-auditor + code-flow + db-reviewer + test-reviewer**.

---

### 6A.8 — `shipment_route_rare_for_tenant` Context field (DB-backed derivation)

**Theme**: Reader path. Derive the rarity signal from `tenant_route_baselines` at evaluation time.

**Files**:
- MODIFY `app/context.py` — add async derivation `derive_route_rarity(conn, tenant_id, customer_country, origin_country, destination_country) -> bool`; call from `build_context` with the three country values extracted from payload/customer.
- MODIFY `app/rules.py` — `ALLOWED_CONTEXT_FIELDS` 75 → 76 (+1: `shipment_route_rare_for_tenant`).
- NEW `tests/unit/test_route_rare_for_tenant.py` — unit tests covering edge cases.
- MODIFY `tests/unit/test_rules_whitelist.py` — field count `75` → `76`; assert presence of new field.

**Specifics**:

```python
async def derive_route_rarity(
    conn: asyncpg.Connection,
    tenant_id: int,
    customer_country: str | None,
    origin_country: str | None,
    destination_country: str | None,
) -> bool:
    """True iff the triple is <2% of tenant population and baseline ≥100 observations."""
    if not (customer_country and origin_country and destination_country):
        return False
    row = await conn.fetchrow(
        """
        WITH triple AS (
            SELECT observation_count AS triple_count
            FROM tenant_route_baselines
            WHERE tenant_id = $1
              AND customer_country = $2
              AND origin_country = $3
              AND destination_country = $4
        ),
        total AS (
            SELECT COALESCE(SUM(observation_count), 0) AS total_count
            FROM tenant_route_baselines
            WHERE tenant_id = $1
        )
        SELECT
            COALESCE((SELECT triple_count FROM triple), 0)::bigint AS triple_count,
            (SELECT total_count FROM total)::bigint AS total_count
        """,
        tenant_id, customer_country, origin_country, destination_country,
    )
    triple_count = row["triple_count"]
    total_count = row["total_count"]
    if total_count < 100:
        return False
    return (triple_count / total_count) < 0.02
```

Behavior:
- Tenant with no/sparse baseline (<100 observations): False (no FP risk on brand-new tenants).
- Mature baseline + rare triple: True.
- Mature baseline + common triple: False.
- Any country None: False.

The 100-observation threshold and 2% rarity cutoff are initial values; documented in `.ai/decisions.md` for post-launch tuning.

**Validation**:
- Unit tests cover: empty baseline, sparse baseline, mature + rare triple, mature + common triple, None-country, boundary cases (exactly 2%, exactly 100 observations).
- Integration test (against 6A.6-seeded data): seed `tenant_route_baselines` with known distribution; assert derivation matches truth table.
- `pytest tests/ --asyncio-mode=auto` — full suite passes.

**Risk level**: medium. New eval-time DB query (+~1ms per booking). Combined with 6A.7's UPSERT: ~4ms p95 overhead total. Phase 5 baseline ~12ms → post-amendment ~16ms p95. Comfortable within 200ms ceiling. **Watch in 6E launch checklist Day 1-7 monitoring**; calibration backlog acts if trend approaches 195ms.

**Reversibility**: full via revert.

**Pre-commit verification**: ruff + mypy + unit tests.

**Observability**: no new events; build_context latency naturally captures the new query.

**Test changes**: NEW `tests/unit/test_route_rare_for_tenant.py` (~8 tests); MODIFY whitelist test.

**Rollback plan**: revert.

**Declared breaks**:
- **Scope**: `shipment_route_rare_for_tenant` is in `ALLOWED_CONTEXT_FIELDS` and derived, but no rule references it yet.
  **Resolved in**: 6A.9 (population-baseline-rare compound rule).

**Reviewer routing**: Never-Skip (new Context derivation with DB access in evaluation path) → **senior-engineer + security-auditor + code-flow + db-reviewer + test-reviewer**.

---

### 6A.9 — `cold_start_population_baseline_rare_with_carrier_dropoff` compound rule

**Theme**: Sophisticated case-3b detection. Tenant-population-derived rarity + carrier-dropoff + cold-start gate.

**Files**:
- MODIFY `app/rules.yaml` — add the rule.
- NEW `tests/unit/test_rule_population_baseline_rare.py` — truth-table tests.

**Specifics**:

```yaml
- name: cold_start_population_baseline_rare_with_carrier_dropoff
  description: |
    Brand-new customer shipping a route that is rare in the tenant's population
    (<2% of historical triples) AND using carrier-dropoff origin handoff. More
    accurate than the simple country-triangle compound because it uses the
    tenant's actual customer population as the baseline rather than a fixed
    country-equality heuristic. Requires sufficient tenant baseline data
    (≥100 historical observations) to fire.
  condition: "shipment_route_rare_for_tenant AND origin_via_carrier_dropoff AND customer_observations < 10"
  weight: 0.70
  maturity_sensitive: false
```

Weight 0.70 — slightly higher than the simple compound (0.65) because the signal is more specific (tenant-population-derived rather than fixed heuristic).

Both case-3b compounds can fire on the same booking — they're complementary. A Roulottes Lupien booking against a Canadian-freight tenant fires both. Against a US-multinational tenant with diverse routes, only the simple compound fires (population baseline doesn't see CA-US-US as rare). Noisy-OR composition behaves correctly per tenant's data state.

**Validation**:
- Truth-table tests (3 conditions = 8 combinations).
- Integration test: seed `tenant_route_baselines` with known distribution; POST booking with rare triple + carrier dropoff + new customer → assert rule fires.
- Integration test: same booking but established customer (`customer_observations=15`) → rule does NOT fire (cold-start gate).
- Integration test: same booking but tenant baseline empty → rule does NOT fire (insufficient data, derivation returns False).
- `pytest tests/ --asyncio-mode=auto` — full suite passes; case-1 + case-2 regression intact.

**Risk level**: low. Single rule addition; depends on 6A.6, 6A.7, 6A.8 for the underlying signal.

**Reversibility**: full via revert (rule removal).

**Pre-commit verification**: ruff + mypy + unit tests + YAML lint.

**Observability**: rule-fire EMF event emits `rule_name=cold_start_population_baseline_rare_with_carrier_dropoff` automatically.

**Test changes**: NEW `tests/unit/test_rule_population_baseline_rare.py` (~10 tests including integration).

**Rollback plan**: revert.

**Declared breaks**: none.

**Reviewer routing**: Never-Skip (new rule in `app/rules.yaml`) → **senior-engineer + security-auditor + code-flow + test-reviewer**.

---

### 6A.10 — Phase 6A regression validation + `.ai/decisions.md` amendment (renumbered from 6A.4)

**Theme**: Regression-gate confirmation + comprehensive decisions amendment covering case-3a vs case-3b, the structured-field architectural pattern, both new case-3b compounds, and the tenant route population baseline subsystem.

**Files**:
- MODIFY `.ai/decisions.md` — expanded Phase 6A section.
- NO code changes.

**Specifics** — decisions amendment covers:

1. **Case-3a vs case-3b threat model distinction**:
   - Case-3a (established-customer compromise): `case_3_compound` rule. Empirical validation deferred to post-launch when platform supplies carrier-dropoff + case-3a fraud is observed.
   - Case-3b (brand-new-customer fraud): `cold_start_country_triangle_with_carrier_dropoff` (simple) + `cold_start_population_baseline_rare_with_carrier_dropoff` (sophisticated). The 95-record Roulottes Lupien census is case-3b — single-customer cluster caveat documented.

2. **Structured-field architectural pattern** (parallels carrier-dropoff):
   - Platform integration supplies structured signals at booking time.
   - freightsentry-riskd consumes via Pydantic field passthrough.
   - Replay corpus injects known ground truth (CA for Roulottes Lupien); other corpora None/False.
   - Signal returns False when None — corpora without ground truth cannot trigger detection rules accidentally.
   - REJECTED: address-string parsing for country extraction. Same family of problem as address-string-matching that was dropped earlier (format variation makes parsers silently unreliable).

3. **Five new Context fields**: `origin_via_carrier_dropoff`, `shipment_route_unfamiliar_for_customer`, `customer_country_triangle_mismatch`, `customer_registered_country`, `shipment_route_rare_for_tenant`. Total `ALLOWED_CONTEXT_FIELDS`: 71 → **76** (+5). `shipment_origin_country` / `shipment_destination_country` are Python intermediates inside build_context (not whitelisted; never referenced by rule conditions).

4. **Three new rules**:
   - `case_3_compound` (case-3a; weight 0.70, maturity_sensitive)
   - `cold_start_country_triangle_with_carrier_dropoff` (case-3b simple; weight 0.65)
   - `cold_start_population_baseline_rare_with_carrier_dropoff` (case-3b sophisticated; weight 0.70)
   Rule count: 58 → **61** (+3).

5. **Tenant route population baseline subsystem**:
   - New table `tenant_route_baselines` (PK on `(tenant_id, customer_country, origin_country, destination_country)`; RLS enforced).
   - Initial seed via migration 0011 — zero rows for prototype data (no platform-supplied structured countries yet).
   - Synchronous UPSERT on every booking commit (6A.7).
   - Eval-time derivation with 2% rarity threshold + 100-observation minimum (6A.8).
   - Initial thresholds documented for post-launch tuning.

6. **Signals NOT added** (operator decisions): IP-country-unfamiliar (traveler FP), customer-static-IP-set mechanism, address-string-matching, address-string parsing for country extraction.

7. **Architectural concerns documented for post-launch**:
   - Trust-suppression on mature accounts (compromised mature account benefits from low `account_prior`; signals fire but combined score may not reach BLOCK). Phase 7+ workstream — capability-based trust, session-anomaly signals, asymmetric trust freeze.

8. **Latency budget**: ~4ms p95 added from 6A.7 + 6A.8. Phase 5 baseline ~12ms → post-amendment ~16ms. Within budget. Watch in 6E launch checklist Day 1-7 monitoring.

**Validation**:
- `pytest tests/integration/test_case_1.py tests/integration/test_case_2.py -v` — both pass (explicit regression gate).
- `pytest tests/ --asyncio-mode=auto` — full suite passes.
- `ruff check app/ tests/` clean; `mypy app/` strict clean.

**Risk level**: trivial. Doc + regression-gate confirmation.

**Reviewer routing**: → **senior-engineer + code-flow + doc-reviewer**.

**Declared breaks**: none.

---

## End-of-batch state (after 6A.10)

- **Migrations**: 0010 (`customer_baselines.country_route_stats` jsonb) + 0011 (`customers.registered_country` VARCHAR(2) NULL + `tenant_route_baselines` table with RLS).
- **Pydantic model additions**: `ShipmentData.origin_via_carrier_dropoff: bool = False`; `CustomerData.registered_country: str | None = None` (ISO 3166-1 alpha-2 validation).
- **`ALLOWED_CONTEXT_FIELDS`**: 71 → **76** (+5: origin_via_carrier_dropoff, shipment_route_unfamiliar_for_customer, customer_registered_country, customer_country_triangle_mismatch, shipment_route_rare_for_tenant).
- **Rules**: 58 → **61** (+3: case_3_compound, cold_start_country_triangle_with_carrier_dropoff, cold_start_population_baseline_rare_with_carrier_dropoff).
- **New subsystem**: tenant route population baseline (table + RLS + synchronous UPSERT + eval-time rarity derivation).
- **Test count delta**: ~50 new tests across ~7 new test files; ~2 modified.
- Case-1 + case-2 integration regression GREEN.
- `.ai/decisions.md` carries Phase 6A architectural rationale + structured-field pattern + threat-model distinction.
- Detection MEASUREMENT happens in 6C against the 95-record Roulottes Lupien census (case-3b).

## Open items handed to 6B/6C/6D/6E

- **6C** export-from-freight_risk script hardcodes `customer.registered_country: "CA"` + `origin_via_carrier_dropoff: true` for case-3 records; `null` / `false` for case-2 + approved.
- **6C** case-3 detection target restructured: ≥85% of 95 records reach REVIEW or BLOCK via any compound (case-3a `case_3_compound` is NOT expected to fire on this census; case-3b compounds are).
- **6D** AWS GUI runbook (Phase B) flags platform integration as launch-blocking dependency: production payloads must supply `customer.registered_country` + `origin_via_carrier_dropoff` for case-3b detection to fire.
- **6E** aggregate report enumerates 76-field state, 3 new rules, structured-field pattern, population baseline subsystem.
- **6E** calibration backlog adds: trust-suppression on mature accounts (Phase 7+); population baseline threshold tuning; case_3_compound empirical validation deferred.
