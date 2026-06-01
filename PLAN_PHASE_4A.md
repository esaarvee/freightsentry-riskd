# Phase 4 — Batch 4A Plan — TenantConfig foundation

> **Status (2026-06-01)**: Pending operator approval. Batches 4B/4C/4D may be deferred per the per-batch checkpoint preference.

Batch 4A introduces the per-tenant configuration layer. It defines the `TenantConfig` Pydantic v2 model in a new module, adds a per-request loader that reads from the existing `tenants.config` JSONB column, extends `build_context` / `build_modification_context` signatures to thread `tenant_config` through, wires the loader into all three live endpoints (booking, modification, feedback), and ships a tenant onboarding CLI script. **No rule consumes the config in 4A** — 4B (currency normalization) and 4C (cold-start) are the consumers. 4A is the foundation.

Target: **7 commits** (6 implementation + integration tests folded inline).

---

## Decisions absorbed (batch-relevant subset)

| Decision | Value | Source |
|---|---|---|
| TenantConfig persistence column | **Reuse existing `tenants.config` JSONB** (0001_initial.py:42 — `config jsonb NOT NULL DEFAULT '{}'::jsonb`). **DEVIATES from Phase 4 prompt's `tenants.config_json`** — the prompt was drafted assuming a new column; verification confirms the column already exists under name `config` per `.ai/decisions.md § Per-tenant configuration` and `0001_initial.py:42`. Adding a second JSONB column with similar semantics would create two-column drift. | Phase 4 verification (this plan); operator confirmation requested at end-of-plan checkpoint |
| Migration in 4A | **None** — the JSONB column exists; default `'{}'` interpreted by Pydantic as empty config (all overrides None). 4A is code-only. | Phase 4 verification |
| TenantConfig module path | `app/tenant_config.py` (NEW) | Phase 4 prompt |
| Loader signature | `async def load_tenant_config(conn: asyncpg.Connection, tenant_id: int) -> TenantConfig` | Phase 4 prompt |
| Per-request loading | Fresh load on every booking/modification/feedback request. **No caching in 4A**; Phase 5 carry-forward. | Phase 4 prompt |
| TenantConfig fields | `tenant_id: int`, `config_version: int` (required); `maturity_age_days: int \| None`, `maturity_shipments: int \| None`, `maturity_k: float \| None`, `value_caps: dict[str, dict[str, float]] \| None`, `allowed_currencies: list[str] = ["USD"]`, `cold_start_grace_days: int = 0` (optional with defaults); `created_at: datetime`, `updated_at: datetime` (metadata) | Phase 4 prompt |
| TenantConfig defaults | `None` for override fields means "use project default from `app/scoring_constants.py`". `app/scoring_constants.py` REMAINS the source of truth for defaults. TenantConfig does NOT duplicate constants — it overrides them when set. | Phase 4 prompt + decisions.md § Scoring constants module |
| Validation timing | Pydantic validates on load (read path) AND on write (the `tenant_onboard.py` script + 4D admin write — out of scope here). 4A's loader path validates on read. | Phase 4 prompt |
| JSONB → Pydantic boundary cast | Use `cast(dict[str, Any], ...)` or explicit `TenantConfig.model_validate(...)` after `json.loads` if asyncpg returned `str`. Apply Phase 3B cast-at-boundary pattern. | Phase 3B lesson carried forward |
| `build_context` signature change | Add `tenant_config: TenantConfig` parameter (positional or keyword). Available in ctx but **no rule consumes it in 4A**. 4B and 4C are consumers. | Phase 4 prompt |
| `build_modification_context` signature change | Same — add `tenant_config: TenantConfig` and forward to `build_context`. | Phase 4 prompt |
| Endpoint wiring | Booking / modification / feedback endpoints load `tenant_config` AFTER `set_tenant_id` and BEFORE the prior-shipment / baseline reads. Passed to `build_context` / `build_modification_context`; feedback uses it only as a future hook (no current rule consumes it on feedback path either). | Phase 4 prompt |
| Onboarding script path | `scripts/tenant_onboard.py` (NEW) — follows existing `scripts/fetch_enrichment.py` pattern | Phase 4 prompt |
| Onboarding script semantics | Idempotent (re-runnable): UPSERT-like behavior. Takes CLI args: `--external-id` (tenant identifier, surfaces from `tenants.name`), `--display-name` (alias for tenant name), `--config-json` (optional path to a JSON file with initial config). Creates tenant row + writes config JSONB. Generates an API token, prints once. | Phase 4 prompt |
| Schema change | **None** in 4A. The `tenants.config` column already exists. | Phase 4 verification |
| `seeded_admin_token` fixture | Already exists in `tests/conftest.py:133-147`. Reused in 4D; not modified in 4A. | conftest.py |

### Documented deviation

The Phase 4 prompt says (in two places):

> "new column `tenants.config_json JSONB NULL`"
> "Migration: new column `tenants.config_json JSONB NOT NULL DEFAULT '{}'`"

`0001_initial.py:42` shows the column already exists:

```sql
CREATE TABLE tenants (
    id         serial PRIMARY KEY,
    name       text NOT NULL,
    config     jsonb NOT NULL DEFAULT '{}'::jsonb,    -- <-- already here, named `config`
    ...
);
```

And `.ai/decisions.md:215` documents:

> "Single `tenants.config JSONB` column. Schema validated at write time by `app/config_tenant.py::TenantConfig` (Pydantic v2)."

Two reconciliations possible:

1. **Reuse existing `tenants.config` column** (this plan's choice). Zero migration; matches decisions.md and reality.
2. Add `tenants.config_json` alongside the existing `tenants.config`. Creates two JSONB columns with identical semantics — wasteful.

Option 1 is the lowest-risk reversible interpretation. Surfacing as operator confirmation at end-of-plan checkpoint per CLAUDE.md autonomous-execution rule (an unanticipated decision arose during planning, not during execution, so AskUserQuestion is appropriate — not a STATUS.md row).

---

## Workflow context

- 6-step commit cycle per CLAUDE.md. Pre-commit hooks active.
- Reviewer routing per CLAUDE.md triage gate:
  - 4A.1 (TenantConfig Pydantic model — new file under `app/`): **Never-Skip** (new `.py` file under `app/`) → standard + test-reviewer.
  - 4A.2 (loader function + boundary cast): Never-Skip (new code path consuming auth-bound tenant_id; reading session-scoped data with RLS implication) → standard panel + db-reviewer + security-auditor.
  - 4A.3 (`build_context` / `build_modification_context` signature extension): Never-Skip (touches scoring/context wiring) → standard panel + test-reviewer.
  - 4A.4 (endpoint wiring across 3 endpoints): Never-Skip (auth-handling/transaction-scoped code) → standard panel + db-reviewer + test-reviewer.
  - 4A.5 (tenant onboarding script): standard panel + db-reviewer (writes to tenants + api_tokens).
  - 4A.6 (integration tests, test-only): test-reviewer + senior-engineer + code-flow.
  - 4A.7 (`.ai/decisions.md` update — doc-only): doc-reviewer only (per triage gate).

- Reviewer-invocation slice template:
  > `Plan file: PLAN_PHASE_4A.md, current commit: 4A.N (<title>), upcoming commits: 4A.{N+1} through 4A.7 sections. Read only those sections.`

---

## Cross-batch dependencies

- **Consumes from Phase 1**: existing `tenants` table at `alembic/versions/0001_initial.py:39-45` (including `config jsonb` column); `api_tokens` table at `:247-256`; `app/auth.py::require_api_token` for AuthContext shape.
- **Consumes from Phase 2**: `app/scoring_constants.py` constants — TenantConfig overrides reference these as fallback defaults. No change to scoring_constants in 4A.
- **Consumes from Phase 3**: `app/context.py::build_context` and `build_modification_context` signatures — extended in 4A.3.
- **Consumed by 4B**: 4B populates `value_caps` and `allowed_currencies` from the TenantConfig; rules consult ctx fields derived from these.
- **Consumed by 4C**: 4C reads `maturity_age_days / maturity_shipments / maturity_k / cold_start_grace_days` from `tenant_config` inside `score()`.
- **Consumed by 4D**: admin endpoints inherit the same TenantConfig load discipline; tenant onboarding (4A.5) is the path admins use to create tenants.
- **Consumed by Phase 5**: 4A's per-request fresh load is the hot-path target for Phase 5's in-process 60s TTL cache.

---

## 4A.1 — `app/tenant_config.py` — TenantConfig Pydantic v2 model

**Theme**: Define the `TenantConfig` Pydantic model in a new module. Validation only — no DB I/O in this commit. Pure model + unit tests.

**Files**:
- `app/tenant_config.py` (NEW)
- `tests/unit/test_tenant_config_model.py` (NEW)

**Specifics**:

```python
"""TenantConfig — per-tenant override layer over project defaults.

The model is the validation boundary between the JSONB stored in
`tenants.config` and the runtime scoring/rule paths. Override fields
default to None — None means "fall back to the project default in
app/scoring_constants.py". The constants module remains the source of
truth for defaults; this model layers overrides on top.

Loaded once per request by `load_tenant_config` (4A.2) and threaded
through `build_context` (4A.3) into the scorer.

Phase 4 scope: persistence + validation + threading. No rule consumes
config fields in 4A — 4B (currency normalization) and 4C (cold-start
overrides) are the consumers. The signature is in place so 4B and 4C
are pure extensions.

Phase 5 carry-forward: in-process 60s TTL cache wrapping the loader.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


DEFAULT_ALLOWED_CURRENCIES: list[str] = ["USD"]
DEFAULT_COLD_START_GRACE_DAYS: int = 0


class TenantConfig(BaseModel):
    """Pydantic v2 validation boundary for per-tenant overrides.

    Required fields (always present on a loaded config):
      tenant_id: int — FK to tenants.id
      config_version: int — bumped on every config change; defaults to 0
        for the empty-config case so newly-created tenants validate.

    Optional override fields (None means "use project default from
    app/scoring_constants.py"):
      maturity_age_days: int | None — overrides MATURITY_AGE_DAYS (default 180)
      maturity_shipments: int | None — overrides MATURITY_SHIPMENTS (default 50)
      maturity_k: float | None — overrides MATURITY_K (default 0.30)
      value_caps: dict[str, dict[str, float]] | None — per-currency-per-tier
        thresholds; shape {currency: {tier: threshold}} where tier ∈
        {high, new_user, medium, low}. Currency-implicit-USD default
        applied at the consumer (4B) when this field is None.

    Optional fields with non-None defaults (always set on load):
      allowed_currencies: list[str] = ["USD"] — currencies this tenant
        accepts. 4B validates BookingRequest.shipment.currency at request
        time against this list; 400 if not in.
      cold_start_grace_days: int = 0 — days post-tenant-onboarding during
        which scoring applies a 0.5x multiplier on the maturity formula
        (softer maturity-sensitive rule firing for newly-onboarded
        tenants). 0 disables. 4C is the consumer.

    Metadata:
      created_at, updated_at — both populated from tenant row at load
        time (4A.2). Not stored in JSONB; surfaced by the loader.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: int = Field(..., gt=0)
    config_version: int = Field(default=0, ge=0)

    maturity_age_days: int | None = Field(default=None, gt=0)
    maturity_shipments: int | None = Field(default=None, gt=0)
    maturity_k: float | None = Field(default=None, ge=0.0, le=1.0)
    value_caps: dict[str, dict[str, float]] | None = None
    allowed_currencies: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_CURRENCIES))
    cold_start_grace_days: int = Field(default=DEFAULT_COLD_START_GRACE_DAYS, ge=0)

    created_at: datetime
    updated_at: datetime

    @field_validator("allowed_currencies")
    @classmethod
    def _validate_currencies(cls, v: list[str]) -> list[str]:
        """ISO 4217 currency codes are 3 uppercase letters. Enforce shape
        without maintaining an enumerated list (acceptance of any 3-letter
        code lets tenants onboard for currencies we haven't pre-blessed)."""
        if not v:
            msg = "allowed_currencies must be non-empty"
            raise ValueError(msg)
        for code in v:
            if not (isinstance(code, str) and len(code) == 3 and code.isupper() and code.isalpha()):
                msg = f"allowed_currencies entry {code!r} must be a 3-letter uppercase ISO 4217 code"
                raise ValueError(msg)
        return v

    @field_validator("value_caps")
    @classmethod
    def _validate_value_caps(
        cls, v: dict[str, dict[str, float]] | None
    ) -> dict[str, dict[str, float]] | None:
        """Each outer key is a currency (validated like allowed_currencies).
        Each inner dict must contain the 4 tier keys with positive floats.

        Tier keys validated against {high, new_user, medium, low} —
        matches the 4 distinct thresholds in the 7 currency-implicit rules
        4B rewrites. Adding a 5th tier later is a model change reviewed
        under the standard panel."""
        if v is None:
            return None
        required_tiers = frozenset({"high", "new_user", "medium", "low"})
        for currency, tiers in v.items():
            if not (
                isinstance(currency, str)
                and len(currency) == 3
                and currency.isupper()
                and currency.isalpha()
            ):
                msg = f"value_caps currency {currency!r} must be 3-letter uppercase ISO 4217"
                raise ValueError(msg)
            present = frozenset(tiers.keys())
            if present != required_tiers:
                msg = (
                    f"value_caps[{currency!r}] keys must be exactly "
                    f"{sorted(required_tiers)}; got {sorted(present)}"
                )
                raise ValueError(msg)
            for tier, threshold in tiers.items():
                if not isinstance(threshold, (int, float)) or threshold <= 0:
                    msg = f"value_caps[{currency!r}][{tier!r}] must be positive number; got {threshold!r}"
                    raise ValueError(msg)
        return v


def parse_config_jsonb(
    raw: dict[str, Any],
    *,
    tenant_id: int,
    created_at: datetime,
    updated_at: datetime,
) -> TenantConfig:
    """Build a TenantConfig from a JSONB-decoded dict + the surrounding
    tenant row metadata (created_at, updated_at, tenant_id).

    The JSONB blob carries ONLY the override fields + config_version;
    the loader (4A.2) supplies tenant_id and timestamps. Centralising
    that here keeps 4A.2's loader concise.
    """
    payload = dict(raw or {})
    payload["tenant_id"] = tenant_id
    payload["created_at"] = created_at
    payload["updated_at"] = updated_at
    return TenantConfig.model_validate(payload)
```

`tests/unit/test_tenant_config_model.py` — 20 tests, including:

1. Empty config + required metadata → valid `TenantConfig` with all overrides None, `allowed_currencies=["USD"]`, `cold_start_grace_days=0`.
2. `extra="forbid"` rejects unknown top-level fields.
3. `maturity_age_days: 0` → ValidationError (must be > 0); `-1` → ValidationError.
4. `maturity_k: 1.5` → ValidationError (must be ≤ 1.0); `-0.1` → ValidationError.
5. `allowed_currencies=[]` → ValidationError (non-empty).
6. `allowed_currencies=["usd"]` → ValidationError (lowercase rejected).
7. `allowed_currencies=["US"]` → ValidationError (2-letter rejected).
8. `allowed_currencies=["USDX"]` → ValidationError (4-letter rejected).
9. `value_caps={"USD": {"high": 10000, "new_user": 5000, "medium": 2000, "low": 1000}}` → valid.
10. `value_caps={"USD": {"high": 10000}}` → ValidationError (missing 3 tier keys).
11. `value_caps={"USD": {"high": 10000, "new_user": 5000, "medium": 2000, "low": 1000, "extra": 9}}` → ValidationError (extra tier key).
12. `value_caps={"USD": {"high": -1, "new_user": 5000, "medium": 2000, "low": 1000}}` → ValidationError (non-positive).
13. `value_caps={"usd": {...}}` → ValidationError (lowercase currency).
14. `cold_start_grace_days=-1` → ValidationError (ge=0).
15. `tenant_id=0` → ValidationError (gt=0).
16. `config_version=-1` → ValidationError (ge=0).
17. `frozen=True` — assignment after construction raises.
18. `parse_config_jsonb({}, tenant_id=1, created_at=now, updated_at=now)` → valid.
19. `parse_config_jsonb({"unknown_field": 1}, ...)` → ValidationError (extra="forbid" applies to the merged dict).
20. `parse_config_jsonb(None, ...)` → valid (defensive — None treated as empty dict).

**Validation**:
- `pytest tests/unit/test_tenant_config_model.py -v` → 20 tests pass.
- `mypy app/` strict clean.
- `ruff check app/ tests/` clean.

**Risk**: **Low**. Pure model definition; no I/O.

**Reversibility**: Easy — git revert.

**Pre-commit verification**: All gates green.

**Observability**: N/A — no runtime behavior.

**Test changes**: 20 new unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None. Module is added but no other code imports it until 4A.2 (loader) and 4A.3 (signature extension).

**Reviewer routing**: Never-Skip (new `.py` file under `app/`) → standard + test-reviewer.

---

## 4A.2 — Loader: `load_tenant_config(conn, tenant_id) -> TenantConfig`

**Theme**: Add the per-request loader that reads `tenants.config` JSONB + `tenants.created_at` + `tenants.updated_at` (need to add `updated_at` column, see below) and returns a validated `TenantConfig`. Apply Phase 3B cast-at-boundary pattern for JSONB → Pydantic.

**Files**:
- `app/tenant_config.py` (EDIT — append `load_tenant_config` function)
- `alembic/versions/0005_tenants_updated_at.py` (NEW — adds `tenants.updated_at` column; `tenants` table currently has `first_seen` and `created_at` but no `updated_at`; we need the updated_at so admin writes in Phase 4D+ can surface staleness, and so the loaded TenantConfig.updated_at reflects last change)
- `tests/unit/test_tenant_config_loader.py` (NEW)

**Specifics**:

### Migration (0005_tenants_updated_at.py)

```python
"""Add tenants.updated_at column for TenantConfig staleness tracking.

Phase 4A: load_tenant_config returns TenantConfig.updated_at sourced
from this column. Default `now()` for existing tenants; future writes
SHOULD update the column via the onboarding script (4A.5) and admin
write endpoints (post-v1).

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE tenants
            ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

        COMMENT ON COLUMN tenants.updated_at IS
            'Last time the tenant row (including config JSONB) was modified.';
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS updated_at;")
```

### Loader (append to `app/tenant_config.py`)

```python
import json
from typing import Any, cast

import asyncpg


async def load_tenant_config(
    conn: asyncpg.Connection,
    tenant_id: int,
) -> TenantConfig:
    """Load the tenant's config from the `tenants.config` JSONB column.

    Returns a validated TenantConfig with all override fields populated
    from JSONB and metadata fields populated from row columns. If the
    JSONB is empty `{}` (the default for newly-created tenants), all
    override fields default to None and consumers fall back to
    app/scoring_constants.py.

    Phase 4 does NOT cache. Phase 5 wraps this in a 60s TTL cache.

    Defense-in-depth: explicit `tenant_id` parameter in the WHERE clause
    rather than relying on session-scoped RLS. (`tenants` table is NOT
    RLS-enabled per 0001_initial.py:37-38 — tenants are not scoped to
    themselves; we read by id with a tight WHERE.)

    Raises:
        LookupError: if tenant_id has no row (caller decides the HTTP
            translation — endpoints treat this as a 500-class
            misconfiguration since auth has already validated the
            token-tenant binding).
        pydantic.ValidationError: if the JSONB shape is invalid. This
            should never happen in production (the onboarding script
            validates on write) but surfaces stored-data corruption.
    """
    row = await conn.fetchrow(
        """
        SELECT config, created_at, updated_at
          FROM tenants
         WHERE id = $1
        """,
        tenant_id,
    )
    if row is None:
        msg = f"tenant {tenant_id} not found"
        raise LookupError(msg)

    # asyncpg may return JSONB as str OR dict depending on codec config.
    # Phase 3B cast-at-boundary pattern: handle both, narrow type for mypy.
    raw_config = row["config"]
    if isinstance(raw_config, str):
        decoded = cast("dict[str, Any]", json.loads(raw_config))
    else:
        decoded = cast("dict[str, Any]", raw_config or {})

    return parse_config_jsonb(
        decoded,
        tenant_id=tenant_id,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
```

### Unit tests

`tests/unit/test_tenant_config_loader.py` — 8 tests:

1. Load tenant with empty `config={}` → all overrides None, defaults applied.
2. Load tenant with `config={"maturity_age_days": 90}` → override set, others None.
3. Load tenant with full override config (all 5 overrides + `allowed_currencies=["USD","CAD"]` + `cold_start_grace_days=14`) → all populated correctly.
4. Load non-existent tenant_id → `LookupError`.
5. Load tenant with invalid JSONB shape (e.g., `value_caps` missing a tier) → `pydantic.ValidationError`.
6. Load tenant with JSONB stored as TEXT (asyncpg codec case A) → parses correctly.
7. Load tenant with JSONB returned as `dict` (asyncpg codec case B) → parses correctly.
8. Load tenant with `created_at < updated_at` (config was updated post-creation) → both timestamps populated correctly.

Tests use `db_conn` + `seeded_tenant` fixtures; each test seeds the relevant config and reads back.

**Validation**:
- `docker compose exec app alembic upgrade head` — migration applies cleanly.
- `docker compose exec app alembic downgrade -1 && docker compose exec app alembic upgrade head` — round-trip clean.
- `pytest tests/unit/test_tenant_config_loader.py -v --asyncio-mode=auto` → 8 tests pass.
- `pytest tests/ --asyncio-mode=auto -q` → full suite green (no regression on the 675 existing tests; migration is additive).
- `mypy app/` strict clean (boundary cast must compile).

**Risk**: **Medium**. Migration is additive but writes a new column to the tenants table. The loader is sub-millisecond but adds a sequential DB roundtrip to every request that calls it (4A.4 wires it into 3 endpoints). Phase 4 budget for the latency increase: ~1ms per request; Phase 5 cache eliminates.

**Reversibility**: Medium. Loader revert is clean; migration downgrade drops the column. Production data writes to `updated_at` would be lost on downgrade (acceptable pre-launch).

**Pre-commit verification**: All gates green.

**Observability**: Loader emits `structlog.get_logger(__name__).debug("tenant_config.loaded", tenant_id=..., config_version=..., metric=True)` on every load. Phase 5 surfaces in CloudWatch EMF; for Phase 4 it's a debug-level sanity log.

**Test changes**: 8 unit tests (loader) + zero integration changes in this commit (4A.4 + 4A.6 cover endpoint-level integration).

**Rollback plan**: `git revert` + `alembic downgrade -1`.

**Declared breaks**:
- **Scope**: Loader is defined and migration adds `tenants.updated_at`, but no production code path calls the loader. 4A.4 wires it in.
- **Resolved in**: 4A.4 (endpoint wiring).

**Reviewer routing**: Never-Skip (migration + new code path with auth-bound tenant_id) → standard panel + db-reviewer + security-auditor.

---

## 4A.3 — Extend `build_context` and `build_modification_context` signatures

**Theme**: Add `tenant_config: TenantConfig` parameter to both `build_context` and `build_modification_context`. The parameter is stored on `ctx` as a passthrough (or kept in scope for downstream consumers) but **no rule reads it in 4A**. 4B and 4C are the consumers.

**Files**:
- `app/context.py` (EDIT — extend two function signatures; add `tenant_config` to ctx dict as `_tenant_config` private key, OR pass to downstream as parameter only)
- `tests/unit/test_context_tenant_config_passthrough.py` (NEW — sanity test)

**Specifics**:

Decision: **DO NOT** add tenant_config to the `ctx` dict (the dict is the DSL evaluator's name-lookup environment; adding a non-DSL-field key would muddy the contract). Instead, accept `tenant_config` as a parameter, store nothing in ctx in 4A, and 4B/4C add derived ctx fields (e.g., `shipment_value_threshold_high`) that ARE DSL-valid names.

```python
async def build_context(
    conn: asyncpg.Connection,
    *,
    tenant_id: int,
    customer_id: int,
    customer_row: asyncpg.Record,
    enricher: Enricher,
    payload: BookingRequest,
    destination_hmac: str,
    tenant_config: TenantConfig,    # NEW — required keyword arg
    email_hmac: str | None = None,
    phone_hmac: str | None = None,
    as_of: date | None = None,
) -> tuple[dict[str, Any], CustomerBaseline, EnrichmentRow]:
    ...
    # In 4A: parameter accepted but unused inside the body. 4B and 4C
    # add derivations.
```

Same for `build_modification_context` — accept `tenant_config: TenantConfig` and forward to its inner `build_context` call.

```python
async def build_modification_context(
    conn: asyncpg.Connection,
    *,
    ...,
    tenant_config: TenantConfig,    # NEW
    ...,
) -> tuple[dict[str, Any], CustomerBaseline, EnrichmentRow]:
    ...
    ctx, baseline, enrichment = await build_context(
        conn,
        ...
        tenant_config=tenant_config,    # NEW — forward
    )
    ...
```

Import `TenantConfig` from `app.tenant_config` at the top of `app/context.py`.

### Unit test

`tests/unit/test_context_tenant_config_passthrough.py` — 3 tests:

1. `build_context` called with a valid `TenantConfig` returns a ctx unchanged from prior shape (no new keys yet). Asserts ctx has the same 66 DSL-eligible fields as Phase 3.
2. `build_modification_context` accepts `tenant_config` and forwards correctly (mock `build_context` and assert call kwargs include `tenant_config`).
3. Both functions raise `TypeError` if `tenant_config` is omitted (positional invocations from existing tests would break — this test pins the requirement so subsequent commits propagate the parameter).

**Validation**:
- `pytest tests/unit/test_context_tenant_config_passthrough.py -v --asyncio-mode=auto` → 3 tests pass.
- `pytest tests/ --asyncio-mode=auto -q` → **expected failures here** in any test that calls `build_context` / `build_modification_context` without `tenant_config`. This commit's job is to extend the signature; 4A.4 fixes the endpoint call sites; 4A.6 fixes any remaining test fixture call sites.
- `mypy app/` strict clean.

**Risk**: **High**. Signature change ripples across all build_context callers. Mid-commit-cycle this means tests will fail until 4A.4 + 4A.6 land. **Declared break** — see below.

**Reversibility**: Medium. Reverting requires reverting downstream commits too if they've shipped. Confined to one batch so revert-of-batch is the strategy.

**Pre-commit verification**: pre-commit will FAIL on `pytest tests/unit/` if existing unit tests call `build_context` without `tenant_config`. The pre-commit hook bypass policy: `--no-verify` IS permitted here per CLAUDE.md "Bypass policy — declared-break commits introducing transitional state". The declared-break section below names the bypass + the resolving commit.

**Observability**: N/A.

**Test changes**: 3 new unit tests; existing unit tests that call build_context expected to break until 4A.4 + 4A.6 land (declared break).

**Rollback plan**: `git revert` + revert subsequent commits in same batch.

**Declared breaks**:
- **Scope**: `build_context` and `build_modification_context` require new `tenant_config` parameter. Existing call sites in `app/api/booking.py`, `app/api/modification.py`, and tests in `tests/unit/test_context*.py` + `tests/integration/test_*` will fail to typecheck (mypy) and fail to run (pytest) until those call sites are updated.
- **Resolved in**: 4A.4 (endpoint call sites updated) + 4A.6 (integration test call sites updated). `pytest tests/unit/test_context*.py` may require fixture updates in conftest if tests call build_context directly; 4A.4 includes updating `tests/unit/conftest.py` `base_ctx` helper if needed.
- **Pre-commit bypass**: `git commit --no-verify` permitted for 4A.3. **Specifically bypassed**: `pytest tests/unit/ -x` (mypy may also fail depending on call-site typing). Other gates (`ruff check`, `ruff format`) must still pass. Bypass restored at 4A.4 commit.

**Reviewer routing**: Never-Skip (touches scoring/context wiring; transitional state introduced) → standard panel + test-reviewer.

---

## 4A.4 — Wire `load_tenant_config` into 3 endpoints + update test fixtures

**Theme**: Add the `load_tenant_config` call to `app/api/booking.py`, `app/api/modification.py`, and `app/api/feedback.py`. Update test fixtures that call `build_context` directly. Restore green test suite (resolves 4A.3 declared break).

**Files**:
- `app/api/booking.py` (EDIT — add load + pass to build_context)
- `app/api/modification.py` (EDIT — add load + pass to build_modification_context)
- `app/api/feedback.py` (EDIT — add load even though no consumer yet; 4B may consume on feedback path if currency validation applies to feedback request_ids)
- `tests/unit/conftest.py` (EDIT — `base_ctx` helper or any fixture that synthesizes a context for unit tests needs a default `TenantConfig` factory)
- `tests/unit/test_context*.py` (EDIT — pass a synthetic TenantConfig to build_context calls)
- `tests/integration/test_context.py` (EDIT — same)

**Specifics**:

### Endpoint wiring pattern (applied identically to all 3)

In each endpoint, after `set_tenant_id(conn, auth.tenant_id)` and BEFORE any other read:

```python
from app.tenant_config import TenantConfig, load_tenant_config

# inside the transaction, after set_tenant_id:
tenant_config = await load_tenant_config(conn, auth.tenant_id)
```

Then thread `tenant_config=tenant_config` into the `build_context` / `build_modification_context` call. Feedback endpoint loads it but does NOT pass to anything — kept for shape consistency + future hook (4B may add currency validation to feedback paths if target_request_id resolves to a non-default currency shipment).

### Booking endpoint specifics

Insert right after `set_tenant_id`:

```python
tenant_config = await load_tenant_config(conn, auth.tenant_id)
```

Pass to `build_context(conn, ..., tenant_config=tenant_config, ...)`.

### Modification endpoint specifics

Same pattern. Pass to `build_modification_context(..., tenant_config=tenant_config, ...)`.

### Feedback endpoint specifics

Load after `set_tenant_id`. **Do not** pass anywhere yet; keep variable in scope. Comment:

```python
# Load tenant_config for shape consistency with booking/modification.
# 4A defines no feedback-path consumer; 4B+ may consult allowed_currencies
# if feedback semantics extend to per-currency thresholds.
tenant_config = await load_tenant_config(conn, auth.tenant_id)
_ = tenant_config  # parked for 4B+
```

### Test fixture updates

In `tests/unit/conftest.py`, add a helper:

```python
from datetime import datetime, UTC

from app.tenant_config import TenantConfig

def make_default_tenant_config(tenant_id: int = 1) -> TenantConfig:
    """Synthetic TenantConfig for unit tests — all overrides None,
    defaults applied. Matches a freshly-onboarded tenant with empty
    config JSONB."""
    now = datetime.now(UTC)
    return TenantConfig(
        tenant_id=tenant_id,
        config_version=0,
        created_at=now,
        updated_at=now,
    )
```

Tests that call `build_context` directly pass `tenant_config=make_default_tenant_config()`.

### Latency impact

Per the watch-point: booking 9+1=10 sequential awaits; modification 11+1=12; feedback 5-8+1=6-9. The load is a single indexed lookup (~1ms p95). Phase 5 cache eliminates.

**Validation**:
- `pytest tests/ --asyncio-mode=auto -q` → full suite green (declared break resolved).
- `pytest tests/integration/test_booking_e2e.py -v` — booking flow unchanged behaviorally.
- `pytest tests/integration/test_modification_endpoint.py -v` — modification flow unchanged.
- `pytest tests/integration/test_feedback_endpoint.py -v` — feedback flow unchanged.
- `pytest tests/integration/test_phase3_cross_batch_chain.py -v` — Phase 3 cross-batch chain unchanged.
- `pytest tests/integration/test_case_1_detection.py tests/integration/test_case_2.py -v` — case-1 and case-2 BLOCK assertions hold (no behavioral change in 4A).
- `pytest tests/integration/test_rls_enforcement_under_riskd_app.py -v` — 3C.3 canary unchanged.
- `mypy app/` strict clean. `ruff check app/ tests/` clean.

**Risk**: **High**. Touches all 3 endpoints + multiple test fixtures. Hot path for every booking/modification/feedback request. Risk areas:
- Wrong placement of `load_tenant_config` (before `set_tenant_id` would fail when RLS activates in Phase 5)
- Missing `tenant_config=...` on a build_context call site → AttributeError at runtime, only caught by integration tests
- Test fixture `make_default_tenant_config()` not propagated to a niche test → that test fails post-merge

Mitigation: pre-commit `pytest tests/unit/` runs in the hook; full suite runs in the validate step. Senior + db reviewers verify every call site updated.

**Reviewer attention (operator note, 2026-06-01)**: 4A.4 has the same risk shape as Phase 3A's "5-test compound-evidence rewrite" — a niche test fixture missed → that test fails post-merge. Reviewer panel **must** enumerate every `build_context(` / `build_modification_context(` call site in `app/` and `tests/` and verify each carries `tenant_config=...`. `grep -rn 'build_context\b\|build_modification_context\b' app/ tests/` is the call-site inventory; cross-check against the diff. The declared break from 4A.3 is correctly resolved in 4A.4 itself (no separate commit needed — the fixture update is bundled with the endpoint wiring).

**Reversibility**: Medium-Hard. Reverting requires rolling back together with 4A.3. Confined to one batch so revert-of-batch is the strategy.

**Pre-commit verification**: All gates green (this commit restores the 4A.3 declared break).

**Observability**: Existing booking/modification/feedback structured logs unchanged. Tenant config load adds debug-level log per 4A.2 (no production cost).

**Test changes**: ~10-20 fixture updates across `tests/unit/` and `tests/integration/`; no new tests added in this commit (integration tests for tenant_config-driven behavior live in 4A.6).

**Rollback plan**: `git revert` (must revert 4A.3 too if 4A.3 already shipped to a downstream env — for local branch this is just `git reset HEAD~1`).

**Declared breaks**: None (restores transitional state from 4A.3).

**Reviewer routing**: Never-Skip (auth-handling/transaction-scoped code on hot path; affects 3 endpoints) → standard panel + db-reviewer + test-reviewer.

---

## 4A.5 — `scripts/tenant_onboard.py` — CLI tenant onboarding utility

**Theme**: Idempotent CLI script that creates a tenant + API token (or recreates token if requested) + writes initial config JSONB. Operator runs from the host shell or `docker compose exec app python scripts/tenant_onboard.py ...`.

**Files**:
- `scripts/tenant_onboard.py` (NEW)
- `tests/unit/test_tenant_onboard_script.py` (NEW — unit test of the CLI's helper functions; full-script integration test runs in 4A.6)

**Specifics**:

```python
#!/usr/bin/env python3
"""Tenant onboarding utility.

Creates a tenant row + an initial API token + writes an initial
TenantConfig (optionally loaded from a JSON file). Idempotent — re-runs
on the same `--external-id` (interpreted as `tenants.name` for the
Phase 4 schema) update the config JSONB and surface the existing tenant
id without creating a duplicate.

Usage:
    python scripts/tenant_onboard.py \\
        --external-id tenant-alpha \\
        --display-name "Alpha Corp" \\
        --config-json /path/to/config.json   # optional
        [--rotate-token]                       # optional: issue a new token, supersedes prior

The script prints the token ONCE on stdout — operator must capture it
immediately. Subsequent runs without `--rotate-token` print only the
tenant_id; no token is reprinted.

Phase 4 limitations:
- No FK to `app_users.role` here — admin onboarding via this script is
  out of scope. Operator can INSERT into `app_users` manually for
  admin principals; Phase 5+ may add an `--admin-user` flag.
- Token printed in plaintext to stdout. Production usage should pipe
  to a secret manager (e.g., `aws secretsmanager put-secret-value`)
  rather than store the stdout output.

Exit codes:
  0 — success
  1 — invalid arguments / config JSON
  2 — DB error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
from pathlib import Path

import asyncpg

from app.auth import _hash_token
from app.config import get_settings
from app.tenant_config import TenantConfig, parse_config_jsonb
from datetime import datetime, UTC


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tenant onboarding for freightsentry-riskd.")
    p.add_argument("--external-id", required=True, help="Stable tenant identifier (used as tenants.name).")
    p.add_argument("--display-name", required=True, help="Human-readable tenant display name.")
    p.add_argument(
        "--config-json",
        type=Path,
        default=None,
        help="Path to a JSON file containing initial TenantConfig override fields.",
    )
    p.add_argument(
        "--rotate-token",
        action="store_true",
        help="Issue a new API token even if the tenant already exists.",
    )
    return p.parse_args()


def _load_initial_config(path: Path | None) -> dict:
    if path is None:
        return {}
    if not path.exists():
        print(f"error: --config-json path does not exist: {path}", file=sys.stderr)
        sys.exit(1)
    with path.open(encoding="utf-8") as f:
        try:
            raw = json.load(f)
        except json.JSONDecodeError as e:
            print(f"error: --config-json is not valid JSON: {e}", file=sys.stderr)
            sys.exit(1)
    if not isinstance(raw, dict):
        print("error: --config-json must contain a JSON object at the top level", file=sys.stderr)
        sys.exit(1)
    return raw


def _validate_initial_config(config_dict: dict) -> None:
    """Validate the config dict against TenantConfig before writing to DB."""
    now = datetime.now(UTC)
    try:
        parse_config_jsonb(config_dict, tenant_id=1, created_at=now, updated_at=now)
    except Exception as e:
        print(f"error: initial config fails TenantConfig validation: {e}", file=sys.stderr)
        sys.exit(1)


async def _onboard(
    external_id: str,
    display_name: str,
    initial_config: dict,
    rotate_token: bool,
) -> None:
    settings = get_settings()
    conn = await asyncpg.connect(settings.database_url)
    try:
        async with conn.transaction():
            # UPSERT tenant by external_id (which is `tenants.name` in
            # the schema; the column is plain text, no UNIQUE today, so
            # we manually enforce uniqueness via a SELECT-then-INSERT
            # pattern). If multiple rows match, fail loudly.
            existing = await conn.fetch(
                "SELECT id FROM tenants WHERE name = $1", external_id,
            )
            if len(existing) > 1:
                print(
                    f"error: multiple tenants with name={external_id!r} found; "
                    "manual intervention required",
                    file=sys.stderr,
                )
                sys.exit(2)
            if len(existing) == 1:
                tenant_id = existing[0]["id"]
                await conn.execute(
                    """
                    UPDATE tenants
                       SET config = $1::jsonb,
                           updated_at = now()
                     WHERE id = $2
                    """,
                    json.dumps(initial_config),
                    tenant_id,
                )
                print(f"updated tenant id={tenant_id} name={external_id!r}")
            else:
                tenant_id = await conn.fetchval(
                    """
                    INSERT INTO tenants (name, config)
                    VALUES ($1, $2::jsonb)
                    RETURNING id
                    """,
                    external_id,
                    json.dumps(initial_config),
                )
                print(f"created tenant id={tenant_id} name={external_id!r}")

            # Issue token if new tenant OR --rotate-token requested.
            token_count = await conn.fetchval(
                "SELECT count(*) FROM api_tokens WHERE tenant_id = $1",
                tenant_id,
            )
            if rotate_token or token_count == 0:
                plaintext = secrets.token_urlsafe(32)
                await conn.execute(
                    """
                    INSERT INTO api_tokens (tenant_id, token_hash, role)
                    VALUES ($1, $2, 'tenant')
                    """,
                    tenant_id,
                    _hash_token(plaintext),
                )
                print(f"display_name={display_name!r}")
                print(f"api_token={plaintext}  # CAPTURE NOW — not reprinted")
            else:
                print("api_token: existing (use --rotate-token to issue a new one)")
    finally:
        await conn.close()


def main() -> None:
    args = _parse_args()
    initial_config = _load_initial_config(args.config_json)
    _validate_initial_config(initial_config)
    asyncio.run(
        _onboard(
            external_id=args.external_id,
            display_name=args.display_name,
            initial_config=initial_config,
            rotate_token=args.rotate_token,
        )
    )


if __name__ == "__main__":
    main()
```

`tests/unit/test_tenant_onboard_script.py` — 6 tests on helpers:

1. `_load_initial_config(None)` → `{}`.
2. `_load_initial_config(<valid-path>)` → dict matching file contents.
3. `_load_initial_config(<bad-path>)` → SystemExit(1) (via `pytest.raises`).
4. `_load_initial_config(<non-dict-json>)` → SystemExit(1).
5. `_validate_initial_config({})` → returns None.
6. `_validate_initial_config({"unknown": 1})` → SystemExit(1) (Pydantic `extra="forbid"`).

**Validation**:
- `pytest tests/unit/test_tenant_onboard_script.py -v` → 6 tests pass.
- Manual smoke test: `docker compose exec app python scripts/tenant_onboard.py --external-id testing-tenant-X --display-name "Test X"` → prints tenant id + token.
- `mypy app/ scripts/` strict clean (or scoped per existing mypy config; if `scripts/` is excluded, document the bypass).
- `ruff check app/ tests/ scripts/` clean.

**Risk**: **Medium**. Production-affecting script (creates tenants). The idempotent pattern (UPSERT by name) is correct only if `tenants.name` is treated as a stable external identifier — which is the Phase 4 convention.

**Reversibility**: Medium. Reverting the script is clean; tenants created by it can be deleted via `DELETE FROM tenants WHERE id = $1` (cascade via the FKs in the cleanup pattern).

**Pre-commit verification**: All gates green.

**Observability**: stdout output. Not structured-log-style; this is operator-tooling.

**Test changes**: 6 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: standard panel + db-reviewer (script writes to `tenants` + `api_tokens`).

---

## 4A.6 — Integration tests: tenant config load + cross-tenant isolation + onboarding-via-script

**Theme**: End-to-end integration tests that confirm: (a) every endpoint loads the tenant's own config, not another tenant's; (b) per-request load returns fresh data after a `tenants.config` UPDATE between requests; (c) the onboarding script creates a working tenant + token that the booking endpoint accepts.

**Files**:
- `tests/integration/test_tenant_config_integration.py` (NEW)
- `tests/integration/test_tenant_onboard_script_integration.py` (NEW)

**Specifics**:

### `tests/integration/test_tenant_config_integration.py`

8 tests:

1. **Empty config tenant**: seeded tenant with `config={}` → booking endpoint succeeds; loaded TenantConfig has all overrides None; default `allowed_currencies=["USD"]`.
2. **Custom config tenant**: seeded tenant with `config={"maturity_age_days": 90, "cold_start_grace_days": 7}` → booking endpoint succeeds; loaded TenantConfig reflects overrides. (4A scope: no behavioral difference in scoring yet — that's 4C; this test confirms load shape only via direct SELECT or via a debug endpoint hook.)
3. **Cross-tenant isolation**: seed tenant_a with `config={"maturity_age_days": 60}` and tenant_b with `config={"maturity_age_days": 200}`. Send booking POST under tenant_a's token, then tenant_b's. Both succeed; structured logs (or debug query) confirm each request loaded its own config. (No way to assert directly without exposing config in response — instead, parametrize the test with `unittest.mock.patch` on `load_tenant_config` to capture and assert per-request `tenant_id` argument.)
4. **Per-request fresh load**: send booking with tenant_a's config={}; UPDATE tenant_a's config to `{"cold_start_grace_days": 14}`; send second booking. Mock or capture the second load returns the updated config_version (or in absence of an exposed version, the patched loader call count and the value).
5. **Modification endpoint**: same shape — modification endpoint loads tenant_config and threads to build_modification_context.
6. **Feedback endpoint**: feedback POST under a tenant with custom config; assert loader is called with the correct tenant_id.
7. **Invalid stored config corruption**: stored `tenants.config={"value_caps": {"USD": {"high": 10000}}}` (missing 3 tier keys → invalid). Booking POST → 500 (or 422 if we map ValidationError to 422; check whether to add a translation in 4A.4 or leave as 500). For 4A: leave as 500 (stored-config corruption is a configuration error, not a client error). Test asserts response status 500 + log entry mentions `tenant_config.invalid`.
8. **Tenant not found**: deleting the tenant row mid-request would race; instead, simulate via mocked loader raising LookupError → 500 (auth pre-validates the token-tenant binding so this is a configuration race).

### `tests/integration/test_tenant_onboard_script_integration.py`

4 tests, invoking the script via `subprocess.run` (or importing `_onboard` and running with asyncio):

1. **Create new tenant**: script with --external-id and --display-name → tenant row created, token printed, token works (booking POST under returned token returns 200).
2. **Re-run on existing tenant (no rotate)**: tenant row not duplicated; token unchanged; script prints "existing".
3. **Re-run with --rotate-token**: new token issued; old token NOT invalidated (current behavior — Phase 5 may add revocation). New token works.
4. **Initial config file applied**: script with --config-json path → `tenants.config` JSONB matches file contents; subsequent load_tenant_config returns those values.

**Validation**:
- `pytest tests/integration/test_tenant_config_integration.py -v --asyncio-mode=auto` → 8 tests pass.
- `pytest tests/integration/test_tenant_onboard_script_integration.py -v --asyncio-mode=auto` → 4 tests pass.
- `pytest tests/ --asyncio-mode=auto -q` → full suite green; test count ≈ 675 + 12 (new) + others added in 4A.1 (20) + 4A.2 (8) + 4A.3 (3) + 4A.5 (6) = **675 + 49 = ~724 tests**.

**Risk**: **Medium**. Integration tests for cross-tenant isolation are central to 4A's correctness claim. Test 3 (cross-tenant isolation) is the safety-net for the "tenant_config loaded for tenant_a's request must reflect tenant_a's stored row" invariant.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A — tests only.

**Test changes**: 12 integration tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: test-only — test-reviewer + senior-engineer + code-flow.

---

## 4A.7 — `.ai/decisions.md` update — TenantConfig design choices

**Theme**: Add a new section to `.ai/decisions.md` documenting TenantConfig's shape, the column-name resolution (`tenants.config` reused; deviation from Phase 4 prompt text), the value_caps tier choice (4-tier), the loading-without-caching choice (Phase 5 carry-forward), and the override-vs-default convention (None means fall back to `scoring_constants.py`).

**Files**:
- `.ai/decisions.md` (EDIT — append new section after "Per-tenant configuration")

**Specifics**:

Append a new dated section:

```markdown
## TenantConfig design (Phase 4A — 2026-06-01)

Phase 4A operationalizes the per-tenant configuration layer described
in `## Per-tenant configuration` above. The following choices were made
during 4A planning + execution.

### Column reuse

`tenants.config` (already in `alembic/versions/0001_initial.py:42` as
`jsonb NOT NULL DEFAULT '{}'`) is the storage column. The Phase 4
prompt initially referenced `tenants.config_json` as a new column; that
was a drafting inconsistency with the pre-existing schema. 4A reuses
the existing column (operator-confirmed at end of 4A planning).

### Module

`app/tenant_config.py` (Phase 4 prompt path). The earlier
`app/config_tenant.py` reference (this file, line 215, pre-Phase 4) is
superseded.

### Override fields

TenantConfig carries override fields with `None` defaults meaning "use
the project default from `app/scoring_constants.py`". Specifically:

- `maturity_age_days: int | None` (default `MATURITY_AGE_DAYS = 180`)
- `maturity_shipments: int | None` (default `MATURITY_SHIPMENTS = 50`)
- `maturity_k: float | None` (default `MATURITY_K = 0.30`)
- `value_caps: dict[str, dict[str, float]] | None` (default applied by
  4B consumer)

Non-None defaults:

- `allowed_currencies: list[str] = ["USD"]`
- `cold_start_grace_days: int = 0`

`app/scoring_constants.py` REMAINS the source of truth for project
defaults. TenantConfig is overrides on top of defaults; it does NOT
duplicate constants.

### value_caps shape: 4-tier per-currency

`value_caps: dict[currency: str, dict[tier: str, threshold: float]]`
where tiers are exactly `{high, new_user, medium, low}` — matches the
4 distinct thresholds in the 7 currency-implicit rules 4B rewrites.

Adding a 5th tier requires a TenantConfig model change reviewed under
the standard panel.

### Loading semantics

Per-request fresh load via `load_tenant_config(conn, tenant_id)`. No
caching in Phase 4. Phase 5 wraps the loader with a 60s in-process
TTL cache (carry-forward).

### Validation timing

- Read path: `parse_config_jsonb` validates the stored JSONB → Pydantic
  every time the loader runs. Stored-data corruption surfaces as a 500
  on the request.
- Write path: `scripts/tenant_onboard.py` validates before INSERT/UPDATE.

### Currency normalization (deferred to 4B)

The `## Currency normalization` section in this file describes the
USD-implicit assumption documented at end of Phase 3D. 4B implements
the resolution.

### Cold-start grace mechanism (deferred to 4C)

`cold_start_grace_days` — during the grace window, the maturity formula
multiplies by 0.5. Consumer is 4C.
```

**Validation**:
- `markdownlint` (if configured) clean.
- `git diff .ai/decisions.md` reviewed by doc-reviewer.

**Risk**: **Low**.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green (doc-only).

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: doc-only — doc-reviewer only (per triage gate).

---

## Batch 4A summary table

| Commit | Theme | Files | Tests added | Risk | Reviewer panel |
|---|---|---|---|---|---|
| 4A.1 | TenantConfig Pydantic v2 model | `app/tenant_config.py` (NEW), 1 new test | 20 | Low | Never-Skip + test-reviewer |
| 4A.2 | Loader + tenants.updated_at migration | `app/tenant_config.py` (EDIT), 1 new alembic, 1 new test | 8 | Medium | Never-Skip + db-reviewer + security-auditor |
| 4A.3 | `build_context` signature extension | `app/context.py` (EDIT), 1 new test | 3 | High (declared break) | Never-Skip + test-reviewer |
| 4A.4 | Wire load into 3 endpoints + fixture fixes | `app/api/booking.py`, `app/api/modification.py`, `app/api/feedback.py`, `tests/unit/conftest.py`, multiple test files | 0 (fixture-only changes) | High | Never-Skip + db-reviewer + test-reviewer |
| 4A.5 | Tenant onboarding script | `scripts/tenant_onboard.py` (NEW), 1 new test | 6 | Medium | Standard + db-reviewer |
| 4A.6 | Integration tests | 2 new tests | 12 | Medium | test-reviewer + senior + code-flow |
| 4A.7 | `.ai/decisions.md` update | `.ai/decisions.md` (EDIT) | 0 | Low | doc-reviewer only |
| **Total** | | | **49 new tests** | | |

Expected test count at end of Batch 4A: **675 + 49 = ~724 tests**.

Migrations count at end of Batch 4A: **5** (0001, 0002, 0003, 0004, 0005).

New module count: **1** (`app/tenant_config.py`).

`tenants.config_json` column count: **0** (reused existing `tenants.config`).

ALLOWED_CONTEXT_FIELDS count at end of Batch 4A: **66** (unchanged — 4B adds 5; 4C adds 0).

Rules count at end of Batch 4A: **79** (unchanged — 4B rewrites 7; no count change).
