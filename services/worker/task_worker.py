"""
ARQ-based Task Worker — pulls send jobs from Redis and executes them.

Each worker process can handle WORKER_CONCURRENCY concurrent jobs using asyncio.
Workers send heartbeats every 30 seconds and are horizontally scalable:
    docker-compose up --scale worker=20

Usage:
  arq services.worker.task_worker.WorkerSettings

Or directly:
  python -m services.worker.task_worker
"""

import os
import asyncio
import logging
import platform
import random
from typing import Optional

from arq import cron
from arq.connections import RedisSettings

from core.logger import setup_service_logging
from core.database import init_database, close_connection
from core.redis_client import get_redis_settings, close_redis
from core.config import (
    WORKER_CONCURRENCY,
    SEND_DELAY_MIN,
    SEND_DELAY_MAX,
    MAX_RETRY_COUNT,
)
from models.job import claim_job, complete_job, fail_job, get_job, upsert_worker_heartbeat
from services.worker.session_pool import SessionPool
from services.worker.send_logic import send_message_to_group

logger = logging.getLogger(__name__)

# ── Global worker state ─────────────────────────────────────────────────────

WORKER_ID = f"{platform.node()}-{os.getpid()}"
_session_pool: Optional[SessionPool] = None
_active_jobs = 0
_total_processed = 0


# ══════════════════════════════════════════════════════════════════════════════
#  LIFECYCLE HOOKS (called by ARQ framework)
# ══════════════════════════════════════════════════════════════════════════════

async def startup(ctx: dict):
    """Called once when the ARQ worker starts."""
    global _session_pool

    setup_service_logging("worker")
    logger.info("=" * 50)
    logger.info(f"Worker {WORKER_ID} Starting (concurrency={WORKER_CONCURRENCY})")
    logger.info("=" * 50)

    await init_database()

    _session_pool = SessionPool()
    await _session_pool.start()

    ctx["pool"] = _session_pool
    ctx["worker_id"] = WORKER_ID

    logger.info(f"✅ Worker {WORKER_ID} ready")


async def shutdown(ctx: dict):
    """Called once when the ARQ worker shuts down."""
    global _session_pool

    logger.info(f"Worker {WORKER_ID} shutting down...")

    if _session_pool:
        await _session_pool.stop()

    await close_connection()
    logger.info(f"Worker {WORKER_ID} stopped")


# ══════════════════════════════════════════════════════════════════════════════
#  HEARTBEAT (runs every 30 seconds via ARQ cron)
# ══════════════════════════════════════════════════════════════════════════════

async def heartbeat(ctx: dict):
    """Send a heartbeat to MongoDB so the scheduler knows we're alive."""
    await upsert_worker_heartbeat(
        worker_id=WORKER_ID,
        active_jobs=_active_jobs,
        total_processed=_total_processed,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN JOB HANDLER
# ══════════════════════════════════════════════════════════════════════════════

async def send_job(ctx: dict, job_id: str):
    """
    Process a scheduled send job.

    Steps:
      1. Atomically claim the job (queued → processing).
      2. Acquire a Telethon client from the session pool.
      3. Iterate over target groups, sending the message.
      4. Apply random delay (10-20s) between groups.
      5. Update job status to done or failed.

    This function is registered with ARQ and called automatically when
    a job is dequeued from Redis.
    """
    global _active_jobs, _total_processed

    pool: SessionPool = ctx["pool"]
    worker_id: str = ctx["worker_id"]

    # ── 1. Claim the job atomically ─────────────────────────────────────
    job = await claim_job(job_id, worker_id)
    if not job:
        # Another worker already claimed it, or it's been cancelled
        logger.debug(f"Job {job_id} already claimed or gone — skipping")
        return

    _active_jobs += 1
    user_id = job["user_id"]
    phone = job["phone"]
    message_id = job["message_id"]
    groups = job.get("groups", [])
    copy_mode = job.get("copy_mode", False)

    logger.info(
        f"📥 Processing job {job_id}: user={user_id}, phone={phone}, "
        f"msg={message_id}, groups={len(groups)}"
    )

    try:
        # ── 2. Acquire Telethon client ──────────────────────────────────
        try:
            client = await pool.acquire(user_id, phone)
        except RuntimeError as e:
            logger.error(f"Session unavailable for {phone}: {e}")
            await fail_job(job_id, str(e))
            return

        # ── 3. Send to each group ───────────────────────────────────────
        sent_count = 0
        flood_total = 0

        for i, group_id in enumerate(groups):
            status, flood_seconds = await send_message_to_group(
                client=client,
                job_id=job_id,
                user_id=user_id,
                phone=phone,
                message_id=message_id,
                group_id=group_id,
                copy_mode=copy_mode,
            )

            if status == "sent":
                sent_count += 1
            elif status == "deactivated":
                # Account is dead — no point continuing
                logger.error(f"Account {phone} deactivated mid-job. Aborting.")
                await fail_job(job_id, "Account deactivated")
                return
            elif status == "flood":
                flood_total += flood_seconds
                if flood_seconds > 0:
                    if flood_seconds > 60:
                        logger.warning(f"🛑 FloodWait too long ({flood_seconds}s). Aborting job {job_id} to free worker slot.")
                        await fail_job(job_id, f"FloodWait {flood_seconds}s")
                        return
                    else:
                        sleep_time = flood_seconds + 5
                        logger.warning(f"⏳ FloodWait: sleeping {sleep_time}s")
                        await asyncio.sleep(sleep_time)

            # ── 4. Random delay between groups ──────────────────────────
            if i < len(groups) - 1:
                delay = random.randint(SEND_DELAY_MIN, SEND_DELAY_MAX)
                await asyncio.sleep(delay)

        # ── 5. Mark job as done ─────────────────────────────────────────
        await complete_job(job_id, groups_sent=sent_count)

        # ── 6. Cleanup stale failing groups (24h+ old) ──────────────────
        from models.group import remove_stale_failing_groups
        removed = await remove_stale_failing_groups(user_id)
        if removed > 0:
            logger.info(f"🗑️ Auto-removed {removed} stale failing group(s) for user {user_id}")

        pool.release(user_id, phone)

        logger.info(
            f"✅ Job {job_id} complete: {sent_count}/{len(groups)} groups sent"
        )

    except Exception as e:
        logger.error(f"Job {job_id} failed with exception: {e}")
        await fail_job(job_id, str(e))

    finally:
        _active_jobs -= 1
        _total_processed += 1


# ══════════════════════════════════════════════════════════════════════════════
#  ARQ WORKER SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

class WorkerSettings:
    """
    ARQ worker configuration.

    ARQ reads this class to configure the worker process.
    Run with: arq services.worker.task_worker.WorkerSettings
    """
    functions = [send_job]
    on_startup = startup
    on_shutdown = shutdown

    cron_jobs = [
        cron(heartbeat, second={0, 30}),  # Every 30 seconds
    ]

    redis_settings = get_redis_settings()
    max_jobs = WORKER_CONCURRENCY
    job_timeout = 3600  # 1 hour max per job
    max_tries = 1       # We handle retries via MongoDB, not ARQ


# ── Direct-run entry point ──────────────────────────────────────────────────

if __name__ == "__main__":
    import subprocess
    import sys

    # Run ARQ worker pointing to this module's WorkerSettings
    subprocess.run([
        sys.executable, "-m", "arq",
        "services.worker.task_worker.WorkerSettings",
    ])
