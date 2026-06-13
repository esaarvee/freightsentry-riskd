"""Cross-batch chain integration test — value demo.

The canonical end-to-end chain that demonstrates modification,
feedback, and scoring all compose correctly across the three new
endpoints:

  booking → modification → feedback → next booking (triggers rule)

The per-batch tests each cover their batch's surfaces in isolation.
This file integrates them: the modification triggers a high-velocity
rule on the path, the feedback rejection feeds the baseline, and the
next booking by the same customer triggers the corresponding
previously-rejected rule. Per-surface tests cannot demonstrate this
shape because the chain crosses the modification and feedback endpoints.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from httpx import AsyncClient

from tests.conftest import seeded_ip_enrichment

_BOOKING_PATH = "/api/v1/shipments/booking/evaluate"
_MOD_PATH = "/api/v1/shipments/modification/evaluate"
_FB_PATH = "/api/v1/shipments/feedback"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _booking_payload(
    *,
    request_id: str,
    customer_external_id: str = "chain-cust",
    user_external_id: str = "chain-user",
    source_ip: str = "203.0.113.90",
    destination_address: str = "20 Destination Ave",
    origin_email: str | None = "chain@example.com",
    booking_ts: str = "2026-05-27T08:00:00Z",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": request_id,
        "customer": {"external_id": customer_external_id},
        "user": {"external_id": user_external_id},
        "source_ip": source_ip,
        "shipment": {
            "origin": {"address": "10 Origin Lane"},
            "destination": {"address": destination_address},
            "value": 1000.00,
            "channel": "api",
        },
        "booking_ts": booking_ts,
    }
    if origin_email:
        payload["contact"] = {"origin_email": origin_email}
    return payload


# ---------------------------------------------------------------------------
# Full cross-batch chain
# ---------------------------------------------------------------------------


async def test_full_chain_booking_modification_feedback_next_booking_triggers_rule(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Full cross-batch chain:
    1. Booking (booking path)
    2. Modification (modification path) — succeeds, persists with
       request_type='modification'
    3. Feedback rejecting the MODIFICATION's request_id (feedback
       path) — applies; baseline.rejected_email_hmacs += 1,
       ip_stats.r_n += 1, customer flagged_count += 1
    4. Next booking from same customer + same IP + same email triggers
       email_previously_rejected_for_customer AND
       ip_previously_rejected_for_customer rules.
    """
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.90", asn_org="Comcast"):
        # Step 1: original booking
        book_1 = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="chain-book-1"),
            headers=_headers(token),
        )
        assert book_1.status_code == 200, book_1.text

        # Step 2: modification on the original
        mod = await unauth_client.post(
            _MOD_PATH,
            json={
                "request_id": "chain-mod-1",
                "original_request_id": "chain-book-1",
                "modification_ts": "2026-05-27T08:15:00Z",
                "modification_type": "value",
                "new_value": {"value": 1100},
            },
            headers=_headers(token),
        )
        assert mod.status_code == 200, mod.text

        # Step 3: feedback REJECTING the modification's request_id
        fb = await unauth_client.post(
            _FB_PATH,
            json={
                "request_id": "chain-fb-1",
                "target_request_id": "chain-mod-1",  # modification, not booking
                "label": "rejected",
                "feedback_ts": "2026-05-27T09:00:00Z",
            },
            headers=_headers(token),
        )
        assert fb.status_code == 200, fb.text
        assert fb.json()["applied"] is True

        # Verify counter incremented (baseline writes flowed through)
        flagged = await db_conn.fetchval(
            "SELECT flagged_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "chain-cust",
        )
        assert flagged == 1

        # Step 4: next booking same customer + same IP + same email
        book_2 = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="chain-book-2",
                booking_ts="2026-05-27T10:00:00Z",
            ),
            headers=_headers(token),
        )
        assert book_2.status_code == 200, book_2.text
        triggered = set(book_2.json()["triggered_rules"])
        # Multiple previously-rejected rules fire from the same rejection
        assert "email_previously_rejected_for_customer" in triggered, triggered
        assert "ip_previously_rejected_for_customer" in triggered, triggered
        assert "origin_previously_rejected_for_customer" in triggered, triggered


# ---------------------------------------------------------------------------
# Approved feedback on a modification — baseline observation, not rejection
# ---------------------------------------------------------------------------


async def test_approved_feedback_on_modification_does_not_flag(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """When feedback approves a modification, the next booking does NOT
    trip previously-rejected rules (positive signal, no baseline r_n
    writes, no counter increments)."""
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.91", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="chain-app-book-1", source_ip="203.0.113.91"),
            headers=_headers(token),
        )
        await unauth_client.post(
            _MOD_PATH,
            json={
                "request_id": "chain-app-mod-1",
                "original_request_id": "chain-app-book-1",
                "modification_ts": "2026-05-27T08:15:00Z",
                "modification_type": "value",
                "new_value": {"value": 1050},
            },
            headers=_headers(token),
        )
        # Approve the modification
        await unauth_client.post(
            _FB_PATH,
            json={
                "request_id": "chain-app-fb-1",
                "target_request_id": "chain-app-mod-1",
                "label": "approved",
                "feedback_ts": "2026-05-27T09:00:00Z",
            },
            headers=_headers(token),
        )

        # Customer flagged_count unchanged
        flagged = await db_conn.fetchval(
            "SELECT flagged_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "chain-cust",
        )
        assert flagged == 0

        # Next booking — no previously-rejected rule fires
        book_2 = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="chain-app-book-2",
                source_ip="203.0.113.91",
                booking_ts="2026-05-27T10:00:00Z",
            ),
            headers=_headers(token),
        )
        triggered = set(book_2.json()["triggered_rules"])
        previously_rejected = {r for r in triggered if "previously_rejected" in r}
        assert not previously_rejected, previously_rejected


# ---------------------------------------------------------------------------
# Modification path inherits previously-rejected derivations
# ---------------------------------------------------------------------------


async def test_modification_on_rejected_baseline_inherits_signal(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """After a rejection, a subsequent modification's Context inherits
    the *_previously_rejected fields via the shared build_context path.
    Verifies that previously-rejected derivations flow through build_modification_context
    when the modification reuses the same source IP."""
    token, _tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.92", asn_org="Comcast"):
        # Booking 1 → feedback rejected
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="chain-inh-book-1", source_ip="203.0.113.92"),
            headers=_headers(token),
        )
        await unauth_client.post(
            _FB_PATH,
            json={
                "request_id": "chain-inh-fb-1",
                "target_request_id": "chain-inh-book-1",
                "label": "rejected",
                "feedback_ts": "2026-05-27T09:00:00Z",
            },
            headers=_headers(token),
        )

        # Booking 2 (so we have something to modify)
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="chain-inh-book-2",
                source_ip="203.0.113.92",
                booking_ts="2026-05-27T10:00:00Z",
            ),
            headers=_headers(token),
        )

        # Modification on booking 2 — Context inherits ip_previously_rejected
        mod = await unauth_client.post(
            _MOD_PATH,
            json={
                "request_id": "chain-inh-mod-1",
                "original_request_id": "chain-inh-book-2",
                "modification_ts": "2026-05-27T10:30:00Z",
                "modification_type": "value",
                "new_value": {"value": 1100},
            },
            headers=_headers(token),
        )
        assert mod.status_code == 200, mod.text
        triggered = set(mod.json()["triggered_rules"])
        # The modification path's Context carries ip_previously_rejected
        # = True because the same IP was rejected in step 1
        assert "ip_previously_rejected_for_customer" in triggered, triggered
