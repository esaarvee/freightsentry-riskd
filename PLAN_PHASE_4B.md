# Phase 4 — Batch 4B Plan — Currency normalization

> **Status (2026-06-01)**: Pending operator approval. Approval may be deferred until after 4A execution reports.

Batch 4B resolves the Phase 3D-documented implicit-USD assumption. It adds a `currency` field to `BookingRequest.shipment` and `ModificationRequest` (defaulting to `"USD"` so existing payloads are unchanged), populates the `value_caps` overrides into 5 new Context fields via `build_context`, and rewrites the 7 currency-implicit rules in `app/rules.yaml` to consult per-currency-per-tier thresholds.

**Highest-blast-radius batch in Phase 4.** Case-1 (dashboard ATO) and case-2 (API ATO) depend on specific rules in the value-anomaly family — the 7-rule rewrite must preserve identical USD-default behavior.

Target: **7 commits**.

---

## Decisions absorbed (batch-relevant subset)

| Decision | Value | Source |
|---|---|---|
| Currency field added to | `BookingRequest.shipment.currency` AND `ModificationRequest.currency` (modification-level, NOT inside the discriminated `new_value` payload — currency applies to the whole evaluation, not to a specific modification dimension) | Phase 4 prompt + planning judgment for modification |
| Currency default | `"USD"` on both payloads — backward compatible for all existing Phase 1-3 payloads | Phase 4 prompt |
| Currency validation timing | At request time (inside endpoint, after `load_tenant_config`); validates `payload.shipment.currency ∈ tenant_config.allowed_currencies`. Reject with **400** if not. Pydantic-level shape validation (3-letter uppercase) is separate and stays in the model. | Phase 4 prompt |
| Validation error shape | FastAPI HTTPException(400, "currency 'EUR' is not in tenant's allowed list ['USD', 'CAD']") — explicit list to aid integrators | Phase 4 prompt + planning judgment |
| Modification rule 1 (magnitude-based) | UNCHANGED. `modification_within_30_min_value_increase` uses `modification_magnitude > 0.2` — fraction, currency-independent. Explicit no-touch declaration. | Phase 3D + Phase 4 prompt watch-point |
| value_caps default for empty config | When `tenant_config.value_caps is None`, use `DEFAULT_VALUE_CAPS = {"USD": {"high": 10000, "new_user": 5000, "medium": 2000, "low": 1000}}` — matches the existing Phase 2 hardcoded thresholds. **Zero behavioral change for USD-default tenants.** | Phase 4 prompt + Phase 2 thresholds (`app/rules.yaml`) |
| When `tenant_config.value_caps` is set but missing the request currency | Falls back to `DEFAULT_VALUE_CAPS["USD"]`. Already validated at request time that currency is allowed; this case occurs when the operator added a currency to `allowed_currencies` but forgot the matching `value_caps[<currency>]`. Logged as a warning. | Phase 4 prompt judgment |
| 5 new Context fields | `shipment_currency`, `shipment_value_threshold_high`, `shipment_value_threshold_new_user`, `shipment_value_threshold_medium`, `shipment_value_threshold_low` | Phase 4 prompt |
| ALLOWED_CONTEXT_FIELDS growth | 66 → 71 (+5) | Phase 4 prompt |
| 7-rule rewrite | `absolute_high_value`, `high_value_new_user`, `flags_with_value`, `threat_intel_high_value`, `ip2p_threat_high_value`, `low_trust_high_value`, `vpn_high_value` | Phase 4 prompt + decisions.md § Currency normalization |
| Rule weight changes | NONE. Weights stay identical; conditions change only the right-hand-side comparison target. | Phase 4 prompt + Phase 2/3 calibration discipline |
| Currency conversion via rates table | EXPLICITLY REJECTED. Per-currency thresholds in `value_caps`; no maintained rates data. | Phase 4 prompt |
| Storage of currency on shipments table | DEFERRED. Phase 4B does not add `shipments.currency` column. Currency-aware analytics in Phase 5+ may add it. Modification path infers currency from request payload (or defaults to USD if absent on a Phase 3-shipped row). | Phase 4 prompt + scope discipline |
| Maturity-sensitive flag for rewritten rules | UNCHANGED. None of the 7 rules are currently maturity-sensitive; they stay non-sensitive. | Phase 2 calibration; no decision to change |
| Regression discipline | After 4B.5 (rule rewrite), case-1 and case-2 integration tests MUST continue passing with identical BLOCK outcomes. If either changes outcome, 4B.5 is a blocker — surface to operator. | Phase 4 prompt watch-point |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md. Pre-commit hooks active.
- Reviewer routing per CLAUDE.md triage gate:
  - 4B.1 (Pydantic model field additions): standard panel + test-reviewer.
  - 4B.2 (DEFAULT_VALUE_CAPS helper + tenant_config consumer): Never-Skip (new code in `app/`) → standard + test-reviewer.
  - 4B.3 (request-time currency validation): Never-Skip (new validation path in 2 endpoints) → standard + db-reviewer + test-reviewer + security-auditor (input validation on hot path).
  - 4B.4 (DSL whitelist +5 + Context derivations): Never-Skip (DSL whitelist + Context hot-path) → standard + security-auditor.
  - 4B.5 (7-rule rewrite in rules.yaml): Never-Skip (rule changes — but per CLAUDE.md triage gate this is technically "adjusting an existing rule's parameters" not "adding/removing a rule", which the lightweight path covers... HOWEVER per Phase 4 prompt watch-point and the blast radius, standard panel + test-reviewer is mandatory). → standard + test-reviewer.
  - 4B.6 (integration tests including case-1/case-2 regression): test-only → test-reviewer + senior-engineer + code-flow.
  - 4B.7 (`.ai/decisions.md` resolution of § Currency normalization): doc-only → doc-reviewer only.

- Reviewer-invocation slice template:
  > `Plan file: PLAN_PHASE_4B.md, current commit: 4B.N (<title>), upcoming commits: 4B.{N+1} through 4B.7 sections. Read only those sections.`

---

## Cross-batch dependencies

- **Consumes from 4A**: `TenantConfig` model (`allowed_currencies`, `value_caps`); `load_tenant_config`; `tenant_config` threaded through `build_context`; same for `build_modification_context`.
- **Consumes from Phase 1-3**: `app/rules.yaml` (7 rules to rewrite), `ALLOWED_CONTEXT_FIELDS` set, `BookingRequest`/`ModificationRequest` models, all 3 endpoints.
- **Consumes from Phase 2**: case-1 + case-2 integration tests as the regression gate.
- **Consumed by 4C**: 4C does not depend directly on 4B; both are pure consumers of 4A's `tenant_config`.
- **Consumed by 4D**: admin endpoints inherit the new request-time validation pattern conceptually; no admin endpoint accepts currency input.

---

## 4B.1 — Add `currency` field to `BookingRequest.shipment` and `ModificationRequest`

**Theme**: Pydantic v2 model field additions. Optional with default `"USD"`. Validation: ISO 4217 shape (3 uppercase letters). No allowed-currencies check at model level — that's request-time (4B.3).

**Files**:
- `app/models.py` (EDIT — extend `ShipmentData` and `ModificationRequest`)
- `tests/unit/test_models_currency.py` (NEW)
- `tests/unit/test_models.py` (EDIT — existing tests that build BookingRequest/ModificationRequest payloads without `currency` confirm backward compatibility; no test rewrites needed since default applies)

**Specifics**:

```python
# in ShipmentData:
class ShipmentData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Address
    destination: Address
    value: Decimal = Field(..., ge=Decimal("0"))
    channel: str
    currency: str = Field(default="USD", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")


# in ModificationRequest:
class ModificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = ...
    original_request_id: str = ...
    modification_ts: datetime
    modification_type: ModificationType
    new_value: dict[str, Any]

    source_ip: IPv4Address | None = None
    user: ModificationUser | None = None
    reason: str | None = Field(None, max_length=512)
    currency: str = Field(default="USD", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
```

`tests/unit/test_models_currency.py` — 10 tests:

1. `ShipmentData(...)` without `currency` → `currency == "USD"`.
2. `ShipmentData(..., currency="USD")` → valid.
3. `ShipmentData(..., currency="CAD")` → valid (Pydantic doesn't enforce allowed list).
4. `ShipmentData(..., currency="usd")` → ValidationError (lowercase rejected by pattern).
5. `ShipmentData(..., currency="US")` → ValidationError (2-letter).
6. `ShipmentData(..., currency="USDX")` → ValidationError (4-letter).
7. `ShipmentData(..., currency="123")` → ValidationError (digits rejected).
8. `ModificationRequest(...)` without `currency` → `currency == "USD"`.
9. `ModificationRequest(..., currency="EUR")` → valid.
10. `ModificationRequest(..., currency="eur")` → ValidationError.

Plus a backward-compatibility scan: import existing test payload JSON fixtures and confirm they all build successfully (no `currency` field present in any fixture means all default to USD).

**Validation**:
- `pytest tests/unit/test_models_currency.py -v` → 10 tests pass.
- `pytest tests/unit/test_models*.py -v` → existing tests pass unchanged.
- `mypy app/` strict clean. `ruff check app/ tests/` clean.

**Risk**: **Low**. Optional field with default; existing payloads unchanged.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 10 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: standard panel + test-reviewer.

---

## 4B.2 — `DEFAULT_VALUE_CAPS` constant + per-request resolution helper

**Theme**: Add the project-default value_caps to `app/tenant_config.py` (or a new `app/value_caps.py` — choosing tenant_config.py to keep value-cap logic colocated with TenantConfig). Add a `resolve_value_caps(tenant_config, currency)` helper that returns the 4-tier dict for the given currency. Falls back to USD defaults gracefully.

**Files**:
- `app/tenant_config.py` (EDIT — append constant + helper)
- `tests/unit/test_value_caps_resolution.py` (NEW)

**Specifics**:

```python
# Appended to app/tenant_config.py

DEFAULT_VALUE_CAPS: dict[str, dict[str, float]] = {
    "USD": {
        "high": 10000.0,
        "new_user": 5000.0,
        "medium": 2000.0,
        "low": 1000.0,
    }
}
"""Project-default per-currency value caps.

Tier values match the absolute literals in the 7 currency-implicit
rules pre-Phase-4B (see `app/rules.yaml`):
  - high     = 10000   (absolute_high_value)
  - new_user = 5000    (high_value_new_user)
  - medium   = 2000    (flags_with_value, threat_intel_high_value,
                        ip2p_threat_high_value)
  - low      = 1000    (low_trust_high_value, vpn_high_value)

USD-implicit. Tenants that need non-USD pricing populate
`tenant_config.value_caps` with per-currency overrides. Empty/None
`value_caps` means "use these defaults".

If a tenant adds a non-USD currency to `allowed_currencies` but
doesn't provide a matching `value_caps[currency]`, the resolution
helper falls back to USD-default and emits a warning log.
"""


def resolve_value_caps(
    tenant_config: TenantConfig,
    currency: str,
) -> dict[str, float]:
    """Return the 4-tier threshold dict for the given currency.

    Resolution priority:
      1. `tenant_config.value_caps[currency]` if both the dict and the
         currency key are present.
      2. `DEFAULT_VALUE_CAPS["USD"]` as a safety fallback. Logs a
         `tenant_config.value_caps.fallback` warning.

    Currency is validated at request time before this helper runs
    (4B.3) — so a currency reaching this helper is always in
    `tenant_config.allowed_currencies`. The fallback covers the
    operator-misconfiguration case where a currency is allowed but
    no tier dict exists for it.
    """
    if tenant_config.value_caps and currency in tenant_config.value_caps:
        return tenant_config.value_caps[currency]
    # Fallback path. Log so operators see the misconfiguration.
    import structlog
    structlog.get_logger(__name__).warning(
        "tenant_config.value_caps.fallback",
        tenant_id=tenant_config.tenant_id,
        currency=currency,
        metric=True,
    )
    return DEFAULT_VALUE_CAPS["USD"]
```

`tests/unit/test_value_caps_resolution.py` — 8 tests:

1. `tenant_config.value_caps is None`, currency="USD" → returns DEFAULT_VALUE_CAPS["USD"].
2. `tenant_config.value_caps is None`, currency="CAD" → returns DEFAULT_VALUE_CAPS["USD"] (USD fallback) + warning logged (assert via `caplog`).
3. `tenant_config.value_caps = {"CAD": {...}}`, currency="CAD" → returns that dict.
4. `tenant_config.value_caps = {"USD": {...custom...}}`, currency="USD" → returns custom values.
5. `tenant_config.value_caps = {"USD": {...}, "CAD": {...}}`, currency="EUR" → returns DEFAULT_VALUE_CAPS["USD"] + warning (EUR not in dict).
6. Verify all 4 tier keys present in DEFAULT_VALUE_CAPS["USD"].
7. Verify tier values are exactly 10000/5000/2000/1000 (matches Phase 2 thresholds).
8. Verify returned dict is not the SAME object as DEFAULT_VALUE_CAPS["USD"] OR is read-only (defensive — callers shouldn't mutate the default). Implementation note: return a copy or document as immutable contract. Choosing: callers do not mutate (documented), so returning the same dict reference is fine; this test asserts identity is acceptable.

**Validation**:
- `pytest tests/unit/test_value_caps_resolution.py -v` → 8 tests pass.
- `mypy app/` strict clean.

**Risk**: **Low**.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: Warning log on USD fallback (`tenant_config.value_caps.fallback` with `metric=True` tag for Phase 5 CloudWatch ingestion).

**Reviewer attention (operator note, 2026-06-01)**: The fallback warning catches the operator misconfiguration case (currency allowed but no value_caps entry) without breaking the request. Reviewer panel **must** verify the warning is not lost in noise: (a) the `metric=True` tag is present so Phase 5 EMF ingestion treats it as countable, (b) the structured-log key `tenant_config.value_caps.fallback` is distinct from generic `tenant_config.*` keys so a CloudWatch dashboard can isolate it, (c) the `tenant_id` and `currency` fields are present so the operator can identify which tenant + currency triggered. Unit test must `caplog.records[0].metric is True` to pin the tag.

**Test changes**: 8 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Never-Skip (new code in `app/`) → standard + test-reviewer.

---

## 4B.3 — Request-time currency validation in 2 endpoints (booking + modification)

**Theme**: After `load_tenant_config` in `app/api/booking.py` and `app/api/modification.py`, validate `payload.shipment.currency` (booking) / `payload.currency` (modification) against `tenant_config.allowed_currencies`. Reject with 400 if not in list. Feedback endpoint does NOT validate currency (no shipment currency on feedback path).

**Files**:
- `app/api/booking.py` (EDIT — add currency check)
- `app/api/modification.py` (EDIT — add currency check)
- `tests/integration/test_booking_currency_validation.py` (NEW)
- `tests/integration/test_modification_currency_validation.py` (NEW)

**Specifics**:

In `app/api/booking.py`, after `load_tenant_config`:

```python
if payload.shipment.currency not in tenant_config.allowed_currencies:
    raise HTTPException(
        status_code=400,
        detail=(
            f"currency {payload.shipment.currency!r} is not in tenant's "
            f"allowed list {tenant_config.allowed_currencies}"
        ),
    )
```

In `app/api/modification.py`, after `load_tenant_config`:

```python
if payload.currency not in tenant_config.allowed_currencies:
    raise HTTPException(
        status_code=400,
        detail=(
            f"currency {payload.currency!r} is not in tenant's "
            f"allowed list {tenant_config.allowed_currencies}"
        ),
    )
```

### Integration tests

`tests/integration/test_booking_currency_validation.py` — 5 tests:

1. **Default USD-only tenant**: booking with `currency="USD"` (or no currency field) → 200.
2. **Default USD-only tenant**: booking with `currency="CAD"` → 400, body mentions allowed list `["USD"]`.
3. **Multi-currency tenant**: tenant_config.allowed_currencies=["USD","CAD","EUR"]; booking with `currency="CAD"` → 200.
4. **Multi-currency tenant**: booking with `currency="GBP"` → 400.
5. **Backward compatibility**: existing Phase 1-3 fixture payloads (no `currency` field) → 200 (defaults to USD; allowed in default config).

`tests/integration/test_modification_currency_validation.py` — 4 tests:

1. Modification with no currency field → 200 (defaults to USD).
2. Modification with currency="USD" → 200.
3. Modification with currency="EUR" against USD-only tenant → 400.
4. Modification with currency="EUR" against multi-currency tenant including EUR → 200.

**Validation**:
- `pytest tests/integration/test_booking_currency_validation.py tests/integration/test_modification_currency_validation.py -v --asyncio-mode=auto` → 9 tests pass.
- `pytest tests/ --asyncio-mode=auto -q` → full suite green; existing booking/modification tests unchanged.

**Risk**: **Medium**. Request-time validation on hot path. Risk is rejecting a previously-accepted request. Backward-compat test 5 (booking) explicitly covers the default behavior.

**Reversibility**: Easy — `git revert` removes the validation block.

**Pre-commit verification**: All gates green.

**Observability**: 400 responses log at INFO. Reuse existing structlog pattern from auth.py error paths.

**Test changes**: 9 integration tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Never-Skip → standard + db-reviewer + test-reviewer + security-auditor (input validation on hot path).

---

## 4B.4 — DSL whitelist +5 fields + Context derivations

**Theme**: Add 5 fields to `ALLOWED_CONTEXT_FIELDS` and populate them in `build_context`. The fields are derived from `tenant_config` + `payload.shipment.currency` (booking) or `payload.currency` (modification).

**Files**:
- `app/rules.py` (EDIT — append 5 fields)
- `app/context.py` (EDIT — populate 5 fields in `build_context`)
- `tests/unit/test_context_value_caps_fields.py` (NEW)

**Specifics**:

### `app/rules.py` additions (after 3B's previously-rejected fields):

```python
    # ---- Currency-normalized thresholds (Phase 4B) ---------------------
    "shipment_currency",                    # str — 3-letter ISO 4217 code from payload
    "shipment_value_threshold_high",        # float — tenant_config.value_caps[currency]["high"]
    "shipment_value_threshold_new_user",    # float — ditto ["new_user"]
    "shipment_value_threshold_medium",      # float — ditto ["medium"]
    "shipment_value_threshold_low",         # float — ditto ["low"]
```

Whitelist count: 66 + 5 = **71 fields**.

### `app/context.py` change inside `build_context`:

Add `tenant_config: TenantConfig` parameter (added in 4A.3, signature already in place). Inside the function body, after the existing fields:

```python
from app.tenant_config import resolve_value_caps

# Currency-normalized thresholds. payload.shipment.currency defaults to
# "USD" per BookingRequest.ShipmentData (4B.1). resolve_value_caps
# returns the 4-tier dict, falling back to DEFAULT_VALUE_CAPS["USD"] if
# the tenant hasn't configured the requested currency.
currency = payload.shipment.currency
caps = resolve_value_caps(tenant_config, currency)

ctx["shipment_currency"] = currency
ctx["shipment_value_threshold_high"] = caps["high"]
ctx["shipment_value_threshold_new_user"] = caps["new_user"]
ctx["shipment_value_threshold_medium"] = caps["medium"]
ctx["shipment_value_threshold_low"] = caps["low"]
```

### `build_modification_context` — inherits via `build_context`

`build_modification_context` synthesizes a `BookingRequest` from the prior shipment row, then calls `build_context`. The synthesized booking's `ShipmentData.currency` must reflect the MODIFICATION's currency (not the prior shipment's, which is irrelevant for value-cap rule evaluation — the modification is the in-scope event).

Concretely: in `_booking_from_prior_shipment` (or in the build_modification_context body after the synthesis), override `synthetic_booking.shipment.currency = payload.currency` before passing to `build_context`. Since Pydantic models are frozen, this requires reconstructing — see specifics:

```python
# In build_modification_context, before calling build_context:
synthetic_booking = _booking_from_prior_shipment(...)
# Override the currency to reflect the modification request's currency
# (not the prior shipment's). The prior shipment didn't carry a
# currency field pre-Phase-4B; for shipments rows written before 4B
# the synthesized booking gets USD via the Pydantic default; the
# override below upgrades that to the modification's chosen currency.
synthetic_booking = synthetic_booking.model_copy(
    update={
        "shipment": synthetic_booking.shipment.model_copy(
            update={"currency": payload.currency},
        ),
    },
)
ctx, baseline, enrichment = await build_context(
    conn,
    ...,
    payload=synthetic_booking,
    tenant_config=tenant_config,
    ...,
)
```

### Unit tests

`tests/unit/test_context_value_caps_fields.py` — 6 tests:

1. Empty tenant_config (value_caps=None), payload.shipment.currency="USD" → all 5 ctx fields populated with default thresholds (10000/5000/2000/1000) and `shipment_currency="USD"`.
2. Custom tenant_config (value_caps={"USD": {"high": 50000, "new_user": 20000, "medium": 5000, "low": 2500}}), payload USD → thresholds match custom values.
3. tenant_config(value_caps={"CAD": {...}}), payload currency="CAD" → CAD thresholds.
4. tenant_config(value_caps={"CAD": {...}}), payload currency="USD" → DEFAULT (USD fallback) thresholds; warning logged.
5. Whitelist size: `assert len(ALLOWED_CONTEXT_FIELDS) == 71`.
6. Modification path: synthesized booking inherits modification's currency, not prior shipment's currency.

**Validation**:
- `pytest tests/unit/test_context_value_caps_fields.py -v --asyncio-mode=auto` → 6 tests pass.
- `pytest tests/unit/test_dsl*.py tests/unit/test_rules*.py` → still pass (whitelist size assertion picked up by the YAML loader test).
- `mypy app/` strict clean.

**Risk**: **Medium**. Touches `build_context` (hot path) and the DSL whitelist. The 5 new ctx fields are populated unconditionally on every request.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: Warning on value_caps fallback (per 4B.2).

**Test changes**: 6 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**:
- **Scope**: 5 new whitelist fields populated; no rules reference them yet. No-op until 4B.5.
- **Resolved in**: 4B.5 (7-rule rewrite).

**Reviewer routing**: Never-Skip → standard + security-auditor.

---

## 4B.5 — Rewrite 7 currency-implicit rules in `app/rules.yaml`

**Theme**: The pivot. Replace the absolute-value literals in 7 rules with references to the new `shipment_value_threshold_*` Context fields. Weights and maturity-sensitive flags unchanged. Add per-rule unit tests for the rewritten conditions.

**Files**:
- `app/rules.yaml` (EDIT — rewrite 7 conditions)
- `tests/unit/test_rules_currency_rewrite.py` (NEW — per-rule fire/no-fire tests)
- `tests/unit/test_rules.py` (EDIT if rule_count assertion exists — still 79 rules)

**Specifics**:

### Rule rewrites

| Rule | Before | After |
|---|---|---|
| `vpn_high_value` (line ~51) | `is_vpn AND shipment_value > 1000` | `is_vpn AND shipment_value > shipment_value_threshold_low` |
| `low_trust_high_value` (line ~120) | `trust_score < 0.3 AND shipment_value > 1000` | `trust_score < 0.3 AND shipment_value > shipment_value_threshold_low` |
| `flags_with_value` (line ~143) | `flagged_count > 3 AND shipment_value > 2000` | `flagged_count > 3 AND shipment_value > shipment_value_threshold_medium` |
| `high_value_new_user` (line ~304) | `shipment_value > 5000 AND is_new_user` | `shipment_value > shipment_value_threshold_new_user AND is_new_user` |
| `absolute_high_value` (line ~341) | `shipment_value > 10000` | `shipment_value > shipment_value_threshold_high` |
| `threat_intel_high_value` (line ~346) | `ip_in_threat_list AND shipment_value > 2000` | `ip_in_threat_list AND shipment_value > shipment_value_threshold_medium` |
| `ip2p_threat_high_value` (line ~351) | `ip2p_threat_any AND shipment_value > 2000` | `ip2p_threat_any AND shipment_value > shipment_value_threshold_medium` |

Update inline description text where it mentions the absolute threshold (e.g., `"Absolute high-value shipment (>10000) regardless..."` becomes `"Absolute high-value shipment (above tenant's high tier) regardless..."`).

### Confirm modification rule 1 is NOT rewritten

`modification_within_30_min_value_increase` keeps its current condition: `modification_type == "value" AND modification_time_since_booking == "within_30_min" AND modification_magnitude > 0.2`. `modification_magnitude` is a fraction (`|new - old| / old`) — currency-independent. **Explicit no-touch.**

### Per-rule unit tests

`tests/unit/test_rules_currency_rewrite.py` — 14 tests (2 per rule × 7 rules):

For each rewritten rule, 2 tests:
1. **Fires with USD-default thresholds**: ctx with `shipment_value > <default-threshold>` for that tier + the other conditions met → rule fires.
2. **Does NOT fire with custom-elevated thresholds**: tenant_config.value_caps elevates the relevant tier above the test shipment_value; ctx populated accordingly → rule does NOT fire even though shipment_value matches the OLD literal.

Example for `absolute_high_value`:
```python
def test_absolute_high_value_fires_at_default_threshold():
    ctx = {**default_base_ctx, "shipment_value": 10001.0, "shipment_value_threshold_high": 10000.0}
    rule = find_rule("absolute_high_value")
    assert rule.evaluate(ctx) is True

def test_absolute_high_value_does_not_fire_when_custom_threshold_higher():
    ctx = {**default_base_ctx, "shipment_value": 10001.0, "shipment_value_threshold_high": 50000.0}
    rule = find_rule("absolute_high_value")
    assert rule.evaluate(ctx) is False
```

### USD-default invariance

A separate parametrized test pins that **with USD-default thresholds (10000/5000/2000/1000), every rewritten rule fires identically to its pre-Phase-4B condition** across a matrix of shipment_value values: `{500, 1500, 2500, 4000, 5500, 9500, 10500}`. This is the surgical invariance check.

**Validation**:
- `pytest tests/unit/test_rules_currency_rewrite.py -v` → 14 tests pass + parametrized invariance test passes.
- `python -c "import yaml; assert len(yaml.safe_load(open('app/rules.yaml'))['rules']) == 79"` — rule count unchanged.
- `pytest tests/unit/test_rules*.py -v` → all rule-load tests pass (the DSL loader now resolves the new `shipment_value_threshold_*` names via 4B.4's whitelist).
- **CRITICAL REGRESSION GATE**: `pytest tests/integration/test_case_1_detection.py tests/integration/test_case_2.py -v --asyncio-mode=auto` — both case-1 and case-2 BLOCK at the same final decision as pre-4B. If either changes outcome, BLOCK 4B.5.
- `pytest tests/ --asyncio-mode=auto -q` → full suite green.

**Risk**: **Highest in Phase 4**. The 7 rules are central to case-1 and case-2 BLOCK detection. A bug here (wrong field name, swapped tier, off-by-one in default values) silently breaks fraud detection.

Mitigations:
1. USD-default invariance test (parametrized) — pins identical behavior.
2. Case-1 + case-2 integration tests as gates.
3. Per-rule unit tests with both "fires at default" and "doesn't fire at elevated" assertions.
4. Reviewer panel + senior-engineer pays special attention to threshold-key correctness.

**Reversibility**: Easy — `git revert` restores absolute literals.

**Pre-commit verification**: All gates green. Pre-commit's `pytest tests/unit/` will catch any rule-condition syntax error.

**Observability**: triggered_rules surface in response; same observability as Phase 2.

**Test changes**: 14 unit tests + parametrized invariance test.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: standard panel + test-reviewer. Per CLAUDE.md never-skip clause: "Any change to `app/rules.yaml` weights, thresholds, or conditions that adds or removes a rule (vs adjusting an existing rule's parameters — lightweight)". This is "adjusting conditions" not "adding/removing rules" — technically lightweight per the rule, BUT the blast radius warrants full panel. **Operator decision absorbed: route through standard panel.**

---

## 4B.6 — Integration tests: cross-currency scoring + case-1/case-2 regression

**Theme**: Comprehensive integration tests verifying (a) USD-default behavior unchanged for existing fixtures, (b) non-USD tenants score correctly with calibrated value_caps, (c) case-1 + case-2 BLOCK assertions hold.

**Files**:
- `tests/integration/test_currency_normalization_e2e.py` (NEW)
- `tests/integration/test_case_1_detection.py` (EDIT IF NEEDED — likely no change since USD-default tenants are tested; assert no regression)
- `tests/integration/test_case_2.py` (EDIT IF NEEDED — same)

**Specifics**:

### `tests/integration/test_currency_normalization_e2e.py` — 10 tests:

1. **USD-default tenant + high-value USD booking**: shipment_value=15000, USD-default tenant → `absolute_high_value` fires (same as Phase 3 behavior).
2. **CAD tenant with calibrated value_caps**: tenant_config.value_caps={"CAD": {"high": 12500, "new_user": 6250, "medium": 2500, "low": 1250}}; booking with `shipment_value=12600, currency="CAD"` → `absolute_high_value` fires.
3. **CAD tenant**: booking with `shipment_value=10500, currency="CAD"` → `absolute_high_value` does NOT fire (under CAD-high=12500 threshold).
4. **USD-default tenant**: booking with `shipment_value=10500, currency="USD"` → `absolute_high_value` fires (above USD-high=10000).
5. **Cross-tenant currency drift**: tenant_a (USD-only) and tenant_b (CAD-only with 12500 high). Identical 11000 value bookings → tenant_a fires, tenant_b doesn't. Confirms tenant_config is loaded per-request, not cached cross-tenant.
6. **Modification with currency override**: prior booking USD → modification with `currency="CAD"` against CAD-allowed tenant → modification's value-tier rules evaluate against CAD thresholds.
7. **Modification rule 1 unchanged**: confirm `modification_within_30_min_value_increase` fires based on magnitude regardless of currency. Set up: prior booking value=1000, modification value=1500 (magnitude=0.5 > 0.2), within 30 min. Currency=USD → fires. Currency=CAD → still fires (test pins the currency-independence).
8. **Tenant with allowed_currencies including non-default currency but no value_caps for it**: tenant_config.allowed_currencies=["USD","JPY"], value_caps=None. Booking with `currency="JPY", value=15000`. → `absolute_high_value` fires against USD-default fallback (warning logged); test asserts both the rule fires AND the warning log.
9. **Multi-rule composition USD**: shipment_value=2500, ip_in_threat_list=true, USD tenant → both `absolute_high_value` (no, 2500 < 10000) and `threat_intel_high_value` (yes, 2500 > 2000) — verify the right composition.
10. **Cold tenant onboarding flow + currency**: use `scripts/tenant_onboard.py` (4A.5 helper) to create tenant with `allowed_currencies=["USD","EUR","CAD"]` and full value_caps; verify endpoint accepts requests in all 3 currencies and rejects "GBP" with 400.

### Case-1 / case-2 regression assertions

The existing `tests/integration/test_case_1_detection.py` and `tests/integration/test_case_2.py` should pass unchanged with USD-default tenant (the test fixtures don't set custom tenant_config). After 4B.5, re-run both:

- Case-1 (dashboard ATO ~50 shipments): final decision unchanged.
- Case-2 (API ATO ~21K shipments): final decision unchanged.

If either changes outcome, 4B.5 is broken — surface to operator. **Hard gate.**

If the tests as-written don't seed a tenant config (relying on the default `config={}` JSONB), they should pass without modification. If they DO seed a custom tenant_config, that seed needs `allowed_currencies` set to include the test currency (USD).

**Validation**:
- `pytest tests/integration/test_currency_normalization_e2e.py -v --asyncio-mode=auto` → 10 tests pass.
- `pytest tests/integration/test_case_1_detection.py tests/integration/test_case_2.py -v --asyncio-mode=auto` → 100% same outcomes as before 4B.5.
- `pytest tests/ --asyncio-mode=auto -q` → full suite green.

**Risk**: **High**. Integration tests are the validation gate for the 7-rule rewrite. Failure of test 1 (USD-default invariance) or the case-1/case-2 regression is a Phase 4B blocker.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 10 integration tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: test-only — test-reviewer + senior-engineer + code-flow.

---

## 4B.7 — `.ai/decisions.md` update — resolve § Currency normalization

**Theme**: Mark the `## Currency normalization (Phase 3D — deferred to Phase 4)` section as RESOLVED. Add a "Phase 4B resolution" subsection documenting the implementation choices.

**Files**:
- `.ai/decisions.md` (EDIT — rename section header, append resolution subsection)

**Specifics**:

Rename:
```markdown
## Currency normalization (Phase 3D — deferred to Phase 4)
```
to:
```markdown
## Currency normalization (RESOLVED in Phase 4B, 2026-06-01)
```

Append at end of section:

```markdown
### Phase 4B resolution (2026-06-01)

Implemented per the deferral plan:

1. `BookingRequest.shipment.currency` and `ModificationRequest.currency`
   added as optional `str` fields with `"USD"` default. Validation:
   3-letter uppercase ISO 4217 shape at the Pydantic layer; allowed-list
   check at request time against `tenant_config.allowed_currencies`.
2. `TenantConfig.value_caps: dict[str, dict[str, float]] | None`
   carries per-currency-per-tier thresholds. 4-tier scheme:
   `high / new_user / medium / low` matches the 4 distinct thresholds
   in the 7 rewritten rules.
3. `DEFAULT_VALUE_CAPS = {"USD": {"high": 10000, "new_user": 5000, "medium": 2000, "low": 1000}}` (`app/tenant_config.py`)
   matches Phase 2 hardcoded thresholds. USD-default tenants see zero
   behavioral change.
4. `resolve_value_caps(tenant_config, currency)` resolves per-request,
   falling back to USD defaults with a warning if the tenant has an
   allowed currency without a matching value_caps entry.
5. The 7 rules in `app/rules.yaml` were rewritten to consult
   `shipment_value_threshold_<tier>` Context fields populated in
   `build_context`. Weights and maturity-sensitive flags unchanged.
   Modification rule 1 (`modification_within_30_min_value_increase`)
   was NOT rewritten — its `modification_magnitude > 0.2` is a
   fraction, currency-independent.
6. Case-1 (dashboard ATO) and case-2 (API ATO) regression assertions
   pass unchanged with USD-default tenants.

### Currency conversion via rates table — explicitly rejected

Considered and rejected during Phase 4 planning. Reasons:
- Requires maintained rates data with refresh cadence.
- Float arithmetic against decay-weighted Welford accumulators
  introduces compounding precision drift.
- Per-currency thresholds are operator-tunable per tenant via
  `value_caps` and require no daily upkeep.

Currency conversion can be revisited if v2 demands cross-currency
risk aggregation; it is out of scope for v1.
```

**Validation**:
- Doc-reviewer reads the section.
- `git diff .ai/decisions.md` reviewed.

**Risk**: **Low**.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green (doc-only).

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: doc-only → doc-reviewer only.

---

## Batch 4B summary table

| Commit | Theme | Files | Tests added | Risk | Reviewer panel |
|---|---|---|---|---|---|
| 4B.1 | currency field on payloads | `app/models.py` (EDIT), 1 new test | 10 | Low | Standard + test-reviewer |
| 4B.2 | DEFAULT_VALUE_CAPS + resolution helper | `app/tenant_config.py` (EDIT), 1 new test | 8 | Low | Never-Skip + test-reviewer |
| 4B.3 | Request-time currency validation | `app/api/booking.py`, `app/api/modification.py`, 2 new tests | 9 | Medium | Never-Skip + db-reviewer + test-reviewer + security-auditor |
| 4B.4 | DSL whitelist +5 + Context derivations | `app/rules.py`, `app/context.py`, 1 new test | 6 | Medium | Never-Skip + security-auditor |
| 4B.5 | 7-rule rewrite | `app/rules.yaml`, 1 new test | 14 + parametrized invariance | Highest in Phase 4 | Standard + test-reviewer |
| 4B.6 | Integration tests + case-1/case-2 regression | 1 new test (+ existing case-1/case-2 re-run as gate) | 10 | High | test-reviewer + senior + code-flow |
| 4B.7 | `.ai/decisions.md` resolution | `.ai/decisions.md` (EDIT) | 0 | Low | doc-reviewer only |
| **Total** | | | **57 new tests** | | |

Expected test count at end of Batch 4B: **~724 (post-4A) + 57 = ~781 tests**.

Rule count at end of Batch 4B: **79** (unchanged — 7 rewritten, no count change).

ALLOWED_CONTEXT_FIELDS count at end of Batch 4B: **66 + 5 = 71 fields**.

Migrations count at end of Batch 4B: **5** (unchanged — no schema changes in 4B).
