"""GET /health/ — load-balancer probe. No auth required."""

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
