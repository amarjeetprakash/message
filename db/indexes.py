"""
Centralized MongoDB index management for idempotent and crash-safe initialization.
"""

import logging
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import OperationFailure

logger = logging.getLogger(__name__)

async def ensure_indexes(db: AsyncIOMotorDatabase):
    """
    Idempotent index creation. Checks if index exists before creating.
    """
    # Cleanup legacy non-compound unique indexes on groups to prevent findAndModify E11000 conflicts
    try:
        existing_g_indexes = await db.groups.index_information()
        for legacy_name in ["user_id_1_chat_id_1", "chat_id_1"]:
            if legacy_name in existing_g_indexes:
                logger.info(f"Dropping legacy index {legacy_name} from groups collection...")
                await db.groups.drop_index(legacy_name)
    except Exception as legacy_err:
        logger.warning(f"Note on dropping legacy group index: {legacy_err}")

    # Define your indexes here
    # Format: (collection_name, keys, options)
    index_definitions = [
        # Users: user_id unique
        ("users", "user_id", {"unique": True}),
        ("users", "referred_by", {}),
        
        # Sessions: (user_id, phone) unique compound
        ("sessions", [("user_id", 1), ("phone", 1)], {"unique": True}),
        ("sessions", "user_id", {}),
        ("sessions", "connected", {}),
        
        # Config: user_id unique
        ("config", "user_id", {"unique": True}),
        
        # Groups: (user_id, account_phone, chat_id) unique compound
        ("groups", [("user_id", 1), ("account_phone", 1), ("chat_id", 1)], {"name": "idx_groups_user_phone_chat", "sparse": True}),
        ("groups", [("user_id", 1), ("enabled", 1)], {"name": "idx_groups_user_enabled"}),
        ("groups", "user_id", {}),
        ("groups", "account_phone", {}),
        
        # Plans: user_id unique
        ("plans", "user_id", {"unique": True}),
        ("plans", [("status", 1), ("expires_at", 1)], {"name": "idx_plans_status_expires"}),
        ("plans", "expires_at", {}),
        ("plans", "status", {}),
        
        # Redeem codes: code unique
        ("redeem_codes", "code", {"unique": True}),
        ("redeem_codes", "used_by", {}),
        
        # Send logs
        ("send_logs", [("user_id", 1), ("phone", 1), ("chat_id", 1), ("saved_msg_id", 1), ("sent_at", -1)], {"name": "idx_anti_duplicate"}),
        ("send_logs", [("user_id", 1), ("status", 1), ("sent_at", -1)], {"name": "idx_send_logs_status"}),
        ("send_logs", [("user_id", 1), ("sent_at", -1)], {}),
        ("send_logs", "sent_at", {}),
        # TTL Index: Automatically delete logs after 30 days (2,592,000 seconds)
        ("send_logs", "sent_at", {"name": "idx_logs_ttl", "expireAfterSeconds": 2592000}),
    ]

    for coll_name, keys, options in index_definitions:
        try:
            collection = db[coll_name]
            
            # 1. Generate the expected index name if not provided
            if "name" not in options:
                if isinstance(keys, str):
                    name = f"{keys}_1"
                else:
                    name = "_".join([f"{k}_{v}" for k, v in keys])
            else:
                name = options["name"]

            # 2. Check if index already exists
            existing_indexes = await collection.index_information()
            
            if name in existing_indexes:
                # Index exists. Check if specs match to avoid future conflicts
                existing_spec = existing_indexes[name]
                # MongoDB returns keys as a list of lists/tuples depending on driver
                # Simple check for 'unique' flag suffices for most conflicts
                if options.get("unique") and not existing_spec.get("unique"):
                    logger.warning(f"Index {name} on {coll_name} exists but is NOT unique. Recreating...")
                    await collection.drop_index(name)
                else:
                    # Logic is safe, skip creation
                    continue

            # 3. Create index
            await collection.create_index(keys, **options)
            logger.info(f"Successfully ensured index: {coll_name}.{name}")

        except OperationFailure as e:
            # Handle the specific conflict error gracefully
            if "already exists with different options" in str(e) or e.code == 85:
                logger.warning(f"Index options conflict on {coll_name}. Dropping and recreating...")
                try:
                    await collection.drop_index(name)
                    await collection.create_index(keys, **options)
                except Exception as inner_e:
                    logger.error(f"Failed to resolve index conflict on {coll_name}: {inner_e}")
            else:
                logger.error(f"OperationFailure creating index on {coll_name}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error creating index on {coll_name}: {e}")

    logger.info("✅ Database indexes synchronization complete.")
