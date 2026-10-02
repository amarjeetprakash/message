"""
Inline keyboard builders for Main Bot.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import LOGIN_BOT_USERNAME, CHANNEL_USERNAME, SUPPORT_URL, LOG_CHANNEL_URL


def get_welcome_keyboard() -> InlineKeyboardMarkup:
    """Build welcome screen keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("➕ Add Account", callback_data="add_account"),
            InlineKeyboardButton("📊 Open Dashboard", callback_data="dashboard"),
        ],
        [
            InlineKeyboardButton("🎁 My Plan", callback_data="my_plan"),
        ],
        [
            InlineKeyboardButton("📌 Join Community", url=f"https://t.me/{CHANNEL_USERNAME}"),
            InlineKeyboardButton("📘 Help & Docs", callback_data="help"),
        ],
        [
            InlineKeyboardButton("📢 Updates Logs Channel", url=LOG_CHANNEL_URL),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_add_account_keyboard() -> InlineKeyboardMarkup:
    """Build add account screen keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🚀 Continue to Login Bot", url=f"https://t.me/{LOGIN_BOT_USERNAME}"),
        ],
        [
            InlineKeyboardButton("🏠 Back to Home", callback_data="home"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_premium_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Build premium dashboard keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("➕ Add Account", callback_data="add_account"),
            InlineKeyboardButton("⚙️ Manage Accounts", callback_data="accounts_list"),
        ],
        [
            InlineKeyboardButton("🧹 Clean Groups", callback_data="clean_groups"),
            InlineKeyboardButton("⏯️ Pause / Resume", callback_data="toggle_pause_all"),
        ],
        [
            InlineKeyboardButton("📋 Target Groups", callback_data="groups_list"),
            InlineKeyboardButton("📢 Ad Settings", callback_data="saved_ads"),
        ],
        [
            InlineKeyboardButton("🎁 My Plan / Status", callback_data="my_plan"),
            InlineKeyboardButton("🔄 Toggle Send Mode", callback_data="toggle_send_mode"),
        ],
        [
            InlineKeyboardButton("🧾 Redeem Promo Code", callback_data="redeem_code"),
            InlineKeyboardButton("📘 Commands Map", callback_data="help"),
        ],
        [
            InlineKeyboardButton("📢 Updates Logs Channel", url=LOG_CHANNEL_URL),
        ],
        [
            InlineKeyboardButton("🏠 Back to Home", callback_data="home"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_free_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Build free dashboard keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("➕ Add Account", callback_data="add_account"),
            InlineKeyboardButton("⚙️ Manage Accounts", callback_data="accounts_list"),
        ],
        [
            InlineKeyboardButton("🧹 Clean Groups", callback_data="clean_groups"),
            InlineKeyboardButton("⏯️ Pause / Resume", callback_data="toggle_pause_all"),
        ],
        [
            InlineKeyboardButton("📋 Target Groups", callback_data="groups_list"),
            InlineKeyboardButton("📢 Ad Settings", callback_data="saved_ads"),
        ],
        [
            InlineKeyboardButton("💎 Upgrade to Premium", callback_data="my_plan"),
            InlineKeyboardButton("📘 Commands Map", callback_data="help"),
        ],
        [
            InlineKeyboardButton("🧾 Redeem Promo Code", callback_data="redeem_code"),
            InlineKeyboardButton("📢 Updates Logs Channel", url=LOG_CHANNEL_URL),
        ],
        [
            InlineKeyboardButton("🏠 Back to Home", callback_data="home"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_account_selection_keyboard(sessions: list) -> InlineKeyboardMarkup:
    """Build keyboard with list of accounts for selection."""
    keyboard = []
    
    for s in sessions:
        phone = s.get("phone", "Unknown")
        status = "🟢" if s.get("connected") else "🔴"
        keyboard.append([InlineKeyboardButton(f"{status} {phone}", callback_data=f"manage_account:{phone}")])
    
    keyboard.append([InlineKeyboardButton("➕ Add Another Account", callback_data="add_account")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="dashboard")])
    
    return InlineKeyboardMarkup(keyboard)


def get_plan_keyboard() -> InlineKeyboardMarkup:
    """Build plan display keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🎁 3 DAYS (₹49)", callback_data="buy_plan:trial"),
            InlineKeyboardButton("💎 WEEKLY (₹99)", callback_data="buy_plan:week"),
        ],
        [
            InlineKeyboardButton("🏆 MONTHLY (₹399)", callback_data="buy_plan:month"),
            InlineKeyboardButton("🌟 3 MONTHS (₹799)", callback_data="buy_plan:3month"),
        ],
        [
            InlineKeyboardButton("👑 6 MONTHS (₹1499)", callback_data="buy_plan:6month"),
            InlineKeyboardButton("☄️ 1 YEAR (₹2499)", callback_data="buy_plan:1year"),
        ],
        [
            InlineKeyboardButton("🧾 Redeem Promo Code", callback_data="redeem_code"),
        ],
        [
            InlineKeyboardButton("👨‍💻 Contact Support", url=SUPPORT_URL),
        ],
        [
            InlineKeyboardButton("🔙 Back", callback_data="dashboard"),
            InlineKeyboardButton("🏠 Home", callback_data="home"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_upgrade_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    """Build admin upgrade selection keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🎁 3 Days", callback_data=f"adm_upgr:{target_user_id}:trial"),
            InlineKeyboardButton("💎 1 Week", callback_data=f"adm_upgr:{target_user_id}:week"),
        ],
        [
            InlineKeyboardButton("🏆 1 Month", callback_data=f"adm_upgr:{target_user_id}:month"),
            InlineKeyboardButton("🌟 3 Months", callback_data=f"adm_upgr:{target_user_id}:3month"),
        ],
        [
            InlineKeyboardButton("☄️ 1 Year", callback_data=f"adm_upgr:{target_user_id}:1year"),
        ],
        [
            InlineKeyboardButton("🔙 Back to Admin", callback_data="admin"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)




def get_back_home_keyboard() -> InlineKeyboardMarkup:
    """Simple back and home keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🔙 Go Back", callback_data="dashboard"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="home"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_home_keyboard() -> InlineKeyboardMarkup:
    """Just home button."""
    keyboard = [
        [
            InlineKeyboardButton("🏠 Main Menu", callback_data="home"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_admin_keyboard() -> InlineKeyboardMarkup:
    """Build admin panel keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🩺 Health Monitor", callback_data="admin_health"),
            InlineKeyboardButton("📊 Live Stats", callback_data="admin_stats"),
        ],
        [
            InlineKeyboardButton("💳 Subscriptions", callback_data="adm_sub_menu"),
            InlineKeyboardButton("📢 Global Blast", callback_data="admin_broadcast"),
        ],
        [
            InlineKeyboardButton("🎟 3 Days", callback_data="gen_code:trial"),
            InlineKeyboardButton("🎟 1 Week", callback_data="gen_code:week"),
            InlineKeyboardButton("🎟 1 Month", callback_data="gen_code:month"),
        ],
        [
            InlineKeyboardButton("🎟 3 Month", callback_data="gen_code:3month"),
            InlineKeyboardButton("🎟 6 Month", callback_data="gen_code:6month"),
            InlineKeyboardButton("🎟 1 Year", callback_data="gen_code:1year"),
        ],
        [
            InlineKeyboardButton("⚡ Quick Upgrade User", callback_data="admin_upgrade_init"),
            InlineKeyboardButton("🔍 Enforce Branding (All)", callback_data="admin_enforce_all_branding"),
        ],
        [
            InlineKeyboardButton("👥 User Database", callback_data="admin_users"),
            InlineKeyboardButton("🌙 Night Mode", callback_data="admin_nightmode"),
        ],
        [
            InlineKeyboardButton("🖼 Set PFP (All Users)", callback_data="admin_set_all_pfp"),
            InlineKeyboardButton("📸 PFP Pool / Upload", callback_data="admin_pfp_pool"),
        ],
        [
            InlineKeyboardButton("🏠 Main Menu", callback_data="home"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_admin_pfp_keyboard() -> InlineKeyboardMarkup:
    """Build admin PFP pool management keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🖼 Set Random PFP (All Users)", callback_data="admin_set_all_pfp"),
        ],
        [
            InlineKeyboardButton("🔙 Back to Admin", callback_data="admin"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="home"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_broadcast_keyboard() -> InlineKeyboardMarkup:
    """Build broadcast target selection keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("📢 All Users", callback_data="broadcast:all"),
            InlineKeyboardButton("🔗 Active APIs", callback_data="broadcast:connected"),
        ],
        [
            InlineKeyboardButton("💎 Premium Base", callback_data="broadcast:paid"),
        ],
        [
            InlineKeyboardButton("🔙 Back to Tools", callback_data="admin"),
            InlineKeyboardButton("🏠 Home", callback_data="home"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_manage_account_keyboard(phone: str) -> InlineKeyboardMarkup:
    """Build manage account keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🔌 Disconnect Session", callback_data=f"disconnect_account:{phone}"),
        ],
        [
            InlineKeyboardButton("🔙 Back", callback_data="accounts_list"),
            InlineKeyboardButton("🏠 Home", callback_data="home"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_confirm_disconnect_keyboard(phone: str) -> InlineKeyboardMarkup:
    """Build disconnect confirmation keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("✅ CONFIRM: WIPE IT", callback_data=f"confirm_disconnect:{phone}"),
        ],
        [
            InlineKeyboardButton("❌ CANCEL", callback_data=f"manage_account:{phone}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_subscription_required_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for non-premium access attempt."""
    keyboard = [
        [
            InlineKeyboardButton("💎 Buy Premium Plan", callback_data="buy_plan:month"),
        ],
        [
            InlineKeyboardButton("🎟️ Redeem Promo Code", callback_data="redeem_code"),
        ],
        [
            InlineKeyboardButton("📢 Join Community", url=f"https://t.me/{CHANNEL_USERNAME}"),
            InlineKeyboardButton("👨‍💻 Support", url=SUPPORT_URL),
        ],
        [
            InlineKeyboardButton("🏠 Back to Home", callback_data="home"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_profile_keyboard() -> InlineKeyboardMarkup:
    """Build profile screen keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("⚙️ Manage Accounts", callback_data="accounts_list"),
        ],
        [
            InlineKeyboardButton("🎁 My Plan", callback_data="my_plan"),
        ],
        [
            InlineKeyboardButton("🔙 Back to Dashboard", callback_data="dashboard"),
            InlineKeyboardButton("🏠 Home", callback_data="home"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_night_mode_settings_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for global night mode settings."""
    keyboard = [
        [
            InlineKeyboardButton("🔴 FORCE ON", callback_data="set_nightmode:on"),
            InlineKeyboardButton("🟢 FORCE OFF", callback_data="set_nightmode:off"),
        ],
        [
            InlineKeyboardButton("⏳ AUTO (Schedule)", callback_data="set_nightmode:auto"),
        ],
        [
            InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_stats"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_subscription_menu_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for subscription overview stats."""
    keyboard = [
        [
            InlineKeyboardButton("👥 All Users", callback_data="adm_sub_list:all:0"),
            InlineKeyboardButton("🟢 Active", callback_data="adm_sub_list:active:0"),
        ],
        [
            InlineKeyboardButton("🔴 Free Users", callback_data="adm_sub_list:expired:0"),
            InlineKeyboardButton("⏳ Expiring Soon", callback_data="adm_sub_list:expiring_soon:0"),
        ],
        [
            InlineKeyboardButton("💎 Lifetime", callback_data="adm_sub_list:lifetime:0"),
            InlineKeyboardButton("📥 Export Data", callback_data="adm_sub_export"),
        ],
        [
            InlineKeyboardButton("🔙 Back to Admin", callback_data="admin"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_subscription_list_keyboard(filter_type: str, current_page: int, total_pages: int, search: str = "") -> InlineKeyboardMarkup:
    """Build pagination keyboard for user subscriptions."""
    keyboard = []
    
    # Pagination row
    nav_row = []
    if current_page > 0:
        nav_row.append(InlineKeyboardButton("⏮ Previous", callback_data=f"adm_sub_list:{filter_type}:{current_page-1}"))
    
    # Always display a middle button showing current page
    nav_row.append(InlineKeyboardButton(f"📄 {current_page+1}/{total_pages or 1}", callback_data="ignore"))
    
    if current_page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ⏭", callback_data=f"adm_sub_list:{filter_type}:{current_page+1}"))
        
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([
        InlineKeyboardButton("🔙 Back to Subscriptions", callback_data="adm_sub_menu")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def get_subscription_user_details_keyboard(user_id: int, filter_type: str = "all", page: int = 0) -> InlineKeyboardMarkup:
    """Keyboard for single user actions in subscription view."""
    keyboard = [
        [
            InlineKeyboardButton("➕ Extend 3d", callback_data=f"adm_sub_act:{user_id}:extend:3"),
            InlineKeyboardButton("➕ Extend 7d", callback_data=f"adm_sub_act:{user_id}:extend:7"),
        ],
        [
            InlineKeyboardButton("➕ Extend 30d", callback_data=f"adm_sub_act:{user_id}:extend:30"),
            InlineKeyboardButton("➖ Reduce 7d", callback_data=f"adm_sub_act:{user_id}:reduce:7"),
        ],
        [
            InlineKeyboardButton("🛑 Mark Expired", callback_data=f"adm_sub_act:{user_id}:expire:0"),
            InlineKeyboardButton("🗑 Delete Record", callback_data=f"adm_sub_act:{user_id}:delete:0"),
        ],
        [
            InlineKeyboardButton("🔙 Back to List", callback_data=f"adm_sub_list:{filter_type}:{page}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


