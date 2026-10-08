"""
Centralized Redis caching service for plan status, user configs, and settings.
Includes JSON serialization for datetime objects and transparent fallbacks on error.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from core.redis_client import get_redis_pool

logger = logging.getLogger(__name__)

# Key prefixes
PLAN_CACHE_PREFIX = "cache:plan:"
CONFIG_CACHE_PREFIX = "cache:config:"
DEFAULT_TTL = 300  # 5 minutes

# Circuit breaker for Redis outages (cooldown 30s if connection fails)
import time
_redis_offline_until = 0.0

def _is_redis_available() -> bool:
    global _redis_offline_until
    return time.time() >= _redis_offline_until

def _mark_redis_offline():
    global _redis_offline_until
    _redis_offline_until = time.time() + 30.0



try:
    import orjson
    HAS_ORJSON = True
except ImportError:
    HAS_ORJSON = False

class CustomJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle datetime objects."""
    def default(self, obj):
        if isinstance(obj, datetime):
            return {"__datetime__": True, "val": obj.isoformat()}
        return super().default(obj)


def datetime_decoder(dct: dict) -> dict:
    """JSON decoder hook to restore datetime objects."""
    for key, value in dct.items():
        if isinstance(value, dict) and value.get("__datetime__"):
            try:
                dct[key] = datetime.fromisoformat(value["val"])
            except (ValueError, TypeError):
                pass
    return dct


def serialize_data(data: dict) -> str:
    """Serialize dictionary cleanly to JSON string."""
    clean = {k: v for k, v in data.items() if k != "_id"}
    if HAS_ORJSON:
        def default(obj):
            if isinstance(obj, datetime):
                return {"__datetime__": True, "val": obj.isoformat()}
            raise TypeError
        return orjson.dumps(clean, default=default).decode("utf-8")
    return json.dumps(clean, cls=CustomJSONEncoder)


# ── PLAN CACHING ─────────────────────────────────────────────────────────────

async def get_cached_plan(user_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve user's cached plan from Redis."""
    if not _is_redis_available():
        return None
    try:
        redis = await get_redis_pool()
        raw = await redis.get(f"{PLAN_CACHE_PREFIX}{user_id}")
        if raw:
            return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw, object_hook=datetime_decoder)
    except Exception as e:
        _mark_redis_offline()
        logger.warning(f"Redis unavailable for get_cached_plan (cooldown 30s): {e}")
    return None


async def set_cached_plan(user_id: int, plan: Dict[str, Any], ttl: int = DEFAULT_TTL):
    """Store user's plan in Redis with TTL."""
    if not _is_redis_available() or not plan:
        return
    try:
        redis = await get_redis_pool()
        payload = serialize_data(plan)
        await redis.setex(f"{PLAN_CACHE_PREFIX}{user_id}", ttl, payload)
    except Exception as e:
        _mark_redis_offline()
        logger.warning(f"Redis unavailable for set_cached_plan (cooldown 30s): {e}")


async def invalidate_plan_cache(user_id: int):
    """Invalidate plan cache for a user."""
    if not _is_redis_available():
        return
    try:
        redis = await get_redis_pool()
        await redis.delete(f"{PLAN_CACHE_PREFIX}{user_id}")
    except Exception as e:
        _mark_redis_offline()
        logger.warning(f"Redis unavailable for invalidate_plan_cache (cooldown 30s): {e}")


# ── CONFIG CACHING ────────────────────────────────────────────────────────────

async def get_cached_user_config(user_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve user's cached config from Redis."""
    if not _is_redis_available():
        return None
    try:
        redis = await get_redis_pool()
        raw = await redis.get(f"{CONFIG_CACHE_PREFIX}{user_id}")
        if raw:
            return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw, object_hook=datetime_decoder)
    except Exception as e:
        _mark_redis_offline()
        logger.warning(f"Redis unavailable for get_cached_user_config (cooldown 30s): {e}")
    return None


async def set_cached_user_config(user_id: int, config: Dict[str, Any], ttl: int = DEFAULT_TTL):
    """Store user's config in Redis with TTL."""
    if not _is_redis_available() or not config:
        return
    try:
        redis = await get_redis_pool()
        payload = serialize_data(config)
        await redis.setex(f"{CONFIG_CACHE_PREFIX}{user_id}", ttl, payload)
    except Exception as e:
        _mark_redis_offline()
        logger.warning(f"Redis unavailable for set_cached_user_config (cooldown 30s): {e}")


async def invalidate_user_config_cache(user_id: int):
    """Invalidate user config cache."""
    if not _is_redis_available():
        return
    try:
        redis = await get_redis_pool()
        await redis.delete(f"{CONFIG_CACHE_PREFIX}{user_id}")
    except Exception as e:
        _mark_redis_offline()
        logger.warning(f"Redis unavailable for invalidate_user_config_cache (cooldown 30s): {e}")


async def invalidate_all_user_cache(user_id: int):
    """Invalidate both plan and config cache for a user."""
    await invalidate_plan_cache(user_id)
    await invalidate_user_config_cache(user_id)

