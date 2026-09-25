# Skinstinct draft assistant

Turns Meera's Telegram notes into LinkedIn **drafts** for her review. It never posts to LinkedIn
or anywhere public; there is no LinkedIn integration. Meera posts herself.

```
note in private channel ──► score (Gemini) ──► queue ──► max 3/week, best first
                                                            │
      Meera's private chat ◄── Approve / Redo / Kill ◄── news (Google News RSS) + draft (Gemini,
                                                          voice = published/) + voice score + citations (PubMed)
```

## Setup

1. Python 3.11+, then `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and fill in `GEMINI_API_KEY` and `TELEGRAM_BOT_TOKEN` (from @BotFather).
3. Add the bot as an **admin** of Meera's private notes channel (needed to see channel posts).
4. Find the chat ids: run `python main.py` (it starts in setup mode while ids are missing).
   - Meera sends `/start` to the bot in a private chat → reply gives `REVIEW_CHAT_ID`.
   - Post anything in the notes channel → console logs `Ignored update from chat -100…` → `TELEGRAM_CHAT_ID`.
5. Restart: `python main.py`

## Using it

**Telegram (Meera):**
- Post a note in the notes channel → she gets `📥 Note captured: develop, 18/20. Queue position 1.`
- Drafts arrive in her private chat (max `WEEKLY_CAP` per week, Mon–Sun IST, highest score first):
  source note, why picked, news used, the draft, claims to check (with citations), voice score.
- **Approve** → saved to `approved/YYYY-MM-DD_<slug>.md` · **Redo** → she replies with one line and gets a new draft ·
  **Kill** → logged and dropped.
- `/stats` → notes captured, drafted, approved, approved this week vs target.

**Backlog (CLI):**
```
python backlog.py score --limit 5        # scores as JSON
python backlog.py run --only note_001    # full pipeline preview in the terminal (not queued, not sent)
python backlog.py queue                  # score notes/ and add them to the bot's queue
python backlog.py news "niacinamide"     # test the Google News fetch
python backlog.py voice draft.txt        # voice score + citations for any draft
```

## Rules the code enforces

- Output only goes to `REVIEW_CHAT_ID`; the bot refuses to start normally if it equals the notes channel.
- Only channel posts from `TELEGRAM_CHAT_ID` are processed; buttons and `/stats` only work in `REVIEW_CHAT_ID`.
- Claims not in the note or the fetched news item are tagged `[VERIFY]`; code also flags unsourced numbers and buzzwords.
- News comes only from a real Google News RSS item from the last 30 days, or the draft says "none".
- Citations: quotes from `published/` are checked verbatim by code; papers are real PubMed records. `[VERIFY]` stays either way.

## Files

| File | Purpose |
|---|---|
| `main.py` | Telegram bot (polling), review gate, pacing, `/stats` |
| `pipeline.py` | score → news → draft → voice score → citations |
| `prompts.py` | every Gemini prompt |
| `store.py` | queue (`queue.json`) and `log.csv` |
| `backlog.py` | CLI for `notes/` |
| `published/` | Meera's 15 pieces, the only voice reference |

## Deploying on Vercel (webhook mode)

`app.py` is the Vercel entrypoint (FastAPI). Telegram pushes updates to `/api/telegram`; Vercel Cron calls
`/api/cron` daily at 09:00 IST to send queued drafts; state lives in Supabase.

1. Supabase: create a project → **SQL Editor** → paste `supabase_schema.sql` → **Run**.
   Then **Project Settings → API**: copy the Project URL and the **service_role / secret** key (not anon).
   (Or connect Supabase from the Vercel Marketplace, which adds these variables for you.)
2. **Settings → Environment Variables**: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`, `REVIEW_CHAT_ID`, `WEEKLY_CAP`, plus two random strings `WEBHOOK_SECRET` and
   `CRON_SECRET` (make one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
3. Redeploy, then open `https://<your-app>.vercel.app/` → should show `"ok": true`.
4. Register the webhook once: open `https://<your-app>.vercel.app/api/setup?key=<WEBHOOK_SECRET>`.
5. Stop any laptop copy (`python main.py`); a registered webhook and polling can't run together.
   To go back to laptop mode: `python -c "import requests,config; print(requests.get(f'https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/deleteWebhook').json())"`

On Vercel, approved posts are sent to Meera as `.md` files in Telegram, and `/log` sends `log.csv`.

## Running it on a laptop

Double-click `start_bot.bat` (or run `python main.py` in this folder). Leave the window open;
the bot works while it's running. Close the window (or Ctrl+C) to stop it.

- If the laptop is off or asleep, Telegram holds new notes for 24 hours and the bot catches up when started.
- `queue.json`, `log.csv` and `approved/` are written in this folder.
- Run only one copy at a time (Telegram allows one connection per bot token).
