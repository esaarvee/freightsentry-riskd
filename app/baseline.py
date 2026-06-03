"""CustomerBaseline — per-customer fraud baseline.

Lifecycle:
  1. `await CustomerBaseline.load(conn, tenant_id, customer_id, for_update=True)`
  2. `baseline.decay_to(today)`
  3. `baseline.add_observation(...)`  (or add_rejected_observation on feedback)
  4. `await baseline.save(conn)`

All steps run inside one transaction so the `SELECT FOR UPDATE` row-lock
holds across the read-modify-write sequence. The lock releases on the
enclosing transaction's commit/rollback (per `.ai/gotchas/postgres.md`).

Stat-dict entry shape: `{n, r_n, last}` for every dict except `ip_stats`,
which adds `type` ∈ {cloud, dc, residential} to drive per-IP-type decay.
Per-IP-type half-lives applied on read by `decay_to`:
  cloud / dc  → 365 d
  residential → 60 d
  unknown     → 180 d
Other stat-dicts, flat histograms, and Welford accumulators use the
uniform 90 d half-life.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import asyncpg
import structlog

_log = structlog.get_logger(__name__)

_LN2 = math.log(2)

# Per-IP-type half-lives (days), matching .ai/decisions.md § Decay strategy
HALF_LIFE_IP_CLOUD = 365.0
HALF_LIFE_IP_DC = 365.0
HALF_LIFE_IP_RESIDENTIAL = 60.0
HALF_LIFE_IP_UNKNOWN = 180.0
HALF_LIFE_DEFAULT = 90.0

# IP type tags written to ip_stats[ip]["type"]
IP_TYPE_CLOUD = "cloud"
IP_TYPE_DC = "dc"
IP_TYPE_RESIDENTIAL = "residential"

# Phase 6A.2 — cap on country_route_stats distinct (origin, destination)
# country pairs per customer baseline. Real customers ship across <50
# country combinations; the cap is a defense-in-depth bound against
# adversarial flooding (carrier API ATO pattern with rapidly varying
# spoofed destination countries). Beyond this cap, new pairs are
# silently dropped while existing keys continue to bump.
COUNTRY_ROUTE_STATS_CAP = 256


def classify_ip_type(enrichment: object) -> str | None:
    """Map an EnrichmentRow to the baseline ip_type tag.

    Priority: cloud (CIDR / cloud-provider ASN) → datacenter (ASN keyword
    match) → residential (default for non-cloud IPs with ASN data) →
    None (no ASN data at all — per-IP-type decay falls back to the
    180-day unknown half-life).

    The argument is duck-typed as `object` to avoid an import cycle
    with app.enrich (which already imports signal_helpers); callers
    pass an EnrichmentRow.
    """
    if getattr(enrichment, "is_cloud", False):
        return IP_TYPE_CLOUD
    if getattr(enrichment, "is_datacenter", False):
        return IP_TYPE_DC
    if getattr(enrichment, "asn_org", None):
        return IP_TYPE_RESIDENTIAL
    return None


def _half_life_for_ip_type(ip_type: str | None) -> float:
    return {
        IP_TYPE_CLOUD: HALF_LIFE_IP_CLOUD,
        IP_TYPE_DC: HALF_LIFE_IP_DC,
        IP_TYPE_RESIDENTIAL: HALF_LIFE_IP_RESIDENTIAL,
    }.get(ip_type or "", HALF_LIFE_IP_UNKNOWN)


def _decay_factor(delta_days: int, half_life: float) -> float:
    if delta_days <= 0:
        return 1.0
    return math.exp(-_LN2 * delta_days / half_life)


def _empty_entry() -> dict[str, Any]:
    return {"n": 0.0, "r_n": 0.0, "last": ""}


def _decode_jsonb(value: Any) -> dict[str, Any]:
    """asyncpg returns JSONB as str by default (no codec registered).
    This helper handles both the string and pre-decoded paths."""
    if value is None:
        return {}
    if isinstance(value, str):
        result: dict[str, Any] = json.loads(value)
        return result
    return dict(value)


@dataclass
class CustomerBaseline:
    tenant_id: int
    customer_id: int
    id: int | None = None

    # Stat-dicts (positional vs identity vs network). Mutable on add_observation;
    # decayed in-place on decay_to.
    origin_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    dest_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    lane_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    ip_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    ip_netblock_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    ip_asn_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    country_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    origin_ip_country_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Phase 6A.2 — case-3a route-baseline histogram. Key:
    # f"{origin_country}||{destination_country}". Populated by
    # add_observation when both shipment countries are non-null.
    country_route_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    email_hmacs: dict[str, dict[str, Any]] = field(default_factory=dict)
    phone_hmacs: dict[str, dict[str, Any]] = field(default_factory=dict)
    rejected_email_hmacs: dict[str, dict[str, Any]] = field(default_factory=dict)
    rejected_phone_hmacs: dict[str, dict[str, Any]] = field(default_factory=dict)
    email_domain_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    phone_prefix_stats: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Flat histograms
    ip_type_hist: dict[str, float] = field(default_factory=dict)
    hour_hist: dict[str, float] = field(default_factory=dict)
    weekday_hist: dict[str, float] = field(default_factory=dict)
    channel_hist: dict[str, float] = field(default_factory=dict)

    # Welford triples — value and cadence-hours
    value_n: float = 0.0
    value_mean: float = 0.0
    value_m2: float = 0.0
    cadence_n: float = 0.0
    cadence_mean_h: float = 0.0
    cadence_m2_h: float = 0.0

    # Last-booking pointers
    last_booking_ts: datetime | None = None
    last_booking_lat: float | None = None
    last_booking_lon: float | None = None
    last_booking_country: str | None = None

    # Lifecycle
    decay_anchor_date: date | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def empty(cls, tenant_id: int, customer_id: int) -> CustomerBaseline:
        return cls(tenant_id=tenant_id, customer_id=customer_id)

    @property
    def effective_observations(self) -> float:
        """Post-decay activity proxy. Exposed to rule conditions as
        `customer_observations` (operator amendment 2026-05-25)."""
        return self.value_n

    @property
    def cloud_share(self) -> float:
        """Share of decay-weighted IP observations from cloud IPs.

        Returns 0.0 when the customer has no IP-type observations. The
        ratio is stable under uniform decay (numerator and denominator
        decay by the same factor).
        """
        total = sum(self.ip_type_hist.values())
        if total <= 0:
            return 0.0
        return float(self.ip_type_hist.get("cloud", 0.0)) / total

    @property
    def api_share(self) -> float:
        """Share of decay-weighted bookings from the api channel."""
        total = sum(self.channel_hist.values())
        if total <= 0:
            return 0.0
        return float(self.channel_hist.get("api", 0.0)) / total

    def days_since_last_booking(self, now_ts: datetime) -> int | None:
        """Whole-day count since last_booking_ts. Returns None for the
        first-ever booking (no prior baseline observation). Negative
        deltas (now_ts in the past) clamp to 0."""
        if self.last_booking_ts is None:
            return None
        delta = now_ts - self.last_booking_ts
        return max(0, delta.days)

    # -----------------------------------------------------------------------
    # Load
    # -----------------------------------------------------------------------

    @classmethod
    async def load(
        cls,
        conn: asyncpg.Connection,
        tenant_id: int,
        customer_id: int,
        *,
        for_update: bool = False,
    ) -> CustomerBaseline:
        """Read or return-empty. With `for_update=True`, guarantees a
        row exists and a row-lock is held.

        First-write race fix: if the row doesn't exist and `for_update`
        is True, reserve an empty row via INSERT ... ON CONFLICT DO
        NOTHING (atomic), then SELECT FOR UPDATE the now-existing row.
        Without this, two concurrent first-bookings for the same
        customer would both `load → empty → save` and the second save
        would overwrite the first via ON CONFLICT DO UPDATE — lost
        update on the first-write-from-empty path.
        """
        suffix = " FOR UPDATE" if for_update else ""
        row = await conn.fetchrow(
            "SELECT * FROM customer_baselines WHERE tenant_id = $1 AND customer_id = $2" + suffix,
            tenant_id,
            customer_id,
        )
        if row is None:
            if not for_update:
                return cls.empty(tenant_id, customer_id)
            # Reserve the row atomically; either our INSERT wins or a
            # concurrent INSERT already created it. Either way the
            # subsequent SELECT FOR UPDATE finds + locks a row.
            await conn.execute(
                """
                INSERT INTO customer_baselines (tenant_id, customer_id)
                VALUES ($1, $2)
                ON CONFLICT (tenant_id, customer_id) DO NOTHING
                """,
                tenant_id,
                customer_id,
            )
            row = await conn.fetchrow(
                "SELECT * FROM customer_baselines "
                "WHERE tenant_id = $1 AND customer_id = $2 FOR UPDATE",
                tenant_id,
                customer_id,
            )
            if row is None:
                msg = "customer_baselines row not found after reserve-insert — concurrency anomaly"
                raise RuntimeError(msg)
        return cls._from_row(row)

    @classmethod
    def _from_row(cls, row: asyncpg.Record) -> CustomerBaseline:
        return cls(
            id=row["id"],
            tenant_id=row["tenant_id"],
            customer_id=row["customer_id"],
            origin_stats=_decode_jsonb(row["origin_stats"]),
            dest_stats=_decode_jsonb(row["dest_stats"]),
            lane_stats=_decode_jsonb(row["lane_stats"]),
            ip_stats=_decode_jsonb(row["ip_stats"]),
            ip_netblock_stats=_decode_jsonb(row["ip_netblock_stats"]),
            ip_asn_stats=_decode_jsonb(row["ip_asn_stats"]),
            country_stats=_decode_jsonb(row["country_stats"]),
            origin_ip_country_stats=_decode_jsonb(row["origin_ip_country_stats"]),
            country_route_stats=_decode_jsonb(row["country_route_stats"]),
            email_hmacs=_decode_jsonb(row["email_hmacs"]),
            phone_hmacs=_decode_jsonb(row["phone_hmacs"]),
            rejected_email_hmacs=_decode_jsonb(row["rejected_email_hmacs"]),
            rejected_phone_hmacs=_decode_jsonb(row["rejected_phone_hmacs"]),
            email_domain_stats=_decode_jsonb(row["email_domain_stats"]),
            phone_prefix_stats=_decode_jsonb(row["phone_prefix_stats"]),
            ip_type_hist=_decode_jsonb(row["ip_type_hist"]),
            hour_hist=_decode_jsonb(row["hour_hist"]),
            weekday_hist=_decode_jsonb(row["weekday_hist"]),
            channel_hist=_decode_jsonb(row["channel_hist"]),
            value_n=float(row["value_n"]),
            value_mean=float(row["value_mean"]),
            value_m2=float(row["value_m2"]),
            cadence_n=float(row["cadence_n"]),
            cadence_mean_h=float(row["cadence_mean_h"]),
            cadence_m2_h=float(row["cadence_m2_h"]),
            last_booking_ts=row["last_booking_ts"],
            last_booking_lat=float(row["last_booking_lat"])
            if row["last_booking_lat"] is not None
            else None,
            last_booking_lon=float(row["last_booking_lon"])
            if row["last_booking_lon"] is not None
            else None,
            last_booking_country=row["last_booking_country"],
            decay_anchor_date=row["decay_anchor_date"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            updated_at=row["updated_at"],
        )

    # -----------------------------------------------------------------------
    # Decay
    # -----------------------------------------------------------------------

    def decay_to(self, as_of: date) -> None:
        """Apply lazy decay forward from decay_anchor_date to as_of.

        IP stats: per-entry half-life by `type`. All other stats / histograms /
        Welford accumulators: uniform 90 d. Anchor advances to as_of on
        success. No-op when the baseline has never been written (anchor
        is None) — the anchor is set on the FIRST add_observation.
        """
        if self.decay_anchor_date is None:
            self.decay_anchor_date = as_of
            return
        if self.decay_anchor_date >= as_of:
            return
        delta_days = (as_of - self.decay_anchor_date).days

        # IP stats — per-entry half-life
        for entry in self.ip_stats.values():
            factor = _decay_factor(delta_days, _half_life_for_ip_type(entry.get("type")))
            entry["n"] = float(entry.get("n", 0.0)) * factor
            entry["r_n"] = float(entry.get("r_n", 0.0)) * factor

        # Other stat-dicts — uniform 90 d
        default_factor = _decay_factor(delta_days, HALF_LIFE_DEFAULT)
        for sd in (
            self.origin_stats,
            self.dest_stats,
            self.lane_stats,
            self.ip_netblock_stats,
            self.ip_asn_stats,
            self.country_stats,
            self.origin_ip_country_stats,
            self.country_route_stats,
            self.email_hmacs,
            self.phone_hmacs,
            self.rejected_email_hmacs,
            self.rejected_phone_hmacs,
            self.email_domain_stats,
            self.phone_prefix_stats,
        ):
            for entry in sd.values():
                entry["n"] = float(entry.get("n", 0.0)) * default_factor
                entry["r_n"] = float(entry.get("r_n", 0.0)) * default_factor

        for hist in (self.ip_type_hist, self.hour_hist, self.weekday_hist, self.channel_hist):
            for key in list(hist.keys()):
                hist[key] = float(hist[key]) * default_factor

        # Welford accumulators — decay n + m2 (mean is dimension-less)
        self.value_n *= default_factor
        self.value_m2 *= default_factor
        self.cadence_n *= default_factor
        self.cadence_m2_h *= default_factor

        self.decay_anchor_date = as_of

    # -----------------------------------------------------------------------
    # Observations
    # -----------------------------------------------------------------------

    def add_observation(
        self,
        *,
        ts: datetime,
        ip: str,
        ip_type: str | None,
        ip_netblock: str,
        ip_asn: str | None,
        ip_country: str | None,
        ip_lat: float | None,
        ip_lon: float | None,
        origin: str,
        destination: str,
        channel: str,
        value: float,
        shipment_origin_country: str | None = None,
        shipment_destination_country: str | None = None,
        email_hmac: str | None = None,
        phone_hmac: str | None = None,
        email_domain: str | None = None,
        phone_prefix: str | None = None,
    ) -> None:
        """Fold a positive (approved) observation. Bumps `n` in every
        relevant stat-dict, updates flat histograms, updates Welford for
        value + cadence-hours, advances last-booking pointers.

        `shipment_origin_country` / `shipment_destination_country` are the
        Pydantic Address.country structured-field passthroughs (NOT the IP
        country). When both are non-null, the (origin, destination)
        country pair is bumped in country_route_stats — bounded at
        COUNTRY_ROUTE_STATS_CAP keys to prevent adversarial jsonb bloat
        (Phase 6A.2).
        """
        today_iso = ts.date().isoformat()

        self._bump_ip(ip, ip_type, today_iso)
        self._bump(self.ip_netblock_stats, ip_netblock, today_iso)
        if ip_asn:
            self._bump(self.ip_asn_stats, ip_asn, today_iso)
        if ip_country:
            self._bump(self.country_stats, ip_country, today_iso)
            self._bump(self.origin_ip_country_stats, f"{origin}||{ip_country}", today_iso)

        self._bump(self.origin_stats, origin, today_iso)
        self._bump(self.dest_stats, destination, today_iso)
        self._bump(self.lane_stats, f"{origin}||{destination}", today_iso)

        # Phase 6A.2 — shipment country-pair histogram (case-3a route
        # baseline). Bump only when BOTH countries non-null. Cap at
        # COUNTRY_ROUTE_STATS_CAP distinct pairs: existing keys always
        # bump; new keys are added only while under cap. Beyond cap,
        # subsequent novel pairs are silently dropped (the customer is
        # already extremely route-diverse and route-unfamiliar signal
        # would not be meaningful anyway).
        if shipment_origin_country and shipment_destination_country:
            route_key = f"{shipment_origin_country}||{shipment_destination_country}"
            if (
                route_key in self.country_route_stats
                or len(self.country_route_stats) < COUNTRY_ROUTE_STATS_CAP
            ):
                self._bump(self.country_route_stats, route_key, today_iso)

        if email_hmac:
            self._bump(self.email_hmacs, email_hmac, today_iso)
        if phone_hmac:
            self._bump(self.phone_hmacs, phone_hmac, today_iso)
        if email_domain:
            self._bump(self.email_domain_stats, email_domain, today_iso)
        if phone_prefix:
            self._bump(self.phone_prefix_stats, phone_prefix, today_iso)

        if ip_type:
            self.ip_type_hist[ip_type] = self.ip_type_hist.get(ip_type, 0.0) + 1.0
        self.hour_hist[str(ts.hour)] = self.hour_hist.get(str(ts.hour), 0.0) + 1.0
        self.weekday_hist[str(ts.weekday())] = self.weekday_hist.get(str(ts.weekday()), 0.0) + 1.0
        self.channel_hist[channel] = self.channel_hist.get(channel, 0.0) + 1.0

        self._welford_value(value)
        if self.last_booking_ts is not None:
            hours = (ts - self.last_booking_ts).total_seconds() / 3600.0
            if hours > 0:
                self._welford_cadence(hours)

        self.last_booking_ts = ts
        self.last_booking_lat = ip_lat
        self.last_booking_lon = ip_lon
        self.last_booking_country = ip_country

    def add_rejected_observation(self, *, key_in: str, stat: str, ts: datetime) -> None:
        """Feedback path — increment `r_n` on a specific stat-dict entry.
        `stat` selects which dict to touch (e.g. 'email_hmacs', 'ip_stats').
        Used by 1D-Phase-3 feedback handler."""
        target = getattr(self, stat)
        entry = target.get(key_in, _empty_entry())
        entry["r_n"] = float(entry.get("r_n", 0.0)) + 1.0
        entry["last"] = ts.date().isoformat()
        target[key_in] = entry

    def _bump(self, stats: dict[str, dict[str, Any]], key: str, today_iso: str) -> None:
        entry = stats.get(key, _empty_entry())
        entry["n"] = float(entry.get("n", 0.0)) + 1.0
        entry["last"] = today_iso
        stats[key] = entry

    def _bump_ip(self, ip: str, ip_type: str | None, today_iso: str) -> None:
        entry = self.ip_stats.get(ip, _empty_entry())
        entry["n"] = float(entry.get("n", 0.0)) + 1.0
        entry["last"] = today_iso
        if ip_type:
            entry["type"] = ip_type
        self.ip_stats[ip] = entry

    def _welford_value(self, x: float) -> None:
        self.value_n += 1.0
        delta = x - self.value_mean
        self.value_mean += delta / self.value_n
        self.value_m2 += delta * (x - self.value_mean)

    def _welford_cadence(self, x: float) -> None:
        self.cadence_n += 1.0
        delta = x - self.cadence_mean_h
        self.cadence_mean_h += delta / self.cadence_n
        self.cadence_m2_h += delta * (x - self.cadence_mean_h)

    # -----------------------------------------------------------------------
    # Derived signals
    # -----------------------------------------------------------------------

    def ip_familiarity_tier(self, ip: str, ip_netblock: str, ip_asn: str | None) -> str:
        """Per verification §2.2: only /24 match confers family_familiar
        (the ASN-only shortcut was removed because ASN granularity is too
        coarse — all of GCP shouldn't count as 'family')."""
        if ip in self.ip_stats:
            return "familiar"
        if ip_netblock in self.ip_netblock_stats:
            return "family_familiar"
        if ip_asn and ip_asn in self.ip_asn_stats:
            return "new_known_asn"
        return "fully_new"

    def value_zscore(self, value: float) -> float:
        if self.value_n < 2 or self.value_m2 <= 0:
            return 0.0
        stddev = math.sqrt(self.value_m2 / self.value_n)
        return 0.0 if stddev == 0 else (value - self.value_mean) / stddev

    def cadence_zscore_hours(self, hours_since: float) -> float:
        if self.cadence_n < 2 or self.cadence_m2_h <= 0:
            return 0.0
        stddev = math.sqrt(self.cadence_m2_h / self.cadence_n)
        return 0.0 if stddev == 0 else (hours_since - self.cadence_mean_h) / stddev

    # -----------------------------------------------------------------------
    # Save
    # -----------------------------------------------------------------------

    async def save(self, conn: asyncpg.Connection) -> None:
        """UPSERT into customer_baselines. Caller has held the
        SELECT FOR UPDATE row-lock since load(for_update=True). On
        commit/rollback the lock releases (per .ai/gotchas/postgres.md)."""
        await conn.execute(
            """
            INSERT INTO customer_baselines (
                tenant_id, customer_id,
                origin_stats, dest_stats, lane_stats, ip_stats,
                ip_netblock_stats, ip_asn_stats, country_stats,
                origin_ip_country_stats, country_route_stats,
                email_hmacs, phone_hmacs,
                rejected_email_hmacs, rejected_phone_hmacs,
                email_domain_stats, phone_prefix_stats,
                ip_type_hist, hour_hist, weekday_hist, channel_hist,
                value_n, value_mean, value_m2,
                cadence_n, cadence_mean_h, cadence_m2_h,
                last_booking_ts, last_booking_lat, last_booking_lon,
                last_booking_country, decay_anchor_date, updated_at
            )
            VALUES (
                $1, $2,
                $3::jsonb, $4::jsonb, $5::jsonb, $6::jsonb,
                $7::jsonb, $8::jsonb, $9::jsonb,
                $10::jsonb, $11::jsonb,
                $12::jsonb, $13::jsonb,
                $14::jsonb, $15::jsonb,
                $16::jsonb, $17::jsonb,
                $18::jsonb, $19::jsonb, $20::jsonb, $21::jsonb,
                $22, $23, $24,
                $25, $26, $27,
                $28, $29, $30,
                $31, $32, now()
            )
            ON CONFLICT (tenant_id, customer_id) DO UPDATE SET
                origin_stats             = EXCLUDED.origin_stats,
                dest_stats               = EXCLUDED.dest_stats,
                lane_stats               = EXCLUDED.lane_stats,
                ip_stats                 = EXCLUDED.ip_stats,
                ip_netblock_stats        = EXCLUDED.ip_netblock_stats,
                ip_asn_stats             = EXCLUDED.ip_asn_stats,
                country_stats            = EXCLUDED.country_stats,
                origin_ip_country_stats  = EXCLUDED.origin_ip_country_stats,
                country_route_stats      = EXCLUDED.country_route_stats,
                email_hmacs              = EXCLUDED.email_hmacs,
                phone_hmacs              = EXCLUDED.phone_hmacs,
                rejected_email_hmacs     = EXCLUDED.rejected_email_hmacs,
                rejected_phone_hmacs     = EXCLUDED.rejected_phone_hmacs,
                email_domain_stats       = EXCLUDED.email_domain_stats,
                phone_prefix_stats       = EXCLUDED.phone_prefix_stats,
                ip_type_hist             = EXCLUDED.ip_type_hist,
                hour_hist                = EXCLUDED.hour_hist,
                weekday_hist             = EXCLUDED.weekday_hist,
                channel_hist             = EXCLUDED.channel_hist,
                value_n                  = EXCLUDED.value_n,
                value_mean               = EXCLUDED.value_mean,
                value_m2                 = EXCLUDED.value_m2,
                cadence_n                = EXCLUDED.cadence_n,
                cadence_mean_h           = EXCLUDED.cadence_mean_h,
                cadence_m2_h             = EXCLUDED.cadence_m2_h,
                last_booking_ts          = EXCLUDED.last_booking_ts,
                last_booking_lat         = EXCLUDED.last_booking_lat,
                last_booking_lon         = EXCLUDED.last_booking_lon,
                last_booking_country     = EXCLUDED.last_booking_country,
                decay_anchor_date        = EXCLUDED.decay_anchor_date,
                last_seen                = now(),
                updated_at               = now()
            """,
            self.tenant_id,
            self.customer_id,
            json.dumps(self.origin_stats),
            json.dumps(self.dest_stats),
            json.dumps(self.lane_stats),
            json.dumps(self.ip_stats),
            json.dumps(self.ip_netblock_stats),
            json.dumps(self.ip_asn_stats),
            json.dumps(self.country_stats),
            json.dumps(self.origin_ip_country_stats),
            json.dumps(self.country_route_stats),
            json.dumps(self.email_hmacs),
            json.dumps(self.phone_hmacs),
            json.dumps(self.rejected_email_hmacs),
            json.dumps(self.rejected_phone_hmacs),
            json.dumps(self.email_domain_stats),
            json.dumps(self.phone_prefix_stats),
            json.dumps(self.ip_type_hist),
            json.dumps(self.hour_hist),
            json.dumps(self.weekday_hist),
            json.dumps(self.channel_hist),
            self.value_n,
            self.value_mean,
            self.value_m2,
            self.cadence_n,
            self.cadence_mean_h,
            self.cadence_m2_h,
            self.last_booking_ts,
            self.last_booking_lat,
            self.last_booking_lon,
            self.last_booking_country,
            self.decay_anchor_date,
        )
