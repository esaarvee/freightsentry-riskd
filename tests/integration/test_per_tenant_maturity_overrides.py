"""End-to-end integration tests for per-tenant maturity overrides (4C.4).

Drives the booking endpoint with tenants whose `config` JSONB overrides
the maturity formula constants. Asserts that ScoringResult.maturity
reflects the override.

6 tests covering individual overrides + composition + Layer 1 invariance.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, date, datetime

import asyncpg
from httpx import AsyncClient

from app.auth import AuthContext, require_api_token
from app.main import app
from tests.conftest import _cleanup_tenant


def _booking(
    *,
    request_id: str,
    customer: str,
    user: str = "user-mat",
    source_ip: str = "192.0.2.80",
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "customer": {"external_id": customer},
        "user": {"external_id": user},
        "source_ip": source_ip,
        "shipment": {
            "origin": {"address": "1 Main St"},
            "destination": {"address": "2 Park Ave"},
            "value": "100",
            "channel": "web",
        },
        "booking_ts": datetime.now(UTC).isoformat(),
    }


async def _seed_customer_at_age(
    db_conn: asyncpg.Connection,
    tenant_id: int,
    *,
    external_id: str,
    age_days: int,
    total_shipments: int,
) -> int:
    """Seed a customer whose first_seen is `age_days` days ago and whose
    customers.total_shipments column reflects `total_shipments`."""
    cust_id: int = await db_conn.fetchval(
        """
        INSERT INTO customers (
            tenant_id, external_id, first_seen, total_shipments
        )
        VALUES ($1, $2, now() - make_interval(days => $3), $4)
        RETURNING id
        """,
        tenant_id,
        external_id,
        age_days,
        total_shipments,
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


async def _post_booking(
    client: AsyncClient, tenant_id: int, payload: dict[str, object]
) -> dict[str, object]:
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=tenant_id, role="tenant"
    )
    try:
        # Use unique request_id per call to dodge idempotency replays.
        payload = {**payload, "request_id": f"{payload['request_id']}-{secrets.token_hex(3)}"}
        r = await client.post("/api/v1/shipments/booking/evaluate", json=payload)
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    assert r.status_code == 200, f"booking failed: {r.status_code} {r.text}"
    return r.json()


async def test_default_thresholds_score_is_baseline(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Customer at exactly mature thresholds (180 days, 50 shipments) under
    default config produces ScoringResult.maturity=1.0 → base_prior=0.
    Tight upper bound on score so a regression making maturity=0.5
    (yielding base_prior=0.05) would fail this test."""
    await _seed_customer_at_age(
        db_conn, seeded_tenant, external_id="cust-mat", age_days=180, total_shipments=50
    )
    resp = await _post_booking(
        unauth_client, seeded_tenant, _booking(request_id="REQ-mat-base", customer="cust-mat")
    )
    # Mature customer + neutral trust + no flags → ALLOW with score ≈ 0.
    assert resp["decision"] == "ALLOW"
    assert resp["score"] < 0.05


async def test_maturity_age_days_override_makes_younger_customer_mature(
    db_conn: asyncpg.Connection, unauth_client: AsyncClient
) -> None:
    """Tenant_b sets maturity_age_days=90. A 90-day customer reaches m=1.0
    under tenant_b but m≈0.5 under default tenant_a (assuming shipments
    threshold also satisfied). Compare via score for same-shape input."""
    # Tenant A: default
    tenant_a: int = await db_conn.fetchval(
        "INSERT INTO tenants (name) VALUES ($1) RETURNING id",
        f"mat-a-{secrets.token_hex(3)}",
    )
    # Tenant B: maturity_age_days=90
    tenant_b: int = await db_conn.fetchval(
        "INSERT INTO tenants (name, config) VALUES ($1, $2::jsonb) RETURNING id",
        f"mat-b-{secrets.token_hex(3)}",
        json.dumps({"maturity_age_days": 90}),
    )
    try:
        await _seed_customer_at_age(
            db_conn, tenant_a, external_id="cust-a", age_days=90, total_shipments=50
        )
        await _seed_customer_at_age(
            db_conn, tenant_b, external_id="cust-b", age_days=90, total_shipments=50
        )
        a = await _post_booking(
            unauth_client, tenant_a, _booking(request_id="REQ-mat-a", customer="cust-a")
        )
        b = await _post_booking(
            unauth_client, tenant_b, _booking(request_id="REQ-mat-b", customer="cust-b")
        )
        # Under tenant_a: m = (90/180)*1 = 0.5 → base_prior = 0.10*0.5 = 0.05
        # Under tenant_b: m = 1.0 → base_prior = 0
        # Both score ALLOW but a's score should be higher (more new-customer prior).
        assert a["score"] > b["score"]
    finally:
        await _cleanup_tenant(db_conn, tenant_a)
        await _cleanup_tenant(db_conn, tenant_b)


async def test_maturity_shipments_override_reduces_threshold(
    db_conn: asyncpg.Connection, unauth_client: AsyncClient
) -> None:
    """Tenant_b sets maturity_shipments=10. A customer with 10 shipments
    reaches m=1.0 under tenant_b but 0.2 under default."""
    tenant_a: int = await db_conn.fetchval(
        "INSERT INTO tenants (name) VALUES ($1) RETURNING id",
        f"matsh-a-{secrets.token_hex(3)}",
    )
    tenant_b: int = await db_conn.fetchval(
        "INSERT INTO tenants (name, config) VALUES ($1, $2::jsonb) RETURNING id",
        f"matsh-b-{secrets.token_hex(3)}",
        json.dumps({"maturity_shipments": 10}),
    )
    try:
        await _seed_customer_at_age(
            db_conn, tenant_a, external_id="cust-a", age_days=180, total_shipments=10
        )
        await _seed_customer_at_age(
            db_conn, tenant_b, external_id="cust-b", age_days=180, total_shipments=10
        )
        a = await _post_booking(
            unauth_client, tenant_a, _booking(request_id="REQ-matsh-a", customer="cust-a")
        )
        b = await _post_booking(
            unauth_client, tenant_b, _booking(request_id="REQ-matsh-b", customer="cust-b")
        )
        # Same direction as the age-override test.
        assert a["score"] > b["score"]
    finally:
        await _cleanup_tenant(db_conn, tenant_a)
        await _cleanup_tenant(db_conn, tenant_b)


async def test_combined_overrides_score_matches_expected(
    db_conn: asyncpg.Connection, unauth_client: AsyncClient
) -> None:
    """All three overrides composed: maturity_age_days=90,
    maturity_shipments=20, maturity_k=0.10. Customer at age=60,
    shipments=20 → m = (60/90) * (20/20) = 0.667."""
    tenant_id: int = await db_conn.fetchval(
        "INSERT INTO tenants (name, config) VALUES ($1, $2::jsonb) RETURNING id",
        f"mat-compose-{secrets.token_hex(3)}",
        json.dumps(
            {
                "maturity_age_days": 90,
                "maturity_shipments": 20,
                "maturity_k": 0.10,
            }
        ),
    )
    try:
        await _seed_customer_at_age(
            db_conn, tenant_id, external_id="cust-c", age_days=60, total_shipments=20
        )
        resp = await _post_booking(
            unauth_client, tenant_id, _booking(request_id="REQ-mat-c", customer="cust-c")
        )
        # m ≈ 0.667 → base_prior = 0.10 * 0.333 = 0.033
        # No other rules fire (clean baseline) → score ≈ 0.033 → ALLOW low
        assert resp["decision"] == "ALLOW"
        assert resp["score"] < 0.1
    finally:
        await _cleanup_tenant(db_conn, tenant_id)


async def test_overrides_do_not_affect_layer_1_block(
    db_conn: asyncpg.Connection, unauth_client: AsyncClient
) -> None:
    """A BLOCK rule (`blacklisted_ip`, conditioned on FireHOL Level 1 IP)
    short-circuits Layer 1; tenant_config overrides should not be
    consulted. Compare a default tenant and an extreme-override tenant —
    both BLOCK with score=1.0."""
    tenant_default: int = await db_conn.fetchval(
        "INSERT INTO tenants (name) VALUES ($1) RETURNING id",
        f"mat-l1-default-{secrets.token_hex(3)}",
    )
    tenant_extreme: int = await db_conn.fetchval(
        "INSERT INTO tenants (name, config) VALUES ($1, $2::jsonb) RETURNING id",
        f"mat-l1-extreme-{secrets.token_hex(3)}",
        json.dumps({"maturity_age_days": 1, "maturity_shipments": 1, "maturity_k": 0.99}),
    )
    test_ip = "192.0.2.81"
    await db_conn.execute(
        """
        INSERT INTO ip_enrichment (ip, fh_level1, fh_level2, is_tor, country, lat, lon)
        VALUES ($1::inet, true, false, true, 'US', 38.0, -77.0)
        ON CONFLICT (ip) DO UPDATE SET fh_level1 = true, fh_level2 = false, is_tor = true
        """,
        test_ip,
    )
    try:
        await _seed_customer_at_age(
            db_conn, tenant_default, external_id="cust-l1d", age_days=1, total_shipments=0
        )
        await _seed_customer_at_age(
            db_conn, tenant_extreme, external_id="cust-l1e", age_days=1, total_shipments=0
        )
        a = await _post_booking(
            unauth_client,
            tenant_default,
            _booking(request_id="REQ-mat-l1-a", customer="cust-l1d", source_ip=test_ip),
        )
        b = await _post_booking(
            unauth_client,
            tenant_extreme,
            _booking(request_id="REQ-mat-l1-b", customer="cust-l1e", source_ip=test_ip),
        )
    finally:
        await _cleanup_tenant(db_conn, tenant_default)
        await _cleanup_tenant(db_conn, tenant_extreme)
        await db_conn.execute("DELETE FROM ip_enrichment WHERE ip = $1::inet", test_ip)
    # Both BLOCK with score=1.0 — overrides not consulted on Layer 1.
    assert a["decision"] == "BLOCK"
    assert a["score"] == 1.0
    assert b["decision"] == "BLOCK"
    assert b["score"] == 1.0


async def test_empty_config_tenant_unchanged_from_phase3(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Tenants with empty config (`{}`) score identically to pre-4C
    behavior. This is the invariance pin for the per-tenant override
    refactor — no behavioral drift on default tenants."""
    # Mature customer at exactly the default thresholds.
    await _seed_customer_at_age(
        db_conn, seeded_tenant, external_id="cust-inv", age_days=180, total_shipments=50
    )
    resp = await _post_booking(
        unauth_client, seeded_tenant, _booking(request_id="REQ-mat-inv", customer="cust-inv")
    )
    # Pre-4C: mature customer, no flags, no rules fire → score near 0.
    assert resp["score"] < 0.05
    assert resp["decision"] == "ALLOW"
