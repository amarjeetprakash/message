"""
Profile Picture Manager module.
Handles loading, saving, selecting random profile pictures, and applying them
to Telegram accounts via Telethon.
"""

import os
import random
import logging
import asyncio
from typing import List, Optional, Dict

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.photos import UploadProfilePhotoRequest
from telethon.errors import RPCError

from shared.utils import get_telegram_client_kwargs

logger = logging.getLogger(__name__)

# Base directory for storing profile pictures
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PFP_DIR = os.path.join(BASE_DIR, "data", "profile_photos")


def ensure_pfp_dir() -> str:
    """Ensure that the profile photos directory exists."""
    os.makedirs(PFP_DIR, exist_ok=True)
    return PFP_DIR


def get_profile_photos() -> List[str]:
    """Get list of valid profile photo file paths from the pool."""
    ensure_pfp_dir()
    valid_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    photos = []
    
    for fname in os.listdir(PFP_DIR):
        ext = os.path.splitext(fname)[1].lower()
        if ext in valid_extensions:
            photos.append(os.path.join(PFP_DIR, fname))
            
    return photos


def get_random_profile_photo() -> Optional[str]:
    """Pick a random photo file path from the pool."""
    photos = get_profile_photos()
    if not photos:
        return None
    return random.choice(photos)


def save_profile_photo(file_bytes: bytes, filename: str) -> str:
    """Save photo bytes to the profile photos directory."""
    ensure_pfp_dir()
    # Sanitize filename
    safe_filename = "".join([c for c in filename if c.isalnum() or c in "._-"])
    if not safe_filename:
        safe_filename = f"photo_{random.randint(1000, 9999)}.jpg"
        
    file_path = os.path.join(PFP_DIR, safe_filename)
    with open(file_path, "wb") as f:
        f.write(file_bytes)
        
    logger.info(f"Saved new profile photo to pool: {file_path}")
    return file_path


async def set_client_profile_photo(client: TelegramClient, photo_path: Optional[str] = None) -> bool:
    """
    Set profile photo for a given TelegramClient.
    If photo_path is not provided, picks a random photo from the pool.
    """
    if not photo_path:
        photo_path = get_random_profile_photo()
        
    if not photo_path or not os.path.exists(photo_path):
        logger.warning("No profile photo available in pool to set.")
        return False
        
    try:
        logger.info(f"Uploading profile photo {os.path.basename(photo_path)} for client...")
        uploaded_file = await client.upload_file(photo_path)
        await client(UploadProfilePhotoRequest(file=uploaded_file))
        logger.info("Successfully updated client profile photo!")
        return True
    except RPCError as e:
        logger.error(f"Telegram RPC error setting profile photo: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error setting profile photo: {e}")
        return False


async def set_all_connected_sessions_pfp() -> Dict[str, int]:
    """
    Iterate over all connected sessions in MongoDB and set a random profile picture
    for each active Telethon account.
    Photos are distributed using shuffled round-robin so each user
    gets a different photo and all photos are used evenly.
    """
    from db.models import get_all_connected_sessions
    
    sessions = await get_all_connected_sessions()
    results = {"total": len(sessions), "success": 0, "failed": 0}
    
    if not sessions:
        logger.info("No connected sessions found to set profile photo.")
        return results
        
    photos = get_profile_photos()
    if not photos:
        logger.warning("Cannot bulk set profile photos: pool is empty.")
        return results

    # Shuffle sessions so assignment order is random
    random.shuffle(sessions)
    
    # Build a shuffled round-robin photo list that covers all sessions
    # e.g. 5 photos, 12 users → [p3,p1,p5,p2,p4, p1,p5,p3,p4,p2, p2,p3]
    photo_assignments = []
    while len(photo_assignments) < len(sessions):
        batch = photos.copy()
        random.shuffle(batch)
        photo_assignments.extend(batch)
    photo_assignments = photo_assignments[:len(sessions)]

    for idx, s_doc in enumerate(sessions):
        user_id = s_doc.get("user_id")
        phone = s_doc.get("phone", "Unknown")
        session_str = s_doc.get("session_string")
        
        if not session_str:
            results["failed"] += 1
            continue

        # Skip Paid Premium users: premium users are allowed to choose and keep their own custom PFP
        from models.plan import get_plan, is_plan_active
        from core.config import OWNER_ID
        user_plan = await get_plan(user_id)
        plan_type = (user_plan.get("plan_type") or "").lower() if user_plan else ""
        is_paid_upgrade = (
            user_id == OWNER_ID or
            (await is_plan_active(user_id) and plan_type and not any(k in plan_type for k in ("free", "trial")))
        )
        if is_paid_upgrade:
            logger.info(f"Skipping bulk promo PFP update for paid premium user {user_id} ({phone})")
            continue
        
        # Each session stores its own API credentials
        api_id = s_doc.get("api_id")
        api_hash = s_doc.get("api_hash")
        
        if not api_id or not api_hash:
            logger.warning(f"Skipping PFP for user {user_id} ({phone}): missing API credentials in session")
            results["failed"] += 1
            continue
        
        client = None
        try:
            client = TelegramClient(
                StringSession(session_str),
                api_id,
                api_hash,
                device_model="Worker PFP Client",
                system_version="2.0",
                app_version="2.0",
                **get_telegram_client_kwargs()
            )
            await client.connect()
            
            if not await client.is_user_authorized():
                logger.warning(f"PFP: Client not authorized for user {user_id} ({phone})")
                results["failed"] += 1
                await client.disconnect()
                continue
                
            photo_path = photo_assignments[idx]
            success = await set_client_profile_photo(client, photo_path)
            if success:
                results["success"] += 1
                logger.info(f"PFP set for user {user_id} ({phone}) → {os.path.basename(photo_path)}")
            else:
                results["failed"] += 1
                
        except Exception as e:
            logger.error(f"Failed setting PFP for user {user_id} ({phone}): {e}")
            results["failed"] += 1
        finally:
            if client and client.is_connected():
                await client.disconnect()
                
    return results

