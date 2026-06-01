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

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException

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
from app.tenant_config import load_tenant_config

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

        # Per-request fresh load — no caching in Phase 4 (Phase 5 wraps).
        # Sub-millisecond indexed PK lookup; consumers (4B currency
        # validation, 4C cold-start) are downstream.
        tenant_config = await load_tenant_config(conn, auth.tenant_id)

        # 4B.3 request-time currency check. ISO 4217 shape is enforced at the
        # Pydantic layer (4B.1); this is the allowed-list enforcement. 400 is
        # the right code — the request is well-formed but the chosen currency
        # is not in this tenant's allowed list.
        if payload.shipment.currency not in tenant_config.allowed_currencies:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"currency {payload.shipment.currency!r} is not in tenant's "
                    f"allowed list {tenant_config.allowed_currencies}"
                ),
            )

        # Idempotency: replay returns prior decision without re-running
        # scoring. Scoped by request_type='booking' to keep the booking
        # and modification idempotency lookups symmetric — both
        # endpoints filter by their own discriminator (parallel intent,
        # easier to read side-by-side). The ux_decisions_tenant_request
        # constraint enforces that request_id is unique within
        # (tenant_id, request_id) regardless of type, so the filter
        # narrows correctly even when both types theoretically share a
        # namespace.
        existing = await conn.fetchrow(
            """
            SELECT decision, score, classification, risk_level,
                   triggered_rules, risk_factors
            FROM decisions
            WHERE tenant_id = $1
              AND request_id = $2
              AND request_type = 'booking'
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

        # HMAC PII at ingress (real hmac_hex now that signal_helpers ships).
        # Plaintext does not propagate past this point. destination_hmac
        # is computed up-front because build_context() needs it for the
        # recipient cross-customer count query.
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
        destination_hmac = hmac_hex(payload.shipment.destination.address, secret)

        # Build Context (sequential reads + decay + derived flags). Baseline
        # is loaded FOR UPDATE inside this transaction.
        context_env, baseline, enrichment = await build_context(
            conn,
            tenant_id=auth.tenant_id,
            customer_id=customer_id,
            customer_row=customer_row,
            enricher=enricher,
            payload=payload,
            destination_hmac=destination_hmac,
            tenant_config=tenant_config,
            email_hmac=email_hmac,
            phone_hmac=phone_hmac,
        )

        # Score. CustomerState carries the Layer 2 inputs (trust + maturity
        # + flags) typed; build_context() populated these into ctx already.
        customer_state = CustomerState(
            trust_score=context_env["trust_score"],
            account_age_days=context_env["account_age_days"],
            total_shipments=context_env["total_shipments"],
            flagged_count=context_env["flagged_count"],
        )
        result = score(
            ruleset, context_env, customer_state=customer_state, tenant_config=tenant_config
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

        # Persist shipment. email_hmac and phone_hmac (added in 3B.1)
        # land on the shipments row so the 3B.3 feedback endpoint can
        # populate baseline.rejected_email_hmacs / rejected_phone_hmacs
        # per-shipment. NULL when the booking payload supplies no
        # contact email/phone.
        shipment_id = await conn.fetchval(
            """
            INSERT INTO shipments (
                tenant_id, customer_id, user_id, request_id, source_ip,
                origin, destination, value, channel, booking_ts,
                destination_hmac, email_hmac, phone_hmac
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6::jsonb, $7::jsonb, $8, $9, $10,
                $11, $12, $13
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
            destination_hmac,
            email_hmac,
            phone_hmac,
        )

        # Persist decision with explicit request_type='booking' (3A.6
        # makes the discriminator visible at the call site; the 0003
        # migration's DEFAULT 'booking' would cover us if omitted, but
        # explicit literal mirrors the modification endpoint's
        # 'modification' literal and makes intent unambiguous).
        #
        # UniqueViolation handling: see app/api/modification.py for the
        # parallel case. The current flat UNIQUE on
        # (tenant_id, request_id) means a request_id colliding across
        # booking + modification namespaces would 500 without this
        # catch. Phase 5 BUGS.md follow-up widens the UNIQUE to include
        # request_type.
        risk_factor_json = json.dumps([asdict(rf) for rf in result.risk_factors])
        try:
            await conn.execute(
                """
                INSERT INTO decisions (
                    tenant_id, shipment_id, request_id, request_type,
                    score, decision,
                    classification, risk_level, triggered_rules, risk_factors
                )
                VALUES ($1, $2, $3, 'booking', $4, $5, $6, $7, $8, $9::jsonb)
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
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "request_id already used by another decision in this tenant "
                    "(booking-modification namespace collision)"
                ),
            ) from exc

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
