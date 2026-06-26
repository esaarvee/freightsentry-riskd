"""Unit tests for `app.enrichment_refresh` — per-source downloaders,
atomic-replace + streaming, sanity floors, retry+backoff, IP2Proxy
magic-byte branch ladder, license-key sanitization.

Mocking pattern: `httpx.MockTransport` returns canned responses keyed
by URL prefix-match. New convention introduced by this commit; older
project tests use `httpx.ASGITransport` against `app.main:app`.

Sentinel license keys are intentionally distinctive strings; the
license-key-sanitization tests assert these strings never reach log
records or RefreshResult fields.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import structlog
import structlog.testing

from app import enrichment_refresh as er
from app.config import Settings

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "enrichment_refresh"

_SENTINEL_MAXMIND = "SENTINEL-MAXMIND-KEY-DO-NOT-LOG-1234567890"
_SENTINEL_IP2P = "SENTINEL-IP2P-TOKEN-DO-NOT-LOG-abcdef1234"


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_loaded_sources() -> None:
    er._reset_loaded_sources_for_tests()


@pytest.fixture(autouse=True)
def _short_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten retry backoff so tests don't burn real wall clock on
    network-failure paths."""
    monkeypatch.setattr(er, "_RETRY_BASE_DELAY_SECONDS", 0.001)


@pytest.fixture
def low_floors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Override sanity floors to 1 byte so the small C0 fixtures pass."""
    monkeypatch.setattr(er, "_SANITY_FLOORS", dict.fromkeys(er._SANITY_FLOORS, 1))
    monkeypatch.setattr(er, "_EXTRACTED_FLOORS", dict.fromkeys(er._EXTRACTED_FLOORS, 1))


@pytest.fixture
def synthetic_settings() -> Settings:
    return Settings(
        database_url="postgresql://test:test@localhost:5432/test",
        hmac_secret="test-secret",
        maxmind_license_key=_SENTINEL_MAXMIND,
        ip2proxy_download_token=_SENTINEL_IP2P,
    )  # type: ignore[call-arg]


@pytest.fixture
def _stub_binary_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the MaxMind/IP2Proxy binary loads for the swap / result-handling
    orchestration tests, so they exercise the refresh flow without depending on
    whether the small binary fixtures parse under the installed maxminddb /
    IP2Proxy versions. NOT used by the CoW test
    (``test_pre_swap_reference_stays_usable_after_swap``) or
    ``TestLoadSourcesResilience`` — those run the real
    ``Enricher._load_{maxmind,ip2proxy}`` graceful-degradation guard."""
    from app.enrich import Enricher

    monkeypatch.setattr(Enricher, "_load_maxmind", lambda self: None)
    monkeypatch.setattr(Enricher, "_load_ip2proxy", lambda self: None)


def _read_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _const_response(
    status: int, body: bytes, headers: dict[str, str] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, headers=headers)

    return handler


# ---------------------------------------------------------------------------
# atomic_replace + atomic_replace_stream
# ---------------------------------------------------------------------------


class TestAtomicReplace:
    async def test_writes_content_and_no_tempfile(self, tmp_path: Path) -> None:
        target = tmp_path / "out.bin"
        written = await er.atomic_replace(target, b"hello world")
        assert written == 11
        assert target.read_bytes() == b"hello world"
        # No leftover tempfiles in the directory
        leftover = list(tmp_path.glob(f"{target.name}.tmp.*"))
        assert leftover == []

    async def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "out.bin"
        target.write_bytes(b"OLD CONTENT")
        await er.atomic_replace(target, b"new")
        assert target.read_bytes() == b"new"

    async def test_creates_parent_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "deep" / "out.bin"
        await er.atomic_replace(target, b"data")
        assert target.read_bytes() == b"data"

    async def test_cleans_tempfile_on_write_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulate write failure — original target preserved + tempfile cleaned."""
        target = tmp_path / "out.bin"
        target.write_bytes(b"ORIGINAL")
        original_write = os.write

        def failing_write(fd: int, data: bytes) -> int:
            # Allow other fd writes (test infra) to succeed; fail only
            # when the target's tempfile is the destination.
            if any(f.name.startswith(target.name + ".tmp.") for f in tmp_path.iterdir()):
                msg = "simulated write failure"
                raise OSError(msg)
            return original_write(fd, data)

        monkeypatch.setattr(os, "write", failing_write)
        with pytest.raises(OSError, match="simulated write failure"):
            await er.atomic_replace(target, b"NEW")
        # Original preserved + tempfile gone
        assert target.read_bytes() == b"ORIGINAL"
        assert list(tmp_path.glob(f"{target.name}.tmp.*")) == []


class TestAtomicReplaceStream:
    async def test_streams_from_fileobj(self, tmp_path: Path) -> None:
        target = tmp_path / "stream.bin"
        src = io.BytesIO(b"X" * (3 << 20))  # 3 MiB
        written = await er.atomic_replace_stream(target, src, chunk_size=1 << 20)
        assert written == 3 << 20
        assert target.stat().st_size == 3 << 20
        # No orphan tempfile
        assert list(tmp_path.glob(f"{target.name}.tmp.*")) == []

    async def test_chunked_writes_match_content(self, tmp_path: Path) -> None:
        target = tmp_path / "stream.bin"
        content = b"abcdefghij" * 100_000  # 1 MB
        src = io.BytesIO(content)
        await er.atomic_replace_stream(target, src, chunk_size=4096)
        assert target.read_bytes() == content

    async def test_cleans_tempfile_on_stream_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "stream.bin"

        class _FailingReader:
            def read(self, _n: int = -1) -> bytes:
                msg = "simulated source read failure"
                raise OSError(msg)

        with pytest.raises(OSError, match="simulated source read failure"):
            await er.atomic_replace_stream(target, _FailingReader())  # type: ignore[arg-type]
        assert not target.exists()
        assert list(tmp_path.glob(f"{target.name}.tmp.*")) == []

    async def test_streams_in_chunks_not_full_buffer(self, tmp_path: Path) -> None:
        """Pin the streaming invariant: the IP2Proxy
        path is 1.6 GB extracted; a regression that buffered the full
        source into a Python bytes object would defeat the entire reason
        `atomic_replace_stream` exists. This test asserts the source is
        read incrementally via repeated `read(chunk_size)` calls, never
        as a single `read(-1)` or `read(big_n)`."""
        target = tmp_path / "stream.bin"

        class _ChunkTrackingReader:
            """Reader that tracks the maximum bytes requested in any
            single `read(n)` call. The max should equal chunk_size, not
            the total content size."""

            def __init__(self, payload: bytes) -> None:
                self._buf = io.BytesIO(payload)
                self.max_request: int | None = None
                self.requests: list[int] = []

            def read(self, n: int = -1) -> bytes:
                self.requests.append(n)
                if self.max_request is None or (n != -1 and n > self.max_request):
                    self.max_request = n
                return self._buf.read(n if n != -1 else 4096)

        payload = b"X" * (5 << 20)  # 5 MiB
        reader = _ChunkTrackingReader(payload)
        written = await er.atomic_replace_stream(target, reader, chunk_size=512 * 1024)
        assert written == len(payload)
        # The reader should have been called multiple times with the
        # explicit chunk_size — never a "read everything" call.
        assert reader.max_request == 512 * 1024
        assert -1 not in reader.requests, (
            "atomic_replace_stream must not request the full source in one read"
        )
        assert len(reader.requests) >= 10, "expected ~10 chunked reads for 5 MiB / 512 KiB chunks"


# ---------------------------------------------------------------------------
# _http_get_with_retries
# ---------------------------------------------------------------------------


class TestHttpRetry:
    async def test_returns_body_on_first_200(self) -> None:
        async with _make_client(_const_response(200, b"ok")) as client:
            body = await er._http_get_with_retries(
                client, "https://example.com/x", source_name="test"
            )
        assert body == b"ok"

    async def test_retries_on_5xx_then_succeeds(self) -> None:
        attempts: list[int] = []

        def handler(_req: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(503 if len(attempts) < 3 else 200, content=b"win")

        async with _make_client(handler) as client:
            body = await er._http_get_with_retries(
                client, "https://example.com/x", source_name="test"
            )
        assert body == b"win"
        assert len(attempts) == 3

    async def test_4xx_does_not_retry(self) -> None:
        attempts: list[int] = []

        def handler(_req: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(404, content=b"not found")

        async with _make_client(handler) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await er._http_get_with_retries(client, "https://example.com/x", source_name="test")
        assert len(attempts) == 1

    async def test_exhausts_retries_on_persistent_5xx(self) -> None:
        attempts: list[int] = []

        def handler(_req: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(502, content=b"")

        async with _make_client(handler) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await er._http_get_with_retries(client, "https://example.com/x", source_name="test")
        assert len(attempts) == er._RETRY_MAX_ATTEMPTS

    async def test_retries_on_network_error_then_succeeds(self) -> None:
        attempts: list[int] = []

        def handler(_req: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 2:
                msg = "simulated connect failure"
                raise httpx.ConnectError(msg)
            return httpx.Response(200, content=b"recovered")

        async with _make_client(handler) as client:
            body = await er._http_get_with_retries(
                client, "https://example.com/x", source_name="test"
            )
        assert body == b"recovered"
        assert len(attempts) == 2

    async def test_build_http_client_follows_redirects(self) -> None:
        """The production client MUST follow redirects — MaxMind/IP2Proxy
        download endpoints 302 to a signed/CDN URL. Without this every
        licensed-source refresh fails."""
        async with er._build_http_client() as client:
            assert client.follow_redirects is True

    async def test_unfollowed_3xx_is_fatal_not_retried(self) -> None:
        """A 3xx that reaches the classifier is terminal: raise on the
        first attempt rather than burning the retry budget as if it were
        a 5xx. (The handler returns the 302 directly with no Location
        chain to follow, so it surfaces to `raise_for_status`.)"""
        attempts: list[int] = []

        def handler(_req: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(302, content=b"")

        async with _make_client(handler) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await er._http_get_with_retries(client, "https://example.com/x", source_name="test")
        assert len(attempts) == 1


# ---------------------------------------------------------------------------
# FireHOL downloaders (level1 + level2)
# ---------------------------------------------------------------------------


class TestFireHOL:
    async def test_level1_success(self, tmp_path: Path, low_floors: None) -> None:
        body = _read_fixture("firehol_level1.netset")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_firehol_level1(client, tmp_path)
        assert result.status == "success"
        assert result.source_name == "firehol_level1"
        assert (tmp_path / "firehol_level1.netset").read_bytes() == body

    async def test_level2_success(self, tmp_path: Path, low_floors: None) -> None:
        body = _read_fixture("firehol_level2.netset")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_firehol_level2(client, tmp_path)
        assert result.status == "success"
        assert (tmp_path / "firehol_level2.netset").read_bytes() == body

    async def test_sanity_floor_skips(self, tmp_path: Path) -> None:
        # Default floor is 50_000 — the 559-byte fixture is below it.
        body = _read_fixture("firehol_level1.netset")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_firehol_level1(client, tmp_path)
        assert result.status == "skipped_sanity_floor"
        assert not (tmp_path / "firehol_level1.netset").exists()

    async def test_network_failure(self, tmp_path: Path) -> None:
        def handler(_req: httpx.Request) -> httpx.Response:
            msg = "simulated"
            raise httpx.ConnectError(msg)

        async with _make_client(handler) as client:
            result = await er.refresh_firehol_level1(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "network"

    async def test_4xx_response(self, tmp_path: Path) -> None:
        async with _make_client(_const_response(404, b"")) as client:
            result = await er.refresh_firehol_level1(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "network"

    async def test_5xx_exhausts(self, tmp_path: Path) -> None:
        async with _make_client(_const_response(503, b"")) as client:
            result = await er.refresh_firehol_level1(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "network"


# ---------------------------------------------------------------------------
# MaxMind downloaders (City + ASN)
# ---------------------------------------------------------------------------


class TestMaxMind:
    async def test_city_success(
        self,
        tmp_path: Path,
        low_floors: None,
        synthetic_settings: Settings,
    ) -> None:
        body = _read_fixture("GeoLite2-City.tar.gz")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_maxmind_city(client, tmp_path, synthetic_settings)
        assert result.status == "success"
        assert (tmp_path / "GeoLite2-City.mmdb").exists()

    async def test_asn_success(
        self,
        tmp_path: Path,
        low_floors: None,
        synthetic_settings: Settings,
    ) -> None:
        body = _read_fixture("GeoLite2-ASN.tar.gz")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_maxmind_asn(client, tmp_path, synthetic_settings)
        assert result.status == "success"
        assert (tmp_path / "GeoLite2-ASN.mmdb").exists()

    async def test_missing_license_key_fails(self, tmp_path: Path, low_floors: None) -> None:
        # `_env_file=None` keeps this test hermetic — without it the
        # developer's `.env` (which may define MAXMIND_LICENSE_KEY) leaks
        # in and the empty-key guard never fires.
        settings = Settings(
            database_url="postgresql://test:test@localhost:5432/test",
            hmac_secret="test",
            _env_file=None,
        )  # type: ignore[call-arg]
        async with _make_client(_const_response(200, b"")) as client:
            result = await er.refresh_maxmind_city(client, tmp_path, settings)
        assert result.status == "failed"
        assert "license" in (result.error or "").lower()

    async def test_sanity_floor_skips(self, tmp_path: Path, synthetic_settings: Settings) -> None:
        # Default 30 MB floor — the 332-byte fixture is below.
        body = _read_fixture("GeoLite2-City.tar.gz")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_maxmind_city(client, tmp_path, synthetic_settings)
        assert result.status == "skipped_sanity_floor"

    async def test_parse_error_on_corrupt_tarball(
        self,
        tmp_path: Path,
        low_floors: None,
        synthetic_settings: Settings,
    ) -> None:
        async with _make_client(_const_response(200, b"not a tarball")) as client:
            result = await er.refresh_maxmind_city(client, tmp_path, synthetic_settings)
        assert result.status == "failed"
        assert result.failure_class == "parse_error"

    async def test_network_failure(self, tmp_path: Path, synthetic_settings: Settings) -> None:
        def handler(_req: httpx.Request) -> httpx.Response:
            msg = "simulated"
            raise httpx.ConnectError(msg)

        async with _make_client(handler) as client:
            result = await er.refresh_maxmind_city(client, tmp_path, synthetic_settings)
        assert result.status == "failed"
        assert result.failure_class == "network"

    async def test_5xx_exhausts(self, tmp_path: Path, synthetic_settings: Settings) -> None:
        async with _make_client(_const_response(503, b"")) as client:
            result = await er.refresh_maxmind_asn(client, tmp_path, synthetic_settings)
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# IP2Proxy downloader — full branch ladder
# ---------------------------------------------------------------------------


class TestIp2Proxy:
    async def test_zip_happy_path(
        self,
        tmp_path: Path,
        low_floors: None,
        synthetic_settings: Settings,
    ) -> None:
        body = _read_fixture("IP2PROXY-LITE-PX11.zip")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_ip2proxy(client, tmp_path, synthetic_settings)
        assert result.status == "success", result
        bin_path = tmp_path / "IP2PROXY-LITE-PX11.BIN"
        assert bin_path.exists()
        # Extracted BIN should match the fixture's stub content (3072 bytes)
        assert bin_path.stat().st_size == 3072

    async def test_zip_missing_bin_member(
        self,
        tmp_path: Path,
        low_floors: None,
        synthetic_settings: Settings,
    ) -> None:
        # Build a ZIP without the expected BIN member
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("README.TXT", b"only readme")
        async with _make_client(_const_response(200, buf.getvalue())) as client:
            result = await er.refresh_ip2proxy(client, tmp_path, synthetic_settings)
        assert result.status == "failed"
        assert result.failure_class == "parse_error"

    async def test_corrupt_zip(
        self,
        tmp_path: Path,
        low_floors: None,
        synthetic_settings: Settings,
    ) -> None:
        # Starts with ZIP magic but invalid contents
        body = b"PK\x03\x04" + b"\x00" * 100
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_ip2proxy(client, tmp_path, synthetic_settings)
        assert result.status == "failed"
        assert result.failure_class == "parse_error"

    async def test_rate_limited_detected(
        self,
        tmp_path: Path,
        low_floors: None,
        synthetic_settings: Settings,
    ) -> None:
        # Real upstream throttle body (observed 2026-06-09).
        body = b"THIS FILE CAN ONLY BE DOWNLOADED 5 TIMES WITHIN 24 HOURS."
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_ip2proxy(client, tmp_path, synthetic_settings)
        assert result.status == "failed"
        assert result.failure_class == "rate_limited"

    async def test_upstream_html_detected(
        self,
        tmp_path: Path,
        low_floors: None,
        synthetic_settings: Settings,
    ) -> None:
        body = b"<!DOCTYPE html><html><body>Log In</body></html>"
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_ip2proxy(client, tmp_path, synthetic_settings)
        assert result.status == "failed"
        assert result.failure_class == "upstream_html"

    async def test_raw_sanity_floor_skips(
        self,
        tmp_path: Path,
        synthetic_settings: Settings,
    ) -> None:
        # Default 30 MB floor — the 513-byte ZIP fixture is below it,
        # and isn't a rate-limit/HTML body, so it hits the floor branch.
        body = _read_fixture("IP2PROXY-LITE-PX11.zip")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_ip2proxy(client, tmp_path, synthetic_settings)
        assert result.status == "skipped_sanity_floor"
        assert result.source_name == "ip2proxy"

    async def test_extracted_floor_skips_and_removes_artifact(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        synthetic_settings: Settings,
    ) -> None:
        # Raw floor low, extracted floor high — fixture BIN is 3072 B,
        # so the extracted-floor check skips and the artifact is removed.
        monkeypatch.setattr(er, "_SANITY_FLOORS", dict.fromkeys(er._SANITY_FLOORS, 1))
        monkeypatch.setattr(
            er,
            "_EXTRACTED_FLOORS",
            {**er._EXTRACTED_FLOORS, "ip2proxy_extracted": 10_000},
        )
        body = _read_fixture("IP2PROXY-LITE-PX11.zip")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_ip2proxy(client, tmp_path, synthetic_settings)
        assert result.status == "skipped_sanity_floor"
        assert result.source_name == "ip2proxy_extracted"
        assert not (tmp_path / "IP2PROXY-LITE-PX11.BIN").exists()

    async def test_missing_token_fails(self, tmp_path: Path, low_floors: None) -> None:
        # `_env_file=None` keeps this test hermetic — without it the
        # developer's `.env` (which may define IP2PROXY_DOWNLOAD_TOKEN)
        # leaks in and the empty-token guard never fires.
        settings = Settings(
            database_url="postgresql://test:test@localhost:5432/test",
            hmac_secret="test",
            _env_file=None,
        )  # type: ignore[call-arg]
        async with _make_client(_const_response(200, b"")) as client:
            result = await er.refresh_ip2proxy(client, tmp_path, settings)
        assert result.status == "failed"
        assert "token" in (result.error or "").lower()

    async def test_network_failure(self, tmp_path: Path, synthetic_settings: Settings) -> None:
        def handler(_req: httpx.Request) -> httpx.Response:
            msg = "simulated"
            raise httpx.ConnectError(msg)

        async with _make_client(handler) as client:
            result = await er.refresh_ip2proxy(client, tmp_path, synthetic_settings)
        assert result.status == "failed"
        assert result.failure_class == "network"

    async def test_zip_path_uses_streaming_not_bytes(
        self,
        tmp_path: Path,
        low_floors: None,
        synthetic_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cycle-2 regression-pin: the ZIP-extract
        path must call `atomic_replace_stream` (streaming) not
        `atomic_replace` (bytes-form). A regression that buffered the
        1.6 GB BIN into Python `bytes` would OOM on small Fargate
        sizings."""
        stream_calls: list[Path] = []
        bytes_calls: list[Path] = []
        real_stream = er.atomic_replace_stream
        real_bytes = er.atomic_replace

        async def spy_stream(target: Path, src: object, **kwargs: object) -> int:
            stream_calls.append(target)
            return await real_stream(target, src, **kwargs)  # type: ignore[arg-type]

        async def spy_bytes(target: Path, content: bytes) -> int:
            bytes_calls.append(target)
            return await real_bytes(target, content)

        monkeypatch.setattr(er, "atomic_replace_stream", spy_stream)
        monkeypatch.setattr(er, "atomic_replace", spy_bytes)

        body = _read_fixture("IP2PROXY-LITE-PX11.zip")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_ip2proxy(client, tmp_path, synthetic_settings)
        assert result.status == "success", result
        assert len(stream_calls) == 1
        assert stream_calls[0].name == "IP2PROXY-LITE-PX11.BIN"
        assert bytes_calls == []

    async def test_direct_bin_extracted_floor_removes_artifact(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        synthetic_settings: Settings,
    ) -> None:
        """Cycle-2 regression-pin per cycle-1 senior-engineer finding:
        the direct-BIN fallback branch must remove the on-disk artifact
        when the extracted floor fails. Cycle-1 had asymmetric cleanup
        that left a truncated BIN on disk for the direct-BIN path."""
        monkeypatch.setattr(er, "_SANITY_FLOORS", dict.fromkeys(er._SANITY_FLOORS, 1))
        monkeypatch.setattr(
            er,
            "_EXTRACTED_FLOORS",
            {**er._EXTRACTED_FLOORS, "ip2proxy_extracted": 10_000},
        )
        # Body that's neither ZIP nor gzip nor HTML nor rate-limited
        # → hits direct-BIN fallback. 200 bytes < 10 KB extracted floor.
        body = b"\x00" * 200
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_ip2proxy(client, tmp_path, synthetic_settings)
        assert result.status == "skipped_sanity_floor"
        assert result.source_name == "ip2proxy_extracted"
        assert not (tmp_path / "IP2PROXY-LITE-PX11.BIN").exists(), (
            "direct-BIN fallback must remove truncated artifact on extracted-floor fail"
        )


# ---------------------------------------------------------------------------
# Cloud CIDR sources — AWS / GCP / Azure / Cloudflare
# ---------------------------------------------------------------------------


class TestAws:
    async def test_success_writes_cidr_file(self, tmp_path: Path, low_floors: None) -> None:
        body = _read_fixture("ip-ranges.json")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_aws(client, tmp_path)
        assert result.status == "success"
        cidr = (tmp_path / "aws.cidr").read_text()
        assert "192.0.2.0/24" in cidr
        assert "203.0.113.128/25" in cidr

    async def test_sanity_floor_raw(self, tmp_path: Path) -> None:
        body = _read_fixture("ip-ranges.json")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_aws(client, tmp_path)
        assert result.status == "skipped_sanity_floor"
        assert result.source_name == "aws"

    async def test_extracted_floor_skips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Raw floor low; extracted floor high enough to fail
        monkeypatch.setattr(er, "_SANITY_FLOORS", dict.fromkeys(er._SANITY_FLOORS, 1))
        monkeypatch.setattr(
            er,
            "_EXTRACTED_FLOORS",
            {**er._EXTRACTED_FLOORS, "aws_extracted": 10_000},
        )
        body = _read_fixture("ip-ranges.json")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_aws(client, tmp_path)
        assert result.status == "skipped_sanity_floor"
        assert result.source_name == "aws_extracted"
        assert not (tmp_path / "aws.cidr").exists()

    async def test_parse_error(self, tmp_path: Path, low_floors: None) -> None:
        async with _make_client(_const_response(200, b"not json")) as client:
            result = await er.refresh_aws(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "parse_error"

    async def test_network_failure(self, tmp_path: Path) -> None:
        def handler(_req: httpx.Request) -> httpx.Response:
            msg = "simulated"
            raise httpx.ConnectError(msg)

        async with _make_client(handler) as client:
            result = await er.refresh_aws(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "network"


class TestGcp:
    async def test_success(self, tmp_path: Path, low_floors: None) -> None:
        body = _read_fixture("cloud.json")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_gcp(client, tmp_path)
        assert result.status == "success"
        cidr = (tmp_path / "gcp.cidr").read_text()
        # IPv6 entry from fixture should be filtered out
        assert "2001:db8" not in cidr
        assert "192.0.2.0/25" in cidr

    async def test_extracted_floor_skips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(er, "_SANITY_FLOORS", dict.fromkeys(er._SANITY_FLOORS, 1))
        monkeypatch.setattr(
            er,
            "_EXTRACTED_FLOORS",
            {**er._EXTRACTED_FLOORS, "gcp_extracted": 10_000},
        )
        body = _read_fixture("cloud.json")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_gcp(client, tmp_path)
        assert result.status == "skipped_sanity_floor"
        assert result.source_name == "gcp_extracted"

    async def test_parse_error(self, tmp_path: Path, low_floors: None) -> None:
        async with _make_client(_const_response(200, b"{not json")) as client:
            result = await er.refresh_gcp(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "parse_error"

    async def test_network_failure(self, tmp_path: Path) -> None:
        def handler(_req: httpx.Request) -> httpx.Response:
            msg = "simulated"
            raise httpx.ConnectError(msg)

        async with _make_client(handler) as client:
            result = await er.refresh_gcp(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "network"

    async def test_4xx_response(self, tmp_path: Path) -> None:
        async with _make_client(_const_response(404, b"")) as client:
            result = await er.refresh_gcp(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "network"

    async def test_5xx_exhausts(self, tmp_path: Path) -> None:
        async with _make_client(_const_response(503, b"")) as client:
            result = await er.refresh_gcp(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "network"


class TestAzure:
    async def test_two_step_success(self, tmp_path: Path, low_floors: None) -> None:
        json_body = _read_fixture("azure-service-tags.json")
        # Fake Microsoft download page with the JSON URL embedded
        details_body = (
            b"<html>Other content "
            b'href="https://download.microsoft.com/download/abc/'
            b'ServiceTags_Public_20260609.json" more</html>'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "details.aspx" in url:
                return httpx.Response(200, content=details_body)
            if "ServiceTags_Public" in url:
                return httpx.Response(200, content=json_body)
            return httpx.Response(500, content=b"unexpected URL")

        async with _make_client(handler) as client:
            result = await er.refresh_azure(client, tmp_path)
        assert result.status == "success", result
        cidr = (tmp_path / "azure.cidr").read_text()
        assert "192.0.2.0/26" in cidr

    async def test_no_json_url_in_page(self, tmp_path: Path, low_floors: None) -> None:
        details_body = b"<html>No JSON URL here</html>"
        async with _make_client(_const_response(200, details_body)) as client:
            result = await er.refresh_azure(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "parse_error"

    async def test_extracted_floor_skips(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(er, "_SANITY_FLOORS", dict.fromkeys(er._SANITY_FLOORS, 1))
        monkeypatch.setattr(
            er,
            "_EXTRACTED_FLOORS",
            {**er._EXTRACTED_FLOORS, "azure_extracted": 10_000},
        )
        json_body = _read_fixture("azure-service-tags.json")
        details_body = (
            b'href="https://download.microsoft.com/download/abc/ServiceTags_Public_20260609.json"'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if "details.aspx" in str(request.url):
                return httpx.Response(200, content=details_body)
            return httpx.Response(200, content=json_body)

        async with _make_client(handler) as client:
            result = await er.refresh_azure(client, tmp_path)
        assert result.status == "skipped_sanity_floor"
        assert result.source_name == "azure_extracted"

    async def test_first_hop_network_failure(self, tmp_path: Path) -> None:
        """Details-page fetch fails with a network error."""

        def handler(_req: httpx.Request) -> httpx.Response:
            msg = "simulated"
            raise httpx.ConnectError(msg)

        async with _make_client(handler) as client:
            result = await er.refresh_azure(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "network"

    async def test_second_hop_network_failure(self, tmp_path: Path, low_floors: None) -> None:
        """First hop succeeds (page contains a JSON URL); second hop
        fails with a network error. Exercises the second `except
        httpx.HTTPError` branch in `refresh_azure`."""
        details_body = (
            b'href="https://download.microsoft.com/download/abc/ServiceTags_Public_20260609.json"'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if "details.aspx" in str(request.url):
                return httpx.Response(200, content=details_body)
            msg = "simulated second-hop failure"
            raise httpx.ConnectError(msg)

        async with _make_client(handler) as client:
            result = await er.refresh_azure(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "network"

    async def test_second_hop_json_decode_error(self, tmp_path: Path, low_floors: None) -> None:
        """First hop succeeds; second hop returns malformed JSON."""
        details_body = (
            b'href="https://download.microsoft.com/download/abc/ServiceTags_Public_20260609.json"'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if "details.aspx" in str(request.url):
                return httpx.Response(200, content=details_body)
            return httpx.Response(200, content=b"{not valid json")

        async with _make_client(handler) as client:
            result = await er.refresh_azure(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "parse_error"


class TestCloudflare:
    async def test_success(self, tmp_path: Path, low_floors: None) -> None:
        body = _read_fixture("ips-v4.txt")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_cloudflare(client, tmp_path)
        assert result.status == "success"
        assert (tmp_path / "cloudflare.cidr").read_bytes() == body

    async def test_sanity_floor(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Raise the floor above the fixture's 44 bytes.
        monkeypatch.setattr(er, "_SANITY_FLOORS", {**er._SANITY_FLOORS, "cloudflare": 1000})
        body = _read_fixture("ips-v4.txt")
        async with _make_client(_const_response(200, body)) as client:
            result = await er.refresh_cloudflare(client, tmp_path)
        assert result.status == "skipped_sanity_floor"

    async def test_network_failure(self, tmp_path: Path) -> None:
        def handler(_req: httpx.Request) -> httpx.Response:
            msg = "simulated"
            raise httpx.ConnectError(msg)

        async with _make_client(handler) as client:
            result = await er.refresh_cloudflare(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "network"

    async def test_4xx_response(self, tmp_path: Path) -> None:
        async with _make_client(_const_response(404, b"")) as client:
            result = await er.refresh_cloudflare(client, tmp_path)
        assert result.status == "failed"
        assert result.failure_class == "network"


# ---------------------------------------------------------------------------
# Health-probe state helpers
# ---------------------------------------------------------------------------


class TestLoadedSources:
    def test_mark_and_query(self) -> None:
        er.mark_source_loaded("firehol_level1")
        assert "firehol_level1" in er.loaded_sources_snapshot()
        assert not er.all_sources_loaded_at_least_once()

    def test_all_loaded(self) -> None:
        for name in er._ALL_SOURCE_NAMES:
            er.mark_source_loaded(name)
        assert er.all_sources_loaded_at_least_once()

    def test_seed_from_disk_picks_up_present_files(self, tmp_path: Path) -> None:
        (tmp_path / "firehol_level1.netset").write_bytes(b"data")
        (tmp_path / "GeoLite2-City.mmdb").write_bytes(b"data")
        er.seed_loaded_from_disk(tmp_path)
        snap = er.loaded_sources_snapshot()
        assert "firehol_level1" in snap
        assert "maxmind_city" in snap
        # Sources without files don't get marked
        assert "ip2proxy" not in snap

    def test_seed_skips_empty_files(self, tmp_path: Path) -> None:
        (tmp_path / "firehol_level1.netset").touch()  # 0 bytes
        er.seed_loaded_from_disk(tmp_path)
        assert "firehol_level1" not in er.loaded_sources_snapshot()


# ---------------------------------------------------------------------------
# License-key sanitization
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# refresh_all_once + refresh_loop
# ---------------------------------------------------------------------------


class _StubAppState:
    """Minimal stand-in for `fastapi.FastAPI.app.state` so refresh_all_once
    can swap `enricher` without an actual FastAPI instance."""

    def __init__(self, enricher: object) -> None:
        self.enricher = enricher


class _StubApp:
    def __init__(self, enricher: object) -> None:
        self.state = _StubAppState(enricher)


def _mock_client_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[], httpx.AsyncClient]:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


class TestRefreshAllOnce:
    async def test_all_success_swaps_enricher(
        self,
        tmp_path: Path,
        low_floors: None,
        synthetic_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
        _stub_binary_loads: None,
    ) -> None:
        """Every source returns its fixture; after the tick the swap
        happens AND every source is marked loaded."""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "firehol_level1" in url:
                return httpx.Response(200, content=_read_fixture("firehol_level1.netset"))
            if "firehol_level2" in url:
                return httpx.Response(200, content=_read_fixture("firehol_level2.netset"))
            if "GeoLite2-City" in url:
                return httpx.Response(200, content=_read_fixture("GeoLite2-City.tar.gz"))
            if "GeoLite2-ASN" in url:
                return httpx.Response(200, content=_read_fixture("GeoLite2-ASN.tar.gz"))
            if "ip2location.com" in url:
                return httpx.Response(200, content=_read_fixture("IP2PROXY-LITE-PX11.zip"))
            if "ip-ranges.amazonaws.com" in url:
                return httpx.Response(200, content=_read_fixture("ip-ranges.json"))
            if "gstatic.com" in url:
                return httpx.Response(200, content=_read_fixture("cloud.json"))
            if "microsoft.com/en-us/download" in url:
                return httpx.Response(
                    200,
                    content=(
                        b'href="https://download.microsoft.com/download/abc/'
                        b'ServiceTags_Public_20260609.json"'
                    ),
                )
            if "ServiceTags_Public" in url:
                return httpx.Response(200, content=_read_fixture("azure-service-tags.json"))
            if "cloudflare.com" in url:
                return httpx.Response(200, content=_read_fixture("ips-v4.txt"))
            return httpx.Response(500, content=b"unmocked url")

        monkeypatch.setattr(er, "_build_http_client", _mock_client_factory(handler))

        pre = object()  # sentinel pre-swap enricher
        app = _StubApp(pre)
        results = await er.refresh_all_once(tmp_path, synthetic_settings, app)

        success_names = {r.source_name for r in results if r.status == "success"}
        assert success_names == set(er._ALL_SOURCE_NAMES), (
            f"some sources failed: {[(r.source_name, r.status) for r in results]}"
        )
        # Swap happened
        assert app.state.enricher is not pre
        # Every source marked loaded
        assert er.all_sources_loaded_at_least_once()

    async def test_mixed_outcome_partial_load(
        self,
        tmp_path: Path,
        low_floors: None,
        synthetic_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """4 sources succeed, the rest fail (network or status). Swap
        still happens (≥1 success); only the 4 successful sources mark
        loaded."""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            # Only FireHOL L1+L2 and AWS+Cloudflare succeed
            if "firehol_level1" in url:
                return httpx.Response(200, content=_read_fixture("firehol_level1.netset"))
            if "firehol_level2" in url:
                return httpx.Response(200, content=_read_fixture("firehol_level2.netset"))
            if "ip-ranges.amazonaws.com" in url:
                return httpx.Response(200, content=_read_fixture("ip-ranges.json"))
            if "cloudflare.com" in url:
                return httpx.Response(200, content=_read_fixture("ips-v4.txt"))
            # Everything else 503
            return httpx.Response(503, content=b"")

        monkeypatch.setattr(er, "_build_http_client", _mock_client_factory(handler))

        pre = object()
        app = _StubApp(pre)
        results = await er.refresh_all_once(tmp_path, synthetic_settings, app)

        succeeded = {r.source_name for r in results if r.status == "success"}
        failed = {r.source_name for r in results if r.status == "failed"}
        assert succeeded == {"firehol_level1", "firehol_level2", "aws", "cloudflare"}
        assert "maxmind_city" in failed
        # Swap still happened
        assert app.state.enricher is not pre
        # Only successful sources marked loaded
        assert er.loaded_sources_snapshot() == frozenset(succeeded)

    async def test_all_failed_no_swap(
        self,
        tmp_path: Path,
        synthetic_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If no source succeeds, the Enricher is NOT swapped — the
        existing instance keeps serving."""
        monkeypatch.setattr(
            er, "_build_http_client", _mock_client_factory(_const_response(503, b""))
        )

        pre = object()
        app = _StubApp(pre)
        results = await er.refresh_all_once(tmp_path, synthetic_settings, app)
        assert all(r.status == "failed" for r in results)
        assert app.state.enricher is pre, "no swap when zero successes"

    async def test_unexpected_exception_dropped_from_results(
        self,
        tmp_path: Path,
        low_floors: None,
        synthetic_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
        _stub_binary_loads: None,
    ) -> None:
        """If a downloader raises an exception that escapes its own
        handler (a defense-in-depth path), the gather captures it,
        refresh_all_once logs it, and drops it from results — no crash."""

        async def crashing_refresh(*_args: object, **_kwargs: object) -> er.RefreshResult:
            msg = "simulated unexpected"
            raise RuntimeError(msg)

        monkeypatch.setattr(er, "refresh_firehol_level1", crashing_refresh)

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_read_fixture("ips-v4.txt"))

        monkeypatch.setattr(er, "_build_http_client", _mock_client_factory(handler))

        pre = object()
        app = _StubApp(pre)
        results = await er.refresh_all_once(tmp_path, synthetic_settings, app)
        # Crashing source dropped from results
        assert all(r.source_name != "firehol_level1" for r in results)
        # Other sources still in results — one less than the total source set
        assert len(results) == len(er._ALL_SOURCE_NAMES) - 1


class TestRefreshLoop:
    async def test_cancellation_clean_no_orphans(
        self,
        tmp_path: Path,
        synthetic_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Spawn the loop, let one tick run (all sources 503 → no swap),
        cancel, await — confirm no orphan tempfiles in data_dir."""
        monkeypatch.setattr(
            er, "_build_http_client", _mock_client_factory(_const_response(503, b""))
        )
        # Shorten the inter-tick sleep so the test doesn't wait 24h
        monkeypatch.setattr(er, "_REFRESH_INTERVAL_SECONDS", 0.05)
        # Plant an orphan tempfile to confirm the cleanup runs
        orphan = tmp_path / "firehol_level1.netset.tmp.deadbeef"
        orphan.write_bytes(b"leftover")

        pre = object()
        app = _StubApp(pre)
        task = asyncio.create_task(er.refresh_loop(tmp_path, synthetic_settings, app))
        # Give it a tick + a partial sleep
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Orphan tempfile cleaned
        assert not orphan.exists()
        assert list(tmp_path.glob("*.tmp.*")) == []

    async def test_tick_error_does_not_crash_loop(
        self,
        tmp_path: Path,
        synthetic_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If `refresh_all_once` raises an unexpected exception, the
        loop catches it and continues to the next tick."""
        tick_count = 0

        async def failing_then_ok_once(
            _data_dir: Path, _settings: Settings, _app: object
        ) -> list[er.RefreshResult]:
            nonlocal tick_count
            tick_count += 1
            if tick_count == 1:
                msg = "simulated tick failure"
                raise RuntimeError(msg)
            return []

        monkeypatch.setattr(er, "refresh_all_once", failing_then_ok_once)
        monkeypatch.setattr(er, "_REFRESH_INTERVAL_SECONDS", 0.01)

        pre = object()
        app = _StubApp(pre)
        task = asyncio.create_task(er.refresh_loop(tmp_path, synthetic_settings, app))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert tick_count >= 2, "loop should have run a second tick after the failure"


class TestCowConcurrencyInvariant:
    """Pin the CoW concurrency invariant: a reference to
    the OLD Enricher remains FUNCTIONAL (loaded pytricia tries still
    respond to membership checks; internal handles not closed) AFTER
    `app.state.enricher` is swapped, so concurrent in-flight `enrich()`
    calls don't fault on closed handles."""

    async def test_pre_swap_reference_stays_usable_after_swap(
        self,
        tmp_path: Path,
        low_floors: None,
        synthetic_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # No _stub_binary_loads here: this CoW test deliberately runs the real
        # _load_sources so the pre-swap instance loads whatever the installed
        # readers allow (the maxmind/ip2proxy guard degrades the small fixtures
        # to None; the FireHOL/cloud pytricia tries load and are the handles the
        # CoW invariant checks survive the swap).
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "firehol_level1" in url:
                return httpx.Response(200, content=_read_fixture("firehol_level1.netset"))
            if "firehol_level2" in url:
                return httpx.Response(200, content=_read_fixture("firehol_level2.netset"))
            if "GeoLite2-City" in url:
                return httpx.Response(200, content=_read_fixture("GeoLite2-City.tar.gz"))
            if "GeoLite2-ASN" in url:
                return httpx.Response(200, content=_read_fixture("GeoLite2-ASN.tar.gz"))
            if "ip2location.com" in url:
                return httpx.Response(200, content=_read_fixture("IP2PROXY-LITE-PX11.zip"))
            if "ip-ranges.amazonaws.com" in url:
                return httpx.Response(200, content=_read_fixture("ip-ranges.json"))
            if "gstatic.com" in url:
                return httpx.Response(200, content=_read_fixture("cloud.json"))
            if "microsoft.com/en-us/download" in url:
                return httpx.Response(
                    200,
                    content=(
                        b'href="https://download.microsoft.com/download/abc/'
                        b'ServiceTags_Public_20260609.json"'
                    ),
                )
            if "ServiceTags_Public" in url:
                return httpx.Response(200, content=_read_fixture("azure-service-tags.json"))
            if "cloudflare.com" in url:
                return httpx.Response(200, content=_read_fixture("ips-v4.txt"))
            return httpx.Response(500, content=b"unmocked")

        monkeypatch.setattr(er, "_build_http_client", _mock_client_factory(handler))

        from app.enrich import Enricher

        # Warmup tick to land fixture files on disk.
        warmup_app = _StubApp(object())
        await er.refresh_all_once(tmp_path, synthetic_settings, warmup_app)
        er._reset_loaded_sources_for_tests()

        # Build the pre-swap Enricher, eagerly load its sources.
        pre_enricher = Enricher(data_dir=tmp_path)
        pre_enricher._load_sources()
        assert pre_enricher._loaded is True

        # Baseline: pre-swap _lookup() returns an EnrichmentRow without
        # raising. This call exercises whichever of the source handles
        # successfully loaded (varies by which C extensions are
        # installed; the test runs both with and without
        # pytricia/maxminddb/ip2proxy).
        baseline_row = pre_enricher._lookup("192.0.2.1")
        assert baseline_row.ip == "192.0.2.1"

        # Trigger the swap
        app = _StubApp(pre_enricher)
        await er.refresh_all_once(tmp_path, synthetic_settings, app)
        assert app.state.enricher is not pre_enricher, "expected CoW swap"

        # CoW invariant: the pre-swap instance is STILL functional after
        # the swap. _lookup() must not raise (closed handles would
        # surface as ValueError / segfault on MaxMind C extensions; this
        # call would crash the test interpreter if the swap had any
        # teardown side effect on the old instance).
        post_swap_row = pre_enricher._lookup("192.0.2.1")
        assert post_swap_row.ip == "192.0.2.1"
        # And the loaded sentinel was not mutated by the swap
        assert pre_enricher._loaded is True

    async def test_log_tick_summary_counts(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pin the _log_tick_summary count math so a regression that
        e.g. double-counted failed or dropped the skipped bucket would
        fail the assertion."""
        results = [
            er.RefreshResult(source_name="firehol_level1", status="success"),
            er.RefreshResult(source_name="firehol_level2", status="success"),
            er.RefreshResult(source_name="maxmind_city", status="failed", failure_class="network"),
            er.RefreshResult(source_name="aws", status="skipped_sanity_floor"),
        ]
        with structlog.testing.capture_logs() as captured:
            er._log_tick_summary(results)
        summaries = [r for r in captured if r.get("event") == "enrich.refresh.tick_complete"]
        assert len(summaries) == 1
        s = summaries[0]
        assert s["success_count"] == 2
        assert s["failed_count"] == 1
        assert s["skipped_sanity_floor_count"] == 1
        assert s["total"] == 4


class TestLicenseKeySanitization:
    """Sentinel-string tests: real MAXMIND_LICENSE_KEY and
    IP2PROXY_DOWNLOAD_TOKEN values are intentionally distinctive.
    The sentinel string MUST NEVER appear in log records, exception
    messages, or RefreshResult fields, regardless of upstream outcome.
    """

    async def test_sentinel_absent_from_maxmind_logs_on_network_failure(
        self,
        tmp_path: Path,
        synthetic_settings: Settings,
    ) -> None:
        def handler(_req: httpx.Request) -> httpx.Response:
            msg = "simulated"
            raise httpx.ConnectError(msg)

        with structlog.testing.capture_logs() as captured:
            async with _make_client(handler) as client:
                result = await er.refresh_maxmind_city(client, tmp_path, synthetic_settings)
        # No sentinel anywhere in result fields
        for field_value in (result.error, result.source_name, result.failure_class):
            assert _SENTINEL_MAXMIND not in str(field_value or "")
        # No sentinel in any captured log record
        for record in captured:
            serialized = json.dumps(record, default=str)
            assert _SENTINEL_MAXMIND not in serialized

    async def test_sentinel_absent_from_ip2proxy_logs_on_5xx_exhaustion(
        self,
        tmp_path: Path,
        synthetic_settings: Settings,
    ) -> None:
        with structlog.testing.capture_logs() as captured:
            async with _make_client(_const_response(503, b"")) as client:
                result = await er.refresh_ip2proxy(client, tmp_path, synthetic_settings)
        for field_value in (result.error, result.source_name, result.failure_class):
            assert _SENTINEL_IP2P not in str(field_value or "")
        for record in captured:
            serialized = json.dumps(record, default=str)
            assert _SENTINEL_IP2P not in serialized

    async def test_sentinel_absent_from_ip2proxy_logs_on_rate_limit(
        self,
        tmp_path: Path,
        low_floors: None,
        synthetic_settings: Settings,
    ) -> None:
        body = b"THIS FILE CAN ONLY BE DOWNLOADED 5 TIMES WITHIN 24 HOURS."
        with structlog.testing.capture_logs() as captured:
            async with _make_client(_const_response(200, body)) as client:
                result = await er.refresh_ip2proxy(client, tmp_path, synthetic_settings)
        assert result.failure_class == "rate_limited"
        for record in captured:
            serialized = json.dumps(record, default=str)
            assert _SENTINEL_IP2P not in serialized

    async def test_safe_str_exc_does_not_leak_url(self) -> None:
        # httpx.ConnectError's __str__ can include the URL; _safe_str_exc
        # returns only the class name.
        exc = httpx.ConnectError(f"failed to reach https://example.com/x?token={_SENTINEL_IP2P}")
        assert er._safe_str_exc(exc) == "ConnectError"
        assert _SENTINEL_IP2P not in er._safe_str_exc(exc)


class TestLoadSourcesResilience:
    """Guard (graceful degradation): a corrupt / version-incompatible source
    file degrades like a *missing* one. ``_load_sources`` must not raise, the
    affected reader stays ``None``, and ``_lookup`` returns a degraded row —
    rather than crashing the load (and, via lazy load, a booking request) or
    blocking the refresh swap. Mirrors the already-guarded lookup path."""

    def test_load_sources_tolerates_corrupt_maxmind_and_ip2proxy(self, tmp_path: Path) -> None:
        # The asserted enrich.source_load_failed metrics only fire when the
        # readers are importable; otherwise _load_{maxmind,ip2proxy} returns
        # at the ImportError branch before the guarded open. Both are hard
        # deps, so skip cleanly rather than fail in a degenerate env that
        # lacks them.
        pytest.importorskip("maxminddb")
        pytest.importorskip("IP2Proxy")
        from app.enrich import Enricher
        from app.observability import METRIC_SPECS

        # Structurally-invalid binaries at the exact paths _load_sources reads.
        # (Short/garbage content: maxminddb and IP2Proxy both reject these on
        # open — the same outcome a real corrupt or version-incompatible
        # download would produce.)
        (tmp_path / "GeoLite2-City.mmdb").write_bytes(b"not a valid mmdb file")
        (tmp_path / "GeoLite2-ASN.mmdb").write_bytes(b"not a valid mmdb file")
        (tmp_path / "IP2PROXY-LITE-PX11.BIN").write_bytes(b"not a valid bin")

        enricher = Enricher(data_dir=tmp_path)
        with structlog.testing.capture_logs() as captured:
            enricher._load_sources()  # must NOT raise

        # Sentinel set; bad readers degraded to None.
        assert enricher._loaded is True
        assert enricher._mm_city is None
        assert enricher._mm_asn is None
        assert enricher._ip2p is None

        # Each failure is observable as one alarmable metric family
        # (enrich.source_load_failed) with a `source` dimension — the alarm
        # around the fail-open guard.
        failures = [r for r in captured if r.get("event") == "enrich.source_load_failed"]
        assert {r["source"] for r in failures} == {"maxmind_city", "maxmind_asn", "ip2proxy"}
        for r in failures:
            # metric=True so the EMF processor emits a CloudWatch point.
            assert r.get("metric") is True
            # Leak-safe: `error` is the exception TYPE name only (a bare
            # identifier), never a message that could carry a path/secret.
            assert r["error"].isidentifier()

        # The metric family is registered so the EMF processor emits it
        # (rather than the one-shot stderr pass-through) with source as the
        # CloudWatch dimension.
        assert "enrich.source_load_failed" in METRIC_SPECS
        assert METRIC_SPECS["enrich.source_load_failed"].dimensions == ("source",)

        # The corrupt-but-present sources are reflected for /health.
        assert enricher.degraded_sources() == frozenset({"maxmind_city", "maxmind_asn", "ip2proxy"})

        # Lookup degrades gracefully: no geo/proxy signals, no raise.
        row = enricher._lookup("192.0.2.1")
        assert row.ip == "192.0.2.1"
        assert row.country is None
