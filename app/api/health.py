"""GET /health/ — load-balancer probe. No auth required."""

import asyncio
from typing import Annotated

import asyncpg
import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.db import get_pool
from app.enrich import Enricher
from app.enrichment_refresh import all_sources_loaded_at_least_once
from app.runtime import get_enricher

_log = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/")
async def health(enricher: Annotated[Enricher, Depends(get_enricher)]) -> JSONResponse:
    """1s liveness check against the asyncpg pool + enrichment readiness.

    Returns:
      * 200 with `{ok: True, db: "ok", enrichment: "ok" | "degraded",
        pool: {...}}` on DB success. The `enrichment` field reports
        whether every Pattern B-lite source (per
        `enrichment_refresh._ALL_SOURCE_NAMES`) has either successfully
        refreshed at least once OR was present on disk at startup
        (hybrid Pattern A defense) AND that no loaded source failed to
        parse. A source that was downloaded but is corrupt /
        version-incompatible (the enricher dropped its reader and
        recorded it in `degraded_sources()`) flips this field to
        `"degraded"`. `"degraded"` does NOT change the HTTP status code
        per operator decision: the ALB target + ECS task health probe
        key off the status code, so degraded enrichment
        stays IN ROTATION (a corrupt dataset must not drop every task);
        operators alarm on the `enrich.source_load_failed` EMF metric and
        this field, not on task death.
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
    # Degraded if a source hasn't loaded at least once (cold start / still
    # warming) OR a loaded source failed to parse (downloaded-but-corrupt).
    # Both stay HTTP 200 — see docstring: the rotation probe keys off the
    # status code, so enrichment health must never flip it to non-2xx.
    enrichment_ok = all_sources_loaded_at_least_once() and not enricher.degraded_sources()
    enrichment = "ok" if enrichment_ok else "degraded"
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
