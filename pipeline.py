"""Note -> score -> news -> draft.

Every external call is wrapped; failures come back as data ("error" verdicts, empty news),
never as exceptions, so the bot keeps running.
"""
import json
import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from google import genai
from google.genai import types

import config
import prompts

log = logging.getLogger("skinstinct")

BUZZWORDS = ["glow", "self-love", "self-care", "journey", "game-changer", "game changer", "holy grail",
             "radiant", "pamper", "skin-loving", "unlock", "elevate"]
SCORE_KEYS = ("specificity", "science_depth", "audience_fit", "point_of_view")

# ------------------------------------------------------------------ Gemini

_client = None


def _gemini() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def ask_json(system: str, prompt: str, temperature: float) -> dict | None:
    """One Gemini call expecting JSON. Retries once on invalid JSON or API error, then returns None."""
    cfg = types.GenerateContentConfig(system_instruction=system, temperature=temperature,
                                      response_mime_type="application/json")
    for attempt in (1, 2):
        try:
            resp = _gemini().models.generate_content(model=config.GEMINI_MODEL, contents=prompt, config=cfg)
            data = json.loads(resp.text)
            if isinstance(data, dict):
                return data
            log.warning("Gemini returned non-object JSON (attempt %d)", attempt)
        except json.JSONDecodeError:
            log.warning("Gemini returned invalid JSON (attempt %d)", attempt)
        except Exception as e:
            log.warning("Gemini call failed (attempt %d): %s", attempt, e)
            time.sleep(2)
    return None

# ------------------------------------------------------------------ voice


def load_published() -> list[tuple[str, str]]:
    return [(p.stem, p.read_text(encoding="utf-8").strip()) for p in sorted(config.PUBLISHED_DIR.glob("*.txt"))]


def voice_context() -> str:
    """All 15 published pieces. LinkedIn posts first as the format reference, newsletters for tone."""
    pieces = load_published()
    li = [f"### {n}\n{t}\n" for n, t in pieces if n.startswith("linkedin")]
    nl = [f"### {n}\n{t}\n" for n, t in pieces if n.startswith("newsletter")]
    return ("## MEERA'S LINKEDIN POSTS (primary reference for format, structure and length)\n\n" + "\n".join(li)
            + "\n## MEERA'S NEWSLETTERS (reference for vocabulary and tone only, not format)\n\n" + "\n".join(nl))

# ------------------------------------------------------------------ Step 1: score


def score(note: str) -> dict:
    data = ask_json(prompts.SCORE_SYSTEM, prompts.SCORE_PROMPT.format(note=note), temperature=0.2)
    if not data or data.get("verdict") not in ("develop", "hold", "discard"):
        return {"verdict": "error", "reason": "scoring failed (invalid or no JSON after retry)", "total_score": 0}
    for k in SCORE_KEYS:
        try:
            data[k] = max(1, min(5, int(data.get(k, 1))))
        except (TypeError, ValueError):
            data[k] = 1
    data["total_score"] = sum(data[k] for k in SCORE_KEYS)
    return data

# ------------------------------------------------------------------ Step 2: news


def fetch_news(query: str, limit: int = 5) -> list[dict]:
    """Top Google News RSS items from the last NEWS_LOOKBACK_DAYS. No LLM involved."""
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
           + "&hl=en-IN&gl=IN&ceid=IN:en")
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        log.warning("News fetch failed for %r: %s", query, e)
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.NEWS_LOOKBACK_DAYS)
    items = []
    for e in feed.entries:
        if not getattr(e, "published_parsed", None):
            continue
        published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        if published < cutoff:
            continue
        source = e.get("source", {}).get("title", "")
        title = e.title
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        items.append({"title": title, "source": source, "date": published.strftime("%d %b %Y"), "link": e.link})
        if len(items) == limit:
            break
    return items


def pick_news(core_idea: str, items: list[dict]) -> tuple[dict | None, str]:
    if not items:
        return None, "No relevant news found (no items in the last %d days)" % config.NEWS_LOOKBACK_DAYS
    listing = "\n".join(f"{i}. {it['title']} ({it['source']}, {it['date']})" for i, it in enumerate(items, 1))
    data = ask_json(prompts.NEWS_PICK_SYSTEM,
                    prompts.NEWS_PICK_PROMPT.format(core_idea=core_idea, items=listing), temperature=0)
    if not data:
        return None, "No relevant news found (news selection failed)"
    pick = data.get("pick")
    if isinstance(pick, int) and 1 <= pick <= len(items):
        return items[pick - 1], data.get("reason", "")
    return None, "No relevant news found: " + data.get("reason", "none of the items fit")

# ------------------------------------------------------------------ Step 3: draft


NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}


def _unsourced_numbers(draft: str, sources: str) -> list[str]:
    """Numbers in the draft that appear in neither the note nor the news item and are not marked [VERIFY]."""
    sources += " " + " ".join(d for w, d in NUMBER_WORDS.items() if re.search(r"\b" + w + r"\b", sources, re.I))
    flagged = []
    for sentence in re.split(r"(?<=[.!?])\s+", draft):
        if "[VERIFY]" in sentence:
            continue
        for num in re.findall(r"\d+(?:\.\d+)?%?", sentence):
            if num.rstrip("%") not in sources and num not in flagged:
                flagged.append(num)
    return flagged


def draft(note: str, scored: dict, news: dict | None, instruction: str = "") -> dict:
    news_block = (f"Title: {news['title']}\nSource: {news['source']}\nDate: {news['date']}\nLink: {news['link']}"
                  if news else prompts.NO_NEWS)
    extra = f"\nMEERA'S REDO INSTRUCTION (follow it): {instruction}\n" if instruction else ""
    prompt = prompts.DRAFT_PROMPT.format(voice=voice_context(), note=note, core_idea=scored.get("core_idea", ""),
                                         news=news_block, extra=extra)
    data = ask_json(prompts.DRAFT_SYSTEM, prompt, temperature=0.6)
    if not data or not str(data.get("draft", "")).strip():
        return {"error": "drafting failed (invalid or no JSON after retry)"}
    text = data["draft"].strip()
    claims = [str(c) for c in data.get("claims_to_check") or []]

    # Guardrails the model can't talk its way past.
    sources = note + " " + (news["title"] if news else "")
    for num in _unsourced_numbers(text, sources):
        claims.append(f"Number '{num}' is not in the note or news item and is not marked [VERIFY]")
    hits = [w for w in BUZZWORDS if re.search(r"\b" + re.escape(w) + r"\b", text, re.I)]
    if hits:
        claims.append("Buzzwords to remove: " + ", ".join(hits))
    return {"draft": text, "claims_to_check": claims, "words": len(text.split())}

# ------------------------------------------------------------------ Step 3b: voice score + citations

VOICE_DIMS = ("opening", "register", "stance", "structure", "restraint")


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def _verified(ref: str | None, quote: str | None, pieces: dict[str, str]) -> bool:
    """True only if the quote appears verbatim in the named published piece."""
    return bool(ref and quote and ref in pieces and len(quote.split()) >= 4
                and _norm(quote).strip(" .\"'") in _norm(pieces[ref]))


def voice_score(draft_text: str, claims: list[str]) -> dict:
    pieces = dict(load_published())
    li_words = [len(t.split()) for n, t in pieces.items() if n.startswith("linkedin")]
    paras = [p for p in draft_text.split("\n\n") if p.strip()]
    metrics = {
        "words": len(draft_text.split()),
        "linkedin_range": (min(li_words), max(li_words)),
        "paragraphs": len(paras),
        "verify_tags": draft_text.count("[VERIFY]"),
        "buzzwords": [w for w in BUZZWORDS if re.search(r"\b" + re.escape(w) + r"\b", draft_text, re.I)],
        "emoji": len(re.findall(r"[\U0001F300-\U0001FAFF☀-➿]", draft_text)),
    }
    real_claims = [c for c in claims if not c.startswith(("Number '", "Buzzwords"))]
    data = ask_json(prompts.VOICE_SYSTEM,
                    prompts.VOICE_PROMPT.format(voice=voice_context(), draft=draft_text,
                                                claims="\n".join(f"- {c}" for c in real_claims) or "(none)"),
                    temperature=0)
    if not data or not isinstance(data.get("dimensions"), dict):
        return {"error": "voice scoring failed", "metrics": metrics}

    dims = {}
    for d in VOICE_DIMS:
        v = data["dimensions"].get(d) or {}
        try:
            s = max(1, min(5, int(v.get("score", 1))))
        except (TypeError, ValueError):
            s = 1
        ok = _verified(v.get("ref"), v.get("ref_quote"), pieces)
        dims[d] = {"score": s, "why": v.get("why", ""), "ref": v.get("ref") if ok else None,
                   "ref_quote": v.get("ref_quote") if ok else None}
    # Hard caps the model can't override.
    lo, hi = metrics["linkedin_range"]
    if not (lo * 0.8 <= metrics["words"] <= hi * 1.2):
        dims["structure"]["score"] = min(dims["structure"]["score"], 3)
    if metrics["buzzwords"] or metrics["emoji"]:
        dims["restraint"]["score"] = min(dims["restraint"]["score"], 2)

    cited = []
    for c in data.get("claims") or []:
        ok = _verified(c.get("ref"), c.get("ref_quote"), pieces)
        cited.append({"claim": c.get("claim", ""), "ref": c.get("ref") if ok else None,
                      "ref_quote": c.get("ref_quote") if ok else None})
    return {"dimensions": dims, "total": sum(v["score"] for v in dims.values()),
            "closest_reference": data.get("closest_reference", ""), "top_fix": data.get("top_fix", ""),
            "claims": cited, "metrics": metrics}


def format_voice(v: dict) -> str:
    m = v["metrics"]
    lo, hi = m["linkedin_range"]
    lines = [f"Length {m['words']} words (her LinkedIn posts: {lo}-{hi}) · {m['paragraphs']} paragraphs · "
             f"{m['verify_tags']} [VERIFY] · buzzwords: {', '.join(m['buzzwords']) or 'none'} · emoji: {m['emoji']}"]
    if "error" in v:
        return "\n".join(lines + [v["error"]])
    lines.insert(0, f"Voice match: {v['total']}/25 · closest to {v['closest_reference']}")
    for d in VOICE_DIMS:
        x = v["dimensions"][d]
        ref = f"\n   ↳ cf. {x['ref']}: \"{x['ref_quote']}\"" if x["ref"] else ""
        lines.append(f"• {d} {x['score']}/5: {x['why']}{ref}")
    lines.append(f"Top fix: {v['top_fix']}")
    return "\n".join(lines)


def format_claims(v: dict | None, claims: list[str], citations: dict | None = None) -> str:
    """Claims list with two kinds of citation: Meera's own published pieces and a PubMed paper.
    Neither removes the [VERIFY] tag; they are leads for Meera to check."""
    cited_list = (v or {}).get("claims", [])
    by_text = {c["claim"]: c for c in cited_list}
    real = [c for c in claims if not c.startswith(("Number '", "Buzzwords"))]
    citations = citations or {}
    out = []
    for c in claims:
        if c not in real:
            out.append(f"• {c}")
            continue
        x = by_text.get(c)
        if x is None and len(cited_list) == len(real):  # model reworded; match by position
            x = cited_list[real.index(c)]
        lines = [f"• {c}"]
        lines.append(f"   ↳ Published: {x['ref']}: \"{x['ref_quote']}\"" if x and x["ref"]
                     else "   ↳ Published: not in your published pieces")
        cit = citations.get(c)
        if cit and cit["paper"]:
            p = cit["paper"]
            lines.append(f"   ↳ Source: {p['authors']} ({p['year']}). {p['title']}. {p['journal']}. {p['link']}"
                         f"\n     Abstract: {cit['why']}")
        elif cit:
            lines.append(f"   ↳ Source: {cit['why']}")
        out.append("\n".join(lines))
    return "\n".join(out) or "• nothing flagged"

# ------------------------------------------------------------------ Step 3c: citations (PubMed)

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def fetch_pubmed(query: str, limit: int = 4) -> list[dict]:
    """Real papers from PubMed for a keyword query. No LLM involved."""
    try:
        ids = requests.get(EUTILS + "esearch.fcgi", timeout=15, params={
            "db": "pubmed", "term": query, "retmax": limit, "sort": "relevance", "retmode": "json",
        }).json()["esearchresult"]["idlist"]
        time.sleep(0.4)  # NCBI allows 3 requests/second without an API key
        if not ids:
            return []
        res = requests.get(EUTILS + "esummary.fcgi", timeout=15,
                           params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"}).json()["result"]
        time.sleep(0.4)
    except Exception as e:
        log.warning("PubMed fetch failed for %r: %s", query, e)
        return []
    abstracts = {}
    try:
        xml = requests.get(EUTILS + "efetch.fcgi", timeout=20,
                           params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}).text
        time.sleep(0.4)
        for art in ET.fromstring(xml).iter("PubmedArticle"):
            pmid = art.findtext(".//PMID")
            abstracts[pmid] = " ".join("".join(t.itertext()) for t in art.iter("AbstractText"))
    except Exception as e:
        log.warning("PubMed abstracts failed: %s", e)
    papers = []
    for pmid in ids:
        r = res.get(pmid, {})
        authors = r.get("authors") or []
        papers.append({
            "title": r.get("title", "").rstrip("."),
            "authors": (authors[0]["name"] + (" et al." if len(authors) > 1 else "")) if authors else "",
            "journal": r.get("source", ""),
            "year": (r.get("pubdate") or "")[:4],
            "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "abstract": abstracts.get(pmid, ""),
        })
    return papers


def cite(claims: list[str]) -> dict[str, dict]:
    """Map each claim to a real PubMed paper whose title addresses it, or to None with a reason."""
    real = [c for c in claims if not c.startswith(("Number '", "Buzzwords"))]
    if not real:
        return {}
    q = ask_json(prompts.CITE_QUERY_SYSTEM, "\n".join(f"{i}. {c}" for i, c in enumerate(real, 1)), temperature=0)
    queries = (q or {}).get("queries") or []
    if len(queries) != len(real):
        return {c: {"paper": None, "why": "citation search failed"} for c in real}
    candidates = []
    for query in map(str, queries):
        papers = fetch_pubmed(query)
        if not papers and len(query.split()) > 3:  # broaden once before giving up
            papers = fetch_pubmed(" ".join(query.split()[:3]))
        candidates.append(papers)

    listing = []
    for i, (c, papers) in enumerate(zip(real, candidates), 1):
        listing.append(f"CLAIM {i}: {c}")
        listing += [f"  {j}. {p['title']} ({p['journal']}, {p['year']})\n     Abstract: {p['abstract'][:900] or '(none)'}"
                    for j, p in enumerate(papers, 1)] or ["  (no candidates)"]
    picked = ask_json(prompts.CITE_PICK_SYSTEM, "\n".join(listing), temperature=0) or {}

    out = {c: {"paper": None, "why": "no matching paper found on PubMed"} for c in real}
    for p in picked.get("picks") or []:
        try:
            ci, pi = int(p.get("claim")), p.get("pick")
            claim, papers = real[ci - 1], candidates[ci - 1]
        except (TypeError, ValueError, IndexError):
            continue
        if isinstance(pi, int) and 1 <= pi <= len(papers):
            out[claim] = {"paper": papers[pi - 1], "why": p.get("why", "")}
        elif p.get("why"):
            out[claim]["why"] = "no matching paper: " + p["why"]
    return out

# ------------------------------------------------------------------ full run


def find_news(scored: dict) -> tuple[dict | None, str]:
    query = scored.get("news_query", "")
    items = fetch_news(query)
    if not items and len(query.split()) > 2:  # broaden once before giving up
        items = fetch_news(" ".join(query.split()[:2]))
    return pick_news(scored.get("core_idea", ""), items)


def develop(note: str, scored: dict, instruction: str = "", previous: dict | None = None) -> dict:
    """News + draft + voice score + citations for an already-scored note.
    On a redo, `previous` is the last result: its news item is reused so only the draft changes."""
    result = {"note": note, "score": scored}
    if previous:
        result["news"], result["news_reason"] = previous.get("news"), previous.get("news_reason", "")
    else:
        result["news"], result["news_reason"] = find_news(scored)
    result["draft"] = draft(note, scored, result["news"], instruction)
    if "error" not in result["draft"]:
        result["voice"] = voice_score(result["draft"]["draft"], result["draft"]["claims_to_check"])
        result["citations"] = cite(result["draft"]["claims_to_check"])
    return result


def run(note: str, instruction: str = "") -> dict:
    """Score, and for 'develop' notes develop a draft. Used by the backlog preview."""
    scored = score(note)
    if scored["verdict"] != "develop":
        return {"note": note, "score": scored, "news": None, "news_reason": "", "draft": None}
    return develop(note, scored, instruction)


def format_review(result: dict) -> str:
    """The message sent to Meera's private chat."""
    s = result["score"]
    scores = " ".join(f"{k.split('_')[0]} {s.get(k, '-')}" for k in SCORE_KEYS)
    head = [f"📝 Source note: {result['note'][:200]}{'…' if len(result['note']) > 200 else ''}",
            f"Why picked: {s.get('reason', '')} [{s['verdict']}, {s.get('total_score', 0)}/20: {scores}]"]
    if not result.get("draft"):
        return "\n\n".join(head + ["No draft: this note was not marked 'develop'."])
    n = result["news"]
    head.append(f"News used: {n['title']} ({n['source']}, {n['date']})\n{n['link']}" if n
                else f"News used: none. {result['news_reason']}")
    d = result["draft"]
    if "error" in d:
        return "\n\n".join(head + [f"Draft failed: {d['error']}"])
    v = result.get("voice")
    return "\n\n".join(head + ["---- DRAFT ----", d["draft"],
                               "---- CHECK BEFORE POSTING ----", format_claims(v, d["claims_to_check"], result.get("citations")),
                               "---- VOICE SCORE (vs published/) ----", format_voice(v) if v else "not scored"])
