"""Queue + log. State lives in queue.json; log.csv is regenerated from it on every save.

Each operation loads from disk and saves back, so the bot and the backlog CLI can both use it.
"""
import csv
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone

import config

IST = timezone(timedelta(hours=5, minutes=30))  # Meera is in Mumbai; weeks run Mon-Sun IST
LOG_COLUMNS = ["note_id", "source", "received_at", "verdict", "total_score",
               "drafted_at", "decision", "decided_at", "redo_count"]
_lock = threading.Lock()


def now() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def week_start() -> datetime:
    today = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=today.weekday())


def _this_week(ts: str | None) -> bool:
    return bool(ts) and datetime.fromisoformat(ts) >= week_start()


def load() -> dict:
    if not config.QUEUE_FILE.exists():
        return {}
    return json.loads(config.QUEUE_FILE.read_text(encoding="utf-8"))


def _save(notes: dict):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.QUEUE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(notes, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, config.QUEUE_FILE)
    with open(config.LOG_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for n in sorted(notes.values(), key=lambda n: n["received_at"]):
            w.writerow({k: n.get(k, "") for k in LOG_COLUMNS})


def update(note_id: str, **fields) -> dict:
    with _lock:
        notes = load()
        notes[note_id].update(fields)
        _save(notes)
        return notes[note_id]


def get(note_id: str) -> dict | None:
    return load().get(note_id)


def add(note_id: str, source: str, text: str, scored: dict) -> dict:
    """Record a newly scored note. develop/hold go into the queue; discard/error are only logged."""
    status = "queued" if scored["verdict"] in ("develop", "hold") else scored["verdict"]
    rec = {"note_id": note_id, "source": source, "text": text, "received_at": now(),
           "verdict": scored["verdict"], "total_score": scored.get("total_score", 0), "score": scored,
           "status": status, "drafted_at": "", "decision": "", "decided_at": "", "redo_count": 0}
    with _lock:
        notes = load()
        notes[note_id] = rec
        _save(notes)
    return rec


def exists(note_id: str) -> bool:
    return note_id in load()


def queue() -> list[dict]:
    """Waiting notes, best first: 'develop' before 'hold', then by total score."""
    waiting = [n for n in load().values() if n["status"] == "queued"]
    return sorted(waiting, key=lambda n: (n["verdict"] != "develop", -n["total_score"], n["received_at"]))


def sent_this_week() -> int:
    """First drafts sent this week (redos don't count against the cap)."""
    return sum(1 for n in load().values() if _this_week(n.get("drafted_at")))


def stats() -> dict:
    notes = list(load().values())
    return {
        "captured": len(notes),
        "drafted": sum(1 for n in notes if n.get("drafted_at")),
        "approved": sum(1 for n in notes if n.get("decision") == "approved"),
        "approved_week": sum(1 for n in notes if n.get("decision") == "approved" and _this_week(n.get("decided_at"))),
        "sent_week": sent_this_week(),
        "queued": len(queue()),
        "awaiting": sum(1 for n in notes if n["status"] == "in_review"),
    }


def save_approved(rec: dict) -> str:
    """Write the approved draft to approved/YYYY-MM-DD_<slug>.md and return the path."""
    config.APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    result = rec["result"]
    slug = re.sub(r"[^a-z0-9]+", "-", rec["score"].get("core_idea", rec["note_id"]).lower()).strip("-")[:50]
    path = config.APPROVED_DIR / f"{datetime.now(IST):%Y-%m-%d}_{slug}.md"
    n = result.get("news")
    news = f"{n['title']} ({n['source']}, {n['date']}) {n['link']}" if n else "none"
    claims = "\n".join(f"- {c}" for c in result["draft"]["claims_to_check"]) or "- none"
    path.write_text(
        f"# {rec['score'].get('core_idea', '')}\n\n"
        f"- Note: {rec['note_id']} ({rec['source']})\n- Approved: {now()}\n- News: {news}\n"
        f"- Redos: {rec.get('redo_count', 0)}\n\n## Post\n\n{result['draft']['draft']}\n\n"
        f"## Checked before posting\n\n{claims}\n\n## Source note\n\n{rec['text']}\n",
        encoding="utf-8")
    return str(path)
