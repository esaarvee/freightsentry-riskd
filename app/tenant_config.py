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
      created_at, updated_at — both populated from the tenants row at
        load time (4A.2). Not stored in JSONB; surfaced by the loader.
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
        # Tier keys match the 4 distinct thresholds in the 7 currency-implicit
        # rules 4B rewrites. Adding a 5th tier is a model change reviewed
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
    the loader (4A.2) supplies tenant_id and timestamps. Centralising
    that here keeps 4A.2's loader concise.

    `raw=None` is treated as an empty config (defensive — defaults
    `tenants.config` to `'{}'::jsonb` per `0001_initial.py:42`, but
    decoded values flowing through asyncpg could theoretically be None
    in edge cases).
    """
    payload: dict[str, Any] = dict(raw or {})
    payload["tenant_id"] = tenant_id
    payload["created_at"] = created_at
    payload["updated_at"] = updated_at
    return TenantConfig.model_validate(payload)
