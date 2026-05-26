"""Integration tests for app/enrich.py — covers cache hit/miss/stale + the
ip_enrichment table round-trip. Source-file lookups (MaxMind, FireHOL,
IP2Proxy, cloud CIDRs) are exercised by 1D.8 case-2 fixture replay; the
unit cost of mocking each binary format here would dwarf the value.
"""

from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from pathlib import Path

import asyncpg
import pytest

from app.enrich import Enricher, EnrichmentRow


@pytest.fixture
def empty_enricher(tmp_path: Path) -> Enricher:
    """Enricher pointed at an empty data dir — all sources missing.
    Useful for cache-mechanism tests where lookups should return
    EnrichmentRow with default (None / False) fields."""
    return Enricher(data_dir=tmp_path)


async def _delete_ip(conn: asyncpg.Connection, ip: str) -> None:
    await conn.execute("DELETE FROM ip_enrichment WHERE ip = $1::inet", ip)


async def test_cache_miss_persists_empty_row(
    db_conn: asyncpg.Connection, empty_enricher: Enricher
) -> None:
    """First lookup with no sources loaded persists a default EnrichmentRow
    (all None/False fields). Subsequent lookup returns the cached row."""
    ip = IPv4Address("198.51.100.1")
    try:
        row = await empty_enricher.enrich(db_conn, ip)
        assert row.ip == str(ip)
        assert row.country is None
        assert row.is_cloud is False
        assert row.is_proxy is False

        cached = await db_conn.fetchrow(
            "SELECT ip, is_cloud, is_proxy, updated_at FROM ip_enrichment WHERE ip = $1::inet",
            str(ip),
        )
        assert cached is not None
        assert cached["is_cloud"] is False
        assert cached["is_proxy"] is False
    finally:
        await _delete_ip(db_conn, str(ip))


async def test_cache_hit_returns_persisted_row(
    db_conn: asyncpg.Connection, empty_enricher: Enricher
) -> None:
    """Pre-seed the cache; lookup returns the cached row (verifiable via
    a non-default field set manually)."""
    ip = IPv4Address("198.51.100.2")
    try:
        await db_conn.execute(
            """
            INSERT INTO ip_enrichment (
                ip, country, asn_org, is_cloud, cloud_provider, is_datacenter,
                is_proxy, updated_at
            ) VALUES ($1::inet, 'CA', 'Hetzner Online GmbH', false, NULL, true,
                      false, now())
            """,
            str(ip),
        )

        row = await empty_enricher.enrich(db_conn, ip)
        assert row.country == "CA"
        assert row.asn_org == "Hetzner Online GmbH"
        assert row.is_datacenter is True
    finally:
        await _delete_ip(db_conn, str(ip))


async def test_stale_cache_refreshes(
    db_conn: asyncpg.Connection, empty_enricher: Enricher
) -> None:
    """A row older than 14 days triggers a refresh (the empty Enricher
    overwrites with default fields). The freshness window is the SQL
    `updated_at > now() - interval '14 days'` predicate in enrich()."""
    ip = IPv4Address("198.51.100.3")
    try:
        stale_time = datetime.now(tz=UTC) - timedelta(days=20)
        await db_conn.execute(
            """
            INSERT INTO ip_enrichment (ip, country, is_cloud, updated_at)
            VALUES ($1::inet, 'XX', true, $2)
            """,
            str(ip),
            stale_time,
        )

        row = await empty_enricher.enrich(db_conn, ip)
        # Empty Enricher returned a default row, overwriting 'XX' / is_cloud=True.
        assert row.country is None
        assert row.is_cloud is False

        refreshed = await db_conn.fetchrow(
            "SELECT updated_at FROM ip_enrichment WHERE ip = $1::inet",
            str(ip),
        )
        assert refreshed is not None
        assert refreshed["updated_at"] > stale_time
    finally:
        await _delete_ip(db_conn, str(ip))


def test_empty_enrichment_row_defaults() -> None:
    row = EnrichmentRow.empty("192.0.2.99")
    assert row.ip == "192.0.2.99"
    assert row.country is None
    assert row.is_cloud is False
    assert row.is_proxy is False
    assert row.is_vpn is False
    assert row.is_tor is False
    assert row.is_datacenter is False
    assert row.fh_level1 is False
    assert row.fh_level2 is False


def test_enricher_handles_missing_data_dir(tmp_path: Path) -> None:
    """Enricher init succeeds even when data_dir doesn't exist or is empty —
    sources lazy-load on first use; missing files log a warning, not raise."""
    nonexistent = tmp_path / "does-not-exist"
    enricher = Enricher(data_dir=nonexistent)
    enricher._load_sources()  # must not raise

    # Confirm the lazy-load actually attempted to populate sources (each
    # is None because every file was missing, NOT because _load_sources
    # is a no-op).
    assert enricher._loaded is True
    assert enricher._mm_city is None
    assert enricher._mm_asn is None
    assert enricher._ip2p is None
    assert enricher._firehol_l1 is None
    assert enricher._firehol_l2 is None
    assert enricher._cloud_tries == {}
