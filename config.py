"""Settings loaded from .env. Nothing secret lives in code."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# The private channel the bot listens to. Messages from any other chat are ignored.
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# Where drafts and buttons go. Defaults to the notes channel itself (drafts appear as replies under the note).
# Set it to Meera's private chat id to review there instead. Either way it's private; nothing is public.
REVIEW_CHAT_ID = os.getenv("REVIEW_CHAT_ID") or TELEGRAM_CHAT_ID
REVIEW_IN_CHANNEL = REVIEW_CHAT_ID == TELEGRAM_CHAT_ID

NEWS_LOOKBACK_DAYS = int(os.getenv("NEWS_LOOKBACK_DAYS", "30"))
WEEKLY_CAP = int(os.getenv("WEEKLY_CAP", "3"))

PUBLISHED_DIR = ROOT / "published"
NOTES_DIR = ROOT / "notes"
# Vercel: webhook mode + Supabase storage (its filesystem doesn't persist).
SERVERLESS = bool(os.getenv("VERCEL"))
# Names match what the Supabase <-> Vercel integration sets. Use the service-role/secret key, never the anon key.
SUPABASE_URL = (os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")).rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SECRET_KEY", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # Telegram sends it back on every webhook call
CRON_SECRET = os.getenv("CRON_SECRET", "")        # Vercel Cron sends it as a Bearer token

DATA_DIR = ROOT  # laptop mode: queue.json, log.csv and approved/ live next to the code
APPROVED_DIR = DATA_DIR / "approved"
QUEUE_FILE = DATA_DIR / "queue.json"
LOG_FILE = DATA_DIR / "log.csv"
