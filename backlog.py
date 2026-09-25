"""Backlog mode: run the same pipeline over the notes/ folder.

  python backlog.py score [--limit 5]          score notes, print JSON
  python backlog.py news "niacinamide label"   test the Google News fetch
  python backlog.py queue [--limit N]          score notes and add them to the bot's queue (3/week)
  python backlog.py voice draft.txt           voice score + citations for an existing draft
  python backlog.py run [--limit 2] [--only note_001 note_003]
                                               full pipeline, print review messages
"""
import argparse
import json
import logging
import sys

import config
import pipeline
import store

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(format="%(levelname)s %(message)s", level=logging.WARNING)


def load_notes(limit: int | None, only: list[str] | None) -> list[tuple[str, str]]:
    notes = [(p.stem, p.read_text(encoding="utf-8").strip()) for p in sorted(config.NOTES_DIR.glob("*.txt"))]
    if only:
        notes = [n for n in notes if n[0] in only]
    return notes[:limit] if limit else notes


def cmd_score(args):
    for note_id, text in load_notes(args.limit, args.only):
        print(f"== {note_id} ==")
        print(json.dumps(pipeline.score(text), indent=2, ensure_ascii=False))


def cmd_news(args):
    items = pipeline.fetch_news(args.query)
    print(f"{len(items)} items from the last {config.NEWS_LOOKBACK_DAYS} days for {args.query!r}")
    for it in items:
        print(f"- {it['title']} ({it['source']}, {it['date']})\n  {it['link']}")


def cmd_run(args):
    for note_id, text in load_notes(args.limit, args.only):
        print(f"\n{'=' * 70}\n{note_id}\n{'=' * 70}")
        print(pipeline.format_review(pipeline.run(text)))


def cmd_queue(args):
    """Score backlog notes and add them to the bot's queue. The bot sends them within the weekly cap."""
    added = 0
    for note_id, text in load_notes(args.limit, args.only):
        if store.exists(note_id):
            print(f"{note_id}: already in store, skipped")
            continue
        scored = pipeline.score(text)
        rec = store.add(note_id, "backlog", text, scored)
        added += 1
        print(f"{note_id}: {scored['verdict']:<8} {rec['total_score']:>2}/20  -> {rec['status']}  | {scored.get('reason', '')}")
    s = store.stats()
    print(f"\nAdded {added}. Queue: {s['queued']} waiting; {s['sent_week']}/{config.WEEKLY_CAP} drafts sent this week. "
          "The running bot sends from the queue within the hour.")


def cmd_voice(args):
    """Score an existing draft. The file may include a '---- CHECK BEFORE POSTING ----' section of • claims."""
    text = open(args.file, encoding="utf-8").read()
    draft_text, _, rest = text.partition("---- CHECK BEFORE POSTING ----")
    draft_text = draft_text.split("---- DRAFT ----")[-1].strip()
    claims = [l.strip().lstrip("•").strip() for l in rest.splitlines() if l.strip().startswith("•")]
    v = pipeline.voice_score(draft_text, claims)
    print("---- CHECK BEFORE POSTING ----\n" + pipeline.format_claims(v, claims, pipeline.cite(claims)))
    print("\n---- VOICE SCORE (vs published/) ----\n" + pipeline.format_voice(v))


def main():
    ap = argparse.ArgumentParser(description="Process the notes/ backlog (drafts only, never posts)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("score", cmd_score), ("run", cmd_run), ("queue", cmd_queue)):
        p = sub.add_parser(name)
        p.add_argument("--limit", type=int)
        p.add_argument("--only", nargs="*", help="note ids, e.g. note_001")
        p.set_defaults(func=fn)
    p = sub.add_parser("voice", help="score an existing draft file for voice + cite claims")
    p.add_argument("file")
    p.set_defaults(func=cmd_voice)
    p = sub.add_parser("news")
    p.add_argument("query")
    p.set_defaults(func=cmd_news)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
