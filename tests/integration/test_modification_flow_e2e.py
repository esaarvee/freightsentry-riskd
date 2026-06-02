"""End-to-end modification flow tests — scoring outcomes (3A.8).

3A.6 covers the modification endpoint contract surfaces (envelope shape,
idempotency, 404/422/409, cross-tenant). This file covers the SCORING
outcomes: that the 8 modification rules from 3A.7 fire correctly when
the modification endpoint receives realistic payloads, and that the
booking endpoint continues to behave as before (no regression from the
booking-path defaults BOOKING_PATH_MODIFICATION_DEFAULTS).

Tests use the unauth_client + seeded_api_token fixtures and seed real
DB state so the scoring math runs end-to-end.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
from httpx import AsyncClient

from tests.conftest import create_tenant_with_token, seeded_ip_enrichment, set_test_tenant_id

_BOOKING_PATH = "/api/v1/shipments/booking/evaluate"
_MOD_PATH = "/api/v1/shipments/modification/evaluate"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _booking_payload(
    *,
    request_id: str = "e2e-book-001",
    customer_external_id: str = "e2e-mod-cust",
    user_external_id: str = "e2e-mod-user",
    source_ip: str = "203.0.113.40",
    destination_address: str = "100 Familiar St, Boston, MA",
    value: float = 1000.00,
    channel: str = "api",
    booking_ts: str = "2026-05-27T08:00:00Z",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "customer": {"external_id": customer_external_id},
        "user": {"external_id": user_external_id},
        "source_ip": source_ip,
        "shipment": {
            "origin": {"address": "10 Origin Lane"},
            "destination": {"address": destination_address},
            "value": value,
            "channel": channel,
        },
        "booking_ts": booking_ts,
    }


def _modification_payload(
    *,
    request_id: str,
    original_request_id: str = "e2e-book-001",
    modification_type: str = "value",
    new_value: dict[str, Any] | None = None,
    modification_ts: str = "2026-05-27T08:05:00Z",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "original_request_id": original_request_id,
        "modification_ts": modification_ts,
        "modification_type": modification_type,
        "new_value": new_value or {"value": 1050},
    }


# ---------------------------------------------------------------------------
# Scoring outcomes: low-risk vs high-risk modification
# ---------------------------------------------------------------------------


async def test_low_risk_modification_does_not_block(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Small value change well after booking_ts — no modification rule
    should fire."""
    token, _ = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.40", asn_org="Comcast"):
        booking = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(booking_ts="2026-05-20T08:00:00Z"),
            headers=_headers(token),
        )
        assert booking.status_code == 200, booking.text

        mod = await unauth_client.post(
            _MOD_PATH,
            json=_modification_payload(
                request_id="e2e-low-risk-mod",
                modification_type="value",
                new_value={"value": 1010},  # 1% change — below 0.2 threshold
                modification_ts="2026-05-27T08:00:00Z",  # > 7 days after booking
            ),
            headers=_headers(token),
        )
        assert mod.status_code == 200, mod.text
        # No modification rule should fire on this benign payload
        triggered = set(mod.json()["triggered_rules"])
        modification_rules_fired = {r for r in triggered if r.startswith("modification_")}
        assert not modification_rules_fired, (
            f"unexpected modification rules fired: {modification_rules_fired}"
        )


async def test_high_risk_30min_value_jacking_fires_value_rule(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """50% value increase within 30 minutes of booking →
    modification_within_30_min_value_increase fires."""
    token, _ = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.41", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="e2e-jack-book",
                source_ip="203.0.113.41",
                booking_ts="2026-05-27T08:00:00Z",
            ),
            headers=_headers(token),
        )
        mod = await unauth_client.post(
            _MOD_PATH,
            json=_modification_payload(
                request_id="e2e-value-jack-mod",
                original_request_id="e2e-jack-book",
                modification_type="value",
                new_value={"value": 1500},  # 50% increase
                modification_ts="2026-05-27T08:10:00Z",  # 10 min after booking
            ),
            headers=_headers(token),
        )
        assert mod.status_code == 200, mod.text
        triggered = set(mod.json()["triggered_rules"])
        assert "modification_within_30_min_value_increase" in triggered, triggered


async def test_destination_change_pre_pickup_fires_when_unfamiliar(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Destination change to an address NOT in the customer's baseline
    within 24h of booking → modification_destination_change_pre_pickup
    fires (direction='unfamiliar')."""
    token, _ = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.42", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="e2e-redir-book",
                source_ip="203.0.113.42",
                destination_address="200 Original Ave, Seattle, WA",
                booking_ts="2026-05-27T08:00:00Z",
            ),
            headers=_headers(token),
        )
        mod = await unauth_client.post(
            _MOD_PATH,
            json=_modification_payload(
                request_id="e2e-redir-mod",
                original_request_id="e2e-redir-book",
                modification_type="destination",
                new_value={"destination": {"address": "999 Unfamiliar Rd, Miami, FL"}},
                modification_ts="2026-05-27T15:00:00Z",  # 7h after booking → within_24_hours
            ),
            headers=_headers(token),
        )
        assert mod.status_code == 200, mod.text
        triggered = set(mod.json()["triggered_rules"])
        assert "modification_destination_change_pre_pickup" in triggered, triggered


# ---------------------------------------------------------------------------
# Modification velocity attack
# ---------------------------------------------------------------------------


async def test_modification_velocity_attack_fires_high_velocity_rule(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """5 modifications by the same customer within 1h → the 5th
    modification's evaluation sees modification_velocity_1h > 3 and
    fires modification_high_velocity_1h.

    Note: the velocity COUNT reflects modifications already persisted
    BEFORE the current evaluation, so modifications 1-3 see velocity
    counts 0/1/2 and don't fire; modification 4 sees count 3
    (still > 3 is false); modification 5 sees count 4 (> 3 fires)."""
    token, _ = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.43", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="e2e-vel-book",
                source_ip="203.0.113.43",
                booking_ts="2026-05-27T08:00:00Z",
            ),
            headers=_headers(token),
        )
        # Issue 4 prior modifications, all of which precede the
        # high-velocity check on the 5th.
        for i in range(4):
            resp = await unauth_client.post(
                _MOD_PATH,
                json=_modification_payload(
                    request_id=f"e2e-vel-mod-{i}",
                    original_request_id="e2e-vel-book",
                    modification_type="value",
                    new_value={"value": 1010 + i},
                    modification_ts=f"2026-05-27T08:{30 + i}:00Z",
                ),
                headers=_headers(token),
            )
            assert resp.status_code == 200, resp.text

        # 5th modification — velocity_1h now sees 4 prior mods → > 3 fires
        fifth = await unauth_client.post(
            _MOD_PATH,
            json=_modification_payload(
                request_id="e2e-vel-mod-5",
                original_request_id="e2e-vel-book",
                modification_type="value",
                new_value={"value": 1050},
                modification_ts="2026-05-27T08:35:00Z",
            ),
            headers=_headers(token),
        )
        assert fifth.status_code == 200, fifth.text
        triggered = set(fifth.json()["triggered_rules"])
        assert "modification_high_velocity_1h" in triggered, triggered


# ---------------------------------------------------------------------------
# Concurrent booking + modification on the same customer — serialise via
# the customer_baselines FOR UPDATE lock (per app/baseline.py:236).
# ---------------------------------------------------------------------------


async def test_concurrent_booking_and_modification_serialise(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """A booking and a modification for the same customer issued in
    parallel must both succeed without deadlock or interleaved state.
    The SELECT FOR UPDATE on customer_baselines serialises them.
    """
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.44", asn_org="Comcast"):
        # Seed the original booking
        first_booking = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="e2e-conc-book-1",
                source_ip="203.0.113.44",
                booking_ts="2026-05-27T08:00:00Z",
            ),
            headers=_headers(token),
        )
        assert first_booking.status_code == 200, first_booking.text

        # Fire a second booking and a modification on the first in parallel
        async def post_second_booking() -> Any:
            return await unauth_client.post(
                _BOOKING_PATH,
                json=_booking_payload(
                    request_id="e2e-conc-book-2",
                    source_ip="203.0.113.44",
                    booking_ts="2026-05-27T08:15:00Z",
                ),
                headers=_headers(token),
            )

        async def post_modification() -> Any:
            return await unauth_client.post(
                _MOD_PATH,
                json=_modification_payload(
                    request_id="e2e-conc-mod-1",
                    original_request_id="e2e-conc-book-1",
                    modification_type="value",
                    new_value={"value": 1020},
                    modification_ts="2026-05-27T08:16:00Z",
                ),
                headers=_headers(token),
            )

        booking_resp, mod_resp = await asyncio.gather(post_second_booking(), post_modification())
        assert booking_resp.status_code == 200, booking_resp.text
        assert mod_resp.status_code == 200, mod_resp.text

        # Both decisions persisted; total_shipments incremented by 1
        # for the second booking, NOT for the modification.
        shipment_count = await db_conn.fetchval(
            "SELECT count(*) FROM shipments WHERE tenant_id = $1", tenant_id
        )
        assert shipment_count == 2  # two bookings, no modification shipment

        decision_types = await db_conn.fetch(
            """
            SELECT request_type, count(*) AS c
              FROM decisions WHERE tenant_id = $1
             GROUP BY request_type
            """,
            tenant_id,
        )
        type_map = {r["request_type"]: r["c"] for r in decision_types}
        assert type_map.get("booking") == 2
        assert type_map.get("modification") == 1


# ---------------------------------------------------------------------------
# Booking-path regression — no modification rule fires on a booking
# ---------------------------------------------------------------------------


async def test_booking_path_does_not_trigger_modification_rules(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """A booking evaluation must NEVER fire any of the 8 modification
    rules — the booking-path defaults from BOOKING_PATH_MODIFICATION_
    DEFAULTS keep the rules structurally dormant."""
    token, _ = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.45", asn_org="Comcast"):
        booking = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="e2e-bp-regress",
                source_ip="203.0.113.45",
                booking_ts="2026-05-27T08:00:00Z",
            ),
            headers=_headers(token),
        )
        assert booking.status_code == 200, booking.text
        triggered = set(booking.json()["triggered_rules"])
        modification_rules_fired = {r for r in triggered if r.startswith("modification_")}
        assert not modification_rules_fired, (
            f"booking unexpectedly triggered modification rules: {modification_rules_fired}"
        )


# ---------------------------------------------------------------------------
# Cross-tenant isolation: modification rules + velocity counts are
# per-tenant. Tenant B's modifications must not affect tenant A's
# modification_velocity_1h score.
# ---------------------------------------------------------------------------


async def test_modification_velocity_isolated_by_tenant(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Tenant B issuing 5 modifications must not bump tenant A's
    modification_velocity_1h. Pin against the same external_id
    namespace to verify the WHERE tenant_id filter scopes correctly."""
    token_a, tenant_a = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.46", asn_org="Comcast"):
        # Tenant A: one booking, no modifications
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="e2e-iso-book-a",
                source_ip="203.0.113.46",
                booking_ts="2026-05-27T08:00:00Z",
            ),
            headers=_headers(token_a),
        )

        async with create_tenant_with_token(db_conn) as (token_b, _tenant_b):
            # Tenant B: booking + 5 modifications (would trigger
            # high_velocity_1h on tenant B's 5th modification, but we
            # don't care — we care that tenant A is unaffected).
            await unauth_client.post(
                _BOOKING_PATH,
                json=_booking_payload(
                    request_id="e2e-iso-book-b",
                    source_ip="203.0.113.46",
                    booking_ts="2026-05-27T08:00:00Z",
                ),
                headers=_headers(token_b),
            )
            for i in range(5):
                await unauth_client.post(
                    _MOD_PATH,
                    json=_modification_payload(
                        request_id=f"e2e-iso-mod-b-{i}",
                        original_request_id="e2e-iso-book-b",
                        modification_type="value",
                        new_value={"value": 1010 + i},
                        modification_ts=f"2026-05-27T08:{10 + i}:00Z",
                    ),
                    headers=_headers(token_b),
                )

            # Tenant A's first modification — velocity must read 0
            # (B's 5 modifications scoped to B, not visible to A's query).
            mod_a = await unauth_client.post(
                _MOD_PATH,
                json=_modification_payload(
                    request_id="e2e-iso-mod-a-first",
                    original_request_id="e2e-iso-book-a",
                    modification_type="value",
                    new_value={"value": 1020},
                    modification_ts="2026-05-27T08:30:00Z",
                ),
                headers=_headers(token_a),
            )
            assert mod_a.status_code == 200, mod_a.text
            triggered_a = set(mod_a.json()["triggered_rules"])
            assert "modification_high_velocity_1h" not in triggered_a, triggered_a

        # Phase 5D.2: create_tenant_with_token's finally leaves
        # app.tenant_id at tenant_b; restore tenant_a so the outer
        # seeded_tenant fixture teardown can DELETE its rows under RLS.
        await set_test_tenant_id(db_conn, tenant_a)
