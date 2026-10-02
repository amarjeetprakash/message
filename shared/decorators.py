"""
Subscription check decorators for Group Message Scheduler.
"""

from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes

from core.config import OWNER_ID
from models.plan import is_plan_active
from main_bot.utils.keyboards import get_subscription_required_keyboard

def require_premium(func):
    """
    Decorator to restrict access to premium-only features.
    Allows access if user is the owner OR has an active plan.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        
        # 1. Bypass check for owner
        if user_id == OWNER_ID:
            return await func(update, context, *args, **kwargs)
            
        # 2. Check if plan is active
        active = await is_plan_active(user_id)
        if active:
            return await func(update, context, *args, **kwargs)
            
        # 3. Access denied - send subscription required message
        text = """
⚠️ *PREMIUM ACCESS REQUIRED* ⚠️

This feature is reserved for *Paid Subscribers* only. To continue using the bot, please purchase a plan or redeem a promo code.

💎 *Perks of Premium:*
• Unlimited group messaging
• 24/7 automated delivery
• Multi-account support
• Anti-flood protection

👇 *Choose an option below to unlock:*
"""
        # If it's a callback query
        if update.callback_query:
            await update.callback_query.answer("Subscription required!", show_alert=True)
            await update.callback_query.edit_message_text(
                text,
                parse_mode="Markdown",
                reply_markup=get_subscription_required_keyboard()
            )
        else:
            # If it's a command
            await update.message.reply_text(
                text,
                parse_mode="Markdown",
                reply_markup=get_subscription_required_keyboard()
            )
        return None
        
    return wrapper


async def get_missing_channels(bot, user_id: int) -> list[str]:
    """
    Check membership of user in required channels/chats.
    Returns a list of missing channels/chats.
    """
    if user_id == OWNER_ID:
        return []
        
    from config import CHANNEL_USERNAME
    
    required_targets = []
    if CHANNEL_USERNAME:
        channel = CHANNEL_USERNAME.strip()
        if not channel.startswith("@"):
            channel = f"@{channel}"
        required_targets.append(channel)
    required_targets.append("@SpinifySupport")
    
    missing_targets = []
    from telegram.error import BadRequest
    import logging
    logger = logging.getLogger(__name__)
    
    for target in required_targets:
        try:
            member = await bot.get_chat_member(chat_id=target, user_id=user_id)
            if member.status not in ["member", "creator", "administrator", "restricted"]:
                missing_targets.append(target)
        except BadRequest as e:
            err_msg = str(e).lower()
            if "user not found" in err_msg or "user_id_invalid" in err_msg:
                # The user is definitely not in the chat
                missing_targets.append(target)
            else:
                # Other BadRequest (e.g. Chat not found, Bot is not a member, etc.)
                # This is a configuration/permission issue with the bot itself.
                # Log warning but do NOT block the user.
                logger.warning(f"Could not verify membership in {target} (Bot issue/BadRequest): {e}")
        except Exception as e:
            # Other exceptions (NetworkError, RetryAfter, etc.)
            # Log warning but do NOT block the user.
            logger.warning(f"Could not verify membership in {target} due to unexpected error: {e}")
            
    return missing_targets


def require_channel_join(func):
    """
    Decorator to restrict access to users who have joined the official channel.
    Bypasses check for owner.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user:
            return await func(update, context, *args, **kwargs)
            
        user_id = user.id
        
        missing_targets = await get_missing_channels(context.bot, user_id)
        if not missing_targets:
            return await func(update, context, *args, **kwargs)
            
        # 2. Access denied - send join channel/chat prompt
        missing_mentions = ", ".join(missing_targets)
        text = f"""
⚠️ *MEMBERSHIP REQUIRED*

To use the **Spinify Ads Bot**, you must be a member of: {missing_mentions}.

📢 *Join to receive updates, guides, and support!*

👇 *Please join and click 'Joined ✅' below:*
"""
        from config import CHANNEL_USERNAME
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        buttons = []
        missing_lower = [t.lower() for t in missing_targets]
        if CHANNEL_USERNAME:
            channel_clean = CHANNEL_USERNAME.lstrip('@')
            if f"@{channel_clean}".lower() in missing_lower:
                buttons.append([InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{channel_clean}")])
        if "@spinifysupport" in missing_lower:
            buttons.append([InlineKeyboardButton("💬 Join Group", url="https://t.me/SpinifySupport")])
            
        if not buttons:
            channel_clean = CHANNEL_USERNAME.lstrip('@') if CHANNEL_USERNAME else "SpinifyAdsBot"
            buttons.append([InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{channel_clean}")])
            buttons.append([InlineKeyboardButton("💬 Join Group", url="https://t.me/SpinifySupport")])
            
        buttons.append([InlineKeyboardButton("Joined ✅", callback_data="check_channel_join")])
        keyboard = InlineKeyboardMarkup(buttons)
        
        if update.callback_query:
            await update.callback_query.answer("Membership required!", show_alert=True)
            await update.callback_query.edit_message_text(
                text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            await update.message.reply_text(
                text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        return None
        
    return wrapper

