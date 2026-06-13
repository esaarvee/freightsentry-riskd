"""End-to-end integration tests for Pattern B-lite refresh.

Drives a full refresh tick against mocked `httpx.MockTransport` returning
C0 fixture bytes for every upstream URL pattern. Asserts:
  1. The 9 on-disk artifacts appear under `ENRICHMENT_DATA_DIR` with the
     filenames the Enricher's `_load_sources` reads.
  2. The Enricher constructed against the freshly-populated directory
     successfully loads its sources (skipped if pytricia / maxminddb are
     not installed in the local dev environment).
  3. After the tick, the module-level loaded-sources state advances so
     `/health/` would report `enrichment="ok"`.

Mocking pattern matches C2's lifespan test: monkeypatch
`enrichment_refresh._build_http_client` to inject a MockTransport-backed
client. No live network calls.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app import enrichment_refresh as er
from app.config import Settings

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "enrichment_refresh"


def _all_sources_handler() -> Callable[[httpx.Request], httpx.Response]:
    """Mock handler returning the C0 fixture for every known upstream
    URL pattern."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "firehol_level1" in url:
            return httpx.Response(
                200, content=(_FIXTURES_DIR / "firehol_level1.netset").read_bytes()
            )
        if "firehol_level2" in url:
            return httpx.Response(
                200, content=(_FIXTURES_DIR / "firehol_level2.netset").read_bytes()
            )
        if "GeoLite2-City" in url:
            return httpx.Response(
                200, content=(_FIXTURES_DIR / "GeoLite2-City.tar.gz").read_bytes()
            )
        if "GeoLite2-ASN" in url:
            return httpx.Response(200, content=(_FIXTURES_DIR / "GeoLite2-ASN.tar.gz").read_bytes())
        if "ip2location.com" in url:
            return httpx.Response(
                200, content=(_FIXTURES_DIR / "IP2PROXY-LITE-PX11.zip").read_bytes()
            )
        if "ip-ranges.amazonaws.com" in url:
            return httpx.Response(200, content=(_FIXTURES_DIR / "ip-ranges.json").read_bytes())
        if "gstatic.com" in url:
            return httpx.Response(200, content=(_FIXTURES_DIR / "cloud.json").read_bytes())
        if "microsoft.com/en-us/download" in url:
            return httpx.Response(
                200,
                content=(
                    b'href="https://download.microsoft.com/download/abc/'
                    b'ServiceTags_Public_20260609.json"'
                ),
            )
        if "ServiceTags_Public" in url:
            return httpx.Response(
                200, content=(_FIXTURES_DIR / "azure-service-tags.json").read_bytes()
            )
        if "cloudflare.com" in url:
            return httpx.Response(200, content=(_FIXTURES_DIR / "ips-v4.txt").read_bytes())
        return httpx.Response(500, content=b"unmocked")

    return handler


def _make_mock_client_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[], httpx.AsyncClient]:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


class _StubApp:
    """Minimal `app.state.enricher` carrier — `refresh_all_once` only
    touches `app.state.enricher`."""

    def __init__(self) -> None:
        class _State:
            pass

        self.state = _State()
        self.state.enricher = object()  # placeholder sentinel


@pytest.fixture(autouse=True)
def _reset_loaded() -> None:
    er._reset_loaded_sources_for_tests()


@pytest.fixture
def _synthetic_settings() -> Settings:
    return Settings(
        database_url="postgresql://test:test@localhost:5432/test",
        hmac_secret="test-secret",
        maxmind_license_key="SENTINEL-MAXMIND-KEY",
        ip2proxy_download_token="SENTINEL-IP2P-TOKEN",
    )  # type: ignore[call-arg]


@pytest.fixture
def _low_floors(monkeypatch: pytest.MonkeyPatch) -> None:
    """The C0 fixtures are well below the production sanity floors;
    relax to 1 byte so the refresh tick proceeds end-to-end."""
    monkeypatch.setattr(er, "_SANITY_FLOORS", dict.fromkeys(er._SANITY_FLOORS, 1))
    monkeypatch.setattr(er, "_EXTRACTED_FLOORS", dict.fromkeys(er._EXTRACTED_FLOORS, 1))


async def test_refresh_produces_all_nine_on_disk_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _low_floors: None,
    _synthetic_settings: Settings,
) -> None:
    """End-to-end: one refresh tick against mocked-fixture handler must
    produce every on-disk artifact the Enricher's `_load_sources` reads.
    This is the central C5 acceptance gate — it pins the URL→filename
    mapping across all 9 sources in one place."""
    monkeypatch.setattr(er, "_build_http_client", _make_mock_client_factory(_all_sources_handler()))

    app = _StubApp()
    results = await er.refresh_all_once(tmp_path, _synthetic_settings, app)

    # All 9 succeed
    success_names = {r.source_name for r in results if r.status == "success"}
    assert success_names == set(er._ALL_SOURCE_NAMES), (
        f"some sources failed: {[(r.source_name, r.status) for r in results]}"
    )

    # The 9 expected on-disk filenames (matching Enricher._load_sources)
    expected_files = [
        "firehol_level1.netset",
        "firehol_level2.netset",
        "GeoLite2-City.mmdb",  # extracted from tar.gz
        "GeoLite2-ASN.mmdb",  # extracted from tar.gz
        "IP2PROXY-LITE-PX11.BIN",  # extracted from ZIP
        "aws.cidr",  # extracted from JSON
        "gcp.cidr",  # extracted from JSON
        "azure.cidr",  # extracted from JSON (after HTML scrape)
        "cloudflare.cidr",
    ]
    for filename in expected_files:
        path = tmp_path / filename
        assert path.exists(), f"missing expected on-disk artifact: {filename}"
        assert path.stat().st_size > 0, f"on-disk artifact is empty: {filename}"

    # Module-level loaded-sources state advances: the health probe's
    # `all_sources_loaded_at_least_once()` returns True now.
    assert er.all_sources_loaded_at_least_once()


async def test_refresh_then_enricher_loads_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _low_floors: None,
    _synthetic_settings: Settings,
) -> None:
    """After a successful refresh tick, an `Enricher(data_dir)` instance
    must be able to load its sources from the populated directory. This
    test exercises the consumer-side contract: the refresh module's
    on-disk output is consumable by the Enricher without any further
    transformation.

    Skipped if pytricia/maxminddb are not installed in the local Python
    (the Enricher loaders silently no-op on ImportError, so the test
    would assert against trivially-empty handles)."""
    pytricia = pytest.importorskip("pytricia")
    del pytricia  # used only as availability gate
    monkeypatch.setattr(er, "_build_http_client", _make_mock_client_factory(_all_sources_handler()))

    app = _StubApp()
    await er.refresh_all_once(tmp_path, _synthetic_settings, app)

    from app.enrich import Enricher

    enricher = Enricher(data_dir=tmp_path)
    enricher._load_sources()
    assert enricher._loaded is True

    # FireHOL L1 trie is populated from the fixture (RFC-5737 CIDRs)
    assert enricher._firehol_l1 is not None
    assert "192.0.2.1" in enricher._firehol_l1, (
        "FireHOL L1 trie should contain 192.0.2.0/24 from the fixture"
    )

    # Cloud CIDR tries: every cloud provider's parsed prefix list lands
    # in `_cloud_tries`. Membership check uses pytricia's __contains__
    # (the public API the Enricher uses at lookup time).
    for provider in ("aws", "gcp", "azure", "cloudflare"):
        assert provider in enricher._cloud_tries, (
            f"missing {provider} from cloud tries after refresh"
        )
    # 192.0.2.0/24 (RFC-5737) is in every cloud-provider fixture
    assert "192.0.2.1" in enricher._cloud_tries["aws"], (
        "AWS trie should contain 192.0.2.0/25 fixture prefix"
    )


async def test_partial_failure_does_not_mark_failed_sources_loaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _low_floors: None,
    _synthetic_settings: Settings,
) -> None:
    """If a tick produces partial successes, only the successful sources
    advance `_loaded_sources`. The health probe must NOT report
    `enrichment="ok"` until every source has had at least one success."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # Succeed FireHOL + cloud-CIDR sources; fail MaxMind + IP2Proxy
        if "firehol_level1" in url:
            return httpx.Response(
                200, content=(_FIXTURES_DIR / "firehol_level1.netset").read_bytes()
            )
        if "firehol_level2" in url:
            return httpx.Response(
                200, content=(_FIXTURES_DIR / "firehol_level2.netset").read_bytes()
            )
        if "ip-ranges.amazonaws.com" in url:
            return httpx.Response(200, content=(_FIXTURES_DIR / "ip-ranges.json").read_bytes())
        if "gstatic.com" in url:
            return httpx.Response(200, content=(_FIXTURES_DIR / "cloud.json").read_bytes())
        if "microsoft.com/en-us/download" in url:
            return httpx.Response(
                200,
                content=(
                    b'href="https://download.microsoft.com/download/abc/'
                    b'ServiceTags_Public_20260609.json"'
                ),
            )
        if "ServiceTags_Public" in url:
            return httpx.Response(
                200, content=(_FIXTURES_DIR / "azure-service-tags.json").read_bytes()
            )
        if "cloudflare.com" in url:
            return httpx.Response(200, content=(_FIXTURES_DIR / "ips-v4.txt").read_bytes())
        # MaxMind City+ASN and IP2Proxy 5xx
        return httpx.Response(503, content=b"")

    monkeypatch.setattr(er, "_build_http_client", _make_mock_client_factory(handler))

    app = _StubApp()
    results = await er.refresh_all_once(tmp_path, _synthetic_settings, app)

    success_names = {r.source_name for r in results if r.status == "success"}
    failed_names = {r.source_name for r in results if r.status == "failed"}

    expected_successes = {
        "firehol_level1",
        "firehol_level2",
        "aws",
        "gcp",
        "azure",
        "cloudflare",
    }
    assert success_names == expected_successes
    assert failed_names == {"maxmind_city", "maxmind_asn", "ip2proxy"}

    # Only succeeded sources are marked loaded
    assert er.loaded_sources_snapshot() == frozenset(success_names)
    # Health probe stays "degraded" until every source has had a success
    assert not er.all_sources_loaded_at_least_once()


async def test_health_endpoint_reports_ok_after_full_refresh_tick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _low_floors: None,
    _synthetic_settings: Settings,
    unauth_client: object,
) -> None:
    """End-to-end: refresh tick succeeds across all 9 sources; the
    `/health/` HTTP response then reports `enrichment="ok"`. Bridges
    the C5 refresh-pipeline tests with the C3 health-probe contract."""
    # First: confirm cold-start is degraded
    response = await unauth_client.get("/health/")  # type: ignore[attr-defined]
    assert response.status_code == 200
    assert response.json()["enrichment"] == "degraded"

    monkeypatch.setattr(er, "_build_http_client", _make_mock_client_factory(_all_sources_handler()))
    app = _StubApp()
    await er.refresh_all_once(tmp_path, _synthetic_settings, app)

    # All 9 sources loaded → health probe reports ok
    response = await unauth_client.get("/health/")  # type: ignore[attr-defined]
    assert response.status_code == 200
    body = response.json()
    assert body["enrichment"] == "ok"
    assert body["ok"] is True
    assert body["db"] == "ok"


async def test_enricher_enrich_public_api_returns_populated_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _low_floors: None,
    _synthetic_settings: Settings,
    db_conn: object,
) -> None:
    """End-to-end public-API exercise per plan §C5: after refresh,
    `Enricher.enrich(conn, ip)` returns an EnrichmentRow with non-trivial
    fields when the input IP is in the fixture-loaded FireHOL set.

    Skipped if pytricia is unavailable (dev env case). Uses the `db_conn`
    fixture from conftest for the asyncpg connection."""
    pytest.importorskip("pytricia")
    monkeypatch.setattr(er, "_build_http_client", _make_mock_client_factory(_all_sources_handler()))

    app = _StubApp()
    await er.refresh_all_once(tmp_path, _synthetic_settings, app)

    from ipaddress import IPv4Address

    from app.enrich import Enricher

    enricher = Enricher(data_dir=tmp_path)
    # 192.0.2.1 is in the FireHOL L1 fixture (RFC-5737 192.0.2.0/24)
    # and in every cloud-provider fixture
    row = await enricher.enrich(db_conn, IPv4Address("192.0.2.1"))  # type: ignore[arg-type]
    assert row.ip == "192.0.2.1"
    # FireHOL membership populated from fixture
    assert row.fh_level1 is True, "expected fh_level1 True from fixture CIDR"
    # Cloud detection: AWS prefix covers 192.0.2.0/24
    assert row.is_cloud is True
    # Cleanup: delete the inserted row to keep the DB clean
    await db_conn.execute("DELETE FROM ip_enrichment WHERE ip = $1::inet", "192.0.2.1")  # type: ignore[attr-defined]
