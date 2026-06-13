"""Unit tests for TenantConfig.

25 tests covering:
- Empty config + required metadata happy path
- extra="forbid" rejection of unknown top-level fields
- Numeric bound validators on maturity_age_days, maturity_shipments, maturity_k
- allowed_currencies shape (non-empty, 3-letter, uppercase, alpha)
- value_caps shape (currency code, 4-tier dict, positive thresholds)
- cold_start_grace_days, tenant_id, config_version bounds
- frozen=True immutability
- parse_config_jsonb helper (empty dict, None, extra-field rejection)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.tenant_config import (
    DEFAULT_ALLOWED_CURRENCIES,
    DEFAULT_COLD_START_GRACE_DAYS,
    TenantConfig,
    parse_config_jsonb,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _base_kwargs(**overrides: object) -> dict[str, object]:
    """Build the minimum required kwargs for TenantConfig construction."""
    base: dict[str, object] = {
        "tenant_id": 1,
        "created_at": _now(),
        "updated_at": _now(),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1: Empty config + required metadata
# ---------------------------------------------------------------------------


def test_empty_config_defaults() -> None:
    tc = TenantConfig(**_base_kwargs())
    assert tc.tenant_id == 1
    assert tc.config_version == 0
    assert tc.maturity_age_days is None
    assert tc.maturity_shipments is None
    assert tc.maturity_k is None
    assert tc.value_caps is None
    assert tc.allowed_currencies == DEFAULT_ALLOWED_CURRENCIES
    assert tc.cold_start_grace_days == DEFAULT_COLD_START_GRACE_DAYS


# ---------------------------------------------------------------------------
# 2: extra="forbid" rejection
# ---------------------------------------------------------------------------


def test_extra_forbid_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(unknown_field="x"))


# ---------------------------------------------------------------------------
# 3-4: maturity_age_days / maturity_k bounds
# ---------------------------------------------------------------------------


def test_maturity_age_days_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(maturity_age_days=0))


def test_maturity_age_days_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(maturity_age_days=-1))


def test_maturity_k_above_one_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(maturity_k=1.5))


def test_maturity_k_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(maturity_k=-0.1))


def test_maturity_shipments_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(maturity_shipments=0))


def test_maturity_shipments_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(maturity_shipments=-1))


def test_value_caps_bool_threshold_rejected() -> None:
    # bool subclasses int, so isinstance(True, (int, float)) is True.
    # The validator must reject booleans explicitly.
    with pytest.raises(ValidationError):
        TenantConfig(
            **_base_kwargs(
                value_caps={
                    "USD": {
                        "high": True,  # type: ignore[dict-item]
                        "new_user": 5000.0,
                        "medium": 2000.0,
                        "low": 1000.0,
                    }
                }
            )
        )


# ---------------------------------------------------------------------------
# 5-8: allowed_currencies shape
# ---------------------------------------------------------------------------


def test_allowed_currencies_empty_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(allowed_currencies=[]))


def test_allowed_currencies_lowercase_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(allowed_currencies=["usd"]))


def test_allowed_currencies_two_letter_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(allowed_currencies=["US"]))


def test_allowed_currencies_four_letter_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(allowed_currencies=["USDX"]))


# ---------------------------------------------------------------------------
# 9-13: value_caps shape
# ---------------------------------------------------------------------------


def test_value_caps_valid_full() -> None:
    tc = TenantConfig(
        **_base_kwargs(
            value_caps={
                "USD": {
                    "high": 10000.0,
                    "new_user": 5000.0,
                    "medium": 2000.0,
                    "low": 1000.0,
                }
            }
        )
    )
    assert tc.value_caps is not None
    assert tc.value_caps["USD"]["high"] == 10000.0


def test_value_caps_missing_tier_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(value_caps={"USD": {"high": 10000.0}}))


def test_value_caps_extra_tier_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(
            **_base_kwargs(
                value_caps={
                    "USD": {
                        "high": 10000.0,
                        "new_user": 5000.0,
                        "medium": 2000.0,
                        "low": 1000.0,
                        "extra": 9.0,
                    }
                }
            )
        )


def test_value_caps_negative_threshold_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(
            **_base_kwargs(
                value_caps={
                    "USD": {
                        "high": -1.0,
                        "new_user": 5000.0,
                        "medium": 2000.0,
                        "low": 1000.0,
                    }
                }
            )
        )


def test_value_caps_lowercase_currency_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(
            **_base_kwargs(
                value_caps={
                    "usd": {
                        "high": 10000.0,
                        "new_user": 5000.0,
                        "medium": 2000.0,
                        "low": 1000.0,
                    }
                }
            )
        )


# ---------------------------------------------------------------------------
# 14-16: cold_start_grace_days, tenant_id, config_version bounds
# ---------------------------------------------------------------------------


def test_cold_start_grace_days_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(cold_start_grace_days=-1))


def test_tenant_id_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(tenant_id=0))


def test_config_version_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(**_base_kwargs(config_version=-1))


# ---------------------------------------------------------------------------
# 17: frozen=True immutability
# ---------------------------------------------------------------------------


def test_frozen_model_rejects_assignment() -> None:
    tc = TenantConfig(**_base_kwargs())
    with pytest.raises(ValidationError):
        tc.tenant_id = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 18-20: parse_config_jsonb helper
# ---------------------------------------------------------------------------


def test_parse_config_jsonb_empty_dict() -> None:
    now = _now()
    tc = parse_config_jsonb({}, tenant_id=1, created_at=now, updated_at=now)
    assert tc.tenant_id == 1
    assert tc.maturity_age_days is None
    assert tc.allowed_currencies == DEFAULT_ALLOWED_CURRENCIES


def test_parse_config_jsonb_rejects_extra_field() -> None:
    now = _now()
    with pytest.raises(ValidationError):
        parse_config_jsonb({"unknown_field": 1}, tenant_id=1, created_at=now, updated_at=now)


def test_parse_config_jsonb_none_input_treated_as_empty() -> None:
    now = _now()
    tc = parse_config_jsonb(None, tenant_id=1, created_at=now, updated_at=now)
    assert tc.tenant_id == 1
    assert tc.maturity_age_days is None
