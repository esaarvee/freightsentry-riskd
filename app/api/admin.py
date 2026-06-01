"""Phase 4D admin endpoints — read-only.

Both endpoints require admin role (api_tokens.role == 'admin' via
require_admin_role) and are tenant-bounded: an admin sees only their
own tenant's data; cross-tenant lookups return 404 (hides existence
per security-by-default convention).

NO write endpoints in v1. Decision overrides, manual feedback, etc.
deferred to v2+ per .ai/decisions.md § Endpoints.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthContext, require_admin_role
from app.db import get_conn, set_tenant_id
from app.tenant_config import load_tenant_config

_log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

_STAT_DICT_TRUNCATION_LIMIT = 10


def _decode_jsonb(value: Any) -> Any:
    """Defensive JSONB decode — asyncpg returns JSONB as `str` by default in
    this project (no codec registered). The cast-at-boundary pattern from
    Phase 3B handles both str and pre-decoded forms."""
    if isinstance(value, str):
        return json.loads(value)
    return value


def _truncate_stat_dict(raw: Any, *, limit: int = _STAT_DICT_TRUNCATION_LIMIT) -> dict[str, Any]:
    """Return top-N entries by `n` descending, plus a count of total entries.

    Stat-dict shape: `{key: {n, r_n, last, type?}}`. Sorted by `n` desc to
    surface the highest-frequency entries (most operationally interesting).
    """
    decoded = _decode_jsonb(raw)
    if not isinstance(decoded, dict):
        return {"entries": [], "total_count": 0, "truncated": False}
    items = list(decoded.items())
    items.sort(key=lambda kv: float(kv[1].get("n", 0)), reverse=True)
    total = len(items)
    entries = [{"key": k, **v} for k, v in items[:limit]]
    return {"entries": entries, "total_count": total, "truncated": total > limit}


def _truncate_hmac_set(raw: Any, *, limit: int = _STAT_DICT_TRUNCATION_LIMIT) -> dict[str, Any]:
    """Truncate a flat HMAC set/dict (e.g., rejected_email_hmacs).

    Returns up to `limit` HMAC hex strings + a total count. HMACs are
    pre-obfuscated (no PII visible) so direct surfacing is acceptable
    within the tenant-bounded admin scope.
    """
    decoded = _decode_jsonb(raw)
    if isinstance(decoded, dict):
        keys = list(decoded.keys())
    elif isinstance(decoded, list):
        keys = list(decoded)
    else:
        return {"entries": [], "total_count": 0, "truncated": False}
    total = len(keys)
    return {"entries": keys[:limit], "total_count": total, "truncated": total > limit}


@router.get("/decisions/{request_id}")
async def get_admin_decision(
    request_id: str,
    auth: Annotated[AuthContext, Depends(require_admin_role)],
) -> dict[str, Any]:
    """Return full decision detail for the given request_id.

    Tenant-bounded: only this admin's tenant_id is searched. Cross-tenant
    lookups return 404 (not 403 — hides existence).

    PII handling: origin/destination addresses returned as city + country
    (not the full street address).
    """
    async with get_conn() as conn, conn.transaction():
        await set_tenant_id(conn, auth.tenant_id)
        # Load tenant config for shape consistency with other endpoints (4A
        # wiring); admin endpoint doesn't consult config fields.
        _tc = await load_tenant_config(conn, auth.tenant_id)
        _ = _tc

        row = await conn.fetchrow(
            """
            SELECT
                d.request_id,
                d.request_type,
                d.score,
                d.decision,
                d.classification,
                d.risk_level,
                d.triggered_rules,
                d.risk_factors,
                d.created_at AS decision_created_at,
                s.id            AS shipment_id,
                s.source_ip,
                s.origin,
                s.destination,
                s.value,
                s.channel,
                s.booking_ts
              FROM decisions d
              JOIN shipments s ON s.id = d.shipment_id AND s.tenant_id = d.tenant_id
             WHERE d.tenant_id = $1 AND d.request_id = $2
            """,
            auth.tenant_id,
            request_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="decision not found")

    origin = _decode_jsonb(row["origin"]) or {}
    destination = _decode_jsonb(row["destination"]) or {}
    risk_factors_raw = _decode_jsonb(row["risk_factors"]) or []

    _log.info(
        "admin.decision_lookup",
        tenant_id=auth.tenant_id,
        request_id=request_id,
        request_type=row["request_type"],
        metric=True,
    )
    return {
        "request_id": row["request_id"],
        "request_type": row["request_type"],
        "decision": row["decision"],
        "score": float(row["score"]),
        "classification": row["classification"],
        "risk_level": row["risk_level"],
        "triggered_rules": list(row["triggered_rules"]),
        "risk_factors": risk_factors_raw,
        "shipment": {
            "id": row["shipment_id"],
            "source_ip": str(row["source_ip"]),
            "origin_city": origin.get("city"),
            "origin_country": origin.get("country"),
            "destination_city": destination.get("city"),
            "destination_country": destination.get("country"),
            "value": float(row["value"]),
            "channel": row["channel"],
            "booking_ts": row["booking_ts"].isoformat(),
        },
        "created_at": row["decision_created_at"].isoformat(),
    }


@router.get("/customers/{external_id}/baseline")
async def get_admin_customer_baseline(
    external_id: str,
    auth: Annotated[AuthContext, Depends(require_admin_role)],
) -> dict[str, Any]:
    """Return customer record + baseline state (truncated for response size).

    Tenant-bounded. Cross-tenant lookups return 404.

    Stat-dicts truncated to top-10 by `n` desc. Total count + truncated
    flag included per dict. Full dicts available via Phase 5+ separate
    endpoint if needed.

    PII surfaced:
      - business_name, registered_address (admin authorized for their
        tenant's customer data)
      - HMAC hex strings for email/phone (already obfuscated)
    """
    async with get_conn() as conn, conn.transaction():
        await set_tenant_id(conn, auth.tenant_id)
        _tc = await load_tenant_config(conn, auth.tenant_id)
        _ = _tc

        customer_row = await conn.fetchrow(
            """
            SELECT id, external_id, registered_address, business_name,
                   is_api_partner, first_seen, last_seen, flagged_count,
                   fraud_confirmed_count, total_shipments, created_at
              FROM customers
             WHERE tenant_id = $1 AND external_id = $2
            """,
            auth.tenant_id,
            external_id,
        )
        if customer_row is None:
            raise HTTPException(status_code=404, detail="customer not found")

        baseline_row = await conn.fetchrow(
            """
            SELECT
                origin_stats, dest_stats, lane_stats, ip_stats,
                ip_netblock_stats, ip_asn_stats, country_stats,
                origin_ip_country_stats,
                email_hmacs, phone_hmacs,
                rejected_email_hmacs, rejected_phone_hmacs,
                email_domain_stats, phone_prefix_stats,
                ip_type_hist, hour_hist, weekday_hist, channel_hist,
                value_n, value_mean, value_m2,
                cadence_n, cadence_mean_h, cadence_m2_h,
                last_booking_ts, last_booking_country,
                decay_anchor_date, first_seen AS baseline_first_seen,
                last_seen AS baseline_last_seen, updated_at
              FROM customer_baselines
             WHERE tenant_id = $1 AND customer_id = $2
            """,
            auth.tenant_id,
            customer_row["id"],
        )

    _log.info(
        "admin.customer_baseline_lookup",
        tenant_id=auth.tenant_id,
        customer_external_id=external_id,
        baseline_present=baseline_row is not None,
        metric=True,
    )

    response: dict[str, Any] = {
        "customer": {
            "external_id": customer_row["external_id"],
            "registered_address": customer_row["registered_address"],
            "business_name": customer_row["business_name"],
            "is_api_partner": customer_row["is_api_partner"],
            "first_seen": customer_row["first_seen"].isoformat(),
            "last_seen": customer_row["last_seen"].isoformat(),
            "flagged_count": customer_row["flagged_count"],
            "fraud_confirmed_count": customer_row["fraud_confirmed_count"],
            "total_shipments": customer_row["total_shipments"],
            "created_at": customer_row["created_at"].isoformat(),
        },
        "baseline": None,
    }

    if baseline_row is not None:
        response["baseline"] = {
            "origin_stats": _truncate_stat_dict(baseline_row["origin_stats"]),
            "dest_stats": _truncate_stat_dict(baseline_row["dest_stats"]),
            "lane_stats": _truncate_stat_dict(baseline_row["lane_stats"]),
            "ip_stats": _truncate_stat_dict(baseline_row["ip_stats"]),
            "ip_netblock_stats": _truncate_stat_dict(baseline_row["ip_netblock_stats"]),
            "ip_asn_stats": _truncate_stat_dict(baseline_row["ip_asn_stats"]),
            "country_stats": _truncate_stat_dict(baseline_row["country_stats"]),
            "origin_ip_country_stats": _truncate_stat_dict(baseline_row["origin_ip_country_stats"]),
            "email_hmacs": _truncate_hmac_set(baseline_row["email_hmacs"]),
            "phone_hmacs": _truncate_hmac_set(baseline_row["phone_hmacs"]),
            "rejected_email_hmacs": _truncate_hmac_set(baseline_row["rejected_email_hmacs"]),
            "rejected_phone_hmacs": _truncate_hmac_set(baseline_row["rejected_phone_hmacs"]),
            "email_domain_stats": _truncate_stat_dict(baseline_row["email_domain_stats"]),
            "phone_prefix_stats": _truncate_stat_dict(baseline_row["phone_prefix_stats"]),
            "ip_type_hist": _decode_jsonb(baseline_row["ip_type_hist"]) or {},
            "hour_hist": _decode_jsonb(baseline_row["hour_hist"]) or {},
            "weekday_hist": _decode_jsonb(baseline_row["weekday_hist"]) or {},
            "channel_hist": _decode_jsonb(baseline_row["channel_hist"]) or {},
            "value_n": float(baseline_row["value_n"]),
            "value_mean": float(baseline_row["value_mean"]),
            "value_m2": float(baseline_row["value_m2"]),
            "cadence_n": float(baseline_row["cadence_n"]),
            "cadence_mean_h": float(baseline_row["cadence_mean_h"]),
            "cadence_m2_h": float(baseline_row["cadence_m2_h"]),
            "last_booking_ts": (
                baseline_row["last_booking_ts"].isoformat()
                if baseline_row["last_booking_ts"]
                else None
            ),
            "last_booking_country": baseline_row["last_booking_country"],
            "decay_anchor_date": (
                baseline_row["decay_anchor_date"].isoformat()
                if baseline_row["decay_anchor_date"]
                else None
            ),
            "updated_at": baseline_row["updated_at"].isoformat(),
        }

    return response
