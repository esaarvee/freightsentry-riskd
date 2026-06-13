"""Unit tests for DEFAULT_VALUE_CAPS + resolve_value_caps.

DEFAULT_VALUE_CAPS was re-keyed from "USD" to "CAD" with same
numeric thresholds. The fallback path now returns
DEFAULT_VALUE_CAPS["CAD"]. USD-explicit value_caps overrides still
work end-to-end; multi-currency tenants are still supported.

Tests covering:
- None value_caps + CAD currency → DEFAULT_VALUE_CAPS["CAD"]
- None value_caps + non-CAD currency → CAD fallback + warning
- Custom value_caps + matching currency → custom values
- Custom CAD-only value_caps + CAD → custom values
- Custom USD-only value_caps + USD → custom values (multi-currency support)
- Multi-currency value_caps + missing currency → CAD fallback + warning
- DEFAULT_VALUE_CAPS["CAD"] has all 4 tier keys
- DEFAULT_VALUE_CAPS["CAD"] values match the legacy thresholds
- Returned dict identity (not deep-copied; callers must not mutate)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from app.tenant_config import (
    DEFAULT_VALUE_CAPS,
    TenantConfig,
    resolve_value_caps,
)


def _tc(value_caps: dict[str, dict[str, float]] | None = None, tenant_id: int = 1) -> TenantConfig:
    now = datetime.now(UTC)
    return TenantConfig(
        tenant_id=tenant_id,
        value_caps=value_caps,
        created_at=now,
        updated_at=now,
    )


def test_none_value_caps_cad_returns_default() -> None:
    # value_caps=None is technically the FALLBACK path (the warning is
    # expected); this test only verifies the resolved dict.
    result = resolve_value_caps(_tc(value_caps=None), "CAD")
    assert result == {"high": 10000.0, "new_user": 5000.0, "medium": 2000.0, "low": 1000.0}


def test_empty_value_caps_dict_falls_back() -> None:
    # An operator could store `value_caps: {}` in JSONB (passes the validator
    # because the dict is empty). Falsy short-circuit in resolve_value_caps
    # means this path behaves like None — fall back to CAD-default with warning.
    with patch("app.tenant_config._log") as mock_log:
        result = resolve_value_caps(_tc(value_caps={}, tenant_id=99), "CAD")
    assert result == DEFAULT_VALUE_CAPS["CAD"]
    mock_log.warning.assert_called_once_with(
        "tenant_config.value_caps.fallback",
        tenant_id=99,
        currency="CAD",
        metric=True,
    )


def test_none_value_caps_usd_falls_back_to_cad_with_warning() -> None:
    # structlog doesn't route through stdlib by default, so we patch the
    # bound logger directly and assert the warning was emitted with the
    # tenant_id, currency, and metric=True tag for EMF.
    # USD now triggers the fallback (re-keyed USD → CAD).
    with patch("app.tenant_config._log") as mock_log:
        result = resolve_value_caps(_tc(value_caps=None, tenant_id=42), "USD")
    assert result == DEFAULT_VALUE_CAPS["CAD"]
    mock_log.warning.assert_called_once_with(
        "tenant_config.value_caps.fallback",
        tenant_id=42,
        currency="USD",
        metric=True,
    )


def test_multi_currency_value_caps_missing_currency_falls_back_with_warning() -> None:
    custom = {
        "USD": {"high": 1.0, "new_user": 2.0, "medium": 3.0, "low": 4.0},
        "CAD": {"high": 5.0, "new_user": 6.0, "medium": 7.0, "low": 8.0},
    }
    with patch("app.tenant_config._log") as mock_log:
        result = resolve_value_caps(_tc(value_caps=custom, tenant_id=7), "EUR")
    assert result == DEFAULT_VALUE_CAPS["CAD"]
    mock_log.warning.assert_called_once_with(
        "tenant_config.value_caps.fallback",
        tenant_id=7,
        currency="EUR",
        metric=True,
    )


def test_custom_value_caps_matching_currency_returns_custom_no_warning() -> None:
    # Happy path: value_caps is populated AND currency is in it. The helper
    # must NOT emit a fallback warning. Guards against a regression where the
    # helper accidentally logs on every call (which would flood EMF).
    custom_cad = {"high": 12500.0, "new_user": 6250.0, "medium": 2500.0, "low": 1250.0}
    with patch("app.tenant_config._log") as mock_log:
        result = resolve_value_caps(_tc(value_caps={"CAD": custom_cad}), "CAD")
    assert result == custom_cad
    mock_log.warning.assert_not_called()


def test_custom_usd_value_caps_returns_custom_not_default() -> None:
    """USD-explicit value_caps continues to work end-to-end (multi-currency
    support preserved after the CAD-default switch)."""
    custom_usd = {"high": 99999.0, "new_user": 50000.0, "medium": 20000.0, "low": 10000.0}
    result = resolve_value_caps(_tc(value_caps={"USD": custom_usd}), "USD")
    assert result == custom_usd


def test_default_value_caps_cad_has_all_four_tiers() -> None:
    assert set(DEFAULT_VALUE_CAPS["CAD"].keys()) == {"high", "new_user", "medium", "low"}


def test_default_value_caps_match_current_thresholds() -> None:
    # These literals must match the 7 currency-implicit rules in app/rules.yaml
    # (currency thresholds). The rewritten rules consult these values;
    # re-keyed USD → CAD; numeric thresholds unchanged.
    assert DEFAULT_VALUE_CAPS["CAD"]["high"] == 10000.0
    assert DEFAULT_VALUE_CAPS["CAD"]["new_user"] == 5000.0
    assert DEFAULT_VALUE_CAPS["CAD"]["medium"] == 2000.0
    assert DEFAULT_VALUE_CAPS["CAD"]["low"] == 1000.0


def test_returned_dict_for_default_is_default_reference() -> None:
    # The helper returns DEFAULT_VALUE_CAPS["CAD"] directly on fallback.
    # Callers MUST NOT mutate. Consumers (context derivations)
    # only read the dict.
    result = resolve_value_caps(_tc(value_caps=None), "CAD")
    assert result is DEFAULT_VALUE_CAPS["CAD"]
