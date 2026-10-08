"""
Shared utility functions for Group Message Scheduler.
"""

import re

SMALL_CAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ',
    'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ',
    'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ',
    'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ',
    'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ',
    'z': 'ᴢ',
    'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ',
    'F': 'ꜰ', 'G': 'ɢ', 'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ',
    'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 'O': 'ᴏ',
    'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ',
    'U': 'ᴜ', 'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ',
    'Z': 'ᴢ'
}

def to_small_caps(text: str) -> str:
    """
    Convert text to Small Caps font format with the first letter of each word capitalized.
    E.g., "Hello world" -> "Hᴇʟʟᴏ Wᴏʀʟᴅ"
    Preserves URLs, Telegram handles (@username), emojis, and special symbols.
    """
    if not text:
        return text

    def convert_word(word: str) -> str:
        if word.startswith(("http://", "https://", "t.me/", "@")):
            return word

        res = []
        first_letter_done = False
        for char in word:
            if char.isalpha():
                if not first_letter_done:
                    res.append(char.upper())
                    first_letter_done = True
                else:
                    res.append(SMALL_CAPS_MAP.get(char, char))
            else:
                res.append(char)
        return "".join(res)

    lines = text.split("\n")
    converted_lines = []
    for line in lines:
        words = line.split(" ")
        converted_words = [convert_word(w) for w in words]
        converted_lines.append(" ".join(converted_words))

    return "\n".join(converted_lines)

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
⚠️ *Note:* Running in Free Mode requires keeping `Fʀᴇᴇ Aᴅs Bᴏᴛ Bʏ @SpinifyAdsBot • Pᴏᴡᴇʀᴇᴅ Bʏ @PhiloBots` in your bio, keeping assigned promo profile photo (PFP), remaining joined to @SpinifyAdsBot and @SpinifySupport, and uses a fixed 20-minute interval.

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

def make_progress_bar(current: int, total: int, length: int = 10) -> str:
    """Generate a clean visual ASCII progress bar: [████████░░] 80%"""
    if total <= 0:
        return f"[{'░' * length}] 0%"
    ratio = max(0.0, min(1.0, float(current) / float(total)))
    percent = int(ratio * 100)
    filled = int(round(length * ratio))
    filled = max(0, min(length, filled))
    bar = '█' * filled + '░' * (length - filled)
    return f"[{bar}] {percent}%"


REVERSE_SMALL_CAPS_MAP = {
    'ᴀ': 'a', 'ʙ': 'b', 'ᴄ': 'c', 'ᴅ': 'd', 'ᴇ': 'e',
    'ꜰ': 'f', 'ɢ': 'g', 'ʜ': 'h', 'ɪ': 'i', 'ᴊ': 'j',
    'ᴋ': 'k', 'ʟ': 'l', 'ᴍ': 'm', 'ɴ': 'n', 'ᴏ': 'o',
    'ᴘ': 'p', 'ǫ': 'q', 'ʀ': 'r', 'ꜱ': 's', 'ᴛ': 't',
    'ᴜ': 'u', 'ᴠ': 'v', 'ᴡ': 'w', 'x': 'x', 'ʏ': 'y',
    'ᴢ': 'z'
}

_REVERSE_SMALL_CAPS_TRANS = str.maketrans(REVERSE_SMALL_CAPS_MAP)

def normalize_text_for_filter(text: str) -> str:
    """Normalize text by mapping small-caps unicode characters back to standard ASCII lowercase with C-speed str.translate."""
    if not text:
        return ""
    return text.lower().translate(_REVERSE_SMALL_CAPS_TRANS)


def is_system_or_command_message(msg) -> bool:
    """
    Check if a message is a system notification, bot response, command, or report
    that must never be treated as an advertising message.
    """
    if not msg:
        return False
        
    # Service messages (e.g. pinned message, join/leave, call notifications)
    if hasattr(msg, 'action') and msg.action is not None:
        return True
        
    text = (getattr(msg, 'text', None) or "").strip()
    if not text:
        # If there is no text and no media, it's not a valid ad
        return not bool(getattr(msg, 'media', None))
        
    # Known status/command prefix characters & emojis
    SYSTEM_PREFIXES = (
        ".", "/", "!", "#",
        "✅", "🗑️", "🗑", "🧹", "⏳", "❌", "⚠️", "⚠",
        "📊", "🔴", "⚪", "●", "📋", "🔄", "📥", "💎",
        "📢", "🖼", "🖼️", "👥", "🟢", "🔎", "🔍", "🩺",
        "⚙️", "⚙", "🔒", "⛔", "ℹ️", "ℹ", "💡"
    )
    if text.startswith(SYSTEM_PREFIXES):
        return True
        
    # Check normalized text for known status/system phrases
    normalized = normalize_text_for_filter(text)
    
    SYSTEM_PHRASES = (
        "target groups cleaned",
        "purged unusable groups",
        "remaining active groups",
        "target groups export",
        "target groups list",
        "target groups -",
        "free version paused",
        "remain joined",
        "folder limit reached",
        "group health",
        "send check",
        "saved messages summary",
        "profile photo updated",
        "bulk profile photo update",
        "total wipe complete",
        "account health weak",
        "pong! worker is active",
        "cleared saved messages ads",
        "wiped target groups",
        "setting new ad in saved messages",
        "kurup ads",
        "spinify ads",
        "pfp pool empty",
        "access denied",
        "premium feature",
        "removing specified ad",
        "fetching saved messages details",
    )
    
    for phrase in SYSTEM_PHRASES:
        if phrase in normalized:
            return True
            
    return False


