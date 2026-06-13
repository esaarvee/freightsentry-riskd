"""End-to-end integration tests for cold-start grace period (4C.4).

Grace mechanism: during the grace window after tenant onboarding, the
maturity formula multiplies its result by 0.5. After the window, no
multiplier.

5 tests covering grace disabled, active, expired, composition with
overrides, and Layer 1 invariance.
"""

from __future__ import annotations

import secrets
from datetime import UTC, date, datetime

import asyncpg
from httpx import AsyncClient

from app.auth import AuthContext, require_api_token
from app.main import app
from tests.conftest import _cleanup_tenant, seed_tenant_created_days_ago, set_test_tenant_id
from tests.ips import BLACKLISTED_IP, CLEAN_IP


def _booking(
    *,
    request_id: str,
    customer: str,
    source_ip: str = CLEAN_IP,
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "customer": {"external_id": customer},
        "user": {"external_id": "user-cs"},
        "source_ip": source_ip,
        "shipment": {
            "origin": {"address": "1 Main St"},
            "destination": {"address": "2 Park Ave"},
            "value": "100",
            "channel": "web",
        },
        "booking_ts": datetime.now(UTC).isoformat(),
    }


async def _seed_mature_customer(
    db_conn: asyncpg.Connection, tenant_id: int, external_id: str
) -> int:
    cust_id: int = await db_conn.fetchval(
        """
        INSERT INTO customers (
            tenant_id, external_id, first_seen, total_shipments
        )
        VALUES ($1, $2, now() - make_interval(days => 180), 50)
        RETURNING id
        """,
        tenant_id,
        external_id,
    )
    await db_conn.execute(
        """
        INSERT INTO customer_baselines (tenant_id, customer_id, decay_anchor_date)
        VALUES ($1, $2, $3)
        """,
        tenant_id,
        cust_id,
        date.today(),
    )
    return cust_id


async def _post(
    client: AsyncClient, tenant_id: int, payload: dict[str, object]
) -> dict[str, object]:
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=tenant_id, role="tenant"
    )
    try:
        payload = {**payload, "request_id": f"{payload['request_id']}-{secrets.token_hex(3)}"}
        r = await client.post("/api/v1/shipments/booking/evaluate", json=payload)
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    assert r.status_code == 200, f"booking failed: {r.status_code} {r.text}"
    return r.json()


async def test_grace_disabled_default_no_effect(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Default tenant (cold_start_grace_days=0): mature customer scores at
    baseline (no grace multiplier)."""
    await _seed_mature_customer(db_conn, seeded_tenant, "cust-cs-default")
    resp = await _post(
        unauth_client,
        seeded_tenant,
        _booking(request_id="REQ-cs-default", customer="cust-cs-default"),
    )
    assert resp["decision"] == "ALLOW"
    # Mature → m=1.0 → base_prior=0 → score~0
    assert resp["score"] < 0.05


async def test_grace_active_elevates_score_for_mature_customer(
    db_conn: asyncpg.Connection, unauth_client: AsyncClient
) -> None:
    """Tenant with cold_start_grace_days=14, created 5 days ago. A normally-
    mature customer (age=180, ships=50) scores with maturity=0.5 (not 1.0)
    → base_prior = MAX_NEW_ACCOUNT * 0.5 = 0.05 → account_prior > default."""
    tenant_id = await seed_tenant_created_days_ago(
        db_conn, days_ago=5, config={"cold_start_grace_days": 14}
    )
    # Also seed a default tenant for comparison.
    default_tid: int = await db_conn.fetchval(
        'INSERT INTO tenants (name, config) VALUES ($1, \'{"allowed_currencies": ["USD", "CAD"]}\'::jsonb) RETURNING id',
        f"cs-default-{secrets.token_hex(3)}",
    )
    try:
        await set_test_tenant_id(db_conn, tenant_id)
        await _seed_mature_customer(db_conn, tenant_id, "cust-cs-grace")
        await set_test_tenant_id(db_conn, default_tid)
        await _seed_mature_customer(db_conn, default_tid, "cust-cs-control")
        grace = await _post(
            unauth_client,
            tenant_id,
            _booking(request_id="REQ-cs-grace", customer="cust-cs-grace"),
        )
        control = await _post(
            unauth_client,
            default_tid,
            _booking(request_id="REQ-cs-control", customer="cust-cs-control"),
        )
        # Grace-active tenant produces a higher score for the same customer
        # because maturity is halved → base_prior is elevated.
        assert grace["score"] > control["score"]
    finally:
        await set_test_tenant_id(db_conn, tenant_id)
        await _cleanup_tenant(db_conn, tenant_id)
        await set_test_tenant_id(db_conn, default_tid)
        await _cleanup_tenant(db_conn, default_tid)


async def test_grace_expired_no_effect(
    db_conn: asyncpg.Connection, unauth_client: AsyncClient
) -> None:
    """Tenant with cold_start_grace_days=7, created 10 days ago — past
    window. No multiplier applied; behavior matches default."""
    tenant_id = await seed_tenant_created_days_ago(
        db_conn, days_ago=10, config={"cold_start_grace_days": 7}
    )
    default_tid: int = await db_conn.fetchval(
        'INSERT INTO tenants (name, config) VALUES ($1, \'{"allowed_currencies": ["USD", "CAD"]}\'::jsonb) RETURNING id',
        f"cs-expired-{secrets.token_hex(3)}",
    )
    try:
        await set_test_tenant_id(db_conn, tenant_id)
        await _seed_mature_customer(db_conn, tenant_id, "cust-cs-expired")
        await set_test_tenant_id(db_conn, default_tid)
        await _seed_mature_customer(db_conn, default_tid, "cust-cs-expired-ctrl")
        post = await _post(
            unauth_client,
            tenant_id,
            _booking(request_id="REQ-cs-expired", customer="cust-cs-expired"),
        )
        control = await _post(
            unauth_client,
            default_tid,
            _booking(request_id="REQ-cs-expired-ctrl", customer="cust-cs-expired-ctrl"),
        )
        # Past grace → identical score (NOT just identical decision —
        # asserting only on decision would pass even if grace still applied
        # because both scores stay below the REVIEW threshold).
        assert post["decision"] == control["decision"]
        assert abs(post["score"] - control["score"]) < 1e-9
    finally:
        await set_test_tenant_id(db_conn, tenant_id)
        await _cleanup_tenant(db_conn, tenant_id)
        await set_test_tenant_id(db_conn, default_tid)
        await _cleanup_tenant(db_conn, default_tid)


async def test_grace_composed_with_maturity_overrides(
    db_conn: asyncpg.Connection, unauth_client: AsyncClient
) -> None:
    """maturity_age_days=90 AND cold_start_grace_days=14, created 5 days ago.
    Customer age=180 (mature under 90-day threshold) → m_raw=1.0; grace
    halves → final m=0.5 → base_prior = 0.10 * (1 - 0.5) = 0.05.

    Compares against a control tenant with same overrides but NO grace
    (grace=0) — the grace tenant's score must be strictly greater. A
    `> 0.0` assertion alone would pass even if grace did nothing because
    new-customer base_prior is non-zero by default."""
    grace_tid = await seed_tenant_created_days_ago(
        db_conn,
        days_ago=5,
        config={"maturity_age_days": 90, "cold_start_grace_days": 14},
    )
    # Control: same maturity_age_days override but no grace.
    control_tid = await seed_tenant_created_days_ago(
        db_conn,
        days_ago=5,
        config={"maturity_age_days": 90, "cold_start_grace_days": 0},
    )
    try:
        await set_test_tenant_id(db_conn, grace_tid)
        await _seed_mature_customer(db_conn, grace_tid, "cust-cs-compose")
        await set_test_tenant_id(db_conn, control_tid)
        await _seed_mature_customer(db_conn, control_tid, "cust-cs-compose-ctrl")
        grace_resp = await _post(
            unauth_client,
            grace_tid,
            _booking(request_id="REQ-cs-compose", customer="cust-cs-compose"),
        )
        control_resp = await _post(
            unauth_client,
            control_tid,
            _booking(request_id="REQ-cs-compose-ctrl", customer="cust-cs-compose-ctrl"),
        )
        assert grace_resp["decision"] == "ALLOW"
        # Grace tenant's score is strictly higher than the control (no grace).
        assert grace_resp["score"] > control_resp["score"]
    finally:
        await set_test_tenant_id(db_conn, grace_tid)
        await _cleanup_tenant(db_conn, grace_tid)
        await set_test_tenant_id(db_conn, control_tid)
        await _cleanup_tenant(db_conn, control_tid)


async def test_grace_does_not_affect_layer_1_block(
    db_conn: asyncpg.Connection, unauth_client: AsyncClient
) -> None:
    """A BLOCK rule (`blacklisted_ip`, conditioned on FireHOL Level 1 IP)
    short-circuits Layer 1; cold-start grace should not change BLOCK
    behavior."""
    tenant_id = await seed_tenant_created_days_ago(
        db_conn, days_ago=1, config={"cold_start_grace_days": 30}
    )
    test_ip = BLACKLISTED_IP
    await db_conn.execute(
        """
        INSERT INTO ip_enrichment (ip, fh_level1, fh_level2, is_tor, country, lat, lon)
        VALUES ($1::inet, true, false, true, 'US', 38.0, -77.0)
        ON CONFLICT (ip) DO UPDATE SET fh_level1 = true, fh_level2 = false, is_tor = true
        """,
        test_ip,
    )
    try:
        await set_test_tenant_id(db_conn, tenant_id)
        await _seed_mature_customer(db_conn, tenant_id, "cust-cs-l1")
        resp = await _post(
            unauth_client,
            tenant_id,
            _booking(request_id="REQ-cs-l1", customer="cust-cs-l1", source_ip=test_ip),
        )
    finally:
        await set_test_tenant_id(db_conn, tenant_id)
        await _cleanup_tenant(db_conn, tenant_id)
        await db_conn.execute("DELETE FROM ip_enrichment WHERE ip = $1::inet", test_ip)
    assert resp["decision"] == "BLOCK"
    assert resp["score"] == 1.0
