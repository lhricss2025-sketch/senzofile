"""
╔══════════════════════════════════════════════════════════════╗
║              🌟 SENZO PREMIUM BOT 🌟                         ║
║         Professional File Sharing & Referral System          ║
║              Powered by Senzo Technologies                   ║
╚══════════════════════════════════════════════════════════════╝

Version: 2.0.0
Library: python-telegram-bot==21.7
Database: aiosqlite (async SQLite)
"""

import asyncio
import aiosqlite
import logging
import random
import string
import os
from datetime import datetime, timedelta
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.error import BadRequest, Forbidden, TelegramError

# ─────────────────────────────────────────────
#               CONFIGURATION
# ─────────────────────────────────────────────

BOT_TOKEN = "8863632618:AAHybJVTAKAGoLGrF9CP_SvYhdUwo8j_eQg"
ADMIN_ID = 8105949422
DB_PATH = "senzo_bot.db"

# Rank thresholds
RANKS = [
    (0,    "Bronze 🥉"),
    (10,   "Silver 🥈"),
    (50,   "Gold 🥇"),
    (100,  "Platinum 💎"),
    (250,  "Diamond 👑"),
    (500,  "Legend 🌟"),
]

CREDITS_PER_REFERRAL = 5

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#          CONVERSATION STATE CONSTANTS
# ─────────────────────────────────────────────

# Admin upload states
(
    UPLOAD_WAIT_FILE,
    UPLOAD_WAIT_NAME,
    UPLOAD_WAIT_DESC,
    UPLOAD_WAIT_REFS,
    UPLOAD_WAIT_CREDITS,
    UPLOAD_WAIT_CHANNELS,
) = range(6)

# Broadcast states
(
    BROADCAST_WAIT_TYPE,
    BROADCAST_WAIT_MESSAGE,
    BROADCAST_WAIT_CONFIRM,
) = range(10, 13)

# Redeem states
REDEEM_WAIT_CODE = 20

# ─────────────────────────────────────────────
#              DATABASE SETUP
# ─────────────────────────────────────────────

async def init_db():
    """Initialize all database tables."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                credits INTEGER DEFAULT 0,
                total_referrals INTEGER DEFAULT 0,
                rank TEXT DEFAULT 'Bronze 🥉',
                joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_banned INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                file_id TEXT,
                file_type TEXT,
                required_refs INTEGER,
                required_credits INTEGER DEFAULT 0,
                admin_id INTEGER,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                views INTEGER DEFAULT 0,
                unlocks INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS product_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT,
                channel_username TEXT,
                FOREIGN KEY (product_id) REFERENCES products(id)
            );

            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER,
                product_id TEXT,
                credits_earned INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(referrer_id, referred_id, product_id)
            );

            CREATE TABLE IF NOT EXISTS user_unlocks (
                user_id INTEGER,
                product_id TEXT,
                unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, product_id)
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                type TEXT,
                description TEXT,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS redeem_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                points INTEGER,
                max_uses INTEGER,
                used_count INTEGER DEFAULT 0,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                is_active INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS redeem_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                user_id INTEGER,
                redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(code, user_id)
            );

            CREATE TABLE IF NOT EXISTS broadcast_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT,
                message_type TEXT,
                target_type TEXT,
                total_sent INTEGER DEFAULT 0,
                total_failed INTEGER DEFAULT 0,
                sent_by INTEGER,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()
    logger.info("✅ Database initialized successfully.")


# ─────────────────────────────────────────────
#               HELPER FUNCTIONS
# ─────────────────────────────────────────────

def get_rank(referrals: int) -> str:
    rank = RANKS[0][1]
    for threshold, name in RANKS:
        if referrals >= threshold:
            rank = name
    return rank


def generate_product_id(length=8) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def generate_redeem_code() -> str:
    parts = ["SENZO"] + [
        "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
        for _ in range(2)
    ]
    return "-".join(parts)


async def ensure_user(user_id: int, username: str, full_name: str):
    """Insert user if not exists, update last_active."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, username, full_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                last_active = CURRENT_TIMESTAMP
            """,
            (user_id, username or "", full_name or ""),
        )
        await db.commit()


async def get_user(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def add_credits(user_id: int, amount: int, description: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET credits = credits + ? WHERE user_id = ?",
            (amount, user_id),
        )
        await db.execute(
            "INSERT INTO transactions (user_id, amount, type, description) VALUES (?, ?, 'credit', ?)",
            (user_id, amount, description),
        )
        await db.commit()


async def update_rank(user_id: int):
    user = await get_user(user_id)
    if user:
        new_rank = get_rank(user["total_referrals"])
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET rank = ? WHERE user_id = ?",
                (new_rank, user_id),
            )
            await db.commit()


async def get_product(product_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM products WHERE id = ? AND is_active = 1", (product_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_product_channels(product_id: str) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT channel_username FROM product_channels WHERE product_id = ?",
            (product_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def count_referrals_for_product(referrer_id: int, product_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT COUNT(*) FROM referrals
            WHERE referrer_id = ? AND product_id = ? AND status = 'completed'
            """,
            (referrer_id, product_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def is_unlocked(user_id: int, product_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM user_unlocks WHERE user_id = ? AND product_id = ?",
            (user_id, product_id),
        ) as cursor:
            return await cursor.fetchone() is not None


async def unlock_product(user_id: int, product_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO user_unlocks (user_id, product_id) VALUES (?, ?)",
            (user_id, product_id),
        )
        await db.execute(
            "UPDATE products SET unlocks = unlocks + 1 WHERE id = ?",
            (product_id,),
        )
        await db.commit()


async def check_user_joined_channel(
    bot, user_id: int, channel_username: str
) -> bool:
    try:
        member = await bot.get_chat_member(channel_username, user_id)
        return member.status not in ("left", "kicked", "banned")
    except Exception:
        return False


async def get_all_users() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM users WHERE is_banned = 0"
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def get_active_users(days: int = 7) -> list:
    since = datetime.now() - timedelta(days=days)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM users WHERE is_banned = 0 AND last_active >= ?",
            (since.isoformat(),),
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def get_top_referrers(limit: int = 50) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM users WHERE is_banned = 0 ORDER BY total_referrals DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def get_premium_users(min_credits: int = 500) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM users WHERE is_banned = 0 AND credits >= ?",
            (min_credits,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def get_bot_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        stats = {}
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            stats["total_users"] = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM products WHERE is_active = 1"
        ) as c:
            stats["total_files"] = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM referrals WHERE status = 'completed'"
        ) as c:
            stats["completed_refs"] = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE type = 'credit'"
        ) as c:
            stats["total_credits"] = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM redeem_codes WHERE is_active = 1"
        ) as c:
            stats["active_codes"] = (await c.fetchone())[0]
        async with db.execute(
            "SELECT sent_at FROM broadcast_history ORDER BY sent_at DESC LIMIT 1"
        ) as c:
            row = await c.fetchone()
            stats["last_broadcast"] = row[0] if row else "Never"
        return stats


# ─────────────────────────────────────────────
#               UI BUILDERS
# ─────────────────────────────────────────────

def divider():
    return "━━━━━━━━━━━━━━━━━━━━━━"


def main_menu_text(user: dict) -> str:
    return (
        f"🌟 *SENZO PREMIUM* 🌟\n"
        f"{divider()}\n"
        f"*Welcome back, {user['full_name'] or 'User'}!*\n\n"
        f"👤 *YOUR STATS*\n"
        f"• Rank: {user['rank']}\n"
        f"• Credits: {user['credits']:,} 💰\n"
        f"• Referrals: {user['total_referrals']:,} 🔗\n\n"
        f"{divider()}\n\n"
        f"📱 *MAIN MENU*\n"
        f"Choose an option below:"
    )


def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📦 Browse Files", callback_data="browse_files")],
        [InlineKeyboardButton("🔗 My Referrals", callback_data="my_referrals"),
         InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
        [InlineKeyboardButton("💰 Redeem Code", callback_data="redeem_code"),
         InlineKeyboardButton("👤 My Profile", callback_data="my_profile")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    if is_admin:
        buttons.insert(0, [InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)


def admin_panel_text(stats: dict) -> str:
    last_bc = stats["last_broadcast"]
    if last_bc != "Never":
        try:
            dt = datetime.fromisoformat(last_bc)
            diff = datetime.now() - dt
            hours = int(diff.total_seconds() // 3600)
            last_bc = f"{hours} hours ago" if hours else "< 1 hour ago"
        except Exception:
            pass

    return (
        f"⚙️ *ADMIN DASHBOARD*\n"
        f"{divider()}\n\n"
        f"📊 *STATISTICS*\n"
        f"• 👥 Total Users: {stats['total_users']:,}\n"
        f"• 📦 Total Files: {stats['total_files']:,}\n"
        f"• 🔗 Completed Referrals: {stats['completed_refs']:,}\n"
        f"• 💰 Total Credits Given: {stats['total_credits']:,}\n"
        f"• 🎫 Active Redeem Codes: {stats['active_codes']:,}\n"
        f"• 📢 Last Broadcast: {last_bc}\n\n"
        f"{divider()}\n\n"
        f"🛠️ *ACTIONS*"
    )


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload New File", callback_data="admin_upload")],
        [InlineKeyboardButton("📋 Manage Files", callback_data="admin_manage_files"),
         InlineKeyboardButton("👥 View Users", callback_data="admin_view_users")],
        [InlineKeyboardButton("🎫 Generate Redeem Code", callback_data="admin_gen_code")],
        [InlineKeyboardButton("🔗 View All Redeem Codes", callback_data="admin_list_codes")],
        [InlineKeyboardButton("📢 Send Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📊 Full Statistics", callback_data="admin_full_stats")],
        [InlineKeyboardButton("💾 Backup Database", callback_data="admin_backup")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")],
    ])


# ─────────────────────────────────────────────
#               /start HANDLER
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username, user.full_name)
    context.user_data.clear()

    args = context.args or []
    if args:
        arg = args[0]

        # ── Product deep link ──────────────────
        if arg.startswith("product_"):
            product_id = arg[len("product_"):]
            await handle_product_link(update, context, user, product_id)
            return

        # ── Referral deep link ─────────────────
        if arg.startswith("ref_"):
            parts = arg.split("_")
            if len(parts) == 3:
                referrer_id = int(parts[1])
                product_id = parts[2]
                await handle_referral_join(update, context, user, referrer_id, product_id)
                return

    # Normal start
    user_data = await get_user(user.id)
    is_admin = user.id == ADMIN_ID
    await update.message.reply_text(
        main_menu_text(user_data),
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(is_admin),
    )


async def handle_product_link(update, context, user, product_id: str):
    product = await get_product(product_id)
    if not product:
        await update.message.reply_text("❌ This product link is invalid or no longer active.")
        return

    # Increment views
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE products SET views = views + 1 WHERE id = ?", (product_id,))
        await db.commit()

    channels = await get_product_channels(product_id)
    if channels:
        await show_channel_verification(update, context, user, product_id, channels)
    else:
        await show_product_page(update, context, user, product_id)


async def show_channel_verification(update, context, user, product_id: str, channels: list):
    buttons = []
    for ch in channels:
        clean = ch.lstrip("@")
        buttons.append([InlineKeyboardButton(f"📢 Join {ch}", url=f"https://t.me/{clean}")])
    buttons.append([
        InlineKeyboardButton(
            "✅ I've Joined – Verify Now",
            callback_data=f"verify_{product_id}",
        )
    ])

    channels_list = "\n".join(f"📢 {ch}" for ch in channels)
    text = (
        f"🔒 *CHANNEL VERIFICATION REQUIRED*\n"
        f"{divider()}\n\n"
        f"To access this file, you must join:\n\n"
        f"{channels_list}\n\n"
        f"{divider()}\n"
        f"⚠️ You must join *ALL* channels to proceed."
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown",
                                        reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown",
                                                      reply_markup=InlineKeyboardMarkup(buttons))


async def show_product_page(update, context, user, product_id: str):
    product = await get_product(product_id)
    if not product:
        return

    already_unlocked = await is_unlocked(user.id, product_id)
    if already_unlocked:
        await send_product_file(update, context, product, "🎉 You already unlocked this! Here it is again:")
        return

    ref_count = await count_referrals_for_product(user.id, product_id)
    req_refs = product["required_refs"]
    req_credits = product["required_credits"]

    user_data = await get_user(user.id)
    credits = user_data["credits"]

    ref_link = f"https://t.me/{context.bot.username}?start=ref_{user.id}_{product_id}"

    text = (
        f"📦 *{product['name']}*\n"
        f"{divider()}\n\n"
        f"📝 {product['description']}\n\n"
        f"*REQUIREMENTS:*\n"
        f"• 🔗 Referrals: {ref_count}/{req_refs} needed\n"
        f"• 💰 Credits: {credits}/{req_credits} needed\n\n"
        f"{divider()}\n"
        f"💡 Share your link with friends!\nEach referral = {CREDITS_PER_REFERRAL} credits + progress"
    )

    buttons = [
        [InlineKeyboardButton("🔗 Get Referral Link", callback_data=f"getref_{product_id}")],
        [InlineKeyboardButton("📊 Check Progress", callback_data=f"progress_{product_id}")],
    ]
    if req_credits > 0 and credits >= req_credits:
        buttons.append([InlineKeyboardButton("💰 Unlock with Credits", callback_data=f"unlockc_{product_id}")])

    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown",
                                        reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown",
                                                      reply_markup=InlineKeyboardMarkup(buttons))


async def handle_referral_join(update, context, user, referrer_id: int, product_id: str):
    referred_id = user.id

    # Prevent self-referral
    if referrer_id == referred_id:
        await update.message.reply_text("❌ You cannot refer yourself!")
        user_data = await get_user(user.id)
        await update.message.reply_text(
            main_menu_text(user_data),
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(user.id == ADMIN_ID),
        )
        return

    product = await get_product(product_id)
    if not product:
        user_data = await get_user(user.id)
        await update.message.reply_text(
            main_menu_text(user_data),
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(user.id == ADMIN_ID),
        )
        return

    # Try to record referral
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                """
                INSERT INTO referrals (referrer_id, referred_id, product_id, status)
                VALUES (?, ?, ?, 'completed')
                """,
                (referrer_id, referred_id, product_id),
            )
            await db.execute(
                "UPDATE users SET total_referrals = total_referrals + 1 WHERE user_id = ?",
                (referrer_id,),
            )
            await db.commit()
        except Exception:
            # Duplicate – already referred
            user_data = await get_user(user.id)
            await update.message.reply_text(
                "✅ You already joined through this referral link!\n\nWelcome back!",
            )
            await update.message.reply_text(
                main_menu_text(user_data),
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard(user.id == ADMIN_ID),
            )
            return

    # Add credits to referrer
    await add_credits(referrer_id, CREDITS_PER_REFERRAL, f"Referral bonus – {product['name']}")
    await update_rank(referrer_id)

    # Check if referrer now qualifies for unlock
    ref_count = await count_referrals_for_product(referrer_id, product_id)
    req_refs = product["required_refs"]
    if ref_count >= req_refs and not await is_unlocked(referrer_id, product_id):
        await unlock_product(referrer_id, product_id)
        try:
            await context.bot.send_message(
                chat_id=referrer_id,
                text=(
                    f"🎉 *Congratulations! File Unlocked!*\n"
                    f"{divider()}\n\n"
                    f"📦 *{product['name']}*\n\n"
                    f"You completed all {req_refs} referrals!\n"
                    f"Here is your file:"
                ),
                parse_mode="Markdown",
            )
            await send_file_to_user(context.bot, referrer_id, product)
        except Exception as e:
            logger.warning(f"Could not notify referrer {referrer_id}: {e}")
    else:
        try:
            await context.bot.send_message(
                chat_id=referrer_id,
                text=(
                    f"🎉 *New referral! +{CREDITS_PER_REFERRAL} credits!*\n"
                    f"📦 Progress for *{product['name']}*: {ref_count}/{req_refs}"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Could not notify referrer {referrer_id}: {e}")

    # Show new user main menu
    await update.message.reply_text(
        f"✅ You joined via referral link!\n\n"
        f"👤 {product['name']} – your friend is making progress!"
    )
    user_data = await get_user(user.id)
    await update.message.reply_text(
        main_menu_text(user_data),
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(user.id == ADMIN_ID),
    )


async def send_file_to_user(bot, user_id: int, product: dict):
    file_id = product["file_id"]
    file_type = product["file_type"]
    try:
        if file_type == "document":
            await bot.send_document(chat_id=user_id, document=file_id, caption=f"📦 {product['name']}")
        elif file_type == "video":
            await bot.send_video(chat_id=user_id, video=file_id, caption=f"📦 {product['name']}")
        elif file_type == "photo":
            await bot.send_photo(chat_id=user_id, photo=file_id, caption=f"📦 {product['name']}")
        elif file_type == "audio":
            await bot.send_audio(chat_id=user_id, audio=file_id, caption=f"📦 {product['name']}")
        elif file_type == "voice":
            await bot.send_voice(chat_id=user_id, voice=file_id, caption=f"📦 {product['name']}")
        else:
            await bot.send_document(chat_id=user_id, document=file_id, caption=f"📦 {product['name']}")
    except Exception as e:
        logger.error(f"Failed to send file to {user_id}: {e}")


async def send_product_file(update, context, product: dict, caption: str = ""):
    user_id = update.effective_user.id
    if caption:
        if update.message:
            await update.message.reply_text(caption)
        else:
            await context.bot.send_message(user_id, caption)
    await send_file_to_user(context.bot, user_id, product)


# ─────────────────────────────────────────────
#         CALLBACK QUERY HANDLER (dispatcher)
# ─────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user
    await ensure_user(user.id, user.username, user.full_name)

    # ── Main Menu ──────────────────────────────
    if data == "main_menu":
        user_data = await get_user(user.id)
        await query.edit_message_text(
            main_menu_text(user_data),
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(user.id == ADMIN_ID),
        )

    # ── Browse Files ───────────────────────────
    elif data == "browse_files":
        await show_file_list(query, context)

    # ── My Referrals ───────────────────────────
    elif data == "my_referrals":
        await show_my_referrals(query, context, user.id)

    # ── Leaderboard ────────────────────────────
    elif data == "leaderboard":
        await show_leaderboard(query, context, user.id)

    # ── Redeem Code ────────────────────────────
    elif data == "redeem_code":
        await query.edit_message_text(
            f"💰 *REDEEM CODE*\n{divider()}\n\nSend your redeem code using:\n`/redeem YOUR-CODE`\n\n"
            f"💡 Get codes from special events, giveaways & admin promotions.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
            ]),
        )

    # ── My Profile ─────────────────────────────
    elif data == "my_profile":
        await show_profile(query, context, user.id)

    # ── Help ───────────────────────────────────
    elif data == "help":
        await show_help(query)

    # ── Channel Verification ───────────────────
    elif data.startswith("verify_"):
        product_id = data[len("verify_"):]
        channels = await get_product_channels(product_id)
        all_joined = True
        for ch in channels:
            if not await check_user_joined_channel(context.bot, user.id, ch):
                all_joined = False
                break

        if all_joined:
            await show_product_page(update, context, user, product_id)
        else:
            await query.answer("❌ You haven't joined all channels yet!", show_alert=True)

    # ── Get Referral Link ──────────────────────
    elif data.startswith("getref_"):
        product_id = data[len("getref_"):]
        ref_link = f"https://t.me/{context.bot.username}?start=ref_{user.id}_{product_id}"
        await query.edit_message_text(
            f"🔗 *YOUR REFERRAL LINK*\n{divider()}\n\n"
            f"`{ref_link}`\n\n"
            f"Share this with friends to unlock the file!\nEach join = {CREDITS_PER_REFERRAL} credits.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Check Progress", callback_data=f"progress_{product_id}")],
                [InlineKeyboardButton("🔙 Back to File", callback_data=f"viewprod_{product_id}")],
            ]),
        )

    # ── Progress ───────────────────────────────
    elif data.startswith("progress_"):
        product_id = data[len("progress_"):]
        product = await get_product(product_id)
        ref_count = await count_referrals_for_product(user.id, product_id)
        req = product["required_refs"] if product else 0
        bar_filled = int((ref_count / req * 10)) if req else 0
        bar = "▓" * bar_filled + "░" * (10 - bar_filled)
        await query.edit_message_text(
            f"📊 *PROGRESS – {product['name'] if product else 'Unknown'}*\n"
            f"{divider()}\n\n"
            f"[{bar}] {ref_count}/{req}\n\n"
            f"You need {max(0, req - ref_count)} more referral(s)!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Get Referral Link", callback_data=f"getref_{product_id}")],
                [InlineKeyboardButton("🔙 Back", callback_data=f"viewprod_{product_id}")],
            ]),
        )

    # ── View Product ───────────────────────────
    elif data.startswith("viewprod_"):
        product_id = data[len("viewprod_"):]
        await show_product_page(update, context, user, product_id)

    # ── Unlock with Credits ────────────────────
    elif data.startswith("unlockc_"):
        product_id = data[len("unlockc_"):]
        await handle_credit_unlock(query, context, user.id, product_id)

    # ── Admin Panel ────────────────────────────
    elif data == "admin_panel":
        if user.id != ADMIN_ID:
            await query.answer("🔒 Access Denied!", show_alert=True)
            return
        stats = await get_bot_stats()
        await query.edit_message_text(
            admin_panel_text(stats),
            parse_mode="Markdown",
            reply_markup=admin_panel_keyboard(),
        )

    elif data == "admin_upload":
        if user.id != ADMIN_ID:
            await query.answer("🔒 Access Denied!", show_alert=True)
            return
        context.user_data.clear()
        context.user_data["upload_step"] = "file"
        await query.edit_message_text(
            "📤 *UPLOAD NEW FILE*\n\nStep 1: Send me the file (document, video, photo, audio, or voice).",
            parse_mode="Markdown",
        )

    elif data == "admin_manage_files":
        if user.id != ADMIN_ID:
            await query.answer("🔒 Access Denied!", show_alert=True)
            return
        await show_manage_files(query)

    elif data == "admin_view_users":
        if user.id != ADMIN_ID:
            await query.answer("🔒 Access Denied!", show_alert=True)
            return
        await show_user_list(query)

    elif data == "admin_gen_code":
        if user.id != ADMIN_ID:
            await query.answer("🔒 Access Denied!", show_alert=True)
            return
        await query.edit_message_text(
            "🎫 *GENERATE REDEEM CODE*\n\nUse the command:\n"
            "`/createredeem [points] [max_uses] [expiry_days]`\n\n"
            "Example: `/createredeem 50 100 30`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
            ]),
        )

    elif data == "admin_list_codes":
        if user.id != ADMIN_ID:
            await query.answer("🔒 Access Denied!", show_alert=True)
            return
        await show_redeem_codes_list(query)

    elif data == "admin_broadcast":
        if user.id != ADMIN_ID:
            await query.answer("🔒 Access Denied!", show_alert=True)
            return
        await show_broadcast_panel(query, context)

    elif data.startswith("broadcast_"):
        if user.id != ADMIN_ID:
            await query.answer("🔒 Access Denied!", show_alert=True)
            return
        await handle_broadcast_type_selection(query, context, data)

    elif data == "broadcast_confirm_yes":
        if user.id != ADMIN_ID:
            return
        await execute_broadcast(query, context)

    elif data == "broadcast_confirm_no":
        if user.id != ADMIN_ID:
            return
        context.user_data.pop("broadcast_msg", None)
        context.user_data.pop("broadcast_target", None)
        await query.edit_message_text(
            "❌ Broadcast cancelled.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]
            ]),
        )

    elif data == "admin_full_stats":
        if user.id != ADMIN_ID:
            await query.answer("🔒 Access Denied!", show_alert=True)
            return
        await show_full_stats(query)

    elif data == "admin_backup":
        if user.id != ADMIN_ID:
            await query.answer("🔒 Access Denied!", show_alert=True)
            return
        await do_backup(query, context)

    elif data.startswith("deactivate_"):
        if user.id != ADMIN_ID:
            await query.answer("🔒 Access Denied!", show_alert=True)
            return
        product_id = data[len("deactivate_"):]
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE products SET is_active = 0 WHERE id = ?", (product_id,))
            await db.commit()
        await query.answer("✅ Product deactivated.", show_alert=True)
        await show_manage_files(query)

    elif data.startswith("activate_"):
        if user.id != ADMIN_ID:
            await query.answer("🔒 Access Denied!", show_alert=True)
            return
        product_id = data[len("activate_"):]
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE products SET is_active = 1 WHERE id = ?", (product_id,))
            await db.commit()
        await query.answer("✅ Product activated.", show_alert=True)
        await show_manage_files(query)


# ─────────────────────────────────────────────
#              DISPLAY HELPERS
# ─────────────────────────────────────────────

async def show_file_list(query, context):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, name, required_refs, required_credits FROM products WHERE is_active = 1 ORDER BY created_at DESC LIMIT 20"
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await query.edit_message_text(
            "📦 No files available yet. Check back later!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
            ]),
        )
        return

    buttons = []
    for pid, name, req_refs, req_credits in rows:
        label = f"📦 {name} (Refs: {req_refs}"
        if req_credits:
            label += f" | Credits: {req_credits}"
        label += ")"
        link = f"https://t.me/{context.bot.username}?start=product_{pid}"
        buttons.append([InlineKeyboardButton(label, url=link)])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])

    await query.edit_message_text(
        f"📦 *AVAILABLE FILES*\n{divider()}\n\nClick any file to view details:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_my_referrals(query, context, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT r.product_id, p.name, COUNT(*) as ref_count, p.required_refs
            FROM referrals r
            LEFT JOIN products p ON r.product_id = p.id
            WHERE r.referrer_id = ? AND r.status = 'completed'
            GROUP BY r.product_id
            """,
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        text = (
            f"🔗 *MY REFERRALS*\n{divider()}\n\n"
            "You haven't made any referrals yet.\n\nBrowse files to get your referral link!"
        )
    else:
        lines = [f"🔗 *MY REFERRALS*\n{divider()}\n"]
        for pid, name, count, req in rows:
            bar_filled = min(10, int(count / req * 10)) if req else 10
            bar = "▓" * bar_filled + "░" * (10 - bar_filled)
            lines.append(f"📦 *{name}*\n[{bar}] {count}/{req}\n")
        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
        ]),
    )


async def show_leaderboard(query, context, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, full_name, username, total_referrals FROM users ORDER BY total_referrals DESC LIMIT 10"
        ) as cursor:
            rows = await cursor.fetchall()

        # User rank
        async with db.execute(
            """
            SELECT COUNT(*) + 1 FROM users
            WHERE total_referrals > (SELECT total_referrals FROM users WHERE user_id = ?)
            """,
            (user_id,),
        ) as cursor:
            rank_row = await cursor.fetchone()
            user_rank = rank_row[0] if rank_row else "?"

        async with db.execute(
            "SELECT total_referrals FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            ur = await cursor.fetchone()
            my_refs = ur[0] if ur else 0

    medals = ["🥇", "🥈", "🥉"] + [""] * 7
    lines = [f"🏆 *TOP REFERRERS*\n{divider()}\n"]
    for i, (uid, name, uname, refs) in enumerate(rows):
        medal = medals[i] if i < len(medals) else ""
        display = uname or name or f"User{uid}"
        lines.append(f"{medal} {i+1}. {display} – {refs:,} referrals")

    lines.append(f"\n{divider()}")
    lines.append(f"📌 *YOUR RANK: #{user_rank}*")
    lines.append(f"🔗 Total referrals: {my_refs:,}")

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="leaderboard"),
             InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
        ]),
    )


async def show_profile(query, context, user_id: int):
    user_data = await get_user(user_id)
    if not user_data:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM user_unlocks WHERE user_id = ?", (user_id,)
        ) as cursor:
            unlocked = (await cursor.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND status = 'completed' AND date >= datetime('now', '-7 days')",
            (user_id,),
        ) as cursor:
            last7 = (await cursor.fetchone())[0]

    joined = user_data.get("joined_date", "")[:10] if user_data.get("joined_date") else "Unknown"
    uname = f"@{user_data['username']}" if user_data.get("username") else "No username"

    text = (
        f"👤 *MY PROFILE*\n{divider()}\n\n"
        f"• User ID: `{user_id}`\n"
        f"• Username: {uname}\n"
        f"• Join Date: {joined}\n"
        f"• Rank: {user_data['rank']}\n\n"
        f"{divider()}\n\n"
        f"💰 CREDITS: {user_data['credits']:,}\n"
        f"🔗 REFERRALS: {user_data['total_referrals']:,}\n"
        f"📦 UNLOCKED FILES: {unlocked}\n\n"
        f"{divider()}\n\n"
        f"📊 *REFERRAL HISTORY*\n"
        f"• Last 7 days: {last7} referrals"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")]
        ]),
    )


async def show_help(query):
    text = (
        f"❓ *HELP & COMMANDS*\n{divider()}\n\n"
        f"*User Commands:*\n"
        f"• `/start` – Main menu\n"
        f"• `/redeem [code]` – Redeem a code\n"
        f"• `/myrefs` – Show my referrals\n"
        f"• `/profile` – My profile\n\n"
        f"*How to unlock files:*\n"
        f"1. Open a file link\n"
        f"2. Join required channels\n"
        f"3. Share your referral link\n"
        f"4. Collect required referrals\n"
        f"5. File is automatically sent!\n\n"
        f"*Credits:*\n"
        f"• Each referral = {CREDITS_PER_REFERRAL} credits\n"
        f"• Redeem codes for bonus credits\n\n"
        f"*Powered by Senzo Technologies* 🌟"
    )
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
        ]),
    )


async def handle_credit_unlock(query, context, user_id: int, product_id: str):
    product = await get_product(product_id)
    if not product:
        return
    user_data = await get_user(user_id)
    if user_data["credits"] >= product["required_credits"]:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET credits = credits - ? WHERE user_id = ?",
                (product["required_credits"], user_id),
            )
            await db.execute(
                "INSERT INTO transactions (user_id, amount, type, description) VALUES (?, ?, 'debit', ?)",
                (user_id, product["required_credits"], f"Unlocked: {product['name']}"),
            )
            await db.commit()
        await unlock_product(user_id, product_id)
        await query.edit_message_text(
            f"✅ *File Unlocked with Credits!*\n\n📦 {product['name']}\n\n"
            f"💰 {product['required_credits']} credits deducted.\nHere is your file:",
            parse_mode="Markdown",
        )
        await send_file_to_user(context.bot, user_id, product)
    else:
        await query.answer("❌ Insufficient credits!", show_alert=True)


async def show_manage_files(query):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, name, is_active, views, unlocks FROM products ORDER BY created_at DESC LIMIT 15"
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await query.edit_message_text(
            "📋 No files uploaded yet.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
            ]),
        )
        return

    buttons = []
    for pid, name, active, views, unlocks in rows:
        status = "✅" if active else "❌"
        action = "deactivate" if active else "activate"
        buttons.append([
            InlineKeyboardButton(
                f"{status} {name} (V:{views} U:{unlocks})",
                callback_data=f"{action}_{pid}",
            )
        ])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])

    await query.edit_message_text(
        f"📋 *MANAGE FILES*\n{divider()}\n\nClick to toggle active/inactive:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_user_list(query):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, full_name, username, credits, total_referrals FROM users ORDER BY joined_date DESC LIMIT 10"
        ) as cursor:
            rows = await cursor.fetchall()

    lines = [f"👥 *RECENT USERS (last 10)*\n{divider()}\n"]
    for uid, name, uname, credits, refs in rows:
        display = uname or name or f"User{uid}"
        lines.append(f"• {display} | 💰{credits} | 🔗{refs}")

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
        ]),
    )


async def show_redeem_codes_list(query):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT code, points, max_uses, used_count, expires_at, is_active FROM redeem_codes ORDER BY created_at DESC LIMIT 15"
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await query.edit_message_text(
            "🎫 No redeem codes created yet.\n\nUse `/createredeem points uses days`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
            ]),
        )
        return

    lines = [f"🎫 *REDEEM CODES*\n{divider()}\n"]
    for code, pts, max_u, used, expires, active in rows:
        status = "✅" if active else "❌"
        exp = expires[:10] if expires else "No expiry"
        remaining = max_u - used
        lines.append(f"{status} `{code}`\n  +{pts}pts | {used}/{max_u} used | Exp: {exp} | Left: {remaining}\n")

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
        ]),
    )


async def show_broadcast_panel(query, context):
    await query.edit_message_text(
        f"📢 *BROADCAST PANEL*\n{divider()}\n\nSelect your target audience:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 All Users", callback_data="broadcast_all")],
            [InlineKeyboardButton("🎯 Active (last 7 days)", callback_data="broadcast_active")],
            [InlineKeyboardButton("🏆 Top Referrers (top 50)", callback_data="broadcast_top")],
            [InlineKeyboardButton("💎 Premium (500+ credits)", callback_data="broadcast_premium")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
        ]),
    )


async def handle_broadcast_type_selection(query, context, data: str):
    target_map = {
        "broadcast_all": "all",
        "broadcast_active": "active",
        "broadcast_top": "top",
        "broadcast_premium": "premium",
    }
    target = target_map.get(data)
    if not target:
        return
    context.user_data["broadcast_target"] = target
    context.user_data["broadcast_step"] = "waiting_message"
    await query.edit_message_text(
        f"📢 Broadcast to: *{target.upper()}* users\n\n"
        "Now send me your broadcast message (text, photo, video, or document):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]
        ]),
    )


async def execute_broadcast(query, context):
    target = context.user_data.get("broadcast_target", "all")
    msg_data = context.user_data.get("broadcast_msg")

    if not msg_data:
        await query.edit_message_text("❌ No message to broadcast.")
        return

    # Get user list
    if target == "all":
        user_ids = await get_all_users()
    elif target == "active":
        user_ids = await get_active_users(7)
    elif target == "top":
        user_ids = await get_top_referrers(50)
    elif target == "premium":
        user_ids = await get_premium_users(500)
    else:
        user_ids = await get_all_users()

    total = len(user_ids)
    sent = 0
    failed = 0

    status_msg = await query.edit_message_text(
        f"📢 *Broadcasting...*\n\nTarget: {total} users\nSent: 0 | Failed: 0",
        parse_mode="Markdown",
    )

    msg_type = msg_data.get("type", "text")

    for i, uid in enumerate(user_ids):
        try:
            if msg_type == "text":
                await context.bot.send_message(chat_id=uid, text=msg_data["content"])
            elif msg_type == "photo":
                await context.bot.send_photo(chat_id=uid, photo=msg_data["file_id"],
                                             caption=msg_data.get("caption", ""))
            elif msg_type == "video":
                await context.bot.send_video(chat_id=uid, video=msg_data["file_id"],
                                             caption=msg_data.get("caption", ""))
            elif msg_type == "document":
                await context.bot.send_document(chat_id=uid, document=msg_data["file_id"],
                                                caption=msg_data.get("caption", ""))
            sent += 1
        except (Forbidden, BadRequest):
            failed += 1
        except Exception:
            failed += 1

        # Update progress every 50 users
        if (i + 1) % 50 == 0:
            try:
                await status_msg.edit_text(
                    f"📢 *Broadcasting...*\n\nTarget: {total} users\nSent: {sent} | Failed: {failed}",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        # Rate limiting: ~30 msg/sec
        if (i + 1) % 30 == 0:
            await asyncio.sleep(1)

    # Save broadcast history
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO broadcast_history (message, message_type, target_type, total_sent, total_failed, sent_by) VALUES (?, ?, ?, ?, ?, ?)",
            (str(msg_data.get("content", "")), msg_type, target, sent, failed, ADMIN_ID),
        )
        await db.commit()

    # Clear broadcast data
    context.user_data.pop("broadcast_msg", None)
    context.user_data.pop("broadcast_target", None)
    context.user_data.pop("broadcast_step", None)

    await status_msg.edit_text(
        f"✅ *Broadcast Completed!*\n\n"
        f"📊 Results:\n"
        f"• ✅ Sent: {sent}\n"
        f"• ❌ Failed: {failed}\n"
        f"• 📋 Total: {total}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]
        ]),
    )


async def show_full_stats(query):
    stats = await get_bot_stats()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE joined_date >= datetime('now', '-7 days')"
        ) as cursor:
            new_users_7d = (await cursor.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE last_active >= datetime('now', '-24 hours')"
        ) as cursor:
            active_24h = (await cursor.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM user_unlocks"
        ) as cursor:
            total_unlocks = (await cursor.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM broadcast_history"
        ) as cursor:
            total_broadcasts = (await cursor.fetchone())[0]

    text = (
        f"📊 *FULL STATISTICS*\n{divider()}\n\n"
        f"👥 Total Users: {stats['total_users']:,}\n"
        f"🆕 New (last 7d): {new_users_7d:,}\n"
        f"🟢 Active (24h): {active_24h:,}\n\n"
        f"📦 Total Files: {stats['total_files']:,}\n"
        f"🔓 Total Unlocks: {total_unlocks:,}\n\n"
        f"🔗 Completed Referrals: {stats['completed_refs']:,}\n"
        f"💰 Total Credits Distributed: {stats['total_credits']:,}\n\n"
        f"🎫 Active Redeem Codes: {stats['active_codes']:,}\n"
        f"📢 Total Broadcasts Sent: {total_broadcasts:,}"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
        ]),
    )


async def do_backup(query, context):
    await query.edit_message_text("⏳ *Creating backup...*", parse_mode="Markdown")
    try:
        if os.path.exists(DB_PATH):
            with open(DB_PATH, "rb") as f:
                await context.bot.send_document(
                    chat_id=ADMIN_ID,
                    document=f,
                    filename=f"senzo_bot_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
                    caption="💾 Database backup",
                )
            await query.edit_message_text(
                "✅ Backup sent to your chat!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
                ]),
            )
        else:
            await query.edit_message_text("❌ Database file not found.")
    except Exception as e:
        await query.edit_message_text(f"❌ Backup failed: {e}")


# ─────────────────────────────────────────────
#           MESSAGE HANDLER (Admin Upload)
# ─────────────────────────────────────────────

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username, user.full_name)
    msg = update.message

    # ── ADMIN: Broadcast waiting for message ──
    if (
        user.id == ADMIN_ID
        and context.user_data.get("broadcast_step") == "waiting_message"
    ):
        await handle_broadcast_message_input(update, context)
        return

    # ── ADMIN: File upload flow ────────────────
    if user.id == ADMIN_ID and "upload_step" in context.user_data:
        await handle_admin_upload_step(update, context)
        return

    # ── DEFAULT: re-show main menu ─────────────
    user_data = await get_user(user.id)
    await msg.reply_text(
        main_menu_text(user_data),
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(user.id == ADMIN_ID),
    )


async def handle_broadcast_message_input(update, context):
    msg = update.message
    msg_data = {}

    if msg.text:
        msg_data = {"type": "text", "content": msg.text}
    elif msg.photo:
        msg_data = {"type": "photo", "file_id": msg.photo[-1].file_id, "caption": msg.caption or ""}
    elif msg.video:
        msg_data = {"type": "video", "file_id": msg.video.file_id, "caption": msg.caption or ""}
    elif msg.document:
        msg_data = {"type": "document", "file_id": msg.document.file_id, "caption": msg.caption or ""}
    else:
        await msg.reply_text("❌ Unsupported message type. Please send text, photo, video, or document.")
        return

    context.user_data["broadcast_msg"] = msg_data
    context.user_data.pop("broadcast_step", None)

    preview_text = (
        f"📢 *BROADCAST PREVIEW*\n{divider()}\n\n"
        f"Type: {msg_data['type']}\n"
        f"Target: {context.user_data.get('broadcast_target', 'all').upper()}\n\n"
        "Send this broadcast?"
    )

    await msg.reply_text(
        preview_text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ YES – Send Now", callback_data="broadcast_confirm_yes"),
             InlineKeyboardButton("❌ NO – Cancel", callback_data="broadcast_confirm_no")],
        ]),
    )


async def handle_admin_upload_step(update, context):
    msg = update.message
    step = context.user_data.get("upload_step")

    if step == "file":
        # Detect file type
        if msg.document:
            context.user_data["file_id"] = msg.document.file_id
            context.user_data["file_type"] = "document"
        elif msg.video:
            context.user_data["file_id"] = msg.video.file_id
            context.user_data["file_type"] = "video"
        elif msg.photo:
            context.user_data["file_id"] = msg.photo[-1].file_id
            context.user_data["file_type"] = "photo"
        elif msg.audio:
            context.user_data["file_id"] = msg.audio.file_id
            context.user_data["file_type"] = "audio"
        elif msg.voice:
            context.user_data["file_id"] = msg.voice.file_id
            context.user_data["file_type"] = "voice"
        else:
            await msg.reply_text("❌ Please send a file (document, video, photo, audio, or voice).")
            return
        context.user_data["upload_step"] = "name"
        await msg.reply_text("✅ File received!\n\n📝 Step 2: Enter the *file name/title*:", parse_mode="Markdown")

    elif step == "name":
        context.user_data["file_name"] = msg.text.strip()
        context.user_data["upload_step"] = "desc"
        await msg.reply_text("✅ Name saved!\n\n📝 Step 3: Enter the *description*:", parse_mode="Markdown")

    elif step == "desc":
        context.user_data["file_desc"] = msg.text.strip()
        context.user_data["upload_step"] = "refs"
        await msg.reply_text("✅ Description saved!\n\n🔢 Step 4: Enter *required referrals count* (e.g., 5):", parse_mode="Markdown")

    elif step == "refs":
        try:
            refs = int(msg.text.strip())
            if refs < 0:
                raise ValueError
            context.user_data["required_refs"] = refs
            context.user_data["upload_step"] = "credits"
            await msg.reply_text("✅ Referrals set!\n\n💰 Step 5: Enter *required credits* (0 for free):", parse_mode="Markdown")
        except ValueError:
            await msg.reply_text("❌ Please enter a valid number.")

    elif step == "credits":
        try:
            credits = int(msg.text.strip())
            if credits < 0:
                raise ValueError
            context.user_data["required_credits"] = credits
            context.user_data["upload_step"] = "channels"
            await msg.reply_text(
                "✅ Credits set!\n\n📢 Step 6: Enter *mandatory channels* "
                "(space-separated, e.g. `@channel1 @channel2`)\n\nOr type `skip` to skip:",
                parse_mode="Markdown",
            )
        except ValueError:
            await msg.reply_text("❌ Please enter a valid number.")

    elif step == "channels":
        text = msg.text.strip()
        channels = []
        if text.lower() != "skip":
            raw_channels = text.split()
            for ch in raw_channels:
                if not ch.startswith("@"):
                    ch = "@" + ch
                channels.append(ch)
        context.user_data["channels"] = channels
        await finalize_upload(update, context)


async def finalize_upload(update, context):
    msg = update.message
    data = context.user_data
    product_id = generate_product_id()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO products (id, name, description, file_id, file_type, required_refs, required_credits, admin_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                data["file_name"],
                data["file_desc"],
                data["file_id"],
                data["file_type"],
                data["required_refs"],
                data["required_credits"],
                ADMIN_ID,
            ),
        )
        for ch in data.get("channels", []):
            await db.execute(
                "INSERT INTO product_channels (product_id, channel_username) VALUES (?, ?)",
                (product_id, ch),
            )
        await db.commit()

    context.user_data.clear()

    bot_username = (await context.bot.get_me()).username
    product_link = f"https://t.me/{bot_username}?start=product_{product_id}"
    channels_text = ", ".join(data.get("channels", [])) or "None"

    await msg.reply_text(
        f"✅ *FILE UPLOADED SUCCESSFULLY!*\n{divider()}\n\n"
        f"📦 Name: {data['file_name']}\n"
        f"🔑 Product ID: `{product_id}`\n"
        f"🔗 Required Refs: {data['required_refs']}\n"
        f"💰 Required Credits: {data['required_credits']}\n"
        f"📢 Channels: {channels_text}\n\n"
        f"🌐 *Product Link:*\n`{product_link}`\n\n"
        f"Share this link with users!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")]
        ]),
    )


# ─────────────────────────────────────────────
#              COMMAND HANDLERS
# ─────────────────────────────────────────────

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("🔒 *Access Denied!* Admin only.", parse_mode="Markdown")
        return
    await ensure_user(user.id, user.username, user.full_name)
    stats = await get_bot_stats()
    await update.message.reply_text(
        admin_panel_text(stats),
        parse_mode="Markdown",
        reply_markup=admin_panel_keyboard(),
    )


async def cmd_createredeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "❌ Usage: `/createredeem [points] [max_uses] [expiry_days]`\n"
            "Example: `/createredeem 50 100 30`",
            parse_mode="Markdown",
        )
        return

    try:
        points = int(args[0])
        max_uses = int(args[1])
        expiry_days = int(args[2])
    except ValueError:
        await update.message.reply_text("❌ All arguments must be integers.")
        return

    code = generate_redeem_code()
    expires_at = (datetime.now() + timedelta(days=expiry_days)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO redeem_codes (code, points, max_uses, created_by, expires_at) VALUES (?, ?, ?, ?, ?)",
            (code, points, max_uses, ADMIN_ID, expires_at),
        )
        await db.commit()

    await update.message.reply_text(
        f"✅ *REDEEM CODE CREATED!*\n{divider()}\n\n"
        f"🎫 Code: `{code}`\n"
        f"💰 Points: {points}\n"
        f"👥 Max Uses: {max_uses}\n"
        f"📅 Expires: {expires_at[:10]}\n\n"
        f"Share this code with your users!",
        parse_mode="Markdown",
    )


async def cmd_listcodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!")
        return
    # Forward to inline view
    await update.message.reply_text("⏳ Fetching codes...")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT code, points, max_uses, used_count, expires_at, is_active FROM redeem_codes ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await update.message.reply_text("🎫 No redeem codes yet.")
        return

    lines = [f"🎫 *ALL REDEEM CODES*\n{divider()}\n"]
    for code, pts, max_u, used, expires, active in rows:
        status = "✅" if active else "❌"
        exp = expires[:10] if expires else "No expiry"
        lines.append(f"{status} `{code}` | +{pts}pts | {used}/{max_u} | Exp: {exp}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_deletecode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!")
        return

    if not context.args:
        await update.message.reply_text("❌ Usage: `/deletecode [CODE]`", parse_mode="Markdown")
        return

    code = context.args[0].upper()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE redeem_codes SET is_active = 0 WHERE code = ?", (code,))
        await db.commit()

    await update.message.reply_text(f"✅ Code `{code}` deactivated.", parse_mode="Markdown")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!")
        return
    msg = await update.message.reply_text(
        f"📢 *BROADCAST PANEL*\n{divider()}\n\nSelect your target audience:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 All Users", callback_data="broadcast_all")],
            [InlineKeyboardButton("🎯 Active (last 7 days)", callback_data="broadcast_active")],
            [InlineKeyboardButton("🏆 Top Referrers (top 50)", callback_data="broadcast_top")],
            [InlineKeyboardButton("💎 Premium (500+ credits)", callback_data="broadcast_premium")],
        ]),
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!")
        return
    stats = await get_bot_stats()
    await update.message.reply_text(
        f"📊 *BOT STATISTICS*\n{divider()}\n\n"
        f"👥 Users: {stats['total_users']:,}\n"
        f"📦 Files: {stats['total_files']:,}\n"
        f"🔗 Referrals: {stats['completed_refs']:,}\n"
        f"💰 Credits: {stats['total_credits']:,}\n"
        f"🎫 Active Codes: {stats['active_codes']:,}",
        parse_mode="Markdown",
    )


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!")
        return
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "rb") as f:
            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=f,
                filename=f"senzo_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
                caption="💾 Database backup",
            )
    else:
        await update.message.reply_text("❌ Database file not found.")


async def cmd_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username, user.full_name)

    if not context.args:
        await update.message.reply_text(
            "💰 *REDEEM CODE*\n\nUsage: `/redeem YOUR-CODE`\nExample: `/redeem SENZO-A1B2C-D3E4F`",
            parse_mode="Markdown",
        )
        return

    code = context.args[0].upper()
    now = datetime.now()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT points, max_uses, used_count, expires_at, is_active FROM redeem_codes WHERE code = ?",
            (code,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            await update.message.reply_text("❌ *Invalid code!*", parse_mode="Markdown")
            return

        points, max_uses, used_count, expires_at, is_active = row

        if not is_active:
            await update.message.reply_text("❌ *This code is no longer active!*", parse_mode="Markdown")
            return

        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at)
                if now > expiry:
                    await update.message.reply_text("❌ *Code expired!*", parse_mode="Markdown")
                    return
            except Exception:
                pass

        if used_count >= max_uses:
            await update.message.reply_text("❌ *Code reached maximum uses!*", parse_mode="Markdown")
            return

        # Check if user already used this code
        async with db.execute(
            "SELECT 1 FROM redeem_usage WHERE code = ? AND user_id = ?",
            (code, user.id),
        ) as cursor:
            already_used = await cursor.fetchone()

        if already_used:
            await update.message.reply_text("❌ *Code already used by you!*", parse_mode="Markdown")
            return

        # Redeem!
        await db.execute(
            "INSERT INTO redeem_usage (code, user_id) VALUES (?, ?)",
            (code, user.id),
        )
        await db.execute(
            "UPDATE redeem_codes SET used_count = used_count + 1 WHERE code = ?",
            (code,),
        )
        await db.commit()

    await add_credits(user.id, points, f"Redeem code: {code}")
    user_data = await get_user(user.id)

    await update.message.reply_text(
        f"✅ *CODE REDEEMED SUCCESSFULLY!*\n{divider()}\n\n"
        f"• Code: `{code}`\n"
        f"• +{points} Credits added to your account\n"
        f"• New balance: {user_data['credits']:,} credits\n\n"
        f"{divider()}\n"
        f"Thank you for using Senzo Premium! 🌟",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
        ]),
    )


async def cmd_myrefs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username, user.full_name)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT r.product_id, p.name, COUNT(*) as ref_count, p.required_refs
            FROM referrals r
            LEFT JOIN products p ON r.product_id = p.id
            WHERE r.referrer_id = ? AND r.status = 'completed'
            GROUP BY r.product_id
            """,
            (user.id,),
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await update.message.reply_text("🔗 You haven't made any referrals yet.")
        return

    lines = [f"🔗 *MY REFERRALS*\n{divider()}\n"]
    for pid, name, count, req in rows:
        bar_filled = min(10, int(count / req * 10)) if req else 10
        bar = "▓" * bar_filled + "░" * (10 - bar_filled)
        lines.append(f"📦 *{name}*\n[{bar}] {count}/{req}\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username, user.full_name)
    user_data = await get_user(user.id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM user_unlocks WHERE user_id = ?", (user.id,)
        ) as cursor:
            unlocked = (await cursor.fetchone())[0]

    joined = user_data.get("joined_date", "")[:10] if user_data.get("joined_date") else "Unknown"
    uname = f"@{user_data['username']}" if user_data.get("username") else "No username"

    await update.message.reply_text(
        f"👤 *MY PROFILE*\n{divider()}\n\n"
        f"• User ID: `{user.id}`\n"
        f"• Username: {uname}\n"
        f"• Join Date: {joined}\n"
        f"• Rank: {user_data['rank']}\n\n"
        f"{divider()}\n\n"
        f"💰 CREDITS: {user_data['credits']:,}\n"
        f"🔗 REFERRALS: {user_data['total_referrals']:,}\n"
        f"📦 UNLOCKED FILES: {unlocked}",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"❓ *HELP & COMMANDS*\n{divider()}\n\n"
        f"• `/start` – Main menu\n"
        f"• `/redeem [code]` – Redeem a code\n"
        f"• `/myrefs` – My referrals\n"
        f"• `/profile` – My profile\n"
        f"• `/help` – This message\n\n"
        f"*Powered by Senzo Technologies* 🌟",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
#              ERROR HANDLER
# ─────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception: {context.error}", exc_info=context.error)


# ─────────────────────────────────────────────
#                   MAIN
# ─────────────────────────────────────────────

async def post_init(application):
    await init_db()
    logger.info("🚀 Senzo Premium Bot is running!")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # ── Command Handlers ──────────────────────
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("createredeem", cmd_createredeem))
    app.add_handler(CommandHandler("listcodes", cmd_listcodes))
    app.add_handler(CommandHandler("deletecode", cmd_deletecode))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("redeem", cmd_redeem))
    app.add_handler(CommandHandler("myrefs", cmd_myrefs))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("help", cmd_help))

    # ── Callback Query Handler ─────────────────
    app.add_handler(CallbackQueryHandler(callback_handler))

    # ── Message Handler ────────────────────────
    app.add_handler(
        MessageHandler(
            filters.TEXT | filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE,
            message_handler,
        )
    )

    # ── Error Handler ──────────────────────────
    app.add_error_handler(error_handler)

    logger.info("✅ All handlers registered. Starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
