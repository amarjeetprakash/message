"""
Dashboard handler for Main Bot.
"""

from telegram import Update
from telegram.ext import ContextTypes

from db.models import get_all_user_sessions, get_plan, get_user_config, get_group_count, get_account_stats, update_user_config
from main_bot.utils.keyboards import get_premium_dashboard_keyboard, get_free_dashboard_keyboard, get_add_account_keyboard
from config import MIN_INTERVAL_MINUTES
from shared.utils import escape_markdown
import datetime
from db.models import get_user_groups


def format_last_active(dt: datetime.datetime) -> str:
    """Format datetime as relative 'N m/h/d ago'."""
    if not dt:
        return "Never"
    
    now = datetime.datetime.utcnow()
    diff = now - dt
    
    if diff.total_seconds() < 60:
        return "Just now"
    if diff.total_seconds() < 3600:
        return f"{int(diff.total_seconds() // 60)}m ago"
    if diff.total_seconds() < 86400:
        return f"{int(diff.total_seconds() // 3600)}h ago"
    
    return f"{diff.days}d ago"


async def get_group_status_summary(user_id: int) -> str:
    """Consise summary of group health: 🟢 5 Active ▪ 🔴 2 Paused"""
    groups = await get_user_groups(user_id)
    if not groups:
        return "No groups found"
    
    active = len([g for g in groups if g.get("enabled", True)])
    paused = len(groups) - active
    
    parts = []
    if active > 0:
        parts.append(f"🟢 {active} Active")
    if paused > 0:
        parts.append(f"🔴 {paused} Paused")
        
    return " ▪ ".join(parts)


def format_expiry_date(dt: datetime.datetime) -> str:
    """Format expiry date as a readable string."""
    if not dt:
        return "N/A"
    return dt.strftime("%d %b %Y, %I:%M %p")


async def show_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the main dashboard optimized for performance."""
    import asyncio
    from db.models import get_multi_account_stats
    
    user_id = update.effective_user.id
    user_name = escape_markdown(update.effective_user.first_name or "User")
    
    # ── STEP 1: PARALLEL DATA FETCHING (Level Up) ───────────────────
    # Fetch all base user data in parallel instead of one-by-one.
    # This reduces initial latency by up to 300ms.
    sessions_task = get_all_user_sessions(user_id)
    plan_task = get_plan(user_id)
    config_task = get_user_config(user_id)
    group_count_task = get_group_count(user_id)
    
    sessions, plan, config, group_count = await asyncio.gather(
        sessions_task, plan_task, config_task, group_count_task
    )
    
    # ── STEP 2: BULK STATS FETCHING (Level Up) ──────────────────────
    # Fetch all account stats in a single DB aggregation pipeline.
    # No more N+1 query problem.
    phones = [s.get("phone") for s in sessions if s.get("phone")]
    all_stats = await get_multi_account_stats(user_id, phones) if phones else {}
    
    # ═══ Build account section ═══
    account_section = ""
    total_sends = 0
    if sessions:
        for s in sessions:
            status_icon = "🟢" if s.get("connected") else "🔴"
            phone = s.get("phone", "Unknown")
            stats = all_stats.get(phone, {"success_rate": 0, "last_active": None, "total_sent": 0})
            
            last_active = format_last_active(stats["last_active"])
            rate = stats["success_rate"]
            sends = stats.get("total_sent", 0)
            total_sends += sends
            
            # V6: Show live worker status
            worker_status = s.get("worker_status", "")
            status_line = f" ▪ _{worker_status}_" if worker_status else ""
            
            escaped_phone = escape_markdown(phone)
            account_section += f"  {status_icon} `{escaped_phone}`{status_line}\n"
            account_section += f"     ├─ 📊 Sent: {sends} ▪ Rate: {rate}%\n"
            account_section += f"     └─ ⏱️ Active: {last_active}\n"
    else:
        account_section = "  ○ No accounts connected\n  └─ Tap *Add Account* below"

    
    # ═══ Build plan and dashboard based on subscription ═══
    from config import OWNER_ID
    now_utc = datetime.datetime.utcnow()
    is_active_plan = plan and plan.get("status") == "active" and bool(plan.get("expires_at")) and plan["expires_at"] > now_utc
    is_premium = is_active_plan or user_id == OWNER_ID
    has_connected = any(s.get("connected") for s in sessions) if sessions else False
    
    if is_premium:
        p_type = plan.get("plan_type", "premium") if plan else "premium"
        plan_type = "Free Trial" if p_type == "free_trial" else ("Free User" if p_type == "free_user" else p_type.title())
        
        if user_id == OWNER_ID:
            plan_status = "👑 DEVELOPER/OWNER"
            plan_line2 = "     └─ Lifetime developer license active."
        else:
            time_diff = plan["expires_at"] - now_utc
            total_seconds = max(0, int(time_diff.total_seconds()))
            days_left = total_seconds // 86400
            hours_left = (total_seconds % 86400) // 3600
            expiry_date = format_expiry_date(plan["expires_at"])
            plan_badge = "💎 PREMIUM"
            
            if days_left > 0:
                plan_status = f"{plan_badge} ▪ {days_left}d {hours_left}h left"
            else:
                plan_status = f"{plan_badge} ▪ {hours_left}h left"
            plan_line2 = f"     └─ 📅 Expires: {expiry_date}"
            
        if has_connected and group_count > 0:
            fwd_status = "🟢 *ACTIVE*"
        elif has_connected and group_count == 0:
            fwd_status = "🟡 *NO GROUPS*"
        elif not has_connected:
            fwd_status = "🔴 *NO ACCOUNT*"
        else:
            fwd_status = "🔴 *PAUSED*"
            
        interval = config.get("interval_min", MIN_INTERVAL_MINUTES)
        copy_icon = "🟢" if config.get("copy_mode") else "⚫"
        shuffle_icon = "🟢" if config.get("shuffle_mode") else "⚫"
        send_mode = config.get("send_mode", "sequential").title()
        responder_icon = "🟢" if config.get("auto_reply_enabled") else "⚫"
        reply_text = config.get("auto_reply_text", "")
        reply_preview = escape_markdown(reply_text[:25] + "..." if len(reply_text) > 25 else reply_text)
        
        dashboard_text = f"""
💎 *PREMIUM DASHBOARD* — {user_name}

📱 *ACCOUNTS* ({len(sessions) if sessions else 0})
{account_section}

🏷️ *SUBSCRIPTION*
  ➤ {plan_status}
{plan_line2}

📤 *FORWARDING:* {fwd_status}
  ➤ Groups: {group_count} ▪ Total Sent: {total_sends}
  ➤ Status: {await get_group_status_summary(user_id)}
  ➤ Interval: {interval} min ▪ Night: 12-6 AM

⚙️ *PREMIUM SETTINGS*
  {copy_icon} Copy Mode ▪ {shuffle_icon} Shuffle
  🔄 Send Mode: {send_mode}
  {responder_icon} Responder: _{reply_preview}_

💡 *TIP:* Send `.addgroup <url>` in Saved Messages to add target groups!
"""
        reply_markup = get_premium_dashboard_keyboard()
    else:
        plan_status = "⚪ FREE USER"
        plan_line2 = "     └─ Upgrade to Premium to unlock options!"
        
        if has_connected and group_count > 0:
            fwd_status = "🟢 *ACTIVE (FREE)*"
        elif has_connected and group_count == 0:
            fwd_status = "🟡 *NO GROUPS*"
        elif not has_connected:
            fwd_status = "🔴 *NO ACCOUNT*"
        else:
            fwd_status = "🔴 *PAUSED*"
            
        dashboard_text = f"""
📊 *FREE DASHBOARD* — {user_name}

📱 *ACCOUNTS* ({len(sessions) if sessions else 0})
{account_section}

🏷️ *SUBSCRIPTION*
  ➤ {plan_status}
{plan_line2}

📤 *FORWARDING:* {fwd_status}
  ➤ Groups: {group_count} ▪ Total Sent: {total_sends}
  ➤ Status: {await get_group_status_summary(user_id)}
  ➤ Interval: ⏳ Fixed 20 min (Premium: custom)
  ➤ Send Mode: 🔄 Sequential only (Premium: rotate/random)

📢 *FREE TIER REQUIREMENTS:*
  1. Set your Telegram Bio to: `Fʀᴇᴇ Aᴅs Bᴏᴛ Bʏ @SpinifyAdsBot • Pᴏᴡᴇʀᴇᴅ Bʏ @PhiloBots`.
  2. Keep assigned promo profile picture (PFP).
  3. Must remain joined to official channel @SpinifyAdsBot and chat @spinifychat.

⚙️ *PREMIUM SETTINGS (LOCKED 🔒)*
  ⚫ Copy Mode ▪ ⚫ Shuffle
  🔄 Auto-Responder (DMs)

💡 *TIP:* Send `.addgroup <url>` in Saved Messages to begin!
"""
        reply_markup = get_free_dashboard_keyboard()
        
    from shared.utils import safe_reply
    await safe_reply(update, dashboard_text, reply_markup=reply_markup)



async def dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle dashboard callback."""
    await show_dashboard(update, context)


async def add_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show add account screen."""
    query = update.callback_query
    await query.answer()
    
    text = """
🔗 *CONNECT YOUR ACCOUNT*

Securely link your Telegram account
to start auto-forwarding messages.

✅ 256-bit encrypted session
✅ Your API credentials only
✅ Disconnect anytime

👇 *Tap below to continue to Login Bot*
"""
    
    from shared.utils import safe_reply
    await safe_reply(update, text, reply_markup=get_add_account_keyboard())
async def toggle_send_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle between send modes (sequential -> rotate -> random -> sequential)."""
    query = update.callback_query
    user_id = update.effective_user.id
    
    # Restrict to Premium users
    plan = await get_plan(user_id)
    is_premium = plan is not None and plan.get("status") == "active"
    if not is_premium:
        await query.answer("⚠️ Premium Feature: Upgrade your plan to change send mode.", show_alert=True)
        return
    
    config = await get_user_config(user_id)
    current_mode = config.get("send_mode", "sequential")
    
    # Define rotation
    mode_rotation = {
        "sequential": "rotate",
        "rotate": "random",
        "random": "smart",
        "smart": "sequential"
    }
    
    next_mode = mode_rotation.get(current_mode, "sequential")
    
    # Update config
    await update_user_config(user_id, send_mode=next_mode)
    
    # Refresh dashboard
    await query.answer(f"Send Mode changed to: {next_mode.title()}")
    await show_dashboard(update, context)
