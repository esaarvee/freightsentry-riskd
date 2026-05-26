# Phase 2 — Batch 2B Plan — Context derivations + `shipment_volume_30d` cleanup

> **Status (2026-05-26)**: Pending operator approval after 2A execution. Re-review this plan against 2A's execution learnings (any drift in `CustomerState` shape or scoring API) before approving.

Batch 2B extends `build_context` with the new fields Phase 2C rules consume, adds the cross-customer recipient-overlap count, drops the unused `shipment_volume_30d` column, and extends the DSL whitelist with each new field name. No scoring changes; no rule additions yet (those land in 2C).

Target: 6 commits. (2B.6 absorbs the `shipments.destination_hmac` column add — confirmed missing from Phase 1's `0001_initial.py` migration via direct grep, so the scope is baked in upfront rather than treated as a maybe-branch.)

---

## Decisions absorbed (batch-relevant subset)

| Decision | Value | Source |
|---|---|---|
| `customer_locked_cloud_api` | Derived in `build_context()` (NOT stored as a column). Threshold per verification §3.4: `cloud_share_n / value_n > 0.95 AND api_share_n / value_n > 0.95 AND value_n >= 20`. | Phase 2 bootstrap |
| `ip_familiarity_tier` | /24-match only for `family_familiar`. No "cloud + ASN" shortcut. Already implemented in `app/baseline.py:417-427`; this batch exposes the tier value (not just the booleans) to the Context so rules referencing it can fire. | Verification §2.2 — already in code |
| `days_since_last_booking` | Derived in Context from `baseline.last_booking_ts` and `payload.booking_ts` (or `as_of`). | Phase 2 bootstrap |
| Recipient cross-customer count | SQL count bounded by `tenant_id` AND 30-day window AND `destination_hmac`. Tenant scoping is the security boundary; cross-tenant leakage = security finding. | Phase 2 bootstrap "Watch points" |
| `shipment_volume_30d` column | Drop in this batch via migration. Replace any reads with `customer_velocity_30d` (already a Context field from Phase 1 via `count_user_30d`). | Operator decision 2026-05-26 |
| DSL whitelist extension | Each new Context field gets added to `ALLOWED_CONTEXT_FIELDS` in `app/rules.py`. The whitelist mechanism is unchanged (security boundary stays at `MappingProxyType` + `__builtins__: {}`); only the field set grows. | Bootstrap "DO: Extend the DSL field whitelist" |
| New fields land in Context only when they're consumed by a rule | Per "no dead code" convention. If 2B adds a field, 2C's rule additions must reference it; otherwise the field is removed. | `.ai/conventions.md` |
| Async velocity counts | Sequential awaits on the txn connection (per Phase 1 deviation in `.ai/decisions.md`). The new recipient-overlap count adds one more sequential await. Phase 5 load test revisits if context-load budget tightens. | Phase 1 deviation |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md. **Reviewer-panel quota is available** for Phase 2 — every code-path commit runs the full panel at commit time (no retro-panel fallback pattern needed).
- Reviewer routing per CLAUDE.md triage gate:
  - **2B.1 migration**: standard path with **db-reviewer** (`alembic/versions/` change).
  - **2B.2-2B.5 Context / velocity / whitelist edits**: standard path (senior-engineer + security-auditor + code-flow-reviewer). Test-reviewer when tests change.
  - **2B.6 destination_hmac migration + recipient SQL**: standard path with **db-reviewer** (migration adds shipments column + index) + **security-auditor priority** (cross-tenant query — never-skip per CLAUDE.md's "any commit touching tenant_id scoping" plus a never-skip migration).

Reviewer-invocation slice template:
> `Plan file: PLAN_PHASE_2B.md, current commit: 2B.N (<title>), upcoming commits: 2B.{N+1} through 2B.6 sections. Read only those sections.`

---

## Cross-batch dependencies

- **Consumes from 2A**: `CustomerState` shape is now defined; `account_prior` is computed. Independent: Batch 2B doesn't touch `app/scoring.py`.
- **Consumes from Phase 1**: `app/baseline.py::ip_familiarity_tier`, `app/velocity.py::count_user_30d` (already exists), `app/context.py::build_context`, `app/rules.py::ALLOWED_CONTEXT_FIELDS`.
- **Consumed by 2C**: every new Context field added here is referenced by at least one Phase 2C rule. The 2C plan lists which rule consumes each new field.

---

## 2B.1 — Migration: drop `customers.shipment_volume_30d`

**Theme**: New Alembic migration that drops the unused column. Phase 1 left the column in the schema but no writer maintained it; rules-system replacement (`customer_velocity_30d` from `count_user_30d` SQL via Context) is already in place. Dropping the column removes a no-op storage cost and prevents future drift where someone adds a writer that contradicts the on-demand count.

**Files**:
- `alembic/versions/0002_drop_shipment_volume_30d.py` (NEW)
- `tests/integration/` — no test changes required; the cleanup is invisible to existing tests
- Possibly `app/models.py` — if `shipment_volume_30d` was referenced in the customers SELECT, remove it. (Verify before writing the migration.)

**Specifics**:

Pre-check (run before writing the migration):
```bash
grep -rn "shipment_volume_30d" app/ tests/ alembic/
```
Expected matches: 0001 migration (the column add), no other production reads. If any read is found, this commit grows to include the corresponding removal.

Migration body:
```python
def upgrade() -> None:
    op.drop_column("customers", "shipment_volume_30d")

def downgrade() -> None:
    op.add_column(
        "customers",
        sa.Column("shipment_volume_30d", sa.Integer(), nullable=False, server_default="0"),
    )
    # NOTE: downgrade adds the column back as an unindexed default-0 int.
    # Rebuilding the historical 30-day count from shipments would require
    # a separate data-restore task; the downgrade is structural-only.
```

**Validation**:
- `docker compose exec app alembic upgrade head` clean
- `docker compose exec app alembic downgrade -1 && alembic upgrade head` clean (round-trip per CLAUDE.md schema gate)
- `pytest tests/integration/ -q --asyncio-mode=auto` — full suite green
- `docker compose exec postgres psql -U riskd -d riskd -c "\d customers"` — `shipment_volume_30d` not in the column list

**Risk**: **Low**. Phase 1 left no writer; reading 0 from a removed column would fail loud, not silent — but no reader exists. Mitigation: the grep pre-check confirms.

**Reversibility**: Moderate. Downgrade restores the column shape (default 0) but cannot restore historical values; no values existed.

**Pre-commit verification**: alembic upgrade/downgrade round-trip on a fresh database; ruff and mypy clean on the new migration file.

**Observability**: N/A (no runtime path change).

**Test changes**: None.

**Rollback plan**: `alembic downgrade -1`. Note that if subsequent Phase 2 commits depend on the column being absent, rollback also rolls back those — but no Phase 2 commit reads or writes this column.

**Declared breaks**: None.

**Reviewer routing**: Standard path with **db-reviewer** invoked. NEVER-SKIP per CLAUDE.md (any change to migrations).

---

## 2B.2 — Velocity module: add `count_user_distinct_ips_30d` and `count_recipient_distinct_customers_30d`

**Theme**: Two SQL count helpers in `app/velocity.py`. The first is a customer-IP-diversity proxy used by 2C velocity rules; the second is the cross-customer recipient overlap used by 2C recipient-overlap rules. Both are tenant-scoped.

**Files**:
- `app/velocity.py` (EDIT)
- `tests/integration/test_velocity.py` (EDIT — add tests for the new helpers)

**Specifics**:

```python
async def count_user_distinct_ips_30d(
    conn: asyncpg.Connection,
    tenant_id: int,
    customer_id: int,
) -> int:
    row = await conn.fetchrow(
        """
        SELECT COUNT(DISTINCT source_ip)::int AS cnt
        FROM shipments
        WHERE tenant_id = $1
          AND customer_id = $2
          AND booking_ts > now() - interval '30 days'
        """,
        tenant_id,
        customer_id,
    )
    return int(row["cnt"]) if row else 0


async def count_recipient_distinct_customers_30d(
    conn: asyncpg.Connection,
    tenant_id: int,
    destination_hmac: str,
) -> int:
    """Returns the number of DISTINCT customers within the same tenant
    that have booked to this destination HMAC in the last 30 days.

    SECURITY: tenant_id MUST appear in WHERE. Without it, this query
    leaks fraud-pattern information across tenants. The 2B.6 security
    test asserts cross-tenant isolation.
    """
    row = await conn.fetchrow(
        """
        SELECT COUNT(DISTINCT customer_id)::int AS cnt
        FROM shipments
        WHERE tenant_id = $1
          AND destination_hmac = $2
          AND booking_ts > now() - interval '30 days'
        """,
        tenant_id,
        destination_hmac,
    )
    return int(row["cnt"]) if row else 0
```

Verify the schema for `shipments.destination_hmac`. If the Phase 1 schema does NOT have a `destination_hmac` column (only the destination address string), this commit needs to ADD it via a new migration — and the destination HMAC must be written on every shipment INSERT.

Pre-check (run before writing this commit):
```bash
grep -n "destination_hmac" alembic/versions/*.py app/models.py app/api/booking.py
```

- If `destination_hmac` already exists in the schema (Phase 1 added it): just add the velocity helpers above.
- If NOT: this commit grows to include (a) Alembic migration adding the column, (b) `app/api/booking.py` HMAC the destination on insert, (c) backfill consideration. **If this branch is required, it's a substantive scope expansion — surface to operator via `.claude/STATUS.md` `Unforeseen / checkpoints` per CLAUDE.md "Substantive drift" rule. Don't paper over.**

**Validation**:
- `pytest tests/integration/test_velocity.py -v --asyncio-mode=auto` — new tests pass (4 tests below)
- `pytest tests/ -q --asyncio-mode=auto` — full suite green
- `ruff check app/velocity.py tests/integration/test_velocity.py` clean
- `mypy app/velocity.py` clean

**Risk**: **Medium**. The recipient SQL is on the request hot path; an unbounded COUNT(DISTINCT) over a large `shipments` table could miss the latency budget. Mitigation: the 30-day filter caps the row count to per-tenant 30-day volume; with the existing `(tenant_id, customer_id, booking_ts)` index from Phase 1 the cost is bounded. Phase 5 load test measures.

**Reversibility**: Easy — helpers are pure read functions; revert removes them. No data effect.

**Pre-commit verification**: ruff, mypy, pytest unit + integration.

**Observability**: Each helper emits a structured log if the count exceeds a notable threshold (e.g. recipient count > 10 → log `metric=true, event="recipient.cross_customer.high"`). Optional; lean toward not adding observability for hot-path read-only helpers unless metrics are useful — the count value itself flows into the rule trail and structured `risk.evaluation` log from 2A.5.

**Test changes**:

`test_count_user_distinct_ips_30d`:
- Seed: 3 shipments from same customer, 3 distinct IPs → returns 3.
- Edge: customer with no shipments in window → returns 0.

`test_count_recipient_distinct_customers_30d_within_tenant`:
- Seed: 3 different customers in tenant_a all ship to destination_hmac H. → returns 3.

`test_count_recipient_distinct_customers_30d_excludes_cross_tenant`:
- **SECURITY-LOAD-BEARING.** Seed: 2 customers in tenant_a + 2 customers in tenant_b, ALL shipping to destination_hmac H. Query for tenant_a → must return 2 (NOT 4).
- Assertion: `count == 2` (tenant_b customers not counted).
- This test is the primary defense against the recipient SQL leaking across tenants.

`test_count_recipient_distinct_customers_30d_outside_window`:
- Seed: 5 customers all shipping to H but booking_ts > 30 days ago. → returns 0.

**Rollback plan**: `git revert <hash>`.

**Declared breaks**:
- Scope: `count_recipient_distinct_customers_30d` exists but no caller — Phase 2C's recipient-overlap rules and 2B.4's Context derivation consume it.
- Resolved in: 2B.4 (Context wire-up) and 2C (rule additions).

**Reviewer routing**: Standard path with **security-auditor priority** on the cross-tenant test. Test-reviewer reviews the four tests; senior-engineer verifies SQL is parameterized and indexed; code-flow reviewer verifies the helpers are async and use the passed connection (no separate pool acquisition that would lose tenant context).

---

## 2B.3 — Baseline helper: `cloud_share` / `api_share` derivations + `days_since_last_booking`

**Theme**: Three helpers on `CustomerBaseline` (or as a standalone module if it cleans up better) that derive the lock-in components and the dormancy primitive from existing baseline state. No new schema; no new I/O.

**Files**:
- `app/baseline.py` (EDIT — add three helper methods)
- `tests/unit/test_baseline_derivations.py` (NEW)

**Specifics**:

```python
class CustomerBaseline:
    # ... existing fields ...

    @property
    def cloud_share(self) -> float:
        """Share of customer's decay-weighted IP observations that come
        from cloud IPs. Returns 0.0 when the customer has no observations.

        Reads ip_type_hist["cloud"] / sum(ip_type_hist.values()).
        After decay (uniform 90d for the flat histogram), both numerator
        and denominator are still proportional, so the ratio is stable.
        """
        total = sum(self.ip_type_hist.values())
        if total <= 0:
            return 0.0
        return float(self.ip_type_hist.get("cloud", 0.0)) / total

    @property
    def api_share(self) -> float:
        """Share of customer's decay-weighted bookings from the api channel."""
        total = sum(self.channel_hist.values())
        if total <= 0:
            return 0.0
        return float(self.channel_hist.get("api", 0.0)) / total

    def days_since_last_booking(self, now_ts: datetime) -> int | None:
        """Whole-day count since last_booking_ts. Returns None for a
        first-ever booking (no prior baseline observation)."""
        if self.last_booking_ts is None:
            return None
        delta = now_ts - self.last_booking_ts
        return max(0, delta.days)
```

`tests/unit/test_baseline_derivations.py`:
- `test_cloud_share_zero_for_empty`: empty baseline → 0.0
- `test_cloud_share_one_for_all_cloud`: ip_type_hist = {"cloud": 10.0} → 1.0
- `test_cloud_share_mixed`: ip_type_hist = {"cloud": 8.0, "residential": 2.0} → 0.8
- `test_cloud_share_after_decay_remains_proportional`: ip_type_hist = {"cloud": 4.0, "residential": 1.0} after default-factor decay (e.g. 0.5) → {"cloud": 2.0, "residential": 0.5}. Share remains 0.8.
- `test_api_share_zero_for_empty`: 0.0
- `test_api_share_one_for_pure_api`: channel_hist = {"api": 5.0} → 1.0
- `test_api_share_mixed`: channel_hist = {"api": 4.0, "web": 1.0} → 0.8
- `test_days_since_last_booking_none_for_first_booking`: last_booking_ts = None, now_ts = anything → None
- `test_days_since_last_booking_zero_for_same_day`: last_booking_ts = today 09:00 UTC, now_ts = today 14:00 UTC → 0
- `test_days_since_last_booking_basic_arithmetic`: last_booking_ts = 30 days ago → 30

**Validation**:
- `pytest tests/unit/test_baseline_derivations.py -v` — all 10 tests pass
- `pytest tests/ -q --asyncio-mode=auto` — full suite green
- `ruff check app/baseline.py` clean
- `mypy app/baseline.py` clean

**Risk**: **Low**. Pure-derivation properties + one date-arithmetic helper.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 10 new unit tests; no existing tests touched.

**Rollback plan**: `git revert`.

**Declared breaks**:
- Scope: helpers exist but no caller. Phase 2C's lock-in + dormancy rules consume them via the Context derivations 2B.4 wires.
- Resolved in: 2B.4.

**Reviewer routing**: Standard path.

---

## 2B.4 — `build_context` extensions: derive new Phase 2 fields

**Theme**: Wire `customer_locked_cloud_api`, `days_since_last_booking`, recipient overlap count, IP-familiarity tier exposure, plus other minimal Phase-2-required fields into the Context dict returned by `build_context()`. Each field is constructed from existing baseline / enrichment / SQL-count inputs.

**Files**:
- `app/context.py` (EDIT)
- `tests/integration/test_context.py` (EDIT or NEW — Phase 1 may not have a dedicated context-builder integration test; check first)
- `tests/unit/test_context_derivations.py` (NEW — for the pure derivations that don't need DB)

**Specifics** — new Context fields added (all consumed by 2C rules; one rule per field at minimum):

| Field | Type | Derivation |
|---|---|---|
| `customer_locked_cloud_api` | bool | `baseline.cloud_share > 0.95 AND baseline.api_share > 0.95 AND baseline.value_n >= 20` |
| `customer_locked_web_only` | bool | `1.0 - baseline.api_share > 0.95 AND baseline.value_n >= 20` (web is "not api"; we don't separate web from app in channel_hist beyond `channel != "api"`) |
| `days_since_last_booking` | int \| None | `baseline.days_since_last_booking(payload.booking_ts)` |
| `is_new_user` | bool | `baseline.value_n < 5.0` (proxy for "very few bookings observed"; freight_risk's catalog uses this as a threshold-style classification) |
| `ip_familiarity_tier` | str | Exposed from `baseline.ip_familiarity_tier(...)` already called in Phase 1; previously bound only to derived booleans. Now exposed as a string `"familiar" \| "family_familiar" \| "new_known_asn" \| "fully_new"`. Only used by tier-conditional rules in 2C that need the discrete value. |
| `ip_new_known_asn` | bool | `familiarity == "new_known_asn"` |
| `is_residential_asn` | bool | `NOT enrichment.is_cloud AND NOT enrichment.is_datacenter AND enrichment.asn_org is not None` |
| `ip2p_threat_any` | bool | `bool(enrichment.threat)` (any non-empty threat string). Used by `ip2p_threat_*` rules from freight_risk catalog. |
| `recipient_cross_customer_count` | int | `count_recipient_distinct_customers_30d(conn, tenant_id, destination_hmac)` — one more sequential await; tenant-scoped. |
| `customer_distinct_ips_30d` | int | `count_user_distinct_ips_30d(conn, tenant_id, customer_id)` — one more sequential await. |
| `impossible_travel` | bool | `ip_distance_km > 500 AND days_since_last_booking == 0 AND last_booking_ts is not None` — captures "moved 500+ km within the same day from a known origin" (the freight_risk rule is `impossible_travel_geo` weight 0.65). Conservative: requires same-day delta to avoid false positives on cross-day travel. |

Fields NOT added in Phase 2 (deferred — rules referencing these are excluded from 2C):
- `email_previously_rejected` / `phone_previously_rejected` / `origin_previously_rejected` / `ip_previously_rejected` — depend on feedback endpoint (Phase 3). 2C does NOT add the rules that consume them.
- `email_globally_blocked` / `ip_globally_rejected` / `recipient_globally_rejected` — depend on `global_blocked_vectors` feature (Phase 6+ stub). 2C does NOT add the rules that consume them.
- `customer_novelty_signals` / `customer_dest_diversity` — depend on additional aggregations not in Phase 1 baseline shape. 2C does NOT add the rules that consume them.
- `daily_volume_zscore` — requires a per-day rolling Welford on shipment count. Out of scope; rule excluded.
- `hour_rarity_p` / `weekday_rarity_p` / `channel_share_p` / `origin_rarity_p` / `origin_ip_country_rarity_p` — rarity probabilities; the baseline has the histograms but the p-value derivations are non-trivial. Defer to Phase 3 or post-launch.
- `is_email_globally_blocked` / `is_device_globally_blocked` / `device_blacklisted` / `is_known_device` / `user_agent_*` — out of scope per Phase 2 constraints.
- `origin_mismatches_registered` — requires registered_address comparison logic not present in Phase 1. Defer.

The above non-adds are documented here so 2C's reviewer panel knows which freight_risk rules legitimately don't port.

`tests/unit/test_context_derivations.py` — pure derivations against a stub baseline + stub enrichment:
- `test_customer_locked_cloud_api_flips_at_exact_threshold`: baseline with `cloud_share=0.95, api_share=0.95, value_n=20` → False (strict `>`); change cloud_share to 0.951 → True. (Boundary test on exact 0.95.)
- `test_customer_locked_cloud_api_below_observations_threshold`: cloud_share=1.0, api_share=1.0, value_n=19 → False.
- `test_customer_locked_cloud_api_high_share_but_too_new`: cloud_share=1.0, api_share=1.0, value_n=10 → False.
- `test_customer_locked_web_only_flips_when_api_share_low`: api_share=0.04, value_n=20 → True.
- `test_is_new_user_proxy`: value_n=4.0 → True; value_n=5.0 → False.
- `test_ip_new_known_asn_only_when_tier_matches`: familiarity tier matrix → ip_new_known_asn flips only on `"new_known_asn"`.
- `test_is_residential_asn_requires_non_cloud_non_dc_known_asn`: matrix on (is_cloud, is_datacenter, asn_org) → True only for (False, False, "Some ASN").
- `test_impossible_travel_requires_same_day_distance`: ip_distance=600, days_since_last=0, last_booking_ts set → True. ip_distance=600, days_since_last=1 → False. ip_distance=400, days_since_last=0 → False.
- `test_days_since_last_booking_none_when_no_history`: last_booking_ts None → None.

`tests/integration/test_context.py` — full `build_context` integration:
- `test_context_has_all_phase2_fields`: seed a customer with established baseline, post a booking, assert every Phase 2 field is present in the returned Context.
- `test_recipient_cross_customer_count_respects_tenant`: seed 2 tenants both shipping to same destination_hmac → count for tenant_a is just tenant_a customers, not combined.
- `test_customer_locked_cloud_api_e2e`: seed customer with 25 cloud-API bookings, post a 26th booking → `customer_locked_cloud_api == True`.

**Validation**:
- `pytest tests/unit/test_context_derivations.py -v` — all 9 tests pass
- `pytest tests/integration/test_context.py -v --asyncio-mode=auto` — all 3 tests pass
- `pytest tests/ -q --asyncio-mode=auto` — full suite green
- `ruff check app/context.py` clean
- `mypy app/context.py` clean

**Risk**: **Medium**. The Context-builder is the main pre-scoring orchestrator. Adding 10 fields touches a large surface; risk is a typo causing an existing rule's evaluation to misfire. Mitigation: unit + integration tests; no existing field semantics are changed.

**Reversibility**: Moderate — depends on whether 2C rules have shipped. If 2C is not yet committed, revert is clean.

**Pre-commit verification**: All gates green.

**Observability**: New fields show up in the `risk.evaluation` structured log from 2A.5 via the Context dict; no separate log statement added.

**Test changes**:
- 9 new unit tests in `test_context_derivations.py`
- 3 new integration tests in `test_context.py`
- Existing Phase 1 `test_unfamiliar_ip_against_established_customer_triggers_signals` and friends: no semantic change; they should continue to pass because none of the new Phase 2 fields are read by Phase 1 rules.

**Rollback plan**: `git revert <hash>`. Context fields disappear; 2C rules referencing them would fail at lifespan startup (rule loader's whitelist check). If 2C is committed already, rollback of 2B.4 alone breaks the system — revert 2C first.

**Declared breaks**:
- Scope: Context fields exist but no rule consumes them. 2C wires the rules.
- Resolved in: 2C.

**Reviewer routing**: Standard path. security-auditor specifically reviews the recipient cross-tenant scoping (the tenant_id filter is the load-bearing security boundary; this is the same dimension audited in 2B.2 but at the Context-wiring level).

---

## 2B.5 — DSL whitelist extension

**Theme**: Extend `ALLOWED_CONTEXT_FIELDS` in `app/rules.py` with every Phase 2B / 2C field. Phase 2C will fail at lifespan startup without this — the rule loader validates every condition's Name tokens against the whitelist.

**Files**:
- `app/rules.py` (EDIT — add ~17 field names to `ALLOWED_CONTEXT_FIELDS`)
- `tests/unit/test_rules_whitelist.py` (EDIT or NEW — assert the whitelist contains the new field names)

**Specifics** — new fields added to whitelist:
```python
ALLOWED_CONTEXT_FIELDS = frozenset({
    # Phase 1 (unchanged) — 45 fields
    ...
    # Phase 2B additions
    "customer_locked_cloud_api",
    "customer_locked_web_only",
    "days_since_last_booking",
    "is_new_user",
    "ip_familiarity_tier",
    "ip_new_known_asn",
    "is_residential_asn",
    "ip2p_threat_any",
    "recipient_cross_customer_count",
    "customer_distinct_ips_30d",
    "impossible_travel",
})
```

Total whitelist size after 2B: 45 + 11 = **56 fields**.

`tests/unit/test_rules_whitelist.py`:
- `test_whitelist_contains_every_phase2_field`: assert each of the 11 new field names is in the frozenset.
- `test_whitelist_immutable`: assert `ALLOWED_CONTEXT_FIELDS` is `frozenset`.
- `test_whitelist_size_matches_documentation`: assert `len(ALLOWED_CONTEXT_FIELDS) == 56`.

**Validation**:
- `pytest tests/unit/test_rules_whitelist.py -v` — all 3 tests pass
- `pytest tests/ -q --asyncio-mode=auto` — full suite green
- `ruff check app/rules.py tests/unit/test_rules_whitelist.py` clean
- `mypy app/rules.py` clean

**Risk**: **Medium-high** — security boundary. EACH field must be (a) populated in `build_context` (verified by 2B.4 integration test), (b) safe to expose to operator-supplied rule conditions (the DSL evaluator's `MappingProxyType` wrapping prevents attribute walks; the values are all primitives — bool/int/float/str/None — so there's no escape via attribute access). (c) Not derivable in a way that leaks cross-tenant data (the `recipient_cross_customer_count` field is the load-bearing case here; tenant scoping verified in 2B.2 + 2B.4).

**Reversibility**: Easy — revert removes field names; 2C rules using them would fail loud at startup.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 3 unit tests added.

**Rollback plan**: `git revert <hash>`.

**Declared breaks**:
- Scope: whitelist contains 11 new field names but no rule references them. The rule loader's "every Name token resolves to a known field" check passes (a name in the whitelist is fine; only unknown names fail). 2C wires the consumers.
- Resolved in: 2C.

**Reviewer routing**: Standard path. security-auditor reviews the whitelist additions against the 4-dimension check (existence in Context, primitive type, no cross-tenant leak, no PII-at-rest exposure). The 1D.6 DSL audit established the dimensions; 2B.5 applies them to the new fields.

---

## 2B.6 — `shipments.destination_hmac` migration + endpoint write + cross-tenant integration tests

**Theme**: Pre-check (operator-confirmed 2026-05-26): Phase 1's `0001_initial.py` migration adds `destination jsonb NOT NULL` to `shipments` but does NOT add `destination_hmac`. The recipient-overlap SQL in 2B.2 needs this column, plus an index on `(tenant_id, destination_hmac, booking_ts)` to keep the COUNT(DISTINCT) query inside the latency budget. This commit lands the column, the index, the endpoint write, and the cross-tenant isolation integration test in one atomic change.

**Files**:
- `alembic/versions/0003_add_shipments_destination_hmac.py` (NEW migration)
- `app/api/booking.py` (EDIT — HMAC the destination on every shipment INSERT)
- `app/signal_helpers.py` (no change — `hmac_hex` already exists per Phase 1)
- `tests/integration/test_tenant_isolation.py` (NEW)

**Specifics**:

Migration body (numbered 0003 because 2B.1 already landed 0002):
```python
def upgrade() -> None:
    # Add the column nullable first so the migration is safe to run
    # against an empty Phase 1 shipments table (no historical rows exist
    # in dev/staging; production has no rows yet — no backfill needed).
    op.add_column(
        "shipments",
        sa.Column("destination_hmac", sa.Text(), nullable=True),
    )
    # Backfill is empty in dev; production has no rows. If any non-null
    # rows exist at upgrade time (defensive), they remain NULL — the
    # NOT NULL constraint is enforced AFTER the next deploy lands the
    # endpoint write. To make this commit atomic, we DO add NOT NULL
    # now because Phase 1 has no production rows; if rows exist (somehow)
    # the upgrade fails loud and operator triages.
    op.alter_column("shipments", "destination_hmac", nullable=False)
    op.create_index(
        "ix_shipments_tenant_dest_hmac_booking_ts",
        "shipments",
        ["tenant_id", "destination_hmac", "booking_ts"],
    )

def downgrade() -> None:
    op.drop_index("ix_shipments_tenant_dest_hmac_booking_ts", table_name="shipments")
    op.drop_column("shipments", "destination_hmac")
```

Endpoint write (`app/api/booking.py`):
- Compute `destination_hmac = signal_helpers.hmac_hex(payload.shipment.destination.address, settings.hmac_secret)` at the same place we already construct HMACs for email/phone in Phase 1.
- Pass `destination_hmac` into the shipments INSERT statement alongside the existing columns.
- Per `.ai/decisions.md` § HMAC at egress, this HMAC is over the canonical address string (per the existing `signal_helpers.hmac_hex` convention — no per-row salting).

Integration tests (`tests/integration/test_tenant_isolation.py`):

```python
async def test_recipient_overlap_isolated_by_tenant(seeded_two_tenants):
    """Seed 3 shipments in tenant_a to destination D, and 2 in tenant_b to
    the same D. Request a booking in tenant_a; assert
    recipient_cross_customer_count for that booking returns counts that
    EXCLUDE tenant_b's shipments.
    """
    tenant_a, tenant_b = seeded_two_tenants
    ...
    # Booking in tenant_a; the score loop calls build_context which calls
    # count_recipient_distinct_customers_30d(conn, tenant_a, hmac(D))
    # The integer returned must be <= 3 (tenant_a's customers shipping to D),
    # NOT <= 5 (combined).
    assert ctx["recipient_cross_customer_count"] <= 3


async def test_recipient_overlap_tenant_b_sees_only_its_shipments(seeded_two_tenants):
    # Same seed; query for tenant_b → count <= 2.
    ...


async def test_destination_hmac_written_on_every_insert(client, seeded_tenant):
    """Post a booking; assert the inserted shipments row has a non-null,
    non-empty destination_hmac that equals hmac_hex(payload.destination,
    settings.hmac_secret)."""
    ...


async def test_destination_hmac_is_stable_across_repeats(client, seeded_tenant):
    """Post two bookings with the same destination address; assert the
    two rows have identical destination_hmac values."""
    ...
```

Integration test for the migration round-trip:
- `alembic upgrade head` clean
- `alembic downgrade -2 && alembic upgrade head` clean (round-trip across 0002 + 0003)
- `\d shipments` shows `destination_hmac TEXT NOT NULL` and the new index

```python
async def test_recipient_overlap_isolated_by_tenant(seeded_two_tenants):
    """Seed 3 shipments in tenant_a to destination D, and 2 in tenant_b to
    the same D. Request a booking in tenant_a; assert
    recipient_cross_customer_count for that booking returns counts that
    EXCLUDE tenant_b's shipments.
    """
    tenant_a, tenant_b = seeded_two_tenants
    ...
    # Booking in tenant_a; the score loop calls build_context which calls
    # count_recipient_distinct_customers_30d(conn, tenant_a, hmac(D))
    # The integer returned must be <= 3 (tenant_a's customers shipping to D),
    # NOT <= 5 (combined).
    assert ctx["recipient_cross_customer_count"] <= 3
```

Plus the cross-direction:
```python
async def test_recipient_overlap_tenant_b_sees_only_its_shipments(seeded_two_tenants):
    # Same seed; query for tenant_b → count <= 2.
    ...
```

**Validation**:
- `docker compose exec app alembic upgrade head` clean
- `docker compose exec app alembic downgrade -2 && alembic upgrade head` clean (round-trip across the new 0003 + earlier 0002)
- `pytest tests/integration/test_tenant_isolation.py -v --asyncio-mode=auto` — 4 new tests pass (2 cross-tenant overlap + 2 destination_hmac write/stability)
- `pytest tests/ -q --asyncio-mode=auto` — full suite green
- `docker compose exec postgres psql -U riskd -d riskd -c '\d shipments'` — `destination_hmac TEXT NOT NULL` present + `ix_shipments_tenant_dest_hmac_booking_ts` index listed
- `ruff check app/api/booking.py tests/integration/test_tenant_isolation.py` clean
- `mypy app/` clean

**Risk**: **High**. This commit lands a migration adding a NOT NULL column AND an index AND a persistence-path change AND the cross-tenant security test in one atomic change. The migration's safe-for-empty-table assumption (no Phase 1 production rows) is operator-verified for dev/staging; if a production tenant ever migrated through here with non-empty `shipments`, the NOT NULL add would fail loud. Phase 1 has no production deploy yet, so this is safe.

**Reversibility**: Moderate. The downgrade restores the pre-2B.6 schema cleanly; integration tests in 2C/2D that depend on destination_hmac would fail under the reverted schema. Revert 2B.6 only by reverting subsequent dependent commits as well.

**Pre-commit verification**: All gates green. Alembic round-trip is the load-bearing check.

**Observability**: N/A (no new request-path log; the cross-tenant security boundary is the focus).

**Test changes**: 4 new integration tests (2 cross-tenant isolation in both directions, 2 destination_hmac write + stability).

**Rollback plan**: `alembic downgrade -1` (removes 0003) + `git revert <hash>` (removes endpoint write + tests).

**Declared breaks**: None — the migration + write + tests land atomically; there's no transitional state where the endpoint writes but the column doesn't exist (or vice versa).

**Reviewer routing**: Standard path + **db-reviewer** (migration adds column + index) + **security-auditor priority** (cross-tenant query is the load-bearing security boundary). NEVER-SKIP per CLAUDE.md on both axes (any migration AND any change to tenant_id scoping).

---

## Batch 2B summary

6 commits:
- 2B.1 — Drop `shipment_volume_30d` migration (0002)
- 2B.2 — Velocity helpers: distinct-IPs-30d + recipient-distinct-customers-30d (with tenant-scoping security test)
- 2B.3 — Baseline derivations: `cloud_share`, `api_share`, `days_since_last_booking`
- 2B.4 — `build_context` adds 10-11 Phase 2 fields (with 9 unit + 3 integration tests)
- 2B.5 — DSL whitelist extension (11 new field names, total 56)
- 2B.6 — Migration 0003 add `shipments.destination_hmac` + index + endpoint write + cross-tenant isolation integration tests

At the end of Batch 2B, `build_context` produces every Context field that Phase 2C rules will reference. The DSL whitelist accepts them. No new rules are wired yet.

**Expected test count after 2B**: 297 (post-2A) + 4 (2B.2) + 10 (2B.3) + 12 (2B.4) + 3 (2B.5) + 4 (2B.6) = **330 tests**.

**Fields explicitly NOT added** (deferred or out of scope):
- previously_rejected (Phase 3 — feedback endpoint)
- globally_blocked / globally_rejected (Phase 6+ — global blocked vectors stub)
- customer_novelty_signals / customer_dest_diversity / hour_rarity_p / weekday_rarity_p / channel_share_p / origin_rarity_p / origin_ip_country_rarity_p / daily_volume_zscore (rarity p-values — non-trivial derivation; defer)
- email_globally_blocked / device_blacklisted / user_agent_* (out of scope)
- origin_mismatches_registered (defer)

These exclusions are documented here so 2C reviewer panel knows which freight_risk rules legitimately don't port.
