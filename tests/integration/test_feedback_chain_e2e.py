"""End-to-end feedback chain integration tests.

The canonical chain: booking -> feedback -> next-booking-triggers-rule.
These tests demonstrate that the previously-rejected rules
actually fire when the feedback endpoint has marked the
relevant dimensions, with the build_context derivations carrying
the signal into the next evaluation.

The feedback-endpoint integration tests cover the endpoint contract
surfaces; this file covers the END-TO-END activation chain across two POSTs.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from httpx import AsyncClient

from tests.conftest import create_tenant_with_token, seeded_ip_enrichment, set_test_tenant_id

_BOOKING_PATH = "/api/v1/shipments/booking/evaluate"
_FEEDBACK_PATH = "/api/v1/shipments/feedback"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _booking_payload(
    *,
    request_id: str,
    customer_external_id: str = "fbchain-cust",
    user_external_id: str = "fbchain-user",
    source_ip: str = "203.0.113.60",
    origin_address: str = "10 Origin Lane",
    destination_address: str = "20 Destination Ave",
    origin_email: str | None = "alice@example.com",
    origin_phone: str | None = "+15551234567",
    booking_ts: str = "2026-05-27T08:00:00Z",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": request_id,
        "customer": {"external_id": customer_external_id},
        "user": {"external_id": user_external_id},
        "source_ip": source_ip,
        "shipment": {
            "origin": {"address": origin_address},
            "destination": {"address": destination_address},
            "value": 1000.00,
            "channel": "api",
        },
        "booking_ts": booking_ts,
    }
    contact: dict[str, str] = {}
    if origin_email:
        contact["origin_email"] = origin_email
    if origin_phone:
        contact["origin_phone"] = origin_phone
    if contact:
        payload["contact"] = contact
    return payload


def _feedback_payload(
    *,
    request_id: str,
    target_request_id: str,
    label: str = "rejected",
    feedback_ts: str = "2026-05-27T09:00:00Z",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "target_request_id": target_request_id,
        "label": label,
        "feedback_ts": feedback_ts,
    }


# ---------------------------------------------------------------------------
# Per-dimension chain activation
# ---------------------------------------------------------------------------


async def test_chain_ip_previously_rejected_fires_on_next_booking(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """POST booking -> POST feedback rejected -> POST next booking with
    same source_ip -> ip_previously_rejected_for_customer fires."""
    token, _ = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.60", asn_org="Comcast"):
        b1 = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="chain-ip-book-1"),
            headers=_headers(token),
        )
        assert b1.status_code == 200, b1.text

        fb = await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(
                request_id="chain-ip-fb-1",
                target_request_id="chain-ip-book-1",
                label="rejected",
            ),
            headers=_headers(token),
        )
        assert fb.status_code == 200, fb.text
        assert fb.json()["applied"] is True

        # Next booking from same IP, different request_id; same customer
        b2 = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="chain-ip-book-2",
                booking_ts="2026-05-27T10:00:00Z",
            ),
            headers=_headers(token),
        )
        assert b2.status_code == 200, b2.text
        triggered = set(b2.json()["triggered_rules"])
        assert "ip_previously_rejected_for_customer" in triggered, triggered


async def test_chain_origin_previously_rejected_fires_on_next_booking(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Origin address re-used after a rejection triggers
    origin_previously_rejected_for_customer."""
    token, _ = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.61", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="chain-org-book-1",
                source_ip="203.0.113.61",
                origin_address="500 Repeat Origin Rd",
            ),
            headers=_headers(token),
        )
        await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(
                request_id="chain-org-fb-1",
                target_request_id="chain-org-book-1",
                label="rejected",
            ),
            headers=_headers(token),
        )
        # Next booking: same origin, DIFFERENT IP so ip rule doesn't fire
        b2 = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="chain-org-book-2",
                source_ip="203.0.113.91",
                origin_address="500 Repeat Origin Rd",
                booking_ts="2026-05-27T10:00:00Z",
            ),
            headers=_headers(token),
        )
        assert b2.status_code == 200, b2.text
        triggered = set(b2.json()["triggered_rules"])
        assert "origin_previously_rejected_for_customer" in triggered, triggered
        # IP rule must NOT fire (different IP)
        assert "ip_previously_rejected_for_customer" not in triggered, triggered


async def test_chain_email_previously_rejected_fires_on_next_booking(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Email HMAC re-used after a rejection triggers
    email_previously_rejected_for_customer."""
    token, _ = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.62", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="chain-email-book-1",
                source_ip="203.0.113.62",
                origin_email="repeat.email@example.com",
            ),
            headers=_headers(token),
        )
        await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(
                request_id="chain-email-fb-1",
                target_request_id="chain-email-book-1",
                label="rejected",
            ),
            headers=_headers(token),
        )
        # Next booking: same email, DIFFERENT IP + origin
        b2 = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="chain-email-book-2",
                source_ip="203.0.113.92",
                origin_address="600 Different Origin Rd",
                origin_email="repeat.email@example.com",
                booking_ts="2026-05-27T10:00:00Z",
            ),
            headers=_headers(token),
        )
        assert b2.status_code == 200, b2.text
        triggered = set(b2.json()["triggered_rules"])
        assert "email_previously_rejected_for_customer" in triggered, triggered


# ---------------------------------------------------------------------------
# Approved does NOT trigger any previously-rejected rule
# ---------------------------------------------------------------------------


async def test_approved_feedback_does_not_trigger_previously_rejected(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Approved is a positive signal — baseline unchanged, no
    previously-rejected rule fires on next booking."""
    token, _ = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.63", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="chain-app-book-1", source_ip="203.0.113.63"),
            headers=_headers(token),
        )
        await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(
                request_id="chain-app-fb-1",
                target_request_id="chain-app-book-1",
                label="approved",
            ),
            headers=_headers(token),
        )
        b2 = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="chain-app-book-2",
                source_ip="203.0.113.63",
                booking_ts="2026-05-27T10:00:00Z",
            ),
            headers=_headers(token),
        )
        assert b2.status_code == 200
        triggered = set(b2.json()["triggered_rules"])
        previously_rejected_fired = {r for r in triggered if "previously_rejected" in r}
        assert not previously_rejected_fired, previously_rejected_fired


# ---------------------------------------------------------------------------
# Cross-tenant isolation: per-customer-per-tenant rejected dicts
# ---------------------------------------------------------------------------


async def test_cross_tenant_rejection_does_not_leak(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Tenant A rejects an email; Tenant B's booking with same email
    must NOT trigger email_previously_rejected (per-customer-per-tenant
    isolation; baselines are tenant-scoped)."""
    token_a, tenant_a = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.64", asn_org="Comcast"):
        # Tenant A rejection
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="chain-xt-a-book-1",
                source_ip="203.0.113.64",
                origin_email="shared.email@example.com",
            ),
            headers=_headers(token_a),
        )
        await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(
                request_id="chain-xt-a-fb-1",
                target_request_id="chain-xt-a-book-1",
                label="rejected",
            ),
            headers=_headers(token_a),
        )

        async with create_tenant_with_token(db_conn) as (token_b, _tenant_b):
            # Tenant B booking with the SAME email + DIFFERENT IP
            # (so the rejection state in tenant A could not leak)
            b_b = await unauth_client.post(
                _BOOKING_PATH,
                json=_booking_payload(
                    request_id="chain-xt-b-book-1",
                    customer_external_id="tenant-b-cust",
                    user_external_id="tenant-b-user",
                    source_ip="203.0.113.94",
                    origin_email="shared.email@example.com",
                    booking_ts="2026-05-27T10:00:00Z",
                ),
                headers=_headers(token_b),
            )
            assert b_b.status_code == 200
            triggered = set(b_b.json()["triggered_rules"])
            assert "email_previously_rejected_for_customer" not in triggered, triggered

        # create_tenant_with_token's finally leaves
        # app.tenant_id at tenant_b; restore tenant_a so the outer
        # seeded_tenant fixture teardown can DELETE its rows under RLS.
        await set_test_tenant_id(db_conn, tenant_a)


# ---------------------------------------------------------------------------
# Modification chain — feedback on a modification works the same way
# ---------------------------------------------------------------------------


async def test_chain_feedback_on_modification_triggers_rule_on_next_booking(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """A rejection on a modification's request_id also feeds the
    baseline; subsequent bookings trigger the previously-rejected
    rule (baseline writes happen against the customer regardless of
    whether the rejected decision was for a booking or a modification)."""
    token, _ = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.65", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="chain-mod-book-1", source_ip="203.0.113.65"),
            headers=_headers(token),
        )
        # Modify the booking
        mod_resp = await unauth_client.post(
            "/api/v1/shipments/modification/evaluate",
            json={
                "request_id": "chain-mod-mod-1",
                "original_request_id": "chain-mod-book-1",
                "modification_ts": "2026-05-27T08:30:00Z",
                "modification_type": "value",
                "new_value": {"value": 1100},
            },
            headers=_headers(token),
        )
        assert mod_resp.status_code == 200

        # Reject the modification
        fb = await unauth_client.post(
            _FEEDBACK_PATH,
            json=_feedback_payload(
                request_id="chain-mod-fb-1",
                target_request_id="chain-mod-mod-1",
                label="rejected",
            ),
            headers=_headers(token),
        )
        assert fb.status_code == 200
        assert fb.json()["applied"] is True

        # Next booking same customer + same IP — ip rule fires
        b2 = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="chain-mod-book-2",
                source_ip="203.0.113.65",
                booking_ts="2026-05-27T11:00:00Z",
            ),
            headers=_headers(token),
        )
        assert b2.status_code == 200
        triggered = set(b2.json()["triggered_rules"])
        assert "ip_previously_rejected_for_customer" in triggered, triggered
