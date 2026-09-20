"""
Command handler for processing dot commands from user's Saved Messages.
Commands are sent by user to their own Saved Messages and processed by Worker.
"""

import logging
import asyncio
import re
import random
from typing import Optional, List
from telethon import TelegramClient, utils
from telethon.errors import (
    ChannelPrivateError,
    ChannelInvalidError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
    InviteHashInvalidError,
    InviteHashExpiredError,
    UserAlreadyParticipantError,
)
from telethon.tl.types import InputPeerSelf, InputPeerChannel, InputPeerChat, Channel, Chat, DialogFilter, ChatInviteAlready, ChatInvite
from telethon.tl.functions.messages import GetDialogFiltersRequest, CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.functions.chatlists import CheckChatlistInviteRequest, JoinChatlistInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest

from core.config import MAX_GROUPS_PER_USER, MIN_INTERVAL_MINUTES
from models.session import get_session
from models.user import get_user_config, update_user_config
from models.group import get_user_groups, add_group, remove_group, get_group_count, toggle_group, mark_group_failing, clear_group_fail
from db.models import get_account_stats, get_recent_failed_logs
from models.plan import get_plan
from worker.utils import is_night_mode

logger = logging.getLogger(__name__)


async def process_command(client: TelegramClient, user_id: int, message, sender=None) -> bool:
    """
    Process a dot command from user's Saved Messages.
    Returns True if the message was a command and was processed.
    """
    if not message.text:
        return False
    
    text = message.text.strip()
    
    if not text.startswith("."):
        return False
    
    cmd = text.lower().split()[0]
    
    # Restrict premium commands for free version users
    from models.plan import is_plan_active
    from core.config import OWNER_ID
    issuer_id = getattr(message, "sender_id", user_id)
    is_premium = (user_id == OWNER_ID) or (issuer_id == OWNER_ID) or await is_plan_active(user_id)
    
    premium_commands = {
        ".interval", ".shuffle", ".copymode", ".sendmode", ".responder",
        ".folders", ".addfolder", ".nightmode", ".stats", ".health",
        ".check", ".rmpaused", ".addplan"
    }
    
    if cmd in premium_commands and not is_premium:
        await reply_to_command(client, message, "⚠️ **Premium Feature**\nThis command is restricted to Premium users. Upgrade your plan to unlock.")
        return True
        
    try:
        if cmd == ".help":
            await handle_help(client, user_id, message)
            return True
        elif cmd == ".status":
            await handle_status(client, user_id, message, text)
            return True
        elif cmd == ".stats":
            await handle_stats(client, user_id, message)
            return True
        elif cmd == ".health":
            await handle_health(client, user_id, message, text, sender)
            return True
        elif cmd == ".check":
            await handle_check(client, user_id, message, text, sender)
            return True
        elif cmd == ".userstatus":
            await handle_userstatus(client, user_id, message, text)
            return True
        elif cmd in (".addplan", ".free"):
            await handle_free(client, user_id, message, text)
            return True
        elif cmd == ".checkbrand":
            await handle_checkbrand(client, user_id, message, text)
            return True
        elif cmd == ".clearads":
            await handle_clearads(client, user_id, message, sender)
            return True
        elif cmd == ".setads":
            await handle_setads(client, user_id, message, sender)
            return True
        elif cmd in (".remove", ".rmad"):
            await handle_remove_ad(client, user_id, message, text, sender)
            return True
        elif cmd in (".join", ".joinfolder", ".addlist"):
            await handle_join(client, user_id, message, text)
            return True
        elif cmd == ".show":
            await handle_show(client, user_id, message)
            return True
        elif cmd == ".rmpaused":
            await handle_rmpaused(client, user_id, message)
            return True
        elif cmd == ".groups":
            await handle_groups(client, user_id, message)
            return True
        elif cmd == ".resume" or cmd == ".unpause" or cmd == ".start":
            await handle_resume(client, user_id, message)
            return True
        elif cmd == ".pause" or cmd == ".stop":
            await handle_pause(client, user_id, message)
            return True
        elif cmd == ".pauseall":
            await handle_pauseall(client, user_id, message)
            return True
        elif cmd == ".resumeall":
            await handle_resumeall(client, user_id, message)
            return True
        elif cmd == ".clear":
            await handle_clear(client, user_id, message)
            return True
        elif cmd == ".logs":
            await handle_logs(client, user_id, message)
            return True
        elif cmd == ".addgroup":
            await handle_addgroup(client, user_id, message, text)
            return True
        elif cmd == ".rmgroup":
            await handle_rmgroup(client, user_id, message, text)
            return True
        elif cmd == ".interval":
            await handle_interval(client, user_id, message, text)
            return True
        elif cmd == ".shuffle":
            await handle_shuffle(client, user_id, message, text)
            return True
        elif cmd == ".copymode":
            await handle_copymode(client, user_id, message, text)
            return True
        elif cmd == ".sendmode":
            await handle_sendmode(client, user_id, message, text)
            return True
        elif cmd == ".responder":
            await handle_responder(client, user_id, message, text)
            return True
        elif cmd == ".ping":
            await reply_to_command(client, message, "● Pong! Worker is active ⚡")
            return True
        elif cmd == ".nightmode":
            await handle_nightmode(client, user_id, message, text)
            return True
        elif cmd in (".setpfp", ".setpic"):
            await handle_setpfp(client, user_id, message)
            return True
        elif cmd == ".setallpfp":
            await handle_setallpfp(client, user_id, message)
            return True
        elif cmd == ".folders":
            await handle_folders(client, user_id, message)
            return True
        elif cmd == ".addfolder":
            await handle_addfolder(client, user_id, message, text)
            return True
    except Exception as e:
        logger.error(f"[User {user_id}] Command error: {e}")
        await reply_to_command(client, message, f"Error: {str(e)}")
    
    return False


async def reply_to_command(client: TelegramClient, message, text: str, auto_delete: bool = True, delete_delay: int = 30):
    """Send a reply to the message that triggered the command, auto-delete after delete_delay."""
    import asyncio
    reply = await message.reply(text, parse_mode="markdown")
    
    if auto_delete:
        async def _auto_delete():
            await asyncio.sleep(delete_delay)
            try:
                await reply.delete()
                await message.delete()
            except Exception:
                pass  # Message may already be deleted
        
        asyncio.create_task(_auto_delete())
    return reply


async def handle_help(client: TelegramClient, user_id: int, message):
    """Handle .help command with professional styling."""
    from core.config import MIN_INTERVAL_MINUTES
    text = (
        "💎 *KURUP ADS V6 ELITE — COMMANDS* 💎\n\n"
        "📢 *GROUP MANAGEMENT*\n"
        "├ `.addgroup [url]` — Add target group\n"
        "├ `.addfolder [name]` — Add Telegram folder\n"
        "├ `.rmgroup [idx]` — Remove group by index\n"
        "├ `.rmpaused` — Remove all paused groups\n"
        "├ `.pauseall` — Pause ALL groups at once\n"
        "├ `.resumeall` — Resume ALL groups at once\n"
        "├ `.clear` — Remove *EVERYTHING* from list\n"
        "├ `.groups` — Show your target list\n"
        "└ `.folders` — List your account folders\n\n"
        "⚙️ *WORKER SETTINGS*\n"
        "├ `.interval [min]` — Set loop delay (min: {min}m)\n"
        "├ `.shuffle on/off` — Randomize loop order\n"
        "├ `.copymode on/off` — Fresh message (No Forward tag)\n"
        "├ `.sendmode [pattern]` — `seq` | `rot` | `rand` \n"
        "├ `.responder [msg]` — Set auto-DM reply\n"
        "├ `.responder off` — Disable auto-DM\n"
        "└ `.nightmode on/off` — 12AM-6AM Automation\n\n"
        "⚡ *DIAGNOSTICS & CONTROL*\n"
        "├ `.status` — Live account dashboard\n"
        "├ `.health` — Live group health diagnostics\n"
        "├ `.check` — Live send diagnostic check\n"
        "├ `.stats` — Performance & Success rate\n"
        "├ `.logs` — Recent activity feed\n"
        "├ `.pause` — Global pause all groups\n"
        "├ `.resume` — Global resume all groups\n"
        "└ `.ping` — Connectivity test\n\n"
        "📢 *ADS MANAGEMENT*\n"
        "├ `.show` — Preview active saved ads\n"
        "├ `.setads` — Set ad into Saved Messages\n"
        "├ `.remove [ad_id]` — Remove specific ad by ID\n"
        "└ `.clearads` — Clear all Saved Messages / ads\n"
    ).format(min=MIN_INTERVAL_MINUTES)
    
    await reply_to_command(client, message, text)


async def handle_status(client: TelegramClient, user_id: int, message, text: str = ""):
    """Handle .status command with detailed information for THIS or ANOTHER account."""
    from core.config import OWNER_ID
    
    target_user_id = user_id
    parts = text.split()
    
    # Owner can check other users: .status <user_id>
    if len(parts) > 1 and user_id == OWNER_ID:
        try:
            target_user_id = int(parts[1])
        except ValueError:
            pass # Use self if invalid ID
            
    # Get specifically this account's session
    phone = getattr(client, 'phone', None)
    session = await get_session(target_user_id, phone if target_user_id == user_id else None)
    
    # Get plan (User-wide)
    plan = await get_plan(target_user_id)
    
    # Get config (User-wide)
    config = await get_user_config(target_user_id)
    
    # Get groups (all groups for this user)
    groups = await get_user_groups(target_user_id)
    total_groups = len(groups)
    enabled_groups = len([g for g in groups if g.get("enabled", True)])
    
    # Format plan info
    if plan:
        from datetime import datetime
        expires = plan.get("expires_at")
        if expires and expires > datetime.utcnow():
            days_left = (expires - datetime.utcnow()).days
            hours_left = ((expires - datetime.utcnow()).seconds // 3600)
            p_type = plan.get("plan_type", "premium")
            plan_type = "Free Trial" if p_type == "free_trial" else ("Free User" if p_type == "free_user" else p_type.title())
            plan_badge = "💎 PREMIUM"
            
            if days_left > 0:
                plan_status = f"🟢 Active — {days_left}d {hours_left}h left"
            else:
                plan_status = f"🟢 Active — {hours_left}h left"
        else:
            plan_status = "⚪ Free User (Paid Plan Expired)"
            plan_badge = "⚪ FREE USER"
            plan_type = "Free User"
    else:
        plan_status = "⚪ Free User (No Paid Plan)"
        plan_badge = "⚪ FREE USER"
        plan_type = "Free User"
    
    phone_display = session.get("phone", "Unknown") if session else ("Owner Check" if target_user_id != user_id else "Unknown")
    from core.config import DEFAULT_INTERVAL_MINUTES
    from models.plan import is_plan_active
    is_premium = await is_plan_active(target_user_id)
    interval = 20 if not is_premium else config.get("interval_min", DEFAULT_INTERVAL_MINUTES)
    
    # Setting indicators
    send_mode = config.get("send_mode", "sequential").title()
    
    total_reach = sum(g.get("member_count", 0) for g in groups)
    
    header = "📊 *WORKER DIAGNOSTICS*" if target_user_id == user_id else f"📊 *USER PROFILE: {target_user_id}*"
    
    from shared.utils import make_progress_bar
    from core.config import MAX_GROUPS_PER_USER
    groups_bar = make_progress_bar(enabled_groups, total_groups if total_groups > 0 else MAX_GROUPS_PER_USER)

    text = f"""{header}
    
📱 *ACCOUNT PROFILE*
├ Phone: {phone_display}
└ Status: 🟢 Connected

🏷️ *PLAN INFO*
├ Tier: {plan_type}
└ Status: {plan_status.replace('🟢', '●').replace('🔴', '○')}

⚡ *LIVE SETTINGS*
├ Interval: {interval}m
├ Send Mode: {send_mode}
├ Shuffle: {"🟢 ON" if config.get("shuffle_mode") else "⚫ OFF"}
├ Copy Mode: {"🟢 ON" if config.get("copy_mode") else "⚫ OFF"}
├ Auto-Responder: {"🟢 ON" if config.get("auto_reply_enabled") else "⚫ OFF"}
└ Night Mode: {await get_night_mode_label()}

👥 *GROUPS ({enabled_groups}/{total_groups})*
├ Progress: `{groups_bar}`
└ 📢 Potential Reach: {total_reach:,} members

Type `.help` for available commands
"""
    await reply_to_command(client, message, text)
    
async def handle_stats(client: TelegramClient, user_id: int, message):
    """Handle .stats command - show activity and sender health."""
    from db.models import get_account_stats, get_recent_failed_logs
    
    phone = getattr(client, 'phone', 'Unknown')
    stats = await get_account_stats(user_id, phone)
    recent_fails = await get_recent_failed_logs(user_id, phone, limit=5)
    
    today_sent = stats.get("today_sent", 0)
    today_success = stats.get("today_success", 0)
    today_rate = stats.get("today_rate", 0)
    total_sent = stats.get("total_sent", 0)
    overall_rate = stats.get("success_rate", 0)
    
    # Activity Level Badge
    if today_sent > 100: activity = "🔥 HIGH"
    elif today_sent > 10: activity = "⚡ ACTIVE"
    elif today_sent > 0: activity = "🟢 STABLE"
    else: activity = "⚪ IDLE"
    
    from shared.utils import make_progress_bar
    today_bar = make_progress_bar(today_success, today_sent) if today_sent > 0 else make_progress_bar(0, 100)
    overall_bar = make_progress_bar(int(overall_rate), 100)

    text = f"📈 *SENDER ACTIVITY: `{phone}`*\n"
    text += f"══════════════════════════\n\n"
    
    text += f"📊 *TODAY'S METRICS*\n"
    text += f"├ Activity: {activity}\n"
    text += f"├ Progress: `{today_bar}`\n"
    text += f"├ Transmitted: {today_sent} ads\n"
    text += f"├ Successful: {today_success}\n"
    text += f"└ Success Rate: {today_rate}%\n\n"
    
    text += f"🏆 *OVERALL HEALTH*\n"
    text += f"├ Lifetime Transmissions: {total_sent}\n"
    text += f"├ Health Progress: `{overall_bar}`\n"
    text += f"└ Overall Delivery Rate: {overall_rate}%\n\n"
    
    if recent_fails:
        text += f"⚠️ *RECENT FAILURES*\n"
        for fail in recent_fails:
            reason = fail.get("error", "Unknown").split(":")[0][:20]
            ts = fail.get("sent_at")
            time_str = ts.strftime("%H:%M") if ts else "??"
            text += f"├ `{time_str}` — {reason}\n"
        text += "└ _Check logs for full details_\n\n"
    
    text += f"💡 Activity is tracked per account.\n"
    
    await reply_to_command(client, message, text)


async def handle_groups(client: TelegramClient, user_id: int, message):
    """Handle .groups command - list groups with stylized output."""
    phone = getattr(client, 'phone', None)
    groups = await get_user_groups(user_id, phone=phone)
    
    if not groups:
        await reply_to_command(client, message, 
            f"📁 *TARGET GROUPS — {phone}*\n"
            f"════════════════════════\n\n"
            f"⚪ No groups found.\n\n"
            f"💡 Type `.addgroup [url]` to begin!"
        )
        return
    
    enabled_count = len([g for g in groups if g.get("enabled", True)])
    total_count = len(groups)
    
    header = f"📁 *TARGET GROUPS — {phone}*\n"
    header += f"════════════════════════\n\n"
    header += f"🟢 Active: {enabled_count} ▪ 🔴 Paused: {total_count - enabled_count}\n\n"
    
    # Professional Summarization if list is long
    display_groups = groups[:100]
    
    text = header
    for i, group in enumerate(display_groups, 1):
        title = group.get("chat_title", "Unknown")
        enabled = group.get("enabled", True)
        topic = f" (T:{group.get('topic_id')})" if group.get('topic_id') else ""
        icon = "🟢" if enabled else "🔴"
        
        # Trim long titles
        if len(title) > 20: title = title[:17] + "..."
        
        text += f"  {i}. {icon} `{title}`{topic}\n"
    
    if len(groups) > 100:
        text += f"  ...\n  _And {len(groups) - 100} more groups._\n"
        
    text += f"\n══════════════════════\n"
    from core.config import MAX_GROUPS_PER_USER
    limit = MAX_GROUPS_PER_USER
    text += f"Slots: {total_count}/{limit} ▪ `.rmgroup [idx]`"
    
    await reply_to_command(client, message, text)


async def handle_addgroup(client: TelegramClient, user_id: int, message, text: str):
    """Handle .addgroup <url> [url2] [url3] command - supports multiple groups with progressive progress edits."""
    import asyncio
    # Parse URLs/usernames (split by spaces or newlines)
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await reply_to_command(client, message, 
            "○ Usage: .addgroup [url] [url2] [url3]...\n\n"
            "Examples:\n"
            "  ◦ .addgroup @group1\n"
            "  ◦ .addgroup @group1 @group2 @group3\n"
            "  ◦ .addgroup https://t.me/group1 https://t.me/group2"
        )
        return
    
    # Split input by spaces and newlines to get multiple groups
    group_inputs = parts[1].replace('\n', ' ').split()
    
    if not group_inputs:
        await reply_to_command(client, message, "○ No groups provided")
        return
    
    # Check max group limit
    from core.config import MAX_GROUPS_PER_USER
    limit = MAX_GROUPS_PER_USER
    
    phone = getattr(client, 'phone', None)
    count = await get_group_count(user_id, phone=phone)
    available_slots = limit - count
    
    if available_slots <= 0:
        await reply_to_command(client, message,
            f"○ Maximum groups reached!\n\n"
            f"You can only add up to {limit} groups.\n"
            f"Remove a group with .rmgroup first."
        )
        return
    
    # Limit to available slots
    slot_limit_warning = ""
    if len(group_inputs) > available_slots:
        group_inputs = group_inputs[:available_slots]
        slot_limit_warning = f"⚠️ *Note:* Only processing {available_slots} group(s) due to slot limit.\n\n"
    
    # Initialize a single status message for progressive feedback
    status_msg = await reply_to_command(
        client, 
        message, 
        f"{slot_limit_warning}⏳ Checking {len(group_inputs)} group(s)...",
        auto_delete=False
    )
    
    added = []
    failed = []
    total = len(group_inputs)
    
    for idx, group_input in enumerate(group_inputs, start=1):
        group_input = group_input.strip()
        if not group_input:
            continue
        
        # Update progress before we check this group
        current_status = f"{slot_limit_warning}⏳ **Importing Groups ({idx}/{total})**\n"
        current_status += f"Checking: `{group_input}`...\n"
        if added:
            current_status += "\n✅ **Added:**\n" + "\n".join(f"  ▸ 🟢 {title}" for title in added)
        if failed:
            current_status += "\n\n❌ **Failed:**\n" + "\n".join(f"  ▸ 🔴 {name} — {reason}" for name, reason in failed)
            
        try:
            await status_msg.edit(current_status)
        except Exception:
            pass
            
        # Parse group identifier
        group_identifier, topic_id = parse_group_input(group_input)
        
        if not group_identifier:
            failed.append((group_input, "Invalid URL"))
            continue
        
        try:
            entity = None
            
            # Handle invite links manually
            if isinstance(group_identifier, str) and any(x in group_identifier for x in ["t.me/+", "joinchat/", "t.me/joinchat/"]):
                import re
                hash_match = re.search(r"(?:joinchat/|\+)([\w-]+)", group_identifier)
                if hash_match:
                    invite_hash = hash_match.group(1)
                    from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
                    from telethon.tl.types import ChatInviteAlready, ChatInvite
                    try:
                        invite = await asyncio.wait_for(client(CheckChatInviteRequest(invite_hash)), timeout=10.0)
                        if isinstance(invite, ChatInviteAlready):
                            entity = invite.chat
                        else:
                            try:
                                updates = await asyncio.wait_for(client(ImportChatInviteRequest(invite_hash)), timeout=15.0)
                                if updates.chats:
                                    entity = updates.chats[0]
                            except UserAlreadyParticipantError:
                                # User is already in the group! Let's search dialogs to resolve the entity
                                chat_title = getattr(invite, 'title', '')
                                if chat_title:
                                    dialogs = await client.get_dialogs(limit=100)
                                    for dialog in dialogs:
                                        if dialog.name == chat_title or getattr(dialog.entity, 'title', '') == chat_title:
                                            entity = dialog.entity
                                            break
                                if not entity:
                                    if hasattr(invite, 'chat') and invite.chat:
                                        entity = invite.chat
                                    else:
                                        failed.append((group_input, "Already joined. Open group first."))
                                        continue
                    except asyncio.TimeoutError:
                        failed.append((group_input, "Invite check timeout"))
                        continue
                    except Exception as e:
                        failed.append((group_input, f"Invite Error: {str(e)[:15]}"))
                        continue
            
            if not entity:
                try:
                    # Get the entity (group/channel)
                    try:
                        entity = await asyncio.wait_for(client.get_entity(group_identifier), timeout=10.0)
                    except (ValueError, TypeError):
                        if isinstance(group_identifier, int) and group_identifier > 0:
                            try:
                                channel_id = int(f"-100{group_identifier}")
                                entity = await asyncio.wait_for(client.get_entity(channel_id), timeout=10.0)
                            except Exception:
                                pass
                        if not entity:
                            raise ValueError("Direct get_entity failed")
                except ValueError:
                    # Not in cache, try scanning dialogs
                    found = False
                    if isinstance(group_identifier, int):
                        try:
                            dialogs = await asyncio.wait_for(client.get_dialogs(limit=150), timeout=15.0)
                            for dialog in dialogs:
                                d_peer_id = utils.get_peer_id(dialog.entity) if hasattr(utils, 'get_peer_id') else dialog.id
                                if (str(dialog.id) == str(group_identifier) or 
                                    str(d_peer_id) == str(group_identifier) or
                                    str(dialog.id) == f"-100{group_identifier}" or
                                    str(dialog.id).endswith(str(abs(group_identifier)))):
                                    entity = dialog.entity
                                    found = True
                                    break
                        except asyncio.TimeoutError:
                            pass
                    if not found:
                        failed.append((group_input, "Not found in cache. Open group first."))
                        continue
                except asyncio.TimeoutError:
                    failed.append((group_input, "Timeout resolving group"))
                    continue

            # Ensure user is a participant / member of the group
            from telethon.tl.functions.channels import JoinChannelRequest
            from telethon.errors import UserAlreadyParticipantError, UserBannedInChannelError, ChannelPrivateError, InviteRequestSentError
            if isinstance(entity, Channel):
                try:
                    await asyncio.wait_for(client(JoinChannelRequest(entity)), timeout=10.0)
                except UserAlreadyParticipantError:
                    pass
                except (UserBannedInChannelError, ChannelPrivateError, InviteRequestSentError):
                    failed.append((group_input, "Not a member / Could not join"))
                    continue
                except Exception as join_err:
                    is_joined = False
                    try:
                        async for dialog in client.iter_dialogs(limit=100):
                            if dialog.id == utils.get_peer_id(entity):
                                is_joined = True
                                break
                    except Exception:
                        pass
                    if not is_joined:
                        failed.append((group_input, "Not a member / Could not join"))
                        continue

            chat_id = utils.get_peer_id(entity)
            chat_title = getattr(entity, 'title', None) or getattr(entity, 'username', str(chat_id))
            
            # Get member count
            member_count = 0
            try:
                from telethon.tl.functions.channels import GetFullChannelRequest
                full_chat = await asyncio.wait_for(client(GetFullChannelRequest(entity)), timeout=5.0)
                member_count = full_chat.full_chat.participants_count
            except Exception:
                pass
                
            # Save to database
            # Link to the current account's phone for multi-account support
            success = await add_group(
                user_id, chat_id, chat_title, 
                account_phone=getattr(client, 'phone', None), 
                member_count=member_count,
                topic_id=topic_id
            )
            
            if success:
                display_name = f"{chat_title}" + (f" (Topic {topic_id})" if topic_id else "")
                added.append(display_name)
            else:
                failed.append((group_input, "Already exists or limit reached"))
                
        except (UsernameNotOccupiedError, UsernameInvalidError):
            failed.append((group_input, "Not found"))
        except (ChannelPrivateError, ChannelInvalidError):
            failed.append((group_input, "Private/No access"))
        except (InviteHashInvalidError, InviteHashExpiredError):
            failed.append((group_input, "Invalid invite"))
        except asyncio.TimeoutError:
            failed.append((group_input, "Timeout resolving group"))
        except Exception as e:
            failed.append((group_input, str(e)[:20]))
            
        # V6: Safe Bulk Joining Delay
        # If processing multiple groups, apply a safety gap to prevent hitting Telegram limits
        if idx < total:
            is_invite_link = isinstance(group_identifier, str) and any(x in group_identifier for x in ["joinchat/", "t.me/+"])
            # Base delay: 20-40s for joining via invite links, 5-10s for username resolutions
            if is_invite_link:
                delay = random.uniform(20.0, 40.0)
            else:
                delay = random.uniform(5.0, 10.0)
                
            # Additional cool-down break after every 5 groups to mimic human rest periods
            if idx % 5 == 0:
                rest_break = random.uniform(30.0, 60.0)
                delay += rest_break
            
            current_status = f"{slot_limit_warning}⏳ **Importing Groups ({idx}/{total})**\n"
            current_status += f"⏳ Applying safety gap ({delay:.1f}s)...\n"
            if added:
                current_status += "\n✅ **Added:**\n" + "\n".join(f"  ▸ 🟢 {title}" for title in added)
            if failed:
                current_status += "\n\n❌ **Failed:**\n" + "\n".join(f"  ▸ 🔴 {name} — {reason}" for name, reason in failed)
                
            try:
                await status_msg.edit(current_status)
            except Exception:
                pass
                
            await asyncio.sleep(delay)
            
    # Build final response
    response = f"{slot_limit_warning}"
    
    if added:
        response += f"✅ Added {len(added)} group(s):\n"
        for title in added:
            response += f"  ▸ 🟢 {title}\n"
    
    if failed:
        response += f"\n❌ Failed {len(failed)}:\n"
        for name, reason in failed:
            response += f"  ▸ 🔴 {name} — {reason}\n"
    
    if not added and not failed:
        response += "⚪ No groups were added."
    
    # Add current count
    new_count = await get_group_count(user_id)
    response += f"\n📁 Total: {new_count}/{MAX_GROUPS_PER_USER} slots used."
    
    try:
        await status_msg.edit(response.strip())
    except Exception:
        pass
        
    # Schedule auto delete for the final status message and the trigger command message
    async def _auto_delete_final():
        await asyncio.sleep(30)
        try:
            await status_msg.delete()
            await message.delete()
        except Exception:
            pass  # Message may already be deleted
            
    asyncio.create_task(_auto_delete_final())


async def handle_rmgroup(client: TelegramClient, user_id: int, message, text: str):
    """Handle .rmgroup <number or url> [number2] ... command. Supports batch removal."""
    parts = text.split()
    if len(parts) < 2:
        await reply_to_command(client, message,
            "○ Usage: .rmgroup [number or url] [idx2] [idx3]...\n\n"
            "Examples:\n"
            "  ◦ .rmgroup 1\n"
            "  ◦ .rmgroup 1 5 10 @groupname\n\n"
            "▪ Use .groups to see your groups first."
        )
        return
    
    inputs = parts[1:]
    
    phone = getattr(client, 'phone', None)
    groups = await get_user_groups(user_id, phone=phone)
    
    if not groups:
        await reply_to_command(client, message, "○ You have no groups in your list.")
        return
    
    removed_titles = []
    failed_inputs = []
    
    for item in inputs:
        chat_id = None
        chat_title = None
        
        # Check if index number
        if item.isdigit():
            idx = int(item)
            if 1 <= idx <= len(groups):
                group = groups[idx - 1]
                chat_id = group["chat_id"]
                chat_title = group.get("chat_title", "Unknown")
            else:
                failed_inputs.append(f"{item} (Out of range)")
                continue
        else:
            # Try to resolve url/username
            group_identifier, _ = parse_group_input(item)
            if not group_identifier:
                failed_inputs.append(f"{item} (Invalid URL)")
                continue
                
            try:
                # Resolve entity or match by username/title in existing list
                try:
                    entity = await client.get_entity(group_identifier)
                    chat_id = utils.get_peer_id(entity)
                except Exception:
                    pass
                
                # Match in existing list
                search = group_identifier.lstrip("@").lower()
                for g in groups:
                    if chat_id and g["chat_id"] == chat_id:
                        chat_id = g["chat_id"]
                        chat_title = g["chat_title"]
                        break
                    if search in g.get("chat_title", "").lower() or (g.get("chat_id") and str(g["chat_id"]) == search):
                        chat_id = g["chat_id"]
                        chat_title = g["chat_title"]
                        break
            except Exception:
                failed_inputs.append(f"{item} (Not found)")
                continue
        
        if chat_id:
            try:
                await remove_group(user_id, chat_id)
                removed_titles.append(chat_title or f"Chat {chat_id}")
            except Exception as e:
                failed_inputs.append(f"{item} ({str(e)[:15]})")
    
    # Build response
    resp = ""
    if removed_titles:
        resp += f"✅ Removed {len(removed_titles)} group(s):\n"
        for t in removed_titles:
            resp += f"  ▸ {t}\n"
            
    if failed_inputs:
        resp += f"\n❌ Failed to remove:\n"
        for f in failed_inputs:
            resp += f"  ▸ {f}\n"
            
    if not resp:
        resp = "○ No groups were removed."
    else:
        remaining = await get_group_count(user_id)
        resp += f"\n📁 Remaining: {remaining}/{MAX_GROUPS_PER_USER} slots."
        
    await reply_to_command(client, message, resp.strip())


async def handle_resume(client: TelegramClient, user_id: int, message):
    """Handle .resume command to re-enable all paused groups."""
    from models.group import resume_user_groups
    count = await resume_user_groups(user_id)
    
    if count > 0:
        await reply_to_command(client, message, f"✅ *RESUMED* — {count} groups are now active! ⚡")
    else:
        await reply_to_command(client, message, "⚪ No paused groups found.")

async def handle_pause(client: TelegramClient, user_id: int, message):
    """Handle .pause command to disable all active groups."""
    from models.group import pause_user_groups
    count = await pause_user_groups(user_id)
    
    if count > 0:
        await reply_to_command(client, message, f"🔴 *PAUSED* — {count} groups have been disabled.")
    else:
        await reply_to_command(client, message, "⚪ No active groups found to pause.")

async def handle_clear(client: TelegramClient, user_id: int, message):
    """Handle .clear command to remove ALL groups."""
    from models.group import clear_user_groups
    count = await clear_user_groups(user_id)
    
    if count > 0:
        await reply_to_command(client, message, f"🗑️ *WIPED* — All {count} groups have been removed from your list.", auto_delete=False)
    else:
        await reply_to_command(client, message, "⚪ Your group list is already empty.", auto_delete=False)

async def handle_logs(client: TelegramClient, user_id: int, message):
    """Handle .logs command to show recent activity and progress bar for each account ID."""
    from shared.utils import make_progress_bar
    from db.models import get_recent_failed_logs, get_account_stats, get_group_count
    from core.config import MAX_GROUPS_PER_USER
    
    phone = getattr(client, 'phone', 'Unknown')
    stats = await get_account_stats(user_id, phone)
    group_count = await get_group_count(user_id)
    logs = await get_recent_failed_logs(user_id, phone, limit=10)
    
    today_sent = stats.get("today_sent", 0)
    today_success = stats.get("today_success", 0)
    overall_rate = stats.get("success_rate", 0)
    
    today_bar = make_progress_bar(today_success, today_sent) if today_sent > 0 else make_progress_bar(0, 100)
    groups_bar = make_progress_bar(group_count, MAX_GROUPS_PER_USER)
    overall_bar = make_progress_bar(int(overall_rate), 100)
    
    text = f"📋 *ACTIVITY LOGS — ID `{phone}`*\n"
    text += f"═══════════════════════════════════\n\n"
    text += f"📊 *ACCOUNT ID METRICS*\n"
    text += f"├ 24h Delivery: `{today_bar}` ({today_success}/{today_sent})\n"
    text += f"├ Group Slots:  `{groups_bar}` ({group_count}/{MAX_GROUPS_PER_USER})\n"
    text += f"└ Overall Rate: `{overall_bar}` ({overall_rate}%)\n\n"
    
    if not logs:
        text += f"⚪ *RECENT LOGS*\n"
        text += f"└ No recent issues found. Everything looks normal!\n"
    else:
        text += f"📜 *RECENT EVENT LOGS*\n"
        for log in logs:
            ts = log.get("sent_at")
            time_str = ts.strftime("%H:%M") if ts else "??"
            status = log.get("status", "unknown")
            error = log.get("error", "OK")
            
            icon = "🟢" if status == "success" else "🔴"
            if "skipped" in status.lower() or "Topic closed" in error:
                icon = "⚪"
            elif "Wait" in error or "Rate limited" in error or "slow mode" in error.lower():
                icon = "🟡"
                
            text += f"  `{time_str}` {icon} `{error[:40]}`\n"
            
    text += f"\n💡 Activity and progress tracked per account ID."
    await reply_to_command(client, message, text)


async def handle_interval(client: TelegramClient, user_id: int, message, text: str):
    """Handle .interval <minutes> command."""
    # Parse the interval
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        config = await get_user_config(user_id)
        current = config.get("interval_min", MIN_INTERVAL_MINUTES)
        await reply_to_command(client, message,
            f"➤ Current Interval: {current} minutes\n\n"
            f"Usage: .interval [minutes]\n"
            f"Minimum: {MIN_INTERVAL_MINUTES} minutes\n\n"
            f"Example: .interval 30"
        )
        return
    
    try:
        interval = int(parts[1].strip())
    except ValueError:
        await reply_to_command(client, message,
            f"○ Invalid number\n\n"
            f"Please enter a valid number of minutes.\n"
            f"Example: .interval 30"
        )
        return
    
    # Validate interval
    if interval < MIN_INTERVAL_MINUTES:
        await reply_to_command(client, message,
            f"○ Interval too low\n\n"
            f"Minimum interval is {MIN_INTERVAL_MINUTES} minutes."
        )
        return
    
    if interval > 1440:  # 24 hours max
        await reply_to_command(client, message,
            "○ Interval too high\n\n"
            "Maximum interval is 1440 minutes (24 hours)."
        )
        return
    
    # Update config
    await update_user_config(user_id, interval_min=interval)
    
    await reply_to_command(client, message,
        f"● Interval updated!\n\n"
        f"➤ New interval: {interval} minutes\n\n"
        f"Messages will be forwarded every {interval} minutes."
    )


async def handle_shuffle(client: TelegramClient, user_id: int, message, text: str):
    """Handle .shuffle on/off command."""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        config = await get_user_config(user_id)
        current = "ON" if config.get("shuffle_mode", False) else "OFF"
        await reply_to_command(client, message,
            f"➤ Shuffle Mode: {current}\n\n"
            f"Usage: .shuffle on/off\n"
            f"Randomizes group order each cycle."
        )
        return
    
    val = parts[1].strip().lower()
    enable = val == "on"
    
    await update_user_config(user_id, shuffle_mode=enable)
    status_text = "ENABLED ●" if enable else "DISABLED ○"
    
    await reply_to_command(client, message,
        f"■ Shuffle Mode {status_text}\n\n"
        f"Groups will now be {'randomized' if enable else 'sent in order'} each cycle."
    )


async def handle_copymode(client: TelegramClient, user_id: int, message, text: str):
    """Handle .copymode on/off command."""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        config = await get_user_config(user_id)
        current = "ON" if config.get("copy_mode", False) else "OFF"
        await reply_to_command(client, message,
            f"➤ Copy Mode: {current}\n\n"
            f"Usage: .copymode on/off\n"
            f"Sends as new message instead of forwarding."
        )
        return
    
    val = parts[1].strip().lower()
    enable = val == "on"
    
    await update_user_config(user_id, copy_mode=enable)
    status_text = "ENABLED ●" if enable else "DISABLED ○"
    
    await reply_to_command(client, message,
        f"■ Copy Mode {status_text}\n\n"
        f"Messages will now be {'sent as new copies' if enable else 'forwarded normally'}."
    )


async def handle_sendmode(client: TelegramClient, user_id: int, message, text: str):
    """Handle .sendmode <sequential/rotate/random> command."""
    parts = text.split(maxsplit=1)
    config = await get_user_config(user_id)
    current = config.get("send_mode", "sequential")
    
    if len(parts) < 2:
        await reply_to_command(client, message,
            f"➤ Send Mode: {current.title()}\n\n"
            f"Usage: .sendmode [mode]\n"
            f"Modes:\n"
            f"  ◦ sequential: Ad 1 to all groups, then Ad 2...\n"
            f"  ◦ rotate: Grp 1 gets Ad 1, Grp 2 gets Ad 2...\n"
            f"  ◦ random: Random ad sent to each group\n"
            f"  ◦ smart: Keyword matching + Top-Ad prioritization"
        )
        return
    
    val = parts[1].strip().lower()
    if val in ["seq", "sequential"]:
        val = "sequential"
    elif val in ["rot", "rotate"]:
        val = "rotate"
    elif val in ["rand", "random"]:
        val = "random"
    elif val in ["smart", "auto"]:
        val = "smart"
    else:
        await reply_to_command(client, message, "○ Invalid mode! Choose: sequential, rotate, random, or smart.")
        return
    
    await update_user_config(user_id, send_mode=val)
    
    await reply_to_command(client, message,
        f"■ Send Mode Updated: {val.title()} ●\n\n"
        f"Message distribution pattern changed."
    )


async def handle_responder(client: TelegramClient, user_id: int, message, text: str):
    """Handle .responder on/off or .responder <message>."""
    from db.models import is_plan_active
    is_premium = await is_plan_active(user_id)
    
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        config = await get_user_config(user_id)
        current = "ON" if config.get("auto_reply_enabled", False) else "OFF"
        
        if is_premium:
            current_msg = config.get('auto_reply_text', '')
        else:
            from core.config import DEFAULT_AD_MESSAGE
            current_msg = DEFAULT_AD_MESSAGE
            
        await reply_to_command(client, message,
            f"➤ Auto-Responder: {current}\n\n"
            f"Usage:\n"
            f"  .responder on/off\n"
            f"  .responder [your message]\n\n"
            f"Current message:\n"
            f"\"{current_msg}\""
        )
        return
    
    val = parts[1].strip()
    
    if val.lower() == "on":
        await update_user_config(user_id, auto_reply_enabled=True)
        await reply_to_command(client, message, "■ Auto-Responder ENABLED ●")
    elif val.lower() == "off":
        await update_user_config(user_id, auto_reply_enabled=False)
        await reply_to_command(client, message, "■ Auto-Responder DISABLED ○")
    else:
        # Set message
        if not is_premium:
            from core.config import DEFAULT_AD_MESSAGE
            await reply_to_command(client, message, 
                "⚠️ *Custom Auto-Responder is a Premium Feature!*\n\n"
                "As a Free User, your auto-responder will use the default advertising message:\n"
                f"\"{DEFAULT_AD_MESSAGE}\"\n\n"
                "Upgrade to Premium to customize this message!"
            )


            # Enable it anyway, but don't set custom message
            await update_user_config(user_id, auto_reply_enabled=True)
        else:
            await update_user_config(user_id, auto_reply_text=val, auto_reply_enabled=True)
            await reply_to_command(client, message, 
                f"● Auto-Responder set and ENABLED!\n\n"
                f"➤ New message: {val}"
            )


async def handle_userstatus(client: TelegramClient, user_id: int, message, text: str):
    """Owner command: .userstatus <user_id>"""
    from core.config import OWNER_ID
    if user_id != OWNER_ID:
        await reply_to_command(client, message, "❌ Reserved for owner.")
        return
        
    parts = text.split()
    if len(parts) < 2:
        await reply_to_command(client, message, "○ Usage: .userstatus [user_id]")
        return
        
    try:
        target_id = int(parts[1])
        await handle_status(client, user_id, message, f".status {target_id}")
    except ValueError:
        await reply_to_command(client, message, "○ Invalid User ID.")

async def handle_addplan(client: TelegramClient, user_id: int, message, text: str):
    """Owner command: .addplan <user_id> <week/month/days>"""
    from core.config import OWNER_ID
    issuer_id = getattr(message, "sender_id", user_id)
    if issuer_id != OWNER_ID:
        await reply_to_command(client, message, "❌ Reserved for owner.")
        return
        
    parts = text.split()
    if len(parts) < 3:
        await reply_to_command(client, message, "○ Usage: .addplan [user_id] [duration]")
        return
        
    try:
        target_id = int(parts[1])
        duration_input = parts[2].lower()
        
        from models.plan import extend_plan, activate_plan
        from core.config import PLAN_DURATIONS
        
        if duration_input in PLAN_DURATIONS:
            await activate_plan(target_id, duration_input)
            days = PLAN_DURATIONS[duration_input]
        else:
            try:
                days = int(duration_input)
                await extend_plan(target_id, days)
            except ValueError:
                await reply_to_command(client, message, "○ Invalid duration. Use: week, month, 3month, 6month, 1year, or number of days.")
                return
                
        await reply_to_command(client, message, 
            f"✅ Plan upgraded for user {target_id}!\n"
            f"  ▸ +{days} days premium added."
        )
    except Exception as e:
        await reply_to_command(client, message, f"❌ Error: {str(e)}")

async def handle_free(client: TelegramClient, user_id: int, message, text: str):
    """Owner command: .free or .addplan <user_id> [days]"""
    from core.config import OWNER_ID
    issuer_id = getattr(message, "sender_id", user_id)
    if issuer_id != OWNER_ID:
        await reply_to_command(client, message, "❌ Reserved for owner.")
        return
        
    parts = text.split()
    days = 365
    target_id = None
    
    if len(parts) >= 2 and parts[1].isdigit():
        target_id = int(parts[1])
        if len(parts) >= 3 and parts[2].isdigit():
            days = int(parts[2])
    elif message.is_reply:
        reply_msg = await message.get_reply_message()
        if reply_msg and reply_msg.sender_id:
            target_id = reply_msg.sender_id
            if len(parts) >= 2 and parts[1].isdigit():
                days = int(parts[1])
    elif len(parts) >= 2 and parts[1].lower() in ("week", "month", "3month", "6month", "1year"):
        # Support duration keywords
        target_id = user_id
        from core.config import PLAN_DURATIONS
        days = PLAN_DURATIONS.get(parts[1].lower(), 30)
                
    if not target_id:
        target_id = user_id  # Default to current session owner if no ID given
        
    try:
        from models.plan import extend_plan
        await extend_plan(target_id, days)
        await reply_to_command(client, message, 
            f"🎉 **FREE PREMIUM GRANTED**\n\n"
            f"👤 **User ID:** `{target_id}`\n"
            f"⏳ **Duration:** {days} days\n"
            f"💎 **Status:** Active Premium"
        )
    except Exception as e:
        await reply_to_command(client, message, f"❌ Error: {str(e)}")

async def handle_checkbrand(client: TelegramClient, user_id: int, message, text: str):
    """Owner command: .checkbrand <user_id> or .checkbrand all"""
    from core.config import OWNER_ID
    issuer_id = getattr(message, "sender_id", user_id)
    if issuer_id != OWNER_ID:
        await reply_to_command(client, message, "❌ Reserved for owner.")
        return
        
    parts = text.split()
    if len(parts) < 2:
        await reply_to_command(client, message, "○ Usage: .checkbrand [user_id / all]")
        return
        
    target_input = parts[1].lower()
    from worker.sender import active_senders
    
    if target_input == "all":
        if not active_senders:
            await reply_to_command(client, message, "⚪ No active users running in the worker to check.")
            return
            
        await reply_to_command(client, message, f"⏳ Force checking and applying branding for all {len(active_senders)} active users...")
        
        success_count = 0
        fail_count = 0
        for target_id, target_sender in list(active_senders.items()):
            try:
                await target_sender._enforce_profile_branding()
                success_count += 1
            except Exception as e:
                logger.error(f"Error enforcing branding for user {target_id}: {e}")
                fail_count += 1
                
        await reply_to_command(client, message, 
            f"✅ **GLOBAL BRANDING CHECK COMPLETED**\n\n"
            f"👥 Total Checked: {len(active_senders)}\n"
            f"🟢 Success: {success_count}\n"
            f"🔴 Failed: {fail_count}"
        )
        return
        
    try:
        target_id = int(target_input)
    except ValueError:
        await reply_to_command(client, message, "❌ Invalid User ID. Must be numerical or 'all'.")
        return
        
    target_sender = active_senders.get(target_id)
    if not target_sender:
        await reply_to_command(client, message, f"❌ User {target_id} is not active or running in the worker.")
        return
        
    await reply_to_command(client, message, f"⏳ Checking and applying profile branding for User `{target_id}`...")
    
    try:
        # Run branding check and apply
        await target_sender._enforce_profile_branding()
        
        # Get target user's current profile info to report back to admin
        from telethon.tl.functions.users import GetFullUserRequest
        full = await target_sender.client(GetFullUserRequest('me'))
        me = full.users[0]
        about = full.full_user.about or "*(Empty)*"
        full_name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        
        from db.models import is_plan_active
        is_premium = await is_plan_active(target_id)
        plan_status_str = "Premium" if is_premium else "Free User"
        
        report = (
            f"✅ **BRANDING CHECK COMPLETED**\n\n"
            f"👤 **User ID:** `{target_id}`\n"
            f"📞 **Phone:** `{target_sender.phone}`\n"
            f"💎 **Plan Status:** {plan_status_str}\n\n"
            f"📋 **Current Profile Details:**\n"
            f"├ **Name:** `{full_name}`\n"
            f"└ **Bio:** `{about}`\n\n"
            f"⚡ **Status:** Branding checked and applied successfully!"
        )
        await reply_to_command(client, message, report)
        
    except Exception as e:
        await reply_to_command(client, message, f"❌ Error enforcing branding for User {target_id}: {str(e)}")

async def handle_rmpaused(client: TelegramClient, user_id: int, message):
    """Remove all paused groups."""
    groups = await get_user_groups(user_id)
    paused = [g for g in groups if not g.get("enabled", True)]
    
    if not paused:
        await reply_to_command(client, message, "⚪ No paused groups to remove.")
        return
        
    count = 0
    for g in paused:
        await remove_group(user_id, g["chat_id"])
        count += 1
        
    await reply_to_command(client, message, f"✅ Removed {count} paused group(s).")


async def handle_pauseall(client: TelegramClient, user_id: int, message):
    """Pause ALL groups at once."""
    from db.models import update_all_groups_status, get_group_count
    count = await get_group_count(user_id)
    if count == 0:
        await reply_to_command(client, message, "⚪ You have no groups to pause.")
        return
    await update_all_groups_status(user_id, enabled=False)
    await reply_to_command(client, message, f"⏸ Paused ALL {count} group(s). Use `.resumeall` to resume.")


async def handle_resumeall(client: TelegramClient, user_id: int, message):
    """Resume ALL groups at once."""
    from db.models import update_all_groups_status, get_group_count
    count = await get_group_count(user_id)
    if count == 0:
        await reply_to_command(client, message, "⚪ You have no groups to resume.")
        return
    await update_all_groups_status(user_id, enabled=True)
    await reply_to_command(client, message, f"▶️ Resumed ALL {count} group(s). Messaging will start on next cycle.")


def parse_group_input(input_str: str) -> tuple[Optional[str], Optional[int]]:
    """
    Parse group URL, username, or ID into a (identifier, topic_id) tuple.
    Returns (None, None) if parsing fails.
    """
    input_str = input_str.strip().rstrip('/')
    if not input_str:
        return None, None
        
    # Normalize alternative domains to t.me
    input_str = input_str.replace("telegram.me/", "t.me/").replace("telegram.dog/", "t.me/")
    
    # 1. Private Topic/Forum Pattern (t.me/c/123/456)
    private_topic_match = re.search(r"t\.me/c/(\d+)/(\d+)$", input_str)
    if private_topic_match:
        ident = int(f"-100{private_topic_match.group(1)}")
        topic_id = int(private_topic_match.group(2))
        return ident, topic_id

    # 2. Public Topic/Forum Pattern (t.me/username/456)
    public_topic_match = re.search(r"t\.me/([a-zA-Z0-9_]{5,})/(\d+)$", input_str)
    if public_topic_match:
        ident = f"@{public_topic_match.group(1)}"
        topic_id = int(public_topic_match.group(2))
        return ident, topic_id

    # 3. Addlist/Chatlist (addlist:slug)
    if "addlist/" in input_str:
        slug = input_str.split("addlist/")[1].split("?")[0]
        return f"addlist:{slug}", None

    # 4. Direct Join/Invite Links
    if any(x in input_str for x in ["t.me/+", "joinchat/", "tg://join?invite="]):
        return input_str, None

    # 5. Standard private links (t.me/c/1839485732)
    private_match = re.search(r"t\.me/c/(\d+)$", input_str)
    if private_match:
        return int(f"-100{private_match.group(1)}"), None

    # 6. Standard public links (t.me/groupname)
    if "t.me/" in input_str:
        ident = input_str.split("/")[-1].split("?")[0]
        if ident.isdigit():
            return int(f"-100{ident}"), None
        if not ident.startswith("@"):
            ident = f"@{ident}"
        return ident, None

    # 7. Raw username with @
    if input_str.startswith("@"):
        return input_str, None

    # 8. Raw Numeric ID
    if re.match(r"^-?\d+$", input_str):
        val = int(input_str)
        if val > 0:
            if input_str.startswith("100") and len(input_str) >= 10:
                val = int(f"-{input_str}")
            elif len(input_str) >= 8:
                val = int(f"-100{input_str}")
        elif val < 0 and not input_str.startswith("-100") and len(str(abs(val))) >= 8:
            val = int(f"-100{abs(val)}")
        return val, None

    # 9. Fallback for raw alphanumeric strings (assume username)
    if re.match(r"^[a-zA-Z0-9_]+$", input_str):
        return f"@{input_str}", None
    
    return None, None
async def handle_nightmode(client: TelegramClient, user_id: int, message, text: str):
    """Handle .nightmode on/off/auto command (Owner only)."""
    from core.config import OWNER_ID
    if user_id != OWNER_ID:
        await reply_to_command(client, message, "❌ This command is restricted to the BOT OWNER.")
        return
        
    parts = text.split()
    if len(parts) < 2:
        from db.models import get_global_settings
        settings = await get_global_settings()
        current = settings.get("night_mode_force", "auto").upper()
        await reply_to_command(client, message, 
            f"🌙 GLOBAL NIGHT MODE\n\n"
            f"➤ Current: {current}\n\n"
            f"Usage: .nightmode [on/off/auto]\n"
            f"  ◦ `on`: Force night mode NOW\n"
            f"  ◦ `off`: Disable night mode NOW\n"
            f"  ◦ `auto`: Use standard 00:00-06:00 IST"
        )
        return
        
    val = parts[1].lower()
    if val not in ["on", "off", "auto"]:
        await reply_to_command(client, message, "❌ Use: .nightmode on/off/auto")
        return
        
    from db.models import update_global_settings
    await update_global_settings(night_mode_force=val)
    
    await reply_to_command(client, message, 
        f"✅ GLOBAL NIGHT MODE updated to: *{val.upper()}*\n\n"
        f"This change affects all accounts globally."
    )

async def get_night_mode_label() -> str:
    """Helper to get a human-friendly night mode status label."""
    from db.models import get_global_settings
    # Fix: Import is_night_mode from worker.utils instead of send_logic
    from worker.utils import is_night_mode as check_night_mode
    
    settings = await get_global_settings()
    force = settings.get("night_mode_force", "auto")
    active = await check_night_mode()
    
    if force == "on":
        return "🔴 FORCED ON"
    if force == "off":
         return "🟢 FORCED OFF"
    
    return "🌙 Active (00-06 IST)" if active else "☀️ Inactive (Daytime)"


async def handle_folders(client: TelegramClient, user_id: int, message):
    """List all Telegram chat folders (filters)."""
    try:
        from telethon.tl.functions.messages import GetDialogFiltersRequest
        filters = await client(GetDialogFiltersRequest())
        
        if not filters:
            await reply_to_command(client, message, "📁 No folders found on your account.")
            return
            
        text = "📁 *YOUR TELEGRAM FOLDERS*\n"
        text += "══════════════════════════\n\n"
        
        count = 0
        # Fix: Iterate over filters.filters
        for f in getattr(filters, 'filters', []):
            if hasattr(f, 'title') and f.title:
                text += f"▪ `{f.title}`\n"
                count += 1
                
        if count == 0:
            await reply_to_command(client, message, "📁 No custom folders found.")
            return
            
        text += f"\n💡 Use `.addfolder [name]` to add all groups from a folder."
        await reply_to_command(client, message, text)
        
    except Exception as e:
        logger.error(f"Error fetching folders: {e}")
        await reply_to_command(client, message, f"❌ Error: {str(e)}")


async def handle_addfolder(client: TelegramClient, user_id: int, message, text: str):
    """Add all groups from a specific Telegram folder or Share Link."""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await reply_to_command(client, message, "○ Usage: `.addfolder [folder_name OR share_link]`\n\nExample: `.addfolder Crypto` or `.addfolder t.me/addlist/...`")
        return
        
    folder_input = parts[1].strip()
    
    # 1. Handle Share Links (Chatlists)
    if "t.me/addlist" in folder_input or "telegram.me/addlist" in folder_input:
        await handle_addlist_link(client, user_id, message, folder_input)
        return

    await reply_to_command(client, message, f"🔍 Searching for folder: `{folder_input}`...")
    
    try:
        filters = await client(GetDialogFiltersRequest())
        
        target_filter = None
        # Fix: Iterate over filters.filters
        for f in getattr(filters, 'filters', []):
            if hasattr(f, 'title') and f.title and f.title.lower() == folder_input.lower():
                target_filter = f
                break
                
        if not target_filter:
            await reply_to_command(client, message, f"❌ Folder `{folder_input}` not found.\n\nType `.folders` to see all available folders.")
            return
            
        # 2. Get peers from filter
        peers = getattr(target_filter, 'include_peers', [])
        
        # 3. IF NO EXPLICIT PEERS, handle FLAGS (e.g. "Groups" folder)
        if not peers:
            await reply_to_command(client, message, f"📂 Folder `{folder_input}` uses categories. Scanning dialogs...")
            peers = await fetch_peers_by_flags(client, target_filter)
            
        if not peers:
            await reply_to_command(client, message, f"⚪ Folder `{folder_input}` is empty or contains no supported groups.")
            return
            
        await process_folder_peers(client, user_id, message, folder_input, peers)
        
    except Exception as e:
        logger.error(f"Error adding folder: {e}")
        await reply_to_command(client, message, f"❌ Error: {str(e)}")

async def handle_addlist_link(client: TelegramClient, user_id: int, message, link: str):
    """Import groups from a shared folder link (Chatlist)."""
    try:
        from telethon.tl.functions.chatlists import CheckChatlistInviteRequest, JoinChatlistInviteRequest
        
        # Extract slug correctly (handle queries or trailing slashes)
        import re
        slug_match = re.search(r"addlist/([a-zA-Z0-9_-]+)", link)
        if not slug_match:
            await reply_to_command(client, message, f"❌ Invalid shared folder link.")
            return
            
        slug = slug_match.group(1)
        await reply_to_command(client, message, f"🔗 Checking shared folder link...")
        
        # Check the invite
        invite = await client(CheckChatlistInviteRequest(slug))
        
        # Handle both ChatlistInvite (new) and ChatlistInviteAlready (already joined)
        # Both have a 'chatlist' attribute but it contains different types of objects
        from telethon.tl.types.chatlists import ChatlistInviteAlready
        
        title = "Shared Folder"
        peers = []
        
        if hasattr(invite, 'chatlist'):
            title = getattr(invite.chatlist, 'title', "Shared Folder")
        
        # ChatlistInvite has 'peers'
        # ChatlistInviteAlready has 'already_peers'
        peers = getattr(invite, 'peers', []) or getattr(invite, 'already_peers', [])
        
        if not peers:
            await reply_to_command(client, message, f"⚪ Shared folder `{title}` is empty or already fully synced.")
            return
            
        await reply_to_command(client, message, f"📂 Found {len(peers)} items in shared folder `{title}`.\nImporting...")
        
        # Only Join if it's a new invite
        if not isinstance(invite, ChatlistInviteAlready):
            try:
                await client(JoinChatlistInviteRequest(slug, peers))
            except Exception as e:
                if "CHATLISTS_TOO_MUCH" in str(e) or "chatlists too much" in str(e).lower():
                    await reply_to_command(client, message, "⚠️ **Folder Limit Reached!**\nTrying to join groups individually... This may take a moment.")
                    from telethon.tl.functions.channels import JoinChannelRequest
                    import asyncio
                    for p in peers:
                        try:
                            await client(JoinChannelRequest(p))
                            await asyncio.sleep(0.5)
                        except Exception as join_e:
                            logger.error(f"Failed to manually join {p}: {join_e}")
                else:
                    raise e
        
        # Now process like a folder
        await process_folder_peers(client, user_id, message, title, peers)
        
    except Exception as e:
        logger.error(f"Error adding chatlist: {e}")
        await reply_to_command(client, message, f"❌ Chatlist Error: {str(e)}")

async def fetch_peers_by_flags(client: TelegramClient, f: DialogFilter) -> list:
    """Fetch all peers matching a DialogFilter's flags."""
    peers = []
    try:
        async for dialog in client.iter_dialogs(limit=500):
            entity = dialog.entity
            is_group = isinstance(entity, (Chat, Channel)) and not getattr(entity, 'broadcast', False)
            is_broadcast = isinstance(entity, Channel) and getattr(entity, 'broadcast', False)
            
            # Match flags
            match = False
            if f.groups and (is_group or is_broadcast): match = True
            if f.broadcasts and is_broadcast: match = True
            if f.contacts and getattr(entity, 'contact', False): match = True
            if f.non_contacts and not getattr(entity, 'contact', False) and not getattr(entity, 'bot', False) and not getattr(entity, 'is_self', False): match = True
            
            # Exclusions (basic)
            if match:
                if f.exclude_muted and dialog.dialog.notify_settings.silent: match = False
                if f.exclude_read and dialog.unread_count == 0: match = False
                if f.exclude_archived and dialog.archived: match = False
                
            if match:
                peers.append(entity)
    except Exception as e:
        logger.warning(f"Error fetching peers by flags: {e}")
    return peers

async def process_folder_peers(client, user_id, message, folder_name, peers):
    """Common logic to resolve and add multiple peers from a foldery source."""
    if peers is None:
        peers = []
        
    # Check current group count for this specific account phone
    phone = getattr(client, 'phone', None)
    count = await get_group_count(user_id, phone=phone)
    available_slots = MAX_GROUPS_PER_USER - count
    
    if available_slots <= 0:
        await reply_to_command(client, message, f"❌ Maximum groups ({MAX_GROUPS_PER_USER}) reached.")
        return
        
    added = []
    failed = []
    
    # Limit to available slots
    to_process = peers
    if len(peers) > available_slots:
        to_process = peers[:available_slots]
        await reply_to_command(client, message, f"⚠️ Only {available_slots} slots available. Skipping remaining {len(peers)-available_slots}...")
        
    for peer in to_process:
        try:
            # Resolve entity if it's not already resolved
            if isinstance(peer, (Channel, Chat)):
                entity = peer
            else:
                entity = await client.get_entity(peer)
            
            # We only want groups (megagroups) or chats, not broadcast channels
            if not isinstance(entity, (Channel, Chat)):
                continue
            if isinstance(entity, Channel) and getattr(entity, 'broadcast', False):
                continue

            # Ensure membership
            from telethon.tl.functions.channels import JoinChannelRequest
            from telethon.errors import UserAlreadyParticipantError
            if isinstance(entity, Channel):
                try:
                    await client(JoinChannelRequest(entity))
                except UserAlreadyParticipantError:
                    pass
                except Exception as join_err:
                    logger.warning(f"Could not join folder peer {getattr(entity, 'title', entity)}: {join_err}")
                    failed.append(getattr(entity, 'title', 'Unknown'))
                    continue
                
            chat_id = utils.get_peer_id(entity)
            chat_title = entity.title
            
            # Get member count
            member_count = 0
            try:
                from telethon.tl.functions.channels import GetFullChannelRequest
                full_chat = await client(GetFullChannelRequest(entity))
                member_count = full_chat.full_chat.participants_count
            except Exception:
                pass
            
            success = await add_group(user_id, chat_id, chat_title, account_phone=getattr(client, 'phone', None), member_count=member_count)
            if success:
                added.append(chat_title)
            else:
                failed.append(chat_title)
                
        except Exception as e:
            logger.warning(f"Failed to add peer: {e}")
            
    # Final response
    res = f"✅ *IMPORT COMPLETE*\n"
    res += f"📁 Source: `{folder_name}`\n"
    res += f"🎯 Added: {len(added)}\n"
    
    if failed:
        res += f"❌ Skip (exists): {len(failed)}\n"
        
    new_total = await get_group_count(user_id, phone=phone)
    res += f"\nTotal Groups: {new_total}/{MAX_GROUPS_PER_USER}"
    
    await reply_to_command(client, message, res)


async def handle_health(client: TelegramClient, user_id: int, message, text: str, sender=None):
    """
    Handle .health command.
    Checks and displays group health status.
    If argument is 'check', 'live', or 'run', performs a live diagnostic check.
    """
    parts = text.lower().split()
    is_live = len(parts) > 1 and parts[1] in ("check", "live", "run")
    phone = getattr(client, 'phone', None)
    
    groups = await get_user_groups(user_id)
    if not groups:
        await reply_to_command(
            client,
            message,
            f"📊 *GROUP HEALTH — {phone}*\n══════════════════════════\n\n⚪ No target groups found to check."
        )
        return

    if not is_live:
        # Show DB summary
        total = len(groups)
        healthy = 0
        group_fail = 0
        account_fail = 0
        user_paused = 0
        
        for g in groups:
            enabled = g.get("enabled", True)
            fail_type = g.get("fail_type")
            if enabled:
                healthy += 1
            elif fail_type == "group":
                group_fail += 1
            elif fail_type == "account":
                account_fail += 1
            else:
                user_paused += 1
                
        summary_text = (
            f"📊 *GROUP HEALTH SUMMARY — {phone}*\n"
            f"══════════════════════════\n"
            f"🟢 **Healthy / Active:** {healthy}\n"
            f"🔴 **Group Issues:** {group_fail} (dead/restricted)\n"
            f"🟡 **Account Mutes:** {account_fail} (restricted)\n"
            f"⚪ **User Paused:** {user_paused}\n"
            f"══════════════════════════\n"
            f"**Total Targets:** {total}\n\n"
            f"💡 *Run `.health check` to perform a live diagnostic check.*"
        )
        await reply_to_command(client, message, summary_text)
        return

    # Perform live check
    status_msg = await reply_to_command(
        client,
        message,
        f"🔍 **Starting Live Health Check — {phone}...**\nPreparing to diagnostic {len(groups)} group(s).",
        auto_delete=False
    )
    
    import asyncio
    passed = 0
    failed = 0
    failures = []
    total = len(groups)
    
    for idx, group in enumerate(groups, start=1):
        chat_id = group.get("chat_id")
        chat_title = group.get("chat_title", "Unknown")
        
        # Periodically update status (every 3 groups or so to avoid message rate limits)
        if idx == 1 or idx % 3 == 0 or idx == total:
            status_text = (
                f"🔍 **Live Health Diagnostics ({idx}/{total})**\n"
                f"══════════════════════════\n"
                f"⏳ Checking: `{chat_title}`\n"
                f"🟢 Passed (Healthy): {passed}\n"
                f"🔴 Failed (Issues): {failed}"
            )
            try:
                await status_msg.edit(status_text)
            except Exception:
                pass
                
        # Run resolution check
        is_writable = False
        reason = "Unknown error"
        should_disable = False
        
        try:
            # Try to resolve entity (robust fallback)
            entity = None
            try:
                entity = await client.get_entity(chat_id)
            except (ValueError, TypeError):
                if isinstance(chat_id, int) and chat_id > 0:
                    try:
                        channel_id = int(f"-100{chat_id}")
                        entity = await client.get_entity(channel_id)
                    except Exception:
                        pass
                if not entity:
                    from telethon.tl.types import PeerChannel
                    if isinstance(chat_id, int) and chat_id > 0:
                        try:
                            entity = await client.get_entity(PeerChannel(chat_id))
                        except Exception:
                            pass
            
            if not entity:
                # Scan dialogs with peer ID and suffix matching
                try:
                    async for dialog in client.iter_dialogs(limit=300):
                        d_peer_id = utils.get_peer_id(dialog.entity) if hasattr(utils, 'get_peer_id') else dialog.id
                        if (dialog.id == chat_id or 
                            d_peer_id == chat_id or 
                            str(dialog.id) == str(chat_id) or 
                            (isinstance(chat_id, int) and str(dialog.id) == f"-100{chat_id}") or
                            str(dialog.id).endswith(str(abs(chat_id)))):
                            entity = dialog.entity
                            break
                except Exception:
                    pass

            if not entity:
                reason = "Entity Not Found (Membership Required)"
                should_disable = True
                is_writable = False
            
            if entity:
                # Check permissions
                permissions = await client.get_permissions(entity)
                if entity.broadcast and not permissions.is_admin:
                    is_writable = False
                    reason = "ChannelPostForbidden (Not Admin)"
                    should_disable = True
                elif hasattr(permissions, 'can_send_messages') and not permissions.can_send_messages:
                    is_writable = False
                    reason = "GroupMuted (Send permission restricted)"
                    should_disable = True
                else:
                    is_writable = True
                    
        except Exception as e:
            is_writable = False
            reason = type(e).__name__
            should_disable = False
            
        if is_writable:
            passed += 1
            await clear_group_fail(user_id, chat_id)
            # If the sender entity cache has this group in its negative/failed cache, we should clear it.
            if sender and hasattr(sender, '_failed_entities') and chat_id in sender._failed_entities:
                sender._failed_entities.pop(chat_id, None)
        else:
            failed += 1
            if should_disable:
                await mark_group_failing(user_id, chat_id, reason)
                failures.append(f"• `{chat_title}`: {reason}")
                # Add to negative cache of sender if sender exists
                if sender and hasattr(sender, '_failed_entities'):
                    from datetime import datetime
                    sender._failed_entities[chat_id] = (datetime.utcnow(), f"Live check fail: {reason}")
            else:
                failures.append(f"• `{chat_title}`: Temporary check fail ({reason})")
                
        # Delay slightly to avoid spamming / flood waits
        await asyncio.sleep(0.2)

    # Prepare final report
    final_report = (
        f"📊 *HEALTH CHECK REPORT — {phone}*\n"
        f"══════════════════════════\n"
        f"🟢 **Healthy & Writable:** {passed}\n"
        f"🔴 **Errors / Restricted:** {failed}\n"
        f"══════════════════════════\n"
    )
    if failures:
        final_report += "**Failure Details:**\n" + "\n".join(failures[:20]) + "\n"
        if len(failures) > 20:
            final_report += f"_...and {len(failures) - 20} more group errors._\n"
        final_report += f"\n💡 *Groups with errors have been disabled in the list.*"
    else:
        final_report += "🎉 All groups are healthy and writable!"
        
    await status_msg.edit(final_report)
    
    # Schedule deletion of both the status message and the command trigger message after 60 seconds
    async def delete_messages_later(msg1, msg2, delay=60):
        await asyncio.sleep(delay)
        try:
            await msg1.delete()
        except Exception:
            pass
        try:
            await msg2.delete()
        except Exception:
            pass
            
    asyncio.create_task(delete_messages_later(status_msg, message, 60))


async def handle_check(client: TelegramClient, user_id: int, message, text: str, sender=None):
    """
    Handle .check command.
    Sends a test message to all groups (active or paused) to verify actual sending capability,
    then immediately deletes the test message.
    """
    phone = getattr(client, 'phone', None)
    groups = await get_user_groups(user_id)
    if not groups:
        await reply_to_command(
            client,
            message,
            f"📊 *SEND CHECK — {phone}*\n══════════════════════════\n\n⚪ No target groups found to check."
        )
        return

    status_msg = await reply_to_command(
        client,
        message,
        f"🔍 **Running Live Send Verification (.check) — {phone}...**\nPreparing to send test messages to {len(groups)} group(s).",
        auto_delete=False
    )

    import asyncio
    from telethon.errors import RPCError
    success_count = 0
    fail_count = 0
    failures = []
    total = len(groups)

    for idx, group in enumerate(groups, start=1):
        chat_id = group.get("chat_id")
        chat_title = group.get("chat_title", "Unknown")
        topic_id = group.get("topic_id")

        if idx == 1 or idx % 3 == 0 or idx == total:
            status_text = (
                f"🔍 **Live Send Diagnostics ({idx}/{total})**\n"
                f"══════════════════════════\n"
                f"⏳ Checking: `{chat_title}`\n"
                f"🟢 Success: {success_count}\n"
                f"🔴 Failed: {fail_count}"
            )
            try:
                await status_msg.edit(status_text)
            except Exception:
                pass

        success = False
        reason = "Unknown error"

        try:
            # 1. Resolve entity
            entity = None
            try:
                entity = await client.get_entity(chat_id)
            except (ValueError, TypeError):
                if isinstance(chat_id, int) and chat_id > 0:
                    try:
                        channel_id = int(f"-100{chat_id}")
                        entity = await client.get_entity(channel_id)
                    except Exception:
                        pass
                if not entity:
                    from telethon.tl.types import PeerChannel
                    if isinstance(chat_id, int) and chat_id > 0:
                        try:
                            entity = await client.get_entity(PeerChannel(chat_id))
                        except Exception:
                            pass
            
            if not entity:
                try:
                    async for dialog in client.iter_dialogs(limit=300):
                        d_peer_id = utils.get_peer_id(dialog.entity) if hasattr(utils, 'get_peer_id') else dialog.id
                        if (dialog.id == chat_id or 
                            d_peer_id == chat_id or 
                            str(dialog.id) == str(chat_id) or 
                            (isinstance(chat_id, int) and str(dialog.id) == f"-100{chat_id}") or
                            str(dialog.id).endswith(str(abs(chat_id)))):
                            entity = dialog.entity
                            break
                except Exception:
                    pass

            if not entity:
                raise ValueError("Not found in dialog list")

            # 2. Try sending test message
            test_text = "🔍 **System Diagnostic Check**\nVerifying write capabilities. This message will be deleted."
            try:
                test_msg = await client.send_message(
                    entity=entity,
                    message=test_text,
                    reply_to=topic_id
                )
            except RPCError as topic_err:
                if topic_id and ("TOPIC_CLOSED" in str(topic_err).upper() or "REPLY_MESSAGE_ID_INVALID" in str(topic_err).upper()):
                    test_msg = await client.send_message(
                        entity=entity,
                        message=test_text
                    )
                else:
                    raise topic_err
            success = True

            # 3. Immediately delete test message
            try:
                await test_msg.delete()
            except Exception:
                pass

        except Exception as e:
            success = False
            reason = type(e).__name__

        if success:
            success_count += 1
            await clear_group_fail(user_id, chat_id)
            if sender and hasattr(sender, '_failed_entities') and chat_id in sender._failed_entities:
                sender._failed_entities.pop(chat_id, None)
        else:
            fail_count += 1
            await mark_group_failing(user_id, chat_id, reason)
            failures.append(f"• `{chat_title}`: {reason}")
            if sender and hasattr(sender, '_failed_entities'):
                from datetime import datetime
                sender._failed_entities[chat_id] = (datetime.utcnow(), f"Live send check fail: {reason}")

        # Sleep briefly to respect Telegram rate limits
        await asyncio.sleep(1.0)

    # Final report
    final_report = (
        f"📊 *SEND CHECK REPORT — {phone}*\n"
        f"══════════════════════════\n"
        f"🟢 **Successful Sends:** {success_count}\n"
        f"🔴 **Failed Sends:** {fail_count}\n"
        f"══════════════════════════\n"
    )
    if failures:
        final_report += "**Failure Details:**\n" + "\n".join(failures[:20]) + "\n"
        if len(failures) > 20:
            final_report += f"_...and {len(failures) - 20} more errors._\n"
        final_report += f"\n💡 *Groups with sending failures have been disabled in the list.*"
    else:
        final_report += "🎉 All groups successfully passed the send check!"

    await status_msg.edit(final_report)

    async def delete_messages_later(msg1, msg2, delay=60):
        await asyncio.sleep(delay)
        try:
            await msg1.delete()
        except Exception:
            pass
        try:
            await msg2.delete()
        except Exception:
            pass

    asyncio.create_task(delete_messages_later(status_msg, message, 60))


async def handle_clearads(client: TelegramClient, user_id: int, message, sender=None):
    """Clear all Saved Messages (ads) for this account cleanly in a single pass without loops."""
    import asyncio
    from core.config import OWNER_ID
    issuer_id = getattr(message, "sender_id", user_id)
    if issuer_id != user_id and issuer_id != OWNER_ID:
        await reply_to_command(client, message, "❌ Reserved for account owner.", auto_delete=False)
        return

    command_msg_id = message.id
    
    try:
        # Retrieve up to 1000 messages from Saved Messages in a single pass
        msg_ids_to_delete = []
        async for msg in client.iter_messages('me', limit=1000):
            if msg.id == command_msg_id:
                continue
            msg_ids_to_delete.append(msg.id)
            
        total_found = len(msg_ids_to_delete)
        
        if total_found > 0:
            # Delete in chunks of 100 to avoid Telegram request size limits
            chunk_size = 100
            for i in range(0, total_found, chunk_size):
                chunk = msg_ids_to_delete[i:i+chunk_size]
                try:
                    await client.delete_messages('me', chunk)
                except Exception as b_err:
                    logger.warning(f"[User {user_id}] Batch delete failed in clearads: {b_err}, trying fallback")
                    # Fallback to single-message delete for this chunk
                    for m_id in chunk:
                        try:
                            await client.delete_messages('me', [m_id])
                        except Exception:
                            pass
                await asyncio.sleep(0.2)  # Respect rate limits between chunks

        # Send a single success reply
        success_text = f"🗑️ **SUCCESS**\n\nAll `{total_found}` message(s) have been cleared from Saved Messages."
        await reply_to_command(client, message, success_text, auto_delete=True, delete_delay=15)

        # Clean up the original command message immediately
        try:
            await message.delete()
        except Exception:
            pass

        # Trigger wake up if sender is active to update status
        if sender:
            sender.ads_updated = True
            sender.wake_up_event.set()
            await asyncio.sleep(0.1)
            sender.wake_up_event.clear()

    except Exception as e:
        logger.error(f"[User {user_id}] Error in .clearads: {e}")
        try:
            await reply_to_command(client, message, f"❌ **Error clearing ads:** {str(e)}", auto_delete=False)
        except Exception:
            pass


async def handle_setads(client: TelegramClient, user_id: int, message, sender=None):
    """Set a message into Saved Messages."""
    from core.config import OWNER_ID
    issuer_id = getattr(message, "sender_id", user_id)
    if issuer_id != user_id and issuer_id != OWNER_ID:
        await reply_to_command(client, message, "❌ Reserved for account owner.", auto_delete=False)
        return

    status_msg = await reply_to_command(client, message, "⏳ Setting new ad in Saved Messages...", auto_delete=False)
    try:
        # First, clear existing ad messages from Saved Messages so old ads are not kept
        msg_ids_to_delete = []
        STATUS_PREFIXES = (".", "✅", "🗑️", "⏳", "❌", "⚠️", "📊", "🔴", "⚪", "●", "📋")
        async for old_msg in client.iter_messages('me', limit=200):
            if old_msg.id == message.id or old_msg.id == status_msg.id:
                continue
            if old_msg.text:
                stripped = old_msg.text.strip()
                if (stripped.startswith(STATUS_PREFIXES) or 
                    "Free Version Paused" in stripped or 
                    "remain joined" in stripped):
                    continue
            if hasattr(old_msg, 'action') and old_msg.action is not None:
                continue
            if not old_msg.text and not old_msg.media:
                continue
            msg_ids_to_delete.append(old_msg.id)

        if msg_ids_to_delete:
            try:
                await client.delete_messages('me', msg_ids_to_delete)
            except Exception as del_err:
                logger.warning(f"[User {user_id}] Failed to clear old ads in handle_setads: {del_err}")

        if message.is_reply:
            reply_msg = await message.get_reply_message()
            if not reply_msg:
                await status_msg.edit("❌ **Error:** Could not fetch replied message.")
                return
            
            # Send/copy reply_msg to Saved Messages
            if reply_msg.text or reply_msg.media:
                try:
                    saved_msg = await client.send_message(
                        'me',
                        message=reply_msg.text or None,
                        file=reply_msg.media,
                        formatting_entities=reply_msg.entities if reply_msg.text else None
                    )
                except Exception:
                    saved_msg = await client.forward_messages('me', reply_msg)
            else:
                await status_msg.edit("❌ **Error:** Replied message has no text or media.")
                return
        else:
            text = message.text or ""
            parts = text.split(maxsplit=1)
            cmd_text = parts[1].strip() if len(parts) > 1 else ""
            
            if not cmd_text and not message.media:
                await status_msg.edit("❌ **Usage:** `.setads <ad message>` or reply to a message with `.setads`.")
                return
                
            saved_msg = await client.send_message(
                'me',
                message=cmd_text or None,
                file=message.media
            )
            
        await status_msg.edit("✅ **SUCCESS**\n\nThe new ad has been set in Saved Messages and old ads cleared.")
        
        # Trigger immediate wake up & cycle abort so old ads in progress are aborted immediately
        if sender:
            sender.ads_updated = True
            sender.wake_up_event.set()
            await asyncio.sleep(0.1)
            sender.wake_up_event.clear()

        # Clean up trigger command message so it isn't saved as an ad.
        try:
            await message.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"[User {user_id}] Error in .setads: {e}")
        try:
            await status_msg.edit(f"❌ **Error setting ads:** {str(e)}")
        except Exception:
            pass


async def handle_remove_ad(client: TelegramClient, user_id: int, message, text: str = "", sender=None):
    """Remove specific ad message(s) from Saved Messages by ID or reply."""
    import asyncio
    import re
    from core.config import OWNER_ID

    issuer_id = getattr(message, "sender_id", user_id)
    if issuer_id != user_id and issuer_id != OWNER_ID:
        await reply_to_command(client, message, "❌ Reserved for account owner.", auto_delete=False)
        return

    command_msg_id = message.id
    target_ids = []

    if message.is_reply:
        reply_msg = await message.get_reply_message()
        if reply_msg and reply_msg.id != command_msg_id:
            target_ids.append(reply_msg.id)

    if not target_ids:
        parts = text.strip().split(maxsplit=1)
        raw_args = parts[1].strip() if len(parts) > 1 else ""

        # Remove leading "ad " or "ads " prefix if present (case insensitive)
        if raw_args.lower().startswith("ad "):
            raw_args = raw_args[3:].strip()
        elif raw_args.lower().startswith("ads "):
            raw_args = raw_args[4:].strip()

        found_ids = [int(x) for x in re.findall(r'\b\d+\b', raw_args)]
        for mid in found_ids:
            if mid != command_msg_id and mid not in target_ids:
                target_ids.append(mid)

    if not target_ids:
        usage_text = (
            "❌ **Invalid Usage**\n\n"
            "**Usage:**\n"
            "• `.remove <ad_id>` — Remove ad by ID (e.g. `.remove 12345`)\n"
            "• `.remove ad <ad_id>` — Remove ad by ID (e.g. `.remove ad 12345`)\n"
            "• Reply to an ad message with `.remove`\n\n"
            "💡 *Tip: Use `.show` to view your active ad IDs.*"
        )
        await reply_to_command(client, message, usage_text, auto_delete=True, delete_delay=20)
        return

    status_msg = await reply_to_command(client, message, "⏳ Removing specified ad(s)...", auto_delete=False)

    try:
        deleted_count = 0
        failed_ids = []

        try:
            res = await client.delete_messages('me', target_ids)
            if isinstance(res, list):
                deleted_count = len([x for x in res if x])
            elif isinstance(res, int):
                deleted_count = res
            else:
                deleted_count = len(target_ids)
        except Exception as b_err:
            logger.warning(f"[User {user_id}] Batch delete failed in handle_remove_ad: {b_err}, falling back to single delete")
            for m_id in target_ids:
                try:
                    await client.delete_messages('me', [m_id])
                    deleted_count += 1
                except Exception:
                    failed_ids.append(m_id)

        if deleted_count > 0:
            removed_ids_str = ", ".join(f"`{mid}`" for mid in target_ids if mid not in failed_ids)
            success_text = (
                f"🗑️ **SUCCESS**\n\n"
                f"Successfully removed `{deleted_count}` ad(s) from Saved Messages.\n"
                f"**Removed ID(s):** {removed_ids_str}"
            )
            await status_msg.edit(success_text)

            # Trigger wake up if sender is active to update ad list immediately
            if sender:
                sender.ads_updated = True
                sender.wake_up_event.set()
                await asyncio.sleep(0.1)
                sender.wake_up_event.clear()
        else:
            await status_msg.edit("⚠️ **Failed to remove ad(s).** The specified ID(s) could not be found or deleted from Saved Messages.")

        # Auto delete status response message after 15 seconds
        async def _auto_delete_status():
            await asyncio.sleep(15)
            try:
                await status_msg.delete()
            except Exception:
                pass
        asyncio.create_task(_auto_delete_status())

        # Clean up original command message
        try:
            await message.delete()
        except Exception:
            pass

        # Trigger wake up if sender is active to update status
        if sender:
            sender.wake_up_event.set()
            await asyncio.sleep(0.1)
            sender.wake_up_event.clear()

    except Exception as e:
        logger.error(f"[User {user_id}] Error in handle_remove_ad: {e}")
        try:
            await status_msg.edit(f"❌ **Error removing ad:** {str(e)}")
        except Exception:
            pass


async def handle_join(client: TelegramClient, user_id: int, message, text: str):
    """Command: .join <username/link/folder_link>... (joins groups/folders and adds them to DB)."""

    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await reply_to_command(client, message, 
            "○ Usage: `.join [username / link / folder_link] [url2]...`"
        )
        return

    inputs = parts[1].replace('\n', ' ').split()
    if not inputs:
        await reply_to_command(client, message, "❌ No inputs provided.")
        return

    status_msg = await reply_to_command(client, message, f"⏳ Processing {len(inputs)} join request(s)...", auto_delete=False)
    
    joined = []
    failed = []
    
    for idx, raw_input in enumerate(inputs, start=1):
        raw_input = raw_input.strip()
        if not raw_input:
            continue
            
        # Update progress
        progress_text = f"⏳ **Join Progress ({idx}/{len(inputs)})**\n"
        progress_text += f"Current: `{raw_input}`...\n"
        if joined:
            progress_text += "\n✅ **Joined:**\n" + "\n".join(f"  ▸ 🟢 {j}" for j in joined)
        if failed:
            progress_text += "\n❌ **Failed:**\n" + "\n".join(f"  ▸ 🔴 {f} — {r}" for f, r in failed)
        try:
            await status_msg.edit(progress_text)
        except Exception:
            pass

        try:
            # 1. Check if it's a shared folder (chatlist) link
            if "addlist/" in raw_input:
                slug_match = re.search(r"addlist/([a-zA-Z0-9_-]+)", raw_input)
                if not slug_match:
                    failed.append((raw_input, "Invalid folder link"))
                    continue
                    
                slug = slug_match.group(1)
                invite = await client(CheckChatlistInviteRequest(slug))
                peers = getattr(invite, 'peers', []) or getattr(invite, 'already_peers', [])
                
                if not peers:
                    failed.append((raw_input, "Folder is empty or already joined"))
                    continue
                
                title = "Shared Folder"
                if hasattr(invite, 'chatlist'):
                    title = getattr(invite.chatlist, 'title', "Shared Folder")
                
                # Try joining folder directly
                try:
                    await client(JoinChatlistInviteRequest(slug, peers))
                    joined.append(f"Folder `{title}` ({len(peers)} groups)")
                            
                except Exception as folder_err:
                    if "CHATLISTS_TOO_MUCH" in str(folder_err) or "chatlists too much" in str(folder_err).lower():
                        await status_msg.edit(f"{progress_text}\n⚠️ Folder limit reached! Joining {len(peers)} groups individually with safe delays...")
                        
                        folder_joined_count = 0
                        for p_idx, peer in enumerate(peers, start=1):
                            try:
                                # Safe delay between manual joins (except first)
                                if p_idx > 1:
                                    delay = random.uniform(15.0, 30.0)
                                    await status_msg.edit(f"{progress_text}\n⏳ Folder Limit Reached. Safe delay: waiting {delay:.1f}s before next group...")
                                    await asyncio.sleep(delay)
                                    
                                await client(JoinChannelRequest(peer))
                                folder_joined_count += 1
                                    
                            except Exception as peer_err:
                                logger.error(f"Failed to join folder peer {peer}: {peer_err}")
                                
                        # Save folder peers to database
                await process_folder_peers(client, user_id, message, title, peers)
                
            # 2. Check if it's a private chat invite link
            elif any(x in raw_input for x in ["t.me/+", "joinchat/", "t.me/joinchat/"]):
                hash_match = re.search(r"(?:joinchat/|\+)([\w-]+)", raw_input)
                if not hash_match:
                    failed.append((raw_input, "Invalid invite link"))
                    continue
                    
                invite_hash = hash_match.group(1)
                invite = await client(CheckChatInviteRequest(invite_hash))
                entity = None
                
                if isinstance(invite, ChatInviteAlready):
                    entity = invite.chat
                    chat_title = getattr(entity, 'title', None) or getattr(entity, 'username', str(utils.get_peer_id(entity)))
                    joined.append(f"{chat_title} (Already joined)")
                else:
                    # Join via invite link
                    updates = await client(ImportChatInviteRequest(invite_hash))
                    if updates.chats:
                        entity = updates.chats[0]
                        chat_title = getattr(entity, 'title', None) or getattr(entity, 'username', str(utils.get_peer_id(entity)))
                        joined.append(chat_title)
                    else:
                        failed.append((raw_input, "Could not resolve chat from invite"))

                if entity:
                    chat_id = utils.get_peer_id(entity)
                    chat_title = getattr(entity, 'title', None) or getattr(entity, 'username', str(chat_id))
                    await add_group(user_id, chat_id, chat_title, account_phone=getattr(client, 'phone', None))
            
            # 3. Public group/channel username or link
            else:
                # Clean username
                clean_input = raw_input.strip()
                if "t.me/" in clean_input:
                    clean_input = clean_input.split("t.me/")[-1]
                clean_input = clean_input.lstrip('@')
                if "/" in clean_input:
                    clean_input = clean_input.split("/")[0]
                
                # Safe delay if processing multiple individual groups (except first)
                if idx > 1:
                    delay = random.uniform(15.0, 30.0)
                    await status_msg.edit(f"{progress_text}\n⏳ Safe delay: waiting {delay:.1f}s before joining public group...")
                    await asyncio.sleep(delay)

                entity = await client.get_entity(clean_input)
                
                # Check dialogs to see if already joined
                already_joined = False
                try:
                    chat_id = utils.get_peer_id(entity)
                    async for dialog in client.iter_dialogs(limit=100):
                        if dialog.id == chat_id:
                            already_joined = True
                            break
                except Exception:
                    pass
                    
                if not already_joined:
                    await client(JoinChannelRequest(entity))
                    
                chat_id = utils.get_peer_id(entity)
                chat_title = getattr(entity, 'title', None) or getattr(entity, 'username', str(chat_id))
                await add_group(user_id, chat_id, chat_title, account_phone=getattr(client, 'phone', None))
                joined.append(f"{chat_title}" + (" (Already joined)" if already_joined else ""))
                
        except Exception as e:
            err_msg = str(e)
            if "ChannelPrivateError" in err_msg or "channel specified is private" in err_msg.lower():
                err_msg = "Private channel or banned"
            elif "InviteHashExpiredError" in err_msg or "invite hash expired" in err_msg.lower():
                err_msg = "Invite link expired"
            elif "InviteHashInvalidError" in err_msg or "invite hash invalid" in err_msg.lower():
                err_msg = "Invalid invite link"
            elif "ValueError" in err_msg:
                err_msg = "Not found or invalid username"
            elif "FloodWaitError" in err_msg or "flood" in err_msg.lower():
                err_msg = "Rate limited (FloodWait)"
            else:
                err_msg = err_msg[:50]
            failed.append((raw_input, err_msg))
            
    # Final response
    final_text = "🏁 **Join Session Completed**\n\n"
    if joined:
        final_text += "✅ **Successfully Joined:**\n" + "\n".join(f"  ▸ 🟢 {j}" for j in joined) + "\n\n"
    if failed:
        final_text += "❌ **Failed to Join:**\n" + "\n".join(f"  ▸ 🔴 {f} — {r}" for f, r in failed) + "\n\n"
        
    await status_msg.edit(final_text)
    
    # Auto-delete
    async def _auto_delete():
        await asyncio.sleep(60)
        try:
            await status_msg.delete()
            await message.delete()
        except Exception:
            pass
    asyncio.create_task(_auto_delete())


async def handle_show(client: TelegramClient, user_id: int, message):
    """Show how many messages are in Saved Messages and a preview of active ads."""
    from core.config import OWNER_ID
    issuer_id = getattr(message, "sender_id", user_id)
    if issuer_id != user_id and issuer_id != OWNER_ID:
        await reply_to_command(client, message, "❌ Reserved for account owner.")
        return

    status_msg = await reply_to_command(client, message, "⏳ Fetching Saved Messages details...", auto_delete=False)
    try:
        raw_count = 0
        ads = []
        
        STATUS_PREFIXES = (".", "✅", "🗑️", "⏳", "❌", "⚠️", "📊", "🔴", "⚪", "●", "📋")
        async for msg in client.iter_messages('me', limit=1000):
            raw_count += 1
            # Filter like get_all_saved_messages
            if msg.text:
                stripped = msg.text.strip()
                if (stripped.startswith(STATUS_PREFIXES) or 
                    "Free Version Paused" in stripped or 
                    "remain joined" in stripped):
                    continue
            if hasattr(msg, 'action') and msg.action is not None:
                continue
            if not msg.text and not msg.media:
                continue
            ads.append(msg)
            
        # Reverse to show chronological order
        ads.reverse()
        
        response_text = f"📊 **Saved Messages Summary**\n\n"
        response_text += f"▪ Total Raw Messages: `{raw_count}`\n"
        response_text += f"▪ Active Ad Messages: `{len(ads)}`\n\n"
        
        if not ads:
            response_text += "⚠️ **No active ads configured.** Add some messages to Saved Messages to start forwarding."
        else:
            response_text += "📢 **Active Ads Preview:**\n"
            # Show up to 5 previews to avoid hitting Telegram message length limits
            for idx, ad in enumerate(ads[:5], start=1):
                preview_text = ""
                if ad.text:
                    clean_text = ad.text.strip()
                    # Truncate preview to 100 characters
                    preview_text = clean_text[:100] + ("..." if len(clean_text) > 100 else "")
                else:
                    preview_text = "*(No text)*"
                    
                media_type = "None"
                if ad.media:
                    media_type = type(ad.media).__name__.replace("MessageMedia", "")
                    
                response_text += f"\n{idx}️⃣ **Ad ID:** `{ad.id}` | 📁 **Media:** `{media_type}`\n"
                response_text += f"📝 **Preview:** `{preview_text}`\n"
                
            if len(ads) > 5:
                response_text += f"\n*...and {len(ads) - 5} more ads.*"
                
        await status_msg.edit(response_text)
        
    except Exception as e:
        logger.error(f"[User {user_id}] Error in .show: {e}")
        await status_msg.edit(f"❌ **Error displaying Saved Messages:** {str(e)}")


async def handle_setpfp(client: TelegramClient, user_id: int, message):
    """
    Handle .setpfp / .setpic command.
    If the message has an attached photo or is a reply to a photo, downloads and saves to pool, then sets as profile photo.
    Otherwise, picks a random profile photo from pool and sets it.
    """
    from shared.pfp_manager import set_client_profile_photo, save_profile_photo, get_profile_photos
    
    status_msg = await reply_to_command(client, message, "⏳ **Updating profile photo...**", auto_delete=False)
    photo_path = None
    
    # Check if message itself has photo or is a reply to a message with photo
    media_msg = message
    if not getattr(message, 'photo', None) and getattr(message, 'is_reply', False):
        try:
            reply_msg = await message.get_reply_message()
            if reply_msg and getattr(reply_msg, 'photo', None):
                media_msg = reply_msg
        except Exception as err:
            logger.error(f"Error fetching reply message: {err}")
            
    if getattr(media_msg, 'photo', None):
        try:
            downloaded = await client.download_media(media_msg, bytes)
            if downloaded:
                photo_path = save_profile_photo(downloaded, f"pfp_{user_id}_{random.randint(1000, 9999)}.jpg")
        except Exception as e:
            logger.error(f"Error downloading photo from message: {e}")
            
    if not photo_path:
        photos = get_profile_photos()
        if not photos:
            await status_msg.edit("⚠️ **PFP Pool Empty**\nNo profile photos found in `data/profile_photos/`. Send/reply with a photo or upload to Main Bot.")
            return
            
    success = await set_client_profile_photo(client, photo_path)
    if success:
        await status_msg.edit("✅ **Profile photo updated successfully!**")
    else:
        await status_msg.edit("❌ **Failed to update profile photo.** Check system logs.")


async def handle_setallpfp(client: TelegramClient, user_id: int, message):
    """
    Handle .setallpfp command (Owner / Admin only).
    Triggers setting random profile pictures for all connected sessions across the system.
    """
    from core.config import OWNER_ID
    if user_id != OWNER_ID:
        await reply_to_command(client, message, "⛔ **Access Denied**\nThis command is restricted to the Bot Owner.")
        return
        
    from shared.pfp_manager import set_all_connected_sessions_pfp, get_profile_photos
    photos = get_profile_photos()
    if not photos:
        await reply_to_command(client, message, "⚠️ **PFP Pool Empty**\nNo photos available in `data/profile_photos/`.")
        return
        
    status_msg = await reply_to_command(client, message, "⏳ **Updating profile photos for all accounts...**", auto_delete=False)
    res = await set_all_connected_sessions_pfp()
    await status_msg.edit(
        f"✅ **Bulk Profile Photo Update Complete!**\n\n"
        f"👥 Total Accounts: `{res['total']}`\n"
        f"🟢 Successful: `{res['success']}`\n"
        f"🔴 Failed: `{res['failed']}`"
    )




