"""
Async MongoDB connection factory using Motor.

Provides a singleton database instance with connection pooling suitable for
high-throughput distributed services.
"""

import ssl
import logging
import certifi
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from core.config import MONGODB_URI, MONGODB_DB_NAME

logger = logging.getLogger(__name__)

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


def get_database() -> AsyncIOMotorDatabase:
    """Return the shared database handle, creating the client on first call."""
    global _client, _db

    if _db is None:
        _client = AsyncIOMotorClient(
            MONGODB_URI,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=30_000,
            connectTimeoutMS=30_000,
            maxPoolSize=50,
            minPoolSize=5,
        )
        _db = _client[MONGODB_DB_NAME]
        logger.info("MongoDB connection pool created (maxPoolSize=100)")

    return _db


async def init_database() -> AsyncIOMotorDatabase:
    """Initialize database and ensure all indexes exist."""
    database = get_database()

    from models.indexes import ensure_indexes
    await ensure_indexes(database)

    return database


async def close_connection():
    """Close the MongoDB connection pool."""
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None
        logger.info("MongoDB connection closed")
