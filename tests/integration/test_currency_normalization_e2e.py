"""End-to-end currency normalization tests (4B.6).

Verifies:
1. USD-default tenants score identically pre/post 4B.5 (the regression
   invariance is also covered by the existing case-1/case-2 tests).
2. Multi-currency tenants with calibrated value_caps produce
   currency-correct rule firing.
3. Cross-tenant currency drift — each request loads its own tenant's
   value_caps.
4. Modification rule 1 (modification_within_30_min_value_increase) is
   currency-independent (uses modification_magnitude, a fraction).
5. Allowed-but-unconfigured currency falls back to USD-default with a
   warning.

NOTE: case-1 + case-2 regression assertions live in
tests/integration/test_case_1_detection.py and tests/integration/
test_case_2.py — those exercise USD-default tenants and the explicit
regression check is verified via the full-suite pass at the end of 4B.5.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import asyncpg
from httpx import AsyncClient

from app.auth import AuthContext, require_api_token
from app.main import app
from tests.conftest import _cleanup_tenant


async def _set_tenant_config(
    db_conn: asyncpg.Connection, tenant_id: int, config: dict[str, object]
) -> None:
    await db_conn.execute(
        "UPDATE tenants SET config = $1::jsonb WHERE id = $2",
        json.dumps(config),
        tenant_id,
    )


def _booking(
    *,
    request_id: str,
    value: str,
    currency: str = "USD",
    customer: str = "cust-cn",
    user: str = "user-cn",
    source_ip: str = "192.0.2.70",
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "customer": {"external_id": customer},
        "user": {"external_id": user},
        "source_ip": source_ip,
        "shipment": {
            "origin": {"address": "1 Main St"},
            "destination": {"address": "2 Park Ave"},
            "value": value,
            "channel": "web",
            "currency": currency,
        },
        "booking_ts": datetime.now(UTC).isoformat(),
    }


async def _post_booking(
    client: AsyncClient, tenant_id: int, payload: dict[str, object]
) -> dict[str, object]:
    app.dependency_overrides[require_api_token] = lambda: AuthContext(
        tenant_id=tenant_id, role="tenant"
    )
    try:
        r = await client.post("/api/v1/shipments/booking/evaluate", json=payload)
    finally:
        app.dependency_overrides.pop(require_api_token, None)
    assert r.status_code == 200, f"booking failed: {r.status_code} {r.text}"
    return r.json()


async def test_usd_high_value_fires_absolute_high_value(
    seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """USD-default tenant + value=15000 → absolute_high_value fires (threshold 10000)."""
    resp = await _post_booking(
        unauth_client,
        seeded_tenant,
        _booking(request_id="REQ-cn-usd-hi", value="15000", currency="USD"),
    )
    assert "absolute_high_value" in resp["triggered_rules"]


async def test_usd_below_threshold_does_not_fire_absolute_high_value(
    seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """USD value=8000 < threshold 10000 → absolute_high_value silent."""
    resp = await _post_booking(
        unauth_client,
        seeded_tenant,
        _booking(request_id="REQ-cn-usd-lo", value="8000", currency="USD"),
    )
    assert "absolute_high_value" not in resp["triggered_rules"]


async def test_cad_tenant_with_calibrated_caps_fires_at_correct_threshold(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """CAD tenant calibrated high=12500. Value=12600 fires; value=12000 does not."""
    await _set_tenant_config(
        db_conn,
        seeded_tenant,
        {
            "allowed_currencies": ["USD", "CAD"],
            "value_caps": {
                "CAD": {"high": 12500, "new_user": 6250, "medium": 2500, "low": 1250},
            },
        },
    )
    above = await _post_booking(
        unauth_client,
        seeded_tenant,
        _booking(request_id="REQ-cn-cad-hi", value="12600", currency="CAD"),
    )
    assert "absolute_high_value" in above["triggered_rules"]
    below = await _post_booking(
        unauth_client,
        seeded_tenant,
        _booking(request_id="REQ-cn-cad-lo", value="12000", currency="CAD"),
    )
    assert "absolute_high_value" not in below["triggered_rules"]


async def test_cad_value_above_usd_high_below_cad_high_does_not_fire(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """CAD calibrated to high=12500. Value=10500 (above USD threshold 10000
    but below CAD threshold 12500) → absolute_high_value does NOT fire."""
    await _set_tenant_config(
        db_conn,
        seeded_tenant,
        {
            "allowed_currencies": ["USD", "CAD"],
            "value_caps": {
                "CAD": {"high": 12500, "new_user": 6250, "medium": 2500, "low": 1250},
            },
        },
    )
    resp = await _post_booking(
        unauth_client,
        seeded_tenant,
        _booking(request_id="REQ-cn-cad-cross", value="10500", currency="CAD"),
    )
    assert "absolute_high_value" not in resp["triggered_rules"]


async def test_cross_tenant_value_caps_isolation(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """tenant_a (USD-default, high=10000) and tenant_b (USD-elevated,
    high=50000) on identical 11000-value bookings: tenant_a fires
    absolute_high_value, tenant_b does NOT. Confirms per-request load."""
    # tenant_b with USD overridden upward
    other_tenant_id: int = await db_conn.fetchval(
        "INSERT INTO tenants (name, config) VALUES ($1, $2::jsonb) RETURNING id",
        "tenant-b-cn",
        json.dumps(
            {
                "value_caps": {
                    "USD": {"high": 50000, "new_user": 25000, "medium": 10000, "low": 5000}
                }
            }
        ),
    )
    try:
        a = await _post_booking(
            unauth_client,
            seeded_tenant,
            _booking(request_id="REQ-cn-iso-a", value="11000", currency="USD"),
        )
        b = await _post_booking(
            unauth_client,
            other_tenant_id,
            _booking(
                request_id="REQ-cn-iso-b",
                value="11000",
                currency="USD",
                customer="cust-iso-b",
            ),
        )
    finally:
        await _cleanup_tenant(db_conn, other_tenant_id)
    assert "absolute_high_value" in a["triggered_rules"]
    assert "absolute_high_value" not in b["triggered_rules"]


async def test_allowed_currency_without_caps_falls_back_to_usd_default_with_warning(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """Tenant allows JPY but didn't configure value_caps["JPY"]. Resolver falls
    back to USD-default thresholds AND emits the
    `tenant_config.value_caps.fallback` warning."""
    await _set_tenant_config(db_conn, seeded_tenant, {"allowed_currencies": ["USD", "JPY"]})
    with patch("app.tenant_config._log") as mock_log:
        resp = await _post_booking(
            unauth_client,
            seeded_tenant,
            _booking(request_id="REQ-cn-jpy-fallback", value="15000", currency="JPY"),
        )
    assert "absolute_high_value" in resp["triggered_rules"]
    # Warning must fire so the misconfig is observable to operators.
    mock_log.warning.assert_called_once()
    args = mock_log.warning.call_args
    assert args.args[0] == "tenant_config.value_caps.fallback"
    assert args.kwargs["currency"] == "JPY"
    assert args.kwargs["metric"] is True


async def test_multi_rule_composition_usd_at_2500(
    seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """At shipment_value=2500 USD under default thresholds (high=10000,
    medium=2000), absolute_high_value does NOT fire but
    threat_intel_high_value would (with threat-list IP). Pins per-tier
    independence — the rewrite mustn't conflate _high with _medium."""
    # Use a non-threat IP so threat_intel doesn't muddy the assertion.
    resp = await _post_booking(
        unauth_client,
        seeded_tenant,
        _booking(request_id="REQ-cn-mcomp-1", value="2500", currency="USD"),
    )
    # absolute_high_value uses _high (10000) — must NOT fire at 2500.
    assert "absolute_high_value" not in resp["triggered_rules"], (
        "absolute_high_value must consult _high (10000), not _medium (2000) — "
        "regression in 4B.5 rule rewrite"
    )


async def test_threat_intel_high_value_uses_medium_threshold_per_currency(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """USD-default + value=2500 + ip in threat list → threat_intel_high_value
    fires (medium=2000). Same value in CAD-calibrated tenant (medium=2500) does
    NOT fire."""
    # Seed an enrichment row marking the IP as FireHOL Level 1 so it shows
    # up in ip_in_threat_list. blacklisted_ip is a BLOCK rule so Layer 1
    # would short-circuit — use FireHOL Level 2 instead to keep evaluation
    # on the Layer 3 path.
    test_ip = "192.0.2.71"
    await db_conn.execute(
        """
        INSERT INTO ip_enrichment (ip, fh_level1, fh_level2, country, lat, lon)
        VALUES ($1::inet, false, true, 'US', 38.0, -77.0)
        ON CONFLICT (ip) DO UPDATE SET fh_level1 = false, fh_level2 = true
        """,
        test_ip,
    )
    try:
        # USD default tenant — fires.
        usd = await _post_booking(
            unauth_client,
            seeded_tenant,
            _booking(
                request_id="REQ-cn-ti-usd",
                value="2500",
                currency="USD",
                source_ip=test_ip,
            ),
        )
        assert "threat_intel_high_value" in usd["triggered_rules"]

        # CAD-calibrated medium=2500 — does NOT fire (>, strict).
        await _set_tenant_config(
            db_conn,
            seeded_tenant,
            {
                "allowed_currencies": ["USD", "CAD"],
                "value_caps": {
                    "CAD": {"high": 12500, "new_user": 6250, "medium": 2500, "low": 1250},
                },
            },
        )
        cad = await _post_booking(
            unauth_client,
            seeded_tenant,
            _booking(
                request_id="REQ-cn-ti-cad",
                value="2500",
                currency="CAD",
                source_ip=test_ip,
            ),
        )
        assert "threat_intel_high_value" not in cad["triggered_rules"]
    finally:
        await db_conn.execute("DELETE FROM ip_enrichment WHERE ip = $1::inet", test_ip)


async def test_modification_rule_1_currency_independent(
    db_conn: asyncpg.Connection, seeded_tenant: int, unauth_client: AsyncClient
) -> None:
    """modification_within_30_min_value_increase uses modification_magnitude
    (fraction), NOT shipment_value. Currency does NOT affect firing."""
    await _set_tenant_config(db_conn, seeded_tenant, {"allowed_currencies": ["USD", "CAD"]})
    # Prior booking USD value=1000.
    await _post_booking(
        unauth_client,
        seeded_tenant,
        _booking(request_id="REQ-cn-mod-prior", value="1000", currency="USD"),
    )

    # Modification: same booking, value increased to 1500 (magnitude = 0.5),
    # within 30 min. Modification rule 1 should fire regardless of currency.
    for mod_currency in ("USD", "CAD"):
        app.dependency_overrides[require_api_token] = lambda: AuthContext(
            tenant_id=seeded_tenant, role="tenant"
        )
        try:
            mod_resp = await unauth_client.post(
                "/api/v1/shipments/modification/evaluate",
                json={
                    "request_id": f"MOD-cn-{mod_currency}",
                    "original_request_id": "REQ-cn-mod-prior",
                    "modification_ts": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                    "modification_type": "value",
                    "new_value": {"value": 1500},
                    "currency": mod_currency,
                },
            )
        finally:
            app.dependency_overrides.pop(require_api_token, None)
        assert mod_resp.status_code == 200
        assert (
            "modification_within_30_min_value_increase" in mod_resp.json()["triggered_rules"]
        ), f"Rule must fire for {mod_currency} (magnitude-based, currency-independent)"
