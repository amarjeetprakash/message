"""
Login Bot entry point for Group Message Scheduler.
"""

import logging
import asyncio
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    TypeHandler,
)
from telegram import Update
from models.user import update_user_profile

from config import LOGIN_BOT_TOKEN
from shared.bot_init import setup_logging, create_base_application, run_bot_gracefully
from shared.decorators import require_premium

# Import handlers
from login_bot.handlers.start import start_handler
from login_bot.handlers.phone import (
    add_account_callback,
    receive_phone_number,
    receive_api_id,
    receive_api_hash,
    edit_phone_callback,
    cancel_callback,
)
from login_bot.handlers.otp import (
    send_otp_callback,
    resend_otp_callback,
    otp_keypad_callback,
    receive_otp_text,
)
from login_bot.handlers.twofa import receive_2fa_password
from login_bot.handlers.manage import (
    manage_accounts_callback,
    manage_acc_details_callback,
    disconnect_acc_callback,
    confirm_disconnect_acc_callback,
    login_home_callback
)

# Configure logging
logger = setup_logging()

def create_application() -> Application:
    """Create and configure the bot application."""
    application = create_base_application(LOGIN_BOT_TOKEN)

    # ============== Global Middleware ==============


    async def global_profile_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if hasattr(update, 'effective_user') and update.effective_user and not update.effective_user.is_bot:
            user = update.effective_user
            try:
                await update_user_profile(user.id, user.username, user.first_name, user.last_name)
            except Exception as e:
                pass

    # Run on all updates in a separate group so it doesn't block other handlers

    application.add_handler(TypeHandler(Update, global_profile_capture), group=-1)

    # ============== Command Handlers ==============
    # /start is always allowed
    application.add_handler(CommandHandler("start", start_handler))

    # ============== Callback Query Handlers ==============
    # Protected functional callbacks
    patterns = [
        ("^add_account$", add_account_callback),
        ("^edit_phone$", edit_phone_callback),
        ("^cancel$", cancel_callback), # Cancel should be allowed
        ("^send_otp$", send_otp_callback),
        ("^resend_otp$", resend_otp_callback),
        ("^otp:", otp_keypad_callback),
        ("^manage_accounts$", manage_accounts_callback),
        ("^manage_acc:", manage_acc_details_callback),
        ("^disconnect_acc:", disconnect_acc_callback),
        ("^confirm_disc_acc:", confirm_disconnect_acc_callback),
        ("^login_home$", login_home_callback),
    ]
    
    for pattern, callback in patterns:
        application.add_handler(CallbackQueryHandler(callback, pattern=pattern))

    # ============== Message Handlers ==============
    async def handle_text_message(update, context):
        """Route text messages based on state."""
        if not update.message or not update.message.text:
            return

        state = context.user_data.get("state")

        if state == "waiting_api_id":
            await receive_api_id(update, context)
        elif state == "waiting_api_hash":
            await receive_api_hash(update, context)
        elif state == "waiting_phone":
            await receive_phone_number(update, context)
        elif state == "waiting_otp":
            await receive_otp_text(update, context)
        elif state == "waiting_2fa":
            await receive_2fa_password(update, context)

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    return application

async def main():
    """Start the bot."""
    print("=" * 50)
    print("Spinify Ads Bot - Login Bot V3.3")
    print("=" * 50)

    application = create_application()
    await run_bot_gracefully(application, "Login Bot")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
