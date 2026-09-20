"""
Per-user sender logic for the Worker service.
Uses per-user API credentials stored in session.
"""

import logging
import asyncio
import random
import re
from datetime import datetime, timedelta
from typing import Optional, List, Any, Dict

from telethon import TelegramClient, events, utils
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    PeerFloodError,
    ChatWriteForbiddenError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    UserBannedInChannelError,
    InputUserDeactivatedError,
    RPCError,
    MultiError,
    ChannelInvalidError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
    InviteHashExpiredError,
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    SessionPasswordNeededError
)
from telethon.tl.types import InputPeerSelf, InputUserSelf, MessageService
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.account import UpdateProfileRequest

from config import (
    GROUP_GAP_SECONDS, MESSAGE_GAP_SECONDS, DEFAULT_INTERVAL_MINUTES,
    MIN_INTERVAL_MINUTES, OWNER_ID, MAX_GROUPS_PER_USER, MAIN_BOT_USERNAME,
    DEFAULT_AUTO_JOIN_USERNAME, DEFAULT_AD_MESSAGE
)
from db.models import (
    get_session, get_user_groups, get_user_config,
    update_last_saved_id, update_current_msg_index,
    is_plan_active, log_send as db_log_send, remove_group, toggle_group,
    update_session_activity, get_all_user_sessions,
    mark_session_auth_failed, mark_session_disabled, reset_session_auth_fails
)
from models.group import mark_group_failing, clear_group_fail, remove_stale_failing_groups, ensure_default_group

from models.ad_sequence import get_next_ad_sequence_number
from worker.utils import (
    is_night_mode, seconds_until_morning, format_time_remaining,
    UserLogAdapter, send_central_log_return_id, edit_central_log,
    build_progress_bar_report
)
from shared.telegram_error_mapper import map_telegram_error
from shared.utils import get_telegram_client_kwargs
active_senders = {}

from worker.commands import process_command  # Used by event handler

logger = logging.getLogger(__name__)
_cached_global_settings = None
_cached_global_settings_expiry = None

async def get_cached_global_settings():
    global _cached_global_settings, _cached_global_settings_expiry
    now = datetime.utcnow()
    if _cached_global_settings and _cached_global_settings_expiry and now < _cached_global_settings_expiry:
        return _cached_global_settings
        
    try:
        from db.models import get_global_settings
        settings = await get_global_settings()
        _cached_global_settings = settings
        _cached_global_settings_expiry = now + timedelta(seconds=30)
        return settings
    except Exception:
        return {
            "key": "global",
            "night_mode_force": "auto",
            "updated_at": now
        }



class AdaptiveDelayController:
    """Dynamically adjusts gaps based on FloodWait and success rates."""
    MAX_MULTIPLIER = 10.0  # Hard cap — prevents runaway wait times

    def __init__(self, base_gap: int):
        self.base_gap = base_gap
        self.multiplier = 1.0
        self.success_streak = 0
        self.last_flood_at = None

    def get_gap(self) -> int:
        """Apply adaptive multiplier and random jitter for human-like behavior."""
        # Dynamic Time-based Decay:
        # If the account has rested since the last flood wait, gradually decrease the multiplier.
        if self.last_flood_at and self.multiplier > 1.0:
            elapsed_seconds = (datetime.utcnow() - self.last_flood_at).total_seconds()
            # Every 5 minutes (300 seconds) of rest decays the excess multiplier by 15% (decay factor of 0.85)
            # This ensures that after 30-40 minutes of rest or successful sends, the multiplier returns to normal (1.0).
            decay_periods = elapsed_seconds / 300.0
            decay_factor = 0.85 ** decay_periods
            self.multiplier = max(1.0, 1.0 + (self.multiplier - 1.0) * decay_factor)

        jitter = random.uniform(0.8, 1.2)
        return int(self.base_gap * self.multiplier * jitter)

    def on_flood(self, wait_seconds: int):
        self.last_flood_at = datetime.utcnow()
        new_mult = max(self.multiplier * 1.5, (wait_seconds / self.base_gap) * 1.1)
        self.multiplier = min(new_mult, self.MAX_MULTIPLIER)  # cap applied
        self.success_streak = 0

    def on_success(self):
        self.success_streak += 1
        # Every 10 successes, slowly decrease multiplier back to 1.0
        if self.success_streak >= 10 and self.multiplier > 1.0:
            self.multiplier = max(1.0, self.multiplier * 0.9)
            self.success_streak = 0


class UserSender:
    """Handles message sending for a single user using their own API credentials."""
    
    # After this many consecutive auth failures, the session is permanently disabled
    MAX_AUTH_FAILURES = 3

    def __init__(self, user_id: int, phone: str, semaphore: asyncio.Semaphore = None):
        self.user_id = user_id
        self.phone = phone
        self.client = None
        self.running = False
        # Semaphore shared across all senders — caps simultaneous Telethon connections
        self._semaphore = semaphore or asyncio.Semaphore(1)
        
        # Professional Logging with Adapter
        self.logger = UserLogAdapter(logger, {'user_id': user_id, 'phone': phone})
        self.wake_up_event = asyncio.Event()
        self.responder_cache = {}  # Cache: {sender_id: timestamp} to avoid double-replies
        self.status = "Initializing"
        self.message_counter = 0  # Track total sends in this session
        
        # Performance & Reliability state
        self.config_cache = {} # {key: (value, expiry)}
        self.last_status = ""  # Cache to prevent redundant DB writes
        self.adaptive_group_gap = AdaptiveDelayController(GROUP_GAP_SECONDS)
        self.adaptive_msg_gap = AdaptiveDelayController(MESSAGE_GAP_SECONDS)
        self.last_heartbeat = None
        self.error_streak = 0
        
        # V6: Smart dialog priming — only once on startup
        self._dialogs_primed = False
        # V6: Cycle dedup — prevent double-sends on crash/restart
        self._cycle_id = None
        self._sent_this_cycle = set()  # {(msg_id, chat_id)} already sent
        # V6: PeerFlood exponential backoff tracking
        self._peer_flood_count = 0
        # V6: Cycle timing metrics
        self._cycle_start_time = None
        self._last_cycle_duration = None
        self._last_plan_log = None  # Throttling for "Plan expired" logs
        
        # Performance entity caching (Positive and Negative Cache)
        self._entity_cache = {}  # {chat_id: Entity}
        self._failed_entities = {}  # {chat_id: (timestamp, reason)}
        
        # User profile cache (to avoid leaking phone numbers in logs)
        self.first_name = ""
        self.username = ""
        self.last_global_branding_check_at = None
        self.last_pause_warning_sent_at = None
        self.ads_updated = False
    
    def calculate_health_score(self) -> int:
        """Calculate account health score from 0 (Critical) to 100 (Optimal)."""
        score = 100
        score -= min(self.error_streak * 10, 60)
        last_flood = self.adaptive_group_gap.last_flood_at
        if last_flood and (datetime.utcnow() - last_flood).total_seconds() < 3600:
            score -= 25
        if getattr(self, "_peer_flood_count", 0) > 0:
            score -= min(self._peer_flood_count * 30, 50)
        return max(0, min(100, score))

    async def update_status(self, status: str):
        """Update worker status in database with throttling for smoothness."""
        if status == self.last_status:
            return
            
        self.status = status
        self.last_status = status
        
        try:
            from db.database import get_database
            db = get_database()
            await db.sessions.update_one(
                {"user_id": self.user_id, "phone": self.phone},
                {"$set": {
                    "worker_status": status, 
                    "status_updated_at": datetime.utcnow(),
                    "error_streak": self.error_streak,
                    "health_score": self.calculate_health_score(),
                    "last_flood_at": self.adaptive_group_gap.last_flood_at
                }}
            )
        except Exception:
            pass

    async def _get_cached_config(self):
        """Get user config with 5-minute TTL cache."""
        cache_key = f"config_{self.user_id}"
        cached = self.config_cache.get(cache_key)
        if cached:
            val, expiry = cached
            if datetime.utcnow() < expiry:
                return val
        
        config = await get_user_config(self.user_id)
        self.config_cache[cache_key] = (config, datetime.utcnow() + timedelta(seconds=60))
        return config

    async def _cached_is_plan_active(self):
        """Check plan status with 10-minute TTL cache."""
        cache_key = f"plan_{self.user_id}"
        cached = self.config_cache.get(cache_key)
        if cached:
            val, expiry = cached
            if datetime.utcnow() < expiry:
                return val
        
        active = await is_plan_active(self.user_id)
        self.config_cache[cache_key] = (active, datetime.utcnow() + timedelta(minutes=1))
        return active

    async def check_account_health(self, success_count: int, failed_count: int) -> tuple[bool, str]:
        """
        Check if the account health is weak.
        Returns (is_weak, reason).
        """
        # 1. Check consecutive error streak
        if self.error_streak >= 5:
            return True, f"high consecutive error streak ({self.error_streak} errors)"
            
        # 2. Check failure rate in the current cycle
        total_attempts = success_count + failed_count
        if total_attempts >= 4:
            fail_rate = failed_count / total_attempts
            if fail_rate >= 0.5:
                return True, f"high cycle failure rate ({int(fail_rate * 100)}% over {total_attempts} attempts)"
                
        return False, ""

    def select_smart_ad(self, group: dict, messages: list):
        """
        Select best ad message for a group using keyword search & top-ad prioritization.
        Matches group title/topic keywords with ad text; falls back to top primary ad.
        """
        if not messages:
            return None
        if len(messages) == 1:
            return messages[0]
            
        chat_title = (group.get("chat_title") or "").lower()
        title_words = set(re.findall(r'\b[a-zA-Z0-9]{3,}\b', chat_title))
        
        best_msg = None
        best_score = 0
        
        for msg in messages:
            text = (getattr(msg, "text", "") or "").lower()
            if not text:
                continue
            msg_words = set(re.findall(r'\b[a-zA-Z0-9]{3,}\b', text))
            common = title_words.intersection(msg_words)
            score = len(common)
            if score > best_score:
                best_score = score
                best_msg = msg
                
        if best_msg:
            return best_msg
        else:
            return messages[0]

    async def _enforce_profile_branding(self):
        """Enforce name and bio rules based on plan status (Free vs Premium)."""
        try:
            from telethon.tl.functions.users import GetFullUserRequest
            from telethon.tl.functions.account import UpdateProfileRequest
            # Sync with global force check timestamp if exists
            global_settings = await get_cached_global_settings()
            force_check_at = global_settings.get("force_branding_check_at")
            if force_check_at:
                self.last_global_branding_check_at = force_check_at

            from models.plan import get_plan
            user_plan = await get_plan(self.user_id)
            plan_type = (user_plan.get("plan_type") or "").lower() if user_plan else ""
            is_paid_upgrade = (
                self.user_id == OWNER_ID or
                (await is_plan_active(self.user_id) and plan_type.startswith("paid"))
            )
            
            # Fetch current profile info
            full = await self.client(GetFullUserRequest('me'))
            me = full.users[0]
            about = full.full_user.about or ""
            
            first_name = me.first_name or ""
            last_name = me.last_name or ""
            
            enforced_bio = "Fʀᴇᴇ Aᴅs Bᴏᴛ Bʏ @SpinifyAdsBot • Pᴏᴡᴇʀᴇᴅ Bʏ @PhiloBots"
            old_bios = [
                "Fʀᴇᴇ Aᴅs Bᴏᴛ Bʏ @SpinifyAdsBot • Pᴏᴡᴇʀᴇᴅ Bʏ @PhiloBots",
                "ғʀᴇᴇ ᴀᴅs ʙᴏᴛ ʙʏ @SpinifyAdsBot",
                "ᴍade easy by @automessageschedulerBot",
                "ᴍade easy by @PhiloBots",
                "ᴍade easy by @SpinifyAdsBot",
                "ғʀᴇᴇ ᴀᴅs ʙᴏᴛ ʙʏ @automessageschedulerBot",
                "ғʀᴇᴇ ᴀᴅs ʙᴏᴛ ʙʏ @PhiloBots",
                "ғʀᴇᴇ ᴀᴅs ʙᴏᴛ @automessageschedulerBot",
                "ғʀᴇᴇ ᴀᴅs ʙᴏᴛ @PhiloBots",
                "ғʀᴇᴇ ᴀᴅs ʙᴏᴛ @SpinifyAdsBot",
            ]
            
            # 1. Clean any existing promo suffixes (old or new) to restore original first & last names
            clean_first = re.sub(r'(?:◕|ϟ|⚡|\bVɪᴀ\b|\bVia\b|\bBʏ\b|\bBY\b|\bBy\b)\s*@[A-Za-z0-9_]+', '', first_name, flags=re.IGNORECASE).strip()
            clean_last = re.sub(r'(?:◕|ϟ|⚡|\bVɪᴀ\b|\bVia\b|\bBʏ\b|\bBY\b|\bBy\b)\s*@[A-Za-z0-9_]+', '', last_name, flags=re.IGNORECASE).strip()
            for old_suffix in [
                "Bʏ @PhiloBots", "Bʏ @SpinifyAdsBot", "Bʏ @automessageschedulerBot",
                "BY @PhiloBots", "BY @SpinifyAdsBot", "BY @automessageschedulerBot",
                "By @PhiloBots", "By @SpinifyAdsBot", "By @automessageschedulerBot",
                "◕ @PhiloBots", "◕ @SpinifyAdsBot", "◕ @automessageschedulerBot",
                "ϟ @PhiloBots", "ϟ @SpinifyAdsBot", "ϟ @automessageschedulerBot",
                "ϟ Vɪᴀ @SpinifyAdsBot", "ϟ Vɪᴀ @PhiloBots", "ϟ Vɪᴀ @automessageschedulerBot",
                "Vɪᴀ @SpinifyAdsBot", "Vɪᴀ @PhiloBots", "Vɪᴀ @automessageschedulerBot",
                "Via @SpinifyAdsBot", "Via @PhiloBots", "Via @automessageschedulerBot",
            ]:
                clean_first = clean_first.replace(old_suffix, "").strip()
                clean_last = clean_last.replace(old_suffix, "").strip()

            bot_uname = (MAIN_BOT_USERNAME or "SpinifyAdsBot").lstrip("@")
            if not bot_uname or bot_uname.lower() in ["automessageschedulerbot", "philobots"]:
                bot_uname = "SpinifyAdsBot"
            suffix = f"Bʏ @{bot_uname}"

            if not is_paid_upgrade:
                # ── FREE USER ENFORCEMENT ──
                # 1. Enforce name suffix for free users
                new_first = clean_first or "User"
                if clean_last:
                    new_last = f"{clean_last} {suffix}"
                else:
                    new_last = suffix

                if new_first != first_name or new_last != last_name:
                    self.logger.info(f"Enforcing Free Profile Name Suffix: '{new_first}' '{new_last}'")
                    await self.client(UpdateProfileRequest(first_name=new_first, last_name=new_last))

                # 2. Enforce free bio
                if enforced_bio not in about:
                    # Clean the bio first to remove any old/other enforced bios
                    clean_bio = about
                    for ob in old_bios:
                        clean_bio = clean_bio.replace(ob, "").strip()
                    clean_bio = clean_bio.strip(" | -•")
                    
                    if clean_bio:
                        # Limit clean_bio length so the combined string fits within 70 characters
                        max_len = 70 - len(f" | {enforced_bio}")
                        if len(clean_bio) > max_len:
                            clean_bio = clean_bio[:max_len].strip(" | -•")
                        new_bio = f"{clean_bio} | {enforced_bio}"
                    else:
                        new_bio = enforced_bio
                        
                    if about != new_bio:
                        self.logger.info(f"Enforcing Free Bio: '{new_bio}'")
                        await self.client(UpdateProfileRequest(about=new_bio))
                
                # 2. Enforce PFP assignment for free users (if user has no profile photo)
                try:
                    photos = await self.client.get_profile_photos('me', limit=1)
                    if not photos:
                        self.logger.info("Free user has no profile photo, setting promo PFP from pool...")
                        from shared.pfp_manager import set_client_profile_photo
                        await set_client_profile_photo(self.client)
                except Exception as pfp_err:
                    self.logger.warning(f"Error checking/setting PFP for free user: {pfp_err}")
                    
                # 3. Enforce channel and chat join
                from core.config import CHANNEL_USERNAME
                required_channels = []
                if CHANNEL_USERNAME:
                    required_channels.append(CHANNEL_USERNAME.lstrip('@'))
                required_channels.append("spinifychat")
                
                joined_channels = set()
                try:
                    # Check if already joined by checking dialogs (prevents redundant join requests)
                    async for dialog in self.client.iter_dialogs(limit=100):
                        username = getattr(dialog.entity, 'username', '')
                        if username:
                            username_lower = username.lower()
                            for req in required_channels:
                                if username_lower == req.lower():
                                    joined_channels.add(req.lower())
                except Exception as dialog_err:
                    self.logger.warning(f"Error checking dialogs: {dialog_err}")
                
                # Double-check membership for channels not found in the top 100 dialogs
                from telethon.errors import UserNotParticipantError
                from telethon.tl.types import Channel
                for req in required_channels:
                    if req.lower() not in joined_channels:
                        try:
                            # 1. Fetch the entity
                            entity = await self.client.get_entity(req)
                            
                            # For User/Bot entities, they cannot be joined like channels, so we count them as joined
                            from telethon.tl.types import User
                            if isinstance(entity, User):
                                joined_channels.add(req.lower())
                                self.logger.info(f"Verified @{req} is a User/Bot (bypassing channel join check).")
                                continue

                            # 2. For channels/supergroups, check the left flag directly (regular members cannot call get_permissions)
                            if isinstance(entity, Channel):
                                if not entity.left:
                                    joined_channels.add(req.lower())
                                    self.logger.info(f"Verified membership in channel @{req} via entity.left check.")
                                    continue
                                else:
                                    self.logger.info(f"User is not a participant in channel @{req} (entity.left is True)")
                                    continue
                                    
                            # 3. For small groups or fallback
                            await self.client.get_permissions(entity, 'me')
                            joined_channels.add(req.lower())
                            self.logger.info(f"Verified membership in @{req} via get_permissions.")
                        except UserNotParticipantError:
                            self.logger.info(f"User is not a participant in @{req}")
                        except Exception as e:
                            # For other exceptions (e.g. rate limits or connection errors),
                            # we log it but don't pause the user to prevent false positives.
                            self.logger.warning(f"Could not verify membership in @{req} via get_permissions/entity check: {e}")
                            joined_channels.add(req.lower())
                            
                missing_channels = [req for req in required_channels if req.lower() not in joined_channels]
                if missing_channels:
                    missing_mentions = ", ".join([f"@{m}" for m in missing_channels])
                    self.logger.warning(f"Free user is missing channels: {missing_mentions}! Pausing scheduler.")
                    from models.group import pause_user_groups
                    await pause_user_groups(self.user_id)
                    
                    # Throttle warning messages to once every 10 minutes to prevent spamming in background task
                    now = datetime.utcnow()
                    last_sent = getattr(self, "last_pause_warning_sent_at", None)
                    if not last_sent or (now - last_sent) > timedelta(minutes=10):
                        self.last_pause_warning_sent_at = now
                        try:
                            from worker.utils import send_direct_user_message
                            await send_direct_user_message(
                                self.user_id,
                                f"⚠️ <b>Free Version Paused</b>\n\n"
                                f"You must remain joined to {missing_mentions} to use the free version of this bot.\n\n"
                                f"All your groups have been paused. Please join {missing_mentions} and then send <code>.start</code> in Saved Messages to resume."
                            )
                            self.logger.info(f"Sent channel membership reminder to user direct chat.")
                        except Exception as msg_err:
                            self.logger.warning(f"Failed to send channel membership reminder via main bot: {msg_err}. Falling back to Saved Messages.")
                            try:
                                await self.client.send_message(
                                    'me',
                                    f"⚠️ **Free Version Paused**\n\n"
                                    f"You must remain joined to {missing_mentions} to use the free version of this bot.\n\n"
                                    f"All your groups have been paused. Please join {missing_mentions} and then send `.start` in Saved Messages to resume."
                                )
                            except Exception as fallback_err:
                                self.logger.error(f"Fallback to Saved Messages also failed: {fallback_err}")
                        
                    await self.update_status(f"Join {missing_mentions}")
                    return False
                        
            else:
                # ── PREMIUM USER CLEANUP ──
                # Premium users: NO name suffix, NO promo bio, NO auto-assigned PFP
                if clean_first != first_name or clean_last != last_name:
                    self.logger.info(f"Removing Free Name suffix for Paid Premium user: '{clean_first}' '{clean_last}'")
                    await self.client(UpdateProfileRequest(first_name=clean_first or "User", last_name=clean_last))

                clean_bio = about
                for ob in old_bios:
                    clean_bio = clean_bio.replace(ob, "").strip()
                clean_bio = clean_bio.strip(" | -•")
                
                if clean_bio != about:
                    self.logger.info(f"Removing Free Bio suffix for Premium user: '{about}' -> '{clean_bio}'")
                    await self.client(UpdateProfileRequest(about=clean_bio))

                # Remove promo PFP if set for Paid Premium user
                try:
                    photos = await self.client.get_profile_photos('me')
                    if photos:
                        from telethon.tl.functions.photos import DeletePhotosRequest
                        from telethon.tl.types import InputPhoto
                        input_photos = [
                            InputPhoto(id=p.id, access_hash=p.access_hash, file_reference=p.file_reference)
                            for p in photos
                            if hasattr(p, 'id') and hasattr(p, 'access_hash') and hasattr(p, 'file_reference')
                        ]
                        if input_photos:
                            self.logger.info(f"Removing promo PFP for Paid Premium user {self.user_id}...")
                            await self.client(DeletePhotosRequest(id=input_photos))
                except Exception as pfp_del_err:
                    self.logger.warning(f"Note on removing PFP for premium user: {pfp_del_err}")
                    
            # ── DEFAULT GROUP AUTO-JOIN FOR ALL USERS (Free & Premium) ──
            await self._ensure_default_group_autojoin()

            return True
            
        except Exception as e:
            self.logger.error(f"Error enforcing/checking profile branding: {e}")
            return True

    async def _ensure_default_group_autojoin(self):
        """Ensure default group (https://t.me/spinifychat) is auto-joined by Telegram client and added to DB groups."""
        try:
            from telethon.tl.functions.channels import JoinChannelRequest
            default_uname = (DEFAULT_AUTO_JOIN_USERNAME or "spinifychat").lstrip("@")

            entity = None
            try:
                entity = await self.client.get_entity(default_uname)
            except Exception as e:
                self.logger.warning(f"Could not resolve default group @{default_uname}: {e}")

            if entity:
                chat_id = utils.get_peer_id(entity)
                chat_title = getattr(entity, 'title', None) or "Spinify Chat"

                is_left = getattr(entity, 'left', None)
                if is_left is True:
                    self.logger.info(f"Auto-joining default group @{default_uname}...")
                    try:
                        await self.client(JoinChannelRequest(entity))
                        self.logger.info(f"Successfully auto-joined default group @{default_uname}!")
                    except Exception as join_err:
                        self.logger.warning(f"Failed to auto-join @{default_uname}: {join_err}")

                await ensure_default_group(
                    user_id=self.user_id,
                    phone=self.phone,
                    chat_id=chat_id,
                    chat_title=chat_title
                )
        except Exception as err:
            self.logger.warning(f"Error in _ensure_default_group_autojoin: {err}")


    async def start(self):
        """Start the sender loop — semaphore only guards the short connect+auth phase."""
        self.running = True
        self.logger.info("Starting sender...")

        # ── Pre-flight: check plan status (allow free mode startup) ───────
        from db.models import is_plan_active
        if not await is_plan_active(self.user_id):
            self.logger.info(f"Plan is inactive or expired for user {self.user_id} — starting in FREE mode")

        # ── Pre-flight: validate session record exists ─────────────────────
        session_data = await get_session(self.user_id, self.phone)
        if not session_data or not session_data.get("connected"):
            self.logger.warning("No connected session record found — aborting")
            self.running = False
            return

        self.error_streak = session_data.get("error_streak", 0)

        session_string = session_data.get("session_string", "")
        if len(session_string) < 50:  # Valid Telethon StringSession strings are very long
            self.logger.warning("Session string missing or too short — disabling")
            await mark_session_disabled(self.user_id, self.phone, "invalid_session_string")
            return

        api_id = session_data.get("api_id")
        api_hash = session_data.get("api_hash")

        if not api_id or not api_hash:
            self.logger.error("API ID or Hash missing from session document — disabling")
            await mark_session_disabled(self.user_id, self.phone, "missing_api_credentials")
            return

        # Build the client first (no network yet)
        self.client = TelegramClient(
            StringSession(session_string),
            api_id,
            api_hash,
            device_model="Spinify Ads Bot Worker",
            system_version="1.0",
            app_version="1.0",
            **get_telegram_client_kwargs()
        )
        self.client.phone = self.phone


        # ── Phase 1: Connect + Auth (semaphore caps simultaneous connects) ──
        # The semaphore is released as soon as auth succeeds or fails.
        # It does NOT hold during the long-running send loop.
        authorized = False
        async with self._semaphore:
            authorized = await self._connect_and_authenticate()

        if not authorized:
            if self.client:
                await self.client.disconnect()
            return

        # ── Phase 2: Run session (no semaphore — slot is already freed) ─────
        active_senders[self.user_id] = self
        try:
            await self._run_session()
        finally:
            active_senders.pop(self.user_id, None)

    async def _connect_and_authenticate(self) -> bool:
        """
        Connect to Telegram and verify authorization.
        Returns True if authorized, False otherwise.
        Semaphore is held only during this short phase.
        """
        try:
            await self.client.connect()
        except (ConnectionError, OSError) as conn_err:
            self.logger.warning(f"Network connection failed: {conn_err}")
            return False
        except AuthKeyDuplicatedError:
            self.logger.error("🚨 CRITICAL: Session duplicated! Another instance of this bot is likely running.")
            await mark_session_disabled(self.user_id, self.phone, "auth_key_duplicated")
            return False
        except AuthKeyUnregisteredError:
            self.logger.error("🛑 CRITICAL: Account BANNED! (AuthKeyUnregisteredError)")
            await mark_session_disabled(self.user_id, self.phone, "account_banned")
            return False
        except SessionPasswordNeededError:
            self.logger.error("🔑 2FA REQUIRED: Account needs cloud password.")
            await mark_session_disabled(self.user_id, self.phone, "2fa_required")
            return False

        if not await self.client.is_user_authorized():
            fail_count = await mark_session_auth_failed(self.user_id, self.phone)
            if fail_count >= self.MAX_AUTH_FAILURES:
                self.logger.error(
                    f"Session unauthorized — {fail_count} failures. "
                    f"Permanently disabling."
                )
                await mark_session_disabled(
                    self.user_id, self.phone, f"auth_failed_{fail_count}x"
                )
            else:
                self.logger.warning(
                    f"Session unauthorized (failure {fail_count}/{self.MAX_AUTH_FAILURES}). "
                    f"Will retry after 6h cooldown."
                )
            return False

        # Authorized — reset any previous failure counts
        await reset_session_auth_fails(self.user_id, self.phone)
        self.logger.info("✅ Authorized successfully")
        
        # Populate first_name, last_name, and username
        try:
            me = await self.client.get_me()
            if me:
                self.first_name = me.first_name or ""
                self.last_name = me.last_name or ""
                self.username = me.username or ""
        except Exception as me_e:
            self.logger.warning(f"Failed to fetch profile details in get_me(): {me_e}")
        return True

    async def _run_session(self):
        """
        Register event handlers, run background tasks, and enter the send loop.
        Called AFTER the semaphore is released — runs for the entire session lifetime.
        """
        try:
            # ── PHASE 2a: REGISTER HANDLERS IMMEDIATELY ────────────────────
            # Handler 1: Outgoing messages from self (commands + new ads in Saved Messages)
            @self.client.on(events.NewMessage(outgoing=True))
            async def outgoing_handler(event):
                """Handle outgoing messages: dot commands and new ads."""
                try:
                    if not event.message:
                        return
                    
                    text = (event.message.text or "").strip()
                    
                    # 1. Handle Commands (dot commands)
                    if text.startswith("."):
                        self.logger.info(f"Received command: {text.split()[0]}")
                        await process_command(self.client, self.user_id, event.message, sender=self)
                        return

                    # 2. Handle New Ads (sent to Saved Messages)
                    chat = await event.get_chat()
                    is_saved = getattr(chat, 'is_self', False)
                    if not is_saved:
                        try:
                            me = await self.client.get_me()
                            is_saved = event.chat_id == me.id
                        except Exception:
                            pass
                    
                    if is_saved:
                        self.logger.info("New ad detected! Waking up worker...")
                        self.wake_up_event.set()
                        await asyncio.sleep(0.1)
                        self.wake_up_event.clear()

                except Exception as e:
                    self.logger.error(f"Outgoing handler error: {e}")

            # Handler 2: Incoming messages (auto-responder + remote commands)
            @self.client.on(events.NewMessage(incoming=True))
            async def incoming_handler(event):
                """Handle incoming messages: commands from owner + auto-responder."""
                try:
                    if not event.message or not event.message.text:
                        return
                        
                    text = event.message.text.strip()
                    sender_id = event.sender_id
                    
                    # 1. Handle Commands (Incoming in private chat from user or owner)
                    if text.startswith(".") and event.is_private and (sender_id == self.user_id or sender_id == OWNER_ID):
                        self.logger.info(f"Received command from {sender_id}: {text.split()[0]}")
                        await process_command(self.client, self.user_id, event.message, sender=self)
                        return

                    # 2. Handle Auto-Responder (Private messages only)
                    if event.is_private:
                        await self.handle_auto_reply(event)

                except Exception as e:
                    self.logger.error(f"Incoming handler error: {e}")

            self.logger.info("✅ Event handlers registered")

            # ── PHASE 2b: PRIME DIALOGS (once) ─────────────────────────────
            # Pre-load entity cache to prevent "Could not find entity" errors.
            # Only done once per session lifetime — not every cycle.
            try:
                self.logger.info("📂 Priming dialog cache...")
                async for _ in self.client.iter_dialogs(limit=300):
                    pass
                self._dialogs_primed = True
                self.logger.info("✅ Dialog cache primed")
            except Exception as e:
                self.logger.warning(f"Dialog priming failed (non-fatal): {e}")

            # ── PHASE 2c: START BACKGROUND TASKS & MAIN LOOP ───────────────
            watchdog_task = asyncio.create_task(self._connection_watchdog())
            branding_task = asyncio.create_task(self._branding_enforcement_loop())
            
            # Send session started log to central channel
            try:
                from db.models import get_plan, get_user_groups
                from worker.utils import build_session_start_log, send_central_log
                
                # Fetch plan status
                plan_doc = await get_plan(self.user_id)
                plan_status = "Unknown"
                if plan_doc:
                    p_type = plan_doc.get("plan_type", "trial")
                    p_status = plan_doc.get("status", "active")
                    plan_status = f"{p_type.capitalize()} ({p_status.capitalize()})"
                
                # Fetch user groups managed by this account
                all_raw_groups = await get_user_groups(self.user_id, enabled_only=True, phone=self.phone)
                my_groups = [g for g in all_raw_groups if g.get("account_phone") == self.phone]
                
                user_label = await self.get_user_label()
                start_log = build_session_start_log(
                    user_label=user_label,
                    group_count=len(my_groups),
                    plan_status=plan_status
                )
                asyncio.create_task(send_central_log(start_log))
            except Exception as start_log_err:
                self.logger.error(f"Failed to send session start log: {start_log_err}")

            # Run the main send loop
            await self.run_loop()
            
            # Cancel tasks when main loop ends
            watchdog_task.cancel()
            branding_task.cancel()

        except Exception as e:
            self.logger.error(f"Error in session lifecycle: {e}")
        finally:
            if self.client:
                await self.client.disconnect()

    async def stop(self):
        """Stop the sender safely and cleanly close the connection."""
        self.running = False
        if self.client:
            try:
                # Disconnect politely
                if self.client.is_connected():
                    await self.client.disconnect()
            except Exception as e:
                self.logger.error(f"Error disconnecting client on stop: {e}")
    
    
    async def handle_auto_reply(self, event):
        """Send automated reply to incoming private messages with anti-spam protections."""
        try:
            if not event.is_private:
                return

            sender = await event.get_sender()
            sender_id = event.sender_id
            
            # 1. Skip bots, deleted users, verified accounts, and self
            if not sender or getattr(sender, 'bot', False) or getattr(sender, 'deleted', False) or getattr(sender, 'verified', False):
                return
                
            me = await self.client.get_me()
            if sender_id == me.id or getattr(sender, 'is_self', False):
                return

            # 2. Skip official Telegram notification system accounts
            SYSTEM_ACCOUNTS = {777000, 42777, 1782208}
            if sender_id in SYSTEM_ACCOUNTS:
                return

            # 3. Skip dot commands and empty messages
            msg_text = (event.message.text or "").strip()
            if not msg_text or msg_text.startswith("."):
                return
            
            # 4. Check if responder is enabled
            config = await self._get_cached_config()
            if not config.get("auto_reply_enabled", False):
                return
            
            # 5. Prevent spamming (reply once every 4 hours per user)
            now = datetime.utcnow().timestamp()
            last_reply = self.responder_cache.get(sender_id, 0)
            if now - last_reply < 14400:  # 4 hours (14,400 seconds)
                return
            
            # Cache cleanup if cache size grows large
            if len(self.responder_cache) > 5000:
                self.responder_cache = {k: v for k, v in self.responder_cache.items() if now - v < 14400}
            
            is_premium = await self._cached_is_plan_active()
            if is_premium:
                reply_text = config.get("auto_reply_text", "Hello! Thanks for your message.")
            else:
                reply_text = DEFAULT_AD_MESSAGE

            self.logger.info(f"Sending auto-reply to {sender_id}")
            await event.reply(reply_text)
            
            # Update cache
            self.responder_cache[sender_id] = now
            
        except Exception as e:
            self.logger.error(f"Auto-reply error: {e}")

    async def run_loop(self):
        """Main sender loop — V6 Powerhouse."""
        while self.running:
            try:
                # 0. Fresh config + session activity
                self.config_cache.clear()
                self.ads_updated = False
                self._cycle_start_time = datetime.utcnow()
                self._cycle_id = int(self._cycle_start_time.timestamp())
                self._sent_this_cycle.clear()
                self.logger.info(f"Starting new sending cycle (#{self._cycle_id})...")
                asyncio.create_task(update_session_activity(self.user_id, self.phone))
                
                # AUTO-CLEANUP & AUTO-RECOVERY
                try:
                    removed_count = await remove_stale_failing_groups(self.user_id)
                    if removed_count > 0:
                        self.logger.info(f"🧹 Auto-cleanup: Removed {removed_count} stale failing group(s).")
                    
                    from models.group import resume_account_paused_groups
                    recovered = await resume_account_paused_groups(self.user_id)
                    if recovered > 0:
                        self.logger.info(f"🔄 Auto-recovery: Re-enabled {recovered} groups paused due to account restrictions.")
                except Exception as e:
                    self.logger.warning(f"Maintenance and recovery tasks error: {e}")
                
                # AUTO-RECOVERY: If error streak is dangerously high
                if self.error_streak >= 20:
                    cooldown = min(self.error_streak * 30, 1800)
                    self.logger.warning(f"🛑 High error streak. Cooling down for {cooldown//60}m...")
                    await self.update_status(f"Cooldown ({cooldown//60}m)")
                    await asyncio.sleep(cooldown)
                    self.error_streak = 0
                    self.config_cache.clear()
                
                # AUTO HEALTH CHECK: If error streak indicates a weak account
                is_weak, reason = await self.check_account_health(0, 0)
                if is_weak:
                    cooldown_minutes = 15
                    self.logger.warning(f"⚠️ Account health is weak at cycle start ({reason}). Taking a {cooldown_minutes}-minute break...")
                    from worker.utils import send_central_log, build_error_log
                    user_label = await self.get_user_label()
                    asyncio.create_task(send_central_log(build_error_log(
                        user_label,
                        "System Check",
                        "⚠️ ACCOUNT HEALTH WEAK",
                        f"Account is weak due to: {reason}. Taking a {cooldown_minutes}-minute safety break before starting cycle."
                    )))
                    await self.update_status(f"Health Break ({cooldown_minutes}m)")
                    await asyncio.sleep(cooldown_minutes * 60)
                    self.error_streak = 0
                    self.config_cache.clear()
                    continue
                
                # 0. Enforce profile branding (Name, Bio, and Channel join check)
                if not await self._enforce_profile_branding():
                    # If check fails (e.g. Free user not in channel), pause and wait
                    await asyncio.sleep(120)
                    continue
                
                # 1. Check plan validity
                is_premium = await self._cached_is_plan_active()
                
                # 2. Check night mode
                if await is_night_mode():
                    wait_seconds = seconds_until_morning()
                    self.logger.info(f"Auto-Night mode, sleeping {format_time_remaining(wait_seconds)}...")
                    await self.update_status("Night Mode")
                    await asyncio.sleep(min(wait_seconds, 3600))
                    continue
                
                # 3. Get groups — smart assignment
                all_raw_groups = await get_user_groups(self.user_id, enabled_only=True, phone=self.phone)
                
                my_groups = [g for g in all_raw_groups if g.get("account_phone") == self.phone]
                other_groups_count = len([g for g in all_raw_groups if g.get("account_phone") and g.get("account_phone") != self.phone])
                orphan_groups = [g for g in all_raw_groups if g.get("account_phone") is None]
                
                all_sessions = await get_all_user_sessions(self.user_id)
                all_sessions.sort(key=lambda s: s["phone"])
                session_phones = [s["phone"] for s in all_sessions]
                num_accounts = len(session_phones)
                
                groups = list(my_groups)
                if orphan_groups:
                    orphan_groups.sort(key=lambda x: x.get('chat_id', 0))
                    if num_accounts > 1:
                        try:
                            my_idx = session_phones.index(self.phone)
                            my_orphans = [g for i, g in enumerate(orphan_groups) if i % num_accounts == my_idx]
                            groups.extend(my_orphans)
                        except ValueError:
                            groups.extend(orphan_groups)
                    else:
                        groups.extend(orphan_groups)

                if other_groups_count > 0:
                    self.logger.info(f"🛡️ Skipping {other_groups_count} groups managed by other accounts.")

                if not groups:
                    await self.update_status("Sleeping (No assigned groups)")
                    await asyncio.sleep(60)
                    continue

                messages = await self.get_all_saved_messages()
                if not messages:
                    await self.update_status("Sleeping (No ads)")
                    await asyncio.sleep(60)
                    continue
                
                config = await self._get_cached_config()
                if not is_premium:
                    copy_mode = False
                    shuffle_mode = False
                    interval_minutes = 20
                    send_mode = "sequential"
                else:
                    copy_mode = config.get("copy_mode", False)
                    shuffle_mode = config.get("shuffle_mode", False)
                    interval_minutes = config.get("interval_min", DEFAULT_INTERVAL_MINUTES)
                    send_mode = config.get("send_mode", "sequential")
                
                if shuffle_mode:
                    self.logger.info("🔀 Shuffle Mode: Randomized group order.")
                    random.shuffle(groups)

                # Re-prime dialogs only if initial priming failed
                if not self._dialogs_primed:
                    try:
                        async for _ in self.client.iter_dialogs(limit=300):
                            pass
                        self._dialogs_primed = True
                    except Exception:
                        pass

                # 4. Build tasks based on Send Mode
                tasks = []
                
                if send_mode == "sequential":
                    for msg in messages:
                        for group in groups:
                            tasks.append((msg, group))
                elif send_mode == "rotate":
                    for i, group in enumerate(groups):
                        msg = messages[i % len(messages)]
                        tasks.append((msg, group))
                elif send_mode == "random":
                    for group in groups:
                        msg = random.choice(messages)
                        tasks.append((msg, group))
                elif send_mode == "smart":
                    for group in groups:
                        msg = self.select_smart_ad(group, messages)
                        if msg:
                            tasks.append((msg, group))
                
                self.logger.info(f"📋 {len(tasks)} tasks ({send_mode}) | {len(groups)} groups × {len(messages)} ads")

                success_groups = []
                failed_groups = []
                skipped_dedup = 0
                last_send_time = None
                last_msg_id = None

                # --- Animated Progress Tracker Setup ---
                total_tasks = len(tasks)
                success_count = 0
                failed_count = 0
                skipped_count = 0
                health_success = 0
                health_failed = 0
                
                # Build initial report
                user_label = await self.get_user_label()
                initial_report = build_progress_bar_report(
                    user_label=user_label,
                    send_mode=send_mode,
                    index=0,
                    total=total_tasks,
                    success_count=0,
                    failed_count=0,
                    skipped_count=0,
                    current_chat_title=None,
                    completed=False
                )
                
                # Send to central log channel and get message ID
                progress_msg_central_id = await send_central_log_return_id(initial_report)
                
                import time
                last_progress_edit_time = time.time()
                
                async def update_progress_msg(index: int, current_chat_title: Optional[str] = None, completed: bool = False):
                    nonlocal last_progress_edit_time
                    now_time = time.time()
                    # Throttle edits to at least 2 seconds, except for critical updates (first, last, completed)
                    if not completed and index > 0 and index < total_tasks and now_time - last_progress_edit_time < 2.0:
                        return
                    
                    report_text = build_progress_bar_report(
                        user_label=user_label,
                        send_mode=send_mode,
                        index=index,
                        total=total_tasks,
                        success_count=success_count,
                        failed_count=failed_count,
                        skipped_count=skipped_count + skipped_dedup,
                        current_chat_title=current_chat_title,
                        completed=completed
                    )
                    
                    # Update central progress log
                    if progress_msg_central_id:
                        await edit_central_log(progress_msg_central_id, report_text)
                            
                    last_progress_edit_time = now_time

                # 5. Process tasks with adaptive delays
                for i, (msg, group) in enumerate(tasks):
                    if not self.running: break
                    
                    if getattr(self, 'ads_updated', False):
                        self.logger.info("📢 Ad update detected mid-cycle! Aborting current task queue to send updated ads immediately.")
                        self.ads_updated = False
                        break

                    # Night mode check every 10 tasks
                    if i % 10 == 0 and i > 0 and await is_night_mode():
                        self.logger.info("🌙 Night Mode detected mid-cycle. Pausing...")
                        break
                    
                    chat_id = group.get("chat_id")
                    chat_title = group.get('chat_title', 'Unknown')
                    
                    # V6: Real-time pause/remove check
                    from db.database import get_database
                    _db = get_database()
                    _curr_group = await _db.groups.find_one({"user_id": self.user_id, "chat_id": chat_id})
                    if not _curr_group or not _curr_group.get("enabled", True):
                        self.logger.info(f"⏭️ Group {chat_title} paused/removed during cycle. Skipping.")
                        skipped_count += 1
                        await update_progress_msg(i + 1)
                        continue

                    # V6: Cycle deduplication
                    dedup_key = (msg.id, chat_id)
                    if dedup_key in self._sent_this_cycle:
                        skipped_dedup += 1
                        await update_progress_msg(i + 1)
                        continue
                    
                    # Update progress bar to show we are currently sending to this group
                    await update_progress_msg(i, current_chat_title=chat_title)
                    
                    # Enforce timing gap before sending
                    if last_send_time is not None:
                        # Determine which gap to use
                        if send_mode == "sequential" and last_msg_id is not None and msg.id != last_msg_id:
                            target_gap = self.adaptive_msg_gap.get_gap()
                            gap_type = "Msg Gap"
                        else:
                            target_gap = self.adaptive_group_gap.get_gap()
                            gap_type = "Group Gap"
                        
                        elapsed = (datetime.utcnow() - last_send_time).total_seconds()
                        if elapsed < target_gap:
                            sleep_time = target_gap - elapsed
                            await self.update_status(f"{gap_type} ({int(sleep_time)}s)")
                            try:
                                await asyncio.wait_for(self.wake_up_event.wait(), timeout=sleep_time)
                                if getattr(self, 'ads_updated', False):
                                    self.logger.info("📢 Ad update detected during delay! Aborting cycle immediately to send updated ads.")
                                    self.ads_updated = False
                                    break
                            except asyncio.TimeoutError:
                                pass

                    self.logger.info(f"📤 [{i+1}/{len(tasks)}] → {chat_title}")
                    await self.update_status(f"Sending ({i+1}/{len(tasks)})")
                    
                    success, flood_triggered, flood_wait = await self.forward_single_message(msg, group, copy_mode=copy_mode)
                    
                    # Update timing indicators for next task
                    last_send_time = datetime.utcnow()
                    last_msg_id = msg.id
                    
                    if not self.running:
                        self.logger.info("Aborting cycle loop: session is stopping (possibly due to circuit breaker).")
                        break
                    
                    if success:
                        success_groups.append(chat_title)
                        self._sent_this_cycle.add(dedup_key)
                        success_count += 1
                        health_success += 1
                    else:
                        failed_groups.append(chat_title)
                        failed_count += 1
                        health_failed += 1
                    
                    # Check account health and take a break if weak mid-cycle
                    is_weak, reason = await self.check_account_health(health_success, health_failed)
                    if is_weak:
                        cooldown_minutes = 15
                        self.logger.warning(f"⚠️ Account health is weak ({reason}). Taking a {cooldown_minutes}-minute break...")
                        from worker.utils import send_central_log, build_error_log
                        user_label = await self.get_user_label()
                        asyncio.create_task(send_central_log(build_error_log(
                            user_label,
                            chat_title,
                            "⚠️ ACCOUNT HEALTH WEAK",
                            f"Account is weak due to: {reason}. Taking a {cooldown_minutes}-minute safety break mid-cycle."
                        )))
                        await self.update_status(f"Health Break ({cooldown_minutes}m)")
                        await asyncio.sleep(cooldown_minutes * 60)
                        self.error_streak = 0
                        health_success = 0
                        health_failed = 0
                        await self.update_status(f"Sending ({i+1}/{len(tasks)})")
                    
                    await update_progress_msg(i + 1)
                    
                    if flood_triggered:
                        self.adaptive_group_gap.multiplier = min(self.adaptive_group_gap.multiplier * 1.1, 5.0)
                        await self.update_status(f"FloodWait ({flood_wait}s)")
                        await asyncio.sleep(flood_wait)
                
                # Update progress bar to completed state
                await update_progress_msg(total_tasks, completed=True)
                
                # 6. Cycle complete — metrics and report
                # Enforce fixed 20 min interval for free users
                if not is_premium:
                    actual_interval = 20
                else:
                    actual_interval = max(interval_minutes, MIN_INTERVAL_MINUTES)
                cycle_duration = (datetime.utcnow() - self._cycle_start_time).total_seconds()
                self._last_cycle_duration = cycle_duration
                
                if success_groups or failed_groups:
                    sends_24h_success = 0
                    sends_24h_total = 0
                    try:
                        from db.database import get_database
                        db = get_database()
                        since_24h = datetime.utcnow() - timedelta(hours=24)
                        sends_24h_total = await db.send_logs.count_documents({
                            "user_id": self.user_id,
                            "sent_at": {"$gte": since_24h}
                        })
                        sends_24h_success = await db.send_logs.count_documents({
                            "user_id": self.user_id,
                            "sent_at": {"$gte": since_24h},
                            "status": "success"
                        })
                    except Exception as stats_err:
                        self.logger.error(f"Error fetching 24h stats for cycle report: {stats_err}")

                    from worker.utils import send_central_log, build_cycle_report
                    user_label = await self.get_user_label()
                    report = build_cycle_report(
                        user_label, success_groups, failed_groups,
                        send_mode, actual_interval,
                        cycle_duration=cycle_duration, skipped=skipped_dedup,
                        sends_24h_success=sends_24h_success,
                        sends_24h_total=sends_24h_total
                    )
                    asyncio.create_task(send_central_log(report))
                
                self.logger.info(
                    f"✅ Cycle done in {cycle_duration:.0f}s | "
                    f"✓{len(success_groups)} ✗{len(failed_groups)} | "
                    f"Next in {actual_interval}m"
                )
                
                # Reset PeerFlood counter on successful cycle
                if len(success_groups) > 0:
                    self._peer_flood_count = 0
                    # Auto-recover groups paused due to account restrictions
                    try:
                        from models.group import resume_account_paused_groups
                        recovered = await resume_account_paused_groups(self.user_id)
                        if recovered > 0:
                            self.logger.info(f"🔄 Auto-recovered {recovered} groups paused by account restriction")
                    except Exception as e:
                        self.logger.warning(f"Group recovery check failed: {e}")
                
                # 7. Wait for next cycle
                # Add random cycle start jitter (+/- 2 minutes) to make the schedule look organic
                jitter = random.randint(-120, 120)
                wait_seconds = max(60, (actual_interval * 60) + jitter)
                elapsed = 0
                while elapsed < wait_seconds and self.running:
                    if await is_night_mode():
                        break
                    
                    # Check if global branding check was requested by admin
                    global_settings = await get_cached_global_settings()
                    force_check_at = global_settings.get("force_branding_check_at")
                    if force_check_at:
                        if (not self.last_global_branding_check_at) or (force_check_at > self.last_global_branding_check_at):
                            self.logger.info("Global branding check requested by admin! Interrupted sleep.")
                            break

                    # If we were premium, check if our plan expired mid-sleep
                    if is_premium:
                        still_premium = await self._cached_is_plan_active()
                        if not still_premium:
                            self.logger.info("Plan expired mid-sleep. Interrupted sleep to enforce branding.")
                            break

                    rem_min = int((wait_seconds - elapsed) / 60)
                    await self.update_status(f"Next cycle in {rem_min}m")
                    sleep_chunk = min(60, wait_seconds - elapsed)
                    await asyncio.sleep(sleep_chunk)
                    elapsed += sleep_chunk

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.error_streak += 1
                self.logger.error(f"Loop error (streak {self.error_streak}): {e}")
                await self.update_status("Error (Retrying)")
                backoff = min(60 * self.error_streak, 600)
                await asyncio.sleep(backoff)
    
    async def get_all_saved_messages(self) -> list:
        """
        Fetch ALL Saved Messages (excluding command messages and service messages).
        Enforces default message policy:
        - If NO custom ad is set by user, set default ad message in Saved Messages.
        - If custom ad IS set by user, remove the default ad message from Saved Messages.
        """
        try:
            default_text = DEFAULT_AD_MESSAGE.strip()
            raw_messages = []
            STATUS_PREFIXES = (".", "✅", "🗑️", "⏳", "❌", "⚠️", "📊", "🔴", "⚪", "●", "📋")
            async for msg in self.client.iter_messages('me', limit=100):
                # Skip command messages and warning notifications
                if msg.text:
                    stripped = msg.text.strip()
                    if (stripped.startswith(STATUS_PREFIXES) or 
                        "Free Version Paused" in stripped or 
                        "remain joined" in stripped):
                        continue
                # Skip MessageService (calls, pins, joins — cannot be forwarded)
                if hasattr(msg, 'action') and msg.action is not None:
                    continue
                # Skip messages with no content at all
                if not msg.text and not msg.media:
                    continue
                raw_messages.append(msg)

            default_msgs = []
            custom_msgs = []
            for msg in raw_messages:
                msg_text = (msg.text or "").strip()
                if msg_text == default_text or "This is an automated advertising bot." in msg_text or "I am Free Message Bot" in msg_text:

                    default_msgs.append(msg)
                else:
                    custom_msgs.append(msg)

            # Rule 1: Custom message(s) set by user AND default message exists -> Remove default message(s)
            if custom_msgs and default_msgs:
                to_delete = [m.id for m in default_msgs]
                self.logger.info(f"Custom ad set by user. Auto-removing default message (ID(s): {to_delete})...")
                try:
                    await self.client.delete_messages('me', to_delete)
                except Exception as del_err:
                    self.logger.warning(f"Error deleting default ad message: {del_err}")
                messages = custom_msgs
                messages.reverse()
                return messages

            # Rule 2: No custom messages AND no default message exists -> Set default message
            if not custom_msgs and not default_msgs:
                self.logger.info("No ad message set by user. Setting default message in Saved Messages...")
                try:
                    new_def_msg = await self.client.send_message('me', DEFAULT_AD_MESSAGE)
                    messages = [new_def_msg]
                    return messages
                except Exception as send_err:
                    self.logger.error(f"Error setting default ad message: {send_err}")
                    return []

            # Rule 3: Returning existing active messages
            messages = custom_msgs if custom_msgs else default_msgs
            messages.reverse()
            return messages
        except Exception as e:
            logger.error(f"[User {self.user_id}] Error fetching saved messages: {e}")
            return []

    
    async def _resolve_entity(self, chat_id: int, chat_title: str, message_id: int) -> Optional[Any]:
        """
        Resolves a chat_id into a Telethon entity with smart caching.
        Saves failures to self._failed_entities to avoid redundant iter_dialogs calls.
        Returns the entity if found/valid, or None (logging and marking group failure in DB).
        """
        # 1. Check positive cache
        if chat_id in self._entity_cache:
            return self._entity_cache[chat_id]
            
        # 2. Check negative cache (cooldown: 1 hour)
        now = datetime.utcnow()
        if chat_id in self._failed_entities:
            fail_time, fail_reason = self._failed_entities[chat_id]
            if now - fail_time < timedelta(hours=1):
                self.logger.debug(f"Entity resolution for {chat_title} ({chat_id}) skipped due to cached failure: {fail_reason}")
                return None
        
        # 3. Resolve entity
        try:
            entity = None
            try:
                entity = await self.client.get_entity(chat_id)
            except (ValueError, TypeError):
                # If chat_id is positive integer or missing -100 prefix, try converting to supergroup ID (-100...)
                if isinstance(chat_id, int) and chat_id > 0:
                    try:
                        channel_id = int(f"-100{chat_id}")
                        entity = await self.client.get_entity(channel_id)
                    except Exception:
                        pass
                
                if not entity:
                    from telethon.tl.types import PeerChannel
                    if isinstance(chat_id, int) and chat_id > 0:
                        try:
                            entity = await self.client.get_entity(PeerChannel(chat_id))
                        except Exception:
                            pass
                
                if not entity:
                    raise ValueError(f"Could not resolve entity for {chat_id}")

            self._entity_cache[chat_id] = entity
            return entity
        except (ChannelInvalidError, UsernameNotOccupiedError, UsernameInvalidError, InviteHashExpiredError) as e:
            err_name = type(e).__name__
            self.logger.warning(f"❌ Pre-check failed: {chat_title} is invalid ({err_name}). Removing.")
            self._failed_entities[chat_id] = (now, f"Invalid group: {err_name}")
            asyncio.create_task(remove_group(self.user_id, chat_id))
            asyncio.create_task(self.log_send(chat_id, message_id, "removed", f"Pre-check: {err_name}"))
            return None
        except (ChatWriteForbiddenError, ChannelPrivateError, ChatAdminRequiredError, UserBannedInChannelError) as e:
            err_name = type(e).__name__
            self.logger.warning(f"❌ Pre-check: {chat_title} restricted ({err_name}). Removing.")
            self._failed_entities[chat_id] = (now, f"Restricted: {err_name}")
            asyncio.create_task(remove_group(self.user_id, chat_id))
            asyncio.create_task(self.log_send(chat_id, message_id, "removed", f"Pre-check: {err_name}"))
            return None
        except ValueError:
            # Private group not in cache — scan dialogs to find it
            self.logger.info(f"Entity not cached for {chat_id}, scanning dialogs...")
            try:
                entity = None
                async for dialog in self.client.iter_dialogs(limit=300):
                    d_peer_id = utils.get_peer_id(dialog.entity) if hasattr(utils, 'get_peer_id') else dialog.id
                    if (dialog.id == chat_id or 
                        d_peer_id == chat_id or 
                        str(dialog.id) == str(chat_id) or 
                        (isinstance(chat_id, int) and str(dialog.id) == f"-100{chat_id}") or
                        str(dialog.id).endswith(str(abs(chat_id)))):
                        entity = dialog.entity
                        break
                if entity:
                    self._entity_cache[chat_id] = entity
                    return entity
                else:
                    raise ValueError("Not found in dialogs either")
            except Exception as dial_e:
                self.logger.warning(f"Could not resolve entity for {chat_title} ({chat_id}): {dial_e}. Marking failing.")
                self._failed_entities[chat_id] = (now, f"Value/Dialog error: {dial_e}")
                asyncio.create_task(mark_group_failing(self.user_id, chat_id, f"Entity error: {dial_e}"))
                asyncio.create_task(self.log_send(chat_id, message_id, "failed", f"Entity error: {dial_e}"))
                return None
        except Exception as e:
            self.logger.warning(f"Could not resolve entity for {chat_id}: {e}")
            self._failed_entities[chat_id] = (now, f"Unexpected error: {e}")
            asyncio.create_task(self.log_send(chat_id, message_id, "failed", f"Entity error: {e}"))
            return None

    async def _activate_circuit_breaker(self, cooldown_until: datetime, reason: str):
        """
        Updates session in database with cooldown_until and stops the sender loop.
        Disconnects the client cleanly.
        """
        try:
            from db.database import get_database
            db = get_database()
            await db.sessions.update_one(
                {"user_id": self.user_id, "phone": self.phone},
                {"$set": {
                    "cooldown_until": cooldown_until,
                    "worker_status": f"Cooldown ({reason})",
                    "status_updated_at": datetime.utcnow()
                }}
            )
            self.logger.info(f"🔌 Circuit breaker activated. Session cooling down until {cooldown_until} UTC. Stopping loop.")
            self.running = False
            if self.client:
                await self.client.disconnect()
        except Exception as e:
            self.logger.error(f"Error activating circuit breaker: {e}")

    async def get_user_label(self) -> str:
        """
        Get a label for the user in logs:
        - Paid Upgraded Premium users: Full Name (@user_username) [💎 PREMIUM] (ID: user_id)
        - Free users & .free grants: Full Name (@user_username) ϟ Via @bot_username (ID: user_id)
        Ensures user's real Telegram @username is shown and distinct from bot username.
        """
        if (not getattr(self, "_profile_fetched", False)) and self.client:
            try:
                me = await self.client.get_me()
                if me:
                    self.first_name = me.first_name or ""
                    self.last_name = me.last_name or ""
                    self.username = me.username or ""
                    self._profile_fetched = True
            except Exception as e:
                self.logger.warning(f"Error fetching profile details: {e}")
        
        # Fallback: Check DB if username is missing
        if not getattr(self, "username", ""):
            try:
                from db.database import get_database
                db = get_database()
                user_doc = await db.users.find_one({"user_id": self.user_id})
                if user_doc:
                    if not getattr(self, "first_name", ""):
                        self.first_name = user_doc.get("first_name", "")
                    if not getattr(self, "last_name", ""):
                        self.last_name = user_doc.get("last_name", "")
                    self.username = user_doc.get("username", "")
            except Exception:
                pass

        first_name = getattr(self, "first_name", "")
        last_name = getattr(self, "last_name", "")
        
        # Clean any promo suffix if present in last_name for display
        clean_first = re.sub(r'(?:◕|ϟ|⚡|\bVɪᴀ\b|\bVia\b|\bBʏ\b|\bBY\b|\bBy\b)\s*@[A-Za-z0-9_]+', '', first_name, flags=re.IGNORECASE).strip()
        clean_last = re.sub(r'(?:◕|ϟ|⚡|\bVɪᴀ\b|\bVia\b|\bBʏ\b|\bBY\b|\bBy\b)\s*@[A-Za-z0-9_]+', '', last_name, flags=re.IGNORECASE).strip()
        for old_suffix in [
            "Bʏ @PhiloBots", "Bʏ @SpinifyAdsBot", "Bʏ @automessageschedulerBot",
            "BY @PhiloBots", "BY @SpinifyAdsBot", "BY @automessageschedulerBot",
            "By @PhiloBots", "By @SpinifyAdsBot", "By @automessageschedulerBot",
            "◕ @PhiloBots", "◕ @SpinifyAdsBot", "◕ @automessageschedulerBot",
            "ϟ @PhiloBots", "ϟ @SpinifyAdsBot", "ϟ @automessageschedulerBot",
            "ϟ Vɪᴀ @SpinifyAdsBot", "ϟ Vɪᴀ @PhiloBots", "ϟ Vɪᴀ @automessageschedulerBot",
            "Vɪᴀ @SpinifyAdsBot", "Vɪᴀ @PhiloBots", "Vɪᴀ @automessageschedulerBot",
            "Via @SpinifyAdsBot", "Via @PhiloBots", "Via @automessageschedulerBot",
        ]:
            clean_first = clean_first.replace(old_suffix, "").strip()
            clean_last = clean_last.replace(old_suffix, "").strip()

        full_name = f"{clean_first} {clean_last}".strip() or "User"
        user_uname = getattr(self, "username", "").lstrip("@")
        user_tag = f"(@{user_uname})" if user_uname else ""

        is_paid_upgrade = False
        try:
            from models.plan import get_plan
            user_plan = await get_plan(self.user_id)
            plan_type = (user_plan.get("plan_type") or "").lower() if user_plan else ""
            is_paid_upgrade = (
                self.user_id == OWNER_ID or
                (await self._cached_is_plan_active() and plan_type.startswith("paid"))
            )
        except Exception:
            pass
        
        if is_paid_upgrade:
            if user_tag:
                return f"{full_name} {user_tag} [💎 PREMIUM] (ID: {self.user_id})"
            return f"{full_name} [💎 PREMIUM] (ID: {self.user_id})"
        else:
            bot_uname = (MAIN_BOT_USERNAME or "SpinifyAdsBot").lstrip("@")
            if not bot_uname or bot_uname.lower() in ["automessageschedulerbot", "philobots"]:
                bot_uname = "SpinifyAdsBot"
            if user_tag:
                return f"{full_name} {user_tag} Bʏ @{bot_uname} (ID: {self.user_id})"
            return f"{full_name} Bʏ @{bot_uname} (ID: {self.user_id})"

    async def log_send(self, chat_id: int, saved_msg_id: int, status: str = "success", error: Optional[str] = None):
        """Log sending attempt in DB and notify central log channel."""
        await db_log_send(self.user_id, chat_id, saved_msg_id, status, error, phone=self.phone)
        
        if status == "removed":
            return
            
        emoji = "🟢" if status == "success" else "🔴" if status in ("failed", "error", "removed", "failing") else "🟡"
        msg_status = status.upper()
        
        chat_title = "Unknown"
        if chat_id in self._entity_cache:
            entity = self._entity_cache[chat_id]
            chat_title = getattr(entity, 'title', None) or getattr(entity, 'first_name', None) or "Unknown"
            
        user_label = await self.get_user_label()

        # Notify central LOG_CHANNEL_ID (using HTML format for the bot)
        try:
            chat_info_html = f"Chat: <code>{chat_id}</code>" if chat_title == "Unknown" else f"Group: <b>{chat_title}</b> (<code>{chat_id}</code>)"
            central_log_text = (
                f"{emoji} <b>[LOG ENTRY]</b> {msg_status}\n"
                f"├ User: <b>{user_label}</b>\n"
                f"├ {chat_info_html}\n"
                f"├ Saved Msg ID: <code>{saved_msg_id}</code>\n"
            )
            if error:
                central_log_text += f"└ Error: <code>{error}</code>"
            else:
                central_log_text += f"└ Action completed successfully."
                
            from worker.utils import send_central_log
            asyncio.create_task(send_central_log(central_log_text))
        except Exception as cle:
            self.logger.warning(f"Failed to send to central log channel: {cle}")


    async def forward_single_message(self, message, group: dict, copy_mode: bool = False) -> tuple:
        """
        Forward or copy a single message to a single group.
        Returns: (success: bool, flood_triggered: bool, flood_wait_seconds: int)
        """
        chat_id = group.get("chat_id")
        chat_title = group.get("chat_title", "Unknown")
        
        try:
            # ── STEP 1: Pre-validate entity (PREVENTS most errors) ──────────
            entity = await self._resolve_entity(chat_id, chat_title, message.id)
            if not entity:
                return (False, False, 0)
            
            # ── V6: Dynamic organic pre-send action (100% chance, 1.5-3s) ─
            action_type = 'typing'
            if message.media:
                media_class = type(message.media).__name__
                if "Photo" in media_class:
                    action_type = 'photo'
                elif "Document" in media_class:
                    doc = getattr(message.media, 'document', None)
                    mime = getattr(doc, 'mime_type', '') if doc else ''
                    if 'video' in mime:
                        action_type = 'video'
                    elif 'audio' in mime or 'ogg' in mime:
                        action_type = 'audio'
                    else:
                        action_type = 'document'
                else:
                    action_type = 'document'
            
            try:
                async with self.client.action(entity, action_type):
                    await asyncio.sleep(random.uniform(1.5, 3.0))
            except Exception:
                pass

            # ── STEP 6: Topic Awareness ──────────────────────────────────────
            topic_id = group.get("topic_id")

            # ── STEP 7: Send the message ─────────────────────────────────────
            # Safeguard: skip empty messages (no text and no media)
            if not message.text and not message.media:
                self.logger.warning("Skipping empty message")
                return (False, False, 0)

            # Get daily sequence number for ad
            seq_num = await get_next_ad_sequence_number(self.user_id, self.phone)
            orig_text = message.text or ""
            ad_text = (orig_text + f"\n\nID = #{seq_num}") if orig_text else f"ID = #{seq_num}"

            # Try sending message (with topic support & fallback)
            try:
                await self.client.send_message(
                    entity=entity,
                    message=ad_text,
                    file=message.media,
                    formatting_entities=message.entities if orig_text else None,
                    reply_to=topic_id
                )
            except RPCError as send_err:
                err_str = str(send_err).upper()
                # 1. Topic closed or invalid reply_to fallback
                if topic_id and ("TOPIC_CLOSED" in err_str or "REPLY_MESSAGE_ID_INVALID" in err_str or "TOPIC_DELETED" in err_str):
                    self.logger.warning(f"Topic {topic_id} closed/invalid in {chat_title}, trying fallback to main chat...")
                    await self.client.send_message(
                        entity=entity,
                        message=ad_text,
                        file=message.media,
                        formatting_entities=message.entities if orig_text else None
                    )
                # 2. Expired media / file reference invalid retry
                elif message.media and ("MESSAGE_ID_INVALID" in err_str or "FILE_REFERENCE_EXPIRED" in err_str or "OPERATION ON SUCH MESSAGE" in err_str):
                    self.logger.info(f"Media reference expired, re-fetching Saved Message {message.id}...")
                    fresh_msg = await self.client.get_messages("me", ids=message.id)
                    if fresh_msg and fresh_msg.media:
                        await self.client.send_message(
                            entity=entity,
                            message=ad_text,
                            file=fresh_msg.media,
                            formatting_entities=fresh_msg.entities if orig_text else None,
                            reply_to=topic_id
                        )
                    else:
                        raise send_err
                else:
                    raise send_err

            log_action = f"Sent [ID = #{seq_num}]" + (f" (Topic {topic_id})" if topic_id else "")
            
            self.logger.info(f"{log_action} message {message.id} to {chat_title}")
            self.adaptive_group_gap.on_success()
            self.adaptive_msg_gap.on_success()
            self.error_streak = 0
            
            # Log in background to not block the main sender loop
            asyncio.create_task(self.log_send(
                chat_id=chat_id,
                saved_msg_id=message.id,
                status="success"
            ))
            
            # Clear failing status on success
            asyncio.create_task(clear_group_fail(self.user_id, chat_id))
            return (True, False, 0)
            
        except Exception as e:
            # Map the error to clean output
            mapped = map_telegram_error(e)
            err_code = mapped["error_code"]
            disp_msg = mapped["display_message"]
            severity = mapped["severity"]
            
            # Special core logic handlers for certain errors
            if err_code == "FLOOD_WAIT":
                seconds = getattr(e, 'seconds', 30)
                self.logger.warning(f"FloodWait: {seconds}s on {chat_title}")
                self.adaptive_group_gap.on_flood(seconds)
                self.adaptive_msg_gap.on_flood(seconds)
                asyncio.create_task(self.log_send(chat_id, message.id, "flood_wait", disp_msg))
                
                # Circuit breaker check: if flood wait is over 5 minutes (300 seconds)
                if seconds > 300:
                    self.logger.error(f"🚨 Long FloodWait ({seconds}s) on {chat_title} — activating circuit breaker.")
                    cooldown_until = datetime.utcnow() + timedelta(seconds=int(seconds * 1.1) + 5)
                    asyncio.create_task(self._activate_circuit_breaker(cooldown_until, f"FloodWait ({seconds}s)"))
                    # Auto-appeal to SpamBot for long flood waits
                    from shared.spambot_appeal import appeal_to_spambot
                    asyncio.create_task(appeal_to_spambot(self.client, self.phone, self.user_id, f"Long FloodWait ({seconds}s)"))
                
                return (False, True, int(seconds * 1.1) + 5)
                
            elif err_code == "PEER_FLOOD":
                self.error_streak += 1
                self._peer_flood_count += 1
                cooldown_hours = min(2 ** (self._peer_flood_count - 1), 8)
                cooldown_secs = cooldown_hours * 3600
                self.logger.error(f"🚨 PeerFlood on {chat_title} — cooldown {cooldown_hours}h (flood #{self._peer_flood_count})")
                asyncio.create_task(self.log_send(chat_id, message.id, "peer_flood", disp_msg))
                
                from worker.utils import send_central_log, build_error_log
                user_label = await self.get_user_label()
                asyncio.create_task(send_central_log(build_error_log(
                    user_label, chat_title, "🚨 PEER FLOOD", f"Account restricted — {cooldown_hours}h cooldown"
                )))
                
                # Auto-appeal to SpamBot to request limit removal
                from shared.spambot_appeal import appeal_to_spambot
                asyncio.create_task(appeal_to_spambot(self.client, self.phone, self.user_id, f"PeerFlood (flood #{self._peer_flood_count})"))
                
                # Activate circuit breaker for PeerFlood
                cooldown_until = datetime.utcnow() + timedelta(seconds=cooldown_secs)
                asyncio.create_task(self._activate_circuit_breaker(cooldown_until, f"PeerFlood ({cooldown_hours}h)"))
                
                return (False, True, cooldown_secs)
                
            elif err_code == "ACCOUNT_DEACTIVATED":
                self.logger.error(f"🛑 Account {self.phone} is deactivated by Telegram!")
                asyncio.create_task(mark_session_disabled(self.user_id, self.phone, reason="User Deactivated"))
                asyncio.create_task(self.log_send(chat_id, message.id, "failed", disp_msg))
                from worker.utils import send_central_log, build_error_log
                user_label = await self.get_user_label()
                asyncio.create_task(send_central_log(build_error_log(user_label, chat_title, "🛑 ACCOUNT DEACTIVATED", "Session permanently disabled")))
                self.running = False
                return (False, False, 0)
                
            elif err_code == "TOPIC_CLOSED":
                self.logger.warning(f"⚠️ Topic closed in {chat_title} — marking failing")
                asyncio.create_task(mark_group_failing(self.user_id, chat_id, "Topic Closed"))
                asyncio.create_task(self.log_send(chat_id, message.id, "skipped", disp_msg))
                return (False, False, 0)

            elif err_code == "DISCUSSION_GROUP_REQUIRED":
                self.logger.warning(f"⚠️ Discussion group required for {chat_title} — marking failing")
                asyncio.create_task(mark_group_failing(self.user_id, chat_id, "Must Join Discussion Group"))
                asyncio.create_task(self.log_send(chat_id, message.id, "skipped", disp_msg))
                return (False, False, 0)

            elif err_code in ["LINK_INVALID", "PERMISSION_DENIED"]:
                self.logger.warning(f"❌ Removing group {chat_title}: {disp_msg} ({err_code})")
                asyncio.create_task(remove_group(self.user_id, chat_id))
                asyncio.create_task(self.log_send(chat_id, message.id, "removed", disp_msg))
                return (False, False, 0)

            elif err_code in ["MESSAGE_DELETED", "EMPTY_MESSAGE"]:
                self.logger.warning(f"⚠️ {disp_msg} — skipping {chat_title}")
                asyncio.create_task(self.log_send(chat_id, message.id, "skipped", disp_msg))
                return (False, False, 0)

            elif err_code == "SLOWMODE":
                self.logger.warning(f"⏳ Slow mode in {chat_title} — skipping for now")
                asyncio.create_task(self.log_send(chat_id, message.id, "skipped", disp_msg))
                return (False, False, 0)

            elif getattr(e, 'message', '') == "FROZEN_PARTICIPANT_MISSING":
                self.logger.error(f"🛑 Account {self.phone} is FROZEN by Telegram!")
                asyncio.create_task(mark_group_failing(self.user_id, chat_id, "Account Frozen"))
                return (False, False, 0)

            elif isinstance(e, RPCError):
                self.logger.warning(f"⚠️ RPC error in {chat_title}: {disp_msg} ({err_code}). Marking failing.")
                asyncio.create_task(mark_group_failing(self.user_id, chat_id, disp_msg))
                asyncio.create_task(self.log_send(chat_id, message.id, "failed", disp_msg))
                return (False, False, 0)

            else:
                self.error_streak += 1
                self.logger.error(f"Error forwarding to {chat_title}: {disp_msg} ({type(e).__name__})")
                asyncio.create_task(self.log_send(chat_id, message.id, "failed", disp_msg))
                return (False, False, 0)

    async def _connection_watchdog(self):
        """Background heartbeat with smart authorization check."""
        while self.running:
            try:
                await asyncio.sleep(600) # Every 10 minutes
                if self.client and self.client.is_connected():
                    # 1. Network Ping
                    await self.client.get_me()
                    
                    # 2. Authorization Check (Prevents ghost runs)
                    if not await self.client.is_user_authorized():
                        self.logger.error("Session revoked or unauthorized. Stopping sender.")
                        self.running = False
                        await self.update_status("🔴 Session Revoked")
                        return

                    self.last_heartbeat = datetime.utcnow()
                elif self.running:
                    self.logger.warning("Watchdog detected disconnected client. Reconnecting...")
                    await self.client.connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Watchdog heartbeat error: {e}")

    async def _branding_enforcement_loop(self):
        """Background task that runs branding enforcement check every 60 seconds (1 minute)."""
        self.logger.info("Branding enforcement background loop started.")
        while self.running:
            try:
                await self._enforce_profile_branding()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in branding background loop: {e}")
            await asyncio.sleep(60)
