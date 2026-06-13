"""Integration test for the Pattern B-lite refresh task wired into the
FastAPI lifespan.

The lifespan-aware test pattern is direct invocation
of the `lifespan` async context manager — no `TestClient`, no
`LifespanManager`, no new dev dependency. This matches the project's
existing ASGI-transport convention while exercising lifespan code paths
that `tests/conftest.py`'s `_pool` fixture deliberately skips.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app import enrichment_refresh as er
from app.main import app, lifespan


def _const_handler(status: int, body: bytes) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return handler


def _make_mock_client_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[], httpx.AsyncClient]:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


@pytest.fixture(autouse=True)
def _reset_refresh_module_state() -> None:
    er._reset_loaded_sources_for_tests()


@pytest.fixture
def _short_refresh_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(er, "_REFRESH_INTERVAL_SECONDS", 60)


@pytest.fixture
def _bypass_pool_in_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    """The conftest `_pool` fixture already initialises asyncpg at
    session scope. These lifespan tests don't care about pool semantics
    — they exercise the refresh-task spawn/cancel cycle — so we mock
    `init_pool` and `close_pool` in `app.main`'s namespace to no-ops so
    the lifespan can run without colliding with the session-scoped pool.
    """

    async def _noop_init(_settings: object) -> None:
        return None

    async def _noop_close() -> None:
        return None

    import app.main

    monkeypatch.setattr(app.main, "init_pool", _noop_init)
    monkeypatch.setattr(app.main, "close_pool", _noop_close)


async def test_lifespan_spawns_and_cancels_refresh_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _short_refresh_interval: None,
    _bypass_pool_in_lifespan: None,
) -> None:
    """The lifespan async context manager spawns the refresh task on
    entry and cancels-and-awaits it on exit. No orphan tempfiles in
    ENRICHMENT_DATA_DIR after exit.

    All upstream HTTP calls return 503 so the first tick is all-failed
    (no Enricher swap), keeping the test focused on lifespan semantics
    rather than refresh content."""
    monkeypatch.setattr(
        er, "_build_http_client", _make_mock_client_factory(_const_handler(503, b""))
    )

    # Redirect ENRICHMENT_DATA_DIR to tmp_path so we can scan for orphans
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ENRICHMENT_DATA_DIR", str(tmp_path))

    # Plant an orphan tempfile to confirm shutdown cleanup runs
    orphan = tmp_path / "firehol_level1.netset.tmp.deadbeef"
    orphan.write_bytes(b"leftover")

    async with lifespan(app):
        # Inside the context, the refresh task is alive
        tasks = [t for t in asyncio.all_tasks() if t.get_name() == "enrichment_refresh_loop"]
        assert len(tasks) == 1, f"expected 1 refresh task, got {len(tasks)}"
        # Give it a beat to run one tick (all 503 → no swap)
        await asyncio.sleep(0.1)
        assert not tasks[0].done(), "refresh task should still be running mid-lifespan"

    # After lifespan exit: task is gone from the event loop's active set.
    # A correctly-cancelled-and-awaited task is removed from all_tasks(),
    # not merely marked done — so the empty-list assertion is the tight
    # invariant (per test-reviewer cycle-1 feedback on the lifespan test).
    remaining = [t for t in asyncio.all_tasks() if t.get_name() == "enrichment_refresh_loop"]
    assert remaining == [], (
        f"refresh task should be removed from all_tasks() after lifespan exit; got {remaining}"
    )

    # Orphan tempfile cleaned up by the cancellation cleanup
    assert not orphan.exists(), "lifespan shutdown should clean orphan tempfiles"

    get_settings.cache_clear()


async def test_lifespan_swap_replaces_app_state_enricher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _short_refresh_interval: None,
    _bypass_pool_in_lifespan: None,
) -> None:
    """When refresh succeeds inside the lifespan, `app.state.enricher`
    is atomically swapped (CoW). The pre-swap Enricher
    instance the test captures before entering the lifespan should NOT
    be the same object as `app.state.enricher` after one successful
    refresh tick."""
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "enrichment_refresh"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "firehol_level1" in url:
            return httpx.Response(
                200, content=(fixtures_dir / "firehol_level1.netset").read_bytes()
            )
        if "firehol_level2" in url:
            return httpx.Response(
                200, content=(fixtures_dir / "firehol_level2.netset").read_bytes()
            )
        if "GeoLite2-City" in url:
            return httpx.Response(200, content=(fixtures_dir / "GeoLite2-City.tar.gz").read_bytes())
        if "GeoLite2-ASN" in url:
            return httpx.Response(200, content=(fixtures_dir / "GeoLite2-ASN.tar.gz").read_bytes())
        if "ip2location.com" in url:
            return httpx.Response(
                200, content=(fixtures_dir / "IP2PROXY-LITE-PX11.zip").read_bytes()
            )
        if "ip-ranges.amazonaws.com" in url:
            return httpx.Response(200, content=(fixtures_dir / "ip-ranges.json").read_bytes())
        if "gstatic.com" in url:
            return httpx.Response(200, content=(fixtures_dir / "cloud.json").read_bytes())
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
                200, content=(fixtures_dir / "azure-service-tags.json").read_bytes()
            )
        if "cloudflare.com" in url:
            return httpx.Response(200, content=(fixtures_dir / "ips-v4.txt").read_bytes())
        return httpx.Response(500, content=b"unmocked")

    monkeypatch.setattr(er, "_build_http_client", _make_mock_client_factory(handler))
    # Floors low so the small fixtures pass
    monkeypatch.setattr(er, "_SANITY_FLOORS", dict.fromkeys(er._SANITY_FLOORS, 1))
    monkeypatch.setattr(er, "_EXTRACTED_FLOORS", dict.fromkeys(er._EXTRACTED_FLOORS, 1))

    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ENRICHMENT_DATA_DIR", str(tmp_path))

    async with lifespan(app):
        # Capture the post-init Enricher reference
        pre_enricher = app.state.enricher
        # Wait for at least one refresh tick to complete
        for _ in range(50):
            await asyncio.sleep(0.05)
            if app.state.enricher is not pre_enricher:
                break
        assert app.state.enricher is not pre_enricher, (
            "expected app.state.enricher to be swapped after a successful refresh tick"
        )

    get_settings.cache_clear()
