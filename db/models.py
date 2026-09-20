"""
MongoDB document models and helper functions for CRUD operations.
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import secrets
import pytz

from .database import get_database
from config import (
    PLAN_DURATIONS, DEFAULT_INTERVAL_MINUTES, TIMEZONE
)

IST = pytz.timezone(TIMEZONE)


# ==================== USERS ====================

async def create_user(user_id: int, referred_by: Optional[str] = None) -> Dict[str, Any]:
    """Create a new user."""
    db = get_database()
    
    user_doc = {
        "user_id": user_id,
        "created_at": datetime.utcnow(),
    }
    
    await db.users.update_one(
        {"user_id": user_id},
        {"$setOnInsert": user_doc},
        upsert=True
    )
    
    return await get_user(user_id)


async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Get user by ID."""
    db = get_database()
    return await db.users.find_one({"user_id": user_id})






# ==================== SESSIONS ====================

async def create_session(
    user_id: int, 
    phone: str, 
    session_string: str,
    api_id: int = None,
    api_hash: str = None
) -> Dict[str, Any]:
    """Create or update user session with per-user API credentials."""
    db = get_database()
    
    session_doc = {
        "user_id": user_id,
        "phone": phone,
        "session_string": session_string,
        "api_id": api_id,
        "api_hash": api_hash,
        "connected": True,
        "connected_at": datetime.utcnow(),
    }
    
    await db.sessions.update_one(
        {"user_id": user_id, "phone": phone},
        {"$set": session_doc},
        upsert=True
    )
    
    
    return session_doc


async def get_session(user_id: int, phone: str = None) -> Optional[Dict[str, Any]]:
    """Get session by user ID (and optionally phone)."""
    db = get_database()
    query = {"user_id": user_id}
    if phone:
        query["phone"] = phone
    return await db.sessions.find_one(query)


async def get_all_user_sessions(user_id: int) -> List[Dict[str, Any]]:
    """Get all connected sessions for a specific user."""
    db = get_database()
    cursor = db.sessions.find({"user_id": user_id, "connected": True})
    return await cursor.to_list(length=None)


async def get_all_connected_sessions() -> List[Dict[str, Any]]:
    """Get all connected sessions for worker."""
    db = get_database()
    cursor = db.sessions.find({"connected": True})
    return await cursor.to_list(length=None)


async def update_session_activity(user_id: int, phone: str):
    """Update last active timestamp for a session."""
    db = get_database()
    await db.sessions.update_one(
        {"user_id": user_id, "phone": phone},
        {"$set": {"last_active_at": datetime.utcnow()}}
    )


async def disconnect_session(user_id: int, phone: str = None):
    """Mark session as disconnected."""
    db = get_database()
    query = {"user_id": user_id}
    if phone:
        query["phone"] = phone
    
    await db.sessions.update_many(
        query,
        {"$set": {"connected": False}}
    )


async def is_account_active(user_id: int, phone: str) -> bool:
    """Check if a specific account is connected and session is valid."""
    session = await get_session(user_id, phone)
    return session is not None and session.get("connected", False)


async def mark_session_auth_failed(user_id: int, phone: str) -> int:
    """
    Record a failed authorization attempt.
    Returns the new total fail count so the caller can decide to disable.
    """
    db = get_database()
    await db.sessions.update_one(
        {"user_id": user_id, "phone": phone},
        {
            "$set": {"last_auth_fail": datetime.utcnow()},
            "$inc": {"auth_fail_count": 1},
        }
    )
    session = await get_session(user_id, phone)
    return session.get("auth_fail_count", 1) if session else 1


async def mark_session_disabled(user_id: int, phone: str, reason: str):
    """
    Permanently disable a dead/unauthorized session.
    The WorkerManager will skip it on all future sync cycles.
    """
    db = get_database()
    await db.sessions.update_one(
        {"user_id": user_id, "phone": phone},
        {"$set": {
            "connected": False,
            "worker_disabled": True,
            "disabled_reason": reason,
            "disabled_at": datetime.utcnow(),
        }}
    )


async def reset_session_auth_fails(user_id: int, phone: str):
    """Clear the auth-failure counter after a successful connection."""
    db = get_database()
    await db.sessions.update_one(
        {"user_id": user_id, "phone": phone},
        {"$set": {
            "auth_fail_count": 0,
            "last_auth_fail": None,
            "worker_disabled": False,
            "disabled_reason": None,
        }}
    )


# ==================== CONFIG ====================

async def get_user_config(user_id: int) -> Dict[str, Any]:
    """Get user config, creating default if not exists."""
    db = get_database()
    
    config = await db.config.find_one({"user_id": user_id})
    
    if not config:
        config = {
            "user_id": user_id,
            "interval_min": DEFAULT_INTERVAL_MINUTES,
            "last_saved_id": 0,
            "shuffle_mode": False,
            "copy_mode": False,
            "send_mode": "sequential",
            "auto_reply_enabled": False,
            "auto_reply_text": "🚀 Spinify Ads — 100% FREE!\n\n🤖 Unlimited Bots | Auto Reply & Leave ⚡ Auto Forwarding | Custom Delays\n\n🔥 Automate Your Telegram Ads!\n\n📩 Get Started — Check My Bio!",


            "updated_at": datetime.utcnow(),
        }
        await db.config.insert_one(config)
    
    return config


async def update_user_config(user_id: int, **kwargs) -> Dict[str, Any]:
    """Update user config."""
    db = get_database()
    
    kwargs["updated_at"] = datetime.utcnow()
    
    await db.config.update_one(
        {"user_id": user_id},
        {"$set": kwargs},
        upsert=True
    )
    
    return await get_user_config(user_id)


async def update_last_saved_id(user_id: int, last_saved_id: int):
    """Update last processed saved message ID."""
    db = get_database()
    await db.config.update_one(
        {"user_id": user_id},
        {"$set": {"last_saved_id": last_saved_id, "updated_at": datetime.utcnow()}},
        upsert=True
    )


async def update_current_msg_index(user_id: int, phone: str, index: int):
    """Update current message index for a specific account (phone)."""
    db = get_database()
    await db.sessions.update_one(
        {"user_id": user_id, "phone": phone},
        {"$set": {"current_msg_index": index, "updated_at": datetime.utcnow()}},
        upsert=True
    )


# ==================== GROUPS ====================

async def add_group(user_id: int, chat_id: int, chat_title: str, account_phone: str = None) -> bool:
    """Add a group linked to a specific account phone."""
    db = get_database()
    
    # Check group count for this specific account phone
    from config import MAX_GROUPS_PER_USER
    count = await get_group_count(user_id, phone=account_phone)
    if count >= MAX_GROUPS_PER_USER:
        return False
    
    query = {"user_id": user_id, "chat_id": chat_id}
    if account_phone:
        query["account_phone"] = account_phone

    group_doc = {
        "user_id": user_id,
        "chat_id": chat_id,
        "chat_title": chat_title,
        "account_phone": account_phone,
        "enabled": True,
        "added_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    await db.groups.update_one(
        query,
        {"$set": group_doc},
        upsert=True
    )
    
    return True

async def update_all_groups_status(user_id: int, enabled: bool, account_phone: str = None):
    """Enable or disable all groups for a user (optionally filtered by account)."""
    db = get_database()
    query = {"user_id": user_id}
    if account_phone:
        query["account_phone"] = account_phone
        
    await db.groups.update_many(
        query,
        {"$set": {"enabled": enabled, "updated_at": datetime.utcnow()}}
    )


async def get_user_groups(user_id: int, enabled_only: bool = False, phone: str = None) -> List[Dict[str, Any]]:
    """Get all groups for user (optionally filtered by account phone)."""
    db = get_database()
    
    query = {"user_id": user_id}
    if enabled_only:
        query["enabled"] = True
    if phone:
        query["account_phone"] = phone
    
    cursor = db.groups.find(query)
    return await cursor.to_list(length=None)


async def remove_group(user_id: int, chat_id: int):
    """Remove a group."""
    db = get_database()
    await db.groups.delete_one({"user_id": user_id, "chat_id": chat_id})


async def toggle_group(user_id: int, chat_id: int, enabled: bool, reason: str = None):
    """Enable or disable a group, optionally storing a reason."""
    db = get_database()
    update_data = {"enabled": enabled}
    if not enabled and reason:
        update_data["pause_reason"] = reason
        update_data["paused_at"] = datetime.utcnow()
    elif enabled:
        # Clear reason when re-enabling
        update_data["pause_reason"] = None
        update_data["paused_at"] = None

    await db.groups.update_one(
        {"user_id": user_id, "chat_id": chat_id},
        {"$set": update_data}
    )


async def get_group_count(user_id: int, phone: str = None) -> int:
    """Get count of user's groups (optionally per account phone)."""
    db = get_database()
    query = {"user_id": user_id}
    if phone:
        query["account_phone"] = phone
    return await db.groups.count_documents(query)


async def mark_group_failing(user_id: int, chat_id: int, reason: str):
    """Mark a group as failing. Sets first_fail_at only if not already set.
    
    Distinguishes between group-level and account-level failures.
    Account-level failures (403, PeerFlood) do NOT set first_fail_at,
    so the group won't be auto-removed when the account recovers.
    """
    from models.group import _is_group_level_failure
    db = get_database()
    now = datetime.utcnow()
    is_group_fail = _is_group_level_failure(reason)

    if is_group_fail:
        # Group-level failure: mark with first_fail_at for potential auto-removal
        await db.groups.update_one(
            {"user_id": user_id, "chat_id": chat_id, "first_fail_at": {"$exists": False}},
            {"$set": {"first_fail_at": now, "fail_reason": reason, "fail_type": "group", "enabled": False, "pause_reason": reason}},
        )
        # If already marked, just update reason
        await db.groups.update_one(
            {"user_id": user_id, "chat_id": chat_id, "first_fail_at": {"$exists": True}},
            {"$set": {"fail_reason": reason, "fail_type": "group", "enabled": False, "pause_reason": reason}},
        )
    else:
        # Account-level failure: disable but do NOT set first_fail_at
        await db.groups.update_one(
            {"user_id": user_id, "chat_id": chat_id},
            {"$set": {
                "enabled": False,
                "pause_reason": f"Account issue: {reason}",
                "fail_reason": reason,
                "fail_type": "account",
            }},
        )


async def clear_group_fail(user_id: int, chat_id: int):
    """Clear failing status after a successful send."""
    db = get_database()
    await db.groups.update_one(
        {"user_id": user_id, "chat_id": chat_id},
        {"$unset": {"first_fail_at": "", "fail_reason": "", "fail_type": ""},
         "$set": {"enabled": True, "pause_reason": None}},
    )


async def resume_account_paused_groups(user_id: int) -> int:
    """
    Re-enable all groups that were paused due to account-level failures.
    Called when the account starts successfully sending again.
    Returns count of groups re-enabled.
    """
    db = get_database()
    result = await db.groups.update_many(
        {
            "user_id": user_id,
            "fail_type": "account",
            "enabled": False,
        },
        {
            "$set": {"enabled": True, "pause_reason": None},
            "$unset": {"fail_reason": "", "fail_type": ""},
        }
    )
    return result.modified_count


async def remove_stale_failing_groups(user_id: int) -> int:
    """Remove groups failing for more than 24 hours.
    
    ONLY removes groups with group-level failures (dead/private/banned).
    Groups paused due to account-level restrictions (403) are preserved.
    """
    db = get_database()
    cutoff = datetime.utcnow() - timedelta(hours=24)
    result = await db.groups.delete_many({
        "user_id": user_id,
        "first_fail_at": {"$lte": cutoff},
        # Only delete group-level failures, NOT account-level ones
        "$or": [
            {"fail_type": "group"},
            {"fail_type": {"$exists": False}},  # Legacy entries without fail_type
        ]
    })
    return result.deleted_count


async def get_failing_groups_count() -> int:
    """Get total groups currently marked as failing (for admin stats)."""
    db = get_database()
    return await db.groups.count_documents({"first_fail_at": {"$exists": True}})


# ==================== PLANS ====================



from models.plan import (
    get_plan,
    is_plan_active,
    extend_plan,
    activate_plan,
    check_and_expire_all_plans,
    get_subscription_stats,
    query_subscriptions,
    reduce_plan,
    mark_plan_expired,
    delete_plan,
)



# ==================== REDEEM CODES ====================

async def generate_redeem_code(plan_type: str) -> str:
    """Generate a new redeem code."""
    db = get_database()
    
    code = secrets.token_urlsafe(12).upper()[:12]
    
    code_doc = {
        "code": code,
        "plan_type": plan_type,
        "duration_days": PLAN_DURATIONS.get(plan_type, 7),
        "created_at": datetime.utcnow(),
        "used_by": None,
        "used_at": None,
    }
    
    await db.redeem_codes.insert_one(code_doc)
    return code


async def redeem_code(user_id: int, code: str) -> tuple[bool, str]:
    """
    Redeem a code for user.
    Returns (success, message).
    """
    db = get_database()
    
    code_doc = await db.redeem_codes.find_one({"code": code.upper()})
    
    if not code_doc:
        return False, "❌ Invalid code."
    
    if code_doc.get("used_by"):
        return False, "❌ This code has already been used."
    
    # Apply plan
    await extend_plan(user_id, code_doc["duration_days"])
    
    # Mark code as used
    await db.redeem_codes.update_one(
        {"code": code.upper()},
        {"$set": {"used_by": user_id, "used_at": datetime.utcnow()}}
    )
    
    plan_type = code_doc["plan_type"]
    days = code_doc["duration_days"]
    
    return True, f"✅ Code redeemed! +{days} days ({plan_type}) added to your plan."


# ==================== SEND LOGS ====================

async def log_send(
    user_id: int,
    chat_id: int,
    saved_msg_id: int,
    status: str = "success",
    error: Optional[str] = None,
    phone: Optional[str] = None
):
    """Log a message send attempt and update account stats."""
    db = get_database()
    
    now = datetime.utcnow()
    log_doc = {
        "user_id": user_id,
        "phone": phone,
        "chat_id": chat_id,
        "saved_msg_id": saved_msg_id,
        "sent_at": now,
        "status": status,
        "error": error,
    }
    
    await db.send_logs.insert_one(log_doc)
    
    # Update session activity and success rate
    if phone:
        update_data = {"last_active_at": now}
        
        # Increment total/success counters in session for fast dashboard access
        inc_data = {"stats_total": 1}
        if status == "success":
            inc_data["stats_success"] = 1
            
        await db.sessions.update_one(
            {"user_id": user_id, "phone": phone},
            {"$set": update_data, "$inc": inc_data}
        )


async def get_account_stats(user_id: int, phone: str) -> Dict[str, Any]:
    """Get activity and success rate for a specific account."""
    db = get_database()
    session = await db.sessions.find_one({"user_id": user_id, "phone": phone})
    
    if not session:
        return {"success_rate": 0, "last_active": None, "total_sent": 0, "today_sent": 0}
    
    total = session.get("stats_total", 0)
    success = session.get("stats_success", 0)
    rate = (success / total * 100) if total > 0 else 0
    
    # Get 24h stats
    since_24h = datetime.utcnow() - timedelta(hours=24)
    today_total = await db.send_logs.count_documents({
        "user_id": user_id, 
        "phone": phone, 
        "sent_at": {"$gte": since_24h}
    })
    today_success = await db.send_logs.count_documents({
        "user_id": user_id, 
        "phone": phone, 
        "sent_at": {"$gte": since_24h},
        "status": "success"
    })
    
    return {
        "success_rate": round(rate, 1),
        "last_active": session.get("last_active_at"),
        "total_sent": total,
        "today_sent": today_total,
        "today_success": today_success,
        "today_rate": round((today_success / today_total * 100), 1) if today_total > 0 else 0
    }


async def get_multi_account_stats(user_id: int, phones: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch stats for multiple accounts in parallel using aggregation."""
    if not phones:
        return {}
        
    db = get_database()
    since_24h = datetime.utcnow() - timedelta(hours=24)
    
    # 1. Fetch sessions
    sessions = await db.sessions.find({"user_id": user_id, "phone": {"$in": phones}}).to_list(length=len(phones))
    session_map = {s["phone"]: s for s in sessions}
    
    # 2. Aggregate 24h logs for all requested phones
    pipeline = [
        {"$match": {
            "user_id": user_id,
            "phone": {"$in": phones},
            "sent_at": {"$gte": since_24h}
        }},
        {"$group": {
            "_id": "$phone",
            "today_total": {"$sum": 1},
            "today_success": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}}
        }}
    ]
    
    log_stats = await db.send_logs.aggregate(pipeline).to_list(length=len(phones))
    log_map = {item["_id"]: item for item in log_stats}
    
    results = {}
    for phone in phones:
        sess = session_map.get(phone, {})
        logs = log_map.get(phone, {"today_total": 0, "today_success": 0})
        
        total = sess.get("stats_total", 0)
        success = sess.get("stats_success", 0)
        rate = (success / total * 100) if total > 0 else 0
        
        today_total = logs["today_total"]
        today_success = logs["today_success"]
        today_rate = (today_success / today_total * 100) if today_total > 0 else 0
        
        results[phone] = {
            "success_rate": round(rate, 1),
            "last_active": sess.get("last_active_at"),
            "total_sent": total,
            "today_sent": today_total,
            "today_success": today_success,
            "today_rate": round(today_rate, 1)
        }
    
    return results

async def get_recent_failed_logs(user_id: int, phone: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Get recent failed send attempts for debugging."""
    db = get_database()
    cursor = db.send_logs.find({
        "user_id": user_id,
        "phone": phone,
        "status": {"$in": ["failed", "peer_flood", "flood_wait", "failing"]}
    }).sort("sent_at", -1).limit(limit)
    
    return await cursor.to_list(length=limit)



async def get_send_stats(hours: int = 24) -> Dict[str, int]:
    """Get send statistics for the last N hours categorized by type."""
    db = get_database()
    
    since = datetime.utcnow() - timedelta(hours=hours)
    
    # Successes
    success = await db.send_logs.count_documents({"sent_at": {"$gte": since}, "status": "success"})
    
    # Hard Failures (delivery errors)
    failed = await db.send_logs.count_documents({
        "sent_at": {"$gte": since}, 
        "status": {"$in": ["failed", "peer_flood", "flood_wait"]}
    })
    
    # Maintenance/Pool Cleanup (group specific removals)
    removed = await db.send_logs.count_documents({
        "sent_at": {"$gte": since}, 
        "status": {"$in": ["removed", "failing", "skipped"]}
    })
    
    total = success + failed + removed
    
    return {
        "total": total,
        "success": success,
        "failed": failed,
        "removed": removed
    }


# ==================== ADMIN STATS ====================

async def get_admin_stats() -> Dict[str, Any]:
    """Get overall statistics for admin panel."""
    db = get_database()
    
    total_users = await db.users.count_documents({})
    connected_sessions = await db.sessions.count_documents({"connected": True})
    
    # Plan stats
    now = datetime.utcnow()
    paid_active = await db.plans.count_documents({
        "status": "active",
        "expires_at": {"$gt": now}
    })
    
    # Filter expired plans: only show those expired within the last 7 days
    seven_days_ago = now - timedelta(days=7)
    expired = await db.plans.count_documents({
        "status": "expired",
        "expires_at": {"$gte": seven_days_ago}
    })
    
    # Send stats
    send_stats = await get_send_stats(24)
    
    # Group stats
    total_groups = await db.groups.count_documents({})
    groups_failing = await get_failing_groups_count()
    
    # Success Rate calculation: Corrected to only count Actionable attempts
    # Delivered / (Delivered + Hard Failures)
    success_sent = send_stats["success"]
    failed_sent = send_stats["failed"]
    total_for_rate = success_sent + failed_sent
    avg_success_rate = round((success_sent / total_for_rate * 100) if total_for_rate > 0 else 0, 1)
    
    return {
        "total_users": total_users,
        "connected_sessions": connected_sessions,
        "paid_active": paid_active,
        "expired": expired,
        "sends_24h": send_stats["total"],
        "success_24h": success_sent,
        "failed_24h": failed_sent,
        "total_groups": total_groups,
        "groups_failing": groups_failing,
        "groups_removed_24h": send_stats["removed"],
        "avg_success_rate": avg_success_rate,
    }


async def get_user_profile_data(user_id: int) -> Dict[str, Any]:
    """Fetch all user data for the Profile screen in one aggregated call."""
    db = get_database()
    
    user = await db.users.find_one({"user_id": user_id})
    plan = await get_plan(user_id)
    sessions = await get_all_user_sessions(user_id)
    config = await get_user_config(user_id)
    
    # Get all groups (across all accounts)
    all_groups = await get_user_groups(user_id)
    enabled_groups = [g for g in all_groups if g.get("enabled", True)]
    
    # Aggregate stats across all sessions
    total_sent = 0
    total_success = 0
    last_active = None
    for s in sessions:
        total_sent += s.get("stats_total", 0)
        total_success += s.get("stats_success", 0)
        sa = s.get("last_active_at")
        if sa and (last_active is None or sa > last_active):
            last_active = sa
    
    success_rate = round((total_success / total_sent * 100), 1) if total_sent > 0 else 0
    
    return {
        "user": user,
        "plan": plan,
        "sessions": sessions,
        "config": config,
        "groups": all_groups,
        "enabled_groups": len(enabled_groups),
        "total_groups": len(all_groups),
        "total_sent": total_sent,
        "total_success": total_success,
        "success_rate": success_rate,
        "last_active": last_active,
    }


async def get_all_users_for_broadcast(filter_type: str = "all") -> List[int]:
    """Get user IDs for broadcast."""
    db = get_database()
    now = datetime.utcnow()
    
    if filter_type == "all":
        cursor = db.users.find({}, {"user_id": 1})
    elif filter_type == "connected":
        cursor = db.sessions.find({"connected": True}, {"user_id": 1})
    elif filter_type == "paid":
        plan_cursor = db.plans.find({
            "status": "active",
            "expires_at": {"$gt": now}
        }, {"user_id": 1})
        return [p["user_id"] async for p in plan_cursor]
    else:
        cursor = db.users.find({}, {"user_id": 1})
    
    return [u["user_id"] async for u in cursor]
# ==================== GLOBAL SETTINGS ====================

async def get_global_settings() -> Dict[str, Any]:
    """Get global system settings, creating default if not exists."""
    db = get_database()
    settings = await db.settings.find_one({"key": "global"})
    
    if not settings:
        settings = {
            "key": "global",
            "night_mode_force": "auto",  # auto, on, off
            "updated_at": datetime.utcnow()
        }
        await db.settings.insert_one(settings)
    
    return settings


async def update_global_settings(**kwargs):
    """Update global system settings."""
    db = get_database()
    kwargs["updated_at"] = datetime.utcnow()
    await db.settings.update_one(
        {"key": "global"},
        {"$set": kwargs},
        upsert=True
    )
