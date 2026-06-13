"""Shared fixtures for unit tests that exercise app/rules.yaml end-to-end.

Several rule-test modules (trust-conditioned through IP-familiarity)
each need to load the production rules.yaml,
find a rule by name, and exercise it with a controlled neutral ctx
dict. These helpers live here so the same pattern doesn't get
duplicated across six files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.context import BOOKING_PATH_MODIFICATION_DEFAULTS
from app.rules import ALLOWED_CONTEXT_FIELDS, Rule, RuleSet, load_rules

_RULES_YAML = Path(__file__).resolve().parents[2] / "app" / "rules.yaml"


@pytest.fixture(scope="module")
def ruleset() -> RuleSet:
    """Loads the production app/rules.yaml once per test module."""
    return load_rules(_RULES_YAML)


def find_rule(ruleset: RuleSet, name: str) -> Rule:
    """Return the rule with the given name; raise AssertionError otherwise."""
    for r in ruleset.rules:
        if r.name == name:
            return r
    msg = f"rule {name!r} not found in ruleset"
    raise AssertionError(msg)


def base_ctx() -> dict[str, Any]:
    """Neutral ctx with every whitelisted field populated. Tests
    override specific keys to exercise their target rule. Drift guard
    fails fast if ALLOWED_CONTEXT_FIELDS grows without this fixture
    being updated.
    """
    ctx: dict[str, Any] = {
        # numerics default to non-firing values
        "shipment_value": 100.0,
        "booking_hour_utc": 12,
        "booking_weekday": 2,
        "customer_observations": 100.0,
        "account_age_days": 365,
        "total_shipments": 100,
        "flagged_count": 0,
        "fraud_confirmed_count": 0,
        "trust_score": 1.0,
        "ip_threat_score": 0.0,
        "ip_distance_km": 0.0,
        "velocity_user_hourly": 0,
        "velocity_user_daily": 0,
        "velocity_user_30d": 0,
        "velocity_ip_hourly": 0,
        "velocity_ip_daily": 0,
        "customer_distinct_ips_30d": 0,
        "recipient_cross_customer_count": 0,
        "value_zscore": 0.0,
        "cadence_zscore_hours": 0.0,
        "days_since_last_booking": 0,
        # strings
        "ip_country": "US",
        "ip_familiarity_tier": "familiar",
        # booleans default to False (non-firing)
        "is_api_booking": False,
        "is_platform_booking": True,
        "is_cloud_ip": False,
        "is_datacenter_ip": False,
        "is_vpn": False,
        "is_tor": False,
        "is_proxy": False,
        "ip_in_level1": False,
        "ip_in_level2": False,
        "ip_in_threat_list": False,
        "ip_country_changed": False,
        "ip2p_threat_botnet": False,
        "ip2p_threat_scanner": False,
        "ip2p_threat_spam": False,
        "ip2p_threat_any": False,
        "is_residential_asn": False,
        "is_new_ip": False,
        "ip_new_known_asn": False,
        "ip_fully_new": False,
        "ip_family_familiar": True,
        "is_new_route": False,
        "origin_address_familiar": True,
        "destination_address_familiar": True,
        "origin_ip_country_familiar": True,
        "is_abnormally_dormant": False,
        "customer_locked_cloud_api": False,
        "customer_locked_web_only": False,
        "is_new_user": False,
        "impossible_travel": False,
        "is_email_disposable": False,
        "is_email_blocklisted": False,
        "is_email_suspicious_pattern": False,
        "is_phone_dummy_pattern": False,
        # Previously-rejected — neutral defaults; tests targeting
        # the previously-rejected rules override explicitly.
        "email_previously_rejected": False,
        "phone_previously_rejected": False,
        "origin_previously_rejected": False,
        "ip_previously_rejected": False,
        # Currency-normalized thresholds — USD-default values matching
        # DEFAULT_VALUE_CAPS. Tests for the rewritten rules override
        # these to exercise per-currency behavior.
        "shipment_currency": "USD",
        "shipment_value_threshold_high": 10000.0,
        "shipment_value_threshold_new_user": 5000.0,
        "shipment_value_threshold_medium": 2000.0,
        "shipment_value_threshold_low": 1000.0,
        # case-3a signals. Defaults are neutral (False) so
        # rules predating these signals behave identically. case-3a rule
        # tests override origin_via_carrier_dropoff +
        # shipment_route_unfamiliar_for_customer explicitly.
        "origin_via_carrier_dropoff": False,
        "shipment_route_unfamiliar_for_customer": False,
        # case-3b signals. Defaults are None / False;
        # case-3b rule tests override explicitly. The default
        # registered_country=None + mismatch=False neutralizes the
        # case-3b compound shape in tests that aren't targeting it.
        "customer_registered_country": None,
        # case-3b asymmetric signal (replaces the deleted
        # symmetric triangle-mismatch). Default False; the
        # cold_start_outbound_carrier_dropoff rule tests override.
        "customer_destination_country_mismatch_outbound": False,
        # case-2 learning-based ASN deviation. Default
        # False; the api_booking_from_unfamiliar_asn rule tests
        # override explicitly.
        "unfamiliar_asn_for_customer": False,
        # case-3b sophisticated signal. Default False so
        # rules predating this signal behave identically; case-3b
        # sophisticated rule tests override explicitly.
        "shipment_route_rare_for_tenant": False,
        # Modification — neutral defaults imported from
        # app.context.BOOKING_PATH_MODIFICATION_DEFAULTS so production and
        # tests cannot drift. modification_type "none" matches no enum
        # value, so the modification rules don't trip in
        # non-modification tests. Tests targeting modification rules
        # override these explicitly.
        **BOOKING_PATH_MODIFICATION_DEFAULTS,
    }
    missing = ALLOWED_CONTEXT_FIELDS - set(ctx.keys())
    assert not missing, f"base_ctx missing fields: {missing}"
    return ctx
