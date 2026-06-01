import logging
import sqlite3
import uuid
import asyncio
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from telegram.error import TelegramError

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
import os
BOT_TOKEN = os.getenv("BOT_TOKEN", "8863632618:AAHybJVTAKAGoLGrF9CP_SvYhdUwo8j_eQg")
ADMIN_ID  = int(os.getenv("ADMIN_ID", "8105949422"))
DB_PATH   = "bot.db"

# Conversation states
WAITING_FILE      = 1
WAITING_REF_COUNT = 2

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                credits    INTEGER DEFAULT 10,
                joined_date TEXT
            );

            CREATE TABLE IF NOT EXISTS products (
                id           TEXT PRIMARY KEY,
                file_id      TEXT,
                file_type    TEXT,
                required_refs INTEGER,
                created_date  TEXT
            );

            CREATE TABLE IF NOT EXISTS referrals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER,
                product_id  TEXT,
                status      TEXT DEFAULT 'completed',
                date        TEXT,
                UNIQUE(referrer_id, referred_id, product_id)
            );

            CREATE TABLE IF NOT EXISTS user_unlocks (
                user_id       INTEGER,
                product_id    TEXT,
                unlocked_date TEXT,
                PRIMARY KEY (user_id, product_id)
            );
        """)

def register_user(user_id: int, username: str):
    with get_db() as conn:
        existing = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO users (user_id, username, credits, joined_date) VALUES (?,?,10,?)",
                (user_id, username or "Anonymous", datetime.now().isoformat())
            )
            return True  # new user
        else:
            # update username in case it changed
            conn.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
            return False

def get_user(user_id: int):
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def get_all_products():
    with get_db() as conn:
        return conn.execute("SELECT * FROM products ORDER BY created_date DESC").fetchall()

def get_product(product_id: str):
    with get_db() as conn:
        return conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()

def add_product(file_id: str, file_type: str, required_refs: int) -> str:
    product_id = str(uuid.uuid4())[:8].upper()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO products (id, file_id, file_type, required_refs, created_date) VALUES (?,?,?,?,?)",
            (product_id, file_id, file_type, required_refs, datetime.now().isoformat())
        )
    return product_id

def delete_product(product_id: str):
    with get_db() as conn:
        conn.execute("DELETE FROM products WHERE id=?", (product_id,))
        conn.execute("DELETE FROM referrals WHERE product_id=?", (product_id,))
        conn.execute("DELETE FROM user_unlocks WHERE product_id=?", (product_id,))

def delete_all_products():
    with get_db() as conn:
        conn.executescript("""
            DELETE FROM products;
            DELETE FROM referrals;
            DELETE FROM user_unlocks;
        """)

def get_referral_count(referrer_id: int, product_id: str) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM referrals WHERE referrer_id=? AND product_id=?",
            (referrer_id, product_id)
        ).fetchone()
        return row["cnt"] if row else 0

def add_referral(referrer_id: int, referred_id: int, product_id: str) -> bool:
    """Returns True if referral was newly added."""
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO referrals (referrer_id, referred_id, product_id, status, date) VALUES (?,?,?,?,?)",
                (referrer_id, referred_id, product_id, "completed", datetime.now().isoformat())
            )
            # Award credits to referrer
            conn.execute("UPDATE users SET credits = credits + 5 WHERE user_id=?", (referrer_id,))
        return True
    except sqlite3.IntegrityError:
        return False  # already referred

def is_unlocked(user_id: int, product_id: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_unlocks WHERE user_id=? AND product_id=?",
            (user_id, product_id)
        ).fetchone()
        return row is not None

def unlock_product(user_id: int, product_id: str):
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_unlocks (user_id, product_id, unlocked_date) VALUES (?,?,?)",
            (user_id, product_id, datetime.now().isoformat())
        )

def get_leaderboard():
    with get_db() as conn:
        return conn.execute("""
            SELECT u.user_id, u.username, u.credits,
                   COUNT(r.id) as total_refs
            FROM users u
            LEFT JOIN referrals r ON r.referrer_id = u.user_id
            GROUP BY u.user_id
            ORDER BY total_refs DESC, u.credits DESC
            LIMIT 10
        """).fetchall()

def get_user_rank(user_id: int) -> int:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT user_id FROM (
                SELECT u.user_id, COUNT(r.id) as total_refs
                FROM users u
                LEFT JOIN referrals r ON r.referrer_id = u.user_id
                GROUP BY u.user_id
                ORDER BY total_refs DESC
            )
        """).fetchall()
        for i, row in enumerate(rows, 1):
            if row["user_id"] == user_id:
                return i
        return 0

def get_stats():
    with get_db() as conn:
        users   = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        files   = conn.execute("SELECT COUNT(*) as c FROM products").fetchone()["c"]
        refs    = conn.execute("SELECT COUNT(*) as c FROM referrals").fetchone()["c"]
        return users, files, refs

def get_all_users():
    with get_db() as conn:
        return conn.execute("SELECT * FROM users ORDER BY credits DESC LIMIT 20").fetchall()

def get_user_total_refs(user_id: int) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM referrals WHERE referrer_id=?", (user_id,)
        ).fetchone()
        return row["c"] if row else 0

# ─────────────────────────────────────────────
# Keyboards
# ─────────────────────────────────────────────

def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📦 Browse Files", callback_data="browse_files")],
        [
            InlineKeyboardButton("🔗 My Referrals", callback_data="my_referrals"),
            InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard"),
        ],
        [InlineKeyboardButton("💰 My Credits", callback_data="my_credits")],
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)

def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload New File", callback_data="admin_upload")],
        [InlineKeyboardButton("🗂 Manage Files", callback_data="admin_manage_files")],
        [InlineKeyboardButton("👥 View Users", callback_data="admin_view_users")],
        [InlineKeyboardButton("🗑 Delete All Files", callback_data="admin_delete_all")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ])

def back_keyboard(callback: str = "main_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=callback)]])

# ─────────────────────────────────────────────
# Helper: send file to user
# ─────────────────────────────────────────────

async def deliver_file(context: ContextTypes.DEFAULT_TYPE, user_id: int, product):
    """Send the product file to the user."""
    try:
        caption = "✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n🎉 File unlocked and delivered!"
        if product["file_type"] == "document":
            await context.bot.send_document(user_id, product["file_id"], caption=caption, parse_mode="Markdown")
        elif product["file_type"] == "video":
            await context.bot.send_video(user_id, product["file_id"], caption=caption, parse_mode="Markdown")
        elif product["file_type"] == "photo":
            await context.bot.send_photo(user_id, product["file_id"], caption=caption, parse_mode="Markdown")
        elif product["file_type"] == "audio":
            await context.bot.send_audio(user_id, product["file_id"], caption=caption, parse_mode="Markdown")
        else:
            await context.bot.send_document(user_id, product["file_id"], caption=caption, parse_mode="Markdown")
    except TelegramError as e:
        logger.error(f"Failed to deliver file to {user_id}: {e}")

# ─────────────────────────────────────────────
# /start handler
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = register_user(user.id, user.username or user.first_name)

    args = context.args or []
    param = args[0] if args else ""

    # Deep link: ref_REFERRERID_PRODUCTID
    if param.startswith("ref_"):
        parts = param.split("_")
        if len(parts) == 3:
            _, referrer_id_str, product_id = parts
            try:
                referrer_id = int(referrer_id_str)
            except ValueError:
                referrer_id = None

            if referrer_id and referrer_id != user.id:
                product = get_product(product_id)
                if product:
                    added = add_referral(referrer_id, user.id, product_id)
                    if added:
                        ref_count = get_referral_count(referrer_id, product_id)
                        required  = product["required_refs"]

                        # Notify referrer
                        try:
                            if ref_count >= required and not is_unlocked(referrer_id, product_id):
                                unlock_product(referrer_id, product_id)
                                await deliver_file(context, referrer_id, product)
                                await context.bot.send_message(
                                    referrer_id,
                                    f"✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n"
                                    f"🎉 *File Unlocked!*\n\n"
                                    f"Your referral goal of *{required}* has been reached!\n"
                                    f"The file has been sent to you automatically.",
                                    parse_mode="Markdown"
                                )
                            else:
                                await context.bot.send_message(
                                    referrer_id,
                                    f"✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n"
                                    f"🔗 New referral! Progress: *{ref_count}/{required}*\n"
                                    f"💰 +5 credits added!",
                                    parse_mode="Markdown"
                                )
                        except TelegramError:
                            pass

                        await update.message.reply_text(
                            f"✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n"
                            f"👋 Welcome! You joined via a referral link.\n"
                            f"{'🎁 Welcome bonus: 10 credits added!' if is_new else ''}",
                            parse_mode="Markdown",
                            reply_markup=main_menu_keyboard(user.id == ADMIN_ID)
                        )
                        return
                    else:
                        await update.message.reply_text(
                            "ℹ️ This referral was already counted.",
                            parse_mode="Markdown",
                            reply_markup=main_menu_keyboard(user.id == ADMIN_ID)
                        )
                        return
            elif referrer_id == user.id:
                await update.message.reply_text(
                    "❌ You cannot refer yourself!",
                    parse_mode="Markdown",
                    reply_markup=main_menu_keyboard(user.id == ADMIN_ID)
                )
                return

    # Deep link: product_PRODUCTID
    if param.startswith("product_"):
        product_id = param[8:]
        await show_product_page(update, context, product_id)
        return

    # Normal start
    welcome = (
        f"✨ *SENZO FILES* ✨\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"👋 Welcome, *{user.first_name}*!\n\n"
        f"{'🎁 *+10 welcome credits* added to your account!' if is_new else '💡 Good to see you again!'}\n\n"
        f"Browse files, share referral links, and unlock exclusive content!"
    )
    await update.message.reply_text(
        welcome,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(user.id == ADMIN_ID)
    )

# ─────────────────────────────────────────────
# Show product page
# ─────────────────────────────────────────────

async def show_product_page(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str):
    user = update.effective_user
    product = get_product(product_id)

    if not product:
        text = "❌ Product not found."
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=back_keyboard())
        else:
            await update.message.reply_text(text, reply_markup=back_keyboard())
        return

    unlocked   = is_unlocked(user.id, product_id)
    ref_count  = get_referral_count(user.id, product_id)
    required   = product["required_refs"]
    bot_info   = await context.bot.get_me()
    bot_username = bot_info.username

    if unlocked:
        status_line = "✅ *Status: UNLOCKED*"
        buttons = [
            [InlineKeyboardButton("📥 Download File", callback_data=f"download_{product_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="browse_files")],
        ]
    else:
        progress = "▓" * ref_count + "░" * (required - ref_count)
        status_line = f"🔒 *Status: LOCKED*\n📊 Progress: `{progress}` {ref_count}/{required}"
        ref_link = f"https://t.me/{bot_username}?start=ref_{user.id}_{product_id}"
        buttons = [
            [InlineKeyboardButton("🔗 Copy Referral Link", callback_data=f"reflink_{product_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="browse_files")],
        ]

    text = (
        f"✨ *SENZO FILES* ✨\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"📦 *File ID:* `{product_id}`\n"
        f"🗂 *Type:* {product['file_type'].capitalize()}\n"
        f"🔑 *Required Referrals:* {required}\n\n"
        f"{status_line}"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        await update.message.reply_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

# ─────────────────────────────────────────────
# Callback query handler
# ─────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    user   = query.from_user
    data   = query.data
    await query.answer()

    # ── Main menu ──
    if data == "main_menu":
        await query.edit_message_text(
            "✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n\nChoose an option below:",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(user.id == ADMIN_ID)
        )

    # ── Browse files ──
    elif data == "browse_files":
        products = get_all_products()
        if not products:
            await query.edit_message_text(
                "📭 No files available yet. Check back later!",
                parse_mode="Markdown",
                reply_markup=back_keyboard()
            )
            return

        text = "✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n\n📦 *Available Files:*\n"
        buttons = []
        for p in products:
            unlocked  = is_unlocked(user.id, p["id"])
            ref_count = get_referral_count(user.id, p["id"])
            status    = "✅ UNLOCKED" if unlocked else f"🔒 LOCKED ({p['required_refs']} refs)"
            label     = f"{p['file_type'].upper()} [{p['id']}] — {status}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"view_product_{p['id']}")])

        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    # ── View individual product ──
    elif data.startswith("view_product_"):
        product_id = data[13:]
        await show_product_page(update, context, product_id)

    # ── Download file ──
    elif data.startswith("download_"):
        product_id = data[9:]
        if is_unlocked(user.id, product_id):
            product = get_product(product_id)
            if product:
                await deliver_file(context, user.id, product)
                await query.edit_message_text(
                    "✅ File sent! Check your messages.",
                    parse_mode="Markdown",
                    reply_markup=back_keyboard("browse_files")
                )
            else:
                await query.edit_message_text("❌ File not found.", reply_markup=back_keyboard())
        else:
            await query.edit_message_text(
                "❌ You haven't unlocked this file yet.",
                reply_markup=back_keyboard("browse_files")
            )

    # ── Referral link ──
    elif data.startswith("reflink_"):
        product_id   = data[8:]
        bot_info     = await context.bot.get_me()
        bot_username = bot_info.username
        ref_link     = f"https://t.me/{bot_username}?start=ref_{user.id}_{product_id}"
        product      = get_product(product_id)
        ref_count    = get_referral_count(user.id, product_id)
        required     = product["required_refs"] if product else "?"
        await query.edit_message_text(
            f"✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n\n"
            f"🔗 *Your Referral Link:*\n`{ref_link}`\n\n"
            f"📊 Progress: *{ref_count}/{required}*\n\n"
            f"Share this link with friends to unlock the file!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data=f"view_product_{product_id}")]
            ])
        )

    # ── My referrals ──
    elif data == "my_referrals":
        products  = get_all_products()
        bot_info  = await context.bot.get_me()
        bot_username = bot_info.username
        text = "✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n\n🔗 *Your Referral Progress:*\n\n"

        if not products:
            text += "No files available yet."
        else:
            for p in products:
                ref_count = get_referral_count(user.id, p["id"])
                required  = p["required_refs"]
                unlocked  = is_unlocked(user.id, p["id"])
                progress  = "▓" * ref_count + "░" * max(0, required - ref_count)
                status    = "✅" if unlocked else "🔒"
                ref_link  = f"https://t.me/{bot_username}?start=ref_{user.id}_{p['id']}"
                text += (
                    f"{status} `{p['id']}` — `{progress}` {ref_count}/{required}\n"
                    f"   🔗 `{ref_link}`\n\n"
                )

        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=back_keyboard()
        )

    # ── Leaderboard ──
    elif data == "leaderboard":
        board = get_leaderboard()
        rank  = get_user_rank(user.id)
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        text = "✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n\n🏆 *Leaderboard — Top 10*\n\n"

        for i, row in enumerate(board, 1):
            medal = medals.get(i, f"{i}.")
            uname = row["username"] or "Anonymous"
            text += f"{medal} *{uname}* — {row['total_refs']} refs | 💰 {row['credits']} credits\n"

        text += f"\n📍 *Your Rank:* #{rank}"
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=back_keyboard()
        )

    # ── My credits ──
    elif data == "my_credits":
        db_user    = get_user(user.id)
        total_refs = get_user_total_refs(user.id)
        credits    = db_user["credits"] if db_user else 0
        text = (
            f"✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n\n"
            f"👤 *Profile: {user.first_name}*\n\n"
            f"💰 Credits: *{credits}*\n"
            f"🔗 Total Referrals: *{total_refs}*\n\n"
            f"_Earn +5 credits for each successful referral!_"
        )
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=back_keyboard()
        )

    # ─────────────────────────────────────────
    # Admin section
    # ─────────────────────────────────────────
    elif data == "admin_panel":
        if user.id != ADMIN_ID:
            await query.answer("❌ Access denied.", show_alert=True)
            return
        total_users, total_files, total_refs = get_stats()
        text = (
            f"✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n\n"
            f"⚙️ *Admin Panel*\n\n"
            f"📊 *Statistics:*\n"
            f"👥 Total Users: *{total_users}*\n"
            f"📦 Total Files: *{total_files}*\n"
            f"🔗 Completed Referrals: *{total_refs}*"
        )
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=admin_panel_keyboard()
        )

    elif data == "admin_upload":
        if user.id != ADMIN_ID:
            await query.answer("❌ Access denied.", show_alert=True)
            return
        context.user_data["state"] = WAITING_FILE
        await query.edit_message_text(
            "✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n\n"
            "📤 *Upload New File*\n\nSend me the file (document, video, photo, or audio):",
            parse_mode="Markdown",
            reply_markup=back_keyboard("admin_panel")
        )

    elif data == "admin_manage_files":
        if user.id != ADMIN_ID:
            await query.answer("❌ Access denied.", show_alert=True)
            return
        products = get_all_products()
        if not products:
            await query.edit_message_text(
                "📭 No files uploaded yet.",
                parse_mode="Markdown",
                reply_markup=back_keyboard("admin_panel")
            )
            return

        text    = "✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n\n🗂 *Manage Files:*\n"
        buttons = []
        for p in products:
            label = f"🗑 [{p['id']}] {p['file_type'].upper()} — {p['required_refs']} refs"
            buttons.append([InlineKeyboardButton(label, callback_data=f"admin_del_{p['id']}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("admin_del_"):
        if user.id != ADMIN_ID:
            await query.answer("❌ Access denied.", show_alert=True)
            return
        product_id = data[10:]
        delete_product(product_id)
        await query.answer(f"✅ Product {product_id} deleted.", show_alert=True)
        # Refresh manage files
        products = get_all_products()
        if not products:
            await query.edit_message_text(
                "📭 No files remaining.",
                parse_mode="Markdown",
                reply_markup=back_keyboard("admin_panel")
            )
        else:
            buttons = []
            for p in products:
                label = f"🗑 [{p['id']}] {p['file_type'].upper()} — {p['required_refs']} refs"
                buttons.append([InlineKeyboardButton(label, callback_data=f"admin_del_{p['id']}")])
            buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
            await query.edit_message_text(
                "✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n\n🗂 *Manage Files:*\n",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons)
            )

    elif data == "admin_delete_all":
        if user.id != ADMIN_ID:
            await query.answer("❌ Access denied.", show_alert=True)
            return
        await query.edit_message_text(
            "⚠️ *Are you sure?*\n\nThis will delete ALL files and referral data.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Yes, delete all", callback_data="admin_confirm_delete_all")],
                [InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")],
            ])
        )

    elif data == "admin_confirm_delete_all":
        if user.id != ADMIN_ID:
            await query.answer("❌ Access denied.", show_alert=True)
            return
        delete_all_products()
        await query.edit_message_text(
            "✅ All files and data deleted.",
            parse_mode="Markdown",
            reply_markup=back_keyboard("admin_panel")
        )

    elif data == "admin_view_users":
        if user.id != ADMIN_ID:
            await query.answer("❌ Access denied.", show_alert=True)
            return
        users = get_all_users()
        text  = "✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n\n👥 *Top 20 Users:*\n\n"
        for i, u in enumerate(users, 1):
            uname = u["username"] or "Anonymous"
            text += f"{i}. *{uname}* — 💰 {u['credits']} credits\n"
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=back_keyboard("admin_panel")
        )

# ─────────────────────────────────────────────
# File/message handler (admin upload flow)
# ─────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    state = context.user_data.get("state")

    # Only admin uses this flow
    if user.id != ADMIN_ID:
        return

    if state == WAITING_FILE:
        msg      = update.message
        file_id  = None
        file_type = None

        if msg.document:
            file_id   = msg.document.file_id
            file_type = "document"
        elif msg.video:
            file_id   = msg.video.file_id
            file_type = "video"
        elif msg.photo:
            file_id   = msg.photo[-1].file_id
            file_type = "photo"
        elif msg.audio:
            file_id   = msg.audio.file_id
            file_type = "audio"
        else:
            await msg.reply_text("❌ Please send a document, video, photo, or audio file.")
            return

        context.user_data["pending_file_id"]   = file_id
        context.user_data["pending_file_type"] = file_type
        context.user_data["state"]             = WAITING_REF_COUNT

        await msg.reply_text(
            f"✅ Got it! *{file_type.capitalize()}* received.\n\n"
            f"🔢 How many referrals should be required to unlock this file?\n"
            f"_(Send a number, e.g. 3)_",
            parse_mode="Markdown",
            reply_markup=back_keyboard("admin_panel")
        )

    elif state == WAITING_REF_COUNT:
        text = update.message.text or ""
        if not text.isdigit() or int(text) < 1:
            await update.message.reply_text(
                "❌ Please send a valid positive number (e.g. 3)."
            )
            return

        required  = int(text)
        file_id   = context.user_data.pop("pending_file_id", None)
        file_type = context.user_data.pop("pending_file_type", None)
        context.user_data.pop("state", None)

        if not file_id or not file_type:
            await update.message.reply_text("❌ Something went wrong. Please start over.")
            return

        product_id   = add_product(file_id, file_type, required)
        bot_info     = await context.bot.get_me()
        bot_username = bot_info.username
        link         = f"https://t.me/{bot_username}?start=product_{product_id}"

        await update.message.reply_text(
            f"✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n\n"
            f"✅ *File uploaded successfully!*\n\n"
            f"📦 ID: `{product_id}`\n"
            f"🗂 Type: {file_type.capitalize()}\n"
            f"🔑 Required Referrals: *{required}*\n\n"
            f"🔗 *Share Link:*\n`{link}`",
            parse_mode="Markdown",
            reply_markup=back_keyboard("admin_panel")
        )

# ─────────────────────────────────────────────
# /help command
# ─────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "✨ *SENZO FILES* ✨\n━━━━━━━━━━━━━━━━\n\n"
        "📖 *How it works:*\n\n"
        "1. Browse available files\n"
        "2. Get your unique referral link for a file\n"
        "3. Share the link with friends\n"
        "4. When they join via your link, your progress increases\n"
        "5. Once you reach the required referrals, the file is auto-sent to you!\n\n"
        "💰 *Credits:* +10 on signup, +5 per referral\n\n"
        "Use /start to open the main menu."
    )
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=main_menu_keyboard(update.effective_user.id == ADMIN_ID)
    )

# ─────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────

def main():
    init_db()
    logger.info("Database initialised.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  help_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND,
        handle_message
    ))

    logger.info("Bot is running…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
