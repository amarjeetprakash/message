"""
MongoDB database connection using motor (async driver).
"""

import ssl
import certifi
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from config import MONGODB_URI

# Global client and database instances
_client: AsyncIOMotorClient = None
_db: AsyncIOMotorDatabase = None


def get_database() -> AsyncIOMotorDatabase:
    """Get the database instance, creating connection if needed."""
    global _client, _db
    
    if _db is None:
        try:
            # Configure SSL/TLS for MongoDB Atlas
            # Use certifi's certificate bundle for proper SSL verification
            _client = AsyncIOMotorClient(
                MONGODB_URI,
                tlsCAFile=certifi.where(),
                tlsAllowInvalidCertificates=True,
                serverSelectionTimeoutMS=30000,
                connectTimeoutMS=30000,
                maxPoolSize=50,
                minPoolSize=5,
            )
            # Access a property to trigger client creation (idempotent)
            _db = _client.spinify
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to connect to MongoDB: {e}")
            raise
    
    return _db



async def init_database() -> AsyncIOMotorDatabase:
    """Initialize database connection and ensure all indexes exist."""
    database = get_database()
    
    # Fast connectivity & authentication check before attempting index synchronization
    await database.command("ping")
    
    # Run idempotent index synchronization
    from .indexes import ensure_indexes
    await ensure_indexes(database)
    
    return database


async def init_indexes():
    """Legacy wrapper for backward compatibility."""
    await init_database()


async def close_connection():
    """Close the database connection."""
    global _client, _db
    
    if _client:
        _client.close()
        _client = None
        _db = None
