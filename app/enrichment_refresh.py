"""Pattern B-lite enrichment refresh module.

Per-source async downloaders + atomic file replacement + two-stage
sanity floors + retry-with-jittered-backoff. This module exposes the
per-source primitives + helpers; the refresh loop and FastAPI lifespan
integration consume them (see `.ai/enrichment.md` § Refresh module).

License keys (MaxMind, IP2Proxy) flow through URL query strings.
This module constructs the licensed URL inside per-source downloader
functions and confines the secret to a local variable; structured
log fields and metric dimensions reference only `source_name`. URLs
are never logged or re-raised in exceptions. See `_safe_str_exc`.

See `.ai/enrichment.md` for the architecture write-up + disk-budget
note (IP2Proxy LITE PX11 extracted BIN is ~1.6 GB; `atomic_replace`
+ `atomic_replace_stream` exist as separate primitives so the small
sources stay on the bytes form while IP2Proxy streams through).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import ipaddress
import json
import os
import random
import re
import secrets
import tarfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final, Literal

import httpx
import structlog

from app.config import Settings

_log = structlog.get_logger(__name__)


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

_FIREHOL_BASE: Final[str] = "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master"
_MAXMIND_BASE: Final[str] = "https://download.maxmind.com/app/geoip_download"
_IP2PROXY_BASE: Final[str] = "https://www.ip2location.com/download/"
_AWS_URL: Final[str] = "https://ip-ranges.amazonaws.com/ip-ranges.json"
_GCP_URL: Final[str] = "https://www.gstatic.com/ipranges/cloud.json"
_CLOUDFLARE_URL: Final[str] = "https://www.cloudflare.com/ips-v4"
_AZURE_DETAILS_URL: Final[str] = "https://www.microsoft.com/en-us/download/details.aspx?id=56519"
# Regex for the dated ServiceTags JSON URL embedded in the Microsoft
# download confirmation page. The URL changes weekly; the dated filename
# pattern is the stable anchor.
_AZURE_JSON_URL_RE: Final[re.Pattern[bytes]] = re.compile(
    rb"https://download\.microsoft\.com/download/[^\"\s<>]+ServiceTags_Public_\d+\.json"
)

# Raw download floors (bytes). Applied to the upstream response body
# BEFORE any extract/parse. A response below the floor surfaces as a
# `skipped_sanity_floor` RefreshResult and the existing on-disk artifact
# (if any) is preserved.
_SANITY_FLOORS: Final[dict[str, int]] = {
    "firehol_level1": 50_000,
    "firehol_level2": 50_000,
    "maxmind_city": 30_000_000,
    "maxmind_asn": 5_000_000,
    "ip2proxy": 30_000_000,
    "aws": 30_000,
    "gcp": 5_000,
    "azure": 1_000_000,
    "cloudflare": 100,
}

# Post-extract floors (bytes). Applied AFTER parse/extract and BEFORE
# atomic_replace writes the on-disk artifact the Enricher loads.
# Defends against "raw response looks legitimate but extracted/parsed
# artifact is empty or near-empty" (silent degradation per
# verification-pattern-b-lite.md V-3).
_EXTRACTED_FLOORS: Final[dict[str, int]] = {
    "aws_extracted": 5_000,
    "gcp_extracted": 1_000,
    "azure_extracted": 50_000,
    "ip2proxy_extracted": 500_000_000,
}

_REFRESH_INTERVAL_SECONDS: Final[int] = 24 * 60 * 60
_RETRY_MAX_ATTEMPTS: Final[int] = 3
_RETRY_BASE_DELAY_SECONDS: Final[float] = 2.0
_DEFAULT_HTTP_TIMEOUT: Final[float] = 60.0
_DEFAULT_IP2P_TIMEOUT: Final[float] = 300.0  # 82 MB ZIP at slow links
_STREAM_CHUNK_SIZE: Final[int] = 1 << 20  # 1 MiB

# IP2Proxy rate-limit response body prefix (observed 2026-06-09).
# Upstream returns a 56-byte ASCII body starting with this literal when
# the per-token 5/24h cap is hit. Distinguished from a real BIN/ZIP via
# prefix-match before the magic-byte ladder.
_IP2P_RATE_LIMIT_PREFIX: Final[bytes] = b"THIS FILE CAN ONLY BE DOWNLOADED"

# Magic-byte signatures
_ZIP_MAGIC: Final[bytes] = b"PK\x03\x04"
_GZIP_MAGIC: Final[bytes] = b"\x1f\x8b"
_HTML_PREFIXES: Final[tuple[bytes, ...]] = (b"<!", b"<?", b"<h", b"<H")

# IP2Proxy LITE PX11 archive member name (verified 2026-06-09).
_IP2P_BIN_MEMBER: Final[str] = "IP2PROXY-LITE-PX11.BIN"

# On-disk target filenames the Enricher's _load_sources reads.
_TARGET_FIREHOL_L1: Final[str] = "firehol_level1.netset"
_TARGET_FIREHOL_L2: Final[str] = "firehol_level2.netset"
_TARGET_MAXMIND_CITY: Final[str] = "GeoLite2-City.mmdb"
_TARGET_MAXMIND_ASN: Final[str] = "GeoLite2-ASN.mmdb"
_TARGET_IP2PROXY: Final[str] = "IP2PROXY-LITE-PX11.BIN"
_TARGET_AWS: Final[str] = "aws.cidr"
_TARGET_GCP: Final[str] = "gcp.cidr"
_TARGET_AZURE: Final[str] = "azure.cidr"
_TARGET_CLOUDFLARE: Final[str] = "cloudflare.cidr"

# Module-level health-probe state; no lock.
# `_loaded_sources` is single-writer (refresh task's mark_source_loaded)
# multi-reader (health probe's all_sources_loaded_at_least_once).
# set.add and set.__contains__ are GIL-atomic on CPython.
_loaded_sources: set[str] = set()

# Set of source names tracked by the health probe. Matches the keys of
# `_SANITY_FLOORS`. A source is "loaded" once it has either successfully
# refreshed OR was present on disk at startup (hybrid Pattern A defense).
_ALL_SOURCE_NAMES: Final[frozenset[str]] = frozenset(_SANITY_FLOORS.keys())


# ----------------------------------------------------------------------------
# Public types
# ----------------------------------------------------------------------------


RefreshStatus = Literal["success", "failed", "skipped_sanity_floor"]
FailureClass = Literal[
    "network",
    "parse_error",
    "rate_limited",
    "upstream_html",
    "other",
]


@dataclass(frozen=True)
class RefreshResult:
    """Outcome of a single per-source refresh attempt.

    `error` is the str-form of any caught exception (not the Exception
    object) so log records don't accidentally pickle the original
    traceback, which could include URL fragments. `failure_class` is
    populated only for `status="failed"`.
    """

    source_name: str
    status: RefreshStatus
    bytes_written: int | None = None
    error: str | None = None
    failure_class: FailureClass | None = None
    duration_ms: float = 0.0


# ----------------------------------------------------------------------------
# Health-probe state helpers
# ----------------------------------------------------------------------------


def mark_source_loaded(source_name: str) -> None:
    """Mark a source as having successfully loaded at least once.

    Called by the refresh task on each successful per-source refresh.
    Also called by `seed_loaded_from_disk` for sources whose files were
    present at startup (hybrid Pattern A defense — supports environments
    where enrichment data was baked into the image and refresh hasn't
    run a full tick yet).
    """
    _loaded_sources.add(source_name)


def all_sources_loaded_at_least_once() -> bool:
    """True iff every known source name is in `_loaded_sources`."""
    return _loaded_sources >= _ALL_SOURCE_NAMES


def loaded_sources_snapshot() -> frozenset[str]:
    """Read-only view of the loaded-sources set for diagnostics."""
    return frozenset(_loaded_sources)


def _reset_loaded_sources_for_tests() -> None:
    """Test-only: clear `_loaded_sources`. Production code does not call."""
    _loaded_sources.clear()


def seed_loaded_from_disk(data_dir: Path) -> None:
    """For each known source, if the Enricher's on-disk artifact already
    exists in `data_dir`, mark the source as loaded.

    Lets the hybrid Pattern A path register baked-in image files so the
    health probe reports `ok` from cold start when files are present,
    without waiting for the first refresh tick.
    """
    on_disk = {
        "firehol_level1": data_dir / _TARGET_FIREHOL_L1,
        "firehol_level2": data_dir / _TARGET_FIREHOL_L2,
        "maxmind_city": data_dir / _TARGET_MAXMIND_CITY,
        "maxmind_asn": data_dir / _TARGET_MAXMIND_ASN,
        "ip2proxy": data_dir / _TARGET_IP2PROXY,
        "aws": data_dir / _TARGET_AWS,
        "gcp": data_dir / _TARGET_GCP,
        "azure": data_dir / _TARGET_AZURE,
        "cloudflare": data_dir / _TARGET_CLOUDFLARE,
    }
    for source_name, path in on_disk.items():
        if path.exists() and path.stat().st_size > 0:
            mark_source_loaded(source_name)


# ----------------------------------------------------------------------------
# Atomic file replacement
# ----------------------------------------------------------------------------


def _tempfile_path(target: Path) -> Path:
    """Tempfile beside the target, sharing the same filesystem so
    `os.rename` is atomic on POSIX. Random suffix prevents collisions
    if two refreshes ever race on the same target (which shouldn't
    happen, but defensive)."""
    return target.parent / f"{target.name}.tmp.{secrets.token_hex(4)}"


async def atomic_replace(target: Path, content: bytes) -> int:
    """Write `content` to a tempfile in `target.parent`, fsync, rename.

    Returns bytes written. On any IO error, the tempfile is cleaned up
    and the original target (if any) is preserved.

    For sources whose extracted/post-parse content fits comfortably in
    memory (< 100 MB). IP2Proxy (1.6 GB BIN) uses `atomic_replace_stream`
    instead.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tempfile_path(target)

    def _write() -> int:
        # Open with O_CLOEXEC; create new (O_EXCL) so a stale tempfile
        # collision surfaces as an error rather than silently appending.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        fd = os.open(str(tmp), flags, 0o644)
        try:
            written = os.write(fd, content)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(str(tmp), str(target))
        return written

    try:
        return await asyncio.to_thread(_write)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


async def atomic_replace_stream(
    target: Path,
    src: IO[bytes],
    *,
    chunk_size: int = _STREAM_CHUNK_SIZE,
) -> int:
    """Streaming variant: copy `src` to a tempfile in chunks, fsync, rename.

    Used by the IP2Proxy downloader (1.6 GB BIN extracted from a 82 MB
    ZIP; loading the BIN into a Python `bytes` object would cost 1.6 GB
    of RAM in the refresh task). `src` is any binary file-like object —
    typically the fileobj returned by `zipfile.ZipFile.open(member)`,
    which itself streams the deflate decoder.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tempfile_path(target)

    def _stream() -> int:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        fd = os.open(str(tmp), flags, 0o644)
        total = 0
        try:
            while True:
                chunk = src.read(chunk_size)
                if not chunk:
                    break
                pos = 0
                while pos < len(chunk):
                    pos += os.write(fd, chunk[pos:])
                total += len(chunk)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(str(tmp), str(target))
        return total

    try:
        return await asyncio.to_thread(_stream)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


# ----------------------------------------------------------------------------
# HTTP with retry + backoff
# ----------------------------------------------------------------------------


def _safe_str_exc(exc: BaseException) -> str:
    """Sanitized exception string. httpx exceptions can carry the full
    URL (including query string with license key) in their `__str__`.
    We log only the exception class name and a generic class-of-failure
    descriptor — never the URL or any query parameter."""
    cls = type(exc).__name__
    # httpx exception class names are stable + descriptive (ConnectError,
    # ReadTimeout, HTTPStatusError, etc.) — surfacing the class alone
    # carries enough information for ops triage without leaking secrets.
    return cls


async def _sleep_with_jitter(attempt: int) -> None:
    """Sleep for `_RETRY_BASE_DELAY_SECONDS * 2**attempt` with ±25% jitter.

    Attempt 0 → ~2s, attempt 1 → ~4s, attempt 2 → ~8s (±25% each).
    Jitter spreads thundering-herd retries when multiple sources fail
    on the same network blip.
    """
    base = _RETRY_BASE_DELAY_SECONDS * (2**attempt)
    jitter = base * 0.25 * (2 * random.random() - 1)
    await asyncio.sleep(max(0.0, base + jitter))


async def _http_get_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    source_name: str,
    timeout: float = _DEFAULT_HTTP_TIMEOUT,
) -> bytes:
    """GET `url` with bounded retries + jittered exponential backoff.

    On 4xx response: raises `httpx.HTTPStatusError` immediately (no
    retry — 4xx is upstream rejection, retrying won't help).
    On 5xx / network error: retry up to `_RETRY_MAX_ATTEMPTS` times.
    On final failure: raises the last exception.

    The URL is constructed by the caller (license key inlined into
    query string when needed). This function never logs the URL or
    embeds it in raised exceptions.
    """
    last_exc: BaseException | None = None
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            response = await client.get(url, timeout=timeout)
            if 400 <= response.status_code < 500:
                # Don't retry 4xx; surface immediately with sanitized message.
                response.raise_for_status()
            response.raise_for_status()
            body: bytes = response.content
            return body
        except httpx.HTTPStatusError as exc:
            # 4xx → fatal, no retry. 5xx → retry below. Anything else
            # (a 3xx that wasn't followed, or any other unexpected
            # non-2xx) → fatal: retrying a redirect or other terminal
            # status never resolves, so surface it immediately instead
            # of burning the retry budget.
            status_code = exc.response.status_code
            if 400 <= status_code < 500:
                _log.warning(
                    "enrich.refresh.http_4xx",
                    source_name=source_name,
                    status_code=status_code,
                )
                raise
            if not 500 <= status_code < 600:
                _log.warning(
                    "enrich.refresh.http_unexpected",
                    source_name=source_name,
                    status_code=status_code,
                )
                raise
            last_exc = exc
            _log.warning(
                "enrich.refresh.http_5xx",
                source_name=source_name,
                status_code=status_code,
                attempt=attempt,
            )
        except (httpx.HTTPError, OSError) as exc:
            last_exc = exc
            _log.warning(
                "enrich.refresh.network_error",
                source_name=source_name,
                error_class=_safe_str_exc(exc),
                attempt=attempt,
            )
        if attempt < _RETRY_MAX_ATTEMPTS - 1:
            await _sleep_with_jitter(attempt)
    assert last_exc is not None
    raise last_exc


# ----------------------------------------------------------------------------
# Per-source downloaders — primitives shared by the per-source functions
# ----------------------------------------------------------------------------


def _check_raw_floor(body: bytes, source_name: str) -> RefreshResult | None:
    """Raw-stage floor check: consults `_SANITY_FLOORS` keyed by
    `source_name`. Returns a skipped_sanity_floor RefreshResult if body
    size is below the floor; None otherwise."""
    floor = _SANITY_FLOORS[source_name]
    if len(body) < floor:
        return _emit_floor_skip(source_name, len(body), floor)
    return None


def _check_extracted_floor_bytes(body: bytes, source_name_for_metric: str) -> RefreshResult | None:
    """Post-parse floor check on extracted bytes (AWS/GCP/Azure case).
    `source_name_for_metric` is the `_extracted` key, also used as the
    metric source_name so the dashboard distinguishes raw-stage from
    extracted-stage skips."""
    floor = _EXTRACTED_FLOORS[source_name_for_metric]
    if len(body) < floor:
        return _emit_floor_skip(source_name_for_metric, len(body), floor)
    return None


def _emit_floor_skip(source_name: str, bytes_attempted: int, floor_bytes: int) -> RefreshResult:
    """Shared floor-skip emit. Logs the EMF metric + returns the result."""
    _log.warning(
        "enrich.refresh.skipped_sanity_floor",
        source_name=source_name,
        bytes_attempted=bytes_attempted,
        floor_bytes=floor_bytes,
        metric=True,
    )
    return RefreshResult(
        source_name=source_name,
        status="skipped_sanity_floor",
        bytes_written=None,
    )


def _log_failure(source_name: str, failure_class: FailureClass, error: str | None) -> RefreshResult:
    """Emit the EMF failure metric + return a failed RefreshResult."""
    _log.warning(
        "enrich.refresh.failure",
        source_name=source_name,
        failure_class=failure_class,
        metric=True,
    )
    return RefreshResult(
        source_name=source_name,
        status="failed",
        error=error,
        failure_class=failure_class,
    )


def _log_success(source_name: str, bytes_written: int, duration_ms: float) -> RefreshResult:
    """Emit the EMF success metric + return a successful RefreshResult."""
    _log.info(
        "enrich.refresh.success",
        source_name=source_name,
        duration_ms=duration_ms,
        bytes_written=bytes_written,
        metric=True,
    )
    return RefreshResult(
        source_name=source_name,
        status="success",
        bytes_written=bytes_written,
        duration_ms=duration_ms,
    )


# ----------------------------------------------------------------------------
# FireHOL — simple text netset; download → atomic_replace as-is
# ----------------------------------------------------------------------------


async def _refresh_firehol_level(
    client: httpx.AsyncClient,
    target_dir: Path,
    level: Literal["level1", "level2"],
) -> RefreshResult:
    source_name = f"firehol_{level}"
    url = f"{_FIREHOL_BASE}/firehol_{level}.netset"
    target = target_dir / f"firehol_{level}.netset"
    start = time.perf_counter()
    try:
        body = await _http_get_with_retries(client, url, source_name=source_name)
    except httpx.HTTPError as exc:
        return _log_failure(source_name, "network", _safe_str_exc(exc))
    if (skip := _check_raw_floor(body, source_name)) is not None:
        return skip
    try:
        written = await atomic_replace(target, body)
    except OSError as exc:
        return _log_failure(source_name, "other", _safe_str_exc(exc))
    duration_ms = (time.perf_counter() - start) * 1000.0
    return _log_success(source_name, written, duration_ms)


async def refresh_firehol_level1(client: httpx.AsyncClient, target_dir: Path) -> RefreshResult:
    return await _refresh_firehol_level(client, target_dir, "level1")


async def refresh_firehol_level2(client: httpx.AsyncClient, target_dir: Path) -> RefreshResult:
    return await _refresh_firehol_level(client, target_dir, "level2")


# ----------------------------------------------------------------------------
# MaxMind — tar.gz containing GeoLite2-<edition>_YYYYMMDD/GeoLite2-<edition>.mmdb
# ----------------------------------------------------------------------------


def _extract_mmdb_from_tarball(body: bytes, edition: str) -> bytes:
    """Extract the .mmdb member from a MaxMind tar.gz response body.

    Raises `tarfile.TarError` if the archive is unreadable or the
    expected member isn't present.
    """
    expected_suffix = f"GeoLite2-{edition}.mmdb"
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name.endswith(expected_suffix):
                extracted = tar.extractfile(member)
                if extracted is None:
                    msg = f"member {member.name} not extractable"
                    raise tarfile.TarError(msg)
                return extracted.read()
    msg = f"no .{expected_suffix} member in archive"
    raise tarfile.TarError(msg)


async def _refresh_maxmind(
    client: httpx.AsyncClient,
    target_dir: Path,
    settings: Settings,
    edition: Literal["City", "ASN"],
) -> RefreshResult:
    source_name = f"maxmind_{edition.lower()}"
    target = target_dir / f"GeoLite2-{edition}.mmdb"
    license_key = settings.maxmind_license_key
    if not license_key:
        _log.warning(
            "enrich.refresh.skipped_no_license",
            source_name=source_name,
        )
        return _log_failure(source_name, "other", "no license key configured")
    url = f"{_MAXMIND_BASE}?edition_id=GeoLite2-{edition}&license_key={license_key}&suffix=tar.gz"
    start = time.perf_counter()
    try:
        body = await _http_get_with_retries(
            client, url, source_name=source_name, timeout=_DEFAULT_IP2P_TIMEOUT
        )
    except httpx.HTTPError as exc:
        return _log_failure(source_name, "network", _safe_str_exc(exc))
    if (skip := _check_raw_floor(body, source_name)) is not None:
        return skip
    try:
        mmdb_bytes = _extract_mmdb_from_tarball(body, edition)
    except (tarfile.TarError, OSError) as exc:
        return _log_failure(source_name, "parse_error", _safe_str_exc(exc))
    try:
        written = await atomic_replace(target, mmdb_bytes)
    except OSError as exc:
        return _log_failure(source_name, "other", _safe_str_exc(exc))
    duration_ms = (time.perf_counter() - start) * 1000.0
    return _log_success(source_name, written, duration_ms)


async def refresh_maxmind_city(
    client: httpx.AsyncClient, target_dir: Path, settings: Settings
) -> RefreshResult:
    return await _refresh_maxmind(client, target_dir, settings, "City")


async def refresh_maxmind_asn(
    client: httpx.AsyncClient, target_dir: Path, settings: Settings
) -> RefreshResult:
    return await _refresh_maxmind(client, target_dir, settings, "ASN")


# ----------------------------------------------------------------------------
# IP2Proxy — ZIP + defensive magic-byte detection
# ----------------------------------------------------------------------------


async def refresh_ip2proxy(
    client: httpx.AsyncClient, target_dir: Path, settings: Settings
) -> RefreshResult:
    """ZIP-extract refresh path:
    1. Fetch ZIP response (~82 MB).
    2. Raw sanity floor (30 MB) — catches rate-limit / login-page bodies.
    3. Rate-limit prefix detection on `THIS FILE CAN ONLY BE DOWNLOADED`
       → failure_class="rate_limited" (distinguishes upstream throttle
       from broken upstream in ops dashboards).
    4. HTML prefix detection (token-rejected → /log-in redirect)
       → failure_class="upstream_html".
    5. Magic-byte branch ladder: ZIP / gzip-tar / direct BIN.
    6. ZIP path: open inner member, stream to tempfile via
       atomic_replace_stream (1.6 GB target file; loading into bytes
       would OOM).
    7. Extracted sanity floor (500 MB) after fsync, before rename
       (via the target stat post-stream).
    """
    source_name = "ip2proxy"
    target = target_dir / _TARGET_IP2PROXY
    token = settings.ip2proxy_download_token
    if not token:
        _log.warning(
            "enrich.refresh.skipped_no_license",
            source_name=source_name,
        )
        return _log_failure(source_name, "other", "no token configured")
    url = f"{_IP2PROXY_BASE}?token={token}&file=PX11LITEBIN"
    start = time.perf_counter()
    try:
        body = await _http_get_with_retries(
            client, url, source_name=source_name, timeout=_DEFAULT_IP2P_TIMEOUT
        )
    except httpx.HTTPError as exc:
        return _log_failure(source_name, "network", _safe_str_exc(exc))

    # Rate-limit detection — runs BEFORE the size floor because a
    # 56-byte rate-limit body is smaller than the floor; we want the
    # `rate_limited` failure_class, not `skipped_sanity_floor`.
    if body.startswith(_IP2P_RATE_LIMIT_PREFIX):
        return _log_failure(source_name, "rate_limited", "upstream daily download quota exceeded")

    # HTML-page detection (token rejected, redirected to /log-in).
    if body.startswith(_HTML_PREFIXES):
        return _log_failure(
            source_name, "upstream_html", "upstream returned HTML; token may be invalid"
        )

    if (skip := _check_raw_floor(body, source_name)) is not None:
        return skip

    # Magic-byte branch ladder. Each branch writes target on success and
    # returns either a failure RefreshResult OR None (file written; common
    # post-extract floor + cleanup tail handles the rest, unified for all
    # three branches so the extracted-floor cleanup is symmetric).
    if body.startswith(_ZIP_MAGIC):
        extraction_failure = await _extract_ip2proxy_zip(body, target, source_name)
    elif body.startswith(_GZIP_MAGIC):
        extraction_failure = await _extract_ip2proxy_tarball(body, target, source_name)
    else:
        # Direct BIN fallback: write the response bytes as-is.
        try:
            await atomic_replace(target, body)
            extraction_failure = None
        except OSError as exc:
            return _log_failure(source_name, "other", _safe_str_exc(exc))

    if extraction_failure is not None:
        return extraction_failure

    # Unified post-extract floor check + cleanup. Applies to ZIP, tar.gz,
    # AND direct-BIN — fixes the cycle-1 asymmetry where direct-BIN left
    # a sub-floor truncated artifact on disk.
    if (extracted_skip := _check_extracted_floor_on_disk(target, source_name)) is not None:
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)
        return extracted_skip

    written = target.stat().st_size
    duration_ms = (time.perf_counter() - start) * 1000.0
    return _log_success(source_name, written, duration_ms)


def _check_extracted_floor_on_disk(target: Path, source_name: str) -> RefreshResult | None:
    """Post-stream floor check for IP2Proxy (reads `target.stat().st_size`).
    Returns a skipped_sanity_floor RefreshResult if the on-disk file is
    smaller than the extracted floor; None otherwise. Caller is
    responsible for unlinking the artifact on skip."""
    extracted_key = f"{source_name}_extracted"
    floor = _EXTRACTED_FLOORS.get(extracted_key)
    if floor is None:
        return None
    written = target.stat().st_size
    if written < floor:
        result = _emit_floor_skip(extracted_key, written, floor)
        # Mirror the bytes_written into the result so observability can
        # see what was actually written before we unlink.
        return RefreshResult(
            source_name=result.source_name,
            status=result.status,
            bytes_written=written,
        )
    return None


async def _extract_ip2proxy_zip(
    body: bytes, target: Path, source_name: str
) -> RefreshResult | None:
    """Extract the BIN member from a ZIP body via streaming to tempfile.

    Returns a failure RefreshResult on parse/IO error; returns None on
    success (caller handles success accounting after post-extract floor).
    """
    try:
        with zipfile.ZipFile(io.BytesIO(body), mode="r") as zf:
            if _IP2P_BIN_MEMBER not in zf.namelist():
                msg = f"ZIP missing expected member {_IP2P_BIN_MEMBER!r}"
                return _log_failure(source_name, "parse_error", msg)
            with zf.open(_IP2P_BIN_MEMBER) as member:
                await atomic_replace_stream(target, member)
    except (zipfile.BadZipFile, OSError) as exc:
        return _log_failure(source_name, "parse_error", _safe_str_exc(exc))
    return None


async def _extract_ip2proxy_tarball(
    body: bytes, target: Path, source_name: str
) -> RefreshResult | None:
    """Defensive fallback: extract the BIN from a tar.gz body if upstream
    ever switches format. Same return shape as _extract_ip2proxy_zip.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.isfile() and member.name.endswith(_TARGET_IP2PROXY):
                    extracted = tar.extractfile(member)
                    if extracted is None:
                        msg = f"member {member.name} not extractable"
                        return _log_failure(source_name, "parse_error", msg)
                    await atomic_replace_stream(target, extracted)
                    return None
        return _log_failure(source_name, "parse_error", f"tar.gz missing {_TARGET_IP2PROXY!r}")
    except (tarfile.TarError, OSError) as exc:
        return _log_failure(source_name, "parse_error", _safe_str_exc(exc))


# ----------------------------------------------------------------------------
# Cloud CIDR sources — AWS / GCP / Azure / Cloudflare
# ----------------------------------------------------------------------------


def _cidr_list_to_bytes(cidrs: list[str]) -> bytes:
    """Serialize a CIDR list to the newline-separated format the Enricher
    expects (`firehol_*.netset` shape). One CIDR per line, no header."""
    return ("\n".join(cidrs) + "\n").encode("ascii")


def _filter_valid_ipv4_cidrs(raw: list[str]) -> list[str]:
    """Validate each CIDR string is parseable as IPv4Network. Skip
    silently on parse errors — upstream may include IPv6 entries which
    we discard."""
    valid: list[str] = []
    for cidr in raw:
        try:
            ipaddress.IPv4Network(cidr, strict=False)
        except (ValueError, TypeError):
            # AddressValueError is a ValueError subclass; ValueError covers both.
            continue
        valid.append(cidr)
    return valid


CidrExtractor = Callable[[dict[str, object]], list[str]]


async def _handle_json_cidr_body(
    body: bytes,
    *,
    source_name: str,
    target: Path,
    extract_cidrs: CidrExtractor,
    start: float,
) -> RefreshResult:
    """Common tail: parse JSON → extract CIDR list → extracted floor →
    atomic_replace → success/failure accounting. Shared by AWS, GCP, and
    Azure (the JSON-fetch half — Azure adds an extra HTML-scrape preamble
    in `refresh_azure`)."""
    if (skip := _check_raw_floor(body, source_name)) is not None:
        return skip
    try:
        data = json.loads(body)
        cidrs = extract_cidrs(data)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return _log_failure(source_name, "parse_error", _safe_str_exc(exc))
    serialized = _cidr_list_to_bytes(cidrs)
    extracted_key = f"{source_name}_extracted"
    if (skip := _check_extracted_floor_bytes(serialized, extracted_key)) is not None:
        return skip
    try:
        written = await atomic_replace(target, serialized)
    except OSError as exc:
        return _log_failure(source_name, "other", _safe_str_exc(exc))
    duration_ms = (time.perf_counter() - start) * 1000.0
    return _log_success(source_name, written, duration_ms)


async def _refresh_json_cidr_source(
    client: httpx.AsyncClient,
    target_dir: Path,
    *,
    source_name: str,
    url: str,
    target_filename: str,
    extract_cidrs: CidrExtractor,
) -> RefreshResult:
    """One-step driver for AWS/GCP: fetch JSON, then delegate to the
    shared tail. Azure has a two-step variant in `refresh_azure`."""
    target = target_dir / target_filename
    start = time.perf_counter()
    try:
        body = await _http_get_with_retries(client, url, source_name=source_name)
    except httpx.HTTPError as exc:
        return _log_failure(source_name, "network", _safe_str_exc(exc))
    return await _handle_json_cidr_body(
        body,
        source_name=source_name,
        target=target,
        extract_cidrs=extract_cidrs,
        start=start,
    )


def _extract_aws_cidrs(data: dict[str, object]) -> list[str]:
    prefixes = data.get("prefixes")
    if not isinstance(prefixes, list):
        return []
    out: list[str] = []
    for entry in prefixes:
        if not isinstance(entry, dict):
            continue
        cidr = entry.get("ip_prefix")
        if isinstance(cidr, str):
            out.append(cidr)
    return _filter_valid_ipv4_cidrs(out)


def _extract_gcp_cidrs(data: dict[str, object]) -> list[str]:
    prefixes = data.get("prefixes")
    if not isinstance(prefixes, list):
        return []
    out: list[str] = []
    for entry in prefixes:
        if not isinstance(entry, dict):
            continue
        cidr = entry.get("ipv4Prefix")
        if isinstance(cidr, str):
            out.append(cidr)
    return _filter_valid_ipv4_cidrs(out)


def _extract_azure_cidrs(data: dict[str, object]) -> list[str]:
    values = data.get("values")
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value_entry in values:
        if not isinstance(value_entry, dict):
            continue
        props = value_entry.get("properties")
        if not isinstance(props, dict):
            continue
        prefixes = props.get("addressPrefixes")
        if not isinstance(prefixes, list):
            continue
        for prefix in prefixes:
            if isinstance(prefix, str):
                out.append(prefix)
    return _filter_valid_ipv4_cidrs(out)


async def refresh_aws(client: httpx.AsyncClient, target_dir: Path) -> RefreshResult:
    return await _refresh_json_cidr_source(
        client,
        target_dir,
        source_name="aws",
        url=_AWS_URL,
        target_filename=_TARGET_AWS,
        extract_cidrs=_extract_aws_cidrs,
    )


async def refresh_gcp(client: httpx.AsyncClient, target_dir: Path) -> RefreshResult:
    return await _refresh_json_cidr_source(
        client,
        target_dir,
        source_name="gcp",
        url=_GCP_URL,
        target_filename=_TARGET_GCP,
        extract_cidrs=_extract_gcp_cidrs,
    )


async def refresh_azure(client: httpx.AsyncClient, target_dir: Path) -> RefreshResult:
    """Two-step: scrape Microsoft download page for the dated JSON URL,
    then delegate to the shared `_handle_json_cidr_body` tail. The URL
    changes weekly; the dated filename pattern is the stable anchor."""
    source_name = "azure"
    target = target_dir / _TARGET_AZURE
    start = time.perf_counter()
    try:
        details_body = await _http_get_with_retries(
            client, _AZURE_DETAILS_URL, source_name=source_name
        )
    except httpx.HTTPError as exc:
        return _log_failure(source_name, "network", _safe_str_exc(exc))
    match = _AZURE_JSON_URL_RE.search(details_body)
    if match is None:
        return _log_failure(
            source_name,
            "parse_error",
            "no ServiceTags_Public JSON URL in Microsoft download page",
        )
    json_url = match.group(0).decode("ascii")
    try:
        body = await _http_get_with_retries(client, json_url, source_name=source_name)
    except httpx.HTTPError as exc:
        return _log_failure(source_name, "network", _safe_str_exc(exc))
    return await _handle_json_cidr_body(
        body,
        source_name=source_name,
        target=target,
        extract_cidrs=_extract_azure_cidrs,
        start=start,
    )


async def refresh_cloudflare(client: httpx.AsyncClient, target_dir: Path) -> RefreshResult:
    """Cloudflare publishes a plain newline-separated CIDR list at a
    stable URL. Write as-is to `cloudflare.cidr`."""
    source_name = "cloudflare"
    target = target_dir / _TARGET_CLOUDFLARE
    start = time.perf_counter()
    try:
        body = await _http_get_with_retries(client, _CLOUDFLARE_URL, source_name=source_name)
    except httpx.HTTPError as exc:
        return _log_failure(source_name, "network", _safe_str_exc(exc))
    if (skip := _check_raw_floor(body, source_name)) is not None:
        return skip
    try:
        written = await atomic_replace(target, body)
    except OSError as exc:
        return _log_failure(source_name, "other", _safe_str_exc(exc))
    duration_ms = (time.perf_counter() - start) * 1000.0
    return _log_success(source_name, written, duration_ms)


# ----------------------------------------------------------------------------
# Refresh loop + per-tick orchestration
# ----------------------------------------------------------------------------


def _build_http_client() -> httpx.AsyncClient:
    """Construct the per-tick `httpx.AsyncClient`. Tests monkeypatch this
    module-level function to inject `httpx.MockTransport`, keeping the
    refresh loop unit-testable without live network.

    `follow_redirects=True` is required: the MaxMind and IP2Proxy
    download endpoints answer with a 302 to a signed/CDN URL, and
    several upstreams may redirect http→https. Without it those sources
    fail every tick (httpx's default is no-follow)."""
    timeout = httpx.Timeout(_DEFAULT_HTTP_TIMEOUT, read=_DEFAULT_IP2P_TIMEOUT)
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True)


async def _run_all_sources(
    client: httpx.AsyncClient, data_dir: Path, settings: Settings
) -> list[RefreshResult]:
    """Run all 9 downloaders concurrently. Per-source failures are caught
    inside each downloader and surface as RefreshResult; the gather here
    uses `return_exceptions=True` only as defense-in-depth for any
    exception that escapes a downloader (would be a bug). Unexpected
    exceptions are logged and dropped from the result list."""
    tasks: list[asyncio.Task[RefreshResult]] = [
        asyncio.create_task(refresh_firehol_level1(client, data_dir)),
        asyncio.create_task(refresh_firehol_level2(client, data_dir)),
        asyncio.create_task(refresh_maxmind_city(client, data_dir, settings)),
        asyncio.create_task(refresh_maxmind_asn(client, data_dir, settings)),
        asyncio.create_task(refresh_ip2proxy(client, data_dir, settings)),
        asyncio.create_task(refresh_aws(client, data_dir)),
        asyncio.create_task(refresh_gcp(client, data_dir)),
        asyncio.create_task(refresh_azure(client, data_dir)),
        asyncio.create_task(refresh_cloudflare(client, data_dir)),
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[RefreshResult] = []
    for r in raw_results:
        if isinstance(r, BaseException):
            _log.warning(
                "enrich.refresh.unexpected_exception",
                error_class=_safe_str_exc(r),
            )
            continue
        results.append(r)
    return results


def _log_tick_summary(results: list[RefreshResult]) -> None:
    """One aggregate log line per tick. Counts success / failed /
    skipped_sanity_floor by status; success ratio is the headline ops
    indicator. Per-source detail is in the individual EMF metrics."""
    success = sum(1 for r in results if r.status == "success")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped_sanity_floor")
    _log.info(
        "enrich.refresh.tick_complete",
        success_count=success,
        failed_count=failed,
        skipped_sanity_floor_count=skipped,
        total=len(results),
    )


def _cleanup_orphan_tempfiles(data_dir: Path) -> None:
    """Best-effort cleanup of orphan tempfiles left by interrupted
    refreshes. Called on lifespan-shutdown cancellation."""
    if not data_dir.exists():
        return
    with contextlib.suppress(OSError):
        for tmp in data_dir.glob("*.tmp.*"):
            with contextlib.suppress(OSError):
                tmp.unlink()


async def refresh_all_once(
    data_dir: Path,
    settings: Settings,
    app: object,
) -> list[RefreshResult]:
    """One refresh tick: seed loaded-sources from disk (hybrid Pattern A
    defense), run all 9 downloaders concurrently, atomically swap
    `app.state.enricher` with a freshly-loaded instance if any source
    succeeded (CoW — concurrent enrich() calls finish
    on the OLD instance; refcount → 0 closes its handles via __del__).

    `app` is typed as `object` to avoid a hard dependency on FastAPI in
    this module; the only attribute used is `app.state.enricher`. The
    lifespan integration in `app/main.py` passes the real FastAPI app.
    """
    # Imported lazily to avoid a circular import at module load time
    # (app.enrich does not import enrichment_refresh, but the inverse
    # being inline-import-only keeps the module-load DAG tidy).
    from app.enrich import Enricher

    seed_loaded_from_disk(data_dir)
    async with _build_http_client() as client:
        results = await _run_all_sources(client, data_dir, settings)

    successes = [r for r in results if r.status == "success"]
    for r in successes:
        mark_source_loaded(r.source_name)

    # Swap only when this tick produced new data. If nothing succeeded,
    # the prior Enricher (which may have lazy-loaded disk-resident files
    # earlier) continues to serve — no degradation, no unnecessary churn.
    if successes:
        new_enricher = Enricher(data_dir=data_dir)
        await asyncio.to_thread(new_enricher._load_sources)
        app.state.enricher = new_enricher  # type: ignore[attr-defined]
        _log.info(
            "enrich.refresh.enricher_swapped",
            success_count=len(successes),
        )
    return results


async def refresh_loop(
    data_dir: Path,
    settings: Settings,
    app: object,
) -> None:
    """Forever refresh loop. Runs `refresh_all_once` then sleeps
    `_REFRESH_INTERVAL_SECONDS` (24h). Catches CancelledError on
    lifespan shutdown, cleans orphan tempfiles, and re-raises so the
    awaiting `asyncio.gather` observes cancellation cleanly.

    Any per-tick error other than CancelledError is logged but does not
    crash the loop — the next tick proceeds."""
    try:
        while True:
            try:
                results = await refresh_all_once(data_dir, settings, app)
                _log_tick_summary(results)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.warning(
                    "enrich.refresh.tick_error",
                    error_class=_safe_str_exc(exc),
                )
            await asyncio.sleep(_REFRESH_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        _log.info("enrich.refresh.cancelled")
        _cleanup_orphan_tempfiles(data_dir)
        raise
