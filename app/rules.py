"""Rule-set loader + Context-field whitelist.

`load_rules(path)` reads a YAML rule definition, parses every condition
via app.dsl, validates that every Name token resolves to a known
Context field (fail-fast at app lifespan startup, not at request time),
and returns an immutable `RuleSet`.

`ALLOWED_CONTEXT_FIELDS` is the single source of truth for the rule
DSL vocabulary. Adding a new rule with a new field requires:
  1. Extending this set
  2. Populating the field in `app.context.build_context`
  3. Documenting it in `.ai/rules.md` § DSL Context fields

Phase 1 set only — Phase 2 adds trust-conditional + customer-lock-in
+ recipient-overlap fields. See `.ai/decisions.md` § Rule catalogue
target.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from app.dsl import collect_names, parse_condition

ALLOWED_CONTEXT_FIELDS: frozenset[str] = frozenset(
    {
        # Request fields
        "shipment_value",
        "is_api_booking",
        "is_platform_booking",
        "booking_hour_utc",
        "booking_weekday",
        # Customer + maturity
        "customer_observations",
        "account_age_days",
        "total_shipments",
        "flagged_count",
        "fraud_confirmed_count",
        "trust_score",
        # IP enrichment
        "is_cloud_ip",
        "is_datacenter_ip",
        "is_vpn",
        "is_tor",
        "is_proxy",
        "ip_in_level1",
        "ip_in_level2",
        "ip_in_threat_list",
        "ip_threat_score",
        "ip_country",
        "ip_distance_km",
        "ip_country_changed",
        "ip2p_threat_botnet",
        "ip2p_threat_scanner",
        "ip2p_threat_spam",
        "ip2p_threat_any",
        "is_residential_asn",
        # Familiarity (baseline-derived)
        "ip_familiarity_tier",
        "is_new_ip",
        "ip_new_known_asn",
        "ip_fully_new",
        "ip_family_familiar",
        "is_new_route",
        "origin_address_familiar",
        "destination_address_familiar",
        "origin_ip_country_familiar",
        # Velocity (SQL-backed)
        "velocity_user_hourly",
        "velocity_user_daily",
        "velocity_user_30d",
        "velocity_ip_hourly",
        "velocity_ip_daily",
        "customer_distinct_ips_30d",
        "recipient_cross_customer_count",
        # Value + cadence
        "value_zscore",
        "cadence_zscore_hours",
        "is_abnormally_dormant",
        # Phase 2B Layer-2 / 2C-rule inputs
        "customer_locked_cloud_api",
        "customer_locked_web_only",
        "days_since_last_booking",
        "is_new_user",
        "impossible_travel",
        # Email/phone classifiers
        "is_email_disposable",
        "is_email_blocklisted",
        "is_email_suspicious_pattern",
        "is_phone_dummy_pattern",
        # Modification (3A) — populated by build_modification_context
        "modification_time_since_booking",  # Literal: within_30_min | within_1_hour | within_24_hours | 1_to_7_days | over_7_days
        "modification_magnitude",  # float in [0.0, +inf); fraction for value-type, 0/1 for categorical
        "modification_direction",  # Literal: familiar | unfamiliar | blocked | unknown
        "modification_velocity_1h",  # int — count of this customer's modifications in last 1h
        "modification_velocity_24h",  # int — count of this customer's modifications in last 24h
        "modification_type",  # Literal: destination | value | recipient | service_level | pickup_time. Booking-path default is the "none" sentinel (matches no enum value), keeping modification rules structurally dormant on bookings — see app/context.py BOOKING_PATH_MODIFICATION_DEFAULTS.
        # Previously rejected (3B) — populated by build_context from baseline state
        "email_previously_rejected",  # bool — email_hmac present in baseline.rejected_email_hmacs
        "phone_previously_rejected",  # bool — phone_hmac present in baseline.rejected_phone_hmacs
        "origin_previously_rejected",  # bool — baseline.origin_stats[origin_key].r_n > 0
        "ip_previously_rejected",  # bool — baseline.ip_stats[ip].r_n > 0
        # Currency-normalized thresholds (Phase 4B) — populated by build_context
        # from tenant_config.value_caps via resolve_value_caps. 4B.5 rewrites
        # the 7 currency-implicit rules to consult these instead of literals.
        "shipment_currency",  # str — 3-letter ISO 4217 code from payload.shipment.currency
        "shipment_value_threshold_high",  # float — caps[currency]["high"]
        "shipment_value_threshold_new_user",  # float — caps[currency]["new_user"]
        "shipment_value_threshold_medium",  # float — caps[currency]["medium"]
        "shipment_value_threshold_low",  # float — caps[currency]["low"]
        # Phase 6A.2 — case-3a signals. origin_via_carrier_dropoff is a
        # passthrough from payload.shipment; shipment_route_unfamiliar_for_customer
        # is derived from baseline.country_route_stats via
        # _derive_route_unfamiliar in app/context.py.
        "origin_via_carrier_dropoff",
        "shipment_route_unfamiliar_for_customer",
    }
)


ActionLiteral = Literal["", "BLOCK"]


@dataclass
class Rule:
    name: str
    description: str
    condition: str
    weight: float
    action: ActionLiteral = ""
    maturity_sensitive: bool = False
    evaluator: Callable[[Mapping[str, Any]], bool] = field(default=lambda _ctx: False)

    def evaluate(self, ctx: Mapping[str, Any]) -> bool:
        return self.evaluator(ctx)


@dataclass(frozen=True)
class Thresholds:
    allow_max: float = 0.60
    block_min: float = 0.80


@dataclass(frozen=True)
class RuleSet:
    rules: tuple[Rule, ...]
    thresholds: Thresholds


def load_rules(yaml_path: Path) -> RuleSet:
    """Load + validate. Raises ValueError on any condition referencing an
    unknown Context field (fail-fast at startup). Raises DSLError from
    `parse_condition` on any non-whitelisted AST node."""
    with yaml_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    thresholds_raw = data.get("thresholds", {})
    thresholds = Thresholds(
        allow_max=float(thresholds_raw.get("allow_max", 0.60)),
        block_min=float(thresholds_raw.get("block_min", 0.80)),
    )

    rules_list = data.get("rules", [])
    rules: list[Rule] = []
    for raw in rules_list:
        condition: str = raw["condition"]
        names = collect_names(condition)
        unknown = names - ALLOWED_CONTEXT_FIELDS
        if unknown:
            msg = (
                f"rule {raw['name']!r}: condition references unknown "
                f"Context fields {sorted(unknown)}. Either extend "
                f"ALLOWED_CONTEXT_FIELDS in app/rules.py or fix the rule."
            )
            raise ValueError(msg)

        action_raw = raw.get("action", "")
        if action_raw not in ("", "BLOCK"):
            msg = (
                f"rule {raw['name']!r}: unsupported action {action_raw!r}. "
                "Only 'BLOCK' or absent (score-only) are valid."
            )
            raise ValueError(msg)

        weight = float(raw.get("weight", 0.0))
        if not 0.0 <= weight <= 1.0:
            # Negative weights would invert the noisy-OR contribution
            # (violates the .ai/decisions.md guardrail "no negative-
            # weight rules"); weights > 1 would push score above the
            # band ceiling. Fail fast at lifespan startup.
            msg = f"rule {raw['name']!r}: weight {weight} must be in [0.0, 1.0]"
            raise ValueError(msg)

        evaluator = parse_condition(condition)
        rules.append(
            Rule(
                name=raw["name"],
                description=raw.get("description", ""),
                condition=condition,
                weight=weight,
                action=action_raw,
                maturity_sensitive=bool(raw.get("maturity_sensitive", False)),
                evaluator=evaluator,
            )
        )
    return RuleSet(rules=tuple(rules), thresholds=thresholds)
