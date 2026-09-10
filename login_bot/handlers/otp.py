"""
OTP handling with inline keypad for Login Bot.
Uses per-user API credentials collected during login.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
    SessionPasswordNeededError,
)


import re
from config import API_ID, API_HASH
from db.models import create_session, create_user
from login_bot.utils.keyboards import (
    get_otp_keypad, get_resend_otp_keyboard, get_2fa_keyboard, get_success_keyboard
)
from shared.utils import escape_markdown, get_telegram_client_kwargs

logger = logging.getLogger(__name__)

# Store Telethon clients temporarily during login
_login_clients = {}


async def _send_or_edit(update: Update, text: str, parse_mode: str = "Markdown", reply_markup=None):
    """Helper to send text reply or edit inline message depending on update source."""
    if update.callback_query:
        try:
            return await update.callback_query.edit_message_text(
                text=text, parse_mode=parse_mode, reply_markup=reply_markup
            )
        except Exception:
            pass
    if update.effective_message:
        return await update.effective_message.reply_text(
            text=text, parse_mode=parse_mode, reply_markup=reply_markup
        )


def get_otp_display(otp: str) -> str:
    """Generate OTP display string."""
    display = ""
    for i in range(5):
        if i < len(otp):
            display += f"{otp[i]} "
        else:
            display += "_ "
    return display.strip()


async def send_otp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send OTP to user's phone using global API credentials."""
    query = update.callback_query
    user_id = update.effective_user.id
    
    phone = context.user_data.get("phone")
    
    if not phone:
        if query:
            await query.answer("❌ Phone number not found. Start over.", show_alert=True)
        else:
            await update.effective_message.reply_text("❌ Phone number not found. Start over with /start")
        return
    
    if query:
        await query.answer("📤 Sending OTP...")
    else:
        await update.effective_message.reply_text("📤 Sending OTP...")
    
    api_id = context.user_data.get("api_id") or API_ID
    api_hash = context.user_data.get("api_hash") or API_HASH

    try:
        # Create Telethon client with PER-USER API credentials
        client = TelegramClient(
            StringSession(),
            api_id,
            api_hash,
            device_model="Spinify Ads Bot",
            system_version="1.0",
            app_version="1.0",
            **get_telegram_client_kwargs()
        )

        
        await client.connect()
        
        # Send code
        result = await client.send_code_request(phone)
        
        # Store client and code hash
        _login_clients[user_id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": result.phone_code_hash,
        }
        
        # Initialize OTP buffer
        context.user_data["otp_buffer"] = ""
        context.user_data["state"] = "waiting_otp"
        
        text = f"""
🔑 *Enter OTP Code*

📱 Phone: `{phone}`
🔢 OTP:  `{get_otp_display("")}`

Tap digits below or **type/paste code in chat** 👇
"""
        
        await _send_or_edit(
            update,
            text,
            parse_mode="Markdown",
            reply_markup=get_otp_keypad(""),
        )
        
    except FloodWaitError as e:
        await _send_or_edit(
            update,
            f"⏳ *Too Many Attempts*\n\nPlease wait {e.seconds} seconds before trying again.",
            parse_mode="Markdown",
            reply_markup=get_resend_otp_keyboard(),
        )
    except Exception as e:
        logger.error(f"Error sending OTP: {e}")
        await _send_or_edit(
            update,
            f"❌ *Error Sending OTP*\n\n{escape_markdown(str(e))}",
            parse_mode="Markdown",
            reply_markup=get_resend_otp_keyboard(),
        )


async def resend_otp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resend OTP."""
    await send_otp_callback(update, context)


async def receive_otp_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process OTP code sent directly as a text message in chat."""
    state = context.user_data.get("state")
    if state != "waiting_otp":
        return

    text = update.message.text.strip()
    # Extract digits from message text
    otp_digits = re.sub(r"\D", "", text)
    
    if len(otp_digits) < 5 or len(otp_digits) > 6:
        await update.message.reply_text(
            "❌ *Invalid OTP format*\n\nPlease enter the 5-digit (or 6-digit) code received on Telegram, or tap the keypad below.",
            parse_mode="Markdown",
            reply_markup=get_otp_keypad(context.user_data.get("otp_buffer", "")),
        )
        return

    context.user_data["otp_buffer"] = otp_digits
    await verify_otp(update, context, otp_digits)


async def otp_keypad_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle OTP keypad button presses."""
    query = update.callback_query
    user_id = update.effective_user.id
    data = query.data
    
    if not data.startswith("otp:"):
        return
    
    action = data.split(":")[1]
    otp_buffer = context.user_data.get("otp_buffer", "")
    phone = context.user_data.get("phone", "Unknown")
    
    # Handle actions
    if action == "back":
        # Remove last digit
        otp_buffer = otp_buffer[:-1]
        await query.answer()
        
    elif action == "clear":
        # Clear all
        otp_buffer = ""
        await query.answer("Cleared")
        
    elif action == "submit":
        # Submit OTP
        if len(otp_buffer) < 5:
            await query.answer("❌ OTP too short", show_alert=True)
            return
        
        await verify_otp(update, context, otp_buffer)
        return
        
    elif action.isdigit():
        # Add digit (max 6 digits)
        if len(otp_buffer) < 6:
            otp_buffer += action
            await query.answer()
        else:
            await query.answer("Max digits reached")
    
    # Update buffer
    context.user_data["otp_buffer"] = otp_buffer
    
    # Update display
    text = f"""
🔑 *Enter OTP Code*

📱 Phone: `{phone}`
🔢 OTP:  `{get_otp_display(otp_buffer)}`

Tap digits below or **type/paste code in chat** 👇
"""
    
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_otp_keypad(otp_buffer),
    )


async def verify_otp(update: Update, context: ContextTypes.DEFAULT_TYPE, otp: str):
    """Verify OTP and sign in."""
    query = update.callback_query
    user_id = update.effective_user.id
    
    login_data = _login_clients.get(user_id)
    
    if not login_data:
        if query:
            await query.answer("❌ Session expired. Start over.", show_alert=True)
        else:
            await update.effective_message.reply_text("❌ Session expired. Please start over with /start")
        return
    
    client = login_data["client"]
    phone = login_data["phone"]
    phone_code_hash = login_data["phone_code_hash"]
    
    if query:
        await query.answer("🔄 Verifying...")
    else:
        await update.effective_message.reply_text("🔄 Verifying code...")
    
    try:
        # Attempt sign in
        await client.sign_in(
            phone=phone,
            code=otp,
            phone_code_hash=phone_code_hash
        )
        
        # Success! Save session
        await save_session_and_complete(update, context, client, phone)
        
    except SessionPasswordNeededError:
        # 2FA required
        context.user_data["state"] = "waiting_2fa"
        
        text = """
🔒 *Two-Step Verification Required*

Your account has 2FA enabled.
Please enter your Telegram 2FA password:
"""
        
        await _send_or_edit(
            update,
            text,
            parse_mode="Markdown",
            reply_markup=get_2fa_keyboard(),
        )
        
    except PhoneCodeInvalidError:
        await _send_or_edit(
            update,
            "❌ *Invalid OTP*\n\nThe code you entered is incorrect. Try again.",
            parse_mode="Markdown",
            reply_markup=get_otp_keypad(context.user_data.get("otp_buffer", "")),
        )
        
    except PhoneCodeExpiredError:
        await _send_or_edit(
            update,
            "⏰ *OTP Expired*\n\nThe code has expired. Please request a new one.",
            parse_mode="Markdown",
            reply_markup=get_resend_otp_keyboard(),
        )
        
    except FloodWaitError as e:
        await _send_or_edit(
            update,
            f"⏳ *Too Many Attempts*\n\nPlease wait {e.seconds} seconds.",
            parse_mode="Markdown",
            reply_markup=get_resend_otp_keyboard(),
        )
        
    except Exception as e:
        logger.error(f"OTP verification error: {e}")
        await _send_or_edit(
            update,
            f"❌ *Error*\n\n{escape_markdown(str(e))}",
            parse_mode="Markdown",
            reply_markup=get_resend_otp_keyboard(),
        )


async def save_session_and_complete(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE, 
    client: TelegramClient, 
    phone: str,
):
    """Save session with global API credentials and show success screen."""
    query = update.callback_query
    user_id = update.effective_user.id
    
    try:
        # Get session string
        session_string = client.session.save()
        
        # Get API credentials from context
        api_id = context.user_data.get("api_id") or API_ID
        api_hash = context.user_data.get("api_hash") or API_HASH
        
        # Save to database WITH API credentials
        await create_user(user_id)
        
        # Sync user profile info (username, names)
        from models.user import update_user_profile
        await update_user_profile(
            user_id, 
            update.effective_user.username,
            update.effective_user.first_name,
            update.effective_user.last_name
        )
        
        await create_session(user_id, phone, session_string, api_id, api_hash)
        
        # Send account added notification to the central log channel
        try:
            from worker.utils import send_central_log, mask_phone
            from html import escape
            from db.models import get_plan
            
            uname = f" (@{escape(update.effective_user.username)})" if update.effective_user.username else ""
            fname = escape(update.effective_user.first_name or "User")
            masked = mask_phone(phone)
            
            plan_doc = await get_plan(user_id)
            plan_status = "Unknown"
            if plan_doc:
                p_type = plan_doc.get("plan_type", "trial")
                p_status = plan_doc.get("status", "active")
                plan_status = f"{p_type.capitalize()} ({p_status.capitalize()})"

            msg = (
                f"<b>╔══════════════════════════╗</b>\n"
                f"<b>║  📱 Telegram Session Added║</b>\n"
                f"<b>╚══════════════════════════╝</b>\n\n"
                f"👤 <b>User:</b> <code>{fname}</code>{uname}\n"
                f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
                f"📞 <b>Phone:</b> <code>{masked}</code>\n"
                f"💎 <b>Plan:</b> <b>{plan_status}</b>\n\n"
                f"<b>━━━━━━━━━━━━━━━━━━━━━━━━━━</b>"
            )
            import asyncio
            asyncio.create_task(send_central_log(msg))
        except Exception as log_err:
            logger.error(f"Failed to send account added log: {log_err}")

        # Auto set random profile photo and enforce name suffix for free users on login
        try:
            from models.plan import get_plan, is_plan_active
            from config import MAIN_BOT_USERNAME, OWNER_ID
            user_plan = await get_plan(user_id)
            plan_type = (user_plan.get("plan_type") or "").lower() if user_plan else ""
            is_paid_upgrade = (
                user_id == OWNER_ID or
                (await is_plan_active(user_id) and plan_type.startswith("paid"))
            )
            if not is_paid_upgrade:
                from shared.pfp_manager import set_client_profile_photo
                await set_client_profile_photo(client)

                from telethon.tl.functions.users import GetFullUserRequest
                from telethon.tl.functions.account import UpdateProfileRequest
                full = await client(GetFullUserRequest('me'))
                me = full.users[0]
                first_name = me.first_name or ""
                last_name = me.last_name or ""
                import re
                clean_first = re.sub(r'(?:◕|ϟ|⚡|\bVɪᴀ\b|\bVia\b)\s*@[A-Za-z0-9_]+', '', first_name, flags=re.IGNORECASE).strip()
                clean_last = re.sub(r'(?:◕|ϟ|⚡|\bVɪᴀ\b|\bVia\b)\s*@[A-Za-z0-9_]+', '', last_name, flags=re.IGNORECASE).strip()
                for old_suffix in [
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
                suffix = f"ϟ @{bot_uname}"
                new_first = clean_first or "User"
                new_last = f"{clean_last} {suffix}" if clean_last else suffix

                if new_first != first_name or new_last != last_name:
                    await client(UpdateProfileRequest(first_name=new_first, last_name=new_last))
                    logger.info(f"Enforced profile name suffix on login for user {user_id}: '{new_first}' '{new_last}'")
            else:
                logger.info(f"User {user_id} is Paid Premium: skipping PFP & name suffix on login.")
        except Exception as pfp_err:
            logger.error(f"Auto branding setting error on login for user {user_id}: {pfp_err}")

        # Disconnect client
        await client.disconnect()
        
        # Clean up
        if user_id in _login_clients:
            del _login_clients[user_id]
        
        context.user_data.clear()

        # Fetch the user's current plan and build success text
        from db.models import get_plan
        from shared.utils import build_connection_success_text
        plan = await get_plan(user_id)
        text = build_connection_success_text(phone, plan)
        
        await _send_or_edit(
            update,
            text,
            parse_mode="Markdown",
            reply_markup=get_success_keyboard(),
        )
        
    except Exception as e:
        logger.error(f"Error saving session: {e}")
        await _send_or_edit(
            update,
            f"❌ *Error Saving Session*\n\n{escape_markdown(str(e))}",
            parse_mode="Markdown",
        )
