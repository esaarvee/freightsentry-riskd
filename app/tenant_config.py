"""TenantConfig — per-tenant override layer over project defaults.

The model is the validation boundary between the JSONB stored in
`tenants.config` and the runtime scoring/rule paths. Override fields
default to None — None means "fall back to the project default in
app/scoring_constants.py". The constants module remains the source of
truth for defaults; this model layers overrides on top.

Loaded once per request by `load_tenant_config` and threaded
through `build_context` into the scorer.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast

import asyncpg
import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

_log = structlog.get_logger(__name__)

DEFAULT_ALLOWED_CURRENCIES: list[str] = ["CAD"]
DEFAULT_COLD_START_GRACE_DAYS: int = 0

# Project-default per-currency value caps.
#
# Tier values match the absolute literals in the currency-implicit rules
# (see app/rules.yaml):
#   - high     = 10000   (absolute_high_value)
#   - new_user = 5000    (high_value_new_user)
#   - medium   = 2000    (flags_with_value, threat_intel_high_value,
#                         ip2p_threat_high_value)
#   - low      = 1000    (low_trust_high_value, vpn_high_value)
#
# CAD-default. The project is a Canadian freight aggregator; CAD is the
# operational currency. Thresholds are interpreted as CAD, no
# exchange-rate conversion. Tenants that need non-CAD pricing populate
# tenant_config.value_caps with per-currency overrides. Empty/None
# value_caps means "use these defaults". If a tenant adds a non-CAD
# currency to allowed_currencies but doesn't provide a matching
# value_caps[currency], resolve_value_caps falls back to CAD-default and
# emits a warning log.
DEFAULT_VALUE_CAPS: dict[str, dict[str, float]] = {
    "CAD": {
        "high": 10000.0,
        "new_user": 5000.0,
        "medium": 2000.0,
        "low": 1000.0,
    }
}


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
        {high, new_user, medium, low}. Currency-implicit-CAD default
        applied at the consumer when this field is None.

    Optional fields with non-None defaults (always set on load):
      allowed_currencies: list[str] = ["CAD"] — currencies this tenant
        accepts. BookingRequest.shipment.currency is validated against
        this list at request time; 400 if not in.
      cold_start_grace_days: int = 0 — days post-tenant-onboarding during
        which scoring applies a 0.5x multiplier on the maturity formula
        (softer maturity-sensitive rule firing for newly-onboarded
        tenants). 0 disables. Consumed by the scorer's cold-start grace.

    Metadata:
      created_at, updated_at — both populated from the tenants row at
        load time. Not stored in JSONB; surfaced by the loader.
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
        # ISO 4217 currency codes are 3 uppercase letters. Enforce shape
        # without maintaining an enumerated allow-list (acceptance of any
        # 3-letter code lets tenants onboard for currencies we haven't
        # pre-blessed).
        if not v:
            msg = "allowed_currencies must be non-empty"
            raise ValueError(msg)
        for code in v:
            if not (isinstance(code, str) and len(code) == 3 and code.isupper() and code.isalpha()):
                msg = (
                    f"allowed_currencies entry {code!r} must be a 3-letter uppercase ISO 4217 code"
                )
                raise ValueError(msg)
        return v

    @field_validator("value_caps", mode="before")
    @classmethod
    def _validate_value_caps(cls, v: object) -> object:
        # mode="before" so the validator sees the raw input BEFORE Pydantic
        # coerces (a bool tier value would otherwise become 1.0 silently).
        # Each outer key is a currency (validated like allowed_currencies).
        # Each inner dict must contain the 4 tier keys with positive floats.
        # Tier keys match the 4 distinct thresholds in the currency-implicit
        # rules. Adding a 5th tier is a model change reviewed
        # under the standard panel.
        if v is None:
            return None
        if not isinstance(v, dict):
            msg = f"value_caps must be a dict or None; got {type(v).__name__}"
            raise ValueError(msg)
        required_tiers = frozenset({"high", "new_user", "medium", "low"})
        for currency, tiers in v.items():
            if not isinstance(tiers, dict):
                msg = f"value_caps[{currency!r}] must be a dict; got {type(tiers).__name__}"
                raise ValueError(msg)
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
                # `isinstance(x, (int, float))` admits bool (bool subclasses int)
                # — reject explicitly so `{"high": true}` from a malformed JSONB
                # blob can't pass as a threshold of 1.
                if (
                    isinstance(threshold, bool)
                    or not isinstance(threshold, int | float)
                    or threshold <= 0
                ):
                    msg = (
                        f"value_caps[{currency!r}][{tier!r}] must be a "
                        f"positive number; got {threshold!r}"
                    )
                    raise ValueError(msg)
        return v


def parse_config_jsonb(
    raw: dict[str, Any] | None,
    *,
    tenant_id: int,
    created_at: datetime,
    updated_at: datetime,
) -> TenantConfig:
    """Build a TenantConfig from a JSONB-decoded dict + the surrounding
    tenant row metadata (created_at, updated_at, tenant_id).

    The JSONB blob carries ONLY the override fields + config_version;
    the loader supplies tenant_id and timestamps. Centralising that here
    keeps the loader concise.

    `raw=None` is treated as an empty config (defensive — `tenants.config`
    defaults to `'{}'::jsonb`, but
    decoded values flowing through asyncpg could theoretically be None
    in edge cases).
    """
    payload: dict[str, Any] = dict(raw or {})
    payload["tenant_id"] = tenant_id
    payload["created_at"] = created_at
    payload["updated_at"] = updated_at
    return TenantConfig.model_validate(payload)


async def load_tenant_config(
    conn: asyncpg.Connection,
    tenant_id: int,
) -> TenantConfig:
    """Load the tenant's config from the `tenants.config` JSONB column.

    Returns a validated TenantConfig with override fields populated from
    JSONB and metadata fields populated from row columns. Empty JSONB
    `{}` (default for newly-created tenants) yields all overrides None;
    consumers fall back to project defaults in app/scoring_constants.py.

    Defense-in-depth: explicit `tenant_id` parameter in the WHERE clause
    rather than relying on session-scoped RLS. (The `tenants` table is
    NOT RLS-enabled — tenants are not scoped to themselves; we read by id
    with a tight WHERE.)

    Raises:
        LookupError: if tenant_id has no row. The caller decides the
            HTTP translation — endpoints treat this as a 500-class
            misconfiguration because auth has already validated the
            token-tenant binding.
        pydantic.ValidationError: if the stored JSONB shape is invalid.
            Should not occur in production (onboarding script validates
            on write) but surfaces stored-data corruption.
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
    # Cast-at-boundary pattern: handle both, narrow type for mypy.
    raw_config = row["config"]
    decoded: dict[str, Any]
    if isinstance(raw_config, str):
        decoded = cast("dict[str, Any]", json.loads(raw_config))
    else:
        decoded = cast("dict[str, Any]", raw_config or {})

    config = parse_config_jsonb(
        decoded,
        tenant_id=tenant_id,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
    _log.debug(
        "tenant_config.loaded",
        tenant_id=tenant_id,
        config_version=config.config_version,
        metric=True,
    )
    return config


def resolve_value_caps(
    tenant_config: TenantConfig,
    currency: str,
) -> dict[str, float]:
    """Return the 4-tier threshold dict for the given currency.

    Resolution priority:
      1. tenant_config.value_caps[currency] if both the dict and the
         currency key are present.
      2. DEFAULT_VALUE_CAPS["CAD"] as a safety fallback. Logs a
         `tenant_config.value_caps.fallback`
         warning with the tenant_id and currency so operators can
         spot the misconfiguration.

    Currency is validated at request time before this helper runs
    — so a currency reaching this helper is always in
    tenant_config.allowed_currencies. The fallback covers the
    operator-misconfiguration case where a currency is allowed but no
    tier dict exists for it.
    """
    if tenant_config.value_caps and currency in tenant_config.value_caps:
        return tenant_config.value_caps[currency]
    _log.warning(
        "tenant_config.value_caps.fallback",
        tenant_id=tenant_config.tenant_id,
        currency=currency,
        metric=True,
    )
    return DEFAULT_VALUE_CAPS["CAD"]
