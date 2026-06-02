"""GET /health/ — load-balancer probe. No auth required."""

import asyncpg
import pytest
from httpx import AsyncClient


async def test_health_returns_ok(unauth_client: AsyncClient) -> None:
    response = await unauth_client.get("/health/")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["db"] == "ok"
    assert body["pool"]["min_size"] == 2
    assert body["pool"]["max_size"] == 10


async def test_health_no_auth_required(unauth_client: AsyncClient) -> None:
    """/health/ must succeed without an Authorization header."""
    response = await unauth_client.get("/health/")
    assert response.status_code == 200


async def test_health_returns_503_on_db_failure(
    unauth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the SELECT 1 liveness check fails, /health returns 503 with
    ok=false. Load balancers key rotation on status code, so the failure
    mode must surface as non-2xx."""
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
