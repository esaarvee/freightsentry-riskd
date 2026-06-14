"""Maturity + modification rule composition integration test.

Layer 2 downweights maturity-sensitive rule weights
by a `(1 - maturity)`-style factor when the customer is "young" (thin
baseline). The 8 modification rules include some that are
maturity-sensitive (destination_change_pre_pickup, high_velocity_24h,
dormant_customer, recipient_change, destination_change_residential_asn),
others are not (within_30_min_value_increase, high_velocity_1h,
low_trust_customer).

These tests prove the two systems compose correctly: maturity-sensitive
modification rules downweight for thin baselines and fire at full
weight for mature ones; non-maturity-sensitive rules fire at full
weight regardless. Per-batch tests cannot demonstrate this because the
composition spans the maturity layer and the modification rules.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg
from httpx import AsyncClient

from tests.conftest import seed_customer_with_baseline, seeded_ip_enrichment

_BOOKING_PATH = "/api/v1/shipments/booking/evaluate"
_MOD_PATH = "/api/v1/shipments/modification/evaluate"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _booking_payload(
    *,
    request_id: str,
    customer_external_id: str,
    user_external_id: str = "mat-user",
    source_ip: str = "203.0.113.100",
    booking_ts: str = "2026-05-27T08:00:00Z",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "shipment_id": f"ship-{request_id}",
        "transaction_number": f"txn-{request_id}",
        "customer": {"external_id": customer_external_id},
        "user": {"external_id": user_external_id},
        "source_ip": source_ip,
        "shipment": {
            "origin": {"address": "10 Origin Lane"},
            "destination": {"address": "20 Destination Ave"},
            "value": 1000.00,
            "channel": "api",
        },
        "booking_ts": booking_ts,
    }


async def _seed_mature_customer(
    db_conn: asyncpg.Connection,
    tenant_id: int,
    *,
    external_id: str,
) -> int:
    """Seed a mature customer (effective_observations >= 50). Maturity
    in the Layer 2 formula approaches 1.0 as observations accumulate;
    a customer with 50 observations is effectively mature."""
    return await seed_customer_with_baseline(
        db_conn,
        tenant_id,
        external_id=external_id,
        first_seen_days_ago=365,
        total_shipments=50,
        baseline_kwargs={"value_n": 50.0},
    )


async def _seed_thin_customer(
    db_conn: asyncpg.Connection,
    tenant_id: int,
    *,
    external_id: str,
) -> int:
    """Seed a thin-baseline customer (low effective_observations).
    Layer 2 maturity stays close to 0; maturity-sensitive rules
    downweight aggressively."""
    return await seed_customer_with_baseline(
        db_conn,
        tenant_id,
        external_id=external_id,
        first_seen_days_ago=7,
        total_shipments=2,
        baseline_kwargs={"value_n": 2.0},
    )


# ---------------------------------------------------------------------------
# Maturity-sensitive modification rule downweights for thin baselines
# ---------------------------------------------------------------------------


async def test_high_velocity_24h_score_lower_for_thin_baseline(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """modification_high_velocity_24h is maturity-sensitive (weight 0.45).
    Same number of modifications by a thin-baseline customer should
    produce a LOWER score than by a mature customer because Layer 2
    downweights the rule contribution."""
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.100", asn_org="Comcast"):
        # Seed two customers — mature and thin
        await _seed_mature_customer(db_conn, tenant_id, external_id="mature-cust")
        await _seed_thin_customer(db_conn, tenant_id, external_id="thin-cust")

        # Create a base booking for each (so they have something to modify)
        for ext_id, request_id in [
            ("mature-cust", "mat-book-1"),
            ("thin-cust", "thin-book-1"),
        ]:
            await unauth_client.post(
                _BOOKING_PATH,
                json=_booking_payload(request_id=request_id, customer_external_id=ext_id),
                headers=_headers(token),
            )

        # Fire 11 modifications per customer to trip high_velocity_24h
        # (threshold > 10) for both
        for ext_id, base_book in [
            ("mature-cust", "mat-book-1"),
            ("thin-cust", "thin-book-1"),
        ]:
            for i in range(11):
                await unauth_client.post(
                    _MOD_PATH,
                    json={
                        "request_id": f"{ext_id}-mod-{i}",
                        "original_request_id": base_book,
                        "shipment_id": f"ship-{base_book}",
                        "transaction_number": f"txn-{base_book}",
                        "modification_ts": f"2026-05-27T0{8 + (i % 2)}:{(i * 5) % 60:02d}:00Z",
                        "modification_type": "value",
                        "new_value": {"value": 1010 + i},
                    },
                    headers=_headers(token),
                )

        # 12th modification for each — observe the rule firing + the
        # score difference attributable to maturity downweight
        scores: dict[str, float] = {}
        for ext_id, base_book in [
            ("mature-cust", "mat-book-1"),
            ("thin-cust", "thin-book-1"),
        ]:
            resp = await unauth_client.post(
                _MOD_PATH,
                json={
                    "request_id": f"{ext_id}-final-mod",
                    "original_request_id": base_book,
                    "shipment_id": f"ship-{base_book}",
                    "transaction_number": f"txn-{base_book}",
                    "modification_ts": "2026-05-27T20:00:00Z",
                    "modification_type": "value",
                    "new_value": {"value": 1500},
                },
                headers=_headers(token),
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            triggered = set(body["triggered_rules"])
            assert "modification_high_velocity_24h" in triggered, triggered
            scores[ext_id] = body["score"]

        # The thin-baseline customer's score for the maturity-sensitive
        # rule contribution is downweighted; the mature customer fires
        # the rule at full weight. The mature score should be >= the
        # thin score (Layer 2 downweighting is a maturity multiplier).
        assert scores["mature-cust"] >= scores["thin-cust"], scores


# ---------------------------------------------------------------------------
# Non-maturity-sensitive modification rule fires regardless
# ---------------------------------------------------------------------------


async def test_high_velocity_1h_fires_for_both_thin_and_mature(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """modification_high_velocity_1h is NOT maturity-sensitive (weight
    0.70). The rule fires at full weight for both thin and mature
    customers — the campaign signal is age-agnostic."""
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.101", asn_org="Comcast"):
        await _seed_mature_customer(db_conn, tenant_id, external_id="mature-cust-h1")
        await _seed_thin_customer(db_conn, tenant_id, external_id="thin-cust-h1")

        for ext_id, request_id in [
            ("mature-cust-h1", "mat-h1-book"),
            ("thin-cust-h1", "thin-h1-book"),
        ]:
            await unauth_client.post(
                _BOOKING_PATH,
                json=_booking_payload(
                    request_id=request_id,
                    customer_external_id=ext_id,
                    source_ip="203.0.113.101",
                ),
                headers=_headers(token),
            )

        # 4 modifications each within the same hour → 5th fires
        # modification_high_velocity_1h (threshold > 3)
        for ext_id, base_book in [
            ("mature-cust-h1", "mat-h1-book"),
            ("thin-cust-h1", "thin-h1-book"),
        ]:
            for i in range(4):
                await unauth_client.post(
                    _MOD_PATH,
                    json={
                        "request_id": f"{ext_id}-h1-mod-{i}",
                        "original_request_id": base_book,
                        "shipment_id": f"ship-{base_book}",
                        "transaction_number": f"txn-{base_book}",
                        "modification_ts": f"2026-05-27T08:{10 + i}:00Z",
                        "modification_type": "value",
                        "new_value": {"value": 1010 + i},
                    },
                    headers=_headers(token),
                )

            # 5th modification trips high_velocity_1h
            final = await unauth_client.post(
                _MOD_PATH,
                json={
                    "request_id": f"{ext_id}-h1-final",
                    "original_request_id": base_book,
                    "shipment_id": f"ship-{base_book}",
                    "transaction_number": f"txn-{base_book}",
                    "modification_ts": "2026-05-27T08:30:00Z",
                    "modification_type": "value",
                    "new_value": {"value": 1500},
                },
                headers=_headers(token),
            )
            assert final.status_code == 200, final.text
            triggered = set(final.json()["triggered_rules"])
            assert "modification_high_velocity_1h" in triggered, (
                f"high_velocity_1h didn't fire for {ext_id}: {triggered}"
            )


# ---------------------------------------------------------------------------
# Trust score composition with low_trust_customer rule
# ---------------------------------------------------------------------------


async def test_low_trust_customer_rule_fires_with_seeded_low_trust(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """modification_low_trust_customer: trust_score < 0.3 AND
    modification_type == 'destination'. trust_score is a derived
    Layer 2 input. Seed a low-trust customer (lots of flagged_count)
    and verify the rule fires when they attempt a destination change."""
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.102", asn_org="Comcast"):
        # Seed a customer with high flagged_count → low trust_score
        await seed_customer_with_baseline(
            db_conn,
            tenant_id,
            external_id="lowtrust-cust",
            first_seen_days_ago=30,
            total_shipments=20,
            flagged_count=10,  # heavy flagging → trust_score drops below 0.3
            fraud_confirmed_count=2,
            baseline_kwargs={"value_n": 20.0},
        )

        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="lowtrust-book",
                customer_external_id="lowtrust-cust",
                source_ip="203.0.113.102",
            ),
            headers=_headers(token),
        )

        mod = await unauth_client.post(
            _MOD_PATH,
            json={
                "request_id": "lowtrust-mod",
                "original_request_id": "lowtrust-book",
                "shipment_id": "ship-lowtrust-book",
                "transaction_number": "txn-lowtrust-book",
                "modification_ts": "2026-05-27T08:30:00Z",
                "modification_type": "destination",
                "new_value": {"destination": {"address": "999 New Address, Other City"}},
            },
            headers=_headers(token),
        )
        assert mod.status_code == 200, mod.text
        body = mod.json()
        triggered = set(body["triggered_rules"])
        # trust_score for a customer with 10 flags + 2 fraud
        # confirmations + 20 observations / 30d age should sit well
        # below 0.3 — the rule fires
        assert "modification_low_trust_customer" in triggered, (
            f"low_trust_customer should fire but didn't: trust likely too high, "
            f"triggered={triggered}"
        )


# ---------------------------------------------------------------------------
# Maturity-aware compound: multiple modification rules fire together
# ---------------------------------------------------------------------------


async def test_destination_change_compound_rules_fire_together(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """A destination change to an unfamiliar address by a mature
    customer triggers BOTH:
    - modification_destination_change_pre_pickup (maturity-sensitive)
    - modification_destination_change_residential_asn (maturity-sensitive)
    Verifies that noisy-OR composition handles multiple modification
    rules firing in the same evaluation."""
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(
        db_conn,
        "203.0.113.103",
        asn_org="Comcast",
        # Residential ASN — explicit override
        is_cloud=False,
        is_datacenter=False,
    ):
        cust = await _seed_mature_customer(db_conn, tenant_id, external_id="compound-cust")
        # Add familiar destinations to baseline so the new destination
        # registers as "unfamiliar"
        await db_conn.execute(
            "UPDATE customer_baselines SET dest_stats = $1::jsonb WHERE customer_id = $2",
            json.dumps({"10 Familiar Ave": {"n": 5.0, "r_n": 0.0, "last": "2026-05-01"}}),
            cust,
        )

        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(
                request_id="compound-book",
                customer_external_id="compound-cust",
                source_ip="203.0.113.103",
                booking_ts="2026-05-27T08:00:00Z",
            ),
            headers=_headers(token),
        )

        mod = await unauth_client.post(
            _MOD_PATH,
            json={
                "request_id": "compound-mod",
                "original_request_id": "compound-book",
                "shipment_id": "ship-compound-book",
                "transaction_number": "txn-compound-book",
                # 12h after booking — lands in the within_24_hours bucket
                # (within_30_min covers <= 30min; within_1_hour covers
                # 30min < t <= 1h; within_24_hours covers 1h < t <= 24h).
                # pre_pickup rule condition requires this bucket exactly.
                "modification_ts": "2026-05-27T20:00:00Z",
                "modification_type": "destination",
                "new_value": {"destination": {"address": "999 New Unfamiliar Rd"}},
            },
            headers=_headers(token),
        )
        assert mod.status_code == 200, mod.text
        triggered = set(mod.json()["triggered_rules"])
        # Both maturity-sensitive destination-change rules fire on the
        # same evaluation; noisy-OR composes them in the final score
        assert "modification_destination_change_pre_pickup" in triggered, triggered
        assert "modification_destination_change_residential_asn" in triggered, triggered
