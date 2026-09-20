"""
Centralized configuration for the distributed Group Message Scheduler.

Loads all settings from environment variables via python-dotenv.
Every service (bot, scheduler, worker) imports from this module.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()


# ── Helpers ─────────────────────────────────────────────────────────────────

def _safe_int(value: str, default: int = 0) -> int:
    """Safely parse integer from string."""
    try:
        return int(value) if value else default
    except (ValueError, TypeError):
        return default


def _safe_float(value: str, default: float = 1.0) -> float:
    try:
        return float(value) if value else default
    except (ValueError, TypeError):
        return default


# ── Bot Tokens ──────────────────────────────────────────────────────────────

MAIN_BOT_TOKEN: str = os.getenv("MAIN_BOT_TOKEN", "")
LOGIN_BOT_TOKEN: str = os.getenv("LOGIN_BOT_TOKEN", "")

# ── Bot Usernames ───────────────────────────────────────────────────────────

_raw_main_bot = os.getenv("MAIN_BOT_USERNAME", "SpinifyAdsBot").strip()
if not _raw_main_bot or _raw_main_bot.lstrip("@").lower() in ["automessageschedulerbot", "philobots"]:
    MAIN_BOT_USERNAME: str = "SpinifyAdsBot"
else:
    MAIN_BOT_USERNAME: str = _raw_main_bot

LOGIN_BOT_USERNAME: str = os.getenv("LOGIN_BOT_USERNAME", "spinifyLoginbot")

# ── Owner / Admin ───────────────────────────────────────────────────────────

OWNER_ID: int = _safe_int(os.getenv("OWNER_ID", "8395808382"))

# ── MongoDB ─────────────────────────────────────────────────────────────────

MONGODB_URI: str = os.getenv(
    "MONGODB_URI",
    "mongodb://localhost:27017/spinify",
)
MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "spinify")

# ── Redis ───────────────────────────────────────────────────────────────────

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")

# ── Worker ──────────────────────────────────────────────────────────────────

WORKER_CONCURRENCY: int = _safe_int(os.getenv("WORKER_CONCURRENCY", "10"), 10)
SESSION_POOL_MAX_SIZE: int = _safe_int(os.getenv("SESSION_POOL_MAX_SIZE", "200"), 200)
SESSION_POOL_IDLE_TTL: int = _safe_int(os.getenv("SESSION_POOL_IDLE_TTL", "1800"), 1800)  # 30 min

# ── Scheduler ───────────────────────────────────────────────────────────────

SCHEDULER_POLL_INTERVAL: float = _safe_float(os.getenv("SCHEDULER_POLL_INTERVAL", "1.5"), 1.5)
DEAD_WORKER_THRESHOLD_SECONDS: int = _safe_int(os.getenv("DEAD_WORKER_THRESHOLD_SECONDS", "120"), 120)

# ── Job retry ───────────────────────────────────────────────────────────────

MAX_RETRY_COUNT: int = _safe_int(os.getenv("MAX_RETRY_COUNT", "5"), 5)
RETRY_BASE_DELAY_SECONDS: int = _safe_int(os.getenv("RETRY_BASE_DELAY_SECONDS", "30"), 30)

# ── Scheduling Rules ───────────────────────────────────────────────────────

GROUP_GAP_SECONDS: int = _safe_int(os.getenv("GROUP_GAP_SECONDS", "40"), 40)
MESSAGE_GAP_SECONDS: int = _safe_int(os.getenv("MESSAGE_GAP_SECONDS", "210"), 210)
MIN_INTERVAL_MINUTES: int = _safe_int(os.getenv("MIN_INTERVAL_MINUTES", "15"), 15)
DEFAULT_INTERVAL_MINUTES: int = _safe_int(os.getenv("DEFAULT_INTERVAL_MINUTES", "15"), 15)
MAX_GROUPS_PER_USER: int = _safe_int(os.getenv("MAX_GROUPS_PER_USER", "100"), 100)

# ── Rate-limit protection ──────────────────────────────────────────────────

SEND_DELAY_MIN: int = _safe_int(os.getenv("SEND_DELAY_MIN", "10"), 10)
SEND_DELAY_MAX: int = _safe_int(os.getenv("SEND_DELAY_MAX", "20"), 20)

# ── Night Mode (IST) ───────────────────────────────────────────────────────

NIGHT_MODE_START_HOUR: int = 0
NIGHT_MODE_END_HOUR: int = 6
TIMEZONE: str = "Asia/Kolkata"

# ── Plans ───────────────────────────────────────────────────────────────────

PLAN_PRICES: dict = {
    "trial": 49,
    "week": 99,
    "month": 399,
    "3month": 799,
    "6month": 1499,
    "1year": 2499
}

PLAN_DURATIONS: dict = {
    "trial": 3,
    "week": 7,
    "month": 30,
    "3month": 90,
    "6month": 180,
    "1year": 365
}

# ── Telegram MTProto Proxy Settings ──
TELEGRAM_PROXY_SERVER: str = os.getenv("TELEGRAM_PROXY_SERVER", "")
TELEGRAM_PROXY_PORT: int = _safe_int(os.getenv("TELEGRAM_PROXY_PORT", "0"))
TELEGRAM_PROXY_SECRET: str = os.getenv("TELEGRAM_PROXY_SECRET", "")


# ── Channel ─────────────────────────────────────────────────────────────────

_raw_channel = os.getenv("CHANNEL_USERNAME", "SpinifyAdsBot").strip()
if not _raw_channel or _raw_channel.lstrip("@").lower() in ["automessageschedulerbot", "philobots"]:
    CHANNEL_USERNAME: str = "SpinifyAdsBot"
else:
    CHANNEL_USERNAME: str = _raw_channel

DEFAULT_AUTO_JOIN_GROUP: str = os.getenv("DEFAULT_AUTO_JOIN_GROUP", "https://t.me/spinifychat")
DEFAULT_AUTO_JOIN_USERNAME: str = os.getenv("DEFAULT_AUTO_JOIN_USERNAME", "spinifychat")
DEFAULT_AD_MESSAGE: str = os.getenv("DEFAULT_AD_MESSAGE", "🚀 Spinify Ads — 100% FREE!\n\n🤖 Unlimited Bots | Auto Reply & Leave ⚡ Auto Forwarding | Custom Delays\n\n🔥 Automate Your Telegram Ads!\n\n📩 Get Started — Check My Bio!")





# ── Validation ──────────────────────────────────────────────────────────────

def validate_config(require_bots: bool = True, require_redis: bool = False):
    """
    Validate critical configuration on startup.

    Args:
        require_bots:  True when running a bot service (main/login).
        require_redis: True when running scheduler or worker.
    """
    missing: list[str] = []

    if require_bots:
        if not MAIN_BOT_TOKEN or "main_bot_token" in MAIN_BOT_TOKEN.lower():
            missing.append("MAIN_BOT_TOKEN")
        if not LOGIN_BOT_TOKEN or "login_bot_token" in LOGIN_BOT_TOKEN.lower():
            missing.append("LOGIN_BOT_TOKEN")

    if "username:password" in MONGODB_URI:
        missing.append("MONGODB_URI (looks like a placeholder)")

    if require_redis and REDIS_URL == "redis://localhost:6379":
        # Not necessarily missing, but warn
        import logging
        logging.getLogger(__name__).warning(
            "REDIS_URL is set to default localhost — ensure Redis is running."
        )

    if missing:
        print("\n" + "!" * 50)
        print(f"CRITICAL ERROR: Missing or placeholder config:\n{', '.join(missing)}")
        print("Please check your .env file.")
        print("!" * 50 + "\n")
        sys.exit(1)
