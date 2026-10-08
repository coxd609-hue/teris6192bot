import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------- CONFIG ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")


# ---------- DATABASE ----------
def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            content TEXT NOT NULL,
            tag TEXT DEFAULT 'general',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id)
    """)
    conn.commit()
    cur.close()
    conn.close()
    logger.info("Database initialized.")


# ---------- COMMANDS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Welcome to Teris Memory Bot!*\n\n"
        "I help you save, retrieve, and organize your notes.\n\n"
        "*Commands:*\n"
        "• `/save <text>` — Save a note\n"
        "• `/save <text> #tag` — Save with a tag\n"
        "• `/retrieve` — Show your last 5 notes\n"
        "• `/retrieve #tag` — Show notes with a tag\n"
        "• `/organize` — View notes grouped by tag\n"
        "• `/tags` — List all your tags\n"
        "• `/delete <id>` — Delete a note by ID\n"
        "• `/clear` — Delete all your notes\n"
        "• `/help` — Show this message"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args

    if not args:
        await update.message.reply_text(
            "Usage: `/save Buy milk #shopping`", parse_mode="Markdown"
        )
        return

    content = " ".join(args)
    tag = "general"

    # Extract tag if any word starts with #
    words = content.split()
    tags = [w for w in words if w.startswith("#") and len(w) > 1]
    if tags:
        tag = tags[0][1:].lower()
        words = [w for w in words if w not in tags]
        content = " ".join(words).strip()

    if not content:
        await update.message.reply_text("Please provide text to save.")
        return

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO memories (user_id, content, tag) VALUES (%s, %s, %s) RETURNING id",
        (user_id, content, tag),
    )
    new_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()

    await update.message.reply_text(
        f"✅ Saved (ID: `{new_id}`) under tag `#{tag}`\n📝 {content}",
        parse_mode="Markdown",
    )


async def retrieve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args

    conn = get_conn()
    cur = conn.cursor()

    if args and args[0].startswith("#"):
        tag = args[0][1:].lower()
        cur.execute(
            "SELECT id, content, tag, created_at FROM memories "
            "WHERE user_id = %s AND tag = %s ORDER BY created_at DESC LIMIT 10",
            (user_id, tag),
        )
        rows = cur.fetchall()
        header = f"📂 Notes tagged `#{tag}`:"
    else:
        cur.execute(
            "SELECT id, content, tag, created_at FROM memories "
            "WHERE user_id = %s ORDER BY created_at DESC LIMIT 5",
            (user_id,),
        )
        rows = cur.fetchall()
        header = "📂 Your last 5 notes:"

    cur.close()
    conn.close()

    if not rows:
        await update.message.reply_text("No notes found.")
        return

    lines = [header, ""]
    for r in rows:
        date = r["created_at"].strftime("%Y-%m-%d %H:%M")
        lines.append(f"`[{r['id']}]` #{r['tag']} — {r['content']}\n_{date}_\n")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def organize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Group the user's notes by tag."""
    user_id = update.effective_user.id

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT tag, COUNT(*) as count FROM memories WHERE user_id = %s "
        "GROUP BY tag ORDER BY count DESC",
        (user_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        await update.message.reply_text("You have no notes yet. Use /save to add one.")
        return

    keyboard = [
        [InlineKeyboardButton(f"#{r['tag']} ({r['count']})", callback_data=f"tag:{r['tag']}")]
        for r in rows
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🗂 *Your notes by tag:*\nTap a tag to view its notes.",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )


async def tag_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline tag button presses."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    tag = query.data.split(":", 1)[1]

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, content, created_at FROM memories "
        "WHERE user_id = %s AND tag = %s ORDER BY created_at DESC",
        (user_id, tag),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        await query.edit_message_text(f"No notes under `#{tag}`.", parse_mode="Markdown")
        return

    lines = [f"📂 Notes under `#{tag}`:", ""]
    for r in rows:
        lines.append(f"`[{r['id']}]` {r['content']}")

    await query.edit_message_text("\n".join(lines), parse_mode="Markdown")


async def tags(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT tag FROM memories WHERE user_id = %s ORDER BY tag",
        (user_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        await update.message.reply_text("No tags yet.")
        return

    tag_list = "\n".join([f"• `#{r['tag']}`" for r in rows])
    await update.message.reply_text(f"🏷 *Your tags:*\n{tag_list}", parse_mode="Markdown")


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args

    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: `/delete <id>`", parse_mode="Markdown")
        return

    note_id = int(args[0])

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM memories WHERE id = %s AND user_id = %s RETURNING id",
        (note_id, user_id),
    )
    deleted = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    if deleted:
        await update.message.reply_text(f"🗑 Deleted note `{note_id}`.", parse_mode="Markdown")
    else:
        await update.message.reply_text("Note not found or not yours.")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    keyboard = [[
        InlineKeyboardButton("✅ Yes, delete all", callback_data="confirm_clear"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_clear"),
    ]]
    await update.message.reply_text(
        "⚠️ Are you sure you want to delete *all* your notes?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def clear_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "confirm_clear":
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM memories WHERE user_id = %s", (user_id,))
        conn.commit()
        cur.close()
        conn.close()
        await query.edit_message_text("🗑 All your notes have been deleted.")
    else:
        await query.edit_message_text("Cancelled.")


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Unknown command. Try /help.")


# ---------- MAIN ----------
def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN is not set.")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set.")

    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("save", save))
    app.add_handler(CommandHandler("retrieve", retrieve))
    app.add_handler(CommandHandler("organize", organize))
    app.add_handler(CommandHandler("tags", tags))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("clear", clear))

    app.add_handler(CallbackQueryHandler(tag_button, pattern=r"^tag:"))
    app.add_handler(CallbackQueryHandler(clear_callback, pattern=r"^(confirm_clear|cancel_clear)$"))

    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    logger.info("Bot is starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
