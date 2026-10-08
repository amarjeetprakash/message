"""
Utility functions for the Worker service.
"""

from datetime import datetime
from typing import Optional
import pytz

from config import TIMEZONE, NIGHT_MODE_START_HOUR, NIGHT_MODE_END_HOUR

IST = pytz.timezone(TIMEZONE)


async def is_night_mode() -> bool:
    """
    Check if current time is within night mode hours.
    Night mode: 00:00 - 06:00 IST
    Overrides based on global settings:
    - force=on: always true
    - force=off: always false
    - force=auto: time-based
    """
    from db.models import get_global_settings
    settings = await get_global_settings()
    force = settings.get("night_mode_force", "auto")
    
    if force == "on":
        return True
    if force == "off":
        return False
        
    now_ist = datetime.now(IST)
    current_hour = now_ist.hour
    
    return NIGHT_MODE_START_HOUR <= current_hour < NIGHT_MODE_END_HOUR


def seconds_until_morning() -> int:
    """
    Calculate seconds until night mode ends (06:00 IST).
    """
    now_ist = datetime.now(IST)
    
    # Target is 06:00 today or tomorrow
    morning = now_ist.replace(
        hour=NIGHT_MODE_END_HOUR,
        minute=0,
        second=0,
        microsecond=0
    )
    
    if now_ist >= morning:
        # Already past 6 AM, this shouldn't happen during night mode
        # but calculate for tomorrow just in case
        from datetime import timedelta
        morning += timedelta(days=1)
    
    delta = morning - now_ist
    return int(delta.total_seconds())


def format_time_remaining(seconds: int) -> str:
    """Format seconds into human-readable string."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


import logging

class UserLogAdapter(logging.LoggerAdapter):
    """
    Adapter that adds user context to log messages.
    Usage:
        logger = UserLogAdapter(original_logger, {'user_id': 123, 'phone': '+1234'})
        logger.info("Message") -> "[User 123][+1234] Message"
    """
    def process(self, msg, kwargs):
        user_id = self.extra.get('user_id', 'Unknown')
        phone = self.extra.get('phone', 'Unknown')
        return f"[User {user_id}][{phone}] {msg}", kwargs


# ═══════════════════════════════════════════════════════
#  CENTRAL LOG CHANNEL — Styled Helpers
# ═══════════════════════════════════════════════════════

async def send_central_log(text: str):
    """Send an update log to the central LOG_CHANNEL_ID using MAIN_BOT_TOKEN."""
    from config import LOG_CHANNEL_ID
    if not LOG_CHANNEL_ID:
        logging.getLogger(__name__).debug("LOG_CHANNEL_ID is not set — skipping central log")
        return
    try:
        from config import MAIN_BOT_TOKEN
        if not MAIN_BOT_TOKEN:
            logging.getLogger(__name__).warning("MAIN_BOT_TOKEN is empty — cannot send to log channel")
            return
        from shared.bot_init import create_base_bot
        
        # Truncate messages over 4096 chars (Telegram limit)
        if len(text) > 4096:
            text = text[:4090] + "\n..."
            
        async with create_base_bot(token=MAIN_BOT_TOKEN) as bot:
            await bot.send_message(chat_id=LOG_CHANNEL_ID, text=text, parse_mode="HTML")
    except Exception as e:
        _logger = logging.getLogger(__name__)
        error_str = str(e)
        # Log full error details for debugging
        _logger.error(f"Failed to send central log to {LOG_CHANNEL_ID}: {error_str}")
        # If it's a parse error, retry without HTML formatting
        if "parse" in error_str.lower() or "can't" in error_str.lower():
            try:
                from config import MAIN_BOT_TOKEN
                from shared.bot_init import create_base_bot
                async with create_base_bot(token=MAIN_BOT_TOKEN) as bot:
                    await bot.send_message(chat_id=LOG_CHANNEL_ID, text=text)
                _logger.info("Retried without HTML parse_mode — success")
            except Exception as retry_e:
                _logger.error(f"Retry without parse_mode also failed: {retry_e}")
async def send_direct_user_message(user_id: int, text: str):
    """Send a private message to a user directly from the Main Bot using MAIN_BOT_TOKEN."""
    from config import MAIN_BOT_TOKEN
    if not MAIN_BOT_TOKEN:
        logging.getLogger(__name__).warning("MAIN_BOT_TOKEN is empty — cannot send direct message")
        return
    from shared.bot_init import create_base_bot
    async with create_base_bot(token=MAIN_BOT_TOKEN) as bot:
        await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")



def mask_phone(phone: str) -> str:
    """Mask a phone number for privacy: +91****82"""
    if not phone or len(phone) < 6:
        return "****"
    return f"{phone[:3]}****{phone[-2:]}"


def build_live_update(user_label: str, chat_title: str, action: str, index: int, total: int) -> str:
    """Build a styled live update message for a single successful send."""
    now_ist = datetime.now(IST)
    time_str = now_ist.strftime("%I:%M %p")
    progress_pct = int((index / total) * 100) if total > 0 else 0

    # Progress bar visual
    filled = progress_pct // 10
    bar = "█" * filled + "░" * (10 - filled)

    return (
        f"<b>┌─── 🟢 LIVE UPDATE ───</b>\n"
        f"<b>│</b>\n"
        f"<b>│</b> 👤 User:    <code>{user_label}</code>\n"
        f"<b>│</b> 📢 Target:  <code>{chat_title}</code>\n"
        f"<b>│</b> ⚡ Action:  {action}\n"
        f"<b>│</b> 📊 Progress: [{bar}] {progress_pct}%\n"
        f"<b>│</b> ⏱ Time:    {time_str} IST\n"
        f"<b>│</b>\n"
        f"<b>└─── {index}/{total} ───</b>"
    )


def build_cycle_report(user_label: str, success_groups: list, failed_groups: list, send_mode: str, interval: int, cycle_duration: float = 0, skipped: int = 0, sends_24h_success: int = None, sends_24h_total: int = None) -> str:
    """Build a premium styled cycle summary report — V6 with metrics."""
    now_ist = datetime.now(IST)
    time_str = now_ist.strftime("%d %b %Y • %I:%M %p IST")
    total = len(success_groups) + len(failed_groups)
    rate = int((len(success_groups) / total) * 100) if total > 0 else 0

    # Duration formatting
    if cycle_duration > 0:
        dur_min = int(cycle_duration // 60)
        dur_sec = int(cycle_duration % 60)
        dur_str = f"{dur_min}m {dur_sec}s" if dur_min > 0 else f"{dur_sec}s"
        throughput = f"{(len(success_groups) / (cycle_duration / 60)):.1f}" if cycle_duration > 0 else "N/A"
    else:
        dur_str = "N/A"
        throughput = "N/A"

    # Speed rating
    if cycle_duration > 0 and total > 0:
        avg_per_group = cycle_duration / total
        if avg_per_group < 30:
            speed_badge = "⚡ BLAZING"
        elif avg_per_group < 60:
            speed_badge = "🚀 FAST"
        elif avg_per_group < 120:
            speed_badge = "🟢 NORMAL"
        else:
            speed_badge = "🐢 SLOW"
    else:
        speed_badge = "—"

    # Header
    text = (
        f"<b>╔══════════════════════════╗</b>\n"
        f"<b>║   📊 CYCLE REPORT        ║</b>\n"
        f"<b>╚══════════════════════════╝</b>\n\n"
        f"👤 <b>User:</b> <code>{user_label}</code>\n"
        f"🕐 <b>Completed:</b> {time_str}\n"
        f"⚙️ <b>Mode:</b> {send_mode.title()} | <b>Interval:</b> {interval}m\n\n"
    )

    # Stats bar
    text += f"<b>━━━ DELIVERY STATS ━━━</b>\n"
    text += f"  ✅ Delivered:  <b>{len(success_groups)}</b>\n"
    text += f"  ❌ Failed:     <b>{len(failed_groups)}</b>\n"
    if skipped > 0:
        text += f"  ⏭ Skipped:    <b>{skipped}</b> (dedup)\n"
    text += f"  📈 Success:    <b>{rate}%</b>\n"
    if sends_24h_success is not None and sends_24h_total is not None:
        text += f"  ✉️ Sent (24h): <b>{sends_24h_success}/{sends_24h_total}</b> (Delivered/Attempted)\n"
    text += "\n"

    # V6: Performance metrics
    text += f"<b>━━━ PERFORMANCE ━━━</b>\n"
    text += f"  ⏱ Duration:   <b>{dur_str}</b>\n"
    text += f"  📊 Throughput: <b>{throughput} groups/min</b>\n"
    text += f"  {speed_badge}\n\n"

    # Success list
    if success_groups:
        unique_success = []
        for g in success_groups:
            if g not in unique_success:
                unique_success.append(g)
        text += f"<b>✅ SENT ({len(unique_success)}):</b>\n"
        for i, g in enumerate(unique_success[:15], 1):
            text += f"  {i}. {g}\n"
        if len(unique_success) > 15:
            text += f"  <i>...+{len(unique_success)-15} more groups</i>\n"
        text += "\n"

    # Failed list
    if failed_groups:
        unique_failed = []
        for g in failed_groups:
            if g not in unique_failed:
                unique_failed.append(g)
        text += f"<b>❌ FAILED ({len(unique_failed)}):</b>\n"
        for i, g in enumerate(unique_failed[:10], 1):
            text += f"  {i}. {g}\n"
        if len(unique_failed) > 10:
            text += f"  <i>...+{len(unique_failed)-10} more groups</i>\n"
        text += "\n"

    text += f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>"
    return text


def build_error_log(user_label: str, chat_title: str, error_type: str, detail: str = "") -> str:
    """Build a styled error/warning log entry."""
    now_ist = datetime.now(IST)
    time_str = now_ist.strftime("%I:%M %p")

    return (
        f"<b>⚠️ ALERT</b> | {time_str} IST\n"
        f"👤 <code>{user_label}</code>\n"
        f"📢 <code>{chat_title}</code>\n"
        f"🔴 {error_type}"
        + (f"\n📝 <i>{detail[:60]}</i>" if detail else "")
    )


def build_cleanup_log(user_label: str, removed_count: int, removed_titles: list = None) -> str:
    """Build a styled auto-cleanup log."""
    now_ist = datetime.now(IST)
    time_str = now_ist.strftime("%I:%M %p")

    text = (
        f"<b>🧹 AUTO-CLEANUP</b> | {time_str} IST\n"
        f"👤 <code>{user_label}</code>\n"
        f"🗑 Removed <b>{removed_count}</b> failing group(s)\n"
    )
    if removed_titles:
        for t in removed_titles[:5]:
            text += f"  • {t}\n"
        if len(removed_titles) > 5:
            text += f"  <i>...+{len(removed_titles)-5} more</i>\n"
    return text


def build_session_start_log(user_label: str, group_count: int, plan_status: str) -> str:
    """Build a styled session start notification."""
    now_ist = datetime.now(IST)
    time_str = now_ist.strftime("%I:%M %p IST")

    return (
        f"<b>🟢 SESSION STARTED</b> | {time_str}\n"
        f"👤 <code>{user_label}</code>\n"
        f"📂 Groups: <b>{group_count}</b>\n"
        f"💎 Plan: <b>{plan_status}</b>"
    )


async def send_central_log_return_id(text: str) -> Optional[int]:
    """Send an update log to the central LOG_CHANNEL_ID and return its message_id."""
    from config import LOG_CHANNEL_ID
    import logging
    if not LOG_CHANNEL_ID:
        logging.getLogger(__name__).debug("LOG_CHANNEL_ID is not set — skipping central log")
        return None
    try:
        from config import MAIN_BOT_TOKEN
        if not MAIN_BOT_TOKEN:
            logging.getLogger(__name__).warning("MAIN_BOT_TOKEN is empty — cannot send to log channel")
            return None
        from shared.bot_init import create_base_bot
        if len(text) > 4096:
            text = text[:4090] + "\n..."
        async with create_base_bot(token=MAIN_BOT_TOKEN) as bot:
            msg = await bot.send_message(chat_id=LOG_CHANNEL_ID, text=text, parse_mode="HTML")
            return msg.message_id
    except Exception as e:
        logging.getLogger(__name__).error(f"Failed to send log with ID: {e}")
        return None


async def edit_central_log(message_id: int, text: str) -> bool:
    """Edit a previously sent message in the central LOG_CHANNEL_ID."""
    from config import LOG_CHANNEL_ID
    import logging
    if not LOG_CHANNEL_ID or not message_id:
        return False
    try:
        from config import MAIN_BOT_TOKEN
        if not MAIN_BOT_TOKEN:
            logging.getLogger(__name__).warning("MAIN_BOT_TOKEN is empty — cannot send to log channel")
            return False
        from shared.bot_init import create_base_bot
        if len(text) > 4096:
            text = text[:4090] + "\n..."
        async with create_base_bot(token=MAIN_BOT_TOKEN) as bot:
            await bot.edit_message_text(chat_id=LOG_CHANNEL_ID, message_id=message_id, text=text, parse_mode="HTML")
            return True
    except Exception as e:
        logging.getLogger(__name__).error(f"Failed to edit central log {message_id}: {e}")
        return False


def build_progress_bar_report(user_label: str, send_mode: str, index: int, total: int, success_count: int, failed_count: int, skipped_count: int, current_chat_title: Optional[str] = None, completed: bool = False) -> str:
    """Builds a premium progress bar log report with dynamic animation."""
    progress_pct = int((index / total) * 100) if total > 0 else 0
    
    # Generate progress bar using emojis
    filled_blocks = progress_pct // 10
    empty_blocks = 10 - filled_blocks
    progress_bar = "🟩" * filled_blocks + "⬜" * empty_blocks
    
    status_header = "⏳ <b>[CYCLE PROGRESS] IN PROGRESS</b>" if not completed else "✅ <b>[CYCLE PROGRESS] COMPLETED</b>"
    
    now_ist = datetime.now(IST)
    time_str = now_ist.strftime("%I:%M %p IST")
    
    # Emojis for status
    mode_emoji = "🔄" if send_mode == "rotate" else "🔀" if send_mode == "random" else "➡️"
    
    text = (
        f"{status_header}\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"👤 <b>User:</b> <code>{user_label}</code>\n"
        f"⚙️ <b>Mode:</b> {mode_emoji} <code>{send_mode.upper()}</code>\n"
        f"📊 <b>Progress:</b> <code>{progress_bar}</code> <b>{progress_pct}%</b> ({index}/{total})\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"🟢 <b>Delivered:</b> <code>{success_count}</code>\n"
        f"🔴 <b>Failed:</b>    <code>{failed_count}</code>\n"
    )
    
    if skipped_count > 0:
        text += f"⏭️ <b>Skipped:</b>   <code>{skipped_count}</code>\n"
        
    text += f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
    
    if completed:
        text += f"🏁 <b>Status:</b> Cycle completed at {time_str}!"
    elif current_chat_title:
        text += f"📤 <b>Current:</b> Sending to <b>{current_chat_title}</b>..."
    else:
        text += f"⏳ <b>Status:</b> Initializing..."
        
    return text


from shared.utils import is_system_or_command_message, normalize_text_for_filter




