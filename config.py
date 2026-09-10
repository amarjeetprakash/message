"""
Configuration module for Group Message Scheduler.
Loads all settings from environment variables.
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ============== Bot Tokens ==============
MAIN_BOT_TOKEN = os.getenv("MAIN_BOT_TOKEN", "")
LOGIN_BOT_TOKEN = os.getenv("LOGIN_BOT_TOKEN", "")

# ============== Bot Usernames ==============
_raw_main_bot = os.getenv("MAIN_BOT_USERNAME", "SpinifyAdsBot").strip()
if not _raw_main_bot or _raw_main_bot.lstrip("@").lower() in ["automessageschedulerbot", "philobots"]:
    MAIN_BOT_USERNAME = "SpinifyAdsBot"
else:
    MAIN_BOT_USERNAME = _raw_main_bot

LOGIN_BOT_USERNAME = os.getenv("LOGIN_BOT_USERNAME", "spinifyLoginbot")

def _safe_int(value: str, default: int = 0) -> int:
    """Safely parse integer from string."""
    try:
        if not value:
            return default
        return int(value)
    except (ValueError, TypeError):
        return default

# ============== Telegram API Credentials ==============
API_ID = _safe_int(os.getenv("API_ID", "27018379"), 27018379)
API_HASH = os.getenv("API_HASH", "2fcf836dc55474ea1d593db8bf0947ae").strip()

# ============== Owner/Admin ==============
OWNER_ID = _safe_int(os.getenv("OWNER_ID", "8395808382"))

# ============== MongoDB ==============
MONGODB_URI = os.getenv(
    "MONGODB_URI",
    "mongodb+srv://Spinify:xKtH3qsMhOnTH2Pd@spinifybot.bxjgzoh.mongodb.net/spinify?retryWrites=true&w=majority&appName=SpinifyBot"
)

# ============== Validation ==============
def validate_config():
    """Validate critical configuration on startup."""
    missing = []
    if not MAIN_BOT_TOKEN or "main_bot_token" in MAIN_BOT_TOKEN.lower(): 
        missing.append("MAIN_BOT_TOKEN")
    if not LOGIN_BOT_TOKEN or "login_bot_token" in LOGIN_BOT_TOKEN.lower(): 
        missing.append("LOGIN_BOT_TOKEN")
    
    # Check for placeholder MongoDB URI
    if "username:password" in MONGODB_URI:
        missing.append("MONGODB_URI (Current value looks like a placeholder)")
        
    if missing:
        import sys
        print("\n" + "!"*50)
        print(f"CRITICAL ERROR: Missing or placeholder configuration keys:\n{', '.join(missing)}")
        print("Please check your .env file.")
        print("!"*50 + "\n")
        sys.exit(1)

# Run validation
validate_config()

# ============== Channel ==============
_raw_channel = os.getenv("CHANNEL_USERNAME", "SpinifyAdsBot").strip()
if not _raw_channel or _raw_channel.lstrip("@").lower() in ["automessageschedulerbot", "philobots"]:
    CHANNEL_USERNAME = "SpinifyAdsBot"
else:
    CHANNEL_USERNAME = _raw_channel

PAYMENT_UPI_ID = os.getenv("PAYMENT_UPI_ID", "rain@slc")
SUPPORT_HANDLE = os.getenv("SUPPORT_HANDLE", "@spinify")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/spinify")
LOG_CHANNEL_ID = _safe_int(os.getenv("LOG_CHANNEL_ID", "0"))
LOG_CHANNEL_URL = os.getenv("LOG_CHANNEL_URL", "https://t.me/spinifylogs")

DEFAULT_AUTO_JOIN_GROUP = os.getenv("DEFAULT_AUTO_JOIN_GROUP", "https://t.me/spinifychat")
DEFAULT_AUTO_JOIN_USERNAME = os.getenv("DEFAULT_AUTO_JOIN_USERNAME", "spinifychat")
DEFAULT_AD_MESSAGE = os.getenv("DEFAULT_AD_MESSAGE", "I am Free Message Bot \n\nBy Using @SpinifyAdsBot")




# ============== Scheduling Rules ==============
MAX_GROUPS_PER_USER = _safe_int(os.getenv("MAX_GROUPS_PER_USER", "50"))
GROUP_GAP_SECONDS = _safe_int(os.getenv("GROUP_GAP_SECONDS", "40"))           # 40 seconds (Premium speed)
MESSAGE_GAP_SECONDS = _safe_int(os.getenv("MESSAGE_GAP_SECONDS", "210"))        # 3.5 minutes
MIN_INTERVAL_MINUTES = _safe_int(os.getenv("MIN_INTERVAL_MINUTES", "15"))       # Minimum user interval
DEFAULT_INTERVAL_MINUTES = _safe_int(os.getenv("DEFAULT_INTERVAL_MINUTES", "15"))   # Default interval

# ============== Night Mode (IST) ==============
NIGHT_MODE_START_HOUR = 0       # 00:00 IST
NIGHT_MODE_END_HOUR = 6         # 06:00 IST
TIMEZONE = "Asia/Kolkata"

# ============== Plans ==============
PLAN_PRICES = {
    "trial": 49,      # ₹49/3 days
    "week": 99,       # ₹99/week
    "month": 399,     # ₹399/month
    "3month": 799,    # ₹799/3 months
    "6month": 1499,   # ₹1499/6 months
    "1year": 2499,    # ₹2499/1 year
}

PLAN_DURATIONS = {
    "trial": 3,       # 3 days
    "week": 7,        # 7 days
    "month": 30,      # 30 days
    "3month": 90,     # 90 days
    "6month": 180,    # 180 days
    "1year": 365,     # 365 days
}

# ============== Telegram MTProto Proxy ==============
TELEGRAM_PROXY_SERVER = os.getenv("TELEGRAM_PROXY_SERVER", "")
TELEGRAM_PROXY_PORT = _safe_int(os.getenv("TELEGRAM_PROXY_PORT", "0"))
TELEGRAM_PROXY_SECRET = os.getenv("TELEGRAM_PROXY_SECRET", "")

