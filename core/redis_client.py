"""
Redis connection utilities for ARQ job queue and general caching.
"""

import logging
from typing import Optional
from arq.connections import RedisSettings, ArqRedis, create_pool
from urllib.parse import urlparse

from core.config import REDIS_URL

logger = logging.getLogger(__name__)

_pool: Optional[ArqRedis] = None


def get_redis_settings() -> RedisSettings:
    """Parse REDIS_URL into ARQ RedisSettings."""
    parsed = urlparse(REDIS_URL)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or 0),
        password=parsed.password,
    )


async def get_redis_pool() -> ArqRedis:
    """Get or create the shared ARQ Redis connection pool."""
    global _pool
    if _pool is None:
        _pool = await create_pool(get_redis_settings())
        logger.info(f"Redis pool created → {REDIS_URL}")
    return _pool


async def close_redis():
    """Close the Redis connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Redis pool closed")


async def redis_health_check() -> bool:
    """Return True if Redis is reachable."""
    try:
        pool = await get_redis_pool()
        return await pool.ping()
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        return False
