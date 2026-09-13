"""
Shared utility functions for Group Message Scheduler.
"""

import re

def escape_markdown(text: str) -> str:
    """
    Escape markdown characters for Telegram's legacy Markdown parser.
    Escapes: _, *, [, ]
    """
    if not text:
        return ""
    # We only escape characters that are used in our templates or could be accidentally typed by users.
    # Legacy Markdown (V1) is tricky. V2 is more strict but V1 is what's being used here.
    return re.sub(r'([_*\[\]])', r'\\\1', str(text))

def build_connection_success_text(phone: str, plan: dict) -> str:
    """
    Build standardized success message after account connection.
    Used by both OTP and 2FA flows.
    """
    from datetime import datetime
    
    if plan and plan.get("status") == "active" and plan.get("expires_at", datetime.min) > datetime.utcnow():
        plan_type = escape_markdown(plan.get("plan_type", "premium").replace("_", " ").upper())
        expires_at = plan["expires_at"]
        days_left = (expires_at - datetime.utcnow()).days
        hours_left = (expires_at - datetime.utcnow()).seconds // 3600

        time_left = f"{days_left}d {hours_left}h" if days_left > 0 else f"{hours_left}h"
        return f"""
✅ *Connected Successfully!*

📱 `{phone}` is now linked to your account.

💎 *Plan:* {plan_type} Premium
⏳ *Remaining:* {time_left}

🚀 Your premium plan is active. Open the dashboard to configure groups and intervals.
"""
    else:
        # Free Plan Tier
        return f"""
✅ *Connected Successfully!*

📱 `{phone}` is now linked to your account.

⚪ *Plan:* Free User (Free Mode Active)
⚠️ *Note:* Running in Free Mode requires keeping `Fʀᴇᴇ Aᴅs Bᴏᴛ Bʏ @SpinifyAdsBot • Pᴏᴡᴇʀᴇᴅ Bʏ @PhiloBots` in your bio, keeping assigned promo profile photo (PFP), remaining joined to @SpinifyAdsBot and @spinifychat, and uses a fixed 20-minute interval.

🚀 Open the dashboard to configure target groups!
"""
async def safe_reply(update, text: str, reply_markup=None, parse_mode="Markdown"):
    """
    Safely send or edit a message, handling common Telegram errors
    like 'Message is not modified' or 'User blocked the bot'.
    """
    from telegram.error import BadRequest, Forbidden
    
    try:
        # 1. Try to edit if it's a callback
        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                )
                return
            except BadRequest as e:
                # Common harmless error when refreshing dashboard with same data
                if "Message is not modified" in str(e):
                    return
                # If editing fails for other reasons (e.g. message too old), try sending new
                pass
        
        # 2. Send new message as final fallback
        await update.effective_chat.send_message(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except Forbidden:
        pass
    except Exception as e:
        # Check again in the outer catch for "not modified" just in case
        if "Message is not modified" not in str(e):
            import logging
            logging.getLogger(__name__).error(f"safe_reply failed: {e}")

def get_telegram_client_kwargs() -> dict:
    """
    Get additional kwargs for TelegramClient initialization (e.g., MTProto Proxy, socket resilience).
    """
    from telethon import connection
    from config import TELEGRAM_PROXY_SERVER, TELEGRAM_PROXY_PORT, TELEGRAM_PROXY_SECRET
    
    kwargs = {
        "connection_retries": 5,
        "retry_delay": 3,
        "auto_reconnect": True,
        "sequential_updates": False,
    }
    if TELEGRAM_PROXY_SERVER and TELEGRAM_PROXY_PORT:
        kwargs["proxy"] = (TELEGRAM_PROXY_SERVER, TELEGRAM_PROXY_PORT, TELEGRAM_PROXY_SECRET)
        kwargs["connection"] = connection.ConnectionTcpMTProxyRandomizedIntermediate
    return kwargs

