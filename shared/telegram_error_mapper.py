"""
Shared helper for mapping complex Telethon exceptions and RPC errors
into clean, user-friendly structures for logging and UI display.
"""

from telethon.errors import (
    FloodWaitError,
    PeerFloodError,
    ChatWriteForbiddenError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    UserBannedInChannelError,
    InputUserDeactivatedError,
    RPCError,
    ChannelInvalidError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
    InviteHashExpiredError,
)


def map_telegram_error(e: Exception) -> dict:
    """
    Normalizes all Telegram/Telethon errors into clean structured output.
    Returns a dict with:
        error_code: str
        display_message: str
        severity: str ('error', 'warning', 'critical')
        retryable: bool
    """
    error_str = str(e).upper()
    
    # 1. Flood Limits
    if isinstance(e, FloodWaitError):
        return {
            "error_code": "FLOOD_WAIT",
            "display_message": f"Rate limited by Telegram. Waiting {e.seconds}s.",
            "severity": "warning",
            "retryable": True
        }
    
    if isinstance(e, PeerFloodError):
        return {
            "error_code": "PEER_FLOOD",
            "display_message": "Account temporarily restricted by Telegram (PeerFlood).",
            "severity": "error",
            "retryable": False
        }

    # 2. Banned / Deactivated
    if isinstance(e, InputUserDeactivatedError) or "USER_DEACTIVATED" in error_str:
        return {
            "error_code": "ACCOUNT_DEACTIVATED",
            "display_message": "Account has been banned or deactivated by Telegram.",
            "severity": "critical",
            "retryable": False
        }

    # 3. Link / Chat Invalid
    if isinstance(e, (ChannelInvalidError, UsernameNotOccupiedError, UsernameInvalidError, InviteHashExpiredError)):
        return {
            "error_code": "LINK_INVALID",
            "display_message": "Group link is dead or invalid.",
            "severity": "error",
            "retryable": False
        }

    # 4. Permission / Restricted
    if isinstance(e, (ChatWriteForbiddenError, ChannelPrivateError, ChatAdminRequiredError, UserBannedInChannelError)):
        return {
            "error_code": "PERMISSION_DENIED",
            "display_message": "No permission to send messages here.",
            "severity": "warning",
            "retryable": False
        }

    # 5. Entity resolution errors (Telethon raises ValueError or RPCError when entity can't be resolved)
    if "COULD NOT FIND THE INPUT ENTITY" in error_str or "INPUT ENTITY NOT FOUND" in error_str:
        return {
            "error_code": "ENTITY_NOT_FOUND",
            "display_message": "Could not locate group. Try re-adding it.",
            "severity": "error",
            "retryable": False
        }

    # 6. RPCError string matching
    if isinstance(e, RPCError):
        if "MESSAGE_ID_INVALID" in error_str or "OPERATION ON SUCH MESSAGE" in error_str or "FILE_REFERENCE_EXPIRED" in error_str:
            return {
                "error_code": "MESSAGE_DELETED",
                "display_message": "Ad message was deleted or media file reference expired.",
                "severity": "error",
                "retryable": False
            }
        if "TOPIC_CLOSED" in error_str:
            return {
                "error_code": "TOPIC_CLOSED",
                "display_message": "Group topic is closed.",
                "severity": "warning",
                "retryable": False
            }
        if "JOIN DISCUSSION GROUP" in error_str or "DISCUSSION_GROUP" in error_str:
            return {
                "error_code": "DISCUSSION_GROUP_REQUIRED",
                "display_message": "Must join discussion group to comment.",
                "severity": "warning",
                "retryable": False
            }
        if "MESSAGE CANNOT BE EMPTY" in error_str or "MESSAGE_EMPTY" in error_str:
            return {
                "error_code": "EMPTY_MESSAGE",
                "display_message": "Attempted to send an empty message.",
                "severity": "error",
                "retryable": False
            }
        if "403" in error_str or "FORBIDDEN" in error_str:
            return {
                "error_code": "FORBIDDEN",
                "display_message": "Action forbidden (likely muted).",
                "severity": "warning",
                "retryable": False
            }
        if "SLOWMODE_WAIT" in error_str:
            return {
                "error_code": "SLOWMODE",
                "display_message": "Group is in slow mode.",
                "severity": "warning",
                "retryable": True
            }

        # Generic RPCError extraction
        error_code = error_str.split("(")[0].strip() if "(" in error_str else error_str[:30]
        return {
            "error_code": error_code or "RPC_ERROR",
            "display_message": f"Telegram Error: {error_code.replace('_', ' ').title()}",
            "severity": "warning",
            "retryable": False
        }

    # 6. Fallback Unknown Exception
    err_text = str(e)[:40] or "Unknown Error"
    return {
        "error_code": "UNKNOWN_ERROR",
        "display_message": f"Error: {err_text}",
        "severity": "error",
        "retryable": False
    }
