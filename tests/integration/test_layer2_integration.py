"""Layer 2 + maturity downweight integration matrix.

These tests verify the 3-layer scoring formula composes correctly when
posted through the booking endpoint. The unit tests in
tests/unit/test_scoring_layer2.py verify the math in isolation; these
verify the same math holds at the HTTP boundary with real baseline
state, real enrichment, real customer rows.

Each test constructs a customer + baseline + ip_enrichment configuration
that isolates a specific contribution path (base prior alone, trust
amplification, flag-tier elevation, lock-in firing, maturity downweight).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
import structlog
from httpx import AsyncClient

from tests.conftest import seed_customer_with_baseline, seeded_ip_enrichment

_BOOKING_PATH = "/api/v1/shipments/booking/evaluate"

# Residential IP — no threat flags, no cloud / dc. Avoids tripping
# web_booking_from_cloud_ip and the api-channel non-cloud rules.
_CLEAN_IP = "198.51.100.50"


def _payload(
    *,
    request_id: str,
    customer_external_id: str,
    source_ip: str = _CLEAN_IP,
    channel: str = "web",
    value: float = 100.0,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "shipment_id": f"ship-{request_id}",
        "transaction_number": f"txn-{request_id}",
        "customer": {"external_id": customer_external_id},
        "user": {"external_id": f"{customer_external_id}-user"},
        "source_ip": source_ip,
        "shipment": {
            "origin": {"address": "100 Bay Street"},
            "destination": {"address": "500 5th Avenue"},
            "value": value,
            "channel": channel,
        },
        "booking_ts": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    }


async def _post_booking(client: AsyncClient, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    resp = await client.post(
        _BOOKING_PATH, json=payload, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, f"booking endpoint returned {resp.status_code}: {resp.text}"
    body: dict[str, Any] = resp.json()
    return body


# ---------------------------------------------------------------------------
# Brand-new customer with no Layer-3 signals → account_prior base alone
# ---------------------------------------------------------------------------


async def test_brand_new_customer_no_signals_lands_at_base_prior(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """A brand-new customer (no baseline) posting a web-channel booking
    from a clean residential IP at low value trips ZERO Layer-3 rules
    — the maturity-gated familiarity rules need obs >= 10, the channel
    rules need API or non-cloud, the value rules need value > 5000. So
    the final score equals account_prior's base value ≈ 0.10
    (MAX_NEW_ACCOUNT * (1 - maturity=0))."""
    token, tenant_id = seeded_api_token
    await seed_customer_with_baseline(
        db_conn,
        tenant_id,
        external_id="bn-base",
        first_seen_days_ago=0,
        total_shipments=0,
    )
    async with seeded_ip_enrichment(db_conn, _CLEAN_IP):
        with structlog.testing.capture_logs() as captured:
            body = await _post_booking(
                unauth_client,
                token,
                _payload(request_id="bn-base-1", customer_external_id="bn-base"),
            )
    event = next(e for e in captured if e.get("event") == "risk.evaluation")
    assert event["account_prior"] == pytest.approx(0.10, abs=0.01)
    assert event["signal_score"] == pytest.approx(0.0, abs=0.01)
    assert body["score"] == pytest.approx(0.10, abs=0.01)
    assert body["decision"] == "ALLOW"


# ---------------------------------------------------------------------------
# Established customer with a matching-baseline booking → score ≈ 0
# ---------------------------------------------------------------------------


async def test_established_clean_customer_returns_near_zero_score(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """An established customer (50 obs, 365 days old, 0 flags) whose
    booking matches their baseline (familiar origin + dest, familiar
    IP + /24, familiar country) produces no Layer-3 firing and the
    account_prior collapses to ~0 (maturity = 1.0)."""
    token, tenant_id = seeded_api_token
    await seed_customer_with_baseline(
        db_conn,
        tenant_id,
        external_id="est-clean",
        first_seen_days_ago=365,
        total_shipments=50,
        baseline_kwargs={
            "value_n": 50.0,
            "value_mean": 100.0,
            "value_m2": 1000.0,
            # Familiar exact IP + /24 + ASN — ip_familiarity_tier yields
            # "familiar" (not "family_familiar"), so the lower-weight
            # tier rules don't fire.
            "ip_stats": {
                _CLEAN_IP: {"n": 30.0, "r_n": 0, "last": "2026-05-20", "type": "residential"}
            },
            "ip_netblock_stats": {"198.51.100.0": {"n": 30.0, "r_n": 0, "last": "2026-05-20"}},
            "ip_asn_stats": {"Comcast": {"n": 50.0, "r_n": 0, "last": "2026-05-20"}},
            "country_stats": {"US": {"n": 50.0, "r_n": 0, "last": "2026-05-20"}},
            "origin_stats": {"100 Bay Street": {"n": 50.0, "r_n": 0, "last": "2026-05-20"}},
            "dest_stats": {"500 5th Avenue": {"n": 50.0, "r_n": 0, "last": "2026-05-20"}},
            "origin_ip_country_stats": {
                "100 Bay Street||US": {"n": 50.0, "r_n": 0, "last": "2026-05-20"}
            },
            "lane_stats": {
                "100 Bay Street||500 5th Avenue": {"n": 50.0, "r_n": 0, "last": "2026-05-20"}
            },
            "ip_type_hist": {"residential": 50.0},
            "channel_hist": {"web": 50.0},
            "last_booking_ts": datetime.now(tz=UTC) - timedelta(hours=12),
            "last_booking_country": "US",
        },
    )
    async with seeded_ip_enrichment(db_conn, _CLEAN_IP):
        with structlog.testing.capture_logs() as captured:
            body = await _post_booking(
                unauth_client,
                token,
                _payload(request_id="est-clean-1", customer_external_id="est-clean"),
            )
    event = next(e for e in captured if e.get("event") == "risk.evaluation")
    assert event["account_prior"] == pytest.approx(0.0, abs=0.01)
    assert body["score"] < 0.05
    assert body["decision"] == "ALLOW"
    assert body["triggered_rules"] == [], (
        f"established clean customer triggered rules: {body['triggered_rules']}"
    )


# ---------------------------------------------------------------------------
# Flagged customer (tier 2 = 3-5 flags) → flag_prior 0.25 dominates
# ---------------------------------------------------------------------------


async def test_flagged_tier_2_customer_elevates_account_prior(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Established customer with flagged_count=4 (tier 2 → 0.25) AND a
    clean booking → account_prior should be dominated by flag_prior.
    Verifies the flag-tier table lookup composes through the endpoint."""
    token, tenant_id = seeded_api_token
    await seed_customer_with_baseline(
        db_conn,
        tenant_id,
        external_id="flagged-t2",
        first_seen_days_ago=365,
        total_shipments=50,
        flagged_count=4,
        baseline_kwargs={
            "value_n": 50.0,
            "ip_stats": {
                _CLEAN_IP: {"n": 50.0, "r_n": 0, "last": "2026-05-20", "type": "residential"}
            },
            "ip_netblock_stats": {"198.51.100.0": {"n": 50.0, "r_n": 0, "last": "2026-05-20"}},
            "ip_asn_stats": {"Comcast": {"n": 50.0, "r_n": 0, "last": "2026-05-20"}},
            "country_stats": {"US": {"n": 50.0, "r_n": 0, "last": "2026-05-20"}},
            "origin_stats": {"100 Bay Street": {"n": 50.0, "r_n": 0, "last": "2026-05-20"}},
            "dest_stats": {"500 5th Avenue": {"n": 50.0, "r_n": 0, "last": "2026-05-20"}},
            "origin_ip_country_stats": {
                "100 Bay Street||US": {"n": 50.0, "r_n": 0, "last": "2026-05-20"}
            },
            "lane_stats": {
                "100 Bay Street||500 5th Avenue": {"n": 50.0, "r_n": 0, "last": "2026-05-20"}
            },
            "ip_type_hist": {"residential": 50.0},
            "channel_hist": {"web": 50.0},
            "last_booking_ts": datetime.now(tz=UTC) - timedelta(hours=12),
            "last_booking_country": "US",
        },
    )
    async with seeded_ip_enrichment(db_conn, _CLEAN_IP):
        with structlog.testing.capture_logs() as captured:
            body = await _post_booking(
                unauth_client,
                token,
                _payload(request_id="flagged-t2-1", customer_external_id="flagged-t2"),
            )
    event = next(e for e in captured if e.get("event") == "risk.evaluation")
    # An established customer with 50 obs + 365 days + 4 flags still
    # has trust_score ≈ 0.585 (the age + obs sigmoids offset the
    # -0.4 flag penalty). So trust_risk = max(0, (0.5 - 0.585)/0.5)
    # = 0 → no trust_contribution. Mature → base_prior = 0. Only
    # flag_prior (tier 2 = 0.25) contributes.
    assert event["account_prior"] == pytest.approx(0.25, abs=0.02)
    assert body["score"] >= 0.25


# ---------------------------------------------------------------------------
# Brand-new + one flag → trust_contribution + flag_prior + base_prior compose
# ---------------------------------------------------------------------------


async def test_brand_new_with_one_flag_compounds_via_trust_and_flag_prior(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """A brand-new customer (no obs, age 0) with one prior flag —
    trust drops to ~0.16 via the -0.4 flag penalty in compute_trust_score.
    Layer 2 then composes: base_prior=0.10, trust_contribution≈0.17
    (trust_risk=0.68 * 0.25), flag_prior=0.15 (tier 1). The
    account_prior should be noisyOR of all three."""
    token, tenant_id = seeded_api_token
    await seed_customer_with_baseline(
        db_conn,
        tenant_id,
        external_id="bn-flag1",
        first_seen_days_ago=0,
        total_shipments=0,
        flagged_count=1,
    )
    async with seeded_ip_enrichment(db_conn, _CLEAN_IP):
        with structlog.testing.capture_logs() as captured:
            body = await _post_booking(
                unauth_client,
                token,
                _payload(request_id="bn-flag1-1", customer_external_id="bn-flag1"),
            )
    event = next(e for e in captured if e.get("event") == "risk.evaluation")
    # account_prior ≈ noisyOR(0.10, 0.17, 0.15) = 1 - 0.9*0.83*0.85 ≈ 0.365.
    # Loose tolerance because trust_score derivation has multiple
    # sigmoid contributions that depend on the exact age/obs values.
    assert 0.30 <= event["account_prior"] <= 0.45, (
        f"account_prior {event['account_prior']:.3f} outside expected "
        f"0.30-0.45 band for brand-new + 1-flag customer"
    )
    assert body["decision"] in ("ALLOW", "REVIEW")


# ---------------------------------------------------------------------------
# Lock-in rule fires only against a locked customer + non-cloud + API
# ---------------------------------------------------------------------------


async def test_lock_in_rule_fires_against_locked_customer_non_cloud_api(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """A customer with cloud_share > 0.95 AND api_share > 0.95 AND
    value_n >= 20 is "locked" to cloud-API infrastructure. A subsequent
    API booking from a non-cloud, non-datacenter IP should trip
    cloud_api_customer_deviation_iptype (the 5-clause case-2 detector)."""
    token, tenant_id = seeded_api_token
    non_cloud_ip = "198.51.100.77"
    await seed_customer_with_baseline(
        db_conn,
        tenant_id,
        external_id="locked-cust",
        first_seen_days_ago=365,
        total_shipments=50,
        baseline_kwargs={
            "value_n": 50.0,
            "value_mean": 100.0,
            "value_m2": 100.0,
            "ip_stats": {
                "35.190.0.1": {"n": 50.0, "r_n": 0, "last": "2026-05-25", "type": "cloud"}
            },
            "ip_netblock_stats": {"35.190.0.0": {"n": 50.0, "r_n": 0, "last": "2026-05-25"}},
            "ip_asn_stats": {"GOOGLE": {"n": 50.0, "r_n": 0, "last": "2026-05-25"}},
            "ip_type_hist": {"cloud": 50.0},
            "channel_hist": {"api": 50.0},
            "country_stats": {"US": {"n": 50.0, "r_n": 0, "last": "2026-05-25"}},
            "last_booking_ts": datetime.now(tz=UTC) - timedelta(hours=12),
            "last_booking_country": "US",
        },
    )
    async with seeded_ip_enrichment(db_conn, non_cloud_ip, country="US", asn_org="Comcast"):
        body = await _post_booking(
            unauth_client,
            token,
            _payload(
                request_id="locked-1",
                customer_external_id="locked-cust",
                source_ip=non_cloud_ip,
                channel="api",
                value=100.0,
            ),
        )
    assert "cloud_api_customer_deviation_iptype" in body["triggered_rules"], (
        f"lock-in rule did not fire against a locked customer with "
        f"non-cloud API booking. triggered_rules={body['triggered_rules']}"
    )


# ---------------------------------------------------------------------------
# Lock-in rule is silent when ONLY the lock-in flag is False
# (isolates the customer_locked_cloud_api gate as load-bearing)
# ---------------------------------------------------------------------------


async def test_lock_in_rule_silent_when_only_lock_flag_false(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Customer satisfies the obs>=20 + non-cloud + api gates but does
    NOT satisfy customer_locked_cloud_api (channel_hist is mixed, so
    api_share < 0.95). The other clauses are held positive; only the
    lock-in flag is False. This isolates the gate as load-bearing —
    cycle-1 review caught that the prior version of this test had
    obs<20 AS WELL, so the negative result had two independent
    causes."""
    token, tenant_id = seeded_api_token
    non_cloud_ip = "198.51.100.88"
    # 25 observations (satisfies obs >= 20) + mixed channel
    # (53% api → api_share = 0.53 < 0.95 → not locked).
    await seed_customer_with_baseline(
        db_conn,
        tenant_id,
        external_id="unlocked-cust",
        first_seen_days_ago=180,
        total_shipments=25,
        baseline_kwargs={
            "value_n": 25.0,
            "ip_stats": {
                "35.190.0.1": {"n": 25.0, "r_n": 0, "last": "2026-05-25", "type": "cloud"}
            },
            "ip_type_hist": {"cloud": 25.0},
            "channel_hist": {"api": 13.0, "web": 12.0},  # 52% api — not > 0.95
            "last_booking_ts": datetime.now(tz=UTC) - timedelta(hours=12),
        },
    )
    async with seeded_ip_enrichment(db_conn, non_cloud_ip, country="US", asn_org="Comcast"):
        body = await _post_booking(
            unauth_client,
            token,
            _payload(
                request_id="unlocked-1",
                customer_external_id="unlocked-cust",
                source_ip=non_cloud_ip,
                channel="api",
                value=100.0,
            ),
        )
    assert "cloud_api_customer_deviation_iptype" not in body["triggered_rules"], (
        f"lock-in rule fired against a non-locked customer with obs>=20 — "
        f"the customer_locked_cloud_api gate is broken. "
        f"triggered_rules={body['triggered_rules']}"
    )


# ---------------------------------------------------------------------------
# Maturity exposes via Layer 2: account_prior collapses for mature customer
# ---------------------------------------------------------------------------


async def test_maturity_collapses_account_prior_for_mature_customer(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Post the same VPN payload against two customers: (a) brand-new
    (maturity=0) and (b) mature (maturity=1). At Layer 2 the
    `base_prior = MAX_NEW_ACCOUNT * (1 - maturity)` term collapses to 0
    for the mature customer. Verifies the Layer-2 maturity collapse end-
    to-end; the Layer-3 per-rule downweight is exhaustively unit-tested
    in tests/unit/test_scoring_layer2.py and is harder to isolate at the
    integration level because the brand-new vs mature contexts differ in
    `is_new_user` which adds rules unique to the new customer."""
    token, tenant_id = seeded_api_token
    vpn_ip = "192.0.2.99"

    await seed_customer_with_baseline(
        db_conn,
        tenant_id,
        external_id="dw-brand-new",
        first_seen_days_ago=0,
        total_shipments=0,
    )
    await seed_customer_with_baseline(
        db_conn,
        tenant_id,
        external_id="dw-mature",
        first_seen_days_ago=365,
        total_shipments=50,
        baseline_kwargs={
            "value_n": 50.0,
            "ip_stats": {
                "35.190.0.1": {"n": 50.0, "r_n": 0, "last": "2026-05-25", "type": "cloud"}
            },
            "ip_type_hist": {"cloud": 50.0},
            "channel_hist": {"web": 50.0},
            "last_booking_ts": datetime.now(tz=UTC) - timedelta(hours=12),
        },
    )
    async with seeded_ip_enrichment(
        db_conn, vpn_ip, country="NL", asn_org="NordVPN", is_vpn=True, lat=52.3, lon=4.9
    ):
        with structlog.testing.capture_logs() as captured_new:
            await _post_booking(
                unauth_client,
                token,
                _payload(
                    request_id="dw-brand-new-1",
                    customer_external_id="dw-brand-new",
                    source_ip=vpn_ip,
                    value=2500.0,
                ),
            )
        with structlog.testing.capture_logs() as captured_mat:
            await _post_booking(
                unauth_client,
                token,
                _payload(
                    request_id="dw-mature-1",
                    customer_external_id="dw-mature",
                    source_ip=vpn_ip,
                    value=2500.0,
                ),
            )
    event_new = next(e for e in captured_new if e.get("event") == "risk.evaluation")
    event_mat = next(e for e in captured_mat if e.get("event") == "risk.evaluation")

    assert event_new["maturity"] == pytest.approx(0.0, abs=0.01)
    assert event_mat["maturity"] == pytest.approx(1.0, abs=0.01)

    # The Layer-2 maturity collapse: account_prior > 0 for brand-new,
    # ≈ 0 for mature. Both customers receive the same VPN-based payload
    # so Layer-3 firings overlap; the load-bearing observable difference
    # is the Layer-2 account_prior collapse.
    assert event_new["account_prior"] > 0.05
    assert event_mat["account_prior"] == pytest.approx(0.0, abs=0.02)


# ---------------------------------------------------------------------------
# customer_locked_cloud_api flips at the 20-observation boundary
# (replaces the prior tautological smoke test with a real check)
# ---------------------------------------------------------------------------


async def test_customer_locked_cloud_api_flips_at_observation_threshold(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """customer_locked_cloud_api requires `value_n >= 20`. A customer
    with cloud_share=1, api_share=1, value_n=19 → NOT locked. Same
    customer profile with value_n=20 → locked. Exercises the strict
    inequality directly through the booking endpoint."""
    token, tenant_id = seeded_api_token
    non_cloud_ip = "198.51.100.66"

    def _baseline(value_n: float) -> dict[str, Any]:
        return {
            "value_n": value_n,
            "ip_stats": {
                "35.190.0.1": {"n": value_n, "r_n": 0, "last": "2026-05-25", "type": "cloud"}
            },
            "ip_type_hist": {"cloud": value_n},
            "channel_hist": {"api": value_n},
            "last_booking_ts": datetime.now(tz=UTC) - timedelta(hours=12),
        }

    await seed_customer_with_baseline(
        db_conn,
        tenant_id,
        external_id="lock-edge-19",
        first_seen_days_ago=365,
        total_shipments=19,
        baseline_kwargs=_baseline(19.0),
    )
    await seed_customer_with_baseline(
        db_conn,
        tenant_id,
        external_id="lock-edge-20",
        first_seen_days_ago=365,
        total_shipments=20,
        baseline_kwargs=_baseline(20.0),
    )

    async with seeded_ip_enrichment(db_conn, non_cloud_ip, country="US", asn_org="Comcast"):
        body_19 = await _post_booking(
            unauth_client,
            token,
            _payload(
                request_id="lock-edge-19-1",
                customer_external_id="lock-edge-19",
                source_ip=non_cloud_ip,
                channel="api",
                value=100.0,
            ),
        )
        body_20 = await _post_booking(
            unauth_client,
            token,
            _payload(
                request_id="lock-edge-20-1",
                customer_external_id="lock-edge-20",
                source_ip=non_cloud_ip,
                channel="api",
                value=100.0,
            ),
        )

    # value_n=19 below the threshold — the lock-in flag is False, so
    # the deviation rule should NOT fire.
    assert "cloud_api_customer_deviation_iptype" not in body_19["triggered_rules"]
    # value_n=20 at the threshold (>= 20) — flag flips to True, rule fires.
    assert "cloud_api_customer_deviation_iptype" in body_20["triggered_rules"]
