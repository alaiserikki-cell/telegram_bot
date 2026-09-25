"""Vercel entrypoint: the same bot in webhook mode.

  POST /api/telegram   Telegram delivers notes, button presses and commands here
  GET  /api/cron       Vercel Cron (daily): sends queued drafts within the weekly cap
  GET  /api/setup      one-time: registers the webhook with Telegram (needs ?key=WEBHOOK_SECRET)
  GET  /               health check (no secrets)

Laptop mode is unchanged: `python main.py` (polling). Don't run both: a registered webhook stops polling.
"""
import hmac

from fastapi import FastAPI, Header, HTTPException, Request
from telegram import Bot, Update

import config
import main
import store

app = FastAPI()


def _same(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a, b)


@app.get("/")
def health():
    problems = main.check_config()
    if not config.SUPABASE_URL or not config.SUPABASE_KEY:
        problems.append("Supabase not configured (SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY)")
    elif not config.SUPABASE_URL.endswith(".supabase.co"):
        problems.append(f"SUPABASE_URL should be your project URL like https://<ref>.supabase.co, got {config.SUPABASE_URL}")
    else:
        try:
            store.load()
        except Exception as e:
            problems.append(f"Supabase not reachable or tables missing (run supabase_schema.sql): {type(e).__name__}")
    if not config.WEBHOOK_SECRET:
        problems.append("WEBHOOK_SECRET is not set")
    return {"ok": not problems, "problems": problems, "model": config.GEMINI_MODEL, "weekly_cap": config.WEEKLY_CAP}


@app.post("/api/telegram")
async def telegram_webhook(request: Request,
                           x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if not _same(x_telegram_bot_api_secret_token or "", config.WEBHOOK_SECRET):
        raise HTTPException(status_code=403)
    ptb = main.build_app(webhook=True)
    await ptb.initialize()
    try:
        await ptb.process_update(Update.de_json(await request.json(), ptb.bot))
    finally:
        await ptb.shutdown()
    return {"ok": True}


@app.get("/api/cron")
async def cron(authorization: str | None = Header(default=None)):
    if not _same(authorization or "", f"Bearer {config.CRON_SECRET}"):
        raise HTTPException(status_code=401)
    async with Bot(config.TELEGRAM_BOT_TOKEN) as bot:
        await main.dispatch(bot, max_drafts=1)
    return {"ok": True}


@app.get("/api/setup")
async def setup(request: Request, key: str = ""):
    if not _same(key, config.WEBHOOK_SECRET):
        raise HTTPException(status_code=403)
    url = f"https://{request.headers['host']}/api/telegram"
    async with Bot(config.TELEGRAM_BOT_TOKEN) as bot:
        await bot.set_webhook(url=url, secret_token=config.WEBHOOK_SECRET,
                              allowed_updates=["channel_post", "message", "callback_query"])
        info = await bot.get_webhook_info()
    return {"ok": True, "webhook": info.url, "pending_updates": info.pending_update_count}
