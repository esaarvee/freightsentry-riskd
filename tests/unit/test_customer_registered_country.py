"""Unit tests for Phase 6A.5 CustomerData.registered_country Pydantic field
+ Address.country ISO 3166-1 alpha-2 validation extension.

Both fields validate as ISO 3166-1 alpha-2 uppercase two-letter codes when
not None. None remains the safe default — corpora without ground-truth
country data submit None and the downstream triangle-mismatch derivation
correctly returns False.

Address.country validation extension (per 6A.2 security-auditor
informational note) eliminates the "||" composite-key collision risk
on country_route_stats / lane_stats.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import Address, CustomerData

# ---------------------------------------------------------------------------
# CustomerData.registered_country
# ---------------------------------------------------------------------------


def test_registered_country_accepts_valid_iso_codes() -> None:
    for code in ("CA", "US", "GB", "DE", "JP", "AU"):
        customer = CustomerData(external_id="c1", registered_country=code)
        assert customer.registered_country == code


def test_registered_country_defaults_to_none() -> None:
    customer = CustomerData(external_id="c1")
    assert customer.registered_country is None


def test_registered_country_accepts_explicit_none() -> None:
    customer = CustomerData(external_id="c1", registered_country=None)
    assert customer.registered_country is None


def test_registered_country_rejects_lowercase() -> None:
    with pytest.raises(ValidationError):
        CustomerData(external_id="c1", registered_country="ca")


def test_registered_country_rejects_mixed_case() -> None:
    with pytest.raises(ValidationError):
        CustomerData(external_id="c1", registered_country="Ca")


def test_registered_country_rejects_three_letter_code() -> None:
    with pytest.raises(ValidationError):
        CustomerData(external_id="c1", registered_country="CAN")


def test_registered_country_rejects_one_letter() -> None:
    with pytest.raises(ValidationError):
        CustomerData(external_id="c1", registered_country="C")


def test_registered_country_rejects_numeric() -> None:
    with pytest.raises(ValidationError):
        CustomerData(external_id="c1", registered_country="12")


def test_registered_country_rejects_pipe_separator_attack() -> None:
    """A "||"-containing value would collide with the country_route_stats /
    lane_stats composite-key separator. ISO validation forbids it."""
    for malicious in ("||", "C|", "|C", "C||X"):
        with pytest.raises(ValidationError):
            CustomerData(external_id="c1", registered_country=malicious)


def test_registered_country_round_trip_via_model_dump() -> None:
    customer = CustomerData(external_id="c1", registered_country="CA")
    dumped = customer.model_dump()
    assert dumped["registered_country"] == "CA"
    rebuilt = CustomerData.model_validate(dumped)
    assert rebuilt.registered_country == "CA"


# ---------------------------------------------------------------------------
# Address.country (validation extension per 6A.2 security-auditor note)
# ---------------------------------------------------------------------------


def test_address_country_accepts_valid_iso_codes() -> None:
    for code in ("CA", "US", "GB", "DE"):
        addr = Address(address="1 Main St", country=code)
        assert addr.country == code


def test_address_country_defaults_to_none() -> None:
    addr = Address(address="1 Main St")
    assert addr.country is None


def test_address_country_rejects_lowercase() -> None:
    with pytest.raises(ValidationError):
        Address(address="1 Main St", country="us")


def test_address_country_rejects_full_name() -> None:
    with pytest.raises(ValidationError):
        Address(address="1 Main St", country="USA")


def test_address_country_rejects_pipe_separator_attack() -> None:
    """Eliminates composite-key collision risk on country_route_stats /
    lane_stats which use "||"-separated keys."""
    for malicious in ("||", "U|", "|S"):
        with pytest.raises(ValidationError):
            Address(address="1 Main St", country=malicious)
