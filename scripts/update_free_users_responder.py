import asyncio
import os
import sys

# Add project root to path so we can import local modules
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from core.database import get_database
from models.plan import is_plan_active
from models.user import update_user_config

DEFAULT_RESPONDER_TEXT = "I am Free Message Bot \n\nBy Using @SpinifyAdsBot"

async def main():
    db = get_database()
    print("Fetching all user configs...")
    configs = await db.config.find({}).to_list(None)
    print(f"Found {len(configs)} user configs.")
    
    updated_count = 0
    for cfg in configs:
        user_id = cfg.get("user_id")
        if not user_id:
            continue
            
        # Check if plan is active (premium)
        active = await is_plan_active(user_id)
        if not active:
            # Free user, update their auto-responder text
            print(f"User {user_id} is a free user. Updating auto_reply_text...")
            await update_user_config(user_id, auto_reply_text=DEFAULT_RESPONDER_TEXT)
            updated_count += 1
            
    print(f"Migration completed! Updated {updated_count} free users.")

if __name__ == "__main__":
    # Ensure env variables are loaded if running directly
    from dotenv import load_dotenv
    load_dotenv(os.path.join(project_root, ".env"))
    
    asyncio.run(main())
