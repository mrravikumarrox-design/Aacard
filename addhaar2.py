#!/usr/bin/env python3
"""
Aadhaar EID/UID Retrieval & PDF Download Telegram Bot
Professional Version with Premium Plans and Credit System
"""

import os
import sys
import logging
import json
import base64
import io
import time
import uuid
import requests
import pikepdf
import traceback
import sqlite3
import asyncio
import concurrent.futures
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple, List
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    JobQueue,
)
from telegram.request import HTTPXRequest
from telegram.constants import ParseMode

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# States
(NAME, MOBILE, EID_CAPTCHA, EID_OTP, DOWNLOAD_CAPTCHA, DOWNLOAD_OTP) = range(6)
(ADMIN_SET_LIMIT, ADMIN_BROADCAST) = range(10, 12)

BASE_URL = "https://tathya.uidai.gov.in"
EID_ENDPOINT = f"{BASE_URL}/retrieveEidUid/ext/v1/generic/retrieveuideid"
CAPTCHA_ENDPOINT = f"{BASE_URL}/audioCaptchaService/api/captcha/v3/generation"
DOWNLOAD_OTP_ENDPOINT = f"{BASE_URL}/unifiedAppAuthService/api/v2/generate/aadhaar/otp"
DOWNLOAD_PDF_ENDPOINT = f"{BASE_URL}/downloadAadhaarService/api/aadhaar/download"

# Admin IDs
ADMIN_IDS = [6399208358, 8342745290]
BOT_OWNER = "@Ulr2c"
CHANNEL_LINK = "https://t.me/AuraaMethods"
COMMUNITY_LINK = "https://t.me/auraacard"
CHANNEL_ID = -1003115871680

# Default settings
DEFAULT_FREE_LIMIT = 5
DEFAULT_FREE_WINDOW_ENABLED = True

# Premium Plans
PLANS = {
    "basic": {"aadhaars": 10, "price_inr": 130, "price_usdt": 2},
    "standard": {"aadhaars": 20, "price_inr": 250, "price_usdt": 3},
    "premium": {"aadhaars": 50, "price_inr": 550, "price_usdt": 6},
    "enterprise": {"aadhaars": 100, "price_inr": 1000, "price_usdt": 10},
    "ultimate": {"aadhaars": 200, "price_inr": 1800, "price_usdt": 18},
}

# Base headers
BASE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en_IN",
    "appid": "MYAADHAAR",
    "content-type": "application/json",
    "origin": "https://myaadhaar.uidai.gov.in",
    "referer": "https://myaadhaar.uidai.gov.in/",
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "connection": "keep-alive"
}

user_sessions: Dict[int, 'UserSession'] = {}

# Database setup
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            credits INTEGER DEFAULT 0,
            total_downloads INTEGER DEFAULT 0,
            last_download_date TEXT,
            daily_downloads INTEGER DEFAULT 0,
            is_premium INTEGER DEFAULT 0,
            joined_date TEXT,
            plan_type TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            plan_type TEXT,
            transaction_date TEXT,
            status TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    c.execute('''
        INSERT OR IGNORE INTO settings (key, value)
        VALUES 
            ('free_window_enabled', 'True'),
            ('free_limit', '5'),
            ('maintenance_mode', 'False')
    ''')
    
    conn.commit()
    conn.close()

def get_setting(key):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT value FROM settings WHERE key = ?', (key,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def update_setting(key, value):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('UPDATE settings SET value = ? WHERE key = ?', (value, key))
    conn.commit()
    conn.close()

def get_free_window_enabled():
    val = get_setting('free_window_enabled')
    return val.lower() == 'true' if val else DEFAULT_FREE_WINDOW_ENABLED

def get_free_limit():
    val = get_setting('free_limit')
    return int(val) if val else DEFAULT_FREE_LIMIT

def get_maintenance_mode():
    val = get_setting('maintenance_mode')
    return val.lower() == 'true' if val else False

def get_user(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result

def create_user(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        INSERT OR IGNORE INTO users (user_id, credits, joined_date)
        VALUES (?, 0, ?)
    ''', (user_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def update_credits(user_id, amount):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('UPDATE users SET credits = credits + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

def get_credits(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT credits FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 0

def increment_download(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    today = datetime.now().date().isoformat()
    c.execute('SELECT daily_downloads, last_download_date FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    
    if result:
        daily, last_date = result
        if last_date != today:
            daily = 0
        daily += 1
        c.execute('''
            UPDATE users 
            SET total_downloads = total_downloads + 1,
                daily_downloads = ?,
                last_download_date = ?
            WHERE user_id = ?
        ''', (daily, today, user_id))
    conn.commit()
    conn.close()
    return daily

def can_download(user_id):
    if get_maintenance_mode():
        return False, "Bot is under maintenance. Please try again later."
    
    user = get_user(user_id)
    if not user:
        create_user(user_id)
        if get_free_window_enabled():
            return True, "Free window enabled"
        return False, "Free window disabled. Purchase credits to continue."
    
    credits = user[1]
    if credits > 0:
        return True, f"Premium credits available: {credits}"
    
    if not get_free_window_enabled():
        return False, "Free window disabled. Purchase credits to continue."
    
    free_limit = get_free_limit()
    today = datetime.now().date().isoformat()
    daily = user[4] if user[4] else 0
    last_date = user[3] if user[3] else ""
    
    if last_date != today:
        return True, "New day, free downloads reset"
    
    if daily < free_limit:
        return True, f"Free downloads remaining: {free_limit - daily}"
    
    return False, f"Daily free limit reached ({free_limit}/{free_limit}). Purchase credits to continue."

class UserSession:
    def __init__(self):
        self.name = None
        self.mobile = None
        self.eid_number = None
        self.eid_captcha_txn_id = None
        self.eid_otp_txn_id = None
        self.eid_captcha_text = None
        self.download_captcha_txn_id = None
        self.download_otp_txn_id = None
        self.download_captcha_text = None
        self.request_id = None
        self.session = requests.Session()
        self.created_at = time.time()
        self.otp_attempts = 0
        self.max_otp_attempts = 3
        self.full_name = None


def generate_captcha(session: requests.Session) -> Tuple[Optional[bytes], Optional[str], Optional[Dict]]:
    """Generate captcha"""
    try:
        payload = {"captchaLength": "6", "captchaType": "2", "audioCaptchaRequired": True}
        response = session.post(CAPTCHA_ENDPOINT, headers=BASE_HEADERS, json=payload, timeout=15)
        result = response.json()
        
        if result.get("imageBase64") and result.get("transactionId"):
            image_bytes = base64.b64decode(result["imageBase64"])
            return image_bytes, result["transactionId"], None
        return None, None, {"error": "No image in response"}
    except Exception as e:
        return None, None, {"error": str(e)}


def api_call(session, url, payload, label, custom_headers=None):
    """Make API call"""
    try:
        headers = custom_headers if custom_headers else BASE_HEADERS
        logger.info(f"{label}: {json.dumps(payload)}")
        response = session.post(url, headers=headers, json=payload, timeout=15)
        return response.json(), None
    except Exception as e:
        return None, {"error": str(e)}


def crack_pdf(pdf_bytes: bytes, name: str) -> Tuple[Optional[bytes], Optional[str]]:
    """Attempt to crack PDF password with timeout"""
    if name:
        name_clean = ' '.join(name.split()).upper()
        name_prefix = name_clean[:4] if len(name_clean) >= 4 else name_clean.ljust(4, 'X')
    else:
        name_prefix = "MR"
    
    try:
        # Check if already unlocked (quick check)
        try:
            pdf = pikepdf.open(io.BytesIO(pdf_bytes))
            output = io.BytesIO()
            pdf.save(output)
            pdf.close()
            return output.getvalue(), None
        except pikepdf.PasswordError:
            pass
        except Exception:
            pass
        
        # Try years 1950-2015 with timeout protection
        for year in range(1950, 2016):
            password = f"{name_prefix}{year}"
            try:
                pdf = pikepdf.open(io.BytesIO(pdf_bytes), password=password, allow_overwriting_input=True)
                output = io.BytesIO()
                pdf.save(output)
                pdf.close()
                return output.getvalue(), password
            except pikepdf.PasswordError:
                continue
            except Exception:
                continue
        
        return None, None
    except Exception:
        return None, None


# ==================== MAIN MENU ====================

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    """Show main menu with buttons"""
    user_id = update.effective_user.id
    
    keyboard = [
        [InlineKeyboardButton("📥 Download Aadhaar", callback_data="start_download")],
        [InlineKeyboardButton("💰 View Plans", callback_data="view_plans")],
        [InlineKeyboardButton("👤 My Credits", callback_data="check_credits")],
        [InlineKeyboardButton("❓ Help", callback_data="help_menu")]
    ]
    
    if user_id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "🏠 Main Menu\n\n"
        "Welcome to Aadhaar Download Bot!\n"
        "Select an option below:\n\n"
        f"🤖 Bot by {BOT_OWNER}"
    )
    
    try:
        if edit and hasattr(update, 'callback_query'):
            await update.callback_query.edit_message_text(
                text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error showing menu: {e}")
        # Fallback without parse mode
        if edit and hasattr(update, 'callback_query'):
            await update.callback_query.edit_message_text(
                text, reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                text, reply_markup=reply_markup
            )


# ==================== START COMMAND ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    
    # Check maintenance mode
    if get_maintenance_mode():
        await update.message.reply_text(
            "⛔ Bot Under Maintenance\n\n"
            "The bot is currently being updated.\n"
            "Please try again later.\n\n"
            f"Contact: {BOT_OWNER}"
        )
        return ConversationHandler.END
    
    # Check membership
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        if member.status not in ['member', 'administrator', 'creator']:
            keyboard = [
                [InlineKeyboardButton("📢 Join Main Channel", url=CHANNEL_LINK)],
                [InlineKeyboardButton("💬 Join Community", url=COMMUNITY_LINK)],
                [InlineKeyboardButton("🔄 Check Again", callback_data="check_membership")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "❌ Access Denied\n\n"
                "You must join our channels to use this bot:\n\n"
                "1️⃣ Join Main Channel\n"
                "2️⃣ Join Community\n\n"
                "After joining, click 'Check Again'",
                reply_markup=reply_markup
            )
            return ConversationHandler.END
    except:
        keyboard = [
            [InlineKeyboardButton("📢 Join Main Channel", url=CHANNEL_LINK)],
            [InlineKeyboardButton("💬 Join Community", url=COMMUNITY_LINK)],
            [InlineKeyboardButton("🔄 Check Again", callback_data="check_membership")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "❌ Access Denied\n\n"
            "You must join our channels to use this bot:\n\n"
            "1️⃣ Join Main Channel\n"
            "2️⃣ Join Community\n\n"
            "After joining, click 'Check Again'",
            reply_markup=reply_markup
        )
        return ConversationHandler.END
    
    # Check if user can download
    can_dl, msg = can_download(user_id)
    if not can_dl:
        credits = get_credits(user_id)
        keyboard = [
            [InlineKeyboardButton("💰 View Premium Plans", callback_data="view_plans")],
            [InlineKeyboardButton("👤 Check Credits", callback_data="check_credits")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"❌ Access Restricted\n\n"
            f"{msg}\n"
            f"Your Credits: {credits}\n\n"
            f"Purchase premium plans to continue!\n"
            f"Contact: {BOT_OWNER}",
            reply_markup=reply_markup
        )
        return ConversationHandler.END
    
    # Clean up any existing session
    if user_id in user_sessions:
        del user_sessions[user_id]
    user_sessions[user_id] = UserSession()
    create_user(user_id)
    
    # Show main menu
    await show_main_menu(update, context)
    return ConversationHandler.END


# ==================== DOWNLOAD FLOW ====================

async def start_download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle start download from menu"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Check if user can download
    can_dl, msg = can_download(user_id)
    if not can_dl:
        credits = get_credits(user_id)
        keyboard = [
            [InlineKeyboardButton("💰 View Premium Plans", callback_data="view_plans")],
            [InlineKeyboardButton("👤 Check Credits", callback_data="check_credits")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"❌ Access Restricted\n\n"
            f"{msg}\n"
            f"Your Credits: {credits}\n\n"
            f"Purchase premium plans to continue!\n"
            f"Contact: {BOT_OWNER}",
            reply_markup=reply_markup
        )
        return
    
    # Clean up any existing session
    if user_id in user_sessions:
        del user_sessions[user_id]
    user_sessions[user_id] = UserSession()
    create_user(user_id)
    
    await query.edit_message_text(
        "🔐 Aadhaar Download Bot\n\n"
        "📝 Enter your Full Name as in Aadhaar\n"
        "_Type Mr to skip_"
    )
    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    us = user_sessions.get(user_id)
    if not us:
        await update.message.reply_text("⏰ Session expired. /start again.")
        return ConversationHandler.END
    
    name = update.message.text.strip()
    us.name = name if name else "Mr"
    
    await update.message.reply_text(
        f"✅ Name: {us.name}\n\n"
        f"📱 Enter your 10-digit Mobile Number"
    )
    return MOBILE


async def get_mobile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    us = user_sessions.get(user_id)
    if not us:
        await update.message.reply_text("⏰ Session expired. /start again.")
        return ConversationHandler.END
    
    mobile = update.message.text.strip()
    if not mobile.isdigit() or len(mobile) != 10:
        await update.message.reply_text("❌ Enter exactly 10 digits.")
        return MOBILE
    
    us.mobile = mobile
    
    progress = await update.message.reply_text("🔄 Generating captcha...")
    
    image_bytes, txn_id, debug = generate_captcha(us.session)
    
    if not image_bytes:
        await progress.delete()
        await update.message.reply_text("❌ Failed to generate captcha. /start to retry")
        return ConversationHandler.END
    
    us.eid_captcha_txn_id = txn_id
    
    img_byte_arr = io.BytesIO(image_bytes)
    await progress.delete()
    
    await update.message.reply_photo(
        photo=img_byte_arr,
        caption="📸 Enter the captcha text"
    )
    
    return EID_CAPTCHA


async def get_eid_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    us = user_sessions.get(user_id)
    if not us:
        await update.message.reply_text("⏰ Session expired. /start again.")
        return ConversationHandler.END
    
    captcha_text = update.message.text.strip()
    us.eid_captcha_text = captcha_text
    
    progress = await update.message.reply_text("📤 Requesting OTP...")
    
    otp_payload = {
        "mobileNumber": us.mobile,
        "dob": None,
        "email": None,
        "name": us.name,
        "option": "EID",
        "otp": None,
        "otpTxnId": None,
        "captchaTxnId": us.eid_captcha_txn_id,
        "captcha": captcha_text,
        "resendOtp": False
    }
    
    otp_result, _ = api_call(us.session, EID_ENDPOINT, otp_payload, "EID OTP REQUEST")
    
    await progress.delete()
    
    if not otp_result or otp_result.get("status") != "Success" or not otp_result.get("responseData", {}).get("otpSent"):
        error_msg = otp_result.get("responseData", {}).get("message", "Failed to send OTP") if otp_result else "Unknown error"
        await update.message.reply_text(f"❌ OTP Failed: {error_msg}\n/start to retry")
        return ConversationHandler.END
    
    us.eid_otp_txn_id = otp_result["responseData"]["otpTxnId"]
    masked = f"{us.mobile[:2]}****{us.mobile[-4:]}"
    
    await update.message.reply_text(
        f"✅ OTP Sent to {masked}\n\n"
        f"📝 Enter the 6-digit OTP"
    )
    return EID_OTP


async def get_eid_otp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    us = user_sessions.get(user_id)
    if not us:
        await update.message.reply_text("⏰ Session expired. /start again.")
        return ConversationHandler.END
    
    otp = update.message.text.strip()
    if not otp.isdigit() or len(otp) != 6:
        await update.message.reply_text("❌ OTP must be exactly 6 digits.")
        return EID_OTP
    
    us.otp_attempts += 1
    if us.otp_attempts > us.max_otp_attempts:
        await update.message.reply_text(f"❌ Too many attempts. /start to retry.")
        return ConversationHandler.END
    
    progress = await update.message.reply_text("🔍 Verifying OTP...")
    
    verify_payload = {
        "mobileNumber": us.mobile,
        "dob": None,
        "email": None,
        "name": us.name,
        "option": "EID",
        "otp": otp,
        "otpTxnId": us.eid_otp_txn_id,
        "captchaTxnId": us.eid_captcha_txn_id,
        "captcha": us.eid_captcha_text,
        "resendOtp": False
    }
    
    verify_result, _ = api_call(us.session, EID_ENDPOINT, verify_payload, "EID OTP VERIFY")
    
    await progress.delete()
    
    if not verify_result or verify_result.get("status") != "Success":
        error_msg = verify_result.get("responseData", {}).get("message", "Invalid OTP") if verify_result else "Unknown error"
        remaining = us.max_otp_attempts - us.otp_attempts
        await update.message.reply_text(
            f"❌ OTP Failed: {error_msg}\n"
            f"Attempts left: {remaining}"
        )
        if remaining > 0:
            return EID_OTP
        return ConversationHandler.END
    
    response_data = verify_result.get("responseData", {})
    if not response_data.get("eidNumber"):
        await update.message.reply_text("❌ EID not found. /start to retry")
        return ConversationHandler.END
    
    us.eid_number = response_data["eidNumber"]
    us.full_name = response_data.get("name", us.name)
    us.request_id = str(uuid.uuid4())
    
    await update.message.reply_text(
        f"✅ EID Retrieved!\n"
        f"👤 {us.full_name}\n"
        f"🆔 {us.eid_number}\n\n"
        f"🔄 Generating download captcha..."
    )
    
    dl_image_bytes, dl_txn_id, _ = generate_captcha(us.session)
    
    if not dl_image_bytes:
        await update.message.reply_text("❌ Failed to generate captcha. /start to retry")
        return ConversationHandler.END
    
    us.download_captcha_txn_id = dl_txn_id
    
    dl_img_arr = io.BytesIO(dl_image_bytes)
    await update.message.reply_photo(
        photo=dl_img_arr,
        caption="📸 Enter the download captcha"
    )
    
    return DOWNLOAD_CAPTCHA


async def get_download_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    us = user_sessions.get(user_id)
    if not us:
        await update.message.reply_text("⏰ Session expired. /start again.")
        return ConversationHandler.END
    
    captcha_text = update.message.text.strip()
    us.download_captcha_text = captcha_text
    
    progress = await update.message.reply_text("📤 Requesting download OTP...")
    
    dl_otp_payload = {
        "eidNumber": us.eid_number,
        "idType": "eid",
        "captchaTxnId": us.download_captcha_txn_id,
        "captchaValue": captcha_text,
        "transactionId": us.request_id,
        "resendOTP": False
    }
    
    dl_otp_result, _ = api_call(us.session, DOWNLOAD_OTP_ENDPOINT, dl_otp_payload, "DOWNLOAD OTP REQUEST")
    
    await progress.delete()
    
    if not dl_otp_result or dl_otp_result.get("status") != "Success":
        error_msg = dl_otp_result.get("message", "Failed") if dl_otp_result else "Unknown error"
        await update.message.reply_text(f"❌ Failed: {error_msg}\n/start to retry")
        return ConversationHandler.END
    
    txn_id = dl_otp_result.get("txnId")
    us.download_otp_txn_id = txn_id if txn_id else dl_otp_result.get("responseData", {}).get("otpTxnId")
    us.otp_attempts = 0
    
    await update.message.reply_text(
        f"✅ Download OTP Sent!\n\n"
        f"📝 Enter the Download OTP"
    )
    return DOWNLOAD_OTP


async def get_download_otp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    us = user_sessions.get(user_id)
    if not us:
        await update.message.reply_text("⏰ Session expired. /start again.")
        return ConversationHandler.END
    
    otp = update.message.text.strip()
    if not otp.isdigit() or len(otp) != 6:
        await update.message.reply_text("❌ OTP must be exactly 6 digits.")
        return DOWNLOAD_OTP
    
    progress = await update.message.reply_text("📥 Downloading PDF...")
    
    pdf_payload = {
        "eid": us.eid_number,
        "mask": False,
        "otp": otp,
        "otpTxnId": us.download_otp_txn_id
    }
    
    transaction_id = str(uuid.uuid4())
    pdf_headers = BASE_HEADERS.copy()
    pdf_headers["transactionid"] = transaction_id
    pdf_headers["x-request-id"] = transaction_id
    
    try:
        response = us.session.post(DOWNLOAD_PDF_ENDPOINT, headers=pdf_headers, json=pdf_payload, timeout=30)
        await progress.delete()
        
        response_json = response.json()
        
        if response_json.get("status") == "Success" and response_json.get("statusCode") == 200:
            pdf_data = response_json.get("data", {}).get("aadhaarPdf") or response_json.get("aadhaarPdf")
            
            if pdf_data:
                pdf_bytes = base64.b64decode(pdf_data)
                
                if pdf_bytes[:4] == b'%PDF':
                    # Try to crack PDF with timeout
                    await update.message.reply_text("🔓 Attempting to crack PDF...")
                    
                    # Run cracking in a separate thread to avoid blocking
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(crack_pdf, pdf_bytes, us.full_name)
                        try:
                            unlocked_pdf, password = future.result(timeout=30)
                        except concurrent.futures.TimeoutError:
                            await update.message.reply_text("⏰ PDF cracking timed out. Sending locked PDF.")
                            unlocked_pdf = None
                            password = None
                    
                    # Deduct credits or use free download
                    credits = get_credits(user_id)
                    if credits > 0:
                        update_credits(user_id, -1)
                        credit_msg = f"Credits Remaining: {credits - 1}"
                    else:
                        increment_download(user_id)
                        free_limit = get_free_limit()
                        daily = get_user(user_id)[4] if get_user(user_id) else 0
                        credit_msg = f"Free Downloads: {daily}/{free_limit}"
                    
                    if unlocked_pdf:
                        pdf_to_send = unlocked_pdf
                        caption_text = f"🔓 Unlocked\n🔑 Password: {password}" if password else ""
                    else:
                        pdf_to_send = pdf_bytes
                        caption_text = f"🔒 Password Protected\nHint: {us.full_name[:4].upper() if us.full_name else 'MR'}YYYY"
                    
                    pdf_file = io.BytesIO(pdf_to_send)
                    pdf_file.name = f"aadhaar_{us.eid_number[:8]}.pdf"
                    
                    await update.message.reply_document(
                        document=pdf_file,
                        caption=f"📄 Aadhaar PDF\n"
                               f"👤 {us.full_name}\n"
                               f"🆔 {us.eid_number}\n"
                               f"{caption_text}\n"
                               f"{credit_msg}\n\n"
                               f"🤖 Bot by {BOT_OWNER}"
                    )
                    
                    await update.message.reply_text(
                        f"✅ Download Complete!\n\n"
                        f"{credit_msg}\n\n"
                        f"🤖 Bot by {BOT_OWNER}"
                    )
                    
                    # Show main menu after completion
                    await show_main_menu(update, context)
                    
                else:
                    await update.message.reply_text("❌ Invalid PDF data received")
            else:
                await update.message.reply_text("❌ No PDF data in response")
        else:
            error_msg = response_json.get("statusMessage", "Unknown error")
            await update.message.reply_text(f"❌ Download Failed: {error_msg}")
            
    except Exception as e:
        await progress.delete()
        await update.message.reply_text(f"❌ Error: {str(e)}")
    
    if user_id in user_sessions:
        del user_sessions[user_id]
    
    return ConversationHandler.END


# ==================== CANCEL ====================

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
    await update.message.reply_text("❌ Cancelled. /start to begin again.")
    return ConversationHandler.END


# ==================== CALLBACKS ====================

async def check_membership_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        if member.status in ['member', 'administrator', 'creator']:
            await query.edit_message_text(
                "✅ Membership Verified!\n\n"
                "You are a member of our channels.\n"
                "Type /start to begin."
            )
        else:
            keyboard = [
                [InlineKeyboardButton("📢 Join Main Channel", url=CHANNEL_LINK)],
                [InlineKeyboardButton("💬 Join Community", url=COMMUNITY_LINK)],
                [InlineKeyboardButton("🔄 Check Again", callback_data="check_membership")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "❌ Not a Member Yet\n\n"
                "Please join our channels first:\n\n"
                "1️⃣ Join Main Channel\n"
                "2️⃣ Join Community\n\n"
                "After joining, click 'Check Again'",
                reply_markup=reply_markup
            )
    except:
        keyboard = [
            [InlineKeyboardButton("📢 Join Main Channel", url=CHANNEL_LINK)],
            [InlineKeyboardButton("💬 Join Community", url=COMMUNITY_LINK)],
            [InlineKeyboardButton("🔄 Check Again", callback_data="check_membership")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "❌ Error Checking Membership\n\n"
            "Please join our channels first:\n\n"
            "1️⃣ Join Main Channel\n"
            "2️⃣ Join Community\n\n"
            "After joining, click 'Check Again'",
            reply_markup=reply_markup
        )


async def view_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    plans_text = "💰 Premium Plans\n\n"
    for key, plan in PLANS.items():
        plans_text += f"{key.title()} Plan\n"
        plans_text += f"📊 {plan['aadhaars']} Aadhaars\n"
        plans_text += f"💵 ₹{plan['price_inr']} / {plan['price_usdt']} USDT\n\n"
    
    plans_text += f"\n📩 Contact {BOT_OWNER} to purchase\n"
    plans_text += f"💳 Check /credits to see your balance"
    
    keyboard = [
        [InlineKeyboardButton("👤 Check Credits", callback_data="check_credits")],
        [InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        plans_text,
        reply_markup=reply_markup
    )


async def check_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        is_callback = True
    else:
        user_id = update.effective_user.id
        is_callback = False
    
    credits = get_credits(user_id)
    user = get_user(user_id)
    daily = user[4] if user else 0
    last_date = user[3] if user else ""
    today = datetime.now().date().isoformat()
    
    if last_date != today:
        daily = 0
    
    free_limit = get_free_limit()
    remaining = free_limit - daily
    free_enabled = get_free_window_enabled()
    
    text = f"👤 Your Credits\n\n"
    text += f"💳 Premium Credits: {credits}\n"
    text += f"📊 Free Downloads Today: {daily}/{free_limit}\n"
    text += f"⏳ Remaining Today: {max(0, remaining)}\n"
    text += f"🔓 Free Window: {'✅ Enabled' if free_enabled else '❌ Disabled'}\n\n"
    text += f"💰 Buy more credits: /plans"
    
    keyboard = [
        [InlineKeyboardButton("💰 View Plans", callback_data="view_plans")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if is_callback:
        await query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_main_menu(update, context, edit=True)


async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = "❓ Help & Support\n\n"
    text += "How to use:\n"
    text += "1️⃣ /start to begin\n"
    text += "2️⃣ Enter name, mobile, captcha\n"
    text += "3️⃣ Enter OTPs when received\n"
    text += "4️⃣ Get your Aadhaar PDF\n\n"
    text += "Plans:\n"
    for key, plan in PLANS.items():
        text += f"• {key.title()}: {plan['aadhaars']} Aadhaars - ₹{plan['price_inr']}\n"
    text += f"\n📩 Contact: {BOT_OWNER}\n"
    text += f"📢 Channel: {CHANNEL_LINK}"
    
    keyboard = [
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup)


# ==================== ADMIN PANEL ====================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("❌ Unauthorized access!")
        return
    
    free_enabled = get_free_window_enabled()
    free_limit = get_free_limit()
    maintenance = get_maintenance_mode()
    
    text = "⚙️ Admin Panel\n\n"
    text += f"📊 Free Window: {'✅ Enabled' if free_enabled else '❌ Disabled'}\n"
    text += f"📊 Free Limit: {free_limit} per day\n"
    text += f"🔧 Maintenance: {'⛔ Enabled' if maintenance else '✅ Disabled'}\n\n"
    text += "Select an option below:"
    
    keyboard = [
        [InlineKeyboardButton(f"{'🔴' if free_enabled else '🟢'} Toggle Free Window", callback_data="admin_toggle_free")],
        [InlineKeyboardButton("📊 Set Free Limit", callback_data="admin_set_limit")],
        [InlineKeyboardButton(f"{'🟢' if maintenance else '🔴'} Toggle Maintenance", callback_data="admin_toggle_maintenance")],
        [InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📊 Bot Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 List Users", callback_data="admin_list_users")],
        [InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup)


async def admin_toggle_free(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("❌ Unauthorized!")
        return
    
    current = get_free_window_enabled()
    new_value = not current
    update_setting('free_window_enabled', str(new_value))
    
    await query.edit_message_text(
        f"✅ Free window has been {'Enabled' if new_value else 'Disabled'}!\n\n"
        f"Free Limit: {get_free_limit()} per day\n\n"
        f"Click /admin to return to admin panel"
    )


async def admin_set_limit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("❌ Unauthorized!")
        return
    
    await query.edit_message_text(
        "📊 Set Free Limit\n\n"
        "Enter the new daily free limit (number of Aadhaars per day):\n"
        "Example: 10\n\n"
        "_Send /cancel to cancel_"
    )
    return ADMIN_SET_LIMIT


async def admin_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized!")
        return ConversationHandler.END
    
    try:
        new_limit = int(update.message.text.strip())
        if new_limit < 1:
            await update.message.reply_text("❌ Limit must be at least 1")
            return ADMIN_SET_LIMIT
        
        update_setting('free_limit', str(new_limit))
        await update.message.reply_text(
            f"✅ Free limit updated to {new_limit} per day!\n\n"
            f"Type /admin to return to admin panel"
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number")
        return ADMIN_SET_LIMIT


async def admin_toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("❌ Unauthorized!")
        return
    
    current = get_maintenance_mode()
    new_value = not current
    update_setting('maintenance_mode', str(new_value))
    
    await query.edit_message_text(
        f"✅ Maintenance mode has been {'Enabled' if new_value else 'Disabled'}!\n\n"
        f"Users will {'not be able' if new_value else 'be able'} to use the bot.\n\n"
        f"Click /admin to return to admin panel"
    )


async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("❌ Unauthorized!")
        return
    
    await query.edit_message_text(
        "📢 Broadcast Message\n\n"
        "Send the message you want to broadcast to all users:\n"
        "Example: New update available!\n\n"
        "_Send /cancel to cancel_"
    )
    return ADMIN_BROADCAST


async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized!")
        return ConversationHandler.END
    
    message = update.message.text.strip()
    
    status_msg = await update.message.reply_text("📤 Broadcasting message to all users...")
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM users')
    users = c.fetchall()
    conn.close()
    
    success = 0
    failed = 0
    
    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user[0],
                text=f"📢 Announcement\n\n{message}\n\n"
                     f"🤖 Bot by {BOT_OWNER}"
            )
            success += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    
    await status_msg.edit_text(
        f"✅ Broadcast Complete\n\n"
        f"✓ Sent to: {success} users\n"
        f"✗ Failed: {failed} users\n\n"
        f"Type /admin to return to admin panel"
    )
    return ConversationHandler.END


async def admin_stats_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("❌ Unauthorized!")
        return
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]
    c.execute('SELECT SUM(credits) FROM users')
    total_credits = c.fetchone()[0] or 0
    c.execute('SELECT SUM(total_downloads) FROM users')
    total_downloads = c.fetchone()[0] or 0
    c.execute('SELECT COUNT(*) FROM users WHERE credits > 0')
    premium_users = c.fetchone()[0]
    conn.close()
    
    text = f"📊 Bot Statistics\n\n"
    text += f"👥 Total Users: {total_users}\n"
    text += f"⭐ Premium Users: {premium_users}\n"
    text += f"💳 Total Credits: {total_credits}\n"
    text += f"📊 Total Downloads: {total_downloads}\n"
    text += f"🔓 Free Window: {'✅ Enabled' if get_free_window_enabled() else '❌ Disabled'}\n"
    text += f"📊 Free Limit: {get_free_limit()}/day\n"
    text += f"🔧 Maintenance: {'⛔ Enabled' if get_maintenance_mode() else '✅ Disabled'}\n"
    text += f"\n⏰ Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_stats")],
        [InlineKeyboardButton("🏠 Admin Panel", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup)


async def admin_list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("❌ Unauthorized!")
        return
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT user_id, credits, total_downloads FROM users ORDER BY total_downloads DESC LIMIT 50')
    users = c.fetchall()
    conn.close()
    
    if not users:
        await query.edit_message_text("No users found.")
        return
    
    text = "👥 Top Users\n\n"
    for idx, user in enumerate(users, 1):
        text += f"{idx}. User: {user[0]}\n"
        text += f"   💳 Credits: {user[1]}, 📊 Downloads: {user[2]}\n\n"
    
    keyboard = [
        [InlineKeyboardButton("🏠 Admin Panel", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text[:4000], reply_markup=reply_markup)


# ==================== ADMIN COMMANDS (TEXT) ====================

async def admin_add_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                "Usage: /add <amount> <user_id>\n"
                "Example: /add 10 123456789"
            )
            return
        
        amount = int(args[0])
        target_user = int(args[1])
        
        update_credits(target_user, amount)
        
        await update.message.reply_text(
            f"✅ Added {amount} credits to user {target_user}\n"
            f"New balance: {get_credits(target_user)}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def admin_deduct_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                "Usage: /deduct <amount> <user_id>\n"
                "Example: /deduct 5 123456789"
            )
            return
        
        amount = int(args[0])
        target_user = int(args[1])
        
        current = get_credits(target_user)
        if current < amount:
            await update.message.reply_text(f"❌ User only has {current} credits")
            return
        
        update_credits(target_user, -amount)
        
        await update.message.reply_text(
            f"✅ Deducted {amount} credits from user {target_user}\n"
            f"New balance: {get_credits(target_user)}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def admin_check_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        args = context.args
        if len(args) < 1:
            await update.message.reply_text(
                "Usage: /check <user_id>\n"
                "Example: /check 123456789"
            )
            return
        
        target_user = int(args[0])
        user = get_user(target_user)
        
        if not user:
            await update.message.reply_text("User not found")
            return
        
        free_limit = get_free_limit()
        text = f"👤 User Report\n\n"
        text += f"User ID: {target_user}\n"
        text += f"Credits: {user[1]}\n"
        text += f"Total Downloads: {user[2]}\n"
        text += f"Daily Downloads: {user[4] if user[4] else 0}/{free_limit}\n"
        text += f"Joined: {user[5] if user[5] else 'N/A'}\n"
        text += f"Plan: {user[6] if user[6] else 'Free'}"
        
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    free_enabled = get_free_window_enabled()
    free_limit = get_free_limit()
    maintenance = get_maintenance_mode()
    
    text = "⚙️ Admin Panel\n\n"
    text += f"📊 Free Window: {'✅ Enabled' if free_enabled else '❌ Disabled'}\n"
    text += f"📊 Free Limit: {free_limit} per day\n"
    text += f"🔧 Maintenance: {'⛔ Enabled' if maintenance else '✅ Disabled'}\n\n"
    text += "Commands:\n"
    text += "/admin - Show this panel\n"
    text += "/toggle_free - Toggle free window\n"
    text += "/set_limit <number> - Set daily free limit\n"
    text += "/toggle_maintenance - Toggle maintenance mode\n"
    text += "/broadcast <message> - Broadcast message to all users\n"
    text += "/add <amount> <user_id> - Add credits\n"
    text += "/deduct <amount> <user_id> - Deduct credits\n"
    text += "/check <user_id> - Check user details\n"
    text += "/stats - View bot statistics"
    
    keyboard = [
        [InlineKeyboardButton("📊 Admin Panel", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, reply_markup=reply_markup)


async def admin_toggle_free_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    current = get_free_window_enabled()
    new_value = not current
    update_setting('free_window_enabled', str(new_value))
    
    await update.message.reply_text(
        f"✅ Free window has been {'Enabled' if new_value else 'Disabled'}!\n\n"
        f"Free Limit: {get_free_limit()} per day"
    )


async def admin_set_limit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    try:
        args = context.args
        if len(args) < 1:
            await update.message.reply_text(
                "Usage: /set_limit <number>\n"
                "Example: /set_limit 10"
            )
            return
        
        new_limit = int(args[0])
        if new_limit < 1:
            await update.message.reply_text("❌ Limit must be at least 1")
            return
        
        update_setting('free_limit', str(new_limit))
        await update.message.reply_text(f"✅ Free limit updated to {new_limit} per day!")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def admin_toggle_maintenance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    current = get_maintenance_mode()
    new_value = not current
    update_setting('maintenance_mode', str(new_value))
    
    await update.message.reply_text(
        f"✅ Maintenance mode has been {'Enabled' if new_value else 'Disabled'}!\n\n"
        f"Users will {'not be able' if new_value else 'be able'} to use the bot."
    )


async def admin_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    args = context.args
    if len(args) < 1:
        await update.message.reply_text(
            "Usage: /broadcast <message>\n"
            "Example: /broadcast New update available!"
        )
        return
    
    message = ' '.join(args)
    
    status_msg = await update.message.reply_text("📤 Broadcasting message to all users...")
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM users')
    users = c.fetchall()
    conn.close()
    
    success = 0
    failed = 0
    
    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user[0],
                text=f"📢 Announcement\n\n{message}\n\n"
                     f"🤖 Bot by {BOT_OWNER}"
            )
            success += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    
    await status_msg.edit_text(
        f"✅ Broadcast Complete\n\n"
        f"✓ Sent to: {success} users\n"
        f"✗ Failed: {failed} users"
    )


async def admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]
    c.execute('SELECT SUM(credits) FROM users')
    total_credits = c.fetchone()[0] or 0
    c.execute('SELECT SUM(total_downloads) FROM users')
    total_downloads = c.fetchone()[0] or 0
    c.execute('SELECT COUNT(*) FROM users WHERE credits > 0')
    premium_users = c.fetchone()[0]
    conn.close()
    
    text = f"📊 Bot Statistics\n\n"
    text += f"👥 Total Users: {total_users}\n"
    text += f"⭐ Premium Users: {premium_users}\n"
    text += f"💳 Total Credits: {total_credits}\n"
    text += f"📊 Total Downloads: {total_downloads}\n"
    text += f"🔓 Free Window: {'✅ Enabled' if get_free_window_enabled() else '❌ Disabled'}\n"
    text += f"📊 Free Limit: {get_free_limit()}/day\n"
    text += f"🔧 Maintenance: {'⛔ Enabled' if get_maintenance_mode() else '✅ Disabled'}\n"
    text += f"\n⏰ Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    await update.message.reply_text(text)


# ==================== BUTTON HANDLER ====================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == "check_membership":
        await check_membership_callback(update, context)
    elif data == "view_plans":
        await view_plans(update, context)
    elif data == "check_credits":
        await check_credits(update, context)
    elif data == "main_menu":
        await main_menu_callback(update, context)
    elif data == "start_download":
        await start_download_callback(update, context)
    elif data == "help_menu":
        await help_menu(update, context)
    elif data == "admin_panel":
        await admin_panel(update, context)
    elif data == "admin_toggle_free":
        await admin_toggle_free(update, context)
    elif data == "admin_set_limit":
        await admin_set_limit_start(update, context)
    elif data == "admin_toggle_maintenance":
        await admin_toggle_maintenance(update, context)
    elif data == "admin_broadcast":
        await admin_broadcast_start(update, context)
    elif data == "admin_stats":
        await admin_stats_panel(update, context)
    elif data == "admin_list_users":
        await admin_list_users(update, context)


# ==================== CREATE APPLICATION ====================

async def post_init(application: Application):
    """Set bot commands after initialization"""
    await application.bot.set_my_commands([
        BotCommand("start", "Start the bot and show menu"),
        BotCommand("credits", "Check your credits and usage"),
        BotCommand("plans", "View premium plans"),
        BotCommand("admin", "Admin panel (admins only)"),
        BotCommand("cancel", "Cancel current operation"),
    ])
    logger.info("✅ Bot commands set!")


def create_application() -> Application:
    TOKEN = "8388884893:AAFwkLczazIQ_iA0oSSCx3VcdA702ghM15E"
    PROXY_URL = os.getenv("TELEGRAM_PROXY", None)
    
    if not TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN not set!")
        sys.exit(1)
    
    # Build application with job queue
    if PROXY_URL:
        request = HTTPXRequest(proxy_url=PROXY_URL, connect_timeout=30.0, read_timeout=60.0, write_timeout=30.0)
    else:
        request = HTTPXRequest(connect_timeout=30.0, read_timeout=60.0, write_timeout=30.0)
    
    application = (
        Application.builder()
        .token(TOKEN)
        .request(request)
        .job_queue(JobQueue())
        .post_init(post_init)
        .build()
    )
    
    # Main conversation handler
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(start_download_callback, pattern="^start_download$")
        ],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            MOBILE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_mobile)],
            EID_CAPTCHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_eid_captcha)],
            EID_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_eid_otp)],
            DOWNLOAD_CAPTCHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_download_captcha)],
            DOWNLOAD_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_download_otp)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start)
        ],
        allow_reentry=True,
        conversation_timeout=600
    )
    
    # Admin conversation for broadcast
    admin_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast$"),
            CommandHandler("broadcast", admin_broadcast_command)
        ],
        states={
            ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("admin", admin_command)
        ],
        allow_reentry=True
    )
    
    # Admin conversation for set limit
    admin_limit_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_set_limit_start, pattern="^admin_set_limit$"),
        ],
        states={
            ADMIN_SET_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_limit)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("admin", admin_command)
        ],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(admin_conv)
    application.add_handler(admin_limit_conv)
    
    # Admin commands
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("toggle_free", admin_toggle_free_command))
    application.add_handler(CommandHandler("set_limit", admin_set_limit_command))
    application.add_handler(CommandHandler("toggle_maintenance", admin_toggle_maintenance_command))
    application.add_handler(CommandHandler("broadcast", admin_broadcast_command))
    application.add_handler(CommandHandler("add", admin_add_credits))
    application.add_handler(CommandHandler("deduct", admin_deduct_credits))
    application.add_handler(CommandHandler("check", admin_check_user))
    application.add_handler(CommandHandler("stats", admin_stats_command))
    
    # User commands
    application.add_handler(CommandHandler("credits", check_credits))
    application.add_handler(CommandHandler("plans", view_plans_command))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(button_handler))
    
    return application


async def view_plans_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /plans command"""
    plans_text = "💰 Premium Plans\n\n"
    for key, plan in PLANS.items():
        plans_text += f"{key.title()} Plan\n"
        plans_text += f"📊 {plan['aadhaars']} Aadhaars\n"
        plans_text += f"💵 ₹{plan['price_inr']} / {plan['price_usdt']} USDT\n\n"
    
    plans_text += f"\n📩 Contact {BOT_OWNER} to purchase\n"
    plans_text += f"💳 Check /credits to see your balance"
    
    keyboard = [
        [InlineKeyboardButton("👤 Check Credits", callback_data="check_credits")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        plans_text,
        reply_markup=reply_markup
    )


def main() -> None:
    """Start the bot."""
    init_db()
    logger.info("🤖 Starting Aadhaar EID Bot...")
    
    try:
        application = create_application()
        logger.info("✅ Bot is running! Press Ctrl+C to stop.")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            close_loop=False
        )
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()