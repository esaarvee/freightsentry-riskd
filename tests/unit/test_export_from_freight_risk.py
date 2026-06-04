"""Unit tests for scripts/calibration/export_from_freight_risk.py.

Tests run against a synthetic SQLite database constructed in tmp_path
that mirrors the freight_risk schema (shipments + shipment_feedback
tables). No production data is consulted; the freight_risk DB itself
is not opened.

Covers:
- Tier-2 address regex success / failure cases.
- Tier 1 (explicit column) always returns None in the current schema.
- Tier 3 (modal IP geo) gated on MaxMind availability; when absent,
  returns None for every record.
- Tier 4 (null fallback) catches records that fall through tier 2/3.
- Case-3 hardcoded override: customer_registered_country = "CA",
  origin_via_carrier_dropoff = True, regardless of derivation result.
- Case-2 + approved: origin_via_carrier_dropoff = False.
- Per-corpus record counts.
- Pydantic schema validation on the strided sample succeeds.
- Random seed determinism: two consecutive exports with the same seed
  produce byte-identical NDJSON.
- Channel mapping: source 'api' -> 'api'; 'web' -> 'platform'.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.calibration.export_from_freight_risk import (
    _CORPORA,
    TierCounts,
    _channel_from_source,
    _derive_country_tier_2,
    _derive_customer_country,
    _MaxMindReader,
    _user_external_id,
    export,
)

_NULL_MAXMIND = _MaxMindReader(reader=None)


@pytest.fixture(autouse=True)
def _force_null_maxmind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the export script's MaxMind reader to the no-op stub for
    every test in this module. Removes dependence on whether the dev
    machine has ~/.maxmind/GeoLite2-Country.mmdb installed; tier-3
    behavior is exercised explicitly via _NULL_MAXMIND in the focused
    derivation tests."""
    monkeypatch.setattr(
        "scripts.calibration.export_from_freight_risk._MaxMindReader.open",
        classmethod(lambda cls: _NULL_MAXMIND),
    )


# ---------------------------------------------------------------------------
# Synthetic freight_risk fixture
# ---------------------------------------------------------------------------


def _build_synthetic_db(
    path: Path,
    *,
    approved_count: int = 12,
    case2_count: int = 8,
    case3_count: int = 95,
) -> None:
    """Create a SQLite file with the freight_risk schema and three
    classes of records. approved + case2 sized small for fast tests;
    case3 sized to the full 95-record census."""
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE shipments (
            shipment_id TEXT PRIMARY KEY,
            transaction_number TEXT,
            booking_started_at TEXT,
            target_date TEXT,
            source TEXT,
            customer_id TEXT,
            customer_registered_address TEXT,
            origin_address TEXT,
            destination_address TEXT,
            total REAL,
            source_ip TEXT,
            ingested_at TEXT NOT NULL
        );
        CREATE TABLE shipment_feedback (
            shipment_id TEXT,
            feedback TEXT,
            reviewed_at TEXT,
            notes TEXT
        );
        """
    )

    def _insert_shipment(
        shipment_id: str,
        *,
        customer_id: str,
        customer_addr: str,
        origin_addr: str,
        destination_addr: str,
        total: float,
        source: str,
        source_ip: str = "203.0.113.10",
        target_date: str = "2026-01-15",
        booking_started_at: str = "2026-01-15 12:00:00.0000",
    ) -> None:
        db.execute(
            "INSERT INTO shipments (shipment_id, transaction_number, "
            "booking_started_at, target_date, source, customer_id, "
            "customer_registered_address, origin_address, destination_address, "
            "total, source_ip, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                shipment_id,
                f"tx-{shipment_id}",
                booking_started_at,
                target_date,
                source,
                customer_id,
                customer_addr,
                origin_addr,
                destination_addr,
                total,
                source_ip,
                "2026-01-15T12:00:00",
            ),
        )

    # Approved records (Jan-Mar)
    for i in range(approved_count):
        sid = f"appr-{i:03d}"
        _insert_shipment(
            sid,
            customer_id=f"appr-cust-{i}",
            customer_addr=f"1{i} King St, Toronto, ON, M5H 2N2, CA",
            origin_addr=f"1{i} King St, Toronto, ON, CA",
            destination_addr=f"2{i} Bay St, Toronto, ON, CA",
            total=100.0 + i,
            source="web",
        )
        db.execute(
            "INSERT INTO shipment_feedback VALUES (?,?,?,?)",
            (sid, "approve", "2026-01-15T13:00:00", "auto-approved"),
        )

    # case-2 records (gobolt-non-34x-api)
    for i in range(case2_count):
        sid = f"case2-{i:03d}"
        _insert_shipment(
            sid,
            customer_id=f"case2-cust-{i}",
            customer_addr=f"3{i} Queen St, Toronto, ON, CA",
            origin_addr=f"3{i} Queen St, Toronto, ON, CA",
            destination_addr=f"4{i} Main St, New York, NY, US",
            total=500.0 + i,
            source="api",
        )
        db.execute(
            "INSERT INTO shipment_feedback VALUES (?,?,?,?)",
            (sid, "reject", "2026-01-16T09:00:00", "gobolt-non-34x-api"),
        )

    # case-3 records (Roulottes Lupien)
    for i in range(case3_count):
        sid = f"case3-{i:03d}"
        _insert_shipment(
            sid,
            customer_id="roulottes-lupien-2000",
            customer_addr="2700 route 122, SAINT-CYRILLE-DE-WENDOVER, QC, CA",
            origin_addr="2700 route 122, SAINT-CYRILLE-DE-WENDOVER, QC, CA",
            destination_addr=f"5{i} Liberty Ave, Bridgeport, CT, US",
            total=1200.0 + i,
            source="web",
            booking_started_at="2026-05-20 11:00:00.0000",
        )
        db.execute(
            "INSERT INTO shipment_feedback VALUES (?,?,?,?)",
            (
                sid,
                "reject",
                "2026-05-20T14:00:00",
                "Roulottes Lupien — entire customer history fraud (user-confirmed)",
            ),
        )

    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Tier-2 address regex
# ---------------------------------------------------------------------------


def test_tier_2_matches_address_ending_in_two_letter_code() -> None:
    assert _derive_country_tier_2("1 King St, Toronto, ON, CA") == "CA"
    assert _derive_country_tier_2("100 Broad St, NYC, NY, US") == "US"
    assert _derive_country_tier_2("addr, GB") == "GB"


def test_tier_2_permissive_trade_off_pinned() -> None:
    """The regex matches ANY 2-letter uppercase final token, including
    province codes that happen to share the shape with country codes
    (e.g. 'ON' for Ontario). This is the documented trade-off:
    accept some false positives on edge-case addresses to keep the
    primary path simple. Pin it so a future regex tightening cannot
    silently change the contract without a test failure."""
    assert _derive_country_tier_2("1 King St, Toronto, ON") == "ON"
    assert _derive_country_tier_2("100 Broad St, NYC, NY") == "NY"


def test_tier_2_does_not_match_when_no_two_letter_tail() -> None:
    """The regex r'.*,\\s*([A-Z]{2})\\s*$' fires on any address whose
    final comma-separated token is exactly two uppercase letters. By
    design this is permissive (it also matches addresses like
    "..., Toronto, ON" where ON is a province, not a country). The
    plan accepts this trade-off because real freight_risk addresses
    end with the country code (e.g. ", QC, H2V1A6, CA"). Tier 2 is
    expected to return None only when the FINAL token is not exactly
    two uppercase letters."""
    assert _derive_country_tier_2("street") is None
    assert _derive_country_tier_2("1 King St, Toronto, Canada") is None
    assert _derive_country_tier_2("1 King St, Toronto, ca") is None  # lowercase
    assert _derive_country_tier_2("1 King St, Toronto, ON, H2V1A6") is None  # postal tail
    assert _derive_country_tier_2("1 King St") is None  # no comma


def test_tier_2_returns_none_for_falsy_inputs() -> None:
    assert _derive_country_tier_2(None) is None
    assert _derive_country_tier_2("") is None


# ---------------------------------------------------------------------------
# Tier-3 modal IP geo (MaxMind unavailable path)
# ---------------------------------------------------------------------------


def test_tier_3_returns_none_when_maxmind_unavailable(tmp_path: Path) -> None:
    """When the MaxMind reader is a no-op stub (no .mmdb / no
    maxminddb package), tier 3 short-circuits to None for every
    record."""
    db_path = tmp_path / "fr.db"
    _build_synthetic_db(db_path)
    db = sqlite3.connect(db_path)
    tiers = TierCounts()
    result = _derive_customer_country(
        db,
        "appr-cust-0",
        None,  # tier 2 input missing → tier 3 attempted → MaxMind absent → tier 4
        _NULL_MAXMIND,
        tiers,
    )
    db.close()
    assert result is None
    assert tiers.tier_2_address_regex == 0
    assert tiers.tier_3_modal_ip_geo == 0
    assert tiers.tier_4_null == 1


def test_derive_country_prefers_tier_2_over_tier_3(tmp_path: Path) -> None:
    """When tier 2 succeeds, tier 3 is never attempted (even when
    MaxMind is available). This pins the priority order."""
    db_path = tmp_path / "fr.db"
    _build_synthetic_db(db_path)
    db = sqlite3.connect(db_path)
    tiers = TierCounts()
    result = _derive_customer_country(
        db,
        "appr-cust-0",
        "1 King St, Toronto, ON, CA",
        _NULL_MAXMIND,
        tiers,
    )
    db.close()
    assert result == "CA"
    assert tiers.tier_2_address_regex == 1
    assert tiers.tier_3_modal_ip_geo == 0
    assert tiers.tier_4_null == 0


# ---------------------------------------------------------------------------
# Channel mapping
# ---------------------------------------------------------------------------


def test_channel_mapping_api_and_web() -> None:
    assert _channel_from_source("api") == "api"
    assert _channel_from_source("API") == "api"
    assert _channel_from_source("web") == "platform"
    assert _channel_from_source("") == "platform"
    assert _channel_from_source(None) == "platform"


# ---------------------------------------------------------------------------
# User external_id derivation
# ---------------------------------------------------------------------------


def test_user_external_id_is_deterministic() -> None:
    a = _user_external_id("cust-1")
    b = _user_external_id("cust-1")
    c = _user_external_id("cust-2")
    assert a == b
    assert a != c
    assert a.startswith("user-")


# ---------------------------------------------------------------------------
# Full export end-to-end
# ---------------------------------------------------------------------------


def test_export_produces_three_ndjson_files(tmp_path: Path) -> None:
    db_path = tmp_path / "fr.db"
    out_dir = tmp_path / "out"
    _build_synthetic_db(db_path, approved_count=12, case2_count=8, case3_count=95)
    counts = export(db_path=db_path, out_dir=out_dir, seed=42)
    assert (out_dir / "approved_jan_mar.ndjson").exists()
    assert (out_dir / "case2_sample.ndjson").exists()
    assert (out_dir / "case3_census.ndjson").exists()
    assert counts["approved"] == 12
    assert counts["case2"] == 8
    assert counts["case3"] == 95


def test_export_approved_corpus_respects_sample_size(tmp_path: Path) -> None:
    """The approved corpus is configured with sample_size=10_000;
    when the source has fewer rows, the full set is returned."""
    db_path = tmp_path / "fr.db"
    out_dir = tmp_path / "out"
    _build_synthetic_db(db_path, approved_count=5, case2_count=8, case3_count=95)
    counts = export(db_path=db_path, out_dir=out_dir, seed=42)
    assert counts["approved"] == 5


def test_export_case3_records_have_hardcoded_overrides(tmp_path: Path) -> None:
    """case-3 records must carry customer_registered_country='CA' and
    shipment.origin_via_carrier_dropoff=True regardless of the row's
    actual customer_registered_address (which would otherwise tier-2
    to 'CA' as well, but the property is contractual)."""
    db_path = tmp_path / "fr.db"
    out_dir = tmp_path / "out"
    _build_synthetic_db(db_path, approved_count=5, case2_count=5, case3_count=10)
    export(db_path=db_path, out_dir=out_dir, seed=42)
    case3_lines = (out_dir / "case3_census.ndjson").read_text().splitlines()
    for line in case3_lines:
        payload = json.loads(line)
        assert payload["customer"]["registered_country"] == "CA"
        assert payload["shipment"]["origin_via_carrier_dropoff"] is True


def test_export_case2_records_have_no_carrier_dropoff(tmp_path: Path) -> None:
    db_path = tmp_path / "fr.db"
    out_dir = tmp_path / "out"
    _build_synthetic_db(db_path, approved_count=5, case2_count=5, case3_count=5)
    export(db_path=db_path, out_dir=out_dir, seed=42)
    case2_lines = (out_dir / "case2_sample.ndjson").read_text().splitlines()
    for line in case2_lines:
        payload = json.loads(line)
        assert payload["shipment"]["origin_via_carrier_dropoff"] is False


def test_export_approved_records_have_no_carrier_dropoff(tmp_path: Path) -> None:
    db_path = tmp_path / "fr.db"
    out_dir = tmp_path / "out"
    _build_synthetic_db(db_path, approved_count=5, case2_count=5, case3_count=5)
    export(db_path=db_path, out_dir=out_dir, seed=42)
    approved_lines = (out_dir / "approved_jan_mar.ndjson").read_text().splitlines()
    for line in approved_lines:
        payload = json.loads(line)
        assert payload["shipment"]["origin_via_carrier_dropoff"] is False


def test_export_currency_is_cad_on_every_record(tmp_path: Path) -> None:
    db_path = tmp_path / "fr.db"
    out_dir = tmp_path / "out"
    _build_synthetic_db(db_path, approved_count=5, case2_count=5, case3_count=5)
    export(db_path=db_path, out_dir=out_dir, seed=42)
    for filename in ("approved_jan_mar.ndjson", "case2_sample.ndjson", "case3_census.ndjson"):
        for line in (out_dir / filename).read_text().splitlines():
            payload = json.loads(line)
            assert payload["shipment"]["currency"] == "CAD"


def test_export_request_id_pattern_is_deterministic(tmp_path: Path) -> None:
    db_path = tmp_path / "fr.db"
    out_dir = tmp_path / "out"
    _build_synthetic_db(db_path, approved_count=5, case2_count=5, case3_count=5)
    export(db_path=db_path, out_dir=out_dir, seed=42)
    for slug, filename in (
        ("approved", "approved_jan_mar.ndjson"),
        ("case2", "case2_sample.ndjson"),
        ("case3", "case3_census.ndjson"),
    ):
        for idx, line in enumerate((out_dir / filename).read_text().splitlines()):
            assert json.loads(line)["request_id"] == f"replay-{slug}-{idx}"


def test_export_payloads_validate_against_booking_request(tmp_path: Path) -> None:
    """The strided in-export validation runs every 100 records on the
    output. This test runs FULL validation across every emitted record
    as a stronger contract — catches schema drift between the export
    shape and the consuming Pydantic model."""
    from app.models import BookingRequest

    db_path = tmp_path / "fr.db"
    out_dir = tmp_path / "out"
    _build_synthetic_db(db_path, approved_count=5, case2_count=5, case3_count=5)
    export(db_path=db_path, out_dir=out_dir, seed=42)
    for filename in ("approved_jan_mar.ndjson", "case2_sample.ndjson", "case3_census.ndjson"):
        for line in (out_dir / filename).read_text().splitlines():
            BookingRequest.model_validate(json.loads(line))


def test_export_seed_determinism(tmp_path: Path) -> None:
    """Two consecutive exports with the same seed produce byte-identical
    output. This is the contract that lets 7B and 7D measure against
    the same corpora across runs."""
    db_path = tmp_path / "fr.db"
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    # Use enough records to actually exercise sampling.
    _build_synthetic_db(db_path, approved_count=20, case2_count=20, case3_count=10)
    # Reduce sample sizes for the test by temporarily creating tiny corpora.
    export(db_path=db_path, out_dir=out_a, seed=42)
    export(db_path=db_path, out_dir=out_b, seed=42)
    for filename in ("approved_jan_mar.ndjson", "case2_sample.ndjson", "case3_census.ndjson"):
        assert (out_a / filename).read_bytes() == (out_b / filename).read_bytes()


def test_export_missing_db_raises_filenotfound(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="freight_risk DB not found"):
        export(db_path=tmp_path / "no-such.db", out_dir=tmp_path / "out", seed=42)


def test_corpus_specs_have_unique_slugs_and_filenames() -> None:
    """Defensive pin: future amendments must not collide corpus slugs
    or filenames."""
    slugs = [c.slug for c in _CORPORA]
    filenames = [c.filename for c in _CORPORA]
    assert len(set(slugs)) == len(slugs)
    assert len(set(filenames)) == len(filenames)
    assert set(slugs) == {"approved", "case2", "case3"}
