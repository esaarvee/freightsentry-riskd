"""POST /api/v1/shipments/modification/evaluate — Phase 3A endpoint.

Mirrors the booking endpoint's single-transaction discipline (txn opens
before set_tenant_id, idempotency check on (tenant_id, request_id),
SELECT FOR UPDATE on customer baseline via build_modification_context,
persist decision with request_type='modification', return ALLOW |
REVIEW | BLOCK).

Differences from booking:
- Resolves the prior booking via decisions WHERE request_id = original_request_id
  AND request_type = 'booking'; returns 404 if not found, 422 if the
  prior is itself a modification (modify-of-modification is out-of-scope).
- Does NOT INSERT a new shipments row — the modification references
  the prior booking's shipment_id via FK.
- Does NOT increment customers.total_shipments (no new shipment was created).
- Does NOT mutate baseline (the modification is an evaluation, not an
  observation; baseline writes belong on the booking path or via
  feedback). The FOR UPDATE lock is held nonetheless to serialise with
  concurrent booking/feedback writes for the same customer.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Annotated

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthContext, require_api_token
from app.config import Settings, get_settings
from app.context import build_modification_context
from app.db import get_conn, set_tenant_id
from app.enrich import Enricher
from app.models import ModificationRequest, ModificationResponse, RiskFactor
from app.rules import RuleSet
from app.runtime import get_enricher, get_ruleset
from app.scoring import CustomerState, score
from app.tenant_config import load_tenant_config

_log = structlog.get_logger(__name__)

router = APIRouter()


@router.post("/modification/evaluate", response_model=ModificationResponse)
async def evaluate_modification(
    payload: ModificationRequest,
    auth: Annotated[AuthContext, Depends(require_api_token)],
    ruleset: Annotated[RuleSet, Depends(get_ruleset)],
    enricher: Annotated[Enricher, Depends(get_enricher)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ModificationResponse:
    async with get_conn() as conn, conn.transaction():
        await set_tenant_id(conn, auth.tenant_id)

        # Per-request fresh load — no caching in Phase 4 (Phase 5 wraps).
        tenant_config = await load_tenant_config(conn, auth.tenant_id)

        # 4B.3 request-time currency check. Modification carries its own
        # `currency` (defaults to USD); the modification's currency may differ
        # from the prior shipment's, which is fine — value-tier rules at the
        # modification evaluation use the modification's currency.
        if payload.currency not in tenant_config.allowed_currencies:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"currency {payload.currency!r} is not in tenant's "
                    f"allowed list {tenant_config.allowed_currencies}"
                ),
            )

        # First-tier idempotency: replay of this modification's request_id
        # returns the prior decision without re-scoring. Scoped to
        # request_type='modification' so a booking that happens to share
        # the request_id namespace doesn't masquerade as a modification.
        existing = await conn.fetchrow(
            """
            SELECT decision, score, classification, risk_level,
                   triggered_rules, risk_factors
              FROM decisions
             WHERE tenant_id = $1
               AND request_id = $2
               AND request_type = 'modification'
            """,
            auth.tenant_id,
            payload.request_id,
        )
        if existing is not None:
            _log.info(
                "modification.idempotent_replay",
                request_id=payload.request_id,
                tenant_id=auth.tenant_id,
                metric=True,
            )
            return ModificationResponse(
                request_id=payload.request_id,
                decision=existing["decision"],
                score=float(existing["score"]),
                classification=existing["classification"],
                risk_level=existing["risk_level"],
                triggered_rules=existing["triggered_rules"],
                risk_factors=[RiskFactor(**rf) for rf in json.loads(existing["risk_factors"])],
            )

        # Resolve the prior decision + shipment + customer via the
        # original_request_id. JOIN constraints carry explicit
        # tenant_id on every leg per .ai/conventions.md
        # (defense-in-depth above RLS).
        prior = await conn.fetchrow(
            """
            SELECT
                d.id            AS decision_id,
                d.request_type  AS prior_request_type,
                s.id            AS shipment_id,
                s.customer_id   AS customer_id,
                s.user_id       AS user_id,
                s.source_ip     AS source_ip,
                s.origin        AS origin,
                s.destination   AS destination,
                s.destination_hmac AS destination_hmac,
                s.value         AS value,
                s.channel       AS channel,
                s.booking_ts    AS booking_ts,
                c.external_id   AS customer_external_id,
                u.external_id   AS user_external_id
              FROM decisions d
              JOIN shipments s ON s.id = d.shipment_id AND s.tenant_id = d.tenant_id
              JOIN customers c ON c.id = s.customer_id AND c.tenant_id = s.tenant_id
              JOIN users     u ON u.id = s.user_id     AND u.tenant_id = s.tenant_id
             WHERE d.tenant_id = $1
               AND d.request_id = $2
            """,
            auth.tenant_id,
            payload.original_request_id,
        )
        if prior is None:
            raise HTTPException(
                status_code=404,
                detail="Original booking not found for the given original_request_id",
            )
        if prior["prior_request_type"] != "booking":
            # Modification of a non-booking decision is out-of-scope per
            # Phase 3 (today only "modification" can land here, but the
            # message includes the actual value to remain accurate if
            # future request_types are added).
            raise HTTPException(
                status_code=422,
                detail=(
                    "Cannot modify a non-booking decision; "
                    f"original_request_id resolves to request_type={prior['prior_request_type']!r}"
                ),
            )

        # Load the prior customer row for trust + maturity calculation.
        # FK on shipments.customer_id → customers.id guarantees this row
        # exists; an assert documents the structural invariant.
        customer_row = await conn.fetchrow(
            "SELECT * FROM customers WHERE id = $1 AND tenant_id = $2",
            prior["customer_id"],
            auth.tenant_id,
        )
        assert customer_row is not None, (
            "Customer row missing despite FK from shipments.customer_id — "
            "indicates schema corruption or RLS misconfiguration"
        )

        secret = settings.hmac_secret.encode("utf-8")

        # Build modification Context (synthesizes a BookingRequest from
        # the prior shipment + layered modification fields; SELECT FOR
        # UPDATE on customer_baselines acquired inside).
        context_env, _baseline, _enrichment = await build_modification_context(
            conn,
            tenant_id=auth.tenant_id,
            customer_id=prior["customer_id"],
            customer_row=customer_row,
            enricher=enricher,
            payload=payload,
            prior_shipment_row=prior,
            customer_external_id=prior["customer_external_id"],
            user_external_id=prior["user_external_id"],
            hmac_secret=secret,
            tenant_config=tenant_config,
            contact=None,
        )

        customer_state = CustomerState(
            trust_score=context_env["trust_score"],
            account_age_days=context_env["account_age_days"],
            total_shipments=context_env["total_shipments"],
            flagged_count=context_env["flagged_count"],
        )
        result = score(ruleset, context_env, customer_state=customer_state)

        # Persist decision with request_type='modification' against the
        # prior shipment_id (no new shipments row created).
        #
        # The ux_decisions_tenant_request UNIQUE constraint is flat
        # (tenant_id, request_id) regardless of request_type, while the
        # idempotency SELECT above scopes by request_type='modification'.
        # If a tenant submits a modification whose request_id was
        # previously used as a BOOKING's request_id, the idempotency
        # SELECT returns no row (no matching modification), scoring runs,
        # and the INSERT fails on the flat UNIQUE. Catching the
        # UniqueViolation and returning 409 is more useful than an
        # unhandled 500. Phase 5 follow-up (BUGS.md): widen the UNIQUE
        # to (tenant_id, request_id, request_type) so the namespaces
        # are genuinely separate at the DB layer.
        risk_factor_json = json.dumps([asdict(rf) for rf in result.risk_factors])
        try:
            await conn.execute(
                """
                INSERT INTO decisions (
                    tenant_id, shipment_id, request_id, request_type,
                    score, decision,
                    classification, risk_level, triggered_rules, risk_factors
                )
                VALUES ($1, $2, $3, 'modification', $4, $5, $6, $7, $8, $9::jsonb)
                """,
                auth.tenant_id,
                prior["shipment_id"],
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

    _log.info(
        "modification.evaluation",
        metric=True,
        tenant_id=auth.tenant_id,
        request_id=payload.request_id,
        original_request_id=payload.original_request_id,
        modification_type=payload.modification_type,
        decision=result.decision,
        score=result.score,
        account_prior=result.account_prior,
        signal_score=result.signal_score,
        maturity=result.maturity,
        triggered_rules=list(result.triggered_rules),
        modification_velocity_1h=context_env["modification_velocity_1h"],
        modification_velocity_24h=context_env["modification_velocity_24h"],
    )
    return ModificationResponse(
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
