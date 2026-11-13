import asyncio
import html
import logging
import os
import random
import re
import sys
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# Enable UTF-8 for console output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
import httpx
from telegram import (
    BotCommand,
    CopyTextButton,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import (
    ADMIN_ID,
    BOT_TOKEN,
    DB_PATH,
    DOMAIN,
    POLL_INTERVAL,
    MUELLFACH_BASE_URL,
)
from database import (
    clear_all_saved_emails,
    delete_saved_email,
    get_active_users_to_poll,
    get_all_user_ids,
    get_all_users_stats,
    get_email_by_message_id,
    get_live_analytics,
    get_or_create_user,
    get_saved_email_by_id,
    get_saved_emails,
    get_saved_otp,
    get_top_users,
    init_db,
    is_message_seen,
    is_username_taken,
    log_user_message,
    mark_message_seen,
    save_email,
    set_active_email,
    toggle_auto_forward,
)
from muellfach import (
    extract_otp_code,
    get_mailbox_messages,
    get_single_mail,
    strip_html_tags,
    sync_mailbox,
)

# Logging configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("MailFuetch")

# Track bot launch time for uptime calculation
BOT_START_TIME = datetime.now()

def get_uptime_str() -> str:
    delta = datetime.now() - BOT_START_TIME
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days > 0:
        parts.append(f"{days} দিন")
    if hours > 0:
        parts.append(f"{hours} ঘণ্টা")
    if minutes > 0:
        parts.append(f"{minutes} মিনিট")
    parts.append(f"{seconds} সেকেন্ড")
    return " ".join(parts)

# States for Conversations
WAITING_BROADCAST = 1
WAITING_DIRECT_MSG = 2
WAITING_LOGIN_INPUT = 3

# Main Reply Keyboard with Native Button Background Colors
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [
            KeyboardButton("📧 Create Mail", style="success"),
        ],
        [
            KeyboardButton("🔑 Login Acc", style="primary"),
        ],
        [
            KeyboardButton("📥 Current Inbox", style="primary"),
            KeyboardButton("📁 Saved Mails", style="primary"),
        ],
        [
            KeyboardButton("⚙️ Setting Fuetch", style="primary"),
            KeyboardButton("🗑️ Delete Mailbox", style="danger"),
        ],
    ],
    resize_keyboard=True,
)

# Admin Panel Reply Keyboard
ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [
            KeyboardButton("📊 Live Analytics", style="primary"),
            KeyboardButton("👥 Users List", style="primary"),
        ],
        [
            KeyboardButton("🏆 Top Users", style="success"),
            KeyboardButton("💾 Database Backup", style="primary"),
        ],
        [
            KeyboardButton("📢 Broadcast", style="primary"),
            KeyboardButton("💬 Send to User", style="primary"),
        ],
        [
            KeyboardButton("🔙 Back to User Menu", style="danger"),
        ],
    ],
    resize_keyboard=True,
)

def get_main_keyboard(user_id: int):
    """Returns main menu with Create Mail at top, Login Acc, Current Inbox, and Admin Panel for Admin"""
    rows = [
        [
            KeyboardButton("📧 Create Mail", style="success"),
        ],
        [
            KeyboardButton("🔑 Login Acc", style="primary"),
        ],
        [
            KeyboardButton("📥 Current Inbox", style="primary"),
            KeyboardButton("📁 Saved Mails", style="primary"),
        ],
        [
            KeyboardButton("⚙️ Setting Fuetch", style="primary"),
            KeyboardButton("🗑️ Delete Mailbox", style="danger"),
        ],
    ]
    if user_id == ADMIN_ID:
        rows.append([KeyboardButton("👑 Admin Panel", style="primary")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def make_web_url(username: str) -> str:
    return f"{MUELLFACH_BASE_URL}/postfach/{DOMAIN}/{username}"

# ────────────────────────────────────────────────────────────────
# Realistic Username Generator (e.g. sobujdadaroy6575687)
# ────────────────────────────────────────────────────────────────

FIRST_NAMES = [
    "sobuj", "tanvir", "rakib", "shakil", "fahim", "arif", "sohel", "hasan",
    "sabbir", "siam", "mehedi", "shuvo", "nahid", "rabbi", "alvi", "emon",
    "riad", "badhon", "asif", "monir", "sourav", "tuhin", "sumon", "noyon",
    "bappa", "robin", "hridoy", "sagar", "akash", "biplob", "milon", "mithu",
    "joy", "nabil", "ashik", "priya", "ritu", "sadia", "tania", "nusrat"
]

MIDDLE_NAMES = [
    "dada", "babu", "kumar", "chandra", "ahmed", "hossain", "khan", "chowdhury",
    "rahman", "islam", "sheikh", "uddin", "ali"
]

LAST_NAMES = [
    "roy", "das", "mondal", "biswas", "sarkar", "paul", "bhowmik", "datta",
    "sen", "barman", "majumder", "talukder", "debnath", "ghosh", "dhar", "kar",
    "ali", "miah", "haque", "kazi", "khan", "ahmed", "hossain", "islam"
]

async def generate_unique_realistic_username() -> str:
    """Generates realistic human username like 'sobujdadaroy6575687'"""
    for _ in range(50):
        first = random.choice(FIRST_NAMES)
        mid = random.choice(MIDDLE_NAMES) if random.random() < 0.6 else ""
        last = random.choice(LAST_NAMES)
        num_digits = random.randint(4, 7)
        digits = "".join([str(random.randint(0, 9)) for _ in range(num_digits)])
        username = f"{first}{mid}{last}{digits}".lower()
        if not await is_username_taken(username):
            return username
    return f"mailuser{random.randint(1000000, 9999999)}"

# ────────────────────────────────────────────────────────────────
# Command Handlers
# ────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command with a modern, premium greeting and notifies admin of new users"""
    user = update.effective_user
    db_user = await get_or_create_user(user.id, user.first_name, user.username or "")
    
    # 🔔 Send instant Live Alert to Admin if it's a brand new user
    if db_user.get("is_new") and user.id != ADMIN_ID:
        tg_uname = f"@{user.username}" if user.username else "No Username"
        now_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
        alert_msg = (
            f"🔔 <b>নতুন ইউজার জয়েন করেছে!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>নাম:</b> <b>{html.escape(user.first_name)}</b>\n"
            f"🔗 <b>ইউজারনেম:</b> {tg_uname}\n"
            f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
            f"⏰ <b>সময়:</b> {now_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=alert_msg,
                parse_mode=ParseMode.HTML,
            )
        except Exception as alert_err:
            logger.warning(f"Failed to send new user alert to admin: {alert_err}")
    
    admin_note = (
        "👑 <b>অ্যাডমিন এক্সেস সক্রিয়:</b> ড্যাশবোর্ড পরিচালনা করতে নিচের <b>👑 Admin Panel</b> বাটনে চাপ দিন বা /admin লিখুন।\n━━━━━━━━━━━━━━━━━━━━━\n"
        if user.id == ADMIN_ID
        else ""
    )
    
    welcome_text = (
        f"✨ <b>স্বাগতম {html.escape(user.first_name)}! MailFuetch Pro-তে আপনাকে স্বাগতম।</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🚀 <b>হাই-স্পিড ইনস্ট্যান্ট ডিসপোজেবল ইমেইল প্ল্যাটফর্ম</b>\n"
        f"কোনো রেজিস্ট্রেশন ছাড়াই আনলিমিটেড <b>@muellfach.de</b> টেম্পোরারি ইমেইল তৈরি করুন এবং রিয়েল-টাইমে সমস্ত ওটিপি ও মেসেজ গ্রহণ করুন!\n\n"
        f"💎 <b>বটের এক্সক্লুসিভ ফিচারসমূহ:</b>\n"
        f"• ⚡ <b>১-ক্লিক ইনস্ট্যান্ট মেইল:</b> কোনো ঝামেলা ছাড়াই মানুষের মতো বাস্তবধর্মী সুন্দর নামে মেইল তৈরি।\n"
        f"• 🔑 <b>Login Acc:</b> যেকোনো পূর্বের ইউজারনেম বা মেইল দিয়ে লগইন করে ইনবক্স ব্যবহার।\n"
        f"• 📋 <b>ওয়ান-ট্যাপ কপি:</b> ইমেইল এড্রেস ও ওটিপি এক ক্লিকেই সরাসরি ক্লিপবোর্ডে কপি করুন।\n"
        f"• 📸 <b>ইনস্টাগ্রাম ও অন্যান্য অ্যাপ সাপোর্ট:</b> Instagram, Discord, Facebook, Telegram ও যেকোনো সার্ভিসের ওটিপি নিখুঁতভাবে রিসিভ হবে।\n"
        f"• ⚡ <b>রিয়েল-টাইম অটো-ফরওয়ার্ড:</b> নতুন মেসেজ আসার ১ সেকেন্ডের মধ্যেই সরাসরি আপনার চ্যাটে চলে আসবে!\n"
        f"• 📁 <b>১০ দিনের লাইভ হিস্ট্রি:</b> তৈরি করা যেকোনো মেইল বিগত ১০ দিন পর্যন্ত সংরক্ষণ থাকে এবং ১ ক্লিকে সক্রিয় করা যায়।\n"
        f"• 🔒 <b>১০০% নিরাপদ ও স্প্যামমুক্ত:</b> আপনার আসল ব্যক্তিগত ইমেইলকে সুরক্ষিত রাখুন।\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{admin_note}"
        f"👇 <b>শুরু করতে নিচের যেকোনো বাটনে ট্যাপ করুন:</b>"
    )
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_keyboard(user.id),
        parse_mode=ParseMode.HTML,
    )

# ────────────────────────────────────────────────────────────────
# 📧 1-Click Create Mail Handler
# ────────────────────────────────────────────────────────────────

async def create_mail_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Creates a new temporary email in 1 click with a realistic username"""
    user = update.effective_user
    user_id = user.id
    await get_or_create_user(user_id, user.first_name, user.username or "")
    
    username = await generate_unique_realistic_username()
    email_address = f"{username}@{DOMAIN}"
    web_url = make_web_url(username)

    # Save to database (replaces active email and adds to 10-day history)
    await save_email(user_id, email_address, username)

    # Trigger initial sync for this mailbox
    async with httpx.AsyncClient() as client:
        await sync_mailbox(client)

    success_msg = (
        f"🎉 <b>আপনার নতুন ইমেইল তৈরি হয়েছে!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 <b>Email:</b> <code>{email_address}</code>\n"
        f"<i>(ট্যাপ করলেই কপি হয়ে যাবে)</i>\n\n"
        f"🌐 <b>Web Link:</b> <a href=\"{web_url}\">Müllfach Web Postfach</a>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏳ <b>মেয়াদ:</b> এই মেইলটি আগামী <b>১০ দিন</b> আপনার <code>📁 Saved Mails</code> এ থাকবে।\n"
        f"⚡ <b>রিয়েল-টাইম অটো ইনবক্স:</b> সক্রিয় আছে! কোনো ওটিপি বা মেসেজ আসলেই ২-৩ সেকেন্ডে সরাসরি এখানে চলে আসবে।"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Copy Email", copy_text=CopyTextButton(text=email_address), style="success")],
        [InlineKeyboardButton("🌐 ওপেন করুন ওয়েবে", url=web_url)],
        [InlineKeyboardButton("🔄 ইনবক্স চেক করুন", callback_data="check_active_inbox", style="primary")],
        [InlineKeyboardButton("➕ আরেকটি নতুন মেইল", callback_data="quick_create_mail", style="primary")]
    ])

    if update.callback_query:
        await update.callback_query.answer("নতুন মেইল তৈরি হয়েছে!")
        await update.callback_query.message.reply_text(
            success_msg,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    else:
        await update.message.reply_text(
            success_msg,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

# ────────────────────────────────────────────────────────────────
# 🔑 Login Account Handler (Connect via Username or Email)
# ────────────────────────────────────────────────────────────────

async def login_acc_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompts user to enter their email username or full email address"""
    user_id = update.effective_user.id
    text = (
        "🔐 <b>আপনার একাউন্টে লগইন করুন:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"আপনার পূর্বে ব্যবহৃত বা যেকোনো <b>@{DOMAIN}</b> মেইলের <b>ইউজারনেম</b> অথবা <b>পুরো ইমেইল এড্রেস</b> লিখে পাঠান:\n\n"
        f"💡 <i>যেমন:</i>\n"
        f"<code>sobujroy6575687</code>\n"
        f"অথবা\n"
        f"<code>sobujroy6575687@{DOMAIN}</code>\n\n"
        f"❌ বাতিল করতে /cancel লিখুন।"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    return WAITING_LOGIN_INPUT

async def login_acc_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes login and connects user to the specified mailbox"""
    user = update.effective_user
    user_id = user.id
    raw_input = update.message.text.strip()

    # Extract username (support both 'name' and 'name@domain')
    if "@" in raw_input:
        username = raw_input.split("@")[0].strip().lower()
    else:
        username = raw_input.strip().lower()

    # Clean username: remove spaces
    username = re.sub(r"\s+", "", username)

    if len(username) < 3 or not re.match(r"^[a-zA-Z0-9_\-]+$", username):
        await update.message.reply_text(
            "❌ <b>অকার্যকর ইউজারনেম!</b>\n\n"
            "অনুগ্রহ করে শুধুমাত্র ইংরেজি অক্ষর (a-z), সংখ্যা (0-9), ড্যাশ বা আন্ডারস্কোর ব্যবহার করুন (কমপক্ষে ৩ অক্ষর)।\n\n"
            "দয়া করে আবার লিখুন অথবা /cancel লিখুন:",
            parse_mode=ParseMode.HTML,
        )
        return WAITING_LOGIN_INPUT

    email_address = f"{username}@{DOMAIN}"
    web_url = make_web_url(username)

    status_msg = await update.message.reply_text(
        f"⏳ <code>{email_address}</code> এর সাথে কানেক্ট করা হচ্ছে...",
        parse_mode=ParseMode.HTML,
    )

    # Save to user's database and set as active email
    await save_email(user_id, email_address, username)

    # Trigger IMAP sync and fetch existing messages
    async with httpx.AsyncClient() as client:
        await sync_mailbox(client)
        mails = await get_mailbox_messages(client, email_address)

    inbox_summary = ""
    if mails:
        inbox_summary = f"\n\n📬 <b>ইনবক্সে {len(mails)} টি মেইল রয়েছে!</b>"
        for i, m in enumerate(mails[:3], 1):
            s_subj = html.escape(m.get("subject") or "(No Subject)")
            s_from = html.escape(m.get("sender_name") or m.get("sender_email") or "Unknown")
            inbox_summary += f"\n{i}. <b>{s_subj}</b> — <i>{s_from}</i>"
    else:
        inbox_summary = "\n\n📭 <i>ইনবক্স বর্তমানে খালি। কোনো নতুন মেসেজ আসলে সাথে সাথে নোটিফিকেশন পাবেন।</i>"

    success_text = (
        f"✅ <b>সফলভাবে একাউন্টে লগইন সম্পন্ন হয়েছে!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 <b>Active Email:</b> <code>{email_address}</code>\n"
        f"<i>(ট্যাপ করলেই কপি হয়ে যাবে)</i>\n\n"
        f"🌐 <b>Web Link:</b> <a href=\"{web_url}\">Müllfach Postfach</a>\n"
        f"⚡ <b>রিয়েল-টাইম অটো ইনবক্স:</b> সক্রিয় আছে ✅\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
        f"{inbox_summary}"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Copy Email", copy_text=CopyTextButton(text=email_address), style="success")],
        [InlineKeyboardButton("📥 ইনবক্স চেক করুন", callback_data="check_active_inbox", style="primary")],
        [InlineKeyboardButton("🌐 ওয়েবে দেখুন", url=web_url)],
    ])

    await status_msg.edit_text(
        success_text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    await update.message.reply_text(
        "👇 নিচের মেন্যু থেকে কাজ চালিয়ে যেতে পারেন:",
        reply_markup=get_main_keyboard(user_id),
    )
    return ConversationHandler.END

async def cancel_login_acc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels login process"""
    user_id = update.effective_user.id
    await update.message.reply_text(
        "❌ লগইন বাতিল করা হয়েছে।",
        reply_markup=get_main_keyboard(user_id),
    )
    return ConversationHandler.END

# ────────────────────────────────────────────────────────────────
# 📁 Saved Mails (10-Day History & Switcher)
# ────────────────────────────────────────────────────────────────

async def saved_mails_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays saved emails from the last 10 days"""
    user_id = update.effective_user.id
    saved = await get_saved_emails(user_id)
    
    if not saved:
        await update.message.reply_text(
            "📭 <b>আপনার কোনো সেভ করা মেইল নেই!</b>\n\n"
            "নতুন একটি মেইল তৈরি করতে নিচের <b>📧 Create Mail</b> বাটনে চাপ দিন।",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_keyboard(user_id),
        )
        return

    db_user = await get_or_create_user(user_id)
    active = db_user.get("active_email")

    keyboard_buttons = []
    for item in saved:
        is_curr = " (Active ✅)" if item["email"] == active else ""
        button_text = f"✉️ {item['email']}{is_curr}"
        keyboard_buttons.append([
            InlineKeyboardButton(button_text, callback_data=f"switch_mail:{item['id']}", style="primary")
        ])
        
    keyboard_buttons.append([
        InlineKeyboardButton("❌ মেন্যু বন্ধ করুন", callback_data="close_menu", style="danger")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard_buttons)
    await update.message.reply_text(
        "📁 <b>আপনার সেভ করা মেইলসমূহ (বিগত ১০ দিন):</b>\n\n"
        "যেকোনো একটি মেইলে ক্লিক করলে সেটি <b>অ্যাক্টিভ</b> হবে এবং তার লাইভ ইনবক্স দেখতে পাবেন:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
    )

async def switch_mail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switches active email when user clicks a saved mail button"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    email_id = int(query.data.split(":")[1])
    
    email_record = await get_saved_email_by_id(email_id, user_id)
    if not email_record:
        await query.edit_message_text("❌ এই ইমেইলটি খুঁজে পাওয়া যায়নি বা মেয়াদ শেষ হয়ে গেছে।")
        return

    new_active_email = email_record["email"]
    username = email_record["username"]
    await set_active_email(user_id, new_active_email)
    
    web_url = make_web_url(username)
    
    # Fetch inbox for this switched email
    async with httpx.AsyncClient() as client:
        await sync_mailbox(client)
        mails = await get_mailbox_messages(client, new_active_email)

    inbox_preview = ""
    if mails:
        inbox_preview = f"\n\n📬 <b>ইনবক্সে {len(mails)} টি মেইল রয়েছে:</b>\n"
        for i, m in enumerate(mails[:3], 1):
            s_name = html.escape(m.get("sender_name") or m.get("sender_email") or "Unknown")
            s_subj = html.escape(m.get("subject") or "(No Subject)")
            inbox_preview += f"{i}. <b>{s_subj}</b> — <i>{s_name}</i>\n"
    else:
        inbox_preview = "\n\n📭 <i>ইনবক্স বর্তমানে খালি। নতুন মেইল আসলে অটোমেটিক নোটিফিকেশন পাবেন।</i>"

    text = (
        f"✅ <b>ইমেইল সফলভাবে পরিবর্তন করা হয়েছে!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 <b>Active Email:</b> <code>{new_active_email}</code>\n"
        f"🌐 <b>Web Link:</b> <a href=\"{web_url}\">Müllfach Postfach</a>"
        f"{inbox_preview}"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 রিফ্রেশ ইনবক্স", callback_data="check_active_inbox", style="success")],
        [InlineKeyboardButton("🌐 ওয়েব ভিউ", url=web_url)],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

# ────────────────────────────────────────────────────────────────
# 📥 Current Inbox Handler (Current Email + Live Inbox)
# ────────────────────────────────────────────────────────────────

async def current_inbox_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays active email address and immediately checks/refreshes its inbox"""
    user_id = update.effective_user.id
    db_user = await get_or_create_user(user_id)
    active_email = db_user.get("active_email")

    if not active_email:
        await update.message.reply_text(
            "⚠️ <b>আপনার বর্তমানে কোনো অ্যাক্টিভ মেইল নেই!</b>\n\n"
            "নতুন একটি মেইল তৈরি করতে উপরের <b>📧 Create Mail</b> বাটনে চাপুন।",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_keyboard(user_id),
        )
        return

    username = active_email.split("@")[0]
    web_url = make_web_url(username)
    auto_status = "চালু আছে ✅" if db_user.get("auto_forward") == 1 else "বন্ধ আছে ❌"

    status_msg = await update.message.reply_text(
        f"🔄 <code>{active_email}</code> এর ইনবক্স চেক করা হচ্ছে...",
        parse_mode=ParseMode.HTML,
    )

    async with httpx.AsyncClient() as client:
        await sync_mailbox(client)
        mails = await get_mailbox_messages(client, active_email)

    if not mails:
        text = (
            f"📥 <b>আপনার বর্তমান ইনবক্স:</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📧 <b>Active Email:</b> <code>{active_email}</code>\n"
            f"<i>(ট্যাপ করলেই কপি হয়ে যাবে)</i>\n\n"
            f"🌐 <b>Web Link:</b> <a href=\"{web_url}\">Müllfach Postfach</a>\n"
            f"⚡ <b>অটো-ফরওয়ার্ড:</b> {auto_status}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📭 <i>ইনবক্স বর্তমানে খালি। কোনো ইমেইল বা কোড আসলে স্বয়ংক্রিয়ভাবে আপনাকে নোটিফিকেশন দেওয়া হবে।</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Copy Email", copy_text=CopyTextButton(text=active_email), style="success")],
            [InlineKeyboardButton("🔄 আবার চেক করুন", callback_data="check_active_inbox", style="primary")],
            [InlineKeyboardButton("🌐 ওয়েবে দেখুন", url=web_url)],
        ])
        await status_msg.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    # Mails found
    text = (
        f"📥 <b>আপনার বর্তমান ইনবক্স ({len(mails)} টি মেইল পাওয়া গেছে):</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 <b>Mailbox:</b> <code>{active_email}</code>\n"
        f"🌐 <b>Web Link:</b> <a href=\"{web_url}\">Müllfach Postfach</a>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    buttons = [
        [InlineKeyboardButton("📋 Copy Email", copy_text=CopyTextButton(text=active_email), style="success")]
    ]
    for m in mails[:10]:
        mid = m["id"]
        sender = m.get("sender_name") or m.get("sender_email") or "Unknown"
        subject = m.get("subject") or "(No Subject)"
        date_str = m.get("received_at", "")
        item_code = (
            await get_saved_otp(active_email, mid)
            or extract_otp_code(subject)
            or extract_otp_code(f"{sender} {subject}")
        )

        lower_s = (str(sender) + " " + str(subject)).lower()
        if "instagram" in lower_s:
            icon = "📸"
        elif "whatsapp" in lower_s:
            icon = "💬"
        elif "google" in lower_s:
            icon = "🇬"
        elif "telegram" in lower_s:
            icon = "✈️"
        elif "facebook" in lower_s or "meta" in lower_s:
            icon = "👥"
        elif "tiktok" in lower_s:
            icon = "🎵"
        else:
            icon = "🔑" if item_code else "📖"

        display_subj = f"{icon} {subject[:18]}"

        row = [InlineKeyboardButton(display_subj, callback_data=f"read_mail:{mid}", style="primary")]
        if item_code:
            row.append(InlineKeyboardButton(f"📋 {item_code}", copy_text=CopyTextButton(text=item_code), style="success"))
        buttons.append(row)

        s_name = html.escape(sender)
        s_subj = html.escape(subject)
        s_date = html.escape(date_str)
        text += f"{icon} <b>{s_subj}</b>\n👤 প্রেরক: <code>{s_name}</code>\n⏰ সময়: {s_date}\n"
        if item_code:
            text += f"🔑 <b>কোড:</b> <code>{html.escape(item_code)}</code>\n"
        text += "\n"

    buttons.append([InlineKeyboardButton("🔄 রিফ্রেশ ইনবক্স", callback_data="check_active_inbox", style="primary")])
    buttons.append([InlineKeyboardButton("🌐 ওয়েবে ফুল দেখুন", url=web_url)])

    await status_msg.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

async def check_active_inbox_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback for quick inbox refresh"""
    query = update.callback_query
    await query.answer("ইনবক্স রিফ্রেশ করা হচ্ছে...")
    user_id = update.effective_user.id
    db_user = await get_or_create_user(user_id)
    active_email = db_user.get("active_email")

    if not active_email:
        await query.edit_message_text("⚠️ আপনার কোনো সক্রিয় মেইল নেই।")
        return

    username = active_email.split("@")[0]
    web_url = make_web_url(username)

    async with httpx.AsyncClient() as client:
        await sync_mailbox(client)
        mails = await get_mailbox_messages(client, active_email)

    if not mails:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Copy Email", copy_text=CopyTextButton(text=active_email), style="success")],
            [InlineKeyboardButton("🔄 আবার রিফ্রেশ", callback_data="check_active_inbox", style="primary")],
            [InlineKeyboardButton("🌐 ওয়েবে দেখুন", url=web_url)],
        ])
        await query.edit_message_text(
            f"📭 <b>ইনবক্স এখনো খালি!</b>\n\n"
            f"📧 <b>মেইল:</b> <code>{active_email}</code>\n"
            f"<i>(ট্যাপ করলেই কপি হয়ে যাবে)</i>\n\n"
            f"🌐 <b>ওয়েব:</b> <a href=\"{web_url}\">Postfach Link</a>\n\n"
            f"অপেক্ষা করুন, নতুন ইমেইল আসলে বট সাথে সাথে আপনাকে পাঠিয়ে দেবে।",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    text = (
        f"📥 <b>আপনার বর্তমান ইনবক্স ({len(mails)} টি মেইল পাওয়া গেছে):</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 <b>Mailbox:</b> <code>{active_email}</code>\n"
        f"🌐 <b>Web Link:</b> <a href=\"{web_url}\">Postfach Link</a>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    buttons = [
        [InlineKeyboardButton("📋 Copy Email", copy_text=CopyTextButton(text=active_email), style="success")]
    ]
    for m in mails[:10]:
        mid = m["id"]
        sender = m.get("sender_name") or m.get("sender_email") or "Unknown"
        subject = m.get("subject") or "(No Subject)"
        date_str = m.get("received_at", "")
        item_code = (
            await get_saved_otp(active_email, mid)
            or extract_otp_code(subject)
            or extract_otp_code(f"{sender} {subject}")
        )

        lower_s = (str(sender) + " " + str(subject)).lower()
        if "instagram" in lower_s:
            icon = "📸"
        elif "whatsapp" in lower_s:
            icon = "💬"
        elif "google" in lower_s:
            icon = "🇬"
        elif "telegram" in lower_s:
            icon = "✈️"
        elif "facebook" in lower_s or "meta" in lower_s:
            icon = "👥"
        elif "tiktok" in lower_s:
            icon = "🎵"
        else:
            icon = "🔑" if item_code else "📖"

        display_subj = f"{icon} {subject[:18]}"

        row = [InlineKeyboardButton(display_subj, callback_data=f"read_mail:{mid}", style="primary")]
        if item_code:
            row.append(InlineKeyboardButton(f"📋 {item_code}", copy_text=CopyTextButton(text=item_code), style="success"))
        buttons.append(row)

        s_name = html.escape(sender)
        s_subj = html.escape(subject)
        s_date = html.escape(date_str)
        text += f"{icon} <b>{s_subj}</b>\n👤 প্রেরক: <code>{s_name}</code>\n⏰ সময়: {s_date}\n"
        if item_code:
            text += f"🔑 <b>কোড:</b> <code>{html.escape(item_code)}</code>\n"
        text += "\n"

    buttons.append([InlineKeyboardButton("🔄 রিফ্রেশ", callback_data="check_active_inbox", style="primary")])
    buttons.append([InlineKeyboardButton("🌐 ওয়েবে দেখুন", url=web_url)])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

async def read_mail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays single mail details when user clicks Read button"""
    query = update.callback_query
    await query.answer()
    
    mail_id = int(query.data.split(":")[1])
    user_id = update.effective_user.id
    db_user = await get_or_create_user(user_id)
    active_email = db_user.get("active_email")
    target_email = await get_email_by_message_id(mail_id) or active_email

    if not target_email:
        await query.answer("কোনো অ্যাক্টিভ মেইল নেই!", show_alert=True)
        return

    async with httpx.AsyncClient() as client:
        mail_data = await get_single_mail(client, target_email, mail_id)

    if not mail_data:
        await query.answer("মেইলের বিস্তারিত লোড করা সম্ভব হয়নি।", show_alert=True)
        return

    subject = mail_data.get("subject") or "(No Subject)"
    sender = mail_data.get("sender_name") or mail_data.get("sender_email") or "Unknown"
    date_str = mail_data.get("received_at", "")
    
    body_html = mail_data.get("body_html", "")
    raw_body = mail_data.get("body_text") or strip_html_tags(body_html)
    otp = (
        await get_saved_otp(target_email, mail_id)
        or extract_otp_code(subject)
        or extract_otp_code(raw_body, html_content=body_html)
        or extract_otp_code(f"{sender} {subject} {raw_body}", html_content=body_html)
    )

    lower_all = (str(sender) + " " + str(subject) + " " + str(raw_body)).lower()
    service_name = ""
    service_icon = "🔑"
    if "instagram" in lower_all:
        service_name = "Instagram"
        service_icon = "📸"
    elif "whatsapp" in lower_all:
        service_name = "WhatsApp"
        service_icon = "💬"
    elif "google" in lower_all:
        service_name = "Google"
        service_icon = "🇬"
    elif "facebook" in lower_all or "meta" in lower_all:
        service_name = "Facebook"
        service_icon = "👥"
    elif "telegram" in lower_all:
        service_name = "Telegram"
        service_icon = "✈️"
    elif "tiktok" in lower_all:
        service_name = "TikTok"
        service_icon = "🎵"
    elif "twitter" in lower_all or " x " in lower_all:
        service_name = "X (Twitter)"
        service_icon = "✖️"
    elif "binance" in lower_all or "bybit" in lower_all or "crypto" in lower_all:
        service_name = "Binance / Crypto"
        service_icon = "🪙"
    elif "steam" in lower_all:
        service_name = "Steam"
        service_icon = "🎮"
    elif "discord" in lower_all:
        service_name = "Discord"
        service_icon = "🎮"
    elif "microsoft" in lower_all:
        service_name = "Microsoft"
        service_icon = "🪟"
    elif "netflix" in lower_all:
        service_name = "Netflix"
        service_icon = "🎬"
    elif "amazon" in lower_all:
        service_name = "Amazon"
        service_icon = "📦"
    elif "apple" in lower_all:
        service_name = "Apple"
        service_icon = "🍎"
    elif "openai" in lower_all or "chatgpt" in lower_all:
        service_name = "OpenAI"
        service_icon = "🤖"
    elif "uber" in lower_all:
        service_name = "Uber"
        service_icon = "🚗"

    header_title = f"{service_icon} <b>{service_name} ইমেইল বিবরণ:</b>" if service_name else "✉️ <b>ইমেইল বিবরণ:</b>"
    code_label = f"{service_name} OTP" if service_name else "OTP / Code"

    otp_section = ""
    if otp:
        otp_section = f"\n━━━━━━━━━━━━━━━━━━━━━\n🔑 <b>{code_label}:</b> <code>{html.escape(str(otp))}</code>\n"

    # Truncate preview if too long
    body_preview = raw_body[:1000] if raw_body else "(No text content)"

    text = (
        f"{header_title}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>Subject:</b> <b>{html.escape(subject)}</b>\n"
        f"👤 <b>From:</b> <code>{html.escape(sender)}</code>\n"
        f"⏰ <b>Date:</b> {html.escape(date_str)}"
        f"{otp_section}"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 <b>Message Content:</b>\n\n"
        f"{html.escape(body_preview)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
    )

    username = target_email.split("@")[0]
    web_url = make_web_url(username)

    keyboard_rows = []
    if otp:
        keyboard_rows.append([
            InlineKeyboardButton(f"📋 {otp}", copy_text=CopyTextButton(text=otp), style="success")
        ])
    keyboard_rows.append([
        InlineKeyboardButton("⬅️ ইনবক্স", callback_data="check_active_inbox", style="primary"),
        InlineKeyboardButton("🌐 ওয়েবে ফুল দেখুন", url=web_url),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        await query.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        plain_text = (
            f"✉️ ইমেইল বিবরণ:\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 Subject: {subject}\n"
            f"👤 From: {sender}\n"
            f"⏰ Date: {date_str}\n"
        )
        if otp:
            plain_text += f"━━━━━━━━━━━━━━━━━━━━━\n🔑 OTP / Code: {otp}\n"
        plain_text += (
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📄 Message Content:\n\n"
            f"{body_preview}\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        await query.edit_message_text(
            plain_text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )

# ────────────────────────────────────────────────────────────────
# 🗑️ Delete Mailbox Handler
# ────────────────────────────────────────────────────────────────

async def delete_mailbox_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles deletion of current active mailbox"""
    user_id = update.effective_user.id
    db_user = await get_or_create_user(user_id)
    active_email = db_user.get("active_email")

    if not active_email:
        await update.message.reply_text(
            "⚠️ আপনার বর্তমানে কোনো অ্যাক্টিভ মেইল নেই!",
            reply_markup=get_main_keyboard(user_id),
        )
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑️ হ্যাঁ, ডিলিট করুন", callback_data=f"confirm_delete:{active_email}", style="danger"),
            InlineKeyboardButton("❌ না, বাতিল", callback_data="close_menu", style="primary"),
        ]
    ])

    await update.message.reply_text(
        f"🗑️ <b>আপনি কি সত্যিই বর্তমান মেইলটি মুছে ফেলতে চান?</b>\n\n"
        f"📧 <code>{active_email}</code>\n\n"
        f"ডিলিট করলে এই মেইলের নোটিফিকেশন আর আসবে না।",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )

async def confirm_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    email_to_delete = query.data.split(":", 1)[1]
    
    await delete_saved_email(user_id, email_to_delete)
    await query.edit_message_text(
        f"✅ <b>ইমেইলটি ডিলিট করা হয়েছে:</b> <code>{email_to_delete}</code>\n\n"
        f"নতুন মেইল তৈরি করতে <b>📧 Create Mail</b> বাটনে চাপ দিন।",
        parse_mode=ParseMode.HTML,
    )

# ────────────────────────────────────────────────────────────────
# ⚙️ Setting Fuetch Handler
# ────────────────────────────────────────────────────────────────

async def setting_fuetch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows settings menu"""
    user_id = update.effective_user.id
    db_user = await get_or_create_user(user_id)
    auto_fwd = db_user.get("auto_forward", 1)
    
    auto_btn_text = "🔔 অটো-ফরওয়ার্ড: চালু ✅" if auto_fwd == 1 else "🔕 অটো-ফরওয়ার্ড: বন্ধ ❌"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(auto_btn_text, callback_data="toggle_autoforward", style="primary")],
        [InlineKeyboardButton("🧹 ক্লিয়ার ১০-দিনের হিস্ট্রি", callback_data="confirm_clear_history", style="danger")],
        [InlineKeyboardButton("ℹ️ About MailFuetch", callback_data="about_mailfuetch", style="primary")],
        [InlineKeyboardButton("❌ মেন্যু বন্ধ করুন", callback_data="close_menu", style="danger")],
    ])

    await update.message.reply_text(
        "⚙️ <b>MailFuetch Settings:</b>\n\n"
        "নিচে থেকে আপনার পছন্দমতো সেটিংস পরিবর্তন করুন:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )

async def toggle_autoforward_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    new_val = await toggle_auto_forward(user_id)
    await query.answer(f"অটো-ফরওয়ার্ড {'চালু' if new_val == 1 else 'বন্ধ'} করা হয়েছে!")
    
    auto_btn_text = "🔔 অটো-ফরওয়ার্ড: চালু ✅" if new_val == 1 else "🔕 অটো-ফরওয়ার্ড: বন্ধ ❌"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(auto_btn_text, callback_data="toggle_autoforward", style="primary")],
        [InlineKeyboardButton("🧹 ক্লিয়ার ১০-দিনের হিস্ট্রি", callback_data="confirm_clear_history", style="danger")],
        [InlineKeyboardButton("ℹ️ About MailFuetch", callback_data="about_mailfuetch", style="primary")],
        [InlineKeyboardButton("❌ মেন্যু বন্ধ করুন", callback_data="close_menu", style="danger")],
    ])
    await query.edit_message_reply_markup(reply_markup=keyboard)

async def about_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    about_text = (
        f"🌐 <b>Müllfach.de — অফিশিয়াল ওয়েবসাইট প্ল্যাটফর্ম:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Müllfach.de</b> হলো একটি আধুনিক, দ্রুত এবং অত্যন্ত বিশ্বস্ত ডিসপোজেবল টেম্পোরারি ইমেইল প্ল্যাটফর্ম। "
        f"অনাকাঙ্ক্ষিত স্প্যাম, ফিশিং, ডেটা ট্র্যাকিং ও বিজ্ঞাপন থেকে আপনার ব্যক্তিগত ইমেইলকে সুরক্ষিত রাখতে এটি তৈরি করা হয়েছে।\n\n"
        f"✨ <b>ওয়েবসাইটের মূল সুবিধাসমূহ:</b>\n"
        f"• <b>১০০% ফ্রি ও কোনো পাসওয়ার্ড ছাড়াই:</b> কোনো প্রকার রেজিস্ট্রেশন বা ক্রেডিট কার্ড ছাড়াই তাৎক্ষণিক ইনবক্স অ্যাক্সেস।\n"
        f"• <b>৩০ দিনের দীর্ঘস্থায়ী মেয়াদ:</b> অন্যান্য টেম্পোরারি মেইল সাইট যেখানে ১০-১৫ মিনিটে ইনবক্স মুছে দেয়, সেখানে Müllfach-এ মেইল ৩০ দিন পর্যন্ত সংরক্ষিত থাকে!\n"
        f"• <b>হাই-স্পিড IMAP সিঙ্ক:</b> সরাসরি ডেডিকেটেড মেইল সার্ভারের সাথে লাইভ সংযোগ, ফলে ২ সেকেন্ডে মেসেজ ও ওটিপি চলে আসে।\n"
        f"• <b>কাস্টম এড্রেস সাপোর্ট:</b> নিজের পছন্দের নাম বা ইউজারনেম দিয়ে যেকোনো সময় পোস্টফাক তৈরি করার স্বাধীনতা।\n"
        f"• <b>অটো স্প্যাম ও ম্যালওয়্যার প্রোটেকশন:</b> নিরাপদ ব্রাউজিং ও ইনবক্স অভিজ্ঞতা।\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <b>অফিশিয়াল ওয়েবসাইট:</b> <a href=\"{MUELLFACH_BASE_URL}\">{MUELLFACH_BASE_URL}</a>"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 ভিজিট ওয়েবসাইট", url=MUELLFACH_BASE_URL)],
        [InlineKeyboardButton("⬅️ ব্যাক টু সেটিংস", callback_data="back_to_settings")],
    ])
    await query.edit_message_text(about_text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def clear_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚠️ হ্যাঁ, সব মুছুন", callback_data="do_clear_history", style="danger"),
            InlineKeyboardButton("❌ বাতিল", callback_data="back_to_settings", style="primary"),
        ]
    ])
    await query.edit_message_text(
        "⚠️ <b>সতর্কতা:</b> আপনি কি সত্যিই আপনার সেভ করা সব মেইলের হিস্ট্রি মুছে ফেলতে চান?",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )

async def do_clear_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    await clear_all_saved_emails(user_id)
    await query.edit_message_text("✅ আপনার সমস্ত সেভ করা মেইল হিস্ট্রি ক্লিয়ার করা হয়েছে!")

async def back_to_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    db_user = await get_or_create_user(user_id)
    auto_fwd = db_user.get("auto_forward", 1)
    auto_btn_text = "🔔 অটো-ফরওয়ার্ড: চালু ✅" if auto_fwd == 1 else "🔕 অটো-ফরওয়ার্ড: বন্ধ ❌"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(auto_btn_text, callback_data="toggle_autoforward", style="primary")],
        [InlineKeyboardButton("🧹 ক্লিয়ার ১০-দিনের হিস্ট্রি", callback_data="confirm_clear_history", style="danger")],
        [InlineKeyboardButton("ℹ️ About MailFuetch", callback_data="about_mailfuetch", style="primary")],
        [InlineKeyboardButton("❌ মেন্যু বন্ধ করুন", callback_data="close_menu", style="danger")],
    ])
    await query.edit_message_text(
        "⚙️ <b>MailFuetch Settings:</b>\n\nনিচে থেকে আপনার পছন্দমতো সেটিংস পরিবর্তন করুন:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )

async def close_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.delete_message()

# ────────────────────────────────────────────────────────────────
# 👑 Admin Panel Handlers (Authorized for ADMIN_ID: 8929349073)
# ────────────────────────────────────────────────────────────────

async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens Admin Panel for Authorized Admin with Live Analytics Summary"""
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ আপনি এই প্যানেলটি ব্যবহার করার জন্য অনুমোদিত নন।")
        return

    analytics = await get_live_analytics()
    uptime_text = get_uptime_str()

    admin_text = (
        f"👑 <b>MailFuetch Admin Panel:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"স্বাগতম <b>{html.escape(user.first_name)}</b>! আপনার অ্যাডমিন ড্যাশবোর্ড সক্রিয় আছে।\n\n"
        f"📊 <b>লাইভ সিস্টেম অ্যানালিটিক্স:</b>\n"
        f"• 👥 মোট ইউজার সংখ্যা: <b>{analytics['total_users']}</b> জন\n"
        f"• 🌟 আজকের সক্রিয় ইউজার: <b>{analytics['active_today']}</b> জন\n"
        f"• ✉️ মোট তৈরি হওয়া মেইল: <b>{analytics['total_mails']}</b> টি\n"
        f"• 🔑 আজকের রিসিভ হওয়া OTP কোড: <b>{analytics['today_otps']}</b> টি\n"
        f"• 📬 আজকের মোট রিসিভ মেসেজ: <b>{analytics['today_messages']}</b> টি\n"
        f"• ⚡ বর্তমানে অটো-লিসেনার সক্রিয়: <b>{analytics['listening_now']}</b> টি মেইলে\n"
        f"• ⏳ একটানা লাইভ চলছে (Uptime): <b>{uptime_text}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"নিচের বাটনগুলো দিয়ে যেকোনো সেকশনে প্রবেশ করুন 👇"
    )
    await update.message.reply_text(
        admin_text,
        reply_markup=ADMIN_KEYBOARD,
        parse_mode=ParseMode.HTML,
    )

async def admin_live_analytics_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refreshes and displays live system analytics"""
    user = update.effective_user
    if user.id != ADMIN_ID:
        return

    analytics = await get_live_analytics()
    uptime_text = get_uptime_str()

    text = (
        f"📊 <b>MailFuetch লাইভ সিস্টেম অ্যানালিটিক্স:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>মোট ইউজার:</b> <b>{analytics['total_users']}</b> জন\n"
        f"🌟 <b>আজকের সক্রিয় ইউজার:</b> <b>{analytics['active_today']}</b> জন\n"
        f"✉️ <b>মোট তৈরি হওয়া ইমেইল:</b> <b>{analytics['total_mails']}</b> টি\n"
        f"🔑 <b>আজকের মোট OTP কোড:</b> <b>{analytics['today_otps']}</b> টি\n"
        f"📬 <b>আজকের মোট মেসেজ:</b> <b>{analytics['today_messages']}</b> টি\n"
        f"⚡ <b>বর্তমানে রিয়েল-টাইম লিসেনিং:</b> <b>{analytics['listening_now']}</b> টি মেইলে\n"
        f"⏳ <b>একটানা লাইভ চলছে (Uptime):</b> <b>{uptime_text}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=ADMIN_KEYBOARD)

async def admin_backup_db_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends current SQLite database file to Admin directly in chat"""
    user = update.effective_user
    if user.id != ADMIN_ID:
        return

    if not os.path.exists(DB_PATH):
        await update.message.reply_text("❌ ডাটাবেজ ফাইল পাওয়া যায়নি!", reply_markup=ADMIN_KEYBOARD)
        return

    status_msg = await update.message.reply_text("⏳ ডাটাবেজ ব্যাকআপ তৈরি হচ্ছে...")

    file_size_kb = os.path.getsize(DB_PATH) / 1024
    size_str = f"{file_size_kb:.1f} KB" if file_size_kb < 1024 else f"{(file_size_kb/1024):.2f} MB"
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    backup_filename = f"mailfuetch_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"

    try:
        with open(DB_PATH, "rb") as db_file:
            caption = (
                f"💾 <b>MailFuetch Database Backup</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 তারিখ ও সময়: <code>{timestamp_str}</code>\n"
                f"📦 ফাইল সাইজ: <b>{size_str}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>এই ফাইলটিতে সমস্ত ইউজার ও সেভ করা মেইলের রেকর্ড সংরক্ষিত আছে।</i>"
            )
            await context.bot.send_document(
                chat_id=user.id,
                document=db_file,
                filename=backup_filename,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"❌ ব্যাকআপ ফাইল পাঠাতে সমস্যা হয়েছে: {e}")

async def admin_back_to_user_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switches back to user main menu"""
    user = update.effective_user
    await update.message.reply_text(
        "🔙 <b>ইউজার মেন্যুতে ফিরে যাওয়া হয়েছে।</b>",
        reply_markup=get_main_keyboard(user.id),
        parse_mode=ParseMode.HTML,
    )

async def admin_users_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays user list with total emails and daily codes/messages"""
    user = update.effective_user
    if user.id != ADMIN_ID:
        return

    status_msg = await update.message.reply_text("⏳ ইউজার ডেটা প্রস্তুত করা হচ্ছে...")
    users = await get_all_users_stats()
    
    if not users:
        await status_msg.edit_text("📭 ডাটাবেজে এখনো কোনো ইউজার নেই।")
        return

    header = f"👥 <b>সকল ইউজারের তালিকা (মোট: {len(users)} জন):</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    chunks = []
    current_chunk = header
    
    for idx, u in enumerate(users, 1):
        uname = f"@{u['username']}" if u['username'] else "No Username"
        fname = html.escape(u['first_name'])
        entry = (
            f"<b>{idx}. {fname}</b> ({uname})\n"
            f"   🆔 ID: <code>{u['user_id']}</code>\n"
            f"   ✉️ মোট মেইল তৈরি: <b>{u['total_mails']}</b> টি\n"
            f"   🔑 আজকের কোড: <b>{u['daily_codes']}</b> টি | আজকের মেসেজ: <b>{u['daily_messages']}</b> টি\n"
            f"   📦 সর্বমোট মেসেজ: <b>{u['total_messages']}</b> টি\n"
            f"   📧 সক্রিয় মেইল: <code>{html.escape(u['active_email'])}</code>\n"
            f"   ─────────────────────\n"
        )
        if len(current_chunk) + len(entry) > 3800:
            chunks.append(current_chunk)
            current_chunk = entry
        else:
            current_chunk += entry

    if current_chunk:
        chunks.append(current_chunk)

    await status_msg.delete()
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode=ParseMode.HTML, reply_markup=ADMIN_KEYBOARD)

async def admin_top_users_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays top 10 users ranked by mail creation and messages received"""
    user = update.effective_user
    if user.id != ADMIN_ID:
        return

    top_users = await get_top_users(10)
    if not top_users:
        await update.message.reply_text("📭 ডাটাবেজে কোনো ইউজার অ্যাক্টিভিটি পাওয়া যায়নি।", reply_markup=ADMIN_KEYBOARD)
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    text = "🏆 <b>টপ ১০ ইউজার র‍্যাংকিং:</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    for idx, u in enumerate(top_users):
        medal = medals[idx] if idx < len(medals) else f"#{idx+1}"
        uname = f"@{u['username']}" if u['username'] else "No Username"
        fname = html.escape(u['first_name'])
        text += (
            f"{medal} <b>{fname}</b> ({uname})\n"
            f"   🆔 ID: <code>{u['user_id']}</code>\n"
            f"   ✉️ তৈরি করা মেইল: <b>{u['total_mails']}</b> টি\n"
            f"   📬 মোট প্রাপ্ত মেসেজ: <b>{u['total_messages']}</b> টি (🔑 কোড: <b>{u['total_codes']}</b> টি)\n\n"
        )
    text += "━━━━━━━━━━━━━━━━━━━━━"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=ADMIN_KEYBOARD)

async def admin_broadcast_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompts admin to enter broadcast message"""
    user = update.effective_user
    if user.id != ADMIN_ID:
        return ConversationHandler.END

    await update.message.reply_text(
        "📢 <b>সকল ইউজারের কাছে ব্রডকাস্ট মেসেজ পাঠান:</b>\n\n"
        "যে মেসেজটি পাঠাতে চান তা নিচে লিখে রিপ্লাই দিন:\n\n"
        "<i>(বাতিল করতে চাইলে /cancel লিখুন)</i>",
        parse_mode=ParseMode.HTML,
    )
    return WAITING_BROADCAST

async def admin_broadcast_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the broadcast message to all users"""
    user = update.effective_user
    if user.id != ADMIN_ID:
        return ConversationHandler.END

    broadcast_text = update.message.text
    user_ids = await get_all_user_ids()

    if not user_ids:
        await update.message.reply_text("📭 কোনো ইউজার পাওয়া যায়নি।", reply_markup=ADMIN_KEYBOARD)
        return ConversationHandler.END

    status_msg = await update.message.reply_text(f"🚀 ব্রডকাস্ট শুরু হচ্ছে... মোট ইউজার: {len(user_ids)} জন")

    success = 0
    failed = 0

    formatted_msg = (
        f"📢 <b>অফিশিয়াল নোটিফিকেশন:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{html.escape(broadcast_text)}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <i>MailFuetch Bot Team</i>"
    )

    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=formatted_msg,
                parse_mode=ParseMode.HTML,
            )
            success += 1
            await asyncio.sleep(0.04)
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"✅ <b>ব্রডকাস্ট সম্পন্ন হয়েছে!</b>\n\n"
        f"• মোট ইউজার: {len(user_ids)} জন\n"
        f"• সফলভাবে পৌঁছেছে: <b>{success}</b> জন\n"
        f"• ব্যর্থ/ব্লক করেছে: <b>{failed}</b> জন",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END

async def admin_direct_msg_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompts admin to send direct message to a specific user"""
    user = update.effective_user
    if user.id != ADMIN_ID:
        return ConversationHandler.END

    await update.message.reply_text(
        "💬 <b>নির্দিষ্ট ইউজারকে মেসেজ পাঠান:</b>\n\n"
        "নিচের ফরম্যাটে ইউজার আইডি এবং মেসেজটি লিখুন:\n"
        "<code>USER_ID: আপনার মেসেজ</code>\n\n"
        "💡 <i>যেমন:</i>\n"
        "<code>8929349073: আপনার সমস্যা সমাধান করা হয়েছে।</code>\n\n"
        "❌ বাতিল করতে /cancel লিখুন।",
        parse_mode=ParseMode.HTML,
    )
    return WAITING_DIRECT_MSG

async def admin_direct_msg_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends direct message to specified user"""
    user = update.effective_user
    if user.id != ADMIN_ID:
        return ConversationHandler.END

    raw_text = update.message.text.strip()
    if ":" not in raw_text:
        await update.message.reply_text(
            "❌ <b>ভুল ফরম্যাট!</b>\n\n"
            "অনুগ্রহ করে <code>USER_ID: মেসেজ</code> এভাবে লিখুন (মাঝখানে কোলন : দিতে হবে)।\n\n"
            "আবার চেষ্টা করুন অথবা /cancel লিখুন:",
            parse_mode=ParseMode.HTML,
        )
        return WAITING_DIRECT_MSG

    parts = raw_text.split(":", 1)
    target_id_str = parts[0].strip()
    msg_content = parts[1].strip()

    if not target_id_str.isdigit():
        await update.message.reply_text(
            "❌ ইউজার আইডিটি শুধুমাত্র সংখ্যা হতে হবে! আবার লিখুন:",
            parse_mode=ParseMode.HTML,
        )
        return WAITING_DIRECT_MSG

    target_uid = int(target_id_str)
    
    send_text = (
        f"💬 <b>অ্যাডমিন থেকে বার্তা:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{html.escape(msg_content)}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>প্রয়োজনে মেসেজের উত্তর দিতে পারেন।</i>"
    )

    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text=send_text,
            parse_mode=ParseMode.HTML,
        )
        await update.message.reply_text(
            f"✅ <b>মেসেজ সফলভাবে পৌঁছেছে!</b>\n\n"
            f"👤 ইউজার আইডি: <code>{target_uid}</code>\n"
            f"📝 মেসেজ: {html.escape(msg_content)}",
            parse_mode=ParseMode.HTML,
            reply_markup=ADMIN_KEYBOARD,
        )
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(
            f"❌ <b>মেসেজ পাঠানো সম্ভব হয়নি!</b>\n\n"
            f"কারণ: <code>{html.escape(str(e))}</code>\n\n"
            f"<i>(হয়তো ইউজার বটটি কখনো স্টার্ট করেনি বা বট ব্লক করে রেখেছে)</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=ADMIN_KEYBOARD,
        )
        return ConversationHandler.END

async def cancel_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels ongoing broadcast or direct message action"""
    await update.message.reply_text("❌ অপারেশন বাতিল করা হয়েছে।", reply_markup=ADMIN_KEYBOARD)
    return ConversationHandler.END

# ────────────────────────────────────────────────────────────────
# 🚀 Real-time Background Polling Task (2-3 Seconds Polling)
# ────────────────────────────────────────────────────────────────

async def background_inbox_poller(app: Application):
    """Polls muellfach.de every 2-3 seconds for active users and forwards new mails"""
    logger.info("Real-time auto-inbox poller started.")
    
    timeout_cfg = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout_cfg) as client:
        while True:
            try:
                # 1. Fetch all users who have an active email and auto_forward enabled
                active_users = await get_active_users_to_poll()
                if not active_users:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                # 2. Trigger IMAP sync on muellfach server to pull incoming emails
                await sync_mailbox(client)

                # Group user_ids by active_email
                email_to_users = {}
                for uid, email in active_users:
                    if email not in email_to_users:
                        email_to_users[email] = []
                    email_to_users[email].append(uid)

                # 3. Check inbox for each unique active email in parallel
                sem = asyncio.Semaphore(15)

                async def process_user_mailbox(email: str, uids: list[int]):
                    async with sem:
                        try:
                            mails = await get_mailbox_messages(client, email)
                            if not mails:
                                return

                            # Process in arrival order (oldest first, newest last) like real SMS
                            for mail_item in reversed(mails):
                                mid = mail_item.get("id")
                                if not mid:
                                    continue

                                already_seen = await is_message_seen(email, mid)
                                if already_seen:
                                    continue

                                # Fetch full mail details
                                full_mail = await get_single_mail(client, email, mid)
                                if full_mail:
                                    subject = full_mail.get("subject") or "(No Subject)"
                                    sender = (
                                        full_mail.get("sender_name")
                                        or full_mail.get("sender_email")
                                        or "Unknown"
                                    )
                                    date_str = full_mail.get("received_at", "")
                                    body_raw = full_mail.get("body_text") or strip_html_tags(
                                        full_mail.get("body_html", "")
                                    )
                                    body_html = full_mail.get("body_html", "")
                                else:
                                    # Fallback to mail_item data so email is never lost!
                                    subject = mail_item.get("subject") or "(No Subject)"
                                    sender = (
                                        mail_item.get("sender_name")
                                        or mail_item.get("sender_email")
                                        or "Unknown"
                                    )
                                    date_str = mail_item.get("received_at", "")
                                    body_raw = ""
                                    body_html = ""

                                # Universal multi-stage OTP detection across all services worldwide
                                otp = (
                                    extract_otp_code(subject)
                                    or extract_otp_code(body_raw, html_content=body_html)
                                    or extract_otp_code(f"{sender} {subject} {body_raw}", html_content=body_html)
                                )

                                # Smart Service Name and Icon Detection for clean branding
                                lower_all = (str(sender) + " " + str(subject) + " " + str(body_raw)).lower()
                                service_name = ""
                                service_icon = "🔑"
                                if "instagram" in lower_all:
                                    service_name = "Instagram"
                                    service_icon = "📸"
                                elif "whatsapp" in lower_all:
                                    service_name = "WhatsApp"
                                    service_icon = "💬"
                                elif "google" in lower_all:
                                    service_name = "Google"
                                    service_icon = "🇬"
                                elif "facebook" in lower_all or "meta" in lower_all:
                                    service_name = "Facebook"
                                    service_icon = "👥"
                                elif "telegram" in lower_all:
                                    service_name = "Telegram"
                                    service_icon = "✈️"
                                elif "tiktok" in lower_all:
                                    service_name = "TikTok"
                                    service_icon = "🎵"
                                elif "twitter" in lower_all or " x " in lower_all:
                                    service_name = "X (Twitter)"
                                    service_icon = "✖️"
                                elif "binance" in lower_all or "bybit" in lower_all or "crypto" in lower_all:
                                    service_name = "Binance / Crypto"
                                    service_icon = "🪙"
                                elif "steam" in lower_all:
                                    service_name = "Steam"
                                    service_icon = "🎮"
                                elif "discord" in lower_all:
                                    service_name = "Discord"
                                    service_icon = "🎮"
                                elif "microsoft" in lower_all:
                                    service_name = "Microsoft"
                                    service_icon = "🪟"
                                elif "netflix" in lower_all:
                                    service_name = "Netflix"
                                    service_icon = "🎬"
                                elif "amazon" in lower_all:
                                    service_name = "Amazon"
                                    service_icon = "📦"
                                elif "apple" in lower_all:
                                    service_name = "Apple"
                                    service_icon = "🍎"
                                elif "openai" in lower_all or "chatgpt" in lower_all:
                                    service_name = "OpenAI"
                                    service_icon = "🤖"
                                elif "uber" in lower_all:
                                    service_name = "Uber"
                                    service_icon = "🚗"

                                if service_name and otp:
                                    title_header = f"{service_icon} <b>{service_name} ভেরিফিকেশন কোড এসেছে!</b>"
                                    code_title = f"{service_name} OTP"
                                elif otp:
                                    title_header = "🔑 <b>ভেরিফিকেশন কোড (OTP) এসেছে!</b>"
                                    code_title = "OTP / Code"
                                elif service_name:
                                    title_header = f"{service_icon} <b>{service_name} থেকে নতুন মেইল এসেছে!</b>"
                                    code_title = "Code"
                                else:
                                    title_header = "📬 <b>নতুন ইমেইল এসেছে!</b>"
                                    code_title = "OTP / Code"

                                otp_section = ""
                                if otp:
                                    otp_section = (
                                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"🔑 <b>{code_title}:</b> <code>{html.escape(str(otp))}</code>\n"
                                        f"<i>(নিচের বাটনে ট্যাপ করলেই কোড কপি হবে)</i>\n"
                                    )

                                body_snippet = (
                                    body_raw[:600] if body_raw else ("(No content)" if full_mail else "(Loading preview... click Read Details)")
                                )

                                username = email.split("@")[0]
                                web_url = make_web_url(username)

                                sender_esc = html.escape(str(sender))
                                subject_esc = html.escape(str(subject))
                                email_esc = html.escape(str(email))
                                date_esc = html.escape(str(date_str))
                                body_esc = html.escape(str(body_snippet))

                                msg_text = (
                                    f"{title_header}\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"📧 <b>To:</b> <code>{email_esc}</code>\n"
                                    f"👤 <b>From:</b> <code>{sender_esc}</code>\n"
                                    f"📝 <b>Subject:</b> <b>{subject_esc}</b>\n"
                                    f"⏰ <b>Time:</b> {date_esc}\n"
                                    f"{otp_section}"
                                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"📄 <b>Message Preview:</b>\n\n"
                                    f"{body_esc}\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━"
                                )

                                keyboard_rows = []
                                if otp:
                                    keyboard_rows.append([
                                        InlineKeyboardButton(
                                            f"📋 {otp}",
                                            copy_text=CopyTextButton(text=otp),
                                            style="success",
                                        )
                                    ])
                                keyboard_rows.append([
                                    InlineKeyboardButton(
                                        "📖 বিস্তারিত",
                                        callback_data=f"read_mail:{mid}",
                                        style="primary",
                                    ),
                                    InlineKeyboardButton("🌐 ওয়েবে দেখুন", url=web_url),
                                ])

                                keyboard = InlineKeyboardMarkup(keyboard_rows)

                                # Forward to each user listening to this email
                                sent_any = False
                                for uid in uids:
                                    try:
                                        await app.bot.send_message(
                                            chat_id=uid,
                                            text=msg_text,
                                            reply_markup=keyboard,
                                            parse_mode=ParseMode.HTML,
                                            disable_web_page_preview=True,
                                        )
                                        sent_any = True
                                    except Exception as send_err:
                                        logger.warning(f"HTML send failed for user {uid}: {send_err}. Falling back to plain text...")
                                        try:
                                            plain_msg = (
                                                f"📬 নতুন ইমেইল এসেছে!\n"
                                                f"━━━━━━━━━━━━━━━━━━━━━\n"
                                                f"📧 To: {email}\n"
                                                f"👤 From: {sender}\n"
                                                f"📝 Subject: {subject}\n"
                                                f"⏰ Time: {date_str}\n"
                                            )
                                            if otp:
                                                plain_msg += f"━━━━━━━━━━━━━━━━━━━━━\n🔑 OTP / Code: {otp}\n"
                                            plain_msg += (
                                                f"━━━━━━━━━━━━━━━━━━━━━\n"
                                                f"📄 Message Preview:\n\n"
                                                f"{body_snippet}\n"
                                                f"━━━━━━━━━━━━━━━━━━━━━"
                                            )
                                            await app.bot.send_message(
                                                chat_id=uid,
                                                text=plain_msg,
                                                reply_markup=keyboard,
                                                disable_web_page_preview=True,
                                            )
                                            sent_any = True
                                        except Exception as plain_err:
                                            logger.error(f"Error sending plain text mail to user {uid}: {plain_err}")

                                # Mark as seen with extracted OTP for persistence
                                if sent_any:
                                    await mark_message_seen(email, mid, otp=otp)
                                    for uid in uids:
                                        await log_user_message(uid, email, mid, has_otp=(1 if otp else 0))

                        except Exception as email_err:
                            logger.error(f"Error polling email {email}: {email_err}")

                tasks = [process_user_mailbox(em, us) for em, us in email_to_users.items()]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

            except Exception as e:
                logger.error(f"Error in background_inbox_poller loop: {e}")

            await asyncio.sleep(POLL_INTERVAL)

async def poller_supervisor(app: Application):
    """Supervises the inbox poller so it automatically recovers from any unexpected errors"""
    while True:
        try:
            await background_inbox_poller(app)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"background_inbox_poller crashed: {e}. Restarting poller in 5s...", exc_info=True)
            await asyncio.sleep(5)

# ────────────────────────────────────────────────────────────────
# Lightweight Health Check Server (For Render Web Service Port Binding)
# ────────────────────────────────────────────────────────────────

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"MailFuetch Bot is running live!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        # Suppress HTTP log noise in Render console
        pass

def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logger.info(f"Health server successfully listening on port {port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Health server error on port {port}: {e}")

async def keep_alive_pinger():
    """Pings external Render URL every 10 minutes to prevent free-tier spin down"""
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not render_url:
        return
    logger.info(f"Keep-alive pinger active for: {render_url}")
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.sleep(600)  # Ping every 10 mins (Render sleeps after 15 mins)
            try:
                r = await client.get(render_url, timeout=10.0)
                logger.info(f"Keep-alive ping status: {r.status_code}")
            except Exception as e:
                logger.debug(f"Keep-alive ping error: {e}")

# ────────────────────────────────────────────────────────────────
# Application Startup and Main
# ────────────────────────────────────────────────────────────────

async def post_init(application: Application):
    """Initializes database, sets command menu to only /start, and spawns background polling worker"""
    await init_db()
    try:
        await application.bot.set_my_commands([
            BotCommand("start", "Start the bot")
        ])
    except Exception as e:
        logger.warning(f"Could not set bot commands: {e}")
    asyncio.create_task(poller_supervisor(application))
    asyncio.create_task(keep_alive_pinger())

def main():
    if not BOT_TOKEN:
        print("\n" + "=" * 60)
        print("❌ ERROR: BOT_TOKEN is not set!")
        print("Please set your BOT_TOKEN in the '.env' file or in config.py")
        print("Example: BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
        print("=" * 60 + "\n")
        return

    # Start health check server in background thread for Render Web Service
    threading.Thread(target=start_health_server, daemon=True).start()

    # Build Telegram application
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # 1. Admin Conversation Handlers
    broadcast_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^📢 Broadcast$"), admin_broadcast_prompt),
            CommandHandler("broadcast", admin_broadcast_prompt),
        ],
        states={
            WAITING_BROADCAST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_process)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_admin_action)],
    )
    app.add_handler(broadcast_conv)

    direct_msg_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^💬 Send to User$"), admin_direct_msg_prompt),
            CommandHandler("send", admin_direct_msg_prompt),
        ],
        states={
            WAITING_DIRECT_MSG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_direct_msg_process)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_admin_action)],
    )
    app.add_handler(direct_msg_conv)

    # 1.1 User Login Account Conversation Handler
    login_acc_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^(🔑 Login Acc|Login Acc|/login)$"), login_acc_prompt),
            CommandHandler("login", login_acc_prompt),
        ],
        states={
            WAITING_LOGIN_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, login_acc_process)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_login_acc)],
    )
    app.add_handler(login_acc_conv)

    # 2. Admin Action Handlers
    app.add_handler(CommandHandler("admin", admin_panel_handler))
    app.add_handler(CommandHandler("backup", admin_backup_db_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^(👑 Admin Panel|/admin)$"), admin_panel_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^📊 Live Analytics$"), admin_live_analytics_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^👥 Users List$"), admin_users_list_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🏆 Top (10 )?Users$"), admin_top_users_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💾 Database Backup$"), admin_backup_db_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🔙 Back to User Menu$"), admin_back_to_user_menu))

    # 3. Email Creation Handlers (1-Click)
    app.add_handler(CommandHandler(["create", "new"], create_mail_handler))
    app.add_handler(MessageHandler(filters.Regex(r"(Create Mail|Custom Email)"), create_mail_handler))
    app.add_handler(CallbackQueryHandler(create_mail_handler, pattern=r"^quick_create_mail$"))

    # 4. Main Menu Button Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.Regex(r"(Current Inbox|Current Email|Refresh Inbox)"), current_inbox_handler))
    app.add_handler(MessageHandler(filters.Regex(r"Saved Mails"), saved_mails_handler))
    app.add_handler(MessageHandler(filters.Regex(r"Delete Mailbox"), delete_mailbox_handler))
    app.add_handler(MessageHandler(filters.Regex(r"Setting Fuetch"), setting_fuetch_handler))

    # 3. Callback Handlers
    app.add_handler(CallbackQueryHandler(switch_mail_callback, pattern=r"^switch_mail:\d+$"))
    app.add_handler(CallbackQueryHandler(check_active_inbox_callback, pattern=r"^check_active_inbox$"))
    app.add_handler(CallbackQueryHandler(read_mail_callback, pattern=r"^read_mail:\d+$"))
    app.add_handler(CallbackQueryHandler(confirm_delete_callback, pattern=r"^confirm_delete:.+$"))
    app.add_handler(CallbackQueryHandler(toggle_autoforward_callback, pattern=r"^toggle_autoforward$"))
    app.add_handler(CallbackQueryHandler(about_callback, pattern=r"^about_mailfuetch$"))
    app.add_handler(CallbackQueryHandler(clear_history_callback, pattern=r"^confirm_clear_history$"))
    app.add_handler(CallbackQueryHandler(do_clear_history_callback, pattern=r"^do_clear_history$"))
    app.add_handler(CallbackQueryHandler(back_to_settings_callback, pattern=r"^back_to_settings$"))
    app.add_handler(CallbackQueryHandler(close_menu_callback, pattern=r"^close_menu$"))

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Exception while handling an update:", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "⚠️ দুঃখিত, কোনো সমস্যা হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন।"
                )
            except Exception:
                pass

    app.add_error_handler(error_handler)

    logger.info("MailFuetch Bot is starting polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
