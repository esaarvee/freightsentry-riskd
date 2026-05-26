"""POST /api/v1/shipments/booking/evaluate — Phase 1 full pipeline.

Wires build_context → score → single-transaction persist. Real scoring
via Layer 1 + Layer 3 (Layer 2 lands Phase 2). Booking ts drives the
baseline observation timestamp; HMAC at ingress lands here for contact
PII via signal_helpers.hmac_hex.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends

from app.auth import AuthContext, require_api_token
from app.baseline import classify_ip_type
from app.config import Settings, get_settings
from app.context import build_context
from app.db import get_conn, set_tenant_id
from app.enrich import Enricher
from app.models import BookingRequest, BookingResponse, RiskFactor
from app.rules import RuleSet
from app.runtime import get_enricher, get_ruleset
from app.scoring import CustomerState, score
from app.services.entity_upsert import upsert_customer, upsert_user
from app.signal_helpers import email_domain, hmac_hex, netblock_24

_log = structlog.get_logger(__name__)

router = APIRouter()


@router.post("/booking/evaluate", response_model=BookingResponse)
async def evaluate_booking(
    payload: BookingRequest,
    auth: Annotated[AuthContext, Depends(require_api_token)],
    ruleset: Annotated[RuleSet, Depends(get_ruleset)],
    enricher: Annotated[Enricher, Depends(get_enricher)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> BookingResponse:
    # Transaction MUST open before set_tenant_id; set_config(..., is_local=true)
    # is transaction-scoped and silently no-ops outside one.
    async with get_conn() as conn, conn.transaction():
        await set_tenant_id(conn, auth.tenant_id)

        # Idempotency: replay returns prior decision without re-running scoring.
        existing = await conn.fetchrow(
            """
            SELECT decision, score, classification, risk_level,
                   triggered_rules, risk_factors
            FROM decisions
            WHERE tenant_id = $1 AND request_id = $2
            """,
            auth.tenant_id,
            payload.request_id,
        )
        if existing is not None:
            _log.info(
                "booking.idempotent_replay",
                request_id=payload.request_id,
                tenant_id=auth.tenant_id,
                metric=True,
            )
            return BookingResponse(
                request_id=payload.request_id,
                decision=existing["decision"],
                score=float(existing["score"]),
                classification=existing["classification"],
                risk_level=existing["risk_level"],
                triggered_rules=existing["triggered_rules"],
                risk_factors=[RiskFactor(**rf) for rf in json.loads(existing["risk_factors"])],
            )

        # Implicit registration.
        customer_id = await upsert_customer(conn, auth.tenant_id, payload)
        user_id = await upsert_user(
            conn,
            auth.tenant_id,
            customer_id,
            payload.user.external_id,
            payload.user.first_seen_at,
        )

        # Reload customer row post-upsert so build_context sees current
        # first_seen / total_shipments / flag counts. Explicit
        # `tenant_id = $1` is defense-in-depth above RLS (which is
        # dormant under the Phase 1 superuser per .claude/STATUS.md).
        customer_row = await conn.fetchrow(
            "SELECT * FROM customers WHERE id = $1 AND tenant_id = $2",
            customer_id,
            auth.tenant_id,
        )
        if customer_row is None:
            msg = "customer row vanished after upsert — should not happen"
            raise RuntimeError(msg)

        # Build Context (parallel reads + decay + derived flags). Baseline
        # is loaded FOR UPDATE inside this transaction.
        context_env, baseline, enrichment = await build_context(
            conn,
            tenant_id=auth.tenant_id,
            customer_id=customer_id,
            customer_row=customer_row,
            enricher=enricher,
            payload=payload,
        )

        # Score. CustomerState carries the Layer 2 inputs (trust + maturity
        # + flags) typed; build_context() populated these into ctx already.
        customer_state = CustomerState(
            trust_score=context_env["trust_score"],
            account_age_days=context_env["account_age_days"],
            total_shipments=context_env["total_shipments"],
            flagged_count=context_env["flagged_count"],
        )
        result = score(ruleset, context_env, customer_state=customer_state)

        # HMAC PII at ingress (real hmac_hex now that signal_helpers ships).
        # Plaintext does not propagate past this point.
        secret = settings.hmac_secret.encode("utf-8")
        email_hmac = (
            hmac_hex(payload.contact.origin_email, secret)
            if payload.contact and payload.contact.origin_email
            else None
        )
        phone_hmac = (
            hmac_hex(payload.contact.origin_phone, secret)
            if payload.contact and payload.contact.origin_phone
            else None
        )
        email_domain_val = (
            email_domain(payload.contact.origin_email) or None
            if payload.contact and payload.contact.origin_email
            else None
        )

        # Fold THIS booking into the baseline (positive observation).
        baseline.add_observation(
            ts=payload.booking_ts,
            ip=str(payload.source_ip),
            ip_type=classify_ip_type(enrichment),
            ip_netblock=netblock_24(str(payload.source_ip)),
            ip_asn=enrichment.asn_org,
            ip_country=enrichment.country,
            ip_lat=enrichment.lat,
            ip_lon=enrichment.lon,
            origin=payload.shipment.origin.address,
            destination=payload.shipment.destination.address,
            channel=payload.shipment.channel,
            value=float(payload.shipment.value),
            email_hmac=email_hmac,
            phone_hmac=phone_hmac,
            email_domain=email_domain_val,
        )
        await baseline.save(conn)

        # Persist shipment.
        shipment_id = await conn.fetchval(
            """
            INSERT INTO shipments (
                tenant_id, customer_id, user_id, request_id, source_ip,
                origin, destination, value, channel, booking_ts
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6::jsonb, $7::jsonb, $8, $9, $10
            )
            RETURNING id
            """,
            auth.tenant_id,
            customer_id,
            user_id,
            payload.request_id,
            str(payload.source_ip),
            json.dumps(payload.shipment.origin.model_dump()),
            json.dumps(payload.shipment.destination.model_dump()),
            payload.shipment.value,
            payload.shipment.channel,
            payload.booking_ts,
        )

        # Persist decision.
        risk_factor_json = json.dumps([asdict(rf) for rf in result.risk_factors])
        await conn.execute(
            """
            INSERT INTO decisions (
                tenant_id, shipment_id, request_id, score, decision,
                classification, risk_level, triggered_rules, risk_factors
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            """,
            auth.tenant_id,
            shipment_id,
            payload.request_id,
            result.score,
            result.decision,
            result.classification,
            result.risk_level,
            list(result.triggered_rules),
            risk_factor_json,
        )

        # Update customer counters — tenant_id filter as defense-in-depth.
        await conn.execute(
            """
            UPDATE customers
               SET last_seen = now(),
                   total_shipments = total_shipments + 1
             WHERE id = $1 AND tenant_id = $2
            """,
            customer_id,
            auth.tenant_id,
        )

    _log.info(
        "risk.evaluation",
        metric=True,
        tenant_id=auth.tenant_id,
        request_id=payload.request_id,
        decision=result.decision,
        score=result.score,
        account_prior=result.account_prior,
        signal_score=result.signal_score,
        maturity=result.maturity,
        triggered_rules=list(result.triggered_rules),
        trust_score=context_env["trust_score"],
        flagged_count=context_env["flagged_count"],
    )
    return BookingResponse(
        request_id=payload.request_id,
        decision=result.decision,
        score=result.score,
        classification=result.classification,
        risk_level=result.risk_level,
        triggered_rules=list(result.triggered_rules),
        risk_factors=[
            RiskFactor(name=rf.name, description=rf.description, weight=rf.weight)
            for rf in result.risk_factors
        ],
    )
