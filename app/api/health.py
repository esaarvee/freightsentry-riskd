"""GET /health/ — load-balancer probe. No auth required."""

import asyncio

from fastapi import APIRouter

from app.db import get_pool

router = APIRouter()


@router.get("/")
async def health() -> dict[str, object]:
    pool = get_pool()
    try:
        async with asyncio.timeout(1.0):
            async with pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1")
    except (TimeoutError, Exception):
        return {"ok": False, "db": "unreachable"}
    return {
        "ok": result == 1,
        "db": "ok",
        "pool": {
            "size": pool.get_size(),
            "min_size": pool.get_min_size(),
            "max_size": pool.get_max_size(),
        },
    }
