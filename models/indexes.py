"""
Centralized MongoDB index management for all collections.

Combines existing indexes from db/indexes.py with new indexes for
scheduled_jobs, job_logs, and worker_status.
"""

import logging
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import OperationFailure

logger = logging.getLogger(__name__)


async def ensure_indexes(db: AsyncIOMotorDatabase):
    """Idempotent index creation for all collections."""
    logger.info("Synchronizing database indexes...")

    # Cleanup legacy non-compound unique indexes on groups to prevent findAndModify E11000 conflicts
    try:
        existing_g_indexes = await db.groups.index_information()
        for legacy_name in ["user_id_1_chat_id_1", "chat_id_1"]:
            if legacy_name in existing_g_indexes:
                logger.info(f"Dropping legacy index {legacy_name} from groups collection...")
                await db.groups.drop_index(legacy_name)
    except Exception as legacy_err:
        logger.warning(f"Note on dropping legacy group index: {legacy_err}")

    index_definitions = [
        # ── Users ───────────────────────────────────────────────────────
        ("users", "user_id", {"unique": True}),
        ("users", "referred_by", {}),

        # ── Sessions ────────────────────────────────────────────────────
        ("sessions", [("user_id", 1), ("phone", 1)], {"unique": True}),
        ("sessions", "user_id", {}),
        ("sessions", "connected", {}),

        # ── Config ──────────────────────────────────────────────────────
        ("config", "user_id", {"unique": True}),

        # ── Groups ──────────────────────────────────────────────────────
        ("groups", [("user_id", 1), ("account_phone", 1), ("chat_id", 1)], {"name": "idx_groups_user_phone_chat", "sparse": True}),
        ("groups", [("user_id", 1), ("enabled", 1)], {"name": "idx_groups_user_enabled"}),
        ("groups", "user_id", {}),
        ("groups", "account_phone", {}),

        # ── Plans ───────────────────────────────────────────────────────
        ("plans", "user_id", {"unique": True}),
        ("plans", [("status", 1), ("expires_at", 1)], {"name": "idx_plans_status_expires"}),
        ("plans", "expires_at", {}),
        ("plans", "status", {}),

        # ── Redeem codes ────────────────────────────────────────────────
        ("redeem_codes", "code", {"unique": True}),
        ("redeem_codes", "used_by", {}),

        # ── Send logs (legacy) ──────────────────────────────────────────
        ("send_logs", [("user_id", 1), ("phone", 1), ("chat_id", 1),
                       ("saved_msg_id", 1), ("sent_at", -1)],
         {"name": "idx_anti_duplicate"}),
        ("send_logs", [("user_id", 1), ("status", 1), ("sent_at", -1)], {"name": "idx_send_logs_status"}),
        ("send_logs", [("user_id", 1), ("sent_at", -1)], {}),
        ("send_logs", "sent_at",
         {"name": "idx_logs_ttl", "expireAfterSeconds": 2592000}),

        # ══════════════════════════════════════════════════════════════════
        #  NEW — Distributed pipeline collections
        # ══════════════════════════════════════════════════════════════════

        # ── Scheduled jobs ──────────────────────────────────────────────
        ("scheduled_jobs", "job_id", {"unique": True}),
        ("scheduled_jobs", [("status", 1), ("run_at", 1)],
         {"name": "idx_pending_jobs"}),      # Hot path: scheduler query
        ("scheduled_jobs", "user_id", {}),
        ("scheduled_jobs", [("status", 1), ("worker_id", 1)],
         {"name": "idx_stuck_jobs"}),         # Dead-worker recovery

        # ── Job logs ────────────────────────────────────────────────────
        ("job_logs", [("job_id", 1), ("timestamp", 1)], {}),
        ("job_logs", "user_id", {}),
        ("job_logs", "timestamp",
         {"name": "idx_job_logs_ttl", "expireAfterSeconds": 2592000}),  # 30 days

        # ── Worker status ───────────────────────────────────────────────
        ("worker_status", "worker_id", {"unique": True}),
        ("worker_status", "last_seen", {}),
    ]

    created = 0
    for coll_name, keys, options in index_definitions:
        try:
            collection = db[coll_name]

            # Generate expected index name
            if "name" not in options:
                if isinstance(keys, str):
                    name = f"{keys}_1"
                else:
                    name = "_".join([f"{k}_{v}" for k, v in keys])
            else:
                name = options["name"]

            # Check if index already exists
            existing = await collection.index_information()
            if name in existing:
                existing_spec = existing[name]
                if options.get("unique") and not existing_spec.get("unique"):
                    logger.warning(f"Index {name} on {coll_name} not unique — recreating")
                    await collection.drop_index(name)
                else:
                    continue

            await collection.create_index(keys, **options)
            created += 1
            logger.info(f"Created index: {coll_name}.{name}")

        except OperationFailure as e:
            if "already exists with different options" in str(e) or e.code == 85:
                logger.warning(f"Index conflict on {coll_name} — drop + recreate")
                try:
                    await collection.drop_index(name)
                    await collection.create_index(keys, **options)
                    created += 1
                except Exception as inner:
                    logger.error(f"Failed to resolve index conflict on {coll_name}: {inner}")
            else:
                logger.error(f"OperationFailure on {coll_name}: {e}")
        except Exception as e:
            logger.error(f"Error creating index on {coll_name}: {e}")

    logger.info(f"✅ Index sync complete — {created} new indexes created")
