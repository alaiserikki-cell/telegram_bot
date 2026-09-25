"""Settings loaded from .env (laptop) or the host's environment variables (Vercel). Nothing secret lives in code."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")


def env(*names: str, default: str = "") -> str:
    """First non-blank value among `names`, stripped. Blank or whitespace-only values count as unset."""
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return default


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, default=str(default)))
    except ValueError:
        return default


GEMINI_API_KEY = env("GEMINI_API_KEY")
GEMINI_MODEL = env("GEMINI_MODEL", default="gemini-3.8-flash")

TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
# The private channel the bot listens to. Messages from any other chat are ignored.
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")
# Drafts and buttons are posted in the private notes channel itself, as replies under each note.
# (Any REVIEW_CHAT_ID left in the environment is ignored.) Nothing is ever posted publicly.
REVIEW_CHAT_ID = TELEGRAM_CHAT_ID
REVIEW_IN_CHANNEL = True

NEWS_LOOKBACK_DAYS = env_int("NEWS_LOOKBACK_DAYS", 30)
WEEKLY_CAP = env_int("WEEKLY_CAP", 3)

PUBLISHED_DIR = ROOT / "published"
NOTES_DIR = ROOT / "notes"
# Vercel: webhook mode + Supabase storage (its filesystem doesn't persist).
SERVERLESS = bool(env("VERCEL"))
# Names match what the Supabase <-> Vercel integration sets. Use the service-role/secret key, never the anon key.
SUPABASE_URL = env("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL").rstrip("/")
SUPABASE_KEY = env("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY")
WEBHOOK_SECRET = env("WEBHOOK_SECRET")  # Telegram sends it back on every webhook call
CRON_SECRET = env("CRON_SECRET")        # Vercel Cron sends it as a Bearer token

DATA_DIR = ROOT  # laptop mode: queue.json, log.csv and approved/ live next to the code
APPROVED_DIR = DATA_DIR / "approved"
QUEUE_FILE = DATA_DIR / "queue.json"
LOG_FILE = DATA_DIR / "log.csv"
