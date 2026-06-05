#!/usr/bin/env python3
"""Phase 7 freight_risk → NDJSON export script.

Reads the sibling freight_risk SQLite database read-only and writes
three NDJSON corpora to an operator-supplied output directory
(default /tmp/riskd-replay/). Each line is a BookingRequest payload
that the replay orchestrator (scripts/replay_validation.py) consumes.

THIS SCRIPT IS PHASE 7 EPHEMERA. Tracked during Phase 7 only; deleted
in commit 7E.3. NEVER produces output inside the repo tree; output
paths are /tmp by default.

Customer-country derivation (priority order, per-record):
  1. Explicit country column on a customers table — N/A in the
     current freight_risk schema (no clean structured country
     field). Tier always returns None.
  2. Address last-token regex: parse customer_registered_address
     with r'.*,\\s*([A-Z]{2})\\s*$'. Hits when the address ends in
     a 2-letter uppercase code.
  3. Modal IP geo: gather the customer's historical source IPs
     from the shipments table; MaxMind-lookup each. Modal country
     is accepted only when at least 5 successful lookups are
     available AND the top country represents >=70% of those
     lookups; otherwise tier 3 returns None and the record falls
     through to tier 4. Requires ~/.maxmind/GeoLite2-Country.mmdb
     and the maxminddb Python package. If either is unavailable
     the tier short-circuits to None.
  4. Null fallback: tier-4 records get customer.registered_country
     = None. They cannot trigger the case-3b outbound rule by
     accident (the rule's derivation requires both inputs truthy).

Per-corpus hardcoded overrides:
  - case-3 (Roulottes Lupien census): customer.registered_country
    = "CA" (operator ground truth from the fraud investigation);
    shipment.origin_via_carrier_dropoff = True.
  - case-2 + approved: derivation result for customer country;
    shipment.origin_via_carrier_dropoff = False.

All corpora: shipment.currency = "CAD" (Phase 6B project default;
freight_risk total values are currency-implicit).

Usage:
    python3 scripts/calibration/export_from_freight_risk.py \\
        --db /Users/drshott/PycharmProjects/miscProj/freight_risk/freight_risk.db \\
        --out-dir /tmp/riskd-replay/ \\
        --seed 42
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import random
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow the script to import production models for schema validation
# despite living in scripts/calibration/. Repo root added to sys.path
# at import time.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.models import BookingRequest  # noqa: E402

_MAXMIND_DB_PATH = Path.home() / ".maxmind" / "GeoLite2-Country.mmdb"

_COUNTRY_TAIL_RE = re.compile(r".*,\s*([A-Z]{2})\s*$")

_DEFAULT_DB_PATH = Path("/Users/drshott/PycharmProjects/miscProj/freight_risk/freight_risk.db")
_DEFAULT_OUT_DIR = Path("/tmp/riskd-replay/")

_SCHEMA_VALIDATION_STRIDE = 100
_MODAL_IP_MIN_SAMPLES = 5
_MODAL_IP_MIN_CONCENTRATION = 0.70


@dataclass
class CorpusSpec:
    slug: str
    filename: str
    where_clause: str
    sample_size: int | None  # None = full census
    case3_overrides: bool
    # Phase 7C.9: warmup-vs-measurement methodology. When True, the
    # exporter emits K=WARMUP_K pre-March-31 legitimate bookings per
    # measurement customer BEFORE the measurement records, so the
    # customer's accumulated baseline (and the new ASN rule's
    # cold-start gate) reflects production-realistic conditions
    # during replay. Case-3 disabled because the 95-record Roulottes
    # Lupien census is brand-new-customer fraud — by definition no
    # pre-fraud history to warm with.
    warmup_enabled: bool = True


WARMUP_K = 100


_CORPORA: tuple[CorpusSpec, ...] = (
    CorpusSpec(
        slug="approved",
        filename="approved_jan_mar.ndjson",
        where_clause=(
            "f.feedback='approve' AND s.target_date BETWEEN '2026-01-01' AND '2026-03-31'"
        ),
        sample_size=10_000,
        case3_overrides=False,
        warmup_enabled=True,
    ),
    CorpusSpec(
        slug="case2",
        filename="case2_sample.ndjson",
        where_clause="f.feedback='reject' AND f.notes='gobolt-non-34x-api'",
        sample_size=500,
        case3_overrides=False,
        warmup_enabled=True,
    ),
    CorpusSpec(
        slug="case3",
        filename="case3_census.ndjson",
        # Exact match per the Phase 6C / 7A.2 plan; the plan-specified
        # literal text is the operator's ground-truth notes string.
        where_clause=(
            "f.feedback='reject' AND f.notes='Roulottes Lupien — entire customer history fraud (user-confirmed)'"
        ),
        sample_size=None,  # full census
        case3_overrides=True,
        warmup_enabled=False,
    ),
)


@dataclass
class TierCounts:
    tier_1_explicit: int = 0
    tier_2_address_regex: int = 0
    tier_3_modal_ip_geo: int = 0
    tier_4_null: int = 0
    case3_override: int = 0  # tracked separately; not summed with tier_*

    def total_derived(self) -> int:
        return (
            self.tier_1_explicit
            + self.tier_2_address_regex
            + self.tier_3_modal_ip_geo
            + self.tier_4_null
        )


@dataclass
class _MaxMindReader:
    """Lazy MaxMind country lookup. Holds an opened reader when the
    DB is available; otherwise is a no-op stub (returns None).
    """

    reader: Any = None

    @classmethod
    def open(cls) -> _MaxMindReader:
        if not _MAXMIND_DB_PATH.exists():
            return cls(reader=None)
        try:
            import maxminddb
        except ImportError:
            return cls(reader=None)
        try:
            return cls(reader=maxminddb.open_database(str(_MAXMIND_DB_PATH)))
        except Exception:
            return cls(reader=None)

    def country_for_ip(self, ip: str) -> str | None:
        if self.reader is None or not ip:
            return None
        try:
            record = self.reader.get(ip)
        except Exception:
            return None
        if not record:
            return None
        country = record.get("country") if isinstance(record, dict) else None
        if isinstance(country, dict):
            iso = country.get("iso_code")
            if isinstance(iso, str) and len(iso) == 2:
                return iso
        return None


def _derive_country_tier_2(address: str | None) -> str | None:
    """Parse the address last-token for a 2-letter country code."""
    if not address:
        return None
    match = _COUNTRY_TAIL_RE.match(address)
    return match.group(1) if match else None


def _derive_country_tier_3(
    db: sqlite3.Connection,
    customer_id: str,
    maxmind: _MaxMindReader,
) -> str | None:
    """Modal country across the customer's historical source IPs."""
    if maxmind.reader is None:
        return None
    cur = db.execute(
        "SELECT source_ip FROM shipments WHERE customer_id = ? AND source_ip IS NOT NULL",
        (customer_id,),
    )
    ips = [row[0] for row in cur.fetchall() if row[0]]
    if len(ips) < _MODAL_IP_MIN_SAMPLES:
        return None
    countries = [maxmind.country_for_ip(ip) for ip in ips]
    countries = [c for c in countries if c is not None]
    if len(countries) < _MODAL_IP_MIN_SAMPLES:
        return None
    counter = Counter(countries)
    top_country, top_count = counter.most_common(1)[0]
    if top_count / len(countries) < _MODAL_IP_MIN_CONCENTRATION:
        return None
    return top_country


def _derive_customer_country(
    db: sqlite3.Connection,
    customer_id: str,
    customer_registered_address: str | None,
    maxmind: _MaxMindReader,
    tiers: TierCounts,
) -> str | None:
    # Tier 1: explicit column — N/A in the current freight_risk schema.
    # Tier 2: address regex.
    country = _derive_country_tier_2(customer_registered_address)
    if country is not None:
        tiers.tier_2_address_regex += 1
        return country
    # Tier 3: modal IP geo (requires MaxMind).
    country = _derive_country_tier_3(db, customer_id, maxmind)
    if country is not None:
        tiers.tier_3_modal_ip_geo += 1
        return country
    # Tier 4: null fallback.
    tiers.tier_4_null += 1
    return None


def _parse_booking_ts(raw: str | None) -> str | None:
    """freight_risk booking_started_at is 'YYYY-MM-DD HH:MM:SS[.fraction]'.
    Convert to RFC3339 with UTC offset since the Pydantic model expects
    a tz-aware datetime."""
    if not raw:
        return None
    raw = raw.replace(" ", "T")
    # freight_risk timestamps are tz-naive. Treat as UTC for the
    # replay-shaping artifact; the absolute timezone is not load-bearing
    # since velocity and dormancy rules are tz-anchored to booking_ts.
    if "+" not in raw and not raw.endswith("Z"):
        raw = raw + "+00:00"
    return raw


def _channel_from_source(source: str | None) -> str:
    return "api" if (source or "").lower() == "api" else "platform"


def _user_external_id(customer_id: str) -> str:
    """Deterministic per-customer synthetic user identity.
    freight_risk does not model per-user identity; one synthetic user
    per customer."""
    digest = hashlib.sha256(customer_id.encode("utf-8")).hexdigest()[:16]
    return f"user-{digest}"


def _row_to_payload(
    *,
    db: sqlite3.Connection,
    row: sqlite3.Row,
    corpus: CorpusSpec,
    idx: int,
    maxmind: _MaxMindReader,
    tiers: TierCounts,
    replay_role: str = "measurement",
) -> dict[str, Any]:
    customer_id = row["customer_id"]
    # case3 overrides ONLY apply to measurement records (case-3 census
    # is brand-new-customer fraud; no warmup branch reaches case-3).
    # Warmup records get tier-derived customer_country and
    # origin_via_carrier_dropoff=False unconditionally (warmup is
    # legitimate pre-fraud history).
    if corpus.case3_overrides and replay_role == "measurement":
        customer_country: str | None = "CA"
        tiers.case3_override += 1
        origin_via_carrier_dropoff = True
    else:
        customer_country = _derive_customer_country(
            db,
            customer_id,
            row["customer_registered_address"],
            maxmind,
            tiers,
        )
        origin_via_carrier_dropoff = False

    origin_country = _derive_country_tier_2(row["origin_address"])
    destination_country = _derive_country_tier_2(row["destination_address"])

    return {
        "request_id": f"replay-{corpus.slug}-{replay_role}-{idx}",
        "_replay_role": replay_role,
        "customer": {
            "external_id": customer_id,
            "registered_address": row["customer_registered_address"],
            "registered_country": customer_country,
        },
        "user": {"external_id": _user_external_id(customer_id)},
        "source_ip": row["source_ip"],
        "shipment": {
            "origin": {
                "address": row["origin_address"],
                "country": origin_country,
            },
            "destination": {
                "address": row["destination_address"],
                "country": destination_country,
            },
            "value": float(row["total"]) if row["total"] is not None else 0.0,
            "currency": "CAD",
            "channel": _channel_from_source(row["source"]),
            "origin_via_carrier_dropoff": origin_via_carrier_dropoff,
        },
        "booking_ts": _parse_booking_ts(row["booking_started_at"]),
    }


def _fetch_warmup_rows(
    db: sqlite3.Connection,
    customer_ids: list[str],
    k: int = WARMUP_K,
) -> list[sqlite3.Row]:
    """Fetch up to K most-recent legitimate pre-March-31 bookings per
    customer_id. Used by 7C.9 warmup methodology: each customer's
    accumulated baseline is populated by these bookings BEFORE the
    measurement records arrive, so the new ASN rule's cold-start
    gate reflects production-realistic conditions during replay.

    Returns rows ordered by (customer_id, target_date ASC, shipment_id
    ASC) so warmups per customer arrive chronologically. The
    orchestrator processes the NDJSON in order; warmup records hit
    the booking endpoint and populate customer_baselines before any
    measurement records for the same customer.
    """
    if not customer_ids:
        return []
    # SQLite parameter substitution for the IN clause.
    placeholders = ",".join("?" * len(customer_ids))
    sql = f"""
        WITH ranked AS (
            SELECT
                s.shipment_id,
                s.target_date,
                s.source,
                s.customer_id,
                s.customer_registered_address,
                s.origin_address,
                s.destination_address,
                s.total,
                s.source_ip,
                s.booking_started_at,
                ROW_NUMBER() OVER (
                    PARTITION BY s.customer_id
                    ORDER BY s.target_date DESC, s.shipment_id DESC
                ) AS rn
            FROM shipment_feedback f
            INNER JOIN shipments s ON f.shipment_id = s.shipment_id
            WHERE s.customer_id IN ({placeholders})
                AND f.feedback = 'approve'
                AND s.target_date < '2026-03-31'
                AND s.source_ip IS NOT NULL
                AND s.origin_address IS NOT NULL
                AND s.destination_address IS NOT NULL
                AND s.booking_started_at IS NOT NULL
                AND s.total IS NOT NULL
        )
        SELECT
            shipment_id, target_date, source, customer_id,
            customer_registered_address, origin_address,
            destination_address, total, source_ip, booking_started_at
        FROM ranked
        WHERE rn <= ?
        ORDER BY customer_id, target_date ASC, shipment_id ASC
    """
    cur = db.execute(sql, [*customer_ids, k])
    return cur.fetchall()


def _fetch_corpus_rows(
    db: sqlite3.Connection,
    corpus: CorpusSpec,
    rng: random.Random,
) -> list[sqlite3.Row]:
    # where_clause is a module-internal _CORPORA constant (not user
    # input). Additional filters: source_ip / origin_address /
    # destination_address / booking_started_at / total must all be
    # populated for the downstream BookingRequest Pydantic validation
    # to pass; row drops here prevent the strided validation from
    # crashing mid-write on NULL fields.
    sql = f"""
        SELECT
            s.shipment_id,
            s.target_date,
            s.source,
            s.customer_id,
            s.customer_registered_address,
            s.origin_address,
            s.destination_address,
            s.total,
            s.source_ip,
            s.booking_started_at
        FROM shipment_feedback f
        INNER JOIN shipments s ON f.shipment_id = s.shipment_id
        WHERE {corpus.where_clause}
            AND s.source_ip IS NOT NULL
            AND s.origin_address IS NOT NULL
            AND s.destination_address IS NOT NULL
            AND s.booking_started_at IS NOT NULL
            AND s.total IS NOT NULL
        ORDER BY s.shipment_id
    """
    cur = db.execute(sql)
    rows = cur.fetchall()
    if corpus.sample_size is None:
        return rows
    if len(rows) <= corpus.sample_size:
        return rows
    return rng.sample(rows, corpus.sample_size)


def _write_corpus_file(
    *,
    db: sqlite3.Connection,
    corpus: CorpusSpec,
    out_dir: Path,
    rng: random.Random,
    maxmind: _MaxMindReader,
    tiers: TierCounts,
) -> tuple[int, int]:
    """Emit warmup records (if enabled) FIRST per-customer, then
    measurement records. Returns (warmup_count, measurement_count).

    Warmup ordering: rows arrive grouped by customer_id and
    chronological within each customer (oldest first), so each
    customer's baseline accumulates in time-order before any later
    record for that customer is evaluated.
    """
    measurement_rows = _fetch_corpus_rows(db, corpus, rng)
    out_path = out_dir / corpus.filename
    warmup_count = 0
    measurement_count = 0
    with out_path.open("w", encoding="utf-8") as fh:
        # Warmup phase: emit per-customer historical records first.
        if corpus.warmup_enabled and measurement_rows:
            # Unique customer_ids in this measurement set, preserving
            # set semantics (order doesn't matter — warmup query
            # groups by customer_id internally).
            measurement_customer_ids = sorted({row["customer_id"] for row in measurement_rows})
            warmup_rows = _fetch_warmup_rows(db, measurement_customer_ids)
            for idx, row in enumerate(warmup_rows):
                payload = _row_to_payload(
                    db=db,
                    row=row,
                    corpus=corpus,
                    idx=idx,
                    maxmind=maxmind,
                    tiers=tiers,
                    replay_role="warmup",
                )
                if idx % _SCHEMA_VALIDATION_STRIDE == 0:
                    # Strip _replay_role metadata before Pydantic
                    # validation; BookingRequest is extra="forbid".
                    BookingRequest.model_validate(
                        {k: v for k, v in payload.items() if not k.startswith("_")}
                    )
                fh.write(json.dumps(payload) + "\n")
                warmup_count += 1
        # Measurement phase.
        for idx, row in enumerate(measurement_rows):
            payload = _row_to_payload(
                db=db,
                row=row,
                corpus=corpus,
                idx=idx,
                maxmind=maxmind,
                tiers=tiers,
                replay_role="measurement",
            )
            # Schema validation on a strided subset; full validation
            # would slow the export without additional coverage value
            # (the same transformation produces every row).
            if idx % _SCHEMA_VALIDATION_STRIDE == 0:
                # Strip _replay_role metadata before Pydantic validation;
                # BookingRequest is extra="forbid".
                BookingRequest.model_validate(
                    {k: v for k, v in payload.items() if not k.startswith("_")}
                )
            fh.write(json.dumps(payload) + "\n")
            measurement_count += 1
    return warmup_count, measurement_count


def export(
    *,
    db_path: Path,
    out_dir: Path,
    seed: int,
    corpora: Iterable[CorpusSpec] = _CORPORA,
) -> dict[str, dict[str, int]]:
    """Write the three NDJSON corpora to out_dir.

    Returns a dict mapping corpus slug -> {"warmup": int, "measurement": int}.
    Raises FileNotFoundError on missing db, OSError on out_dir write
    failure. Caller is responsible for sequencing (typically:
    export -> orchestrator runs against out_dir).
    """
    if not db_path.exists():
        msg = f"freight_risk DB not found: {db_path}"
        raise FileNotFoundError(msg)
    out_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rng = random.Random(seed)
    tiers = TierCounts()
    maxmind = _MaxMindReader.open()
    counts: dict[str, dict[str, int]] = {}
    try:
        for corpus in corpora:
            warmup_count, measurement_count = _write_corpus_file(
                db=db,
                corpus=corpus,
                out_dir=out_dir,
                rng=rng,
                maxmind=maxmind,
                tiers=tiers,
            )
            counts[corpus.slug] = {
                "warmup": warmup_count,
                "measurement": measurement_count,
            }
    finally:
        db.close()
        if maxmind.reader is not None:
            with contextlib.suppress(Exception):
                maxmind.reader.close()
        # Always log tier summary, even on failure: partial counts
        # help the operator diagnose mid-export crashes.
        _log_tier_summary(tiers)
    return counts


def _log_tier_summary(tiers: TierCounts) -> None:
    print(
        f"tier 1 (explicit column): {tiers.tier_1_explicit} records",
        file=sys.stderr,
    )
    print(
        f"tier 2 (address regex):   {tiers.tier_2_address_regex} records",
        file=sys.stderr,
    )
    print(
        f"tier 3 (modal IP geo):    {tiers.tier_3_modal_ip_geo} records",
        file=sys.stderr,
    )
    print(
        f"tier 4 (null fallback):   {tiers.tier_4_null} records",
        file=sys.stderr,
    )
    print(
        f"case-3 hardcoded:         {tiers.case3_override} records "
        "(customer_registered_country = 'CA')",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=_DEFAULT_DB_PATH)
    ap.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT_DIR)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)

    try:
        counts = export(db_path=args.db, out_dir=args.out_dir, seed=args.seed)
    except FileNotFoundError as exc:
        print(f"export error: {exc}", file=sys.stderr)
        return 2

    for slug, count in counts.items():
        filename = next(c.filename for c in _CORPORA if c.slug == slug)
        warmup = count["warmup"]
        measurement = count["measurement"]
        total = warmup + measurement
        print(
            f"  {slug}: {measurement} measurement + {warmup} warmup = "
            f"{total} records -> {args.out_dir / filename}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
