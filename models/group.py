"""
Group model — CRUD for the groups collection.
"""

from datetime import datetime
from typing import Optional, List

from core.database import get_database
from core.config import MAX_GROUPS_PER_USER


async def add_group(
    user_id: int,
    chat_id: int,
    chat_title: str,
    account_phone: str = None,
    member_count: int = 0,
    topic_id: int = None,
) -> dict:
    """Add or update a group linked to a specific account phone."""
    db = get_database()
    now = datetime.utcnow()

    query = {"user_id": user_id, "chat_id": chat_id}
    if account_phone:
        query["account_phone"] = account_phone

    result = await db.groups.find_one_and_update(
        query,
        {
            "$set": {
                "chat_title": chat_title,
                "enabled": True,
                "updated_at": now,
                "account_phone": account_phone,
                "member_count": member_count,
                "topic_id": topic_id,
            },
            "$setOnInsert": {
                "user_id": user_id,
                "chat_id": chat_id,
                "created_at": now,
            },
        },
        upsert=True,
        return_document=True,
    )
    return result


async def ensure_default_group(user_id: int, phone: str = None, chat_id: int = None, chat_title: str = "Spinify Chat"):
    """Ensure the default group (spinifychat) is present in the user's group list."""
    db = get_database()
    query = {"user_id": user_id}
    if chat_id:
        query["$or"] = [
            {"chat_id": chat_id},
            {"chat_title": {"$regex": "spinifychat", "$options": "i"}}
        ]
    else:
        query["chat_title"] = {"$regex": "spinifychat", "$options": "i"}

    existing = await db.groups.find_one(query)
    if not existing and chat_id:
        await add_group(
            user_id=user_id,
            chat_id=chat_id,
            chat_title=chat_title or "Spinify Chat",
            account_phone=phone,
        )
        return True
    elif existing and phone and not existing.get("account_phone"):
        await db.groups.update_one({"_id": existing["_id"]}, {"$set": {"account_phone": phone}})
    return False



async def get_user_groups(
    user_id: int,
    enabled_only: bool = False,
    phone: str = None,
) -> List[dict]:
    """Get all groups for user (optionally filtered)."""
    db = get_database()
    query: dict = {"user_id": user_id}
    if enabled_only:
        query["enabled"] = True
    if phone:
        query["account_phone"] = phone
    cursor = db.groups.find(query)
    return await cursor.to_list(length=1000)


async def remove_group(user_id: int, chat_id: int):
    """Remove a group."""
    db = get_database()
    await db.groups.delete_one({"user_id": user_id, "chat_id": chat_id})


async def toggle_group(
    user_id: int,
    chat_id: int,
    enabled: bool,
    reason: str = None,
):
    """Enable or disable a group."""
    db = get_database()
    update: dict = {
        "enabled": enabled,
        "updated_at": datetime.utcnow(),
    }
    if reason:
        update["pause_reason"] = reason
    elif enabled:
        update["pause_reason"] = None

    await db.groups.update_one(
        {"user_id": user_id, "chat_id": chat_id},
        {"$set": update},
    )


async def get_group_count(user_id: int, phone: str = None) -> int:
    """Get count of user's groups (optionally filtered by account phone)."""
    db = get_database()
    query = {"user_id": user_id}
    if phone:
        query["account_phone"] = phone
    return await db.groups.count_documents(query)


# Failure reasons that indicate the GROUP itself is dead/inaccessible
# (not just the account being temporarily restricted)
GROUP_LEVEL_FAIL_REASONS = [
    "ChatWriteForbiddenError", "ChannelPrivateError", "ChatAdminRequiredError",
    "UserBannedInChannelError", "ChannelInvalidError", "UsernameNotOccupiedError",
    "UsernameInvalidError", "InviteHashExpiredError",
    "Pre-check:", "Entity error:", "Entity Not Found",
    "RPC: CHAT_ADMIN_REQUIRED", "RPC: CHAT_WRITE_FORBIDDEN",
    "RPC: USER_BANNED_IN_CHANNEL", "RPC: CHANNEL_INVALID",
    "RPC: USERNAME_NOT_OCCUPIED", "RPC: USERNAME_INVALID",
    "RPC: INVITE_HASH_EXPIRED", "LINK_INVALID", "PERMISSION_DENIED",
]

# Account-level / temporary / content / system failures — group is fine, but the sending account is restricted or there is a temporary issue
ACCOUNT_LEVEL_FAIL_KEYWORDS = [
    "403", "FORBIDDEN", "AUTH_KEY", "PeerFlood", "RPCError 403",
    "Frozen", "FROZEN_PARTICIPANT_MISSING", "Account Frozen",
    "Connection", "Timeout", "Socket", "Empty", "deleted", "deleted from Saved Messages",
    "MESSAGE_DELETED", "EMPTY_MESSAGE", "SLOWMODE", "TOPIC_CLOSED", "DISCUSSION_GROUP_REQUIRED",
    "ServerError", "RPCError 500", "500", "Slow mode", "Muted", "Flood", "FloodWait", "Rate limited"
]


def _is_group_level_failure(reason: str) -> bool:
    """Check if the failure reason indicates a group-level issue (not account-level)."""
    if not reason:
        return False
    for keyword in GROUP_LEVEL_FAIL_REASONS:
        if keyword in reason:
            return True
    # If it matches account-level keywords, it's NOT group-level
    for keyword in ACCOUNT_LEVEL_FAIL_KEYWORDS:
        if keyword in reason:
            return False
    # Unknown/temporary/network/system errors are treated as temporary/account-level by default
    # to prevent auto-removing users' groups.
    return False


async def mark_group_failing(user_id: int, chat_id: int, reason: str):
    """
    Mark a group as failing. Sets `first_fail_at` only if not already set,
    so we track how long it has been failing continuously.
    
    Groups are disabled but NOT marked for auto-removal if the failure
    is account-level (e.g. 403 restriction on the sending account).
    """
    db = get_database()
    is_group_fail = _is_group_level_failure(reason)

    if is_group_fail:
        # Group-level failure: mark with first_fail_at for potential auto-removal
        await db.groups.update_one(
            {"user_id": user_id, "chat_id": chat_id, "first_fail_at": {"$exists": False}},
            {"$set": {
                "first_fail_at": datetime.utcnow(),
                "fail_reason": reason,
                "fail_type": "group",
                "enabled": False,
                "pause_reason": reason,
            }},
        )
        # If already marked, just update reason
        await db.groups.update_one(
            {"user_id": user_id, "chat_id": chat_id, "first_fail_at": {"$exists": True}},
            {"$set": {"fail_reason": reason, "fail_type": "group", "enabled": False, "pause_reason": reason}},
        )
    else:
        # Account-level failure: disable the group but do NOT set first_fail_at
        # so it won't be auto-removed. It will recover when the account recovers.
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
    """
    Remove groups that have been failing for more than 24 hours.
    ONLY removes groups with group-level failures (dead/private/banned groups).
    Groups paused due to account-level restrictions are preserved.
    Returns count removed.
    """
    from datetime import timedelta
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
    """Get total number of groups currently marked as failing (admin stat)."""
    db = get_database()
    return await db.groups.count_documents({"first_fail_at": {"$exists": True}})


async def resume_user_groups(user_id: int) -> int:
    """Re-enable paused groups for a user, respecting max group limit."""
    from core.config import MAX_GROUPS_PER_USER
    db = get_database()
    
    limit = MAX_GROUPS_PER_USER
    
    # Get current active/enabled count
    enabled_count = await db.groups.count_documents({"user_id": user_id, "enabled": True})
    if enabled_count >= limit:
        return 0
        
    slots_left = limit - enabled_count
    
    # Find paused groups
    cursor = db.groups.find({"user_id": user_id, "enabled": False}).sort("created_at", 1)
    paused_groups = await cursor.to_list(length=None)
    
    if not paused_groups or slots_left <= 0:
        return 0
        
    groups_to_enable = paused_groups[:slots_left]
    enable_ids = [g["chat_id"] for g in groups_to_enable]
    
    result = await db.groups.update_many(
        {"user_id": user_id, "chat_id": {"$in": enable_ids}},
        {
            "$set": {"enabled": True, "pause_reason": None},
            "$unset": {"first_fail_at": "", "fail_reason": ""}
        }
    )
    return result.modified_count
    
async def pause_user_groups(user_id: int) -> int:
    """Disable all active groups for a user. Returns count updated."""
    db = get_database()
    result = await db.groups.update_many(
        {"user_id": user_id, "enabled": True},
        {"$set": {"enabled": False, "pause_reason": "Global Pause (Command)"}}
    )
    return result.modified_count

async def clear_user_groups(user_id: int) -> int:
    """Delete and remove ALL groups for a user. Returns count removed."""
    db = get_database()
    result = await db.groups.delete_many({"user_id": user_id})
    return result.deleted_count

async def enforce_user_group_limit(user_id: int) -> int:
    """
    Enforce group limits on database level based on MAX_GROUPS_PER_USER.
    """
    from core.config import MAX_GROUPS_PER_USER
    db = get_database()
    
    limit = MAX_GROUPS_PER_USER
    
    # Get all enabled groups for this user
    cursor = db.groups.find({"user_id": user_id, "enabled": True}).sort("created_at", 1)
    enabled_groups = await cursor.to_list(length=None)
    
    if len(enabled_groups) > limit:
        # Keep oldest groups up to limit, disable the rest
        groups_to_disable = enabled_groups[limit:]
        disable_ids = [g["chat_id"] for g in groups_to_disable]
        
        await db.groups.update_many(
            {"user_id": user_id, "chat_id": {"$in": disable_ids}},
            {
                "$set": {
                    "enabled": False,
                    "pause_reason": f"Maximum limit exceeded — limited to {limit} groups."
                }
            }
        )
        return len(groups_to_disable)
    return 0
