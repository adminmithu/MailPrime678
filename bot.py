import asyncio
import logging
import os
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
    BOT_TOKEN,
    DOMAIN,
    POLL_INTERVAL,
    MUELLFACH_BASE_URL,
)
from database import (
    clear_all_saved_emails,
    delete_saved_email,
    get_active_users_to_poll,
    get_or_create_user,
    get_saved_email_by_id,
    get_saved_emails,
    init_db,
    is_message_seen,
    is_username_taken,
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

# State for ConversationHandler
WAITING_CUSTOM_NAME = 1

# Main Reply Keyboard with Native Button Background Colors
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [
            KeyboardButton("✏️ Custom Email", style="success"),
            KeyboardButton("🔄 Refresh Inbox", style="primary"),
        ],
        [
            KeyboardButton("📁 Saved Mails", style="primary"),
            KeyboardButton("⚙️ Setting Fuetch", style="primary"),
        ],
        [
            KeyboardButton("📋 Current Email"),
            KeyboardButton("🗑️ Delete Mailbox", style="danger"),
        ],
    ],
    resize_keyboard=True,
)

def make_web_url(username: str) -> str:
    return f"{MUELLFACH_BASE_URL}/postfach/{DOMAIN}/{username}"

# ────────────────────────────────────────────────────────────────
# Command Handlers
# ────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command"""
    user = update.effective_user
    db_user = await get_or_create_user(user.id)
    
    welcome_text = (
        f"👋 <b>স্বাগতম {user.first_name}! MailFuetch বটে আপনাকে স্বাগতম।</b>\n\n"
        f"এখান থেকে আপনি কোনো রেজিস্ট্রেশন ছাড়াই আনলিমিটেড <b>@muellfach.de</b> টেম্পোরারি ইমেইল তৈরি করতে পারবেন।\n\n"
        f"✨ <b>ফিচারসমূহ:</b>\n"
        f"• <b>✏️ Custom Email:</b> নিজের ইচ্ছামতো নাম দিয়ে ১০ অক্ষরের কাস্টম মেইল।\n"
        f"• <b>🔄 Refresh Inbox:</b> ইনবক্স চেক ও নতুন মেইল দেখা।\n"
        f"• <b>📁 Saved Mails:</b> বিগত ১০ দিনের তৈরি করা সব মেইল এক ক্লিকে সক্রিয় করা।\n"
        f"• <b>⚡ অটো-ফরওয়ার্ড:</b> নতুন ওটিপি বা ইমেইল আসলেই ২-৩ সেকেন্ডে আপনার চ্যাটে মেসেজ চলে আসবে!\n\n"
        f"শুরু করতে নিচের বাটনগুলো ব্যবহার করুন 👇"
    )
    await update.message.reply_text(
        welcome_text,
        reply_markup=MAIN_KEYBOARD,
        parse_mode=ParseMode.HTML,
    )

# ────────────────────────────────────────────────────────────────
# ✏️ Custom Email Conversation
# ────────────────────────────────────────────────────────────────

async def custom_email_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompts user to enter their custom username"""
    text = (
        "✍️ <b>কাস্টম ইমেইল ইউজারনেম দিন:</b>\n\n"
        "অনুগ্রহ করে নামটিতে <b>লেটার (বর্ণ) এবং নাম্বার (সংখ্যা)</b> মিলিয়ে কমপক্ষে <b>১০ ক্যারেক্টার</b> লিখুন (এর বেশিও লিখতে পারেন)।\n\n"
        "💡 <i>যেমন:</i> <code>fahimtest2026</code> অথবা <code>mithu9988xyz</code>\n\n"
        "❌ বাতিল করতে /cancel লিখুন।"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    return WAITING_CUSTOM_NAME

async def custom_email_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validates and creates the custom email"""
    raw_name = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Validation Rules:
    # 1. At least 10 characters
    # 2. Only letters, digits, underscores, dashes
    # 3. Must contain both letters and digits
    if len(raw_name) < 10:
        await update.message.reply_text(
            f"❌ <b>নামটি খুব ছোট!</b>\n\n"
            f"আপনি দিয়েছেন মাত্র {len(raw_name)} টি অক্ষর। কমপক্ষে <b>১০টি অক্ষর ও সংখ্যা</b> মিলিয়ে দিতে হবে।\n\n"
            f"দয়া করে আবার লিখুন (অথবা /cancel লিখুন):",
            parse_mode=ParseMode.HTML,
        )
        return WAITING_CUSTOM_NAME
        
    if not re.match(r"^[a-zA-Z0-9_\-]+$", raw_name):
        await update.message.reply_text(
            "❌ <b>নামে স্পেস বা স্পেশাল ক্যারেক্টার ব্যবহার করা যাবে না!</b>\n\n"
            "শুধুমাত্র ইংরেজি অক্ষর (a-z) এবং সংখ্যা (0-9) ব্যবহার করুন।\n"
            "দয়া করে আবার লিখুন:",
            parse_mode=ParseMode.HTML,
        )
        return WAITING_CUSTOM_NAME
        
    has_letter = bool(re.search(r"[a-zA-Z]", raw_name))
    has_digit = bool(re.search(r"[0-9]", raw_name))
    
    if not (has_letter and has_digit):
        await update.message.reply_text(
            "❌ <b>নামটিতে অক্ষর ও সংখ্যা মিলিয়ে দিতে হবে!</b>\n\n"
            "শুধুমাত্র সংখ্যা বা শুধু অক্ষর চলবে না। লেটার এবং নাম্বার দুটিই থাকতে হবে।\n"
            "💡 <i>যেমন:</i> <code>mithu2026test</code>\n\n"
            "দয়া করে আবার লিখুন:",
            parse_mode=ParseMode.HTML,
        )
        return WAITING_CUSTOM_NAME

    # Valid format check passed!
    username = raw_name.lower()

    # Check if username is already taken within 10 days
    if await is_username_taken(username):
        await update.message.reply_text(
            "⚠️ <b>এই ইউজারনেমটি ইতিমধ্যে নেওয়া হয়েছে!</b>\n\n"
            "বিগত ১০ দিনের মধ্যে এই ইউজারনেমটি দিয়ে অলরেডি মেইল তৈরি করা আছে। "
            "অন্য কারো মেসেজ আপনার কাছে আসার ঝুঁকি এড়াতে দয়া করে অন্য একটি ইউনিক নাম লিখুন:\n\n"
            "💡 <i>টিপস: নামের শেষে ভিন্ন কোনো সংখ্যা বা অক্ষর যুক্ত করতে পারেন।</i>",
            parse_mode=ParseMode.HTML,
        )
        return WAITING_CUSTOM_NAME

    email_address = f"{username}@{DOMAIN}"
    web_url = make_web_url(username)

    # Save to database (replaces active email and adds to 10-day history)
    await save_email(user_id, email_address, username)

    # Trigger initial sync for this mailbox
    async with httpx.AsyncClient() as client:
        await sync_mailbox(client)

    success_msg = (
        f"🎉 <b>আপনার কাস্টম ইমেইল প্রস্তুত!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 <b>Email:</b> <code>{email_address}</code>\n"
        f"🌐 <b>Web Link:</b> <a href=\"{web_url}\">Müllfach Web Postfach</a>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏳ <b>মেয়াদ:</b> এই মেইলটি আগামী <b>১০ দিন</b> আপনার <code>📁 Saved Mails</code> এ থাকবে।\n"
        f"⚡ <b>রিয়েল-টাইম অটো ইনবক্স:</b> সক্রিয় আছে! এই মেইলে কোনো মেসেজ বা ওটিপি আসলে সরাসরি এখানে চলে আসবে।"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 ওপেন করুন ওয়েবে", url=web_url)],
        [InlineKeyboardButton("🔄 ইনবক্স চেক করুন", callback_data="check_active_inbox", style="success")]
    ])

    await update.message.reply_text(
        success_msg,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    return ConversationHandler.END

async def cancel_custom_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels custom email creation"""
    await update.message.reply_text(
        "❌ কাস্টম ইমেইল তৈরি বাতিল করা হয়েছে।",
        reply_markup=MAIN_KEYBOARD,
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
            "নতুন একটি মেইল তৈরি করতে নিচের <b>✏️ Custom Email</b> বাটনে চাপ দিন।",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KEYBOARD,
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
            sender = m.get("sender_name") or m.get("sender_email") or "Unknown"
            subject = m.get("subject") or "(No Subject)"
            inbox_preview += f"{i}. <b>{subject}</b> — <i>{sender}</i>\n"
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
# 🔄 Refresh Inbox Handler
# ────────────────────────────────────────────────────────────────

async def refresh_inbox_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually refreshes inbox of current active email"""
    user_id = update.effective_user.id
    db_user = await get_or_create_user(user_id)
    active_email = db_user.get("active_email")

    if not active_email:
        await update.message.reply_text(
            "⚠️ <b>আপনার কোনো অ্যাক্টিভ মেইল নেই!</b>\n\n"
            "দয়া করে <b>✏️ Custom Email</b> বাটন দিয়ে একটি মেইল তৈরি করুন অথবা <b>📁 Saved Mails</b> থেকে একটি সিলেক্ট করুন।",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KEYBOARD,
        )
        return

    status_msg = await update.message.reply_text(
        f"🔄 <code>{active_email}</code> এর ইনবক্স চেক করা হচ্ছে...",
        parse_mode=ParseMode.HTML,
    )

    username = active_email.split("@")[0]
    web_url = make_web_url(username)

    async with httpx.AsyncClient() as client:
        await sync_mailbox(client)
        mails = await get_mailbox_messages(client, active_email)

    if not mails:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 আবার চেক করুন", callback_data="check_active_inbox", style="success")],
            [InlineKeyboardButton("🌐 ওয়েবে দেখুন", url=web_url)],
        ])
        await status_msg.edit_text(
            f"📭 <b>ইনবক্স খালি!</b>\n\n"
            f"📧 <b>মেইল:</b> <code>{active_email}</code>\n"
            f"🌐 <b>ওয়েব:</b> <a href=\"{web_url}\">Postfach Link</a>\n\n"
            f"এখনো কোনো ইমেইল আসেনি। কোনো ইমেইল বা কোড আসলে স্বয়ংক্রিয়ভাবে আপনাকে নোটিফিকেশন দেওয়া হবে।",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    # Mails found
    text = (
        f"📬 <b>ইনবক্স রিপোর্ট ({len(mails)} টি মেইল পাওয়া গেছে):</b>\n"
        f"📧 <b>Mailbox:</b> <code>{active_email}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    buttons = []
    for m in mails[:5]:
        mid = m["id"]
        sender = m.get("sender_name") or m.get("sender_email") or "Unknown"
        subject = m.get("subject") or "(No Subject)"
        date_str = m.get("received_at", "")
        item_code = extract_otp_code(subject)

        row = [InlineKeyboardButton(f"📖 {subject[:18]}", callback_data=f"read_mail:{mid}", style="primary")]
        if item_code:
            row.append(InlineKeyboardButton(f"📋 {item_code}", copy_text=CopyTextButton(text=item_code), style="success"))
        buttons.append(row)

        text += f"🔹 <b>{subject}</b>\n👤 প্রেরক: <code>{sender}</code>\n⏰ সময়: {date_str}\n"
        if item_code:
            text += f"🔑 <b>কোড:</b> <code>{item_code}</code>\n"
        text += "\n"

    buttons.append([InlineKeyboardButton("🔄 রিফ্রেশ", callback_data="check_active_inbox", style="success")])
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
            [InlineKeyboardButton("🔄 আবার রিফ্রেশ", callback_data="check_active_inbox", style="success")],
            [InlineKeyboardButton("🌐 ওয়েবে দেখুন", url=web_url)],
        ])
        await query.edit_message_text(
            f"📭 <b>ইনবক্স এখনো খালি!</b>\n\n"
            f"📧 <b>মেইল:</b> <code>{active_email}</code>\n"
            f"🌐 <b>ওয়েব:</b> <a href=\"{web_url}\">Postfach Link</a>\n\n"
            f"অপেক্ষা করুন, নতুন ইমেইল আসলে বট সাথে সাথে আপনাকে পাঠিয়ে দেবে।",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    text = (
        f"📬 <b>ইনবক্স রিপোর্ট ({len(mails)} টি মেইল পাওয়া গেছে):</b>\n"
        f"📧 <b>Mailbox:</b> <code>{active_email}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    buttons = []
    for m in mails[:5]:
        mid = m["id"]
        sender = m.get("sender_name") or m.get("sender_email") or "Unknown"
        subject = m.get("subject") or "(No Subject)"
        date_str = m.get("received_at", "")
        item_code = extract_otp_code(subject)

        row = [InlineKeyboardButton(f"📖 {subject[:18]}", callback_data=f"read_mail:{mid}", style="primary")]
        if item_code:
            row.append(InlineKeyboardButton(f"📋 {item_code}", copy_text=CopyTextButton(text=item_code), style="success"))
        buttons.append(row)

        text += f"🔹 <b>{subject}</b>\n👤 প্রেরক: <code>{sender}</code>\n⏰ সময়: {date_str}\n"
        if item_code:
            text += f"🔑 <b>কোড:</b> <code>{item_code}</code>\n"
        text += "\n"

    buttons.append([InlineKeyboardButton("🔄 রিফ্রেশ", callback_data="check_active_inbox", style="success")])
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

    if not active_email:
        await query.answer("কোনো অ্যাক্টিভ মেইল নেই!", show_alert=True)
        return

    async with httpx.AsyncClient() as client:
        mail_data = await get_single_mail(client, active_email, mail_id)

    if not mail_data:
        await query.answer("মেইলের বিস্তারিত লোড করা সম্ভব হয়নি।", show_alert=True)
        return

    subject = mail_data.get("subject") or "(No Subject)"
    sender = mail_data.get("sender_name") or mail_data.get("sender_email") or "Unknown"
    date_str = mail_data.get("received_at", "")
    
    raw_body = mail_data.get("body_text") or strip_html_tags(mail_data.get("body_html", ""))
    otp = extract_otp_code(f"{subject} {raw_body}")

    otp_section = ""
    if otp:
        otp_section = f"\n━━━━━━━━━━━━━━━━━━━━━\n🔑 <b>OTP / Code:</b> <code>{otp}</code>\n"

    # Truncate preview if too long
    body_preview = raw_body[:1000] if raw_body else "(No text content)"

    text = (
        f"✉️ <b>ইমেইল বিবরণ:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>Subject:</b> <b>{subject}</b>\n"
        f"👤 <b>From:</b> <code>{sender}</code>\n"
        f"⏰ <b>Date:</b> {date_str}"
        f"{otp_section}"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 <b>Message Content:</b>\n\n"
        f"{body_preview}\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
    )

    username = active_email.split("@")[0]
    web_url = make_web_url(username)

    row_buttons = [InlineKeyboardButton("⬅️ ইনবক্স", callback_data="check_active_inbox", style="primary")]
    if otp:
        row_buttons.append(
            InlineKeyboardButton(f"📋 {otp}", copy_text=CopyTextButton(text=otp), style="success")
        )

    keyboard = InlineKeyboardMarkup([
        row_buttons,
        [InlineKeyboardButton("🌐 ওয়েবে ফুল দেখুন", url=web_url)],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

# ────────────────────────────────────────────────────────────────
# 📋 Current Email Handler
# ────────────────────────────────────────────────────────────────

async def current_email_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the currently active email address"""
    user_id = update.effective_user.id
    db_user = await get_or_create_user(user_id)
    active_email = db_user.get("active_email")

    if not active_email:
        await update.message.reply_text(
            "📋 <b>বর্তমানে কোনো অ্যাক্টিভ ইমেইল নেই!</b>\n\n"
            "নতুন মেইল তৈরি করতে <b>✏️ Custom Email</b> বাটনে চাপুন।",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KEYBOARD,
        )
        return

    username = active_email.split("@")[0]
    web_url = make_web_url(username)
    auto_status = "চালু আছে ✅" if db_user.get("auto_forward") == 1 else "বন্ধ আছে ❌"

    text = (
        f"📋 <b>আপনার বর্তমান অ্যাক্টিভ ইমেইল:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 <code>{active_email}</code>\n"
        f"<i>(ট্যাপ করলেই কপি হয়ে যাবে)</i>\n\n"
        f"🌐 <b>Web Link:</b> <a href=\"{web_url}\">Müllfach Postfach</a>\n"
        f"⚡ <b>অটো-ফরওয়ার্ড:</b> {auto_status}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 নতুন মেইল নিতে চাইলে <b>✏️ Custom Email</b> বাটন চাপুন।"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 ইনবক্স চেক করুন", callback_data="check_active_inbox", style="success")],
        [InlineKeyboardButton("🌐 ওয়েবে দেখুন", url=web_url)],
    ])

    await update.message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
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
            reply_markup=MAIN_KEYBOARD,
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
        f"নতুন মেইল তৈরি করতে <b>✏️ Custom Email</b> বাটনে চাপ দিন।",
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
# 🚀 Real-time Background Polling Task (2-3 Seconds Polling)
# ────────────────────────────────────────────────────────────────

async def background_inbox_poller(app: Application):
    """Polls muellfach.de every 2-3 seconds for active users and forwards new mails"""
    logger.info("Real-time auto-inbox poller started.")
    
    async with httpx.AsyncClient() as client:
        while True:
            try:
                # 1. Fetch all users who have an active email and auto_forward enabled
                active_users = await get_active_users_to_poll()
                if not active_users:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                # 2. Trigger IMAP sync on muellfach server
                await sync_mailbox(client)

                # Group user_ids by active_email
                email_to_users = {}
                for uid, email in active_users:
                    if email not in email_to_users:
                        email_to_users[email] = []
                    email_to_users[email].append(uid)

                # 3. Check inbox for each unique active email
                for email, uids in email_to_users.items():
                    try:
                        mails = await get_mailbox_messages(client, email)
                        if not mails:
                            continue

                        for mail_item in mails:
                            mid = mail_item["id"]
                            already_seen = await is_message_seen(email, mid)
                            if already_seen:
                                continue

                            # Mark as seen immediately to avoid duplicate notifications
                            await mark_message_seen(email, mid)

                            # Fetch full mail details
                            full_mail = await get_single_mail(client, email, mid)
                            if not full_mail:
                                continue

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
                            otp = extract_otp_code(f"{subject} {body_raw}")

                            otp_section = ""
                            if otp:
                                otp_section = (
                                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"🔑 <b>OTP / Code:</b> <code>{otp}</code>\n"
                                )

                            body_snippet = (
                                body_raw[:600] if body_raw else "(No content)"
                            )

                            username = email.split("@")[0]
                            web_url = make_web_url(username)

                            msg_text = (
                                f"📬 <b>নতুন ইমেইল এসেছে!</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━━\n"
                                f"📧 <b>To:</b> <code>{email}</code>\n"
                                f"👤 <b>From:</b> <code>{sender}</code>\n"
                                f"📝 <b>Subject:</b> <b>{subject}</b>\n"
                                f"⏰ <b>Time:</b> {date_str}\n"
                                f"{otp_section}"
                                f"━━━━━━━━━━━━━━━━━━━━━\n"
                                f"📄 <b>Message Preview:</b>\n\n"
                                f"{body_snippet}\n"
                                f"━━━━━━━━━━━━━━━━━━━━━"
                            )

                            action_row = [
                                InlineKeyboardButton(
                                    "📖 বিস্তারিত",
                                    callback_data=f"read_mail:{mid}",
                                    style="primary",
                                )
                            ]
                            if otp:
                                action_row.append(
                                    InlineKeyboardButton(
                                        f"📋 {otp}",
                                        copy_text=CopyTextButton(text=otp),
                                        style="success",
                                    )
                                )

                            keyboard = InlineKeyboardMarkup([
                                action_row,
                                [InlineKeyboardButton("🌐 ওয়েবে দেখুন", url=web_url)],
                            ])

                            # Forward to each user listening to this email
                            for uid in uids:
                                try:
                                    await app.bot.send_message(
                                        chat_id=uid,
                                        text=msg_text,
                                        reply_markup=keyboard,
                                        parse_mode=ParseMode.HTML,
                                        disable_web_page_preview=True,
                                    )
                                except Exception as send_err:
                                    logger.error(f"Error sending mail to user {uid}: {send_err}")

                    except Exception as email_err:
                        logger.error(f"Error polling email {email}: {email_err}")

            except Exception as e:
                logger.error(f"Error in background_inbox_poller loop: {e}")

            await asyncio.sleep(POLL_INTERVAL)

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

# ────────────────────────────────────────────────────────────────
# Application Startup and Main
# ────────────────────────────────────────────────────────────────

async def post_init(application: Application):
    """Initializes database and spawns background polling worker"""
    await init_db()
    asyncio.create_task(background_inbox_poller(application))

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

    # 1. Custom Email Conversation Handler
    custom_email_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"Custom Email"), custom_email_prompt)
        ],
        states={
            WAITING_CUSTOM_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, custom_email_process)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_custom_email)],
    )
    app.add_handler(custom_email_conv)

    # 2. Main Menu Button Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.Regex(r"Refresh Inbox"), refresh_inbox_handler))
    app.add_handler(MessageHandler(filters.Regex(r"Saved Mails"), saved_mails_handler))
    app.add_handler(MessageHandler(filters.Regex(r"Current Email"), current_email_handler))
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
