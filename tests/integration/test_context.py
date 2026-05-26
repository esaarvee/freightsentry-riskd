"""Integration tests for build_context — exercises the Phase 2B
derivations against the production code path.

Each test seeds a CustomerBaseline directly into the DB with controlled
state (cloud_share / api_share / value_n / last_booking_*), constructs
the BookingRequest + customer_row inputs build_context expects, then
calls build_context() and asserts on the returned ctx_env dict.

This is the ONLY layer where the Phase 2B threshold logic is verified
against the production code: if a future commit weakens `> 0.95` to
`>= 0.95` in app/context.py, the threshold tests below fail because
they assert ctx_env values, not inline-recomputed expressions.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import pytest

from app.context import build_context
from app.enrich import Enricher, EnrichmentRow
from app.models import Address, BookingRequest, CustomerData, ShipmentData, UserData
from app.signal_helpers import hmac_hex

_PHASE_2B_FIELDS = frozenset(
    {
        "customer_locked_cloud_api",
        "customer_locked_web_only",
        "days_since_last_booking",
        "is_new_user",
        "ip_familiarity_tier",
        "ip_new_known_asn",
        "is_residential_asn",
        "ip2p_threat_any",
        "recipient_cross_customer_count",
        "customer_distinct_ips_30d",
        "impossible_travel",
    }
)


def _payload(
    *,
    customer_external_id: str = "ctx-cust",
    source_ip: str = "192.0.2.42",
    destination_address: str = "500 5th Avenue",
    booking_ts: datetime | None = None,
) -> BookingRequest:
    return BookingRequest(
        request_id=f"ctx-req-{secrets.token_hex(4)}",
        customer=CustomerData(external_id=customer_external_id),
        user=UserData(external_id="ctx-user"),
        source_ip=IPv4Address(source_ip),
        shipment=ShipmentData(
            origin=Address(address="100 Bay Street"),
            destination=Address(address=destination_address),
            value=Decimal("250"),
            channel="web",
        ),
        booking_ts=booking_ts or datetime.now(tz=UTC),
    )


def _enricher_stub(enrichment: EnrichmentRow) -> Enricher:
    """A real Enricher whose `enrich` method is mocked to return the
    given row — bypassing the data-file dependency so tests can control
    the enrichment surface deterministically."""
    e = Enricher(data_dir=Path("/nonexistent"))
    e.enrich = AsyncMock(return_value=enrichment)  # type: ignore[method-assign]
    return e


async def _seed_customer(db_conn: asyncpg.Connection, tenant_id: int, external_id: str) -> int:
    cust_id: int = await db_conn.fetchval(
        """
        INSERT INTO customers (tenant_id, external_id, first_seen, total_shipments)
        VALUES ($1, $2, now() - interval '90 days', 20)
        RETURNING id
        """,
        tenant_id,
        external_id,
    )
    return cust_id


async def _seed_baseline(
    db_conn: asyncpg.Connection,
    tenant_id: int,
    customer_id: int,
    *,
    ip_type_hist: dict[str, float] | None = None,
    channel_hist: dict[str, float] | None = None,
    ip_asn_stats: dict[str, Any] | None = None,
    value_n: float = 0.0,
    last_booking_ts: datetime | None = None,
    last_booking_lat: float | None = None,
    last_booking_lon: float | None = None,
) -> None:
    await db_conn.execute(
        """
        INSERT INTO customer_baselines (
            tenant_id, customer_id,
            ip_stats, ip_netblock_stats, ip_asn_stats,
            country_stats, origin_ip_country_stats,
            origin_stats, dest_stats, lane_stats,
            ip_type_hist, hour_hist, weekday_hist, channel_hist,
            value_n, value_mean, value_m2,
            last_booking_ts, last_booking_lat, last_booking_lon,
            last_booking_country, decay_anchor_date
        )
        VALUES (
            $1, $2,
            '{}'::jsonb, '{}'::jsonb, $9::jsonb,
            '{}'::jsonb, '{}'::jsonb,
            '{}'::jsonb, '{}'::jsonb, '{}'::jsonb,
            $3::jsonb, '{}'::jsonb, '{}'::jsonb, $4::jsonb,
            $5, 250, 0,
            $6, $7, $8,
            NULL, current_date
        )
        """,
        tenant_id,
        customer_id,
        json.dumps(ip_type_hist or {}),
        json.dumps(channel_hist or {}),
        value_n,
        last_booking_ts,
        last_booking_lat,
        last_booking_lon,
        json.dumps(ip_asn_stats or {}),
    )


async def _fetch_customer_row(
    db_conn: asyncpg.Connection, tenant_id: int, customer_id: int
) -> asyncpg.Record:
    row = await db_conn.fetchrow(
        "SELECT * FROM customers WHERE id = $1 AND tenant_id = $2",
        customer_id,
        tenant_id,
    )
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# Phase 2B field-set pin — call the real build_context and assert keys
# ---------------------------------------------------------------------------


async def test_build_context_returns_all_phase2_fields(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """Pinning test: every Phase 2B Context field is produced by
    build_context. If a field is dropped in app/context.py, the
    intersection assertion fails."""
    cust_id = await _seed_customer(db_conn, seeded_tenant, "phase2b-pin")
    await _seed_baseline(db_conn, seeded_tenant, cust_id)
    row = await _fetch_customer_row(db_conn, seeded_tenant, cust_id)
    payload = _payload(customer_external_id="phase2b-pin")
    enricher = _enricher_stub(EnrichmentRow.empty("192.0.2.42"))
    dest_hmac = hmac_hex(payload.shipment.destination.address, b"test-secret")

    ctx, _baseline, _enrichment = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=enricher,
        payload=payload,
        destination_hmac=dest_hmac,
    )

    missing = _PHASE_2B_FIELDS - set(ctx.keys())
    assert not missing, f"build_context did not populate: {missing}"


# ---------------------------------------------------------------------------
# customer_locked_cloud_api — strict > 0.95 threshold from production code
# ---------------------------------------------------------------------------


async def test_customer_locked_cloud_api_strict_threshold_via_build_context(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """0.95 cloud_share + 0.95 api_share fails the strict-greater-than
    check — production code uses `> 0.95`, NOT `>= 0.95`. This test
    catches a `>` to `>=` weakening directly in app/context.py."""
    cust_id = await _seed_customer(db_conn, seeded_tenant, "lock-edge")
    await _seed_baseline(
        db_conn,
        seeded_tenant,
        cust_id,
        ip_type_hist={"cloud": 95.0, "residential": 5.0},
        channel_hist={"api": 95.0, "web": 5.0},
        value_n=25.0,
    )
    row = await _fetch_customer_row(db_conn, seeded_tenant, cust_id)
    payload = _payload(customer_external_id="lock-edge")
    enricher = _enricher_stub(EnrichmentRow.empty("192.0.2.42"))
    dest_hmac = hmac_hex(payload.shipment.destination.address, b"test-secret")

    ctx, _baseline, _enrichment = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=enricher,
        payload=payload,
        destination_hmac=dest_hmac,
    )
    assert ctx["customer_locked_cloud_api"] is False


async def test_customer_locked_cloud_api_just_above_threshold_via_build_context(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """Bump cloud_share/api_share to 0.96; the flag flips True."""
    cust_id = await _seed_customer(db_conn, seeded_tenant, "lock-above")
    await _seed_baseline(
        db_conn,
        seeded_tenant,
        cust_id,
        ip_type_hist={"cloud": 96.0, "residential": 4.0},
        channel_hist={"api": 96.0, "web": 4.0},
        value_n=25.0,
    )
    row = await _fetch_customer_row(db_conn, seeded_tenant, cust_id)
    payload = _payload(customer_external_id="lock-above")
    enricher = _enricher_stub(EnrichmentRow.empty("192.0.2.42"))
    dest_hmac = hmac_hex(payload.shipment.destination.address, b"test-secret")

    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=enricher,
        payload=payload,
        destination_hmac=dest_hmac,
    )
    assert ctx["customer_locked_cloud_api"] is True


async def test_customer_locked_cloud_api_fails_below_observations_threshold(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """value_n=19 below the 20 threshold prevents lock-in even with
    100% cloud + 100% api."""
    cust_id = await _seed_customer(db_conn, seeded_tenant, "lock-few")
    await _seed_baseline(
        db_conn,
        seeded_tenant,
        cust_id,
        ip_type_hist={"cloud": 100.0},
        channel_hist={"api": 100.0},
        value_n=19.0,
    )
    row = await _fetch_customer_row(db_conn, seeded_tenant, cust_id)
    payload = _payload(customer_external_id="lock-few")
    enricher = _enricher_stub(EnrichmentRow.empty("192.0.2.42"))
    dest_hmac = hmac_hex(payload.shipment.destination.address, b"test-secret")

    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=enricher,
        payload=payload,
        destination_hmac=dest_hmac,
    )
    assert ctx["customer_locked_cloud_api"] is False


# ---------------------------------------------------------------------------
# impossible_travel — three-condition AND in production code
# ---------------------------------------------------------------------------


async def test_impossible_travel_flips_when_same_day_500km(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """Same-day booking from a location > 500km away from last_booking
    → impossible_travel = True. Exercises the three-condition AND in
    app/context.py."""
    cust_id = await _seed_customer(db_conn, seeded_tenant, "travel-1")
    booking_ts = datetime.now(tz=UTC)
    # NYC (40.7, -74.0) vs LA (34.05, -118.24) is ~3935 km.
    await _seed_baseline(
        db_conn,
        seeded_tenant,
        cust_id,
        last_booking_ts=booking_ts - timedelta(hours=2),
        last_booking_lat=40.7,
        last_booking_lon=-74.0,
    )
    row = await _fetch_customer_row(db_conn, seeded_tenant, cust_id)
    payload = _payload(customer_external_id="travel-1", booking_ts=booking_ts)
    # Enrichment places source_ip in LA.
    la = EnrichmentRow(ip="192.0.2.42", lat=34.05, lon=-118.24)
    enricher = _enricher_stub(la)
    dest_hmac = hmac_hex(payload.shipment.destination.address, b"test-secret")

    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=enricher,
        payload=payload,
        destination_hmac=dest_hmac,
    )
    assert ctx["impossible_travel"] is True
    assert ctx["days_since_last_booking"] == 0
    assert ctx["ip_distance_km"] > 500.0


async def test_impossible_travel_false_when_distance_below_threshold(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """Same day but only ~30 km apart → impossible_travel = False
    (the 500 km threshold catches a `> 500` to `> 100` weakening)."""
    cust_id = await _seed_customer(db_conn, seeded_tenant, "travel-near")
    booking_ts = datetime.now(tz=UTC)
    # NYC (40.7, -74.0) vs Newark (40.7, -74.17) is ~14 km.
    await _seed_baseline(
        db_conn,
        seeded_tenant,
        cust_id,
        last_booking_ts=booking_ts - timedelta(hours=1),
        last_booking_lat=40.7,
        last_booking_lon=-74.0,
    )
    row = await _fetch_customer_row(db_conn, seeded_tenant, cust_id)
    payload = _payload(customer_external_id="travel-near", booking_ts=booking_ts)
    near = EnrichmentRow(ip="192.0.2.42", lat=40.7, lon=-74.17)
    enricher = _enricher_stub(near)
    dest_hmac = hmac_hex(payload.shipment.destination.address, b"test-secret")

    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=enricher,
        payload=payload,
        destination_hmac=dest_hmac,
    )
    assert ctx["impossible_travel"] is False
    assert ctx["days_since_last_booking"] == 0


async def test_impossible_travel_false_for_first_booking(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """No prior booking → last_booking_ts is None → impossible_travel
    is always False even if other inputs would otherwise trigger it."""
    cust_id = await _seed_customer(db_conn, seeded_tenant, "travel-first")
    await _seed_baseline(db_conn, seeded_tenant, cust_id)  # last_booking_ts=None
    row = await _fetch_customer_row(db_conn, seeded_tenant, cust_id)
    payload = _payload(customer_external_id="travel-first")
    enricher = _enricher_stub(EnrichmentRow.empty("192.0.2.42"))
    dest_hmac = hmac_hex(payload.shipment.destination.address, b"test-secret")

    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=enricher,
        payload=payload,
        destination_hmac=dest_hmac,
    )
    assert ctx["impossible_travel"] is False
    assert ctx["days_since_last_booking"] is None


# ---------------------------------------------------------------------------
# ip_familiarity_tier exposure + ip_new_known_asn — string + boolean derived
# ---------------------------------------------------------------------------


async def test_ip_familiarity_tier_exposed_as_string(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """A brand-new IP against an empty baseline yields tier 'fully_new'."""
    cust_id = await _seed_customer(db_conn, seeded_tenant, "tier-new")
    await _seed_baseline(db_conn, seeded_tenant, cust_id)
    row = await _fetch_customer_row(db_conn, seeded_tenant, cust_id)
    payload = _payload(customer_external_id="tier-new")
    enricher = _enricher_stub(EnrichmentRow.empty("192.0.2.42"))
    dest_hmac = hmac_hex(payload.shipment.destination.address, b"test-secret")

    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=enricher,
        payload=payload,
        destination_hmac=dest_hmac,
    )
    assert ctx["ip_familiarity_tier"] == "fully_new"
    assert ctx["ip_new_known_asn"] is False
    assert ctx["ip_fully_new"] is True


# ---------------------------------------------------------------------------
# recipient_cross_customer_count tenant isolation through build_context
# ---------------------------------------------------------------------------


async def test_recipient_cross_customer_count_isolated_by_tenant(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """Seed shipments under two tenants to the same destination_hmac;
    build_context for tenant_a sees only tenant_a's count. This is
    the security boundary at the Context-wiring level."""
    cust_id = await _seed_customer(db_conn, seeded_tenant, "recip-ctx-a")
    await _seed_baseline(db_conn, seeded_tenant, cust_id)
    row = await _fetch_customer_row(db_conn, seeded_tenant, cust_id)
    payload = _payload(customer_external_id="recip-ctx-a")

    dest_hmac = hmac_hex(payload.shipment.destination.address, b"recip-secret")
    now = datetime.now(tz=UTC)
    user_id = await db_conn.fetchval(
        "INSERT INTO users (tenant_id, customer_id, external_id) VALUES ($1, $2, 'recip-ctx-u') RETURNING id",
        seeded_tenant,
        cust_id,
    )

    # Seed 2 priors in tenant_a to the same hmac, 3 in another tenant.
    for i in range(2):
        other_cust = await db_conn.fetchval(
            "INSERT INTO customers (tenant_id, external_id) VALUES ($1, $2) RETURNING id",
            seeded_tenant,
            f"recip-ctx-prior-a-{i}",
        )
        await db_conn.execute(
            """
            INSERT INTO shipments (tenant_id, customer_id, user_id, request_id, source_ip,
                origin, destination, value, channel, booking_ts, destination_hmac)
            VALUES ($1, $2, $3, $4, $5::inet, '{}'::jsonb, '{}'::jsonb, 100, 'web', $6, $7)
            """,
            seeded_tenant,
            other_cust,
            user_id,
            f"recip-ctx-a-{i}",
            "192.0.2.99",
            now - timedelta(days=1),
            dest_hmac,
        )

    # Different tenant, same destination_hmac.
    other_tenant = await db_conn.fetchval(
        "INSERT INTO tenants (name) VALUES ('recip-ctx-other') RETURNING id"
    )
    try:
        for i in range(3):
            o_cust = await db_conn.fetchval(
                "INSERT INTO customers (tenant_id, external_id) VALUES ($1, $2) RETURNING id",
                other_tenant,
                f"recip-ctx-other-{i}",
            )
            o_user = await db_conn.fetchval(
                "INSERT INTO users (tenant_id, customer_id, external_id) VALUES ($1, $2, $3) RETURNING id",
                other_tenant,
                o_cust,
                f"recip-ctx-other-u-{i}",
            )
            await db_conn.execute(
                """
                INSERT INTO shipments (tenant_id, customer_id, user_id, request_id, source_ip,
                    origin, destination, value, channel, booking_ts, destination_hmac)
                VALUES ($1, $2, $3, $4, $5::inet, '{}'::jsonb, '{}'::jsonb, 100, 'web', $6, $7)
                """,
                other_tenant,
                o_cust,
                o_user,
                f"recip-ctx-other-{i}",
                "192.0.2.99",
                now - timedelta(days=1),
                dest_hmac,
            )

        enricher = _enricher_stub(EnrichmentRow.empty("192.0.2.42"))
        ctx, _b, _e = await build_context(
            db_conn,
            tenant_id=seeded_tenant,
            customer_id=cust_id,
            customer_row=row,
            enricher=enricher,
            payload=payload,
            destination_hmac=dest_hmac,
        )
        # Tenant_a sees 2 priors (NOT 5 — the 3 in other_tenant are excluded).
        assert ctx["recipient_cross_customer_count"] == 2
    finally:
        from tests.conftest import _cleanup_tenant

        await _cleanup_tenant(db_conn, other_tenant)


# ---------------------------------------------------------------------------
# is_residential_asn — production matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "is_cloud,is_datacenter,asn_org,expected",
    [
        (False, False, "Comcast", True),
        (True, False, "Amazon", False),
        (False, True, "Linode", False),
        (False, False, None, False),
    ],
)
async def test_is_residential_asn_matrix_through_build_context(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    is_cloud: bool,
    is_datacenter: bool,
    asn_org: str | None,
    expected: bool,
) -> None:
    """Production code: is_residential_asn = (not cloud) AND (not dc)
    AND (asn_org is not None). Tested via build_context output."""
    cust_id = await _seed_customer(
        db_conn, seeded_tenant, f"resi-{is_cloud}-{is_datacenter}-{asn_org}"
    )
    await _seed_baseline(db_conn, seeded_tenant, cust_id)
    row = await _fetch_customer_row(db_conn, seeded_tenant, cust_id)
    payload = _payload(customer_external_id=f"resi-{is_cloud}-{is_datacenter}-{asn_org}")
    enr = EnrichmentRow(
        ip="192.0.2.42",
        is_cloud=is_cloud,
        is_datacenter=is_datacenter,
        asn_org=asn_org,
    )
    enricher = _enricher_stub(enr)
    dest_hmac = hmac_hex(payload.shipment.destination.address, b"test-secret")

    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=enricher,
        payload=payload,
        destination_hmac=dest_hmac,
    )
    assert ctx["is_residential_asn"] is expected


# ---------------------------------------------------------------------------
# is_new_user — strict < 5.0 boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value_n,expected", [(4.0, True), (5.0, False), (0.0, True)])
async def test_is_new_user_strict_boundary_via_build_context(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    value_n: float,
    expected: bool,
) -> None:
    """Production: is_new_user = baseline.value_n < 5.0 (strict). The
    5.0 → False case catches a `<` to `<=` weakening."""
    cust_id = await _seed_customer(db_conn, seeded_tenant, f"newuser-{value_n}")
    await _seed_baseline(db_conn, seeded_tenant, cust_id, value_n=value_n)
    row = await _fetch_customer_row(db_conn, seeded_tenant, cust_id)
    payload = _payload(customer_external_id=f"newuser-{value_n}")
    enricher = _enricher_stub(EnrichmentRow.empty("192.0.2.42"))
    dest_hmac = hmac_hex(payload.shipment.destination.address, b"test-secret")

    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=enricher,
        payload=payload,
        destination_hmac=dest_hmac,
    )
    assert ctx["is_new_user"] is expected


# ---------------------------------------------------------------------------
# ip_new_known_asn — positive case (IP unknown, /24 unknown, but ASN known)
# ---------------------------------------------------------------------------


async def test_ip_new_known_asn_positive_when_asn_in_baseline(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    """Seed an ip_asn_stats entry; an IP from that same ASN but a new
    /24 yields tier 'new_known_asn' and ip_new_known_asn=True."""
    cust_id = await _seed_customer(db_conn, seeded_tenant, "tier-known-asn")
    await _seed_baseline(
        db_conn,
        seeded_tenant,
        cust_id,
        ip_asn_stats={"Comcast": {"n": 10.0, "r_n": 0.0, "last": "2026-05-01"}},
    )
    row = await _fetch_customer_row(db_conn, seeded_tenant, cust_id)
    payload = _payload(customer_external_id="tier-known-asn")
    # Enrichment says source_ip belongs to Comcast — a known ASN in the
    # baseline — but the IP itself and its /24 are new.
    enr = EnrichmentRow(ip="192.0.2.42", asn_org="Comcast")
    enricher = _enricher_stub(enr)
    dest_hmac = hmac_hex(payload.shipment.destination.address, b"test-secret")

    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=enricher,
        payload=payload,
        destination_hmac=dest_hmac,
    )
    assert ctx["ip_familiarity_tier"] == "new_known_asn"
    assert ctx["ip_new_known_asn"] is True
    assert ctx["ip_fully_new"] is False


# ---------------------------------------------------------------------------
# ip2p_threat_any — bool() truthiness on enrichment.threat
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "threat,expected",
    [(None, False), ("", False), ("BOTNET", True), ("SCANNER|SPAM", True)],
)
async def test_ip2p_threat_any_via_build_context(
    db_conn: asyncpg.Connection,
    seeded_tenant: int,
    threat: str | None,
    expected: bool,
) -> None:
    """ip2p_threat_any = bool(enrichment.threat). A `bool()` to
    `is not None` weakening would treat empty string as True; the
    empty-string case catches that regression."""
    cust_id = await _seed_customer(db_conn, seeded_tenant, f"threat-{threat or 'none'}")
    await _seed_baseline(db_conn, seeded_tenant, cust_id)
    row = await _fetch_customer_row(db_conn, seeded_tenant, cust_id)
    payload = _payload(customer_external_id=f"threat-{threat or 'none'}")
    enr = EnrichmentRow(ip="192.0.2.42", threat=threat)
    enricher = _enricher_stub(enr)
    dest_hmac = hmac_hex(payload.shipment.destination.address, b"test-secret")

    ctx, _b, _e = await build_context(
        db_conn,
        tenant_id=seeded_tenant,
        customer_id=cust_id,
        customer_row=row,
        enricher=enricher,
        payload=payload,
        destination_hmac=dest_hmac,
    )
    assert ctx["ip2p_threat_any"] is expected
