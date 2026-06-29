"""GET /health/ — load-balancer probe. No auth required."""

import asyncpg
import pytest
from httpx import AsyncClient

from app import enrichment_refresh as er


@pytest.fixture(autouse=True)
def _reset_enrichment_loaded_state() -> None:
    """Each /health/ test starts with a clean enrichment-loaded set so
    the `enrichment` field reflects what THIS test sets up, not bleed
    from another test."""
    er._reset_loaded_sources_for_tests()


async def test_health_returns_ok(unauth_client: AsyncClient) -> None:
    response = await unauth_client.get("/health/")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["db"] == "ok"
    # Pool internals are intentionally NOT exposed on this unauthenticated
    # endpoint — only coarse db/enrichment signals.
    assert "pool" not in body


async def test_health_no_auth_required(unauth_client: AsyncClient) -> None:
    """/health/ must succeed without an Authorization header."""
    response = await unauth_client.get("/health/")
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_docs_endpoints_disabled_by_default(unauth_client: AsyncClient, path: str) -> None:
    """The app is built with the default (production) environment in the
    test/CI env, so the interactive docs + schema endpoints must not be
    mounted — a scanner hitting the public ALB gets 404, not the schema."""
    response = await unauth_client.get(path)
    assert response.status_code == 404


async def test_health_enrichment_degraded_on_cold_start(
    unauth_client: AsyncClient,
) -> None:
    """No sources have refreshed and none are seeded from disk →
    enrichment="degraded". HTTP 200 stays (degraded
    does NOT affect ALB rotation)."""
    response = await unauth_client.get("/health/")
    assert response.status_code == 200
    assert response.json()["enrichment"] == "degraded"


async def test_health_enrichment_ok_after_all_sources_loaded(
    unauth_client: AsyncClient,
) -> None:
    """When every source is marked loaded, the response reports
    enrichment="ok"."""
    for name in er._ALL_SOURCE_NAMES:
        er.mark_source_loaded(name)
    response = await unauth_client.get("/health/")
    assert response.status_code == 200
    assert response.json()["enrichment"] == "ok"


async def test_health_enrichment_degraded_on_partial_load(
    unauth_client: AsyncClient,
) -> None:
    """Fewer than all sources marked → still degraded."""
    er.mark_source_loaded("firehol_level1")
    er.mark_source_loaded("aws")
    response = await unauth_client.get("/health/")
    assert response.status_code == 200
    assert response.json()["enrichment"] == "degraded"


async def test_health_degraded_on_corrupt_but_downloaded_source(
    unauth_client: AsyncClient,
) -> None:
    """A source downloaded successfully (marked loaded) but FAILED TO PARSE
    (corrupt / version-incompatible) → enrichment="degraded", still HTTP
    200. Previously /health stayed "ok" because it keyed off download
    success, not parse success — silently failing open. Critically the
    status stays 200: the ALB/ECS rotation probe keys off the status code,
    so a corrupt dataset must NOT drop every task (it must alarm via the
    field + the enrich.source_load_failed metric instead)."""
    from app.main import app

    # All sources downloaded OK — the download tracker alone would say "ok".
    for name in er._ALL_SOURCE_NAMES:
        er.mark_source_loaded(name)
    # ...but one loaded source failed to parse (the enricher dropped its
    # reader and recorded the failure).
    app.state.enricher._load_failures.add("maxmind_city")

    response = await unauth_client.get("/health/")
    assert response.status_code == 200  # MUST stay in rotation
    assert response.json()["enrichment"] == "degraded"


async def test_health_returns_503_on_db_failure(
    unauth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the SELECT 1 liveness check fails, /health returns 503 with
    ok=false. Load balancers key rotation on status code, so the failure
    mode must surface as non-2xx. Enrichment field is omitted from the
    503 body (DB failure short-circuits before enrichment check)."""
    # Even with all enrichment loaded, DB failure still returns 503
    for name in er._ALL_SOURCE_NAMES:
        er.mark_source_loaded(name)

    original_fetchval = asyncpg.Connection.fetchval

    async def failing_fetchval(self, query, *args, **kwargs):  # type: ignore[no-untyped-def]
        if query == "SELECT 1":
            msg = "simulated db failure"
            raise asyncpg.PostgresError(msg)
        return await original_fetchval(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchval", failing_fetchval)

    response = await unauth_client.get("/health/")
    assert response.status_code == 503
    assert response.json() == {"ok": False, "db": "unreachable"}
