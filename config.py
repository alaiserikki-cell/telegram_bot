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
# Meera's private chat with the bot. Drafts go here, never back into the notes channel.
REVIEW_CHAT_ID = os.getenv("REVIEW_CHAT_ID", "")

NEWS_LOOKBACK_DAYS = int(os.getenv("NEWS_LOOKBACK_DAYS", "30"))
WEEKLY_CAP = int(os.getenv("WEEKLY_CAP", "3"))

PUBLISHED_DIR = ROOT / "published"
NOTES_DIR = ROOT / "notes"
# Vercel: webhook mode + Upstash Redis storage (its filesystem doesn't persist).
SERVERLESS = bool(os.getenv("VERCEL"))
REDIS_URL = os.getenv("KV_REST_API_URL") or os.getenv("UPSTASH_REDIS_REST_URL", "")
REDIS_TOKEN = os.getenv("KV_REST_API_TOKEN") or os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # Telegram sends it back on every webhook call
CRON_SECRET = os.getenv("CRON_SECRET", "")        # Vercel Cron sends it as a Bearer token

DATA_DIR = ROOT  # laptop mode: queue.json, log.csv and approved/ live next to the code
APPROVED_DIR = DATA_DIR / "approved"
QUEUE_FILE = DATA_DIR / "queue.json"
LOG_FILE = DATA_DIR / "log.csv"
