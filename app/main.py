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


app = FastAPI(title="freightsentry-riskd", lifespan=lifespan)
app.include_router(health_router, prefix="/health", tags=["health"])
app.include_router(booking_router, prefix="/api/v1/shipments", tags=["shipments"])
app.include_router(modification_router, prefix="/api/v1/shipments", tags=["shipments"])
app.include_router(feedback_router, prefix="/api/v1/shipments", tags=["shipments"])
app.include_router(admin_router, prefix="/api/v1")
