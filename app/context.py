"""build_context — per-request orchestration.

Loads baseline (FOR UPDATE) + enrichment + 7 velocity counts via
sequential awaits on the txn connection (asyncpg does not multiplex
operations on a single connection — see in-body comment for the
constraint), applies lazy decay to the baseline, computes derived
flags, populates the Context dict. The Context is a plain
`dict[str, Any]` — the DSL evaluator reads it via name lookup.

Phase 2B adds 11 fields (customer_locked_*, days_since_last_booking,
ip_familiarity_tier exposure, impossible_travel, recipient_cross_
customer_count, customer_distinct_ips_30d, etc.). The recipient
cross-customer count requires destination_hmac as a build_context
parameter — caller computes it via signal_helpers.hmac_hex.

Caller is the booking endpoint inside its single transaction. The
baseline row-lock acquired here holds across the subsequent
`baseline.add_observation` + `baseline.save` + the shipment / decision
INSERTs (per operator amendment 2026-05-25).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from ipaddress import IPv4Address
from typing import Any, Final

import asyncpg

from app.baseline import CustomerBaseline
from app.enrich import Enricher, EnrichmentRow
from app.models import (
    Address,
    BookingRequest,
    ContactData,
    CustomerData,
    ModificationRequest,
    ShipmentData,
    UserData,
)
from app.signal_helpers import (
    composite_threat_score,
    haversine_km,
    hmac_hex,
    is_email_blocklisted,
    is_email_disposable,
    is_email_suspicious_pattern,
    is_phone_dummy_pattern,
    netblock_24,
)
from app.tenant_config import TenantConfig, resolve_value_caps
from app.tenant_route_baselines import derive_route_rarity
from app.trust import compute_trust_score
from app.velocity import (
    count_ip_daily,
    count_ip_hourly,
    count_recipient_distinct_customers_30d,
    count_user_30d,
    count_user_daily,
    count_user_distinct_ips_30d,
    count_user_hourly,
    count_user_modifications_1h,
    count_user_modifications_24h,
)

# Phase 6A.2 — case-3a route-unfamiliar derivation parameters.
# Top-N covers ≥80% of historical observations; signal fires when the
# current (origin_country, destination_country) pair is NOT in that
# top-N prefix. Maturity gate (customer_observations >= 10) ensures the
# signal does not fire on cold-start customers whose baseline has no
# meaningful route history.
_ROUTE_UNFAMILIAR_COVERAGE_THRESHOLD = 0.80
_ROUTE_UNFAMILIAR_MATURITY_THRESHOLD = 10.0


def _derive_route_unfamiliar(
    country_route_stats: dict[str, dict[str, Any]],
    current_origin_country: str | None,
    current_destination_country: str | None,
    customer_observations: float,
) -> bool:
    """Return True iff the current country-pair is NOT in the customer's
    top-N route prefix covering ≥80% of historical observations.

    Safety properties:
    - Cold-start customers (customer_observations < 10): always False.
      The maturity gate prevents the signal from firing before the
      customer has accumulated meaningful history.
    - Missing country data: False (no signal without ground-truth data).
    - Empty histogram (mature customer with no recorded routes — unusual
      but possible if all prior bookings lacked structured country):
      False. Cold-start safe.
    """
    if customer_observations < _ROUTE_UNFAMILIAR_MATURITY_THRESHOLD:
        return False
    if not current_origin_country or not current_destination_country:
        return False
    if not country_route_stats:
        return False

    current_key = f"{current_origin_country}||{current_destination_country}"
    pairs = sorted(
        ((k, float(v.get("n", 0.0))) for k, v in country_route_stats.items()),
        key=lambda kv: kv[1],
        reverse=True,
    )
    total = sum(count for _, count in pairs)
    if total <= 0:
        return False
    coverage_target = total * _ROUTE_UNFAMILIAR_COVERAGE_THRESHOLD
    cumulative = 0.0
    top_n_keys: set[str] = set()
    for key, count in pairs:
        top_n_keys.add(key)
        cumulative += count
        if cumulative >= coverage_target:
            break
    return current_key not in top_n_keys


def _outbound_destination_mismatch(
    customer_country: str | None,
    destination_country: str | None,
) -> bool:
    """Phase 7C.2 case-3b asymmetric mismatch derivation.

    Returns True iff both inputs are truthy (non-None, non-empty)
    AND differ. The Roulottes Lupien attack shape is customer ships
    outside their declared country in the DESTINATION only (origin
    can match customer country). The symmetric triangle-mismatch
    (deleted in 7C.3) required customer_country to differ from BOTH
    origin and destination — too narrow for the empirical attack.

    Null/empty handling: returns False when either input is None or
    empty string. Customers without a declared registered country
    (tier-4 fallback in the freight_risk export) and shipments
    without a structured destination country cannot trigger this
    rule by accident. The empty-string special-case is defensive:
    Pydantic enforces the 2-letter regex at ingress so empty string
    can't reach the derivation through normal booking flow, but
    treating it as no-signal symmetric with None eliminates a class
    of "what if the model loosens" questions.

    Pure boolean; no I/O, no exceptions.
    """
    if not customer_country or not destination_country:
        return False
    return customer_country != destination_country


async def build_context(
    conn: asyncpg.Connection,
    *,
    tenant_id: int,
    customer_id: int,
    customer_row: asyncpg.Record,
    enricher: Enricher,
    payload: BookingRequest,
    destination_hmac: str,
    tenant_config: TenantConfig,
    email_hmac: str | None = None,
    phone_hmac: str | None = None,
    as_of: date | None = None,
) -> tuple[dict[str, Any], CustomerBaseline, EnrichmentRow]:
    """Returns (context_env, baseline, enrichment).

    The caller commits writes (baseline.save, shipment/decision insert,
    customer update) inside the same transaction as the baseline
    FOR UPDATE lock acquired here.

    `tenant_config` populates 5 currency-derived ctx fields in 4B.4
    (shipment_currency + 4 tier thresholds). 4C cold-start enforcement
    consults it inside `score()`.
    """
    today = as_of or date.today()
    source_ip = IPv4Address(str(payload.source_ip))

    # Sequential awaits on the txn connection — asyncpg does not
    # multiplex operations on a single connection. Parallelism via
    # acquiring multiple pool connections is possible but would require
    # the velocity counts to run on a different connection than the
    # baseline-FOR-UPDATE lock holds; the simpler sequential pattern
    # fits inside the 30-50ms context-load budget for Phase 1
    # cardinality. Phase 5 load test revisits if needed.
    baseline = await CustomerBaseline.load(conn, tenant_id, customer_id, for_update=True)
    enrichment = await enricher.enrich(conn, source_ip)
    vel_uh = await count_user_hourly(conn, tenant_id, customer_id)
    vel_ud = await count_user_daily(conn, tenant_id, customer_id)
    vel_u30 = await count_user_30d(conn, tenant_id, customer_id)
    vel_ih = await count_ip_hourly(conn, tenant_id, source_ip)
    vel_id = await count_ip_daily(conn, tenant_id, source_ip)
    vel_distinct_ips = await count_user_distinct_ips_30d(conn, tenant_id, customer_id)
    vel_recipient = await count_recipient_distinct_customers_30d(conn, tenant_id, destination_hmac)
    # Phase 6A.8 — case-3b tenant population baseline rarity. The DB
    # query is bounded by the composite PK's leading-column prefix
    # scan; adds ~1ms p95 per booking. Cold-start tenants (<100
    # observations) return False without firing the rule.
    route_rare = await derive_route_rarity(
        conn,
        tenant_id,
        payload.customer.registered_country,
        payload.shipment.origin.country,
        payload.shipment.destination.country,
    )

    baseline.decay_to(today)

    origin = payload.shipment.origin.address
    destination = payload.shipment.destination.address
    # Phase 6A.2 — shipment country intermediates (Pydantic Address.country
    # structured-field passthrough). Used by route-unfamiliar derivation
    # below. Intentionally NOT added to ctx — never referenced by rule
    # conditions directly. The triangle-mismatch derivation (Phase 6A.5)
    # also reads from these intermediates.
    shipment_origin_country = payload.shipment.origin.country
    shipment_destination_country = payload.shipment.destination.country
    netblock = netblock_24(str(source_ip))
    familiarity = baseline.ip_familiarity_tier(str(source_ip), netblock, enrichment.asn_org)
    age_days = (today - customer_row["first_seen"].date()).days

    cadence_zscore = 0.0
    if baseline.last_booking_ts is not None:
        hours_since = (payload.booking_ts - baseline.last_booking_ts).total_seconds() / 3600.0
        cadence_zscore = baseline.cadence_zscore_hours(hours_since)

    # Phase 2B derivations
    days_since_last = baseline.days_since_last_booking(payload.booking_ts)
    ip_distance_km = haversine_km(
        enrichment.lat,
        enrichment.lon,
        baseline.last_booking_lat,
        baseline.last_booking_lon,
    )
    impossible_travel = (
        baseline.last_booking_ts is not None and days_since_last == 0 and ip_distance_km > 500.0
    )
    customer_locked_cloud_api = (
        baseline.cloud_share > 0.95 and baseline.api_share > 0.95 and baseline.value_n >= 20.0
    )
    customer_locked_web_only = (1.0 - baseline.api_share) > 0.95 and baseline.value_n >= 20.0
    is_residential_asn = (
        not enrichment.is_cloud and not enrichment.is_datacenter and enrichment.asn_org is not None
    )

    ctx: dict[str, Any] = {
        # Request
        "shipment_value": float(payload.shipment.value),
        "is_api_booking": payload.shipment.channel == "api",
        "is_platform_booking": payload.shipment.channel != "api",
        "booking_hour_utc": payload.booking_ts.hour,
        "booking_weekday": payload.booking_ts.weekday(),
        # Customer + maturity
        "customer_observations": baseline.effective_observations,
        "account_age_days": age_days,
        "total_shipments": int(customer_row["total_shipments"]),
        "flagged_count": int(customer_row["flagged_count"]),
        "fraud_confirmed_count": int(customer_row["fraud_confirmed_count"]),
        "trust_score": compute_trust_score(
            account_age_days=age_days,
            effective_observations=baseline.effective_observations,
            flagged_count=int(customer_row["flagged_count"]),
            fraud_confirmed_count=int(customer_row["fraud_confirmed_count"]),
        ),
        # IP enrichment
        "is_cloud_ip": enrichment.is_cloud,
        "is_datacenter_ip": enrichment.is_datacenter,
        "is_vpn": enrichment.is_vpn,
        "is_tor": enrichment.is_tor,
        "is_proxy": enrichment.is_proxy,
        "ip_in_level1": enrichment.fh_level1,
        "ip_in_level2": enrichment.fh_level2,
        "ip_in_threat_list": enrichment.fh_level1 or enrichment.fh_level2,
        "ip_threat_score": composite_threat_score(
            fh_level1=enrichment.fh_level1,
            fh_level2=enrichment.fh_level2,
            ip2p_threat=enrichment.threat,
        ),
        "ip_country": enrichment.country or "",
        "ip_distance_km": ip_distance_km,
        "ip_country_changed": (
            enrichment.country is not None
            and baseline.last_booking_country is not None
            and enrichment.country != baseline.last_booking_country
        ),
        "ip2p_threat_botnet": "BOTNET" in (enrichment.threat or ""),
        "ip2p_threat_scanner": "SCANNER" in (enrichment.threat or ""),
        "ip2p_threat_spam": "SPAM" in (enrichment.threat or ""),
        "ip2p_threat_any": bool(enrichment.threat),
        "is_residential_asn": is_residential_asn,
        # Familiarity
        "ip_familiarity_tier": familiarity,
        "is_new_ip": familiarity in ("new_known_asn", "fully_new"),
        "ip_new_known_asn": familiarity == "new_known_asn",
        "ip_fully_new": familiarity == "fully_new",
        "ip_family_familiar": familiarity == "family_familiar",
        "is_new_route": f"{origin}||{destination}" not in baseline.lane_stats,
        "origin_address_familiar": origin in baseline.origin_stats,
        "destination_address_familiar": destination in baseline.dest_stats,
        "origin_ip_country_familiar": (
            f"{origin}||{enrichment.country or ''}" in baseline.origin_ip_country_stats
        ),
        # Phase 6A.2 — case-3a signals
        "origin_via_carrier_dropoff": payload.shipment.origin_via_carrier_dropoff,
        "shipment_route_unfamiliar_for_customer": _derive_route_unfamiliar(
            baseline.country_route_stats,
            shipment_origin_country,
            shipment_destination_country,
            baseline.effective_observations,
        ),
        # Phase 6A.5 — case-3b signals (brand-new-customer fraud).
        # customer_registered_country is a structured-field passthrough
        # from payload.customer (ISO 3166-1 alpha-2). Phase 7C.2
        # replaces the symmetric triangle-mismatch (deleted) with an
        # asymmetric outbound-destination check matching the
        # empirically-observed Roulottes Lupien attack shape.
        "customer_registered_country": payload.customer.registered_country,
        "customer_destination_country_mismatch_outbound": _outbound_destination_mismatch(
            payload.customer.registered_country,
            shipment_destination_country,
        ),
        # Phase 6A.8 — tenant population baseline rarity (DB-backed,
        # computed above before baseline.decay_to to keep the awaits
        # grouped). Drives the case-3b sophisticated compound rule.
        "shipment_route_rare_for_tenant": route_rare,
        # Velocity
        "velocity_user_hourly": vel_uh,
        "velocity_user_daily": vel_ud,
        "velocity_user_30d": vel_u30,
        "velocity_ip_hourly": vel_ih,
        "velocity_ip_daily": vel_id,
        "customer_distinct_ips_30d": vel_distinct_ips,
        "recipient_cross_customer_count": vel_recipient,
        # Value + cadence
        "value_zscore": baseline.value_zscore(float(payload.shipment.value)),
        "cadence_zscore_hours": cadence_zscore,
        "is_abnormally_dormant": cadence_zscore > 6.0,  # tuned per verification §2.2
        # Phase 2B Layer-2 / 2C-rule inputs
        "customer_locked_cloud_api": customer_locked_cloud_api,
        "customer_locked_web_only": customer_locked_web_only,
        "days_since_last_booking": days_since_last,
        "is_new_user": baseline.value_n < 5.0,
        "impossible_travel": impossible_travel,
        # Email / phone classifiers (OR across origin + destination)
        "is_email_disposable": _any_email_match(payload.contact, is_email_disposable),
        "is_email_blocklisted": _any_email_match(payload.contact, is_email_blocklisted),
        "is_email_suspicious_pattern": _any_email_match(
            payload.contact, is_email_suspicious_pattern
        ),
        "is_phone_dummy_pattern": _any_phone_match(payload.contact, is_phone_dummy_pattern),
        # Previously rejected (3B) — pure dict lookups against the
        # already-loaded baseline; no additional SQL. Rules in 3B.5
        # consume these. email/phone fire only when the CURRENT request
        # supplies an email/phone whose HMAC matches a prior rejection
        # for this customer; origin/ip fire when r_n > 0 for the
        # current request's plaintext-keyed dimension.
        "email_previously_rejected": (
            email_hmac is not None and email_hmac in baseline.rejected_email_hmacs
        ),
        "phone_previously_rejected": (
            phone_hmac is not None and phone_hmac in baseline.rejected_phone_hmacs
        ),
        "origin_previously_rejected": (
            float(baseline.origin_stats.get(origin, {}).get("r_n", 0.0)) > 0.0
        ),
        "ip_previously_rejected": (
            float(baseline.ip_stats.get(str(source_ip), {}).get("r_n", 0.0)) > 0.0
        ),
        # Modification fields — neutral defaults from the module constant
        # so build_context (booking path) and base_ctx (tests) cannot
        # drift. See BOOKING_PATH_MODIFICATION_DEFAULTS for the rationale.
        **BOOKING_PATH_MODIFICATION_DEFAULTS,
    }

    # Phase 4B.4: 5 currency-normalized threshold fields. payload.shipment.
    # currency defaults to "USD" per BookingRequest.ShipmentData (4B.1)
    # for payload-shape backward-compat with Phase 1-3 requests.
    # resolve_value_caps returns the 4-tier dict, falling back to
    # DEFAULT_VALUE_CAPS["CAD"] (with warning) if the tenant hasn't
    # configured the requested currency — Phase 6B switched the project
    # default key from USD to CAD. Allowed-list check ran at the
    # endpoint layer (4B.3) — currency reaching here is always permitted.
    currency = payload.shipment.currency
    caps = resolve_value_caps(tenant_config, currency)
    ctx["shipment_currency"] = currency
    ctx["shipment_value_threshold_high"] = caps["high"]
    ctx["shipment_value_threshold_new_user"] = caps["new_user"]
    ctx["shipment_value_threshold_medium"] = caps["medium"]
    ctx["shipment_value_threshold_low"] = caps["low"]

    return ctx, baseline, enrichment


# =============================================================================
# Modification endpoint context (Phase 3A)
# =============================================================================

MODIFICATION_TIME_BUCKETS: Final[tuple[tuple[timedelta, str], ...]] = (
    (timedelta(minutes=30), "within_30_min"),
    (timedelta(hours=1), "within_1_hour"),
    (timedelta(hours=24), "within_24_hours"),
    (timedelta(days=7), "1_to_7_days"),
)


# Booking-path defaults for the 6 modification Context fields. The DSL
# evaluator requires every referenced field to be populated at
# evaluation time (NameError otherwise), so build_context must supply
# these on the booking path even though no modification rule should
# fire. The "none" sentinel for modification_type matches none of the
# enum literals the modification rules condition on
# (destination | value | recipient | service_level | pickup_time), so
# the rules are structurally dormant on bookings.
#
# Single source of truth: tests/unit/conftest.py base_ctx imports this
# constant so test fixtures cannot drift from production defaults
# (per Phase 2 false-pass-test lesson).
BOOKING_PATH_MODIFICATION_DEFAULTS: Final[dict[str, Any]] = {
    "modification_time_since_booking": "over_7_days",
    "modification_magnitude": 0.0,
    "modification_direction": "unknown",
    "modification_velocity_1h": 0,
    "modification_velocity_24h": 0,
    "modification_type": "none",
}


def _modification_time_bucket(*, booking_ts: datetime, modification_ts: datetime) -> str:
    """Bucket the delta between original booking and modification timestamps.

    Negative delta (modification_ts earlier than booking_ts) is anomalous
    and treated as the most suspicious bucket (within_30_min) so rules
    that condition on a tight window catch it. Same-side TZ convention as
    the production code path (both timestamps come from the DB / payload
    as timezone-aware datetimes).
    """
    delta = modification_ts - booking_ts
    if delta < timedelta(0):
        return "within_30_min"
    for threshold, label in MODIFICATION_TIME_BUCKETS:
        if delta <= threshold:
            return label
    return "over_7_days"


def _modification_magnitude(
    *,
    modification_type: str,
    new_value: dict[str, Any],
    prior_shipment: asyncpg.Record,
    hmac_secret: bytes,
) -> float:
    """Per-type magnitude:

    - value: fractional change |new - old| / old; 0.0 if old <= 0
    - destination: 1.0 if destination HMAC changes, 0.0 otherwise
    - other types (recipient / service_level / pickup_time): 1.0 if the
      payload supplies any value (semantically "a change was requested"),
      0.0 only on an empty new_value dict.
    """
    if modification_type == "value":
        try:
            old = float(prior_shipment["value"])
        except (TypeError, ValueError):
            return 0.0
        if old <= 0:
            return 0.0
        try:
            new_raw = float(new_value.get("value", old))
        except (TypeError, ValueError):
            # Malformed payload — surface as "no signal" rather than crash.
            # Rules can't condition on a magnitude that wasn't computable.
            return 0.0
        return abs(new_raw - old) / old
    if modification_type == "destination":
        new_addr = new_value.get("destination") or {}
        new_address_str = new_addr.get("address") if isinstance(new_addr, dict) else None
        if not new_address_str:
            return 0.0
        old_hmac = prior_shipment["destination_hmac"]
        new_hmac = hmac_hex(new_address_str, hmac_secret)
        return 1.0 if new_hmac != old_hmac else 0.0
    return 1.0 if new_value else 0.0


def _modification_direction(
    *,
    modification_type: str,
    new_value: dict[str, Any],
    baseline: CustomerBaseline,
) -> str:
    """Categorical direction for destination modifications.

    Returns "familiar" if the new destination address is present in the
    customer's baseline.dest_stats (plaintext keys per
    app/baseline.py::_bump), "unfamiliar" otherwise. For non-destination
    modifications, returns "unknown" — direction is only semantically
    defined for routing-target changes today.

    "blocked" (global blocked vectors) is Phase 6+ and is not produced
    by this commit; the field's Literal type reserves the value for
    future expansion alongside the global_blocked_vectors lookup.
    """
    if modification_type != "destination":
        return "unknown"
    new_addr = new_value.get("destination") or {}
    new_address_str = new_addr.get("address") if isinstance(new_addr, dict) else None
    if not new_address_str:
        return "unknown"
    if new_address_str in baseline.dest_stats:
        return "familiar"
    return "unfamiliar"


def _address_from_jsonb(value: Any) -> Address:
    """Reconstruct an Address Pydantic model from a JSONB column value.

    Mirrors `app/baseline.py::_decode_jsonb` defensive str-or-dict
    handling — asyncpg may or may not decode JSONB depending on codec
    configuration. Keeping the precedent consistent across modules.
    """
    if isinstance(value, str):
        value = json.loads(value)
    return Address.model_validate(value)


def _booking_from_prior_shipment(
    *,
    request_id: str,
    booking_ts: datetime,
    prior_shipment: asyncpg.Record,
    customer_external_id: str,
    user_external_id: str,
    source_ip_override: IPv4Address | None,
    contact: ContactData | None,
) -> BookingRequest:
    """Synthesize a BookingRequest from a stored shipments row + caller-
    resolved customer/user external_ids. Used only as the input to
    build_context for the modification path; not persisted.

    The synthesized booking represents the ORIGINAL booking's context
    (so baseline familiarity / IP enrichment / velocity all reflect the
    customer's actual history), with two overrides allowed:

    - source_ip: if the modification arrived from a different IP, the
      modification's IP is the operational signal (newly observed IP
      can trip rules). When omitted, prior IP is used.
    - contact: not stored on the shipment row, so the caller passes
      None unless it can recover it from elsewhere.
    """
    source_ip = source_ip_override
    if source_ip is None:
        prior_ip = prior_shipment["source_ip"]
        source_ip = IPv4Address(str(prior_ip))

    shipment = ShipmentData(
        origin=_address_from_jsonb(prior_shipment["origin"]),
        destination=_address_from_jsonb(prior_shipment["destination"]),
        value=Decimal(str(prior_shipment["value"])),
        channel=prior_shipment["channel"],
    )
    return BookingRequest(
        request_id=request_id,
        customer=CustomerData(external_id=customer_external_id),
        user=UserData(external_id=user_external_id),
        source_ip=source_ip,
        shipment=shipment,
        booking_ts=booking_ts,
        contact=contact,
    )


async def build_modification_context(
    conn: asyncpg.Connection,
    *,
    tenant_id: int,
    customer_id: int,
    customer_row: asyncpg.Record,
    enricher: Enricher,
    payload: ModificationRequest,
    prior_shipment_row: asyncpg.Record,
    customer_external_id: str,
    user_external_id: str,
    hmac_secret: bytes,
    tenant_config: TenantConfig,
    contact: ContactData | None = None,
    as_of: date | None = None,
) -> tuple[dict[str, Any], CustomerBaseline, EnrichmentRow]:
    """Build the Context dict for a modification evaluation.

    Calls build_context with a synthesized BookingRequest reconstructed
    from the prior shipment row, then layers on the 4 non-SQL
    modification fields (time bucket, magnitude, direction, type).
    Velocity fields (modification_velocity_1h / _24h) are populated with
    placeholder zeroes in this commit — 3A.5 wires the real SQL.

    The caller is responsible for resolving the prior shipment row and
    customer / user external_ids before invocation, and for holding the
    transaction inside which build_context's baseline FOR UPDATE lock
    is acquired.
    """
    synthetic_booking = _booking_from_prior_shipment(
        request_id=payload.request_id,
        booking_ts=prior_shipment_row["booking_ts"],
        prior_shipment=prior_shipment_row,
        customer_external_id=customer_external_id,
        user_external_id=user_external_id,
        source_ip_override=(IPv4Address(str(payload.source_ip)) if payload.source_ip else None),
        contact=contact,
    )
    # 4B.4: override the synthesized booking's currency to reflect the
    # MODIFICATION's currency (not the prior shipment's). Currency-aware
    # value-tier rules at modification time evaluate against the new
    # currency. Pre-4B shipments rows carry no currency; the synthesized
    # booking gets USD via Pydantic default, and this override upgrades
    # it to the modification's chosen currency.
    synthetic_booking = synthetic_booking.model_copy(
        update={
            "shipment": synthetic_booking.shipment.model_copy(
                update={"currency": payload.currency},
            ),
        },
    )
    destination_hmac = prior_shipment_row["destination_hmac"]
    ctx, baseline, enrichment = await build_context(
        conn,
        tenant_id=tenant_id,
        customer_id=customer_id,
        customer_row=customer_row,
        enricher=enricher,
        payload=synthetic_booking,
        destination_hmac=destination_hmac,
        tenant_config=tenant_config,
        as_of=as_of,
    )

    ctx["modification_time_since_booking"] = _modification_time_bucket(
        booking_ts=prior_shipment_row["booking_ts"],
        modification_ts=payload.modification_ts,
    )
    ctx["modification_magnitude"] = _modification_magnitude(
        modification_type=payload.modification_type,
        new_value=payload.new_value,
        prior_shipment=prior_shipment_row,
        hmac_secret=hmac_secret,
    )
    ctx["modification_direction"] = _modification_direction(
        modification_type=payload.modification_type,
        new_value=payload.new_value,
        baseline=baseline,
    )
    ctx["modification_type"] = payload.modification_type
    # Modification path: 9 sequential awaits inherited from build_context +
    # these 2 = 11 total on the same txn connection. Phase 5 load-test
    # revisits separate-pool parallelism if the latency budget tightens.
    ctx["modification_velocity_1h"] = await count_user_modifications_1h(
        conn, tenant_id, customer_id
    )
    ctx["modification_velocity_24h"] = await count_user_modifications_24h(
        conn, tenant_id, customer_id
    )

    return ctx, baseline, enrichment


def _any_email_match(contact: ContactData | None, classifier: Any) -> bool:
    if contact is None:
        return False
    for email in (contact.origin_email, contact.destination_email):
        if email and classifier(email):
            return True
    return False


def _any_phone_match(contact: ContactData | None, classifier: Any) -> bool:
    if contact is None:
        return False
    for phone in (contact.origin_phone, contact.destination_phone):
        if phone and classifier(phone):
            return True
    return False
