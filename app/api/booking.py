"""POST /api/v1/shipments/booking/evaluate — stub.

Phase 1 returns ALLOW 0.0 for every well-formed payload. Real scoring
wires in 1D.7-1D.8 (Layer 1 hard-block + Layer 3 signal noisy-OR).

Persistence is synchronous within a single transaction (operator
amendment 2026-05-25): SELECT FOR UPDATE on customer_baselines (lands
1D.3 — Phase 1 stub does INSERT shipments + INSERT decisions + UPDATE
customers without the baseline lock yet), then commit. Failures return
500; retries are safe via UNIQUE(tenant_id, request_id) idempotency on
both shipments and decisions.
"""

import json
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends

from app.auth import AuthContext, require_api_token
from app.db import get_conn, set_tenant_id
from app.models import BookingRequest, BookingResponse, RiskFactor
from app.services.entity_upsert import upsert_customer, upsert_user

_log = structlog.get_logger(__name__)

router = APIRouter()


@router.post("/booking/evaluate", response_model=BookingResponse)
async def evaluate_booking(
    payload: BookingRequest,
    auth: Annotated[AuthContext, Depends(require_api_token)],
) -> BookingResponse:
    # Transaction MUST be open before set_tenant_id; SET LOCAL / set_config(...,
    # is_local=true) is transaction-scoped and silently no-ops outside one.
    async with get_conn() as conn, conn.transaction():
        await set_tenant_id(conn, auth.tenant_id)

        # Idempotency: return prior decision if (tenant_id, request_id) exists.
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
                risk_factors=[
                    RiskFactor(**rf) for rf in json.loads(existing["risk_factors"])
                ],
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

        # Phase 1 stub decision — ALLOW 0.0. Real scoring lands 1D.7-1D.8.
        await conn.execute(
            """
            INSERT INTO decisions (
                tenant_id, shipment_id, request_id, score, decision,
                classification, risk_level, triggered_rules, risk_factors
            )
            VALUES (
                $1, $2, $3, 0.0, 'ALLOW', 'GREEN', 'LOW',
                '{}'::text[], '[]'::jsonb
            )
            """,
            auth.tenant_id,
            shipment_id,
            payload.request_id,
        )

        # Update customer counters.
        await conn.execute(
            """
            UPDATE customers
               SET last_seen = now(),
                   total_shipments = total_shipments + 1
             WHERE id = $1
            """,
            customer_id,
        )

    _log.info(
        "booking.evaluated",
        request_id=payload.request_id,
        tenant_id=auth.tenant_id,
        decision="ALLOW",
        score=0.0,
        metric=True,
    )
    return BookingResponse(
        request_id=payload.request_id,
        decision="ALLOW",
        score=0.0,
        classification="GREEN",
        risk_level="LOW",
        triggered_rules=[],
        risk_factors=[],
    )
