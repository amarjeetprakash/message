import os
import sys
import asyncio
import traceback
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Force UTF-8 encoding for stdout/stderr to support emojis on Windows terminals
if sys.platform.startswith("win"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

async def main():
    print("=" * 60)
    print("🤖 VPS DIAGNOSTIC SCRIPT")
    print("=" * 60)
    
    # 1. Check Python version
    print(f"Python version: {sys.version}")
    
    # 2. Check imports
    print("\n--- Checking Imports ---")
    try:
        from telegram import Bot
        from telegram.ext import Application
        from telegram.request import HTTPXRequest
        import telethon
        import motor
        import pymongo
        print("✅ All imports (telegram, telethon, motor, pymongo) are successful!")
    except Exception as e:
        print("❌ Import error occurred:")
        traceback.print_exc()
        return

    # 3. Check environment configuration
    print("\n--- Checking Config ---")
    token = os.getenv("MAIN_BOT_TOKEN", "")
    log_channel = os.getenv("LOG_CHANNEL_ID", "")
    mongo_uri = os.getenv("MONGODB_URI", "")
    
    print(f"MAIN_BOT_TOKEN: {token[:10]}...{token[-5:] if len(token) > 15 else ''} (Length: {len(token)})")
    print(f"LOG_CHANNEL_ID: {log_channel}")
    print(f"MONGODB_URI: {mongo_uri[:20]}... (Length: {len(mongo_uri)})")
    
    if not token:
        print("❌ MAIN_BOT_TOKEN is missing!")
    if not log_channel:
        print("❌ LOG_CHANNEL_ID is missing!")
    if not mongo_uri:
        print("❌ MONGODB_URI is missing!")
        
    # 4. Check Telegram Bot API Connection (Main Bot)
    print("\n--- Testing Main Bot API Connection ---")
    try:
        from shared.bot_init import create_base_bot
        bot = create_base_bot(token)
        me = await bot.get_me()
        print(f"✅ Main Bot Connection Successful!")
        print(f"   Username: @{me.username}")
        print(f"   ID: {me.id}")
    except Exception as e:
        print("❌ Main Bot connection failed:")
        if "Unauthorized" in str(e) or "InvalidToken" in type(e).__name__:
            print("   👉 Error: Telegram returned 401 Unauthorized. The MAIN_BOT_TOKEN is revoked or invalid.")
            print("   👉 Fix: Get a new token from @BotFather in Telegram and update MAIN_BOT_TOKEN in .env")
        else:
            traceback.print_exc()
        bot = None

    # 5. Check Telegram Bot API Connection (Login Bot)
    login_token = os.getenv("LOGIN_BOT_TOKEN", "")
    print("\n--- Testing Login Bot API Connection ---")
    if not login_token:
        print("❌ LOGIN_BOT_TOKEN is missing!")
    else:
        try:
            from shared.bot_init import create_base_bot
            login_bot = create_base_bot(login_token)
            login_me = await login_bot.get_me()
            print(f"✅ Login Bot Connection Successful!")
            print(f"   Username: @{login_me.username}")
            print(f"   ID: {login_me.id}")
        except Exception as e:
            print("❌ Login Bot connection failed:")
            if "Unauthorized" in str(e) or "InvalidToken" in type(e).__name__:
                print("   👉 Error: Telegram returned 401 Unauthorized. The LOGIN_BOT_TOKEN is revoked or invalid.")
                print("   👉 Fix: Get a new token from @BotFather in Telegram and update LOGIN_BOT_TOKEN in .env")
            else:
                traceback.print_exc()

    # 6. Check Log Channel Messaging
    if bot and log_channel:
        print("\n--- Testing Log Channel Messaging ---")
        try:
            chat_id = int(log_channel)
            print(f"Attempting to send test message to channel {chat_id}...")
            msg = await bot.send_message(
                chat_id=chat_id, 
                text="⚡ <b>VPS Connection Diagnostic Success!</b>\nLogs are sending correctly.",
                parse_mode="HTML"
            )
            print(f"✅ Log Channel Message Sent Successfully! Message ID: {msg.message_id}")
        except Exception as e:
            print("❌ Failed to send log to channel:")
            traceback.print_exc()
            print("\n💡 Tip: Make sure the bot is an ADMINISTRATOR in the channel with Post Messages permission.")

    # 7. Check MongoDB Connection
    print("\n--- Testing MongoDB Connection ---")
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        import certifi
        client = AsyncIOMotorClient(
            mongo_uri,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=5000
        )
        # Ping the server
        await client.admin.command('ping')
        print("✅ MongoDB Connection Successful!")
    except Exception as e:
        print("❌ MongoDB Connection Failed:")
        if "authentication failed" in str(e).lower():
            print("   👉 Error: MongoDB authentication failed (bad auth).")
            print("   👉 Fix: Check the username and password in MONGODB_URI in your .env file or MongoDB Atlas Database Access settings.")
        else:
            traceback.print_exc()

    # 8. Check Redis Connection
    redis_url = os.getenv("REDIS_URL", "")
    print(f"\n--- Testing Redis Connection ({redis_url[:30]}...) ---")
    if not redis_url:
        print("⚠️ REDIS_URL not set in .env (Redis caching will be bypassed).")
    else:
        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(redis_url, socket_timeout=5)
            await r.ping()
            await r.aclose()
            print("✅ Redis Connection Successful!")
        except Exception as e:
            print(f"⚠️ Redis Connection Failed: {e}")
            print("   👉 Note: The bot has a circuit breaker for Redis outages, but real-time caching is disabled until fixed.")

    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
