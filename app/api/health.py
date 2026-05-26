"""GET /health/ — load-balancer probe. No auth required."""

import asyncio

import asyncpg
import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.db import get_pool

_log = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/")
async def health() -> JSONResponse:
    """1s liveness check against the asyncpg pool.

    Returns:
      * 200 with `{ok: True, db: "ok", pool: {...}}` on success.
      * 503 with `{ok: False, db: "unreachable"}` on DB-side failure
        (timeout, asyncpg error, network OSError). Load balancers key
        rotation decisions on the HTTP status code, so the failure mode
        must surface as a non-2xx response.
      * Programmer errors (`RuntimeError` from `get_pool()` if lifespan
        didn't run, `AttributeError`, etc.) propagate as 500 — observable
        in alerts rather than hidden behind a generic "db: unreachable".
    """
    pool = get_pool()
    try:
        async with asyncio.timeout(1.0):
            async with pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1")
    except (TimeoutError, asyncpg.PostgresError, OSError) as exc:
        _log.warning("health.db_check_failed", error_type=type(exc).__name__, detail=str(exc))
        return JSONResponse(
            status_code=503,
            content={"ok": False, "db": "unreachable"},
        )
    return JSONResponse(
        status_code=200,
        content={
            "ok": result == 1,
            "db": "ok",
            "pool": {
                "size": pool.get_size(),
                "min_size": pool.get_min_size(),
                "max_size": pool.get_max_size(),
            },
        },
    )
