"""FastAPI app + lifespan.

Lifespan creates the asyncpg pool at startup and drains it on shutdown.
API routes attach in subsequent commits (1B.4 adds /health; 1C.1 adds
/api/v1/shipments/booking/evaluate; etc.).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.booking import router as booking_router
from app.api.health import router as health_router
from app.config import get_settings
from app.db import close_pool, init_pool
from app.logging import configure_logging


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = structlog.get_logger(__name__)
    logger.info("lifespan.startup", log_level=settings.log_level)
    await init_pool(settings)
    logger.info("lifespan.pool_initialised", min_size=2, max_size=10)
    try:
        yield
    finally:
        await close_pool()
        logger.info("lifespan.shutdown")


app = FastAPI(title="freightsentry-riskd", lifespan=lifespan)
app.include_router(health_router, prefix="/health", tags=["health"])
app.include_router(booking_router, prefix="/api/v1/shipments", tags=["shipments"])
