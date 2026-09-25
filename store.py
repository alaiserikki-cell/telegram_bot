"""Queue + log.

Two backends, same interface:
- local files (laptop): queue.json, log.csv, approved/*.md next to the code
- Supabase (Vercel, whose filesystem is not persistent): used when SUPABASE_URL is set.
  Tables from supabase_schema.sql, accessed through Supabase's REST API.

Each operation reads and writes one note at a time, so the bot and the backlog CLI can both use it.
"""
import csv
import io
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone

import requests

import config

IST = timezone(timedelta(hours=5, minutes=30))  # Meera is in Mumbai; weeks run Mon-Sun IST
LOG_COLUMNS = ["note_id", "source", "received_at", "verdict", "total_score",
               "drafted_at", "decision", "decided_at", "redo_count"]
STALE_DRAFTING = timedelta(minutes=10)  # a draft interrupted (crash/timeout) goes back to the queue
_lock = threading.Lock()


def now() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def week_start() -> datetime:
    today = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=today.weekday())


def _this_week(ts: str | None) -> bool:
    return bool(ts) and datetime.fromisoformat(ts) >= week_start()

# ------------------------------------------------------------------ backends


def _sb(method: str, table: str, params: dict | None = None, body=None):
    """One call to Supabase's REST API (PostgREST)."""
    headers = {"apikey": config.SUPABASE_KEY, "Authorization": f"Bearer {config.SUPABASE_KEY}"}
    if method == "POST":  # upsert
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    r = requests.request(method, f"{config.SUPABASE_URL}/rest/v1/{table}", params=params, json=body,
                         headers=headers, timeout=15)
    r.raise_for_status()
    return r.json() if r.content else None


def _cloud() -> bool:
    return bool(config.SUPABASE_URL)


def load() -> dict:
    if _cloud():
        return {row["data"]["note_id"]: row["data"] for row in _sb("GET", "notes", {"select": "data"})}
    if not config.QUEUE_FILE.exists():
        return {}
    return json.loads(config.QUEUE_FILE.read_text(encoding="utf-8"))


def _put(rec: dict):
    if _cloud():
        _sb("POST", "notes", body={"note_id": rec["note_id"], "data": rec, "updated_at": now()})
        return
    with _lock:
        notes = load()
        notes[rec["note_id"]] = rec
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = config.QUEUE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(notes, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, config.QUEUE_FILE)
        config.LOG_FILE.write_text(log_csv(notes), encoding="utf-8", newline="")

# ------------------------------------------------------------------ notes


def get(note_id: str) -> dict | None:
    if _cloud():
        rows = _sb("GET", "notes", {"select": "data", "note_id": f"eq.{note_id}"})
        return rows[0]["data"] if rows else None
    return load().get(note_id)


def exists(note_id: str) -> bool:
    return get(note_id) is not None


def update(note_id: str, **fields) -> dict:
    rec = get(note_id)
    rec.update(fields)
    _put(rec)
    return rec


def add(note_id: str, source: str, text: str, scored: dict) -> dict:
    """Record a newly scored note. develop/hold go into the queue; discard/error are only logged."""
    status = "queued" if scored["verdict"] in ("develop", "hold") else scored["verdict"]
    rec = {"note_id": note_id, "source": source, "text": text, "received_at": now(),
           "verdict": scored["verdict"], "total_score": scored.get("total_score", 0), "score": scored,
           "status": status, "drafted_at": "", "decision": "", "decided_at": "", "redo_count": 0}
    _put(rec)
    return rec


def _waiting(n: dict) -> bool:
    if n["status"] == "queued":
        return True
    since = n.get("drafting_since")
    return n["status"] == "drafting" and bool(since) and datetime.now(IST) - datetime.fromisoformat(since) > STALE_DRAFTING


def queue(notes: dict | None = None) -> list[dict]:
    """Waiting notes, best first: 'develop' before 'hold', then by total score."""
    notes = load() if notes is None else notes
    return sorted((n for n in notes.values() if _waiting(n)),
                  key=lambda n: (n["verdict"] != "develop", -n["total_score"], n["received_at"]))


def sent_this_week(notes: dict | None = None) -> int:
    """First drafts sent this week (redos don't count against the cap)."""
    notes = load() if notes is None else notes
    return sum(1 for n in notes.values() if _this_week(n.get("drafted_at")))


def stats() -> dict:
    notes = load()
    vals = list(notes.values())
    return {
        "captured": len(vals),
        "drafted": sum(1 for n in vals if n.get("drafted_at")),
        "approved": sum(1 for n in vals if n.get("decision") == "approved"),
        "approved_week": sum(1 for n in vals if n.get("decision") == "approved" and _this_week(n.get("decided_at"))),
        "sent_week": sent_this_week(notes),
        "queued": len(queue(notes)),
        "awaiting": sum(1 for n in vals if n["status"] == "in_review"),
    }


def log_csv(notes: dict | None = None) -> str:
    notes = load() if notes is None else notes
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=LOG_COLUMNS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for n in sorted(notes.values(), key=lambda n: n["received_at"]):
        w.writerow({k: n.get(k, "") for k in LOG_COLUMNS})
    return buf.getvalue()

# ------------------------------------------------------------------ redo + approved


def set_redo(note_id: str | None):
    """Remember which draft Meera pressed Redo on (survives between serverless requests)."""
    if _cloud():
        if note_id:
            _sb("POST", "kv", body={"key": "redo", "value": note_id})
        else:
            _sb("DELETE", "kv", {"key": "eq.redo"})
    else:
        path = config.DATA_DIR / "redo.txt"
        path.write_text(note_id, encoding="utf-8") if note_id else path.unlink(missing_ok=True)


def pop_redo() -> str | None:
    if _cloud():
        rows = _sb("GET", "kv", {"select": "value", "key": "eq.redo"})
        note_id = rows[0]["value"] if rows else None
    else:
        path = config.DATA_DIR / "redo.txt"
        note_id = path.read_text(encoding="utf-8").strip() if path.exists() else None
    set_redo(None)
    return note_id or None


def save_approved(rec: dict) -> tuple[str, str]:
    """Save the approved draft as YYYY-MM-DD_<slug>.md. Returns (filename, markdown)."""
    result = rec["result"]
    slug = re.sub(r"[^a-z0-9]+", "-", rec["score"].get("core_idea", rec["note_id"]).lower()).strip("-")[:50]
    name = f"{datetime.now(IST):%Y-%m-%d}_{slug}.md"
    n = result.get("news")
    news = f"{n['title']} ({n['source']}, {n['date']}) {n['link']}" if n else "none"
    claims = "\n".join(f"- {c}" for c in result["draft"]["claims_to_check"]) or "- none"
    md = (f"# {rec['score'].get('core_idea', '')}\n\n"
          f"- Note: {rec['note_id']} ({rec['source']})\n- Approved: {now()}\n- News: {news}\n"
          f"- Redos: {rec.get('redo_count', 0)}\n\n## Post\n\n{result['draft']['draft']}\n\n"
          f"## Checked before posting\n\n{claims}\n\n## Source note\n\n{rec['text']}\n")
    if _cloud():
        _sb("POST", "approved", body={"name": name, "markdown": md})
    else:
        config.APPROVED_DIR.mkdir(parents=True, exist_ok=True)
        (config.APPROVED_DIR / name).write_text(md, encoding="utf-8")
    return name, md
