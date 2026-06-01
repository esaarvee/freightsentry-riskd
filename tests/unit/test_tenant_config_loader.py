"""Unit tests for load_tenant_config (4A.2).

8 tests covering:
- Empty config tenant (default JSONB '{}') → overrides None, defaults applied
- Custom partial override (maturity_age_days only) → that field set, others None
- Full override config → all fields populated
- Non-existent tenant_id → LookupError
- Invalid stored JSONB shape → pydantic.ValidationError
- JSONB returned as TEXT (asyncpg codec case A) → parses correctly
- JSONB returned as dict (asyncpg codec case B) → parses correctly
- created_at < updated_at (post-creation update) → both populated

Tests touch the DB via the `db_conn` + `seeded_tenant` fixtures.
"""

from __future__ import annotations

import json

import asyncpg
import pytest
from pydantic import ValidationError

from app.tenant_config import load_tenant_config


async def _seed_config(conn: asyncpg.Connection, tenant_id: int, config: dict[str, object]) -> None:
    await conn.execute(
        "UPDATE tenants SET config = $1::jsonb WHERE id = $2",
        json.dumps(config),
        tenant_id,
    )


async def test_empty_config_returns_defaults(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    tc = await load_tenant_config(db_conn, seeded_tenant)
    assert tc.tenant_id == seeded_tenant
    assert tc.config_version == 0
    assert tc.maturity_age_days is None
    assert tc.maturity_shipments is None
    assert tc.maturity_k is None
    assert tc.value_caps is None
    assert tc.allowed_currencies == ["USD"]
    assert tc.cold_start_grace_days == 0
    # Timestamps are timezone-aware so downstream scoring can subtract from
    # datetime.now(UTC) without naive/aware mismatch.
    assert tc.created_at.tzinfo is not None
    assert tc.updated_at.tzinfo is not None


async def test_partial_override_only_target_field_set(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    await _seed_config(db_conn, seeded_tenant, {"maturity_age_days": 90})
    tc = await load_tenant_config(db_conn, seeded_tenant)
    assert tc.maturity_age_days == 90
    assert tc.maturity_shipments is None
    assert tc.maturity_k is None


async def test_full_override_config_loaded(db_conn: asyncpg.Connection, seeded_tenant: int) -> None:
    config = {
        "config_version": 7,
        "maturity_age_days": 90,
        "maturity_shipments": 20,
        "maturity_k": 0.20,
        "value_caps": {
            "USD": {"high": 10000, "new_user": 5000, "medium": 2000, "low": 1000},
            "CAD": {"high": 12500, "new_user": 6250, "medium": 2500, "low": 1250},
        },
        "allowed_currencies": ["USD", "CAD"],
        "cold_start_grace_days": 14,
    }
    await _seed_config(db_conn, seeded_tenant, config)
    tc = await load_tenant_config(db_conn, seeded_tenant)
    assert tc.config_version == 7
    assert tc.maturity_age_days == 90
    assert tc.maturity_shipments == 20
    assert tc.maturity_k == 0.20
    assert tc.value_caps is not None
    assert tc.value_caps["CAD"]["high"] == 12500
    assert tc.allowed_currencies == ["USD", "CAD"]
    assert tc.cold_start_grace_days == 14


async def test_nonexistent_tenant_raises_lookuperror(
    db_conn: asyncpg.Connection,
) -> None:
    with pytest.raises(LookupError):
        await load_tenant_config(db_conn, 9_999_999)


async def test_invalid_stored_jsonb_raises_validationerror(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    # value_caps missing required tier keys — stored-data corruption case.
    # match= scopes the assertion to the expected validator firing, not any
    # ValidationError from an unrelated field.
    await _seed_config(db_conn, seeded_tenant, {"value_caps": {"USD": {"high": 10000}}})
    with pytest.raises(ValidationError, match="value_caps"):
        await load_tenant_config(db_conn, seeded_tenant)


async def test_jsonb_string_codec_path(db_conn: asyncpg.Connection, seeded_tenant: int) -> None:
    # Default asyncpg codec returns JSONB as str — load_tenant_config's
    # json.loads branch handles it.
    await _seed_config(db_conn, seeded_tenant, {"cold_start_grace_days": 5})
    tc = await load_tenant_config(db_conn, seeded_tenant)
    assert tc.cold_start_grace_days == 5


async def test_jsonb_dict_codec_path(db_conn: asyncpg.Connection, seeded_tenant: int) -> None:
    # When a JSONB codec IS registered (e.g., a future perf optimization
    # using set_type_codec(decoder=json.loads, schema="pg_catalog")),
    # asyncpg returns JSONB as a Python dict directly. Register the codec
    # for this connection and verify load_tenant_config's else-branch
    # handles the dict path. Codec is unregistered on test exit so other
    # tests see the default str-codec behavior.
    await db_conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    try:
        await _seed_config(db_conn, seeded_tenant, {"cold_start_grace_days": 10})
        tc = await load_tenant_config(db_conn, seeded_tenant)
        assert tc.cold_start_grace_days == 10
    finally:
        await db_conn.reset_type_codec("jsonb", schema="pg_catalog")


async def test_updated_at_reflects_subsequent_change(
    db_conn: asyncpg.Connection, seeded_tenant: int
) -> None:
    # Initial load — created_at and updated_at are very close (both default to now()).
    tc1 = await load_tenant_config(db_conn, seeded_tenant)
    initial_updated_at = tc1.updated_at

    # Post-creation update bumps tenants.updated_at.
    await db_conn.execute(
        "UPDATE tenants SET config = '{}'::jsonb, updated_at = now() + interval '1 hour' WHERE id = $1",
        seeded_tenant,
    )
    tc2 = await load_tenant_config(db_conn, seeded_tenant)
    assert tc2.updated_at > initial_updated_at
    assert tc2.created_at == tc1.created_at  # created_at unchanged
