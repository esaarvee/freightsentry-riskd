"""GET /health/ — load-balancer probe. No auth required."""

import asyncio

import asyncpg
import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.db import get_pool
from app.enrichment_refresh import all_sources_loaded_at_least_once

_log = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/")
async def health() -> JSONResponse:
    """1s liveness check against the asyncpg pool + enrichment readiness.

    Returns:
      * 200 with `{ok: True, db: "ok", enrichment: "ok" | "degraded",
        pool: {...}}` on DB success. The `enrichment` field reports
        whether every Pattern B-lite source (per
        `enrichment_refresh._ALL_SOURCE_NAMES`) has either successfully
        refreshed at least once OR was present on disk at startup
        (hybrid Pattern A defense). `"degraded"` does NOT change the
        HTTP status code per Amendment 1 F2 / operator decision: the
        ALB target stays in rotation even while enrichment is still
        warming up; operators monitor the field via CloudWatch logs +
        the EMF metrics emitted by the refresh task.
      * 503 with `{ok: False, db: "unreachable"}` on DB-side failure
        (timeout, asyncpg error, network OSError). Load balancers key
        rotation decisions on the HTTP status code, so the DB failure
        mode surfaces as non-2xx. Enrichment-degraded does not.
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
    enrichment = "ok" if all_sources_loaded_at_least_once() else "degraded"
    return JSONResponse(
        status_code=200,
        content={
            "ok": result == 1,
            "db": "ok",
            "enrichment": enrichment,
            "pool": {
                "size": pool.get_size(),
                "min_size": pool.get_min_size(),
                "max_size": pool.get_max_size(),
            },
        },
    )
