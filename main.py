"""Telegram bot (long polling).

Flow: a post in Meera's private notes channel is scored and queued. Up to WEEKLY_CAP drafts a week
(best score first) are developed (news + draft + voice score + citations) and posted to REVIEW_CHAT_ID
with Approve / Redo / Kill buttons. By default that is the notes channel itself: drafts appear as a
reply under the note, and Redo instructions and /stats, /log are typed in the channel too.

The bot never posts anywhere public. Its only outbound messages go to REVIEW_CHAT_ID
(or, for /start, back to whoever sent it, so you can discover your chat id during setup).
"""
import asyncio
import logging
import sys

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters, Update
from telegram.error import NetworkError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import config
import pipeline
import store

sys.stderr.reconfigure(encoding="utf-8")  # Windows console: don't choke on emoji in notes
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("skinstinct")

_dispatch_lock = asyncio.Lock()


def notes_channel_filter() -> filters.BaseFilter:
    """Channel posts from Meera's notes channel only."""
    return filters.UpdateType.CHANNEL_POST & filters.Chat(chat_id=int(config.TELEGRAM_CHAT_ID))


def review_chat_filter() -> filters.BaseFilter:
    return filters.Chat(chat_id=int(config.REVIEW_CHAT_ID))


def buttons(note_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"a|{note_id}"),
        InlineKeyboardButton("🔁 Redo", callback_data=f"r|{note_id}"),
        InlineKeyboardButton("🗑 Kill", callback_data=f"k|{note_id}"),
    ]])


def _reply_to(note_id: str | None) -> ReplyParameters | None:
    """In channel-review mode, thread bot messages under the note they're about."""
    if config.REVIEW_IN_CHANNEL and note_id and note_id.startswith("tg_"):
        return ReplyParameters(message_id=int(note_id[3:]), allow_sending_without_reply=True)
    return None


async def send_review(bot, text: str, markup=None, note_id: str | None = None):
    """Send to REVIEW_CHAT_ID, split to fit Telegram's 4096-char limit. Buttons go on the last part."""
    chunks, current = [], ""
    for para in text.split("\n\n"):
        if current and len(current) + len(para) + 2 > 4000:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{para}" if current else para
    chunks.append(current)
    for i, chunk in enumerate(chunks):
        await bot.send_message(chat_id=config.REVIEW_CHAT_ID, text=chunk[:4096],
                               reply_markup=markup if i == len(chunks) - 1 else None,
                               reply_parameters=_reply_to(note_id) if i == 0 else None,
                               disable_web_page_preview=True)


# ------------------------------------------------------------------ pacing

async def dispatch(bot, max_drafts: int | None = None):
    """Send queued notes as drafts while this week's cap allows, best score first.
    On Vercel each request drafts at most one note (~1-1.5 min) to stay inside the function time limit."""
    done = 0
    async with _dispatch_lock:
        while store.sent_this_week() < config.WEEKLY_CAP and (max_drafts is None or done < max_drafts):
            waiting = store.queue()
            if not waiting:
                return
            rec = waiting[0]
            done += 1
            store.update(rec["note_id"], status="drafting", drafting_since=store.now())
            log.info("Drafting %s (score %s)", rec["note_id"], rec["total_score"])
            try:
                result = await asyncio.to_thread(pipeline.develop, rec["text"], rec["score"])
            except Exception as e:
                log.error("  develop failed: %s", e)
                result = {"draft": {"error": str(e)}}
            if "error" in (result.get("draft") or {"error": "no draft"}):
                store.update(rec["note_id"], status="error", decision="error", decided_at=store.now())
                await safe_send(bot, f"⚠️ Could not draft note {rec['note_id']}: {result['draft'].get('error')}",
                                rec["note_id"])
                continue
            store.update(rec["note_id"], status="in_review", drafted_at=store.now(), result=result)
            try:
                await send_review(bot, pipeline.format_review(result), buttons(rec["note_id"]), rec["note_id"])
            except Exception as e:
                log.error("  sending review failed: %s", e)


async def dispatch_job(context: ContextTypes.DEFAULT_TYPE):
    await dispatch(context.bot)


async def safe_send(bot, text: str, note_id: str | None = None):
    try:
        await bot.send_message(chat_id=config.REVIEW_CHAT_ID, text=text, disable_web_page_preview=True,
                               reply_parameters=_reply_to(note_id))
    except Exception as e:
        log.error("send failed: %s", e)


# ------------------------------------------------------------------ notes in

# Bot messages start with one of these; never treat them as notes (belt and braces: Telegram
# doesn't normally deliver a bot's own channel posts back to it).
BOT_MARKERS = ("📝", "📥", "🔁", "📊", "✅", "🗑", "⚠️")


async def on_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    text = (post.text or post.caption or "").strip()
    note_id = f"tg_{post.message_id}"
    log.info("Note received (msg %s): %r", post.message_id, text[:80])
    if not text or text.startswith(BOT_MARKERS) or store.exists(note_id):
        return
    if config.REVIEW_IN_CHANNEL:
        # Reviewing inside the channel: commands and Redo instructions arrive as channel posts too.
        command = text.split()[0].split("@")[0].lower()
        if command == "/stats":
            return await on_stats(update, context)
        if command == "/log":
            return await on_log(update, context)
        redo_id = store.pop_redo()
        if redo_id:
            return await redo(context.bot, redo_id, text, post)
    try:
        scored = await asyncio.to_thread(pipeline.score, text)
        rec = store.add(note_id, "telegram", text, scored)
        log.info("  verdict=%s total=%s", scored["verdict"], scored.get("total_score"))
        if rec["status"] == "queued":
            position = [n["note_id"] for n in store.queue()].index(note_id) + 1
            full = store.sent_this_week() >= config.WEEKLY_CAP
            await safe_send(context.bot,
                            f"📥 Note captured: {scored['verdict']}, {rec['total_score']}/20. "
                            f"Queue position {position}."
                            + (f" This week's {config.WEEKLY_CAP} drafts are already sent; it waits for next week." if full else ""),
                            note_id)
        else:
            await safe_send(context.bot, f"📥 Note captured: {scored['verdict']}. {scored.get('reason', '')}", note_id)
        if config.SERVERLESS:
            await dispatch(context.bot, max_drafts=1)  # background tasks don't survive the request
        else:
            context.application.create_task(dispatch(context.bot))  # drafting takes ~1 min; don't block updates
    except Exception as e:  # never crash the bot
        log.error("  pipeline failed: %s", e)


# ------------------------------------------------------------------ review gate

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.message.chat.id != int(config.REVIEW_CHAT_ID):
        return
    action, note_id = query.data.split("|", 1)
    rec = store.get(note_id)
    if not rec or rec["status"] != "in_review":
        await query.edit_message_reply_markup(None)
        return
    await query.edit_message_reply_markup(None)
    if action == "a":
        name, md = store.save_approved(rec)
        store.update(note_id, status="approved", decision="approved", decided_at=store.now())
        await query.message.reply_document(document=md.encode("utf-8"), filename=name,
                                           caption="✅ Approved and saved. Post it on LinkedIn yourself when you're ready.")
    elif action == "k":
        store.update(note_id, status="killed", decision="killed", decided_at=store.now())
        await query.message.reply_text("🗑 Killed. Logged and dropped.")
    elif action == "r":
        store.set_redo(note_id)
        where = "Post it in this channel" if config.REVIEW_IN_CHANNEL else "Reply here"
        await query.message.reply_text(f"🔁 What should change? {where} with one line; "
                                       "your next message is used as the instruction, not as a new note.")


async def redo(bot, note_id: str, instruction: str, message):
    """Redraft `note_id` following Meera's one-line instruction (reuses the same news item)."""
    rec = store.get(note_id)
    await message.reply_text("🔁 Redrafting…")
    try:
        result = await asyncio.to_thread(pipeline.develop, rec["text"], rec["score"], instruction, rec.get("result"))
    except Exception as e:
        log.error("redo failed: %s", e)
        result = {"draft": {"error": str(e)}}
    if "error" in result["draft"]:
        store.set_redo(note_id)
        await message.reply_text(f"⚠️ Redraft failed ({result['draft']['error']}). Send the instruction again to retry.")
        return
    store.update(note_id, result=result, decision="redo", redo_count=rec.get("redo_count", 0) + 1)
    await send_review(bot, f"🔁 Redo {rec.get('redo_count', 0) + 1}: \"{instruction}\"\n\n"
                      + pipeline.format_review(result), buttons(note_id), note_id)


async def on_review_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A plain message in Meera's private review chat: the one-line instruction after pressing Redo."""
    note_id = store.pop_redo()
    if not note_id:
        await update.message.reply_text("Notes go in the notes channel. Here: /stats, or press Redo on a draft first.")
        return
    await redo(context.bot, note_id, update.message.text.strip(), update.message)


async def on_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = store.stats()
    await update.effective_message.reply_text(
        f"📊 Notes captured: {s['captured']}\n"
        f"Drafted: {s['drafted']}\n"
        f"Approved: {s['approved']}\n"
        f"Approved this week: {s['approved_week']} / {config.WEEKLY_CAP} target\n"
        f"Drafts sent this week: {s['sent_week']} / {config.WEEKLY_CAP}\n"
        f"Waiting in queue: {s['queued']} · awaiting your review: {s['awaiting']}")


async def on_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_document(document=store.log_csv().encode("utf-8"), filename="log.csv")


# ------------------------------------------------------------------ setup / plumbing

async def on_other(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Anything that did not match a handler: log the chat id and do nothing."""
    chat = update.effective_chat
    log.info("Ignored update from chat %s (%s, %r)", chat.id if chat else "?", chat.type if chat else "?",
             (chat.title or chat.username) if chat else "")


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Setup helper: tells the person in a private chat their chat id (for REVIEW_CHAT_ID)."""
    chat = update.effective_chat
    log.info("/start from private chat %s", chat.id)
    await update.message.reply_text(f"Your chat id is {chat.id}. Put it in .env as REVIEW_CHAT_ID.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, NetworkError):  # transient polling blips; the library retries
        log.warning("Network hiccup (retrying): %s", context.error)
        return
    log.error("Handler error: %s", context.error, exc_info=context.error)


async def post_init(app: Application):
    # A crash mid-draft leaves a note in "drafting"; put it back in the queue.
    for n in store.load().values():
        if n["status"] == "drafting":
            store.update(n["note_id"], status="queued")


def check_config() -> list[str]:
    problems = []
    if not config.TELEGRAM_BOT_TOKEN:
        problems.append("TELEGRAM_BOT_TOKEN is not set")
    for name in ("TELEGRAM_CHAT_ID", "REVIEW_CHAT_ID"):
        value = getattr(config, name)
        if not value:
            problems.append(f"{name} is not set")
        elif not value.lstrip("-").isdigit():
            problems.append(f"{name} must be a numeric chat id, got {value!r}")
    if not config.GEMINI_API_KEY:
        problems.append("GEMINI_API_KEY is not set")
    return problems


def build_app(setup_mode: bool = False, webhook: bool = False) -> Application:
    """Polling (laptop) or webhook (Vercel, see app.py).
    In setup mode only /start and chat-id logging run, so you can discover the ids for .env."""
    builder = Application.builder().token(config.TELEGRAM_BOT_TOKEN)
    builder = builder.updater(None) if webhook else builder.post_init(post_init).concurrent_updates(True)
    app = builder.build()
    if not setup_mode:
        review = review_chat_filter()
        app.add_handler(MessageHandler(notes_channel_filter(), on_note))
        app.add_handler(CallbackQueryHandler(on_button))
        app.add_handler(CommandHandler("stats", on_stats, filters=review))
        app.add_handler(CommandHandler("log", on_log, filters=review))
        app.add_handler(MessageHandler(review & filters.TEXT & ~filters.COMMAND, on_review_text))
        if not webhook:  # on Vercel, Vercel Cron calls /api/cron instead
            app.job_queue.run_repeating(dispatch_job, interval=3600, first=10)  # picks up the new week's cap
    app.add_handler(CommandHandler("start", on_start, filters=filters.ChatType.PRIVATE))
    app.add_handler(MessageHandler(filters.ALL, on_other))
    app.add_error_handler(on_error)
    return app


def main():
    if not config.TELEGRAM_BOT_TOKEN:
        sys.exit("Setup incomplete: TELEGRAM_BOT_TOKEN is not set")
    problems = check_config()
    for p in problems:
        log.warning("Setup: %s", p)
    if problems:
        log.warning("Running in setup mode: /start replies with your chat id; channel posts only log their chat id.")
    app = build_app(setup_mode=bool(problems))
    log.info("Listening to channel %s, sending drafts to %s, cap %d/week, data in %s",
             config.TELEGRAM_CHAT_ID, config.REVIEW_CHAT_ID, config.WEEKLY_CAP, config.DATA_DIR)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
