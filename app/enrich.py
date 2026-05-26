"""IP enrichment — MaxMind / FireHOL / IP2Proxy / cloud-CIDR aggregation.

The `Enricher` lazy-loads source files at first use. Missing files are
tolerated: looked-up IPs still return an `EnrichmentRow`, but the
corresponding fields are `None` / `False` (rules conditioned on those
flags then fire on False — no spurious positives).

Cached in the `ip_enrichment` Postgres table keyed by IP, with a 14-day
staleness window. The actual download of source files happens
out-of-process via `scripts/fetch_enrichment.py` (ECS scheduled task in
production, local cron in dev).

See `.ai/enrichment.md` for the pipeline contract and
`.ai/decisions.md` § IP enrichment for source URLs and refresh cadences.
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

import asyncpg
import structlog

from app.signal_helpers import is_datacenter_asn

_log = structlog.get_logger(__name__)

# Sentinel values from the IP2Proxy LITE PX11 BIN indicating "no data".
# `is_proxy` must be gated on `proxy_type` NOT being one of these (per
# verification §2.4) — a bare True with a sentinel `proxy_type` is a
# false positive.
_IP2P_NULL_SENTINELS: frozenset[str] = frozenset({
    "",
    "-",
    "INVALID IP ADDRESS",
    "NOT SUPPORTED",
    "INVALID DATABASE FILE",
    "DATABASE NOT FOUND",
})


@dataclass(frozen=True)
class EnrichmentRow:
    """One row of IP enrichment. `ip` is the only required field; every
    other column is None / False when the corresponding source had no
    data (file missing, IP not in the netset, etc.)."""

    ip: str
    country: str | None = None
    region: str | None = None
    city: str | None = None
    lat: float | None = None
    lon: float | None = None
    asn_org: str | None = None
    fh_level1: bool = False
    fh_level2: bool = False
    fh_lists: str | None = None
    is_cloud: bool = False
    cloud_provider: str | None = None
    is_datacenter: bool = False
    is_proxy: bool = False
    is_vpn: bool = False
    is_tor: bool = False
    proxy_type: str | None = None
    threat: str | None = None

    @classmethod
    def empty(cls, ip: str) -> EnrichmentRow:
        return cls(ip=ip)


class Enricher:
    """Per-process enrichment service. One instance shared by the app via
    the FastAPI lifespan; sources lazy-load at first `enrich()` call."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._loaded = False
        self._mm_city: Any = None
        self._mm_asn: Any = None
        self._ip2p: Any = None
        self._firehol_l1: Any = None  # pytricia.PyTricia or None
        self._firehol_l2: Any = None
        self._cloud_tries: dict[str, Any] = {}

    # -----------------------------------------------------------------------
    # Source loading — lazy, single-shot. Missing files are skipped silently
    # and logged at WARNING level.
    # -----------------------------------------------------------------------

    def _load_sources(self) -> None:
        if self._loaded:
            return
        self._loaded = True  # set early so partial failures don't re-attempt
        self._load_maxmind()
        self._load_ip2proxy()
        self._load_firehol()
        self._load_cloud_cidrs()

    def _load_maxmind(self) -> None:
        city_path = self._data_dir / "GeoLite2-City.mmdb"
        asn_path = self._data_dir / "GeoLite2-ASN.mmdb"
        try:
            import maxminddb
        except ImportError:
            _log.warning("enrich.maxmind_unavailable", reason="maxminddb not installed")
            return
        if city_path.exists():
            self._mm_city = maxminddb.open_database(str(city_path))
        else:
            _log.warning("enrich.maxmind_city_missing", path=str(city_path))
        if asn_path.exists():
            self._mm_asn = maxminddb.open_database(str(asn_path))
        else:
            _log.warning("enrich.maxmind_asn_missing", path=str(asn_path))

    def _load_ip2proxy(self) -> None:
        bin_path = self._data_dir / "IP2PROXY-LITE-PX11.BIN"
        if not bin_path.exists():
            _log.warning("enrich.ip2proxy_missing", path=str(bin_path))
            return
        try:
            import IP2Proxy
        except ImportError:
            _log.warning("enrich.ip2proxy_unavailable", reason="IP2Proxy not installed")
            return
        self._ip2p = IP2Proxy.IP2Proxy()
        self._ip2p.open(str(bin_path))

    def _load_firehol(self) -> None:
        try:
            import pytricia
        except ImportError:
            _log.warning("enrich.pytricia_unavailable", reason="pytricia not installed")
            return
        for level, attr in (("level1", "_firehol_l1"), ("level2", "_firehol_l2")):
            path = self._data_dir / f"firehol_{level}.netset"
            if not path.exists():
                _log.warning("enrich.firehol_missing", level=level, path=str(path))
                continue
            trie = pytricia.PyTricia(32)
            with path.open(encoding="utf-8") as f:
                for line in f:
                    cidr = line.strip()
                    if cidr and not cidr.startswith("#"):
                        try:
                            trie[cidr] = True
                        except ValueError:
                            continue
            setattr(self, attr, trie)

    def _load_cloud_cidrs(self) -> None:
        try:
            import pytricia
        except ImportError:
            return
        for provider in ("aws", "gcp", "azure", "cloudflare"):
            path = self._data_dir / f"{provider}.cidr"
            if not path.exists():
                _log.warning("enrich.cloud_cidr_missing", provider=provider, path=str(path))
                continue
            trie = pytricia.PyTricia(32)
            with path.open(encoding="utf-8") as f:
                for line in f:
                    cidr = line.strip()
                    if cidr and not cidr.startswith("#"):
                        try:
                            trie[cidr] = True
                        except ValueError:
                            continue
            self._cloud_tries[provider] = trie

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def enrich(
        self,
        conn: asyncpg.Connection,
        ip: IPv4Address,
    ) -> EnrichmentRow:
        """Lookup or refresh. 14-day staleness window; on stale or miss,
        re-run every source and upsert into `ip_enrichment`."""
        ip_str = str(ip)

        cached = await conn.fetchrow(
            """
            SELECT * FROM ip_enrichment
             WHERE ip = $1::inet
               AND updated_at > now() - interval '14 days'
            """,
            ip_str,
        )
        if cached is not None:
            _log.info("enrich.cache_hit", ip=ip_str, metric=True)
            return _record_to_row(cached)

        _log.info("enrich.cache_miss", ip=ip_str, metric=True)
        row = self._lookup(ip_str)
        await self._persist(conn, row)
        return row

    def _lookup(self, ip: str) -> EnrichmentRow:
        """Run every loaded source. Missing source → fields stay default."""
        self._load_sources()
        fields: dict[str, Any] = {"ip": ip}

        # MaxMind
        if self._mm_city is not None:
            geo = self._mm_city.get(ip) or {}
            country_blob = geo.get("country") or {}
            fields["country"] = country_blob.get("iso_code")
            subs = geo.get("subdivisions") or []
            if subs:
                fields["region"] = (subs[0].get("names") or {}).get("en")
            city_blob = geo.get("city") or {}
            fields["city"] = (city_blob.get("names") or {}).get("en")
            location = geo.get("location") or {}
            fields["lat"] = location.get("latitude")
            fields["lon"] = location.get("longitude")
        if self._mm_asn is not None:
            asn_blob = self._mm_asn.get(ip) or {}
            fields["asn_org"] = asn_blob.get("autonomous_system_organization")

        # FireHOL
        if self._firehol_l1 is not None and ip in self._firehol_l1:
            fields["fh_level1"] = True
        if self._firehol_l2 is not None and ip in self._firehol_l2:
            fields["fh_level2"] = True
        fh_hits = [
            name
            for name, hit in (("level1", fields.get("fh_level1")), ("level2", fields.get("fh_level2")))
            if hit
        ]
        if fh_hits:
            fields["fh_lists"] = ",".join(fh_hits)

        # Cloud CIDR match
        for provider, trie in self._cloud_tries.items():
            if ip in trie:
                fields["is_cloud"] = True
                fields["cloud_provider"] = provider
                break
        # ASN-fallback: cloud-providery brand in the ASN org text
        if not fields.get("is_cloud") and fields.get("asn_org"):
            asn_lower = fields["asn_org"].lower()
            for provider in ("amazon", "google", "microsoft", "cloudflare"):
                if provider in asn_lower:
                    fields["is_cloud"] = True
                    fields["cloud_provider"] = (
                        "aws"
                        if provider == "amazon"
                        else "gcp"
                        if provider == "google"
                        else "azure"
                        if provider == "microsoft"
                        else "cloudflare"
                    )
                    break

        fields["is_datacenter"] = is_datacenter_asn(fields.get("asn_org"))

        # IP2Proxy — gate is_proxy on non-sentinel proxy_type
        if self._ip2p is not None:
            try:
                rec = self._ip2p.get_all(ip)
            except Exception as exc:
                _log.warning("enrich.ip2proxy_lookup_failed", ip=ip, error=str(exc))
                rec = {}
            proxy_type = _clean_ip2p(rec.get("proxy_type"))
            threat = _clean_ip2p(rec.get("threat"))
            fields["proxy_type"] = proxy_type
            fields["threat"] = threat
            if proxy_type:
                fields["is_proxy"] = True
                fields["is_vpn"] = proxy_type == "VPN"
                fields["is_tor"] = proxy_type == "TOR"

        return EnrichmentRow(**fields)

    async def _persist(self, conn: asyncpg.Connection, row: EnrichmentRow) -> None:
        await conn.execute(
            """
            INSERT INTO ip_enrichment (
                ip, country, region, city, lat, lon, asn_org,
                fh_level1, fh_level2, fh_lists,
                is_cloud, cloud_provider, is_datacenter,
                is_proxy, is_vpn, is_tor, proxy_type, threat, updated_at
            ) VALUES (
                $1::inet, $2, $3, $4, $5, $6, $7,
                $8, $9, $10,
                $11, $12, $13,
                $14, $15, $16, $17, $18, now()
            )
            ON CONFLICT (ip) DO UPDATE SET
                country         = EXCLUDED.country,
                region          = EXCLUDED.region,
                city            = EXCLUDED.city,
                lat             = EXCLUDED.lat,
                lon             = EXCLUDED.lon,
                asn_org         = EXCLUDED.asn_org,
                fh_level1       = EXCLUDED.fh_level1,
                fh_level2       = EXCLUDED.fh_level2,
                fh_lists        = EXCLUDED.fh_lists,
                is_cloud        = EXCLUDED.is_cloud,
                cloud_provider  = EXCLUDED.cloud_provider,
                is_datacenter   = EXCLUDED.is_datacenter,
                is_proxy        = EXCLUDED.is_proxy,
                is_vpn          = EXCLUDED.is_vpn,
                is_tor          = EXCLUDED.is_tor,
                proxy_type      = EXCLUDED.proxy_type,
                threat          = EXCLUDED.threat,
                updated_at      = now()
            """,
            row.ip, row.country, row.region, row.city, row.lat, row.lon, row.asn_org,
            row.fh_level1, row.fh_level2, row.fh_lists,
            row.is_cloud, row.cloud_provider, row.is_datacenter,
            row.is_proxy, row.is_vpn, row.is_tor, row.proxy_type, row.threat,
        )


def _clean_ip2p(value: Any) -> str | None:
    """Strip IP2Proxy sentinels. Returns None when the value indicates
    'no data' so the caller's `if proxy_type:` test fails correctly."""
    if value is None:
        return None
    s = str(value).strip()
    if s in _IP2P_NULL_SENTINELS:
        return None
    # Defensive: non-printable bytes from a corrupt BIN
    if not s.isprintable():
        return None
    return s


def _record_to_row(record: asyncpg.Record) -> EnrichmentRow:
    return EnrichmentRow(
        ip=str(record["ip"]),
        country=record["country"],
        region=record["region"],
        city=record["city"],
        lat=float(record["lat"]) if record["lat"] is not None else None,
        lon=float(record["lon"]) if record["lon"] is not None else None,
        asn_org=record["asn_org"],
        fh_level1=record["fh_level1"],
        fh_level2=record["fh_level2"],
        fh_lists=record["fh_lists"],
        is_cloud=record["is_cloud"],
        cloud_provider=record["cloud_provider"],
        is_datacenter=record["is_datacenter"],
        is_proxy=record["is_proxy"],
        is_vpn=record["is_vpn"],
        is_tor=record["is_tor"],
        proxy_type=record["proxy_type"],
        threat=record["threat"],
    )
