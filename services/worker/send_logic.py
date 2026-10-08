"""
Core send logic — extracted from the existing worker/sender.py.

Contains the actual Telegram message-sending logic that workers execute:
  - Entity pre-validation
  - Human-like typing simulation
  - FloodWait / PeerFlood / permission error handling
  - Group auto-pause / auto-remove
"""

import asyncio
import random
import logging
from typing import Tuple

from telethon import TelegramClient
from telethon.tl.types import InputPeerSelf
from telethon.errors import (
    FloodWaitError,
    PeerFloodError,
    ChatWriteForbiddenError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    UserBannedInChannelError,
    InputUserDeactivatedError,
    RPCError,
    ChannelInvalidError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
    InviteHashExpiredError,
)

from models.group import remove_group, toggle_group, mark_group_failing, clear_group_fail
from models.job import log_job_event
from models.ad_sequence import get_next_ad_sequence_number
from shared.telegram_error_mapper import map_telegram_error
from core.database import get_database

logger = logging.getLogger(__name__)


async def send_message_to_group(
    client: TelegramClient,
    job_id: str,
    user_id: int,
    phone: str,
    message_id: int,
    group_id: int,
    copy_mode: bool = False,
) -> Tuple[str, int]:
    """
    Send a single message to a single group.

    Returns:
        (status, flood_wait_seconds)
        status is one of: "sent", "failed", "flood", "removed", "paused", "deactivated"
        flood_wait_seconds > 0 if a FloodWaitError was encountered.
    """
    try:
        # ── 1. Pre-validate entity ──────────────────────────────────────
        entity = None
        try:
            try:
                entity = await client.get_entity(group_id)
            except (ValueError, TypeError):
                if isinstance(group_id, int) and group_id > 0:
                    try:
                        channel_id = int(f"-100{group_id}")
                        entity = await client.get_entity(channel_id)
                    except Exception:
                        pass
                if not entity:
                    from telethon.tl.types import PeerChannel
                    if isinstance(group_id, int) and group_id > 0:
                        try:
                            entity = await client.get_entity(PeerChannel(group_id))
                        except Exception:
                            pass
                if not entity:
                    raise ValueError(f"Could not get entity for {group_id}")

        except (ChannelInvalidError, UsernameNotOccupiedError,
                UsernameInvalidError, InviteHashExpiredError) as e:
            logger.warning(f"❌ Group {group_id} invalid ({type(e).__name__}). Removing.")
            asyncio.create_task(remove_group(user_id, group_id))
            await log_job_event(job_id, user_id, phone, group_id, message_id,
                                "removed", f"Link dead: {type(e).__name__}")
            return ("removed", 0)

        except (ChatWriteForbiddenError, ChannelPrivateError,
                ChatAdminRequiredError, UserBannedInChannelError) as e:
            logger.warning(f"❌ Group {group_id} restricted ({type(e).__name__}). Removing.")
            asyncio.create_task(remove_group(user_id, group_id))
            await log_job_event(job_id, user_id, phone, group_id, message_id,
                                "removed", f"Restricted: {type(e).__name__}")
            return ("removed", 0)

        except ValueError:
            # Not in cache! Try resolving raw ID or via recent dialogs
            logger.info(f"Entity not in cache for {group_id}, attempt recovery...")
            try:
                # Try fetching from dialogs summary
                from telethon import utils
                async for dialog in client.iter_dialogs(limit=200):
                    d_peer_id = utils.get_peer_id(dialog.entity) if hasattr(utils, 'get_peer_id') else dialog.id
                    if (dialog.id == int(group_id) or 
                        d_peer_id == int(group_id) or 
                        str(dialog.id) == str(group_id) or 
                        (isinstance(group_id, int) and str(dialog.id) == f"-100{group_id}") or
                        str(dialog.id).endswith(str(abs(int(group_id))))):
                        entity = dialog.entity
                        break
            except Exception: pass
            
            if not entity:
                logger.warning(f"🚨 Entity {group_id} not found after recovery attempts. Marking failing.")
                asyncio.create_task(mark_group_failing(user_id, group_id, "Entity Not Found"))
                await log_job_event(job_id, user_id, phone, group_id, message_id,
                                    "failed", "Entity Not Found")
                return ("failed", 0)

        except Exception as e:
            logger.warning(f"Unexpected entity resolve error for {group_id}: {e}")
            return ("failed", 0)

        # ── 1.5 Check if target is a broadcast channel (auto-remove) ─────
        from telethon.tl.types import Channel
        if isinstance(entity, Channel) and getattr(entity, 'broadcast', False):
            logger.warning(f"❌ Target {group_id} is a broadcast channel. Auto-removing from sending list.")
            asyncio.create_task(remove_group(user_id, group_id))
            await log_job_event(job_id, user_id, phone, group_id, message_id,
                                "removed", "Target is a Channel (Broadcast)")
            return ("removed", 0)

        # ── 2. Stealth: Read History Simulation (Level Up) ──────────
        # Mimics a user opening the group before posting.
        if random.random() > 0.4:
            try:
                from telethon.tl.functions.messages import ReadHistoryRequest
                await client(ReadHistoryRequest(peer=entity, max_id=0))
                await asyncio.sleep(random.uniform(2.0, 5.0))
            except Exception: pass

        # ── 3. SlowMode & Permission Pre-Check (Level Up) ────────────
        try:
            if hasattr(entity, 'broadcast') or getattr(entity, 'megagroup', False):
                from telethon.tl.functions.channels import GetFullChannelRequest
                full_chat_info = await client(GetFullChannelRequest(entity))
                slowmode = getattr(full_chat_info.full_chat, 'slowmode_seconds', 0)
                if slowmode and slowmode > 300: # If > 5 mins, skip for now
                    logger.info(f"⏳ Group {group_id} has high slowmode ({slowmode}s). Skipping.")
                    return ("failing", 0)
        except Exception: pass

        # ── 4. Human-like typing ────────────────────────────────────
        if random.random() > 0.1:
            try:
                typing_duration = random.uniform(4, 9)
                async with client.action(entity, "typing"):
                    await asyncio.sleep(typing_duration)
            except Exception:
                pass  # Typing failure is harmless

        # ── 5. Micro-delay ──────────────────────────────────────────
        await asyncio.sleep(random.uniform(1.0, 3.0))

        # ── 6. Topic Awareness ──────────────────────────────────────
        db = get_database()
        group_doc = await db.groups.find_one({"user_id": user_id, "chat_id": group_id})
        topic_id = group_doc.get("topic_id") if group_doc else None

        # ── 7. Send the message ─────────────────────────────────────────
        # Load the message from Saved Messages
        saved_msg = await client.get_messages("me", ids=message_id)
        if not saved_msg:
            await log_job_event(job_id, user_id, phone, group_id, message_id,
                                "failed", "Message not found in Saved Messages")
            return ("failed", 0)

        from shared.utils import is_system_or_command_message
        if is_system_or_command_message(saved_msg):
            logger.warning(f"Safeguard triggered: prevented system/status message {message_id} from being sent to group {group_id}")
            return ("failed", 0)

        if not saved_msg.text and not saved_msg.media:
            await log_job_event(job_id, user_id, phone, group_id, message_id,
                                "skipped", "Empty message")
            return ("failed", 0)

        # Get daily sequence number for ad
        seq_num = await get_next_ad_sequence_number(user_id, phone)
        orig_text = saved_msg.text or ""
        ad_text = (orig_text + f"\n\nID = #{seq_num}") if orig_text else f"ID = #{seq_num}"

        try:
            await client.send_message(
                entity=entity,
                message=ad_text,
                file=saved_msg.media,
                formatting_entities=saved_msg.entities if orig_text else None,
                reply_to=topic_id
            )
        except RPCError as send_err:
            err_str = str(send_err).upper()
            if topic_id and ("TOPIC_CLOSED" in err_str or "REPLY_MESSAGE_ID_INVALID" in err_str or "TOPIC_DELETED" in err_str):
                logger.warning(f"Topic {topic_id} closed/invalid in group {group_id}, trying fallback to main chat...")
                await client.send_message(
                    entity=entity,
                    message=ad_text,
                    file=saved_msg.media,
                    formatting_entities=saved_msg.entities if orig_text else None
                )
            elif saved_msg.media and ("MESSAGE_ID_INVALID" in err_str or "FILE_REFERENCE_EXPIRED" in err_str or "OPERATION ON SUCH MESSAGE" in err_str):
                logger.info(f"Media reference expired, re-fetching Saved Message {message_id}...")
                fresh_msg = await client.get_messages("me", ids=message_id)
                if fresh_msg and fresh_msg.media:
                    await client.send_message(
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

        await log_job_event(job_id, user_id, phone, group_id, message_id, "sent")
        # Clear any previous failing status on success
        asyncio.create_task(clear_group_fail(user_id, group_id))
        return ("sent", 0)

    except Exception as e:
        mapped = map_telegram_error(e)
        err_code = mapped["error_code"]
        disp_msg = mapped["display_message"]
        
        if err_code == "FLOOD_WAIT":
            seconds = getattr(e, 'seconds', 30)
            logger.warning(f"FloodWait: {seconds}s on group {group_id}")
            await log_job_event(job_id, user_id, phone, group_id, message_id, "flood", disp_msg)
            return ("flood", seconds)
            
        elif err_code == "PEER_FLOOD":
            logger.error(f"🚨 PeerFlood on group {group_id} — account restricted!")
            await log_job_event(job_id, user_id, phone, group_id, message_id, "flood", disp_msg)
            return ("flood", 7200)  # 2-hour cooldown
            
        elif err_code == "ACCOUNT_DEACTIVATED":
            logger.error(f"🛑 Account {phone} is deactivated!")
            from models.session import mark_session_disabled
            asyncio.create_task(mark_session_disabled(user_id, phone, reason="User Deactivated"))
            await log_job_event(job_id, user_id, phone, group_id, message_id, "failed", disp_msg)
            return ("deactivated", 0)
            
        elif err_code == "TOPIC_CLOSED":
            logger.warning(f"⚠️ Topic closed in group {group_id} — marking failing")
            asyncio.create_task(mark_group_failing(user_id, group_id, "Topic Closed"))
            await log_job_event(job_id, user_id, phone, group_id, message_id, "skipped", disp_msg)
            return ("failed", 0)

        elif err_code == "DISCUSSION_GROUP_REQUIRED":
            logger.warning(f"⚠️ Discussion group required for group {group_id} — marking failing")
            asyncio.create_task(mark_group_failing(user_id, group_id, "Must Join Discussion Group"))
            await log_job_event(job_id, user_id, phone, group_id, message_id, "skipped", disp_msg)
            return ("failed", 0)

        elif err_code in ["LINK_INVALID", "PERMISSION_DENIED"]:
            logger.warning(f"❌ Removing group {group_id} due to permanent permission/link error: {disp_msg} ({err_code})")
            asyncio.create_task(remove_group(user_id, group_id))
            await log_job_event(job_id, user_id, phone, group_id, message_id, "removed", disp_msg)
            return ("removed", 0)

        elif err_code in ["MESSAGE_DELETED", "EMPTY_MESSAGE", "SLOWMODE"]:
            logger.warning(f"⚠️ {disp_msg} — skipping group {group_id}")
            await log_job_event(job_id, user_id, phone, group_id, message_id, "skipped", disp_msg)
            return ("failed", 0)

        elif isinstance(e, RPCError):
            logger.warning(f"⚠️ RPC error on group {group_id}: {disp_msg} ({err_code}). Marking failing.")
            asyncio.create_task(mark_group_failing(user_id, group_id, disp_msg))
            await log_job_event(job_id, user_id, phone, group_id, message_id, "failed", disp_msg)
            return ("failed", 0)
            
        else:
            logger.error(f"Error on group {group_id}: {disp_msg} ({type(e).__name__})")
            await log_job_event(job_id, user_id, phone, group_id, message_id, "failed", disp_msg)
            return ("failed", 0)
