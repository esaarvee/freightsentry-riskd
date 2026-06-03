"""Pydantic v2 request/response models for the booking endpoint.

Booking payload schema per .ai/decisions.md § Endpoints. Required fields
listed in the inner models without `= None`; optional fields default to
None so absence is distinguishable from a sentinel (the booking endpoint
upserts customers using COALESCE so a None field leaves the existing DB
value alone).
"""

from datetime import datetime
from decimal import Decimal
from ipaddress import IPv4Address
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str
    city: str | None = None
    # Phase 6A.5: ISO 3166-1 alpha-2 validation when not None. Two-letter
    # uppercase code or None. Eliminates the composite-key collision
    # risk (lane_stats / country_route_stats use "||"-separated composite
    # keys; unbounded country strings could collide via crafted "||"
    # values per 6A.2 security-auditor informational note).
    country: str | None = Field(default=None, min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")
    postal_code: str | None = None


class CustomerData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str
    registered_address: str | None = None
    business_name: str | None = None
    # Phase 6A.5: structured country signal for case-3b detection
    # (cold_start_country_triangle_with_carrier_dropoff). ISO 3166-1
    # alpha-2 uppercase code or None. Platform integration supplies the
    # field on production booking payloads; replay corpora inject ground
    # truth where known (CA for Roulottes Lupien census; null for
    # case-2 and approved corpora). Structured field rejects address-
    # string parsing on purpose — format variation across users / forms /
    # platforms makes parsers silently unreliable (same family of
    # problem that dropped address-string-matching signals earlier).
    registered_country: str | None = Field(
        default=None, min_length=2, max_length=2, pattern=r"^[A-Z]{2}$"
    )
    first_seen_at: datetime | None = None
    is_api_partner: bool | None = None


class EnterpriseData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str


class UserData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str
    first_seen_at: datetime | None = None


class ShipmentData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Address
    destination: Address
    value: Decimal = Field(..., ge=Decimal("0"))
    channel: str
    # Phase 4B: ISO 4217 currency code; defaults to USD so Phase 1-3 payloads
    # are accepted unchanged. Allowed-list check against tenant_config runs
    # at request time in app/api/booking.py (4B.3) — Pydantic enforces shape
    # only.
    currency: str = Field(default="USD", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    # Phase 6A.1: case-3 fraud signal. True when the shipment was dropped at
    # a carrier facility rather than picked up from the origin address. The
    # case-3 attack pattern spoofs the customer's real address as ship-from
    # for credibility but cannot have a carrier pick up there, so the
    # attacker drops at the carrier facility. Defaults False so existing
    # Phase 1-5 payloads are accepted unchanged; platform integration ships
    # the structured signal post-Phase-6 (see docs/production-launch-checklist.md
    # Phase B).
    origin_via_carrier_dropoff: bool = False


class ContactData(BaseModel):
    """PII fields. HMAC at ingress lands 1D.1 (signal_helpers.hmac_hex)."""

    model_config = ConfigDict(extra="forbid")

    origin_email: str | None = None
    origin_phone: str | None = None
    destination_email: str | None = None
    destination_phone: str | None = None


class BookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    customer: CustomerData
    user: UserData
    source_ip: IPv4Address  # v1 is IPv4-only per .ai/decisions.md
    shipment: ShipmentData
    booking_ts: datetime

    enterprise: EnterpriseData | None = None
    contact: ContactData | None = None


class RiskFactor(BaseModel):
    name: str
    description: str
    weight: float


class BookingResponse(BaseModel):
    request_id: str
    decision: Literal["ALLOW", "REVIEW", "BLOCK"]
    score: float = Field(..., ge=0.0, le=1.0)
    classification: Literal["GREEN", "YELLOW", "RED"]
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    triggered_rules: list[str]
    risk_factors: list[RiskFactor]


# =============================================================================
# Modification endpoint (Phase 3A)
# =============================================================================

ModificationType = Literal["destination", "value", "recipient", "service_level", "pickup_time"]


class ModificationUser(BaseModel):
    """Modification-time user — may differ from original booking's user."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(..., min_length=1, max_length=128)


class ModificationRequest(BaseModel):
    """POST /api/v1/shipments/modification/evaluate payload."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(..., min_length=1, max_length=128)
    original_request_id: str = Field(..., min_length=1, max_length=128)
    modification_ts: datetime
    modification_type: ModificationType
    # shape varies by modification_type; validated by build_modification_context (3A.4)
    new_value: dict[str, Any]

    source_ip: IPv4Address | None = None
    user: ModificationUser | None = None
    reason: str | None = Field(None, max_length=512)
    # Phase 4B: applies to the modification evaluation, not the prior
    # shipment. Currency-aware value-tier rules consult this. Defaults to
    # USD for backward compatibility.
    currency: str = Field(default="USD", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")


class ModificationResponse(BaseModel):
    """Same shape as BookingResponse — scoring infrastructure shared."""

    request_id: str
    decision: Literal["ALLOW", "REVIEW", "BLOCK"]
    score: float = Field(..., ge=0.0, le=1.0)
    classification: Literal["GREEN", "YELLOW", "RED"]
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    triggered_rules: list[str]
    risk_factors: list[RiskFactor]


# =============================================================================
# Feedback endpoint (Phase 3B)
# =============================================================================

FeedbackLabel = Literal["approved", "rejected", "fraud_confirmed"]


class FeedbackRequest(BaseModel):
    """POST /api/v1/shipments/feedback payload.

    Two-tier idempotency: hard UNIQUE on (tenant_id, request_id) prevents
    POST-replay double-apply; label-monotonicity on target_request_id
    governs upgrades (approved < rejected < fraud_confirmed). Both tiers
    are enforced by the endpoint, not the model.
    """

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(..., min_length=1, max_length=128)
    target_request_id: str = Field(..., min_length=1, max_length=128)
    label: FeedbackLabel
    feedback_ts: datetime
    note: str | None = Field(None, max_length=2048)
    operator_id: str | None = Field(None, max_length=128)


class FeedbackResponse(BaseModel):
    """POST /api/v1/shipments/feedback response.

    `applied=True` indicates the feedback contributed to baseline writes
    AND/OR customer counter updates. `applied=False` indicates either a
    POST replay (request_id already present) OR a no-op label assertion
    (the new label is not stronger than the prior label per
    monotonicity). `previous_label` is None on first-ever feedback for
    the target.
    """

    applied: bool
    previous_label: FeedbackLabel | None
    target_request_id: str
