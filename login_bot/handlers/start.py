"""
Start and welcome handler for Login Bot.
"""

from telegram import Update
from telegram.ext import ContextTypes

from db.models import create_user
from login_bot.utils.keyboards import get_login_welcome_keyboard
from shared.utils import escape_markdown


WELCOME_TEXT = """
🔐 *SECURE LOGIN PORTAL*

To connect your account, follow 2 easy steps:
1️⃣ Enter your **Phone Number**
2️⃣ Enter the **OTP code** sent to your Telegram

👇 *Tap below to start the connection process*
"""


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command - shows welcome and account status."""
    user = update.effective_user
    user_id = user.id
    
    # 1. Enforce channel and chat join check first
    from shared.decorators import get_missing_channels
    missing_targets = await get_missing_channels(context.bot, user_id)
        
    if missing_targets:
        missing_mentions = ", ".join(missing_targets)
        text = f"""
⚠️ *MEMBERSHIP REQUIRED*

To connect your accounts, you must be a member of: {missing_mentions}.

📢 *Join to receive updates, guides, and support!*

👇 *Please join and send /start again:*
"""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from config import CHANNEL_USERNAME
        buttons = []
        missing_lower = [t.lower() for t in missing_targets]
        if CHANNEL_USERNAME:
            channel_clean = CHANNEL_USERNAME.lstrip('@')
            if f"@{channel_clean}".lower() in missing_lower:
                buttons.append([InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{channel_clean}")])
        if "@spinifychat" in missing_lower:
            buttons.append([InlineKeyboardButton("💬 Join Chat", url="https://t.me/spinifychat")])
            
        if not buttons:
            channel_clean = CHANNEL_USERNAME.lstrip('@') if CHANNEL_USERNAME else "SpinifyAdsBot"
            buttons.append([InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{channel_clean}")])
            buttons.append([InlineKeyboardButton("💬 Join Chat", url="https://t.me/spinifychat")])
            
        keyboard = InlineKeyboardMarkup(buttons)
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return
            
    # Ensure user exists in db
    await create_user(user_id)
    
    # Get account stats
    from db.models import get_all_user_sessions
    accounts = await get_all_user_sessions(user_id)
    acc_count = len(accounts)
    
    first_name = user.first_name or "User"
    greeting = f"👋 *Greeting {escape_markdown(first_name)},*\n\n"
    
    # Dynamic status line
    if acc_count > 0:
        status_line = f"📱 *STATUS:* You have `{acc_count}` account(s) connected.\n\n"
    else:
        status_line = "⚪ *STATUS:* No accounts connected yet.\n\n"
    
    await update.message.reply_text(
        greeting + status_line + WELCOME_TEXT,
        parse_mode="Markdown",
        reply_markup=get_login_welcome_keyboard(),
    )

