"""
Phone number input handler for Login Bot.
Uses per-user API credentials collected during login.
"""

import re
from telegram import Update
from telegram.ext import ContextTypes

from login_bot.utils.keyboards import get_phone_input_keyboard, get_confirm_phone_keyboard, get_cancel_keyboard, get_api_input_keyboard


from config import API_ID, API_HASH


async def add_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start add account flow - ask for phone number directly using system API credentials."""
    query = update.callback_query
    await query.answer()
    
    # Store system API credentials
    context.user_data["api_id"] = API_ID
    context.user_data["api_hash"] = API_HASH
    context.user_data["state"] = "waiting_phone"

    text = """
📱 *Step 1: Enter Phone Number*

Please enter your phone number with country code.
Example: `+91XXXXXXXXXX`
"""
    
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_phone_input_keyboard(),
        disable_web_page_preview=True
    )


async def receive_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process received API ID (legacy fallback)."""
    state = context.user_data.get("state")
    if state != "waiting_api_id":
        return

    api_id_text = update.message.text.strip()
    
    if not api_id_text.isdigit():
        await update.message.reply_text(
            "❌ *Invalid API ID*\n\nAPI ID must be a number. Please enter it again:",
            parse_mode="Markdown",
            reply_markup=get_api_input_keyboard()
        )
        return

    context.user_data["api_id"] = int(api_id_text)
    context.user_data["state"] = "waiting_api_hash"

    text = """
🔑 *Step 2: Enter Telegram API Hash*

Now please enter your **API Hash** from [my.telegram.org](https://my.telegram.org).
"""
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_api_input_keyboard()
    )


async def receive_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process received API Hash (legacy fallback)."""
    state = context.user_data.get("state")
    if state != "waiting_api_hash":
        return

    api_hash = update.message.text.strip()
    
    if len(api_hash) < 10: # Basic validation
        await update.message.reply_text(
            "❌ *Invalid API Hash*\n\nPlease enter a valid API Hash:",
            parse_mode="Markdown",
            reply_markup=get_api_input_keyboard()
        )
        return

    context.user_data["api_hash"] = api_hash
    context.user_data["state"] = "waiting_phone"

    text = """
📱 *Step 2: Enter Phone Number*

Finally, enter your phone number with country code.
Example: `+91XXXXXXXXXX`
"""
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_phone_input_keyboard()
    )


async def receive_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process received phone number with smart auto-formatting."""
    state = context.user_data.get("state")
    
    if state != "waiting_phone":
        return
    
    phone = update.message.text.strip()
    
    # Remove spaces, dashes, parentheses
    phone = re.sub(r"[\s\-\(\)]", "", phone)

    # Auto-prepend '+' if omitted by user
    if not phone.startswith("+"):
        phone = "+" + phone
    
    # Check if it contains only digits after +
    if not re.match(r"^\+\d{10,15}$", phone):
        await update.message.reply_text(
            "❌ *Invalid Phone Number Format*\n\n"
            "Please enter a valid phone number with country code.\n"
            "Example: `+91XXXXXXXXXX`",
            parse_mode="Markdown",
            reply_markup=get_phone_input_keyboard(),
        )
        return
    
    # Ensure system API credentials are set if missing
    if not context.user_data.get("api_id") or not context.user_data.get("api_hash"):
        context.user_data["api_id"] = API_ID
        context.user_data["api_hash"] = API_HASH

    # Store phone and ask for confirmation
    context.user_data["phone"] = phone
    context.user_data["state"] = "confirm_phone"
    
    text = f"""
✅ *Confirm Your Details:*

📱 Phone: `{phone}`

Send OTP verification code now?
"""
    
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_confirm_phone_keyboard(),
    )


async def edit_phone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to phone input."""
    query = update.callback_query
    await query.answer()
    
    text = """
📱 *Enter your phone number with country code:*

Example: `+91XXXXXXXXXX`

Make sure to include the + sign and country code.
"""
    
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_phone_input_keyboard(),
    )
    
    context.user_data["state"] = "waiting_phone"


async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the login process."""
    query = update.callback_query
    await query.answer()
    
    # Clear user data
    context.user_data.clear()
    
    text = """
❌ *Login Cancelled*

You can try again anytime.
"""
    
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard(),
    )
