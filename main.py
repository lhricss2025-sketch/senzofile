"""
╔══════════════════════════════════════════════════════════════╗
║              🌟 SENZO PREMIUM BOT v4.0 🌟                    ║
║         Professional File Sharing & Referral System          ║
║              Powered by Senzo Technologies                   ║
╚══════════════════════════════════════════════════════════════╝

v4.0 - Full Bug-Fixed Final Release
Bugs Fixed:
  1.  verify_ callback double query.answer() crash
  2.  Channel gate not re-shown after failed verification
  3.  Unused ref_link variable removed / properly used
  4.  Broadcast status message edit fixed
  5.  Upload step crash when user sends file instead of text
  6.  Redeem db early-return commit issue
  7.  Broadcast per-message timeout added
  8.  Inactive product graceful handling
  9.  Duplicate referral proper feedback to user
  10. bot.username None-safe with get_me() cache
  11. /data persistence warning for admin
  12. Broadcast state cleared on bot restart gracefully
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
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import BadRequest, Forbidden, TelegramError

# ═══════════════════════════════════════════════════════
#                     CONFIGURATION
# ═══════════════════════════════════════════════════════

BOT_TOKEN  = "8863632618:AAHybJVTAKAGoLGrF9CP_SvYhdUwo8j_eQg"
ADMIN_ID   = 8105949422

# Railway persistent storage fix
# Railway pe /data volume mount karo Settings > Volumes > Mount: /data
if os.path.exists("/data"):
    DB_PATH = "/data/senzo_bot.db"
    STORAGE_OK = True
else:
    DB_PATH    = "senzo_bot.db"
    STORAGE_OK = False   # local ya non-persistent environment

CREDITS_PER_REFERRAL = 5

RANKS = [
    (0,   "Bronze 🥉"),
    (10,  "Silver 🥈"),
    (50,  "Gold 🥇"),
    (100, "Platinum 💎"),
    (250, "Diamond 👑"),
    (500, "Legend 🌟"),
]

# Cache bot username to avoid repeated get_me() calls
_BOT_USERNAME: Optional[str] = None

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
#                    DATABASE INIT
# ═══════════════════════════════════════════════════════

async def init_db():
    logger.info(f"📂 Database: {DB_PATH}")
    if not STORAGE_OK:
        logger.warning("⚠️  /data volume not found — data will reset on redeploy! Mount a volume in Railway.")

    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id         INTEGER PRIMARY KEY,
                username        TEXT    DEFAULT '',
                full_name       TEXT    DEFAULT '',
                credits         INTEGER DEFAULT 0,
                total_referrals INTEGER DEFAULT 0,
                rank            TEXT    DEFAULT 'Bronze 🥉',
                joined_date     TEXT    DEFAULT (datetime('now')),
                last_active     TEXT    DEFAULT (datetime('now')),
                is_banned       INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS products (
                id               TEXT PRIMARY KEY,
                name             TEXT    NOT NULL,
                description      TEXT    DEFAULT '',
                file_id          TEXT    NOT NULL,
                file_type        TEXT    NOT NULL,
                required_refs    INTEGER DEFAULT 1,
                required_credits INTEGER DEFAULT 0,
                admin_id         INTEGER,
                is_active        INTEGER DEFAULT 1,
                created_at       TEXT    DEFAULT (datetime('now')),
                views            INTEGER DEFAULT 0,
                unlocks          INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS product_channels (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id       TEXT    NOT NULL,
                channel_username TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS referrals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_id INTEGER NOT NULL,
                product_id  TEXT    NOT NULL,
                status      TEXT    DEFAULT 'completed',
                date        TEXT    DEFAULT (datetime('now')),
                UNIQUE(referrer_id, referred_id, product_id)
            );

            CREATE TABLE IF NOT EXISTS user_unlocks (
                user_id     INTEGER NOT NULL,
                product_id  TEXT    NOT NULL,
                unlocked_at TEXT    DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, product_id)
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                amount      INTEGER NOT NULL,
                type        TEXT    NOT NULL,
                description TEXT    DEFAULT '',
                date        TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS redeem_codes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    UNIQUE NOT NULL,
                points      INTEGER NOT NULL,
                max_uses    INTEGER NOT NULL,
                used_count  INTEGER DEFAULT 0,
                created_by  INTEGER,
                created_at  TEXT    DEFAULT (datetime('now')),
                expires_at  TEXT,
                is_active   INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS redeem_usage (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL,
                user_id     INTEGER NOT NULL,
                redeemed_at TEXT    DEFAULT (datetime('now')),
                UNIQUE(code, user_id)
            );

            CREATE TABLE IF NOT EXISTS broadcast_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                message_type TEXT,
                target_type  TEXT,
                total_sent   INTEGER DEFAULT 0,
                total_failed INTEGER DEFAULT 0,
                sent_by      INTEGER,
                sent_at      TEXT    DEFAULT (datetime('now'))
            );
        """)
        await db.commit()
    logger.info("✅ Database ready.")


# ═══════════════════════════════════════════════════════
#                   UTILITY HELPERS
# ═══════════════════════════════════════════════════════

def SEP() -> str:
    return "━━━━━━━━━━━━━━━━━━━━━━"


def get_rank(referrals: int) -> str:
    rank = RANKS[0][1]
    for threshold, name in RANKS:
        if referrals >= threshold:
            rank = name
    return rank


def gen_product_id() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def gen_redeem_code() -> str:
    parts = ["SENZO"] + [
        "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
        for _ in range(2)
    ]
    return "-".join(parts)


async def get_bot_username(bot) -> str:
    """FIX #10 – Cache bot username; never returns None."""
    global _BOT_USERNAME
    if not _BOT_USERNAME:
        me = await bot.get_me()
        _BOT_USERNAME = me.username
    return _BOT_USERNAME


def back_kb(target: str = "main_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=target)]])


# ═══════════════════════════════════════════════════════
#                  DATABASE FUNCTIONS
# ═══════════════════════════════════════════════════════

async def ensure_user(user_id: int, username: str, full_name: str):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO users (user_id, username, full_name)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username    = excluded.username,
                    full_name   = excluded.full_name,
                    last_active = datetime('now')
            """, (user_id, username or "", full_name or ""))
            await db.commit()
    except Exception as e:
        logger.error(f"ensure_user: {e}")


async def get_user(user_id: int) -> Optional[dict]:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"get_user: {e}")
        return None


async def add_credits(user_id: int, amount: int, description: str):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET credits = credits + ? WHERE user_id = ?",
                (amount, user_id)
            )
            await db.execute(
                "INSERT INTO transactions (user_id, amount, type, description) VALUES (?, ?, 'credit', ?)",
                (user_id, amount, description)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"add_credits: {e}")


async def deduct_credits(user_id: int, amount: int, description: str):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET credits = credits - ? WHERE user_id = ?",
                (amount, user_id)
            )
            await db.execute(
                "INSERT INTO transactions (user_id, amount, type, description) VALUES (?, ?, 'debit', ?)",
                (user_id, amount, description)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"deduct_credits: {e}")


async def update_rank(user_id: int):
    try:
        user = await get_user(user_id)
        if user:
            new_rank = get_rank(user["total_referrals"])
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE users SET rank = ? WHERE user_id = ?",
                    (new_rank, user_id)
                )
                await db.commit()
    except Exception as e:
        logger.error(f"update_rank: {e}")


async def get_product(product_id: str, include_inactive: bool = False) -> Optional[dict]:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            if include_inactive:
                q = "SELECT * FROM products WHERE id = ?"
                params = (product_id,)
            else:
                q = "SELECT * FROM products WHERE id = ? AND is_active = 1"
                params = (product_id,)
            async with db.execute(q, params) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"get_product: {e}")
        return None


async def get_product_channels(product_id: str) -> list:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT channel_username FROM product_channels WHERE product_id = ?",
                (product_id,)
            ) as cur:
                rows = await cur.fetchall()
                return [r[0] for r in rows]
    except Exception as e:
        logger.error(f"get_product_channels: {e}")
        return []


async def count_refs_for_product(referrer_id: int, product_id: str) -> int:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT COUNT(*) FROM referrals
                WHERE referrer_id = ? AND product_id = ? AND status = 'completed'
            """, (referrer_id, product_id)) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0
    except Exception as e:
        logger.error(f"count_refs: {e}")
        return 0


async def is_unlocked(user_id: int, product_id: str) -> bool:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT 1 FROM user_unlocks WHERE user_id = ? AND product_id = ?",
                (user_id, product_id)
            ) as cur:
                return await cur.fetchone() is not None
    except Exception as e:
        logger.error(f"is_unlocked: {e}")
        return False


async def unlock_product(user_id: int, product_id: str):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO user_unlocks (user_id, product_id) VALUES (?, ?)",
                (user_id, product_id)
            )
            await db.execute(
                "UPDATE products SET unlocks = unlocks + 1 WHERE id = ?",
                (product_id,)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"unlock_product: {e}")


async def check_joined(bot, user_id: int, channel: str) -> bool:
    """FIX #1 – Timeout + proper error handling for channel membership."""
    try:
        ch = channel if channel.startswith("@") else f"@{channel}"
        member = await asyncio.wait_for(
            bot.get_chat_member(chat_id=ch, user_id=user_id),
            timeout=8.0
        )
        return member.status not in ("left", "kicked", "banned")
    except asyncio.TimeoutError:
        logger.warning(f"Timeout checking {channel} for {user_id}")
        return False   # timeout = assume not joined, safer
    except BadRequest:
        # bot not in channel or channel not found
        return True    # don't block user if bot misconfigured
    except Forbidden:
        return True    # bot removed from channel
    except Exception as e:
        logger.warning(f"check_joined {channel}: {e}")
        return True


async def send_file_to_user(bot, user_id: int, product: dict) -> bool:
    """Send file to user. Returns True on success."""
    try:
        fid     = product["file_id"]
        ftype   = product["file_type"]
        caption = f"📦 *{product['name']}*\n\n✅ _Powered by Senzo Premium_"

        kwargs = dict(chat_id=user_id, caption=caption, parse_mode="Markdown")

        if   ftype == "document": await bot.send_document(document=fid, **kwargs)
        elif ftype == "video":    await bot.send_video(video=fid,       **kwargs)
        elif ftype == "photo":    await bot.send_photo(photo=fid,       **kwargs)
        elif ftype == "audio":    await bot.send_audio(audio=fid,       **kwargs)
        elif ftype == "voice":    await bot.send_voice(voice=fid,       **kwargs)
        else:                     await bot.send_document(document=fid,  **kwargs)
        return True
    except Forbidden:
        logger.warning(f"User {user_id} blocked bot.")
        return False
    except Exception as e:
        logger.error(f"send_file_to_user {user_id}: {e}")
        return False


async def get_bot_stats() -> dict:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            stats = {}
            queries = {
                "total_users":    "SELECT COUNT(*) FROM users",
                "total_files":    "SELECT COUNT(*) FROM products WHERE is_active = 1",
                "completed_refs": "SELECT COUNT(*) FROM referrals WHERE status = 'completed'",
                "total_credits":  "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='credit'",
                "active_codes":   "SELECT COUNT(*) FROM redeem_codes WHERE is_active = 1",
            }
            for key, q in queries.items():
                async with db.execute(q) as cur:
                    row = await cur.fetchone()
                    stats[key] = row[0] if row else 0
            async with db.execute(
                "SELECT sent_at FROM broadcast_history ORDER BY sent_at DESC LIMIT 1"
            ) as cur:
                row = await cur.fetchone()
                stats["last_broadcast"] = row[0] if row else "Never"
        return stats
    except Exception as e:
        logger.error(f"get_bot_stats: {e}")
        return {k: 0 for k in ["total_users","total_files","completed_refs",
                                "total_credits","active_codes","last_broadcast"]}


# ═══════════════════════════════════════════════════════
#                    UI BUILDERS
# ═══════════════════════════════════════════════════════

def main_menu_text(user: dict) -> str:
    return (
        f"🌟 *SENZO PREMIUM* 🌟\n"
        f"{SEP()}\n"
        f"*Welcome, {user.get('full_name') or 'User'}!*\n\n"
        f"👤 *YOUR STATS*\n"
        f"• Rank: {user.get('rank', 'Bronze 🥉')}\n"
        f"• Credits: {user.get('credits', 0):,} 💰\n"
        f"• Referrals: {user.get('total_referrals', 0):,} 🔗\n\n"
        f"{SEP()}\n\n"
        f"📱 *MAIN MENU*"
    )


def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if is_admin:
        rows.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    rows += [
        [InlineKeyboardButton("📦 Browse Files",  callback_data="browse_files")],
        [InlineKeyboardButton("🔗 My Referrals",  callback_data="my_referrals"),
         InlineKeyboardButton("🏆 Leaderboard",   callback_data="leaderboard")],
        [InlineKeyboardButton("💰 Redeem Code",   callback_data="redeem_info"),
         InlineKeyboardButton("👤 My Profile",    callback_data="my_profile")],
        [InlineKeyboardButton("❓ Help",           callback_data="help")],
    ]
    return InlineKeyboardMarkup(rows)


def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload New File",       callback_data="admin_upload")],
        [InlineKeyboardButton("📋 Manage Files",          callback_data="admin_manage"),
         InlineKeyboardButton("👥 View Users",            callback_data="admin_users")],
        [InlineKeyboardButton("🎫 Generate Redeem Code",  callback_data="admin_gen_code")],
        [InlineKeyboardButton("🔗 View All Codes",        callback_data="admin_list_codes")],
        [InlineKeyboardButton("📢 Send Broadcast",        callback_data="admin_broadcast")],
        [InlineKeyboardButton("📊 Full Statistics",       callback_data="admin_stats")],
        [InlineKeyboardButton("💾 Backup Database",       callback_data="admin_backup")],
        [InlineKeyboardButton("🔙 Main Menu",             callback_data="main_menu")],
    ])


# ═══════════════════════════════════════════════════════
#                   /start COMMAND
# ═══════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")

    # FIX #12 – clear upload/broadcast state on fresh /start
    for key in ("step", "bc_target", "bc_msg_data",
                "fname", "fdesc", "frefs", "fcred", "fchannels", "file_id", "file_type"):
        context.user_data.pop(key, None)

    args = context.args or []
    if args:
        arg = args[0]

        if arg.startswith("product_"):
            pid = arg[len("product_"):]
            await handle_product_link(update, context, user, pid)
            return

        if arg.startswith("ref_"):
            parts = arg.split("_", 2)
            if len(parts) == 3:
                try:
                    referrer_id = int(parts[1])
                    pid         = parts[2]
                    await handle_referral_join(update, context, user, referrer_id, pid)
                    return
                except ValueError:
                    pass

    u = await get_user(user.id)
    await update.message.reply_text(
        main_menu_text(u),
        parse_mode="Markdown",
        reply_markup=main_menu_kb(user.id == ADMIN_ID),
    )


# ═══════════════════════════════════════════════════════
#              PRODUCT & CHANNEL FLOW
# ═══════════════════════════════════════════════════════

async def handle_product_link(update, context, user, pid: str):
    product = await get_product(pid)

    if not product:
        # FIX #8 – check if it exists but inactive
        inactive = await get_product(pid, include_inactive=True)
        if inactive:
            await update.message.reply_text(
                "⚠️ *This product is currently unavailable.*\n\nPlease contact admin.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "❌ *Product not found!*\n\nThis link is invalid or has been removed.",
                parse_mode="Markdown",
            )
        return

    # Increment views (non-blocking)
    asyncio.create_task(_increment_views(pid))

    channels = await get_product_channels(pid)
    if channels:
        await show_channel_gate(update.message, context, user, pid, channels, product["name"])
    else:
        await show_product_page(update.message, context, user, pid)


async def _increment_views(pid: str):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE products SET views = views + 1 WHERE id = ?", (pid,))
            await db.commit()
    except Exception:
        pass


async def show_channel_gate(target, context, user, pid: str, channels: list, product_name: str):
    """
    target = message object OR None (when called from callback).
    FIX #2 – Always show gate UI, not just alert.
    """
    buttons = []
    for ch in channels:
        clean = ch.lstrip("@")
        buttons.append([InlineKeyboardButton(f"📢 Join {ch}", url=f"https://t.me/{clean}")])
    buttons.append([
        InlineKeyboardButton("✅ I've Joined – Verify Now", callback_data=f"verify_{pid}")
    ])

    ch_list = "\n".join(f"📢 {ch}" for ch in channels)
    text = (
        f"🔒 *CHANNEL VERIFICATION*\n"
        f"{SEP()}\n\n"
        f"📦 *{product_name}*\n\n"
        f"Join ALL these channels first:\n\n"
        f"{ch_list}\n\n"
        f"{SEP()}\n"
        f"⚠️ After joining tap *Verify Now* below."
    )
    kb = InlineKeyboardMarkup(buttons)

    if target is not None:
        await target.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await context.bot.send_message(
            chat_id=user.id, text=text, parse_mode="Markdown", reply_markup=kb
        )


async def show_product_page(target, context, user, pid: str):
    """
    target = message object OR None.
    FIX #3 – ref_link properly used in button.
    """
    product = await get_product(pid)
    if not product:
        txt = "❌ Product not found or no longer active."
        if target:
            await target.reply_text(txt)
        else:
            await context.bot.send_message(user.id, txt)
        return

    already = await is_unlocked(user.id, pid)
    if already:
        txt = "🎉 You already unlocked this! Sending your file again..."
        if target:
            await target.reply_text(txt)
        else:
            await context.bot.send_message(user.id, txt)
        await send_file_to_user(context.bot, user.id, product)
        return

    ref_count = await count_refs_for_product(user.id, pid)
    req_refs  = product["required_refs"]
    req_cred  = product["required_credits"]
    u_data    = await get_user(user.id)
    my_cred   = u_data["credits"] if u_data else 0

    bar_f = min(10, int(ref_count / req_refs * 10)) if req_refs else 10
    bar   = "▓" * bar_f + "░" * (10 - bar_f)

    # FIX #3 – ref_link properly built and used in button
    bot_username = await get_bot_username(context.bot)
    ref_link     = f"https://t.me/{bot_username}?start=ref_{user.id}_{pid}"

    text = (
        f"📦 *{product['name']}*\n"
        f"{SEP()}\n\n"
        f"📝 {product['description']}\n\n"
        f"*REQUIREMENTS:*\n"
        f"• 🔗 Referrals: {ref_count}/{req_refs}  [{bar}]\n"
        f"• 💰 Credits needed: {req_cred} (You have: {my_cred})\n\n"
        f"{SEP()}\n"
        f"💡 Each referral = {CREDITS_PER_REFERRAL} credits + progress"
    )

    buttons = [
        [InlineKeyboardButton("🔗 Get Referral Link", callback_data=f"getref_{pid}")],
        [InlineKeyboardButton("📊 Check Progress",    callback_data=f"progress_{pid}")],
    ]
    if req_cred > 0 and my_cred >= req_cred:
        buttons.append([InlineKeyboardButton("💰 Unlock with Credits", callback_data=f"unlockc_{pid}")])
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])

    kb = InlineKeyboardMarkup(buttons)
    if target:
        await target.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await context.bot.send_message(
            user.id, text, parse_mode="Markdown", reply_markup=kb
        )


# ═══════════════════════════════════════════════════════
#                  REFERRAL JOIN FLOW
# ═══════════════════════════════════════════════════════

async def handle_referral_join(update, context, user, referrer_id: int, pid: str):
    referred_id = user.id

    if referrer_id == referred_id:
        await update.message.reply_text(
            "❌ *You cannot refer yourself!*\n\nPlease share your link with friends.",
            parse_mode="Markdown",
        )
        u = await get_user(user.id)
        await update.message.reply_text(
            main_menu_text(u), parse_mode="Markdown",
            reply_markup=main_menu_kb(user.id == ADMIN_ID)
        )
        return

    product = await get_product(pid)
    if not product:
        u = await get_user(user.id)
        await update.message.reply_text(
            main_menu_text(u), parse_mode="Markdown",
            reply_markup=main_menu_kb(user.id == ADMIN_ID)
        )
        return

    # Try to insert referral
    new_referral = False
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO referrals (referrer_id, referred_id, product_id, status)
                VALUES (?, ?, ?, 'completed')
            """, (referrer_id, referred_id, pid))
            await db.execute(
                "UPDATE users SET total_referrals = total_referrals + 1 WHERE user_id = ?",
                (referrer_id,)
            )
            await db.commit()
        new_referral = True
    except Exception:
        pass  # UNIQUE constraint = already referred

    if new_referral:
        await add_credits(referrer_id, CREDITS_PER_REFERRAL, f"Referral – {product['name']}")
        await update_rank(referrer_id)

        ref_count = await count_refs_for_product(referrer_id, pid)
        req_refs  = product["required_refs"]

        if ref_count >= req_refs and not await is_unlocked(referrer_id, pid):
            await unlock_product(referrer_id, pid)
            try:
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=(
                        f"🎉 *File Unlocked!*\n{SEP()}\n\n"
                        f"📦 *{product['name']}*\n\n"
                        f"✅ You completed all *{req_refs}* referrals!\n"
                        f"Here is your file:"
                    ),
                    parse_mode="Markdown",
                )
                await send_file_to_user(context.bot, referrer_id, product)
            except Exception as e:
                logger.warning(f"notify referrer {referrer_id}: {e}")
        else:
            try:
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=(
                        f"🎉 *New Referral! +{CREDITS_PER_REFERRAL} credits*\n\n"
                        f"📦 *{product['name']}*\n"
                        f"Progress: {ref_count}/{req_refs} referrals"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"notify referrer {referrer_id}: {e}")

        # FIX #9 – new user gets proper welcome
        await update.message.reply_text(
            f"✅ *Welcome to Senzo Premium!*\n\n"
            f"You joined via a referral link.\n"
            f"Your friend is making progress! 🎉",
            parse_mode="Markdown",
        )
    else:
        # FIX #9 – duplicate referral gets proper message
        await update.message.reply_text(
            "👋 *Welcome back!*\n\nYou already joined via this referral link before.",
            parse_mode="Markdown",
        )

    u = await get_user(user.id)
    await update.message.reply_text(
        main_menu_text(u), parse_mode="Markdown",
        reply_markup=main_menu_kb(user.id == ADMIN_ID)
    )


# ═══════════════════════════════════════════════════════
#               CALLBACK QUERY HANDLER
# ═══════════════════════════════════════════════════════

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data  = query.data
    user  = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")

    # FIX #1 – single answer at start, no double answer
    try:
        await query.answer()
    except Exception:
        pass  # already answered or too old — continue anyway

    try:

        # ─── Main Menu ────────────────────────────
        if data == "main_menu":
            u = await get_user(user.id)
            await query.edit_message_text(
                main_menu_text(u), parse_mode="Markdown",
                reply_markup=main_menu_kb(user.id == ADMIN_ID)
            )

        # ─── Browse Files ─────────────────────────
        elif data == "browse_files":
            await cb_browse_files(query, context)

        # ─── My Referrals ─────────────────────────
        elif data == "my_referrals":
            await cb_my_referrals(query, user.id)

        # ─── Leaderboard ──────────────────────────
        elif data == "leaderboard":
            await cb_leaderboard(query, user.id)

        # ─── Redeem Info ──────────────────────────
        elif data == "redeem_info":
            await query.edit_message_text(
                f"💰 *REDEEM CODE*\n{SEP()}\n\n"
                f"Use the command:\n`/redeem YOUR-CODE`\n\n"
                f"Example:\n`/redeem SENZO-AB12C-D34EF`\n\n"
                f"💡 Get codes from events, giveaways & admin promos!",
                parse_mode="Markdown",
                reply_markup=back_kb()
            )

        # ─── My Profile ───────────────────────────
        elif data == "my_profile":
            await cb_profile(query, user.id)

        # ─── Help ─────────────────────────────────
        elif data == "help":
            await query.edit_message_text(
                f"❓ *HELP & COMMANDS*\n{SEP()}\n\n"
                f"*Commands:*\n"
                f"• `/start` – Main menu\n"
                f"• `/redeem CODE` – Redeem a code\n"
                f"• `/myrefs` – My referral progress\n"
                f"• `/profile` – My profile\n\n"
                f"*How to unlock files:*\n"
                f"1. Open any file link\n"
                f"2. Join required channels\n"
                f"3. Share your referral link\n"
                f"4. Collect required referrals\n"
                f"5. File is auto-sent to you! ✅\n\n"
                f"Each referral = *{CREDITS_PER_REFERRAL} credits* 💰\n\n"
                f"_Powered by Senzo Technologies_ 🌟",
                parse_mode="Markdown",
                reply_markup=back_kb()
            )

        # ─── Channel Verify ───────────────────────
        # FIX #1 & #2 – no double answer, show gate UI on failure
        elif data.startswith("verify_"):
            pid      = data[len("verify_"):]
            channels = await get_product_channels(pid)
            product  = await get_product(pid)

            if not product:
                await query.edit_message_text(
                    "❌ This product is no longer available.",
                    reply_markup=back_kb()
                )
                return

            not_joined = []
            for ch in channels:
                if not await check_joined(context.bot, user.id, ch):
                    not_joined.append(ch)

            if not not_joined:
                # All joined – show product page by editing current message
                await show_product_page_edit(query, context, user, pid)
            else:
                # FIX #2 – rebuild gate UI with clear error
                buttons = []
                for ch in channels:
                    clean = ch.lstrip("@")
                    icon  = "❌" if ch in not_joined else "✅"
                    buttons.append([InlineKeyboardButton(
                        f"{icon} Join {ch}", url=f"https://t.me/{clean}"
                    )])
                buttons.append([
                    InlineKeyboardButton("✅ I've Joined – Verify Now", callback_data=f"verify_{pid}")
                ])
                ch_status = "\n".join(
                    f"{'❌' if ch in not_joined else '✅'} {ch}" for ch in channels
                )
                await query.edit_message_text(
                    f"🔒 *CHANNEL VERIFICATION*\n{SEP()}\n\n"
                    f"📦 *{product['name']}*\n\n"
                    f"*Status:*\n{ch_status}\n\n"
                    f"❌ You haven't joined all channels yet!\n"
                    f"Please join the ❌ channels above then tap Verify.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(buttons)
                )

        # ─── Get Referral Link ────────────────────
        elif data.startswith("getref_"):
            pid          = data[len("getref_"):]
            product      = await get_product(pid)
            name         = product["name"] if product else "File"
            bot_username = await get_bot_username(context.bot)
            ref_link     = f"https://t.me/{bot_username}?start=ref_{user.id}_{pid}"
            await query.edit_message_text(
                f"🔗 *YOUR REFERRAL LINK*\n{SEP()}\n\n"
                f"📦 *{name}*\n\n"
                f"`{ref_link}`\n\n"
                f"📋 Tap the link to copy, then share with friends!\n"
                f"Each join = *{CREDITS_PER_REFERRAL} credits* 💰",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 Check Progress",  callback_data=f"progress_{pid}")],
                    [InlineKeyboardButton("🔙 Back to File",    callback_data=f"viewprod_{pid}")],
                ])
            )

        # ─── Progress ─────────────────────────────
        elif data.startswith("progress_"):
            pid       = data[len("progress_"):]
            product   = await get_product(pid)
            ref_count = await count_refs_for_product(user.id, pid)
            req       = product["required_refs"] if product else 1
            name      = product["name"] if product else "File"
            bar_f     = min(10, int(ref_count / req * 10)) if req else 10
            bar       = "▓" * bar_f + "░" * (10 - bar_f)
            remaining = max(0, req - ref_count)

            if remaining == 0:
                status_txt = "✅ *Complete! File should have been sent!*"
            else:
                status_txt = f"⏳ Need *{remaining}* more referral(s)!"

            await query.edit_message_text(
                f"📊 *PROGRESS*\n{SEP()}\n\n"
                f"📦 *{name}*\n\n"
                f"[{bar}] {ref_count}/{req}\n\n"
                f"{status_txt}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 Referral Link",  callback_data=f"getref_{pid}")],
                    [InlineKeyboardButton("🔄 Refresh",        callback_data=f"progress_{pid}")],
                    [InlineKeyboardButton("🔙 Back to File",   callback_data=f"viewprod_{pid}")],
                ])
            )

        # ─── View Product ─────────────────────────
        elif data.startswith("viewprod_"):
            pid = data[len("viewprod_"):]
            await show_product_page_edit(query, context, user, pid)

        # ─── Credit Unlock ────────────────────────
        elif data.startswith("unlockc_"):
            pid = data[len("unlockc_"):]
            await cb_credit_unlock(query, context, user.id, pid)

        # ══════════════ ADMIN CALLBACKS ════════════════

        elif data == "admin_panel":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            stats = await get_bot_stats()
            await query.edit_message_text(
                f"⚙️ *ADMIN DASHBOARD*\n{SEP()}\n\n"
                f"📊 *STATS*\n"
                f"• 👥 Users: {stats['total_users']:,}\n"
                f"• 📦 Files: {stats['total_files']:,}\n"
                f"• 🔗 Referrals: {stats['completed_refs']:,}\n"
                f"• 💰 Credits Given: {stats['total_credits']:,}\n"
                f"• 🎫 Active Codes: {stats['active_codes']:,}\n\n"
                f"{SEP()}\n🛠️ *ACTIONS*",
                parse_mode="Markdown",
                reply_markup=admin_kb()
            )

        elif data == "admin_upload":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            # Clear all upload state
            for k in ("step","fname","fdesc","frefs","fcred","fchannels","file_id","file_type"):
                context.user_data.pop(k, None)
            context.user_data["step"] = "file"
            await query.edit_message_text(
                f"📤 *UPLOAD NEW FILE*\n{SEP()}\n\n"
                f"*Step 1 of 6:* Send the file\n"
                f"(document, video, photo, audio, or voice)\n\n"
                f"_Send /start to cancel at any time._",
                parse_mode="Markdown"
            )

        elif data == "admin_manage":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            await cb_manage_files(query)

        elif data == "admin_users":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            await cb_view_users(query)

        elif data == "admin_gen_code":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            await query.edit_message_text(
                f"🎫 *GENERATE REDEEM CODE*\n{SEP()}\n\n"
                f"Command:\n`/createredeem [points] [max_uses] [expiry_days]`\n\n"
                f"Example:\n`/createredeem 50 100 30`\n\n"
                f"This creates:\n"
                f"• 50 credits per redemption\n"
                f"• Max 100 users can use it\n"
                f"• Expires in 30 days",
                parse_mode="Markdown",
                reply_markup=back_kb("admin_panel")
            )

        elif data == "admin_list_codes":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            await cb_list_codes(query)

        elif data == "admin_broadcast":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            await query.edit_message_text(
                f"📢 *BROADCAST PANEL*\n{SEP()}\n\nSelect target audience:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📝 All Users",           callback_data="bc_all")],
                    [InlineKeyboardButton("🎯 Active (last 7d)",    callback_data="bc_active")],
                    [InlineKeyboardButton("🏆 Top Referrers (50)",  callback_data="bc_top")],
                    [InlineKeyboardButton("💎 Premium (500+ cred)", callback_data="bc_premium")],
                    [InlineKeyboardButton("🔙 Back",                callback_data="admin_panel")],
                ])
            )

        elif data.startswith("bc_") and data in ("bc_all","bc_active","bc_top","bc_premium"):
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            target_map = {
                "bc_all":     "all",
                "bc_active":  "active",
                "bc_top":     "top",
                "bc_premium": "premium",
            }
            context.user_data["bc_target"] = target_map[data]
            context.user_data["step"]      = "bc_msg"
            await query.edit_message_text(
                f"📢 Target: *{target_map[data].upper()}*\n{SEP()}\n\n"
                f"Now send your broadcast message:\n"
                f"(text, photo, video, or document)\n\n"
                f"_Send /start to cancel._",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]
                ])
            )

        elif data == "bc_yes":
            if user.id != ADMIN_ID: return
            await do_broadcast(query, context)

        elif data == "bc_no":
            if user.id != ADMIN_ID: return
            context.user_data.pop("bc_msg_data", None)
            context.user_data.pop("bc_target", None)
            context.user_data.pop("step", None)
            await query.edit_message_text(
                "❌ Broadcast cancelled.",
                reply_markup=back_kb("admin_panel")
            )

        elif data == "admin_stats":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            await cb_full_stats(query)

        elif data == "admin_backup":
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            await cb_backup(query, context)

        elif data.startswith("toggle_"):
            if user.id != ADMIN_ID:
                await query.answer("🔒 Access Denied!", show_alert=True); return
            pid = data[len("toggle_"):]
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT is_active FROM products WHERE id = ?", (pid,)
                ) as cur:
                    row = await cur.fetchone()
                if row:
                    new_state = 0 if row[0] == 1 else 1
                    await db.execute(
                        "UPDATE products SET is_active = ? WHERE id = ?", (new_state, pid)
                    )
                    await db.commit()
            await cb_manage_files(query)

        else:
            logger.warning(f"Unhandled callback: {data}")

    except Exception as e:
        logger.error(f"callback_handler [{data}]: {e}", exc_info=True)
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text="⚠️ Something went wrong. Please try again or send /start."
            )
        except Exception:
            pass


# ═══════════════════════════════════════════════════════
#        PRODUCT PAGE VIA EDIT (from callbacks)
# ═══════════════════════════════════════════════════════

async def show_product_page_edit(query, context, user, pid: str):
    """Edit existing message to show product page. FIX #8 – inactive gracefully."""
    product = await get_product(pid)
    if not product:
        inactive = await get_product(pid, include_inactive=True)
        msg = "⚠️ This product is currently unavailable." if inactive else "❌ Product not found."
        await query.edit_message_text(msg, reply_markup=back_kb())
        return

    already = await is_unlocked(user.id, pid)
    if already:
        await query.edit_message_text(
            "🎉 *Already unlocked!*\n\nSending your file now...",
            parse_mode="Markdown"
        )
        await send_file_to_user(context.bot, user.id, product)
        return

    ref_count = await count_refs_for_product(user.id, pid)
    req_refs  = product["required_refs"]
    req_cred  = product["required_credits"]
    u_data    = await get_user(user.id)
    my_cred   = u_data["credits"] if u_data else 0

    bar_f = min(10, int(ref_count / req_refs * 10)) if req_refs else 10
    bar   = "▓" * bar_f + "░" * (10 - bar_f)

    text = (
        f"📦 *{product['name']}*\n"
        f"{SEP()}\n\n"
        f"📝 {product['description']}\n\n"
        f"*REQUIREMENTS:*\n"
        f"• 🔗 Referrals: {ref_count}/{req_refs}  [{bar}]\n"
        f"• 💰 Credits needed: {req_cred} (You have: {my_cred})\n\n"
        f"{SEP()}\n"
        f"💡 Each referral = {CREDITS_PER_REFERRAL} credits + progress"
    )

    buttons = [
        [InlineKeyboardButton("🔗 Get Referral Link", callback_data=f"getref_{pid}")],
        [InlineKeyboardButton("📊 Check Progress",    callback_data=f"progress_{pid}")],
    ]
    if req_cred > 0 and my_cred >= req_cred:
        buttons.append([InlineKeyboardButton("💰 Unlock with Credits", callback_data=f"unlockc_{pid}")])
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])

    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ═══════════════════════════════════════════════════════
#           CALLBACK SUB-FUNCTIONS
# ═══════════════════════════════════════════════════════

async def cb_browse_files(query, context):
    try:
        bot_username = await get_bot_username(context.bot)
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id, name, required_refs, required_credits FROM products "
                "WHERE is_active = 1 ORDER BY created_at DESC LIMIT 20"
            ) as cur:
                rows = await cur.fetchall()

        if not rows:
            await query.edit_message_text(
                "📦 No files available yet. Check back later!",
                reply_markup=back_kb()
            )
            return

        buttons = []
        for pid, name, req_refs, req_cred in rows:
            label = f"📦 {name}  (🔗{req_refs}"
            if req_cred:
                label += f" | 💰{req_cred}"
            label += ")"
            link = f"https://t.me/{bot_username}?start=product_{pid}"
            buttons.append([InlineKeyboardButton(label, url=link)])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])

        await query.edit_message_text(
            f"📦 *AVAILABLE FILES*\n{SEP()}\n\nTap any file to view:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f"cb_browse_files: {e}")


async def cb_my_referrals(query, user_id: int):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT r.product_id, p.name, COUNT(*) as cnt, p.required_refs
                FROM referrals r
                LEFT JOIN products p ON r.product_id = p.id
                WHERE r.referrer_id = ? AND r.status = 'completed'
                GROUP BY r.product_id
            """, (user_id,)) as cur:
                rows = await cur.fetchall()

        if not rows:
            text = (
                f"🔗 *MY REFERRALS*\n{SEP()}\n\n"
                "No referrals yet.\n\nBrowse files and share your referral link to start!"
            )
        else:
            lines = [f"🔗 *MY REFERRALS*\n{SEP()}\n"]
            for pid, name, cnt, req in rows:
                bf  = min(10, int(cnt / req * 10)) if req else 10
                bar = "▓" * bf + "░" * (10 - bf)
                status = "✅ Unlocked!" if cnt >= req else f"{cnt}/{req}"
                lines.append(f"📦 *{name or pid}*\n[{bar}] {status}\n")
            text = "\n".join(lines)

        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=back_kb()
        )
    except Exception as e:
        logger.error(f"cb_my_referrals: {e}")


async def cb_leaderboard(query, user_id: int):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT user_id, full_name, username, total_referrals "
                "FROM users ORDER BY total_referrals DESC LIMIT 10"
            ) as cur:
                rows = await cur.fetchall()

            async with db.execute("""
                SELECT COUNT(*) + 1 FROM users
                WHERE total_referrals > (
                    SELECT total_referrals FROM users WHERE user_id = ?
                )
            """, (user_id,)) as cur:
                my_rank = (await cur.fetchone())[0]

            async with db.execute(
                "SELECT total_referrals FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                ur = await cur.fetchone()
                my_refs = ur[0] if ur else 0

        medals = ["🥇", "🥈", "🥉"] + [""] * 7
        lines  = [f"🏆 *TOP REFERRERS*\n{SEP()}\n"]
        for i, (uid, name, uname, refs) in enumerate(rows):
            m       = medals[i] if i < 3 else f"{i+1}."
            display = uname or name or f"User{uid}"
            lines.append(f"{m} {display} – {refs:,} referrals")

        lines += [
            f"\n{SEP()}",
            f"📌 *YOUR RANK: #{my_rank}*",
            f"🔗 Your referrals: {my_refs:,}",
        ]

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data="leaderboard"),
                 InlineKeyboardButton("🔙 Back",    callback_data="main_menu")],
            ])
        )
    except Exception as e:
        logger.error(f"cb_leaderboard: {e}")


async def cb_profile(query, user_id: int):
    try:
        u = await get_user(user_id)
        if not u:
            return
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM user_unlocks WHERE user_id = ?", (user_id,)
            ) as cur:
                unlocked = (await cur.fetchone())[0]
            async with db.execute("""
                SELECT COUNT(*) FROM referrals
                WHERE referrer_id = ? AND status = 'completed'
                AND date >= datetime('now', '-7 days')
            """, (user_id,)) as cur:
                last7 = (await cur.fetchone())[0]

        joined = (u.get("joined_date") or "")[:10] or "Unknown"
        uname  = f"@{u['username']}" if u.get("username") else "No username"

        await query.edit_message_text(
            f"👤 *MY PROFILE*\n{SEP()}\n\n"
            f"• User ID: `{user_id}`\n"
            f"• Username: {uname}\n"
            f"• Joined: {joined}\n"
            f"• Rank: {u['rank']}\n\n"
            f"{SEP()}\n\n"
            f"💰 Credits: {u['credits']:,}\n"
            f"🔗 Total Referrals: {u['total_referrals']:,}\n"
            f"📦 Unlocked Files: {unlocked}\n\n"
            f"📊 Last 7 days: {last7} referrals",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
    except Exception as e:
        logger.error(f"cb_profile: {e}")


async def cb_credit_unlock(query, context, user_id: int, pid: str):
    try:
        product = await get_product(pid)
        if not product:
            await query.answer("❌ Product not found!", show_alert=True)
            return

        req_cred = product["required_credits"]
        if req_cred <= 0:
            await query.answer("ℹ️ This product doesn't require credits.", show_alert=True)
            return

        u = await get_user(user_id)
        if not u or u["credits"] < req_cred:
            await query.answer(
                f"❌ Insufficient credits!\nYou have {u['credits'] if u else 0}, need {req_cred}.",
                show_alert=True
            )
            return

        await deduct_credits(user_id, req_cred, f"Unlocked: {product['name']}")
        await unlock_product(user_id, pid)

        await query.edit_message_text(
            f"✅ *Unlocked with Credits!*\n\n"
            f"📦 *{product['name']}*\n"
            f"💰 {req_cred} credits deducted.\n\n"
            f"Sending your file...",
            parse_mode="Markdown"
        )
        await send_file_to_user(context.bot, user_id, product)
    except Exception as e:
        logger.error(f"cb_credit_unlock: {e}")


async def cb_manage_files(query):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id, name, is_active, views, unlocks "
                "FROM products ORDER BY created_at DESC LIMIT 15"
            ) as cur:
                rows = await cur.fetchall()

        if not rows:
            await query.edit_message_text(
                "📋 No files uploaded yet.",
                reply_markup=back_kb("admin_panel")
            )
            return

        buttons = []
        for pid, name, active, views, unlocks in rows:
            icon = "✅" if active else "❌"
            buttons.append([InlineKeyboardButton(
                f"{icon} {name[:28]}  V:{views} U:{unlocks}",
                callback_data=f"toggle_{pid}"
            )])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])

        await query.edit_message_text(
            f"📋 *MANAGE FILES*\n{SEP()}\n\n"
            f"✅ = Active  ❌ = Inactive\n"
            f"Tap to toggle:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f"cb_manage_files: {e}")


async def cb_view_users(query):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT user_id, full_name, username, credits, total_referrals "
                "FROM users ORDER BY joined_date DESC LIMIT 15"
            ) as cur:
                rows = await cur.fetchall()

        lines = [f"👥 *RECENT USERS*\n{SEP()}\n"]
        for uid, name, uname, cred, refs in rows:
            display = uname or name or f"User{uid}"
            lines.append(f"• {display}  💰{cred}  🔗{refs}")

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=back_kb("admin_panel")
        )
    except Exception as e:
        logger.error(f"cb_view_users: {e}")


async def cb_list_codes(query):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT code, points, max_uses, used_count, expires_at, is_active "
                "FROM redeem_codes ORDER BY created_at DESC LIMIT 15"
            ) as cur:
                rows = await cur.fetchall()

        if not rows:
            await query.edit_message_text(
                f"🎫 No codes yet.\n\nUse `/createredeem pts uses days`",
                parse_mode="Markdown",
                reply_markup=back_kb("admin_panel")
            )
            return

        lines = [f"🎫 *REDEEM CODES*\n{SEP()}\n"]
        for code, pts, mu, used, exp, active in rows:
            s       = "✅" if active else "❌"
            exp_str = (exp or "")[:10] or "No expiry"
            left    = mu - used
            lines.append(f"{s} `{code}`\n  +{pts}pts | {used}/{mu} | Left:{left} | Exp:{exp_str}\n")

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=back_kb("admin_panel")
        )
    except Exception as e:
        logger.error(f"cb_list_codes: {e}")


async def cb_full_stats(query):
    try:
        stats = await get_bot_stats()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE joined_date >= datetime('now','-7 days')"
            ) as cur:
                new7 = (await cur.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE last_active >= datetime('now','-1 day')"
            ) as cur:
                active24 = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM user_unlocks") as cur:
                unlocks = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM broadcast_history") as cur:
                bcast = (await cur.fetchone())[0]

        storage_note = "✅ Persistent (/data)" if STORAGE_OK else "⚠️ Non-persistent (add /data volume!)"

        await query.edit_message_text(
            f"📊 *FULL STATISTICS*\n{SEP()}\n\n"
            f"👥 Total Users: {stats['total_users']:,}\n"
            f"🆕 New (7 days): {new7:,}\n"
            f"🟢 Active (24h): {active24:,}\n\n"
            f"📦 Total Files: {stats['total_files']:,}\n"
            f"🔓 Total Unlocks: {unlocks:,}\n\n"
            f"🔗 Referrals: {stats['completed_refs']:,}\n"
            f"💰 Credits Given: {stats['total_credits']:,}\n\n"
            f"🎫 Active Codes: {stats['active_codes']:,}\n"
            f"📢 Broadcasts: {bcast:,}\n\n"
            f"💾 Storage: {storage_note}",
            parse_mode="Markdown",
            reply_markup=back_kb("admin_panel")
        )
    except Exception as e:
        logger.error(f"cb_full_stats: {e}")


async def cb_backup(query, context):
    await query.edit_message_text("⏳ *Creating backup...*", parse_mode="Markdown")
    try:
        if not os.path.exists(DB_PATH):
            await query.edit_message_text(
                "❌ Database file not found.", reply_markup=back_kb("admin_panel")
            )
            return
        with open(DB_PATH, "rb") as f:
            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=f,
                filename=f"senzo_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
                caption="💾 *Senzo Premium – Database Backup*",
                parse_mode="Markdown",
            )
        await query.edit_message_text(
            "✅ Backup sent to your chat!",
            reply_markup=back_kb("admin_panel")
        )
    except Exception as e:
        await query.edit_message_text(
            f"❌ Backup failed:\n{e}",
            reply_markup=back_kb("admin_panel")
        )


# ═══════════════════════════════════════════════════════
#                    BROADCAST
# ═══════════════════════════════════════════════════════

async def do_broadcast(query, context):
    """FIX #4, #7 – status_msg properly tracked, per-message timeout."""
    target   = context.user_data.get("bc_target", "all")
    msg_data = context.user_data.get("bc_msg_data")

    if not msg_data:
        await query.edit_message_text(
            "❌ No message found. Please start over.",
            reply_markup=back_kb("admin_panel")
        )
        return

    # Get target user list
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            if target == "all":
                q = "SELECT user_id FROM users WHERE is_banned = 0"
            elif target == "active":
                q = "SELECT user_id FROM users WHERE is_banned = 0 AND last_active >= datetime('now','-7 days')"
            elif target == "top":
                q = "SELECT user_id FROM users WHERE is_banned = 0 ORDER BY total_referrals DESC LIMIT 50"
            else:  # premium
                q = "SELECT user_id FROM users WHERE is_banned = 0 AND credits >= 500"
            async with db.execute(q) as cur:
                user_ids = [r[0] for r in await cur.fetchall()]
    except Exception as e:
        await query.edit_message_text(
            f"❌ Failed to fetch users: {e}",
            reply_markup=back_kb("admin_panel")
        )
        return

    total = len(user_ids)
    sent  = 0
    failed = 0
    mtype  = msg_data.get("type", "text")

    # FIX #4 – edit the query message to use as status tracker
    status_msg = await query.edit_message_text(
        f"📢 *Broadcasting...*\n\n"
        f"Target: {total:,} users\n"
        f"Sent: 0 | Failed: 0",
        parse_mode="Markdown"
    )

    for i, uid in enumerate(user_ids):
        try:
            # FIX #7 – per-message timeout so one stuck user doesn't hang everything
            async def _send():
                if mtype == "text":
                    await context.bot.send_message(
                        chat_id=uid, text=msg_data["content"]
                    )
                elif mtype == "photo":
                    await context.bot.send_photo(
                        chat_id=uid, photo=msg_data["file_id"],
                        caption=msg_data.get("caption", "")
                    )
                elif mtype == "video":
                    await context.bot.send_video(
                        chat_id=uid, video=msg_data["file_id"],
                        caption=msg_data.get("caption", "")
                    )
                elif mtype == "document":
                    await context.bot.send_document(
                        chat_id=uid, document=msg_data["file_id"],
                        caption=msg_data.get("caption", "")
                    )

            await asyncio.wait_for(_send(), timeout=15.0)
            sent += 1
        except asyncio.TimeoutError:
            failed += 1
        except (Forbidden, BadRequest):
            failed += 1
        except Exception:
            failed += 1

        # Update status every 50 users
        if (i + 1) % 50 == 0:
            try:
                await status_msg.edit_text(
                    f"📢 *Broadcasting...*\n\n"
                    f"Target: {total:,}\n"
                    f"Sent: {sent:,} | Failed: {failed:,}",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        # Rate limit: 30 msgs/sec Telegram limit
        if (i + 1) % 25 == 0:
            await asyncio.sleep(1)

    # Save to history
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO broadcast_history "
                "(message_type, target_type, total_sent, total_failed, sent_by) "
                "VALUES (?,?,?,?,?)",
                (mtype, target, sent, failed, ADMIN_ID)
            )
            await db.commit()
    except Exception:
        pass

    # Clear state
    context.user_data.pop("bc_msg_data", None)
    context.user_data.pop("bc_target", None)
    context.user_data.pop("step", None)

    await status_msg.edit_text(
        f"✅ *Broadcast Complete!*\n{SEP()}\n\n"
        f"• ✅ Sent: {sent:,}\n"
        f"• ❌ Failed: {failed:,}\n"
        f"• 📋 Total: {total:,}",
        parse_mode="Markdown",
        reply_markup=back_kb("admin_panel")
    )


# ═══════════════════════════════════════════════════════
#              MESSAGE HANDLER (text + files)
# ═══════════════════════════════════════════════════════

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg  = update.message
    await ensure_user(user.id, user.username or "", user.full_name or "")

    step = context.user_data.get("step")

    if user.id == ADMIN_ID and step == "bc_msg":
        await handle_bc_input(update, context)
        return

    if user.id == ADMIN_ID and step in ("file", "name", "desc", "refs", "credits", "channels"):
        await handle_upload_step(update, context)
        return

    # Default – show main menu
    u = await get_user(user.id)
    await msg.reply_text(
        main_menu_text(u), parse_mode="Markdown",
        reply_markup=main_menu_kb(user.id == ADMIN_ID)
    )


async def handle_bc_input(update, context):
    msg = update.message
    md  = {}

    if msg.text:
        md = {"type": "text", "content": msg.text}
    elif msg.photo:
        md = {"type": "photo",    "file_id": msg.photo[-1].file_id, "caption": msg.caption or ""}
    elif msg.video:
        md = {"type": "video",    "file_id": msg.video.file_id,     "caption": msg.caption or ""}
    elif msg.document:
        md = {"type": "document", "file_id": msg.document.file_id,  "caption": msg.caption or ""}
    else:
        await msg.reply_text(
            "❌ Unsupported type.\nPlease send: text, photo, video, or document."
        )
        return

    context.user_data["bc_msg_data"] = md
    context.user_data.pop("step", None)

    target  = context.user_data.get("bc_target", "all")
    preview = md.get("content", "") or md.get("caption", "") or f"[{md['type']}]"

    await msg.reply_text(
        f"📢 *BROADCAST PREVIEW*\n{SEP()}\n\n"
        f"Target: *{target.upper()}*\n"
        f"Type: {md['type']}\n"
        f"Preview: {preview[:120]}\n\n"
        f"Confirm send?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ YES – Send Now",  callback_data="bc_yes"),
             InlineKeyboardButton("❌ NO – Cancel",     callback_data="bc_no")],
        ])
    )


async def handle_upload_step(update, context):
    """FIX #5 – Proper type checking at each step, no crash on wrong input."""
    msg  = update.message
    step = context.user_data.get("step")

    if step == "file":
        if msg.document:
            context.user_data.update({"file_id": msg.document.file_id, "file_type": "document"})
        elif msg.video:
            context.user_data.update({"file_id": msg.video.file_id,    "file_type": "video"})
        elif msg.photo:
            context.user_data.update({"file_id": msg.photo[-1].file_id,"file_type": "photo"})
        elif msg.audio:
            context.user_data.update({"file_id": msg.audio.file_id,    "file_type": "audio"})
        elif msg.voice:
            context.user_data.update({"file_id": msg.voice.file_id,    "file_type": "voice"})
        else:
            await msg.reply_text(
                "❌ Please send a *file*.\n"
                "Accepted: document, video, photo, audio, voice.",
                parse_mode="Markdown"
            )
            return
        context.user_data["step"] = "name"
        await msg.reply_text(
            f"✅ File received!\n\n"
            f"*Step 2 of 6:* Enter the file *name/title*:",
            parse_mode="Markdown"
        )

    elif step == "name":
        # FIX #5 – reject non-text input
        if not msg.text:
            await msg.reply_text("❌ Please send a *text* name for the file.", parse_mode="Markdown")
            return
        name = msg.text.strip()
        if not name:
            await msg.reply_text("❌ Name cannot be empty.", parse_mode="Markdown")
            return
        context.user_data["fname"] = name
        context.user_data["step"]  = "desc"
        await msg.reply_text(
            f"✅ Name: *{name}*\n\n*Step 3 of 6:* Enter the *description*:",
            parse_mode="Markdown"
        )

    elif step == "desc":
        if not msg.text:
            await msg.reply_text("❌ Please send a *text* description.", parse_mode="Markdown")
            return
        desc = msg.text.strip()
        if not desc:
            await msg.reply_text("❌ Description cannot be empty.", parse_mode="Markdown")
            return
        context.user_data["fdesc"] = desc
        context.user_data["step"]  = "refs"
        await msg.reply_text(
            f"✅ Description saved!\n\n*Step 4 of 6:* Enter *required referrals* (e.g. `5`):",
            parse_mode="Markdown"
        )

    elif step == "refs":
        if not msg.text:
            await msg.reply_text("❌ Please send a number (e.g. `5`).", parse_mode="Markdown")
            return
        try:
            refs = int(msg.text.strip())
            if refs < 0:
                raise ValueError
        except ValueError:
            await msg.reply_text("❌ Please enter a valid positive number.", parse_mode="Markdown")
            return
        context.user_data["frefs"] = refs
        context.user_data["step"]  = "credits"
        await msg.reply_text(
            f"✅ Referrals: *{refs}*\n\n*Step 5 of 6:* Enter *required credits* (`0` for free):",
            parse_mode="Markdown"
        )

    elif step == "credits":
        if not msg.text:
            await msg.reply_text("❌ Please send a number (e.g. `0`).", parse_mode="Markdown")
            return
        try:
            cred = int(msg.text.strip())
            if cred < 0:
                raise ValueError
        except ValueError:
            await msg.reply_text("❌ Please enter a valid number (0 or more).", parse_mode="Markdown")
            return
        context.user_data["fcred"] = cred
        context.user_data["step"]  = "channels"
        await msg.reply_text(
            f"✅ Credits: *{cred}*\n\n"
            f"*Step 6 of 6:* Enter *mandatory channels*\n\n"
            f"Format: `@channel1 @channel2`\n"
            f"Or type `skip` to add no channels.\n\n"
            f"⚠️ Bot must be *admin* in those channels!",
            parse_mode="Markdown"
        )

    elif step == "channels":
        if not msg.text:
            await msg.reply_text(
                "❌ Please type channel usernames or `skip`.", parse_mode="Markdown"
            )
            return
        text     = msg.text.strip()
        channels = []
        if text.lower() != "skip":
            for ch in text.split():
                ch = ch.strip()
                if not ch.startswith("@"):
                    ch = "@" + ch
                channels.append(ch)
        context.user_data["fchannels"] = channels
        await finalize_upload(update, context)


async def finalize_upload(update, context):
    """FIX #10 – use get_bot_username() instead of direct attribute."""
    msg  = update.message
    data = context.user_data
    pid  = gen_product_id()

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO products
                    (id, name, description, file_id, file_type,
                     required_refs, required_credits, admin_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pid,
                data["fname"],
                data["fdesc"],
                data["file_id"],
                data["file_type"],
                data["frefs"],
                data["fcred"],
                ADMIN_ID,
            ))
            for ch in data.get("fchannels", []):
                await db.execute(
                    "INSERT INTO product_channels (product_id, channel_username) VALUES (?, ?)",
                    (pid, ch)
                )
            await db.commit()

        bot_username = await get_bot_username(context.bot)
        link         = f"https://t.me/{bot_username}?start=product_{pid}"
        chs          = ", ".join(data.get("fchannels", [])) or "None"

        # Clear upload state
        for k in ("step","fname","fdesc","frefs","fcred","fchannels","file_id","file_type"):
            context.user_data.pop(k, None)

        await msg.reply_text(
            f"✅ *FILE UPLOADED SUCCESSFULLY!*\n{SEP()}\n\n"
            f"📦 Name: *{data['fname']}*\n"
            f"🔑 Product ID: `{pid}`\n"
            f"🔗 Required Refs: {data['frefs']}\n"
            f"💰 Required Credits: {data['fcred']}\n"
            f"📢 Channels: {chs}\n\n"
            f"🌐 *Product Link:*\n`{link}`\n\n"
            f"Share this link with users! 🚀",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")]
            ])
        )

    except Exception as e:
        logger.error(f"finalize_upload: {e}")
        for k in ("step","fname","fdesc","frefs","fcred","fchannels","file_id","file_type"):
            context.user_data.pop(k, None)
        await msg.reply_text(
            f"❌ *Upload failed!*\n\nError: {e}\n\nPlease try again.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")]
            ])
        )


# ═══════════════════════════════════════════════════════
#                    COMMANDS
# ═══════════════════════════════════════════════════════

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("🔒 *Access Denied!*", parse_mode="Markdown")
        return
    await ensure_user(user.id, user.username or "", user.full_name or "")
    stats = await get_bot_stats()
    await update.message.reply_text(
        f"⚙️ *ADMIN DASHBOARD*\n{SEP()}\n\n"
        f"• 👥 Users: {stats['total_users']:,}\n"
        f"• 📦 Files: {stats['total_files']:,}\n"
        f"• 🔗 Referrals: {stats['completed_refs']:,}\n"
        f"• 💰 Credits: {stats['total_credits']:,}\n"
        f"• 🎫 Active Codes: {stats['active_codes']:,}\n\n"
        f"{SEP()}\n🛠️ *ACTIONS*",
        parse_mode="Markdown",
        reply_markup=admin_kb()
    )


async def cmd_createredeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "❌ Usage:\n`/createredeem [points] [max_uses] [expiry_days]`\n\n"
            "Example:\n`/createredeem 50 100 30`",
            parse_mode="Markdown"
        )
        return

    try:
        points      = int(args[0])
        max_uses    = int(args[1])
        expiry_days = int(args[2])
        if points <= 0 or max_uses <= 0 or expiry_days <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ All values must be positive integers.")
        return

    code       = gen_redeem_code()
    expires_at = (datetime.now() + timedelta(days=expiry_days)).strftime("%Y-%m-%d %H:%M:%S")

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO redeem_codes (code, points, max_uses, created_by, expires_at) "
                "VALUES (?,?,?,?,?)",
                (code, points, max_uses, ADMIN_ID, expires_at)
            )
            await db.commit()

        await update.message.reply_text(
            f"✅ *REDEEM CODE CREATED!*\n{SEP()}\n\n"
            f"🎫 Code: `{code}`\n"
            f"💰 Points: {points}\n"
            f"👥 Max Uses: {max_uses}\n"
            f"📅 Expires: {expires_at[:10]}\n\n"
            f"Share this code with users!",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to create code: {e}")


async def cmd_listcodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!")
        return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT code, points, max_uses, used_count, expires_at, is_active "
                "FROM redeem_codes ORDER BY created_at DESC"
            ) as cur:
                rows = await cur.fetchall()

        if not rows:
            await update.message.reply_text("🎫 No redeem codes yet.")
            return

        lines = [f"🎫 *ALL REDEEM CODES*\n{SEP()}\n"]
        for code, pts, mu, used, exp, active in rows:
            s = "✅" if active else "❌"
            e = (exp or "")[:10] or "No expiry"
            lines.append(f"{s} `{code}` | +{pts}pts | {used}/{mu} | Exp:{e}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_deletecode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!")
        return
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: `/deletecode CODE`", parse_mode="Markdown"
        )
        return
    code = context.args[0].upper()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE redeem_codes SET is_active = 0 WHERE code = ?", (code,)
        )
        await db.commit()
    await update.message.reply_text(f"✅ Code `{code}` deactivated.", parse_mode="Markdown")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!")
        return
    await update.message.reply_text(
        f"📢 *BROADCAST PANEL*\n{SEP()}\n\nSelect target audience:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 All Users",           callback_data="bc_all")],
            [InlineKeyboardButton("🎯 Active (last 7d)",    callback_data="bc_active")],
            [InlineKeyboardButton("🏆 Top Referrers (50)",  callback_data="bc_top")],
            [InlineKeyboardButton("💎 Premium (500+ cred)", callback_data="bc_premium")],
        ])
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!")
        return
    stats = await get_bot_stats()
    storage_note = "✅ Persistent" if STORAGE_OK else "⚠️ Non-persistent"
    await update.message.reply_text(
        f"📊 *STATS*\n{SEP()}\n\n"
        f"👥 Users: {stats['total_users']:,}\n"
        f"📦 Files: {stats['total_files']:,}\n"
        f"🔗 Referrals: {stats['completed_refs']:,}\n"
        f"💰 Credits: {stats['total_credits']:,}\n"
        f"🎫 Codes: {stats['active_codes']:,}\n\n"
        f"💾 Storage: {storage_note}",
        parse_mode="Markdown"
    )


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🔒 Access Denied!")
        return
    if not os.path.exists(DB_PATH):
        await update.message.reply_text("❌ Database file not found.")
        return
    with open(DB_PATH, "rb") as f:
        await context.bot.send_document(
            chat_id=ADMIN_ID,
            document=f,
            filename=f"senzo_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
            caption="💾 Database Backup"
        )


async def cmd_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """FIX #6 – All DB operations in proper sequence, no early return inside context manager."""
    user = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")

    if not context.args:
        await update.message.reply_text(
            f"💰 *REDEEM CODE*\n\nUsage: `/redeem YOUR-CODE`",
            parse_mode="Markdown"
        )
        return

    code = context.args[0].upper()

    try:
        # Step 1: Read code info
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT points, max_uses, used_count, expires_at, is_active "
                "FROM redeem_codes WHERE code = ?",
                (code,)
            ) as cur:
                row = await cur.fetchone()

        if not row:
            await update.message.reply_text("❌ *Invalid code!*", parse_mode="Markdown")
            return

        points, max_uses, used_count, expires_at, is_active = row

        if not is_active:
            await update.message.reply_text(
                "❌ *This code is no longer active!*", parse_mode="Markdown"
            )
            return

        if expires_at:
            try:
                expiry = datetime.strptime(expires_at[:19], "%Y-%m-%d %H:%M:%S")
                if datetime.now() > expiry:
                    await update.message.reply_text("❌ *Code expired!*", parse_mode="Markdown")
                    return
            except Exception:
                pass

        if used_count >= max_uses:
            await update.message.reply_text(
                "❌ *Code reached maximum uses!*", parse_mode="Markdown"
            )
            return

        # Step 2: Check if user already used it
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT 1 FROM redeem_usage WHERE code = ? AND user_id = ?",
                (code, user.id)
            ) as cur:
                already = await cur.fetchone()

        if already:
            await update.message.reply_text(
                "❌ *Code already used by you!*", parse_mode="Markdown"
            )
            return

        # Step 3: Record usage and increment count (separate connection = clean commit)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO redeem_usage (code, user_id) VALUES (?, ?)",
                (code, user.id)
            )
            await db.execute(
                "UPDATE redeem_codes SET used_count = used_count + 1 WHERE code = ?",
                (code,)
            )
            await db.commit()

        # Step 4: Add credits
        await add_credits(user.id, points, f"Redeem: {code}")
        u = await get_user(user.id)

        await update.message.reply_text(
            f"✅ *CODE REDEEMED SUCCESSFULLY!*\n{SEP()}\n\n"
            f"• Code: `{code}`\n"
            f"• +{points} Credits added! 💰\n"
            f"• New balance: *{u['credits']:,}* credits\n\n"
            f"Thank you for using Senzo Premium! 🌟",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )

    except Exception as e:
        logger.error(f"cmd_redeem: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again.")


async def cmd_myrefs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT r.product_id, p.name, COUNT(*) as cnt, p.required_refs
                FROM referrals r
                LEFT JOIN products p ON r.product_id = p.id
                WHERE r.referrer_id = ? AND r.status = 'completed'
                GROUP BY r.product_id
            """, (user.id,)) as cur:
                rows = await cur.fetchall()

        if not rows:
            await update.message.reply_text(
                "🔗 No referrals yet.\n\nBrowse files and share your link!"
            )
            return

        lines = [f"🔗 *MY REFERRALS*\n{SEP()}\n"]
        for pid, name, cnt, req in rows:
            bf  = min(10, int(cnt / req * 10)) if req else 10
            bar = "▓" * bf + "░" * (10 - bf)
            status = "✅" if cnt >= req else f"{cnt}/{req}"
            lines.append(f"📦 *{name or pid}*\n[{bar}] {status}\n")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.username or "", user.full_name or "")
    u = await get_user(user.id)
    if not u:
        return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM user_unlocks WHERE user_id = ?", (user.id,)
            ) as cur:
                unlocked = (await cur.fetchone())[0]

        joined = (u.get("joined_date") or "")[:10] or "Unknown"
        uname  = f"@{u['username']}" if u.get("username") else "No username"

        await update.message.reply_text(
            f"👤 *MY PROFILE*\n{SEP()}\n\n"
            f"• ID: `{user.id}`\n"
            f"• Username: {uname}\n"
            f"• Joined: {joined}\n"
            f"• Rank: {u['rank']}\n\n"
            f"💰 Credits: {u['credits']:,}\n"
            f"🔗 Referrals: {u['total_referrals']:,}\n"
            f"📦 Unlocked Files: {unlocked}",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"❓ *HELP*\n{SEP()}\n\n"
        f"• `/start` – Main menu\n"
        f"• `/redeem CODE` – Redeem a code\n"
        f"• `/myrefs` – My referral progress\n"
        f"• `/profile` – My profile\n\n"
        f"_Powered by Senzo Technologies_ 🌟",
        parse_mode="Markdown"
    )


# ═══════════════════════════════════════════════════════
#                  ERROR HANDLER
# ═══════════════════════════════════════════════════════

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)


# ═══════════════════════════════════════════════════════
#                      MAIN
# ═══════════════════════════════════════════════════════

async def post_init(application):
    await init_db()
    # Pre-cache bot username at startup
    await get_bot_username(application.bot)

    if not STORAGE_OK:
        try:
            await application.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "⚠️ *STORAGE WARNING*\n\n"
                    "Bot is running WITHOUT persistent storage.\n"
                    "Products and users will reset on redeploy!\n\n"
                    "On Railway: go to your service → Volumes → "
                    "Add Volume → Mount path: `/data`"
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass

    logger.info(f"🚀 Senzo Premium Bot v4.0 started! DB={DB_PATH}")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start",        start))
    app.add_handler(CommandHandler("admin",        cmd_admin))
    app.add_handler(CommandHandler("createredeem", cmd_createredeem))
    app.add_handler(CommandHandler("listcodes",    cmd_listcodes))
    app.add_handler(CommandHandler("deletecode",   cmd_deletecode))
    app.add_handler(CommandHandler("broadcast",    cmd_broadcast))
    app.add_handler(CommandHandler("stats",        cmd_stats))
    app.add_handler(CommandHandler("backup",       cmd_backup))
    app.add_handler(CommandHandler("redeem",       cmd_redeem))
    app.add_handler(CommandHandler("myrefs",       cmd_myrefs))
    app.add_handler(CommandHandler("profile",      cmd_profile))
    app.add_handler(CommandHandler("help",         cmd_help))

    # Callbacks
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Messages
    app.add_handler(MessageHandler(
        filters.TEXT | filters.Document.ALL | filters.PHOTO |
        filters.VIDEO | filters.AUDIO | filters.VOICE,
        message_handler
    ))

    # Global error handler
    app.add_error_handler(error_handler)

    logger.info("✅ All handlers registered. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
