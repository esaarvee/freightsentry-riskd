"""POST /api/v1/shipments/feedback — Phase 3B endpoint.

Two-tier idempotency:

1. **Per-POST dedup**: UNIQUE (tenant_id, request_id) on the feedback
   table; the endpoint's initial SELECT short-circuits a network-retry
   POST replay by returning the prior outcome verbatim (applied=False).

2. **Label-monotonicity dedup**: a new POST with a *different* request_id
   but the *same* target_request_id applies only if the new label is
   stronger than the prior. Strength order:
   `approved (0) < rejected (1) < fraud_confirmed (2)`.

Customer counter deltas are computed by `_compute_counter_deltas` so the
transition matrix is exhaustively testable. The endpoint persists in
a single transaction: feedback INSERT + baseline UPDATE (FOR UPDATE
lock) + customer counter UPDATE. Mirrors booking discipline.

Phase 7C.11 — deferred-observation fold on `approved` feedback. When
the booking endpoint declines to fold a REVIEW/BLOCK booking into the
customer baseline (baseline gated on ALLOW per the case-2 baseline-
pollution finding), a later `approved` feedback against that booking
triggers the fold here. The fold uses enrichment data FRESH at
feedback time — not booking time — because the booking-time snapshot
is not persisted. For stable IP→ASN/country attribution, this is
acceptable on the operator-feedback timescale (30-180 day typical
operator lag). Stronger-guarantee alternatives (persisted booking-
time enrichment snapshot in the decisions row) are deferred design.

Idempotency on the fold path is carried by the existing label-
monotonicity gate: a second `approved` against the same target is
not stronger than the first; the gate short-circuits before the
fold ever runs. An ALLOW-band booking followed by `approved`
feedback is also short-circuited (the new branch checks the prior
decision band and skips the fold when it was already ALLOW).
"""

from __future__ import annotations

import json
from datetime import datetime
from ipaddress import IPv4Address
from typing import Annotated, Any, cast

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthContext, require_api_token
from app.baseline import CustomerBaseline, classify_ip_type
from app.db import get_conn, set_tenant_id
from app.enrich import Enricher
from app.models import Address, FeedbackLabel, FeedbackRequest, FeedbackResponse
from app.runtime import get_enricher
from app.signal_helpers import netblock_24
from app.tenant_config_cache import load_tenant_config_cached

_log = structlog.get_logger(__name__)

router = APIRouter()


# Label-strength ladder. Pure dict — no dependency on the FeedbackLabel
# enum's ordering (Literal has no ordinal semantic).
_LABEL_RANK: dict[str, int] = {"approved": 0, "rejected": 1, "fraud_confirmed": 2}
_REJECTED_SET: frozenset[str] = frozenset({"rejected", "fraud_confirmed"})


def _label_stronger(*, new: str, prior: str | None) -> bool:
    """True if `new` should overwrite `prior` per monotonicity rules.

    First-ever feedback (prior=None) always applies. Equal labels do
    NOT apply (no-op) — the caller can detect via the returned
    applied=False + previous_label match. Downgrades are blocked.
    """
    if prior is None:
        return True
    return _LABEL_RANK[new] > _LABEL_RANK[prior]


def _compute_counter_deltas(prior_label: str | None, new_label: str) -> tuple[int, int]:
    """Return (flag_delta, fraud_delta) for transitioning from
    `prior_label` to `new_label` under label monotonicity.

    Upstream `_label_stronger` ensures `new_label` is "stronger" than
    `prior_label` before this helper runs; this helper only computes the
    counter deltas (not the monotonicity check itself).

    Concrete transitions (exhaustively tested in
    tests/unit/test_feedback_counter_transitions.py):
    | prior            | new              | flag | fraud |
    | None             | approved         | 0    | 0     |
    | None             | rejected         | +1   | 0     |
    | None             | fraud_confirmed  | +1   | +1    |
    | approved         | rejected         | +1   | 0     |
    | approved         | fraud_confirmed  | +1   | +1    |
    | rejected         | fraud_confirmed  | 0    | +1    |
    (Same-label no-ops are caught upstream by _label_stronger.)
    """
    prior_flagged = prior_label in _REJECTED_SET if prior_label else False
    new_flagged = new_label in _REJECTED_SET
    flag_delta = int(new_flagged) - int(prior_flagged)

    prior_fraud = prior_label == "fraud_confirmed"
    new_fraud = new_label == "fraud_confirmed"
    fraud_delta = int(new_fraud) - int(prior_fraud)

    return flag_delta, fraud_delta


def _address_from_jsonb(value: Any) -> str:
    """Extract the plaintext address string from a shipments jsonb column.

    Booking endpoint stores Address.model_dump() into the JSONB column
    (see app/api/booking.py:184), so the canonical shape is
    {"address": str, "city": str | None, ...}. Returns the plaintext
    address used as the key in baseline.origin_stats / dest_stats.
    """
    if isinstance(value, str):
        value = json.loads(value)
    return str(value["address"])


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    payload: FeedbackRequest,
    auth: Annotated[AuthContext, Depends(require_api_token)],
    enricher: Annotated[Enricher, Depends(get_enricher)],
) -> FeedbackResponse:
    async with get_conn() as conn, conn.transaction():
        await set_tenant_id(conn, auth.tenant_id)

        # Per-request fresh load — kept here for shape consistency with
        # booking/modification endpoints. No feedback-path consumer in
        # 4A; 4B+ may consult tenant_config.allowed_currencies if feedback
        # semantics extend to per-currency thresholds. The underscore
        # prefix marks the binding as intentionally unused for now.
        _tenant_config = await load_tenant_config_cached(conn, auth.tenant_id)

        # Tier 1: per-POST idempotency — replay of the same request_id
        # returns the prior outcome without re-applying. Network-retry-safe.
        existing = await conn.fetchrow(
            """
            SELECT label, target_request_id
              FROM feedback
             WHERE tenant_id = $1 AND request_id = $2
            """,
            auth.tenant_id,
            payload.request_id,
        )
        if existing is not None:
            _log.info(
                "feedback.idempotent_replay",
                metric=True,
                tenant_id=auth.tenant_id,
                request_id=payload.request_id,
            )
            # CHECK ck_feedback_label guarantees the stored label is in
            # the FeedbackLabel enum; cast is safe by construction.
            return FeedbackResponse(
                applied=False,
                previous_label=cast(FeedbackLabel, existing["label"]),
                target_request_id=existing["target_request_id"],
            )

        # Resolve target_request_id -> prior decision + shipment + customer.
        # Dual tenant_id filters on every JOIN leg per .ai/conventions.md.
        #
        # Phase 7C.11 — additional columns (`d.decision`, `s.value`,
        # `s.channel`, `s.booking_ts`) needed by the fold-on-approved
        # path. Cheap column-level additions; no schema change.
        prior = await conn.fetchrow(
            """
            SELECT
                d.id            AS decision_id,
                d.decision      AS decision_band,
                s.id            AS shipment_id,
                s.customer_id   AS customer_id,
                s.source_ip     AS source_ip,
                s.origin        AS origin,
                s.destination   AS destination,
                s.value         AS shipment_value,
                s.channel       AS shipment_channel,
                s.booking_ts    AS booking_ts,
                s.email_hmac    AS email_hmac,
                s.phone_hmac    AS phone_hmac
              FROM decisions d
              JOIN shipments s ON s.id = d.shipment_id AND s.tenant_id = d.tenant_id
             WHERE d.tenant_id = $1
               AND d.request_id = $2
            """,
            auth.tenant_id,
            payload.target_request_id,
        )
        if prior is None:
            raise HTTPException(
                status_code=404,
                detail="target_request_id not found for this tenant",
            )

        # Tier 2: label-monotonicity. Find the strongest prior label
        # already applied against this target (across all feedback rows
        # for this target — different request_ids may carry successive
        # upgrades).
        prior_label_row = await conn.fetchrow(
            """
            SELECT label
              FROM feedback
             WHERE tenant_id = $1 AND target_request_id = $2
             ORDER BY feedback_ts DESC, created_at DESC
             LIMIT 1
            """,
            auth.tenant_id,
            payload.target_request_id,
        )
        prior_label: str | None = prior_label_row["label"] if prior_label_row is not None else None

        if not _label_stronger(new=payload.label, prior=prior_label):
            # Audit-trail: still INSERT the feedback row so the operator
            # action is recorded, but DO NOT apply baseline / counter
            # writes. The persisted row makes it queryable that
            # someone tried a no-op or downgrade.
            try:
                await _insert_feedback_row(conn, auth.tenant_id, payload)
            except asyncpg.UniqueViolationError as exc:
                raise HTTPException(
                    status_code=409,
                    detail="request_id already used for another feedback in this tenant",
                ) from exc
            _log.info(
                "feedback.monotonicity_skip",
                metric=True,
                tenant_id=auth.tenant_id,
                request_id=payload.request_id,
                new_label=payload.label,
                prior_label=prior_label,
            )
            return FeedbackResponse(
                applied=False,
                previous_label=cast(FeedbackLabel | None, prior_label),
                target_request_id=payload.target_request_id,
            )

        # Apply: baseline writes (rejected/fraud_confirmed only) +
        # customer counter delta + audit-trail INSERT, all in one
        # transaction.
        if payload.label in _REJECTED_SET:
            # FOR UPDATE on customer_baselines serialises concurrent
            # feedback POSTs for the same customer.
            baseline = await CustomerBaseline.load(
                conn, auth.tenant_id, prior["customer_id"], for_update=True
            )
            # Re-run the tier-2 monotonicity SELECT AFTER acquiring the
            # FOR UPDATE lock. Mirrors the pre-lock SELECT above (~30
            # lines earlier); rerun under lock for race correctness.
            # The earlier (pre-lock) read may have missed a concurrent
            # commit; without this re-read two parallel
            # rejected/fraud_confirmed POSTs for the same target could
            # both see prior=None and both apply, double-incrementing
            # the counter. The lock guarantees the second transaction
            # blocks until the first commits, at which point this
            # re-read returns the now-committed label.
            prior_label_row = await conn.fetchrow(
                """
                SELECT label
                  FROM feedback
                 WHERE tenant_id = $1 AND target_request_id = $2
                 ORDER BY feedback_ts DESC, created_at DESC
                 LIMIT 1
                """,
                auth.tenant_id,
                payload.target_request_id,
            )
            prior_label = prior_label_row["label"] if prior_label_row is not None else None
            if not _label_stronger(new=payload.label, prior=prior_label):
                # Concurrent winner committed a stronger-or-equal label;
                # our application is now a no-op. Still INSERT the audit
                # row (operator-action visibility).
                try:
                    await _insert_feedback_row(conn, auth.tenant_id, payload)
                except asyncpg.UniqueViolationError as exc:
                    raise HTTPException(
                        status_code=409,
                        detail="request_id already used for another feedback in this tenant",
                    ) from exc
                _log.info(
                    "feedback.monotonicity_skip_post_lock",
                    metric=True,
                    tenant_id=auth.tenant_id,
                    request_id=payload.request_id,
                    new_label=payload.label,
                    prior_label=prior_label,
                )
                return FeedbackResponse(
                    applied=False,
                    previous_label=cast(FeedbackLabel | None, prior_label),
                    target_request_id=payload.target_request_id,
                )
            dimensions_written = _apply_baseline_writes(
                baseline=baseline,
                prior=prior,
                ts=payload.feedback_ts,
            )
            await baseline.save(conn)
        elif payload.label == "approved" and prior["decision_band"] != "ALLOW":
            # Phase 7C.11 — fold-deferred-observation on positive
            # feedback. The booking endpoint deferred this observation
            # (decision was REVIEW or BLOCK; baseline gated on ALLOW).
            # Operator has now confirmed the booking as legitimate;
            # fold the deferred observation into the customer baseline
            # so subsequent bookings reflect the confirmed history.
            #
            # Idempotency: a second `approved` against the same target
            # is not stronger than the first (rank ordering: approved
            # = 0); the label-monotonicity gate above short-circuits
            # before this branch ever runs again. An ALLOW-band
            # booking followed by `approved` feedback also short-
            # circuits here (the elif's `decision_band != "ALLOW"`
            # check is the second guard).
            #
            # Concurrency: mirrors the rejected branch's post-FOR-
            # UPDATE-lock monotonicity re-read. Without it, two
            # concurrent `approved` POSTs with different request_ids
            # targeting the same target could both pass the pre-lock
            # monotonicity SELECT (prior=None for both), then
            # serialize on the FOR UPDATE lock — the second commit
            # would double-fold the baseline. The re-read under lock
            # catches the now-committed first `approved` and skips
            # the duplicate fold.
            #
            # Enrichment data is fresh at feedback time (cache hit on
            # ip_enrichment row written during booking-time evaluation;
            # MaxMind re-lookup only on cache miss). For stable
            # IP→ASN/country attribution this is acceptable on the
            # operator-feedback timescale.
            #
            # Decay vs observation timestamp: `decay_to(feedback_ts)`
            # advances the baseline's decay anchor to feedback time so
            # subsequent reads are aligned with current time. The
            # observation's `ts=booking_ts` records the historical
            # event date so per-stat `last` markers honestly reflect
            # when the activity occurred. Welford cadence: when
            # booking_ts < current last_booking_ts (out-of-order fold),
            # `_welford_cadence` short-circuits the negative-hours
            # case at `add_observation`'s if-hours-positive guard, so
            # the fold contributes no cadence sample (acceptable).
            #
            # email_domain side-effect drop: the booking-time
            # add_observation populates email_domain_stats from a
            # derived value not stored on the shipments row. The
            # fold path passes only email_hmac/phone_hmac (the
            # HMAC'd dimensions that ARE on the shipments row);
            # email_domain_stats is not bumped by the fold. Minor
            # accuracy loss; documented for future audit.
            baseline = await CustomerBaseline.load(
                conn, auth.tenant_id, prior["customer_id"], for_update=True
            )
            # Post-lock monotonicity re-read — concurrency defense.
            prior_label_row = await conn.fetchrow(
                """
                SELECT label
                  FROM feedback
                 WHERE tenant_id = $1 AND target_request_id = $2
                 ORDER BY feedback_ts DESC, created_at DESC
                 LIMIT 1
                """,
                auth.tenant_id,
                payload.target_request_id,
            )
            prior_label_under_lock = (
                prior_label_row["label"] if prior_label_row is not None else None
            )
            if not _label_stronger(new=payload.label, prior=prior_label_under_lock):
                # Concurrent winner committed first; skip the fold.
                # Audit row still gets INSERT'd below.
                try:
                    await _insert_feedback_row(conn, auth.tenant_id, payload)
                except asyncpg.UniqueViolationError as exc:
                    raise HTTPException(
                        status_code=409,
                        detail="request_id already used for another feedback in this tenant",
                    ) from exc
                _log.info(
                    "feedback.fold_monotonicity_skip_post_lock",
                    metric=True,
                    tenant_id=auth.tenant_id,
                    request_id=payload.request_id,
                    new_label=payload.label,
                    prior_label=prior_label_under_lock,
                )
                return FeedbackResponse(
                    applied=False,
                    previous_label=cast(FeedbackLabel | None, prior_label_under_lock),
                    target_request_id=payload.target_request_id,
                )

            source_ip = IPv4Address(str(prior["source_ip"]))
            enrichment = await enricher.enrich(conn, source_ip)
            origin_address = Address.model_validate(
                json.loads(prior["origin"]) if isinstance(prior["origin"], str) else prior["origin"]
            )
            destination_address = Address.model_validate(
                json.loads(prior["destination"])
                if isinstance(prior["destination"], str)
                else prior["destination"]
            )
            baseline.decay_to(payload.feedback_ts.date())
            # Preserve current last_booking_* before add_observation
            # writes them. Out-of-order fold (booking_ts older than
            # current last_booking_ts) would otherwise REGRESS the
            # customer's last-known activity timestamp + location to
            # historical values, silently breaking cadence z-score,
            # recency rules, and last-known-location signals on
            # subsequent bookings. We restore the preserved values
            # post-add when the historical fold is older.
            preserved_last_ts = baseline.last_booking_ts
            preserved_last_lat = baseline.last_booking_lat
            preserved_last_lon = baseline.last_booking_lon
            preserved_last_country = baseline.last_booking_country
            baseline.add_observation(
                ts=prior["booking_ts"],
                ip=str(source_ip),
                ip_type=classify_ip_type(enrichment),
                ip_netblock=netblock_24(str(source_ip)),
                ip_asn=enrichment.asn_org,
                ip_country=enrichment.country,
                ip_lat=enrichment.lat,
                ip_lon=enrichment.lon,
                origin=origin_address.address,
                destination=destination_address.address,
                channel=prior["shipment_channel"],
                value=float(prior["shipment_value"]),
                shipment_origin_country=origin_address.country,
                shipment_destination_country=destination_address.country,
                email_hmac=prior["email_hmac"],
                phone_hmac=prior["phone_hmac"],
            )
            if preserved_last_ts is not None and prior["booking_ts"] < preserved_last_ts:
                # Out-of-order fold detected — restore the more-recent
                # last_booking_* values that add_observation just
                # overwrote.
                baseline.last_booking_ts = preserved_last_ts
                baseline.last_booking_lat = preserved_last_lat
                baseline.last_booking_lon = preserved_last_lon
                baseline.last_booking_country = preserved_last_country
            await baseline.save(conn)
            # Enumerate dimensions actually touched by the fold so
            # structured-log consumers see a comparable shape to the
            # rejected path's dimension list.
            dimensions_written = [
                "ip_stats",
                "ip_netblock_stats",
                "ip_asn_stats",
                "origin_stats",
                "dest_stats",
                "lane_stats",
                "value",
                "channel_hist",
                "country_route_stats",
            ]
        else:
            dimensions_written = []

        flag_delta, fraud_delta = _compute_counter_deltas(
            prior_label=prior_label, new_label=payload.label
        )
        if flag_delta != 0 or fraud_delta != 0:
            await conn.execute(
                """
                UPDATE customers
                   SET flagged_count = flagged_count + $1,
                       fraud_confirmed_count = fraud_confirmed_count + $2
                 WHERE id = $3 AND tenant_id = $4
                """,
                flag_delta,
                fraud_delta,
                prior["customer_id"],
                auth.tenant_id,
            )

        try:
            await _insert_feedback_row(conn, auth.tenant_id, payload)
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(
                status_code=409,
                detail="request_id already used for another feedback in this tenant",
            ) from exc

    _log.info(
        "feedback.applied",
        metric=True,
        tenant_id=auth.tenant_id,
        request_id=payload.request_id,
        target_request_id=payload.target_request_id,
        label=payload.label,
        previous_label=prior_label,
        flag_delta=flag_delta,
        fraud_delta=fraud_delta,
        dimensions_written=dimensions_written,
    )
    return FeedbackResponse(
        applied=True,
        previous_label=cast(FeedbackLabel | None, prior_label),
        target_request_id=payload.target_request_id,
    )


def _apply_baseline_writes(
    *,
    baseline: CustomerBaseline,
    prior: asyncpg.Record,
    ts: datetime,
) -> list[str]:
    """Apply per-dimension r_n increments for the rejection. Returns the
    list of dimensions actually written (NULL HMACs are skipped — pre-3B.3
    shipments rows lack email_hmac/phone_hmac, so the email/phone
    dimensions don't contribute for those targets).
    """
    written: list[str] = []

    # IP — always present (source_ip is NOT NULL on shipments).
    baseline.add_rejected_observation(key_in=str(prior["source_ip"]), stat="ip_stats", ts=ts)
    written.append("ip_stats")

    # Origin + destination — plaintext addresses (NOT HMAC) per booking
    # endpoint's add_observation pattern at app/api/booking.py:154-155.
    origin_addr = _address_from_jsonb(prior["origin"])
    baseline.add_rejected_observation(key_in=origin_addr, stat="origin_stats", ts=ts)
    written.append("origin_stats")

    dest_addr = _address_from_jsonb(prior["destination"])
    baseline.add_rejected_observation(key_in=dest_addr, stat="dest_stats", ts=ts)
    written.append("dest_stats")

    # Email/phone HMAC — pre-3B.3 shipments rows have NULL; skip if so.
    # The structured-log dimensions_written reports the actual list.
    if prior["email_hmac"] is not None:
        baseline.add_rejected_observation(
            key_in=prior["email_hmac"], stat="rejected_email_hmacs", ts=ts
        )
        written.append("rejected_email_hmacs")
    if prior["phone_hmac"] is not None:
        baseline.add_rejected_observation(
            key_in=prior["phone_hmac"], stat="rejected_phone_hmacs", ts=ts
        )
        written.append("rejected_phone_hmacs")

    return written


async def _insert_feedback_row(
    conn: asyncpg.Connection,
    tenant_id: int,
    payload: FeedbackRequest,
) -> None:
    """INSERT the feedback audit row. The pure-bootstrap feedback schema
    (per 3B.1 drop-and-recreate) has no decision_id column; the prior
    decision is re-resolvable via the decisions.request_id lookup at
    read time if needed.
    """
    await conn.execute(
        """
        INSERT INTO feedback (
            tenant_id, request_id, target_request_id, label,
            feedback_ts, note, operator_id
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        tenant_id,
        payload.request_id,
        payload.target_request_id,
        payload.label,
        payload.feedback_ts,
        payload.note,
        payload.operator_id,
    )


# Re-export for tests that need to inject custom labels without
# importing from app.models directly.
__all__ = [
    "FeedbackLabel",
    "_compute_counter_deltas",
    "_label_stronger",
    "router",
]
