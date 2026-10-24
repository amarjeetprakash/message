"""
Centralized Telegram Application initialization logic.
"""

import logging
import signal
import asyncio
from telegram import Bot
from telegram.ext import Application
from telegram.request import HTTPXRequest
from db.database import init_database

# Configure logging
def setup_logging():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    import warnings
    from telegram.warnings import PTBUserWarning
    warnings.filterwarnings("ignore", category=PTBUserWarning)
    return logging.getLogger("bot")

from telegram.error import InvalidToken

def create_base_application(token: str) -> Application:
    """Create and configure the bot application with standard timeouts and disabled SSL verification."""
    if not token:
        raise ValueError("Bot token is not set")

    # Hardened timeouts for stability and disabled SSL verification
    request = HTTPXRequest(
        connect_timeout=30,
        read_timeout=60,
        write_timeout=60,
        pool_timeout=60,
        httpx_kwargs={"verify": False},
    )

    return (
        Application.builder()
        .token(token)
        .request(request)
        .build()
    )

def create_base_bot(token: str) -> Bot:
    """Create a configured Bot instance with standard timeouts and disabled SSL verification."""
    request = HTTPXRequest(
        connect_timeout=30,
        read_timeout=60,
        write_timeout=60,
        pool_timeout=60,
        httpx_kwargs={"verify": False},
    )
    return Bot(token=token, request=request)

async def run_bot_gracefully(application: Application, bot_name: str):
    """Run the bot with graceful shutdown support."""
    logger = logging.getLogger(bot_name)
    
    # Initialize database
    try:
        await init_database()
    except Exception as db_err:
        logger.error(f"❌ Database initialization failed: {db_err}")
        logger.error("👉 Please verify your MONGODB_URI credentials in .env")
        return

    # Setup stop event for graceful shutdown
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: stop_event.set())
        except NotImplementedError:
            pass

    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        
        logger.info(f"{bot_name} is running (Async Polling)...")
        
        # Send starting log to central logs channel
        try:
            from config import LOG_CHANNEL_ID, MAIN_BOT_TOKEN
            from datetime import datetime
            
            if LOG_CHANNEL_ID and MAIN_BOT_TOKEN:
                msg = (
                    f"<b>🤖 {bot_name.upper()} STARTED</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚡ <b>Status:</b> Online & Ready\n"
                    f"📅 <b>Time:</b> <code>{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</code>"
                )
                async with create_base_bot(token=MAIN_BOT_TOKEN) as bot:
                    await bot.send_message(chat_id=LOG_CHANNEL_ID, text=msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to send {bot_name} startup log: {e}")
        
        # Wait for shutdown signal
        await stop_event.wait()
        logger.info(f"{bot_name} shutdown signal received...")
    except InvalidToken:
        logger.error(f"❌ Fatal: Invalid or revoked bot token for {bot_name}.")
        logger.error("👉 Please update your bot token in .env with a valid token from @BotFather in Telegram.")
    except Exception as e:
        logger.error(f"❌ Fatal error while running {bot_name}: {e}")
    finally:
        try:
            if application.updater and application.updater.running:
                await application.updater.stop()
        except Exception:
            pass
        try:
            if application.running:
                await application.stop()
        except Exception:
            pass
        try:
            if getattr(application, "_initialized", False):
                await application.shutdown()
        except Exception:
            pass
        logger.info(f"{bot_name} stopped cleanly.")
