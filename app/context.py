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

from datetime import date
from ipaddress import IPv4Address
from typing import Any

import asyncpg

from app.baseline import CustomerBaseline
from app.enrich import Enricher, EnrichmentRow
from app.models import BookingRequest, ContactData
from app.signal_helpers import (
    composite_threat_score,
    haversine_km,
    is_email_blocklisted,
    is_email_disposable,
    is_email_suspicious_pattern,
    is_phone_dummy_pattern,
    netblock_24,
)
from app.trust import compute_trust_score
from app.velocity import (
    count_ip_daily,
    count_ip_hourly,
    count_recipient_distinct_customers_30d,
    count_user_30d,
    count_user_daily,
    count_user_distinct_ips_30d,
    count_user_hourly,
)


async def build_context(
    conn: asyncpg.Connection,
    *,
    tenant_id: int,
    customer_id: int,
    customer_row: asyncpg.Record,
    enricher: Enricher,
    payload: BookingRequest,
    destination_hmac: str,
    as_of: date | None = None,
) -> tuple[dict[str, Any], CustomerBaseline, EnrichmentRow]:
    """Returns (context_env, baseline, enrichment).

    The caller commits writes (baseline.save, shipment/decision insert,
    customer update) inside the same transaction as the baseline
    FOR UPDATE lock acquired here.
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

    baseline.decay_to(today)

    origin = payload.shipment.origin.address
    destination = payload.shipment.destination.address
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
    }

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
