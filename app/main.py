"""FastAPI app + lifespan.

Lifespan creates the asyncpg pool at startup and drains it on shutdown.
Lifespan also spawns the Pattern B-lite refresh task after
`init_runtime` (it needs `app.state.enricher` so it can atomically swap
the instance on each successful refresh tick via the copy-on-write
swap) and cancels-and-awaits it BEFORE closing the pool on shutdown
so the refresh task's structured-log calls still have a working
context.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.booking import router as booking_router
from app.api.feedback import router as feedback_router
from app.api.health import router as health_router
from app.api.modification import router as modification_router
from app.config import get_settings
from app.db import close_pool, init_pool
from app.enrichment_refresh import refresh_loop
from app.logging import configure_logging
from app.runtime import init_runtime


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = structlog.get_logger(__name__)
    logger.info("lifespan.startup", log_level=settings.log_level)
    await init_pool(settings)
    logger.info("lifespan.pool_initialised", min_size=2, max_size=10)
    ruleset, enricher = init_runtime(settings)
    app.state.ruleset = ruleset
    app.state.enricher = enricher
    logger.info(
        "lifespan.runtime_initialised",
        rule_count=len(ruleset.rules),
        allow_max=ruleset.thresholds.allow_max,
        block_min=ruleset.thresholds.block_min,
    )
    refresh_task = asyncio.create_task(
        refresh_loop(Path(settings.enrichment_data_dir), settings, app),
        name="enrichment_refresh_loop",
    )
    logger.info("lifespan.refresh_task_started")
    try:
        yield
    finally:
        refresh_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresh_task
        logger.info("lifespan.refresh_task_cancelled")
        await close_pool()
        logger.info("lifespan.shutdown")


def _docs_kwargs(environment: str) -> dict[str, str | None]:
    """Decide whether the interactive API docs are exposed.

    FastAPI mounts `/docs`, `/redoc`, and `/openapi.json` by default,
    unauthenticated. Behind the internet-facing ALB that publishes the
    entire route + schema surface — including the `/api/v1` admin routes
    and the Bearer auth scheme — to any scanner. We expose them only in
    local/dev. Fail closed: any value other than an explicit dev marker
    (including unset → "production") disables all three. Setting
    `openapi_url=None` also disables the Swagger/ReDoc UIs, which depend
    on the schema; the explicit None on each is belt-and-suspenders.
    """
    if environment.strip().lower() in {"dev", "development", "local"}:
        return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


_docs = _docs_kwargs(get_settings().environment)
app = FastAPI(
    title="freightsentry-riskd",
    lifespan=lifespan,
    docs_url=_docs["docs_url"],
    redoc_url=_docs["redoc_url"],
    openapi_url=_docs["openapi_url"],
)
app.include_router(health_router, prefix="/health", tags=["health"])
app.include_router(booking_router, prefix="/api/v1/shipments", tags=["shipments"])
app.include_router(modification_router, prefix="/api/v1/shipments", tags=["shipments"])
app.include_router(feedback_router, prefix="/api/v1/shipments", tags=["shipments"])
app.include_router(admin_router, prefix="/api/v1")
