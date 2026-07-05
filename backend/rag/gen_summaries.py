"""
gen_summaries.py -- one-time (re-runnable) generation of a short, polished
visitor summary for every place, stored in the Chroma metadata as `summary`.

Why: the map popup used to slice raw visitor-review text, which reads messily.
Instead we pre-generate a clean 2-sentence "what it is / what to expect" blurb
per place with the LLM and cache it in the vector store, so the popup can show
a proper summary with zero per-request cost.

Idempotent: skips places that already have a summary unless --force is passed.
Falls back to a deterministic template if the LLM call fails, so every place
ends up with *something* readable.

Usage (from backend/):
    ../venv/bin/python -m rag.gen_summaries          # fill in missing summaries
    ../venv/bin/python -m rag.gen_summaries --force  # regenerate all
"""

from __future__ import annotations
import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

from rag.search import _get_collection  # noqa: E402

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

PROMPT = """You are writing a concise blurb for a trip-planner app.
In EXACTLY 2 sentences (35-50 words total), summarise this Thiruvananthapuram \
place for a first-time visitor.
Sentence 1: what the place is (its type and significance).
Sentence 2: what to expect there -- concrete highlights (architecture, exhibits, \
atmosphere, food, or activities) drawn from the reviews.
Neutral and polished. Do NOT quote the reviews, do NOT use marketing hype, do NOT \
mention dates, months, or ticket prices. Return only the summary text.

Name: {name}
Type: {ctype}
Vibe tags: {vibe}
Visitor reviews:
{reviews}"""


def _descriptive_type(insight: str, fallback: str) -> str:
    for line in insight.splitlines():
        if line.strip().upper().startswith("CATEGORY:"):
            return line.split(":", 1)[1].strip() or fallback
    return fallback


def _fallback_summary(name: str, ctype: str, vibe: str) -> str:
    tags = ", ".join([t.strip() for t in vibe.split(",")[:3] if t.strip()]) or "a local favourite"
    return f"{name} is a {ctype.lower()} in Thiruvananthapuram. Expect a {tags.lower()} experience."


def run(force: bool = False):
    from groq import Groq
    key = os.getenv("GROQ_API_KEY", "")
    if not key:
        raise SystemExit("GROQ_API_KEY not set in backend/.env")
    client = Groq(api_key=key)

    col = _get_collection()
    res = col.get(include=["documents", "metadatas"])
    ids, docs, metas = res["ids"], res["documents"], res["metadatas"]
    total, done, skipped, failed = len(ids), 0, 0, 0

    for i, pid in enumerate(ids):
        meta = dict(metas[i])
        name = meta.get("name", "")
        if meta.get("summary") and not force:
            skipped += 1
            continue

        insight = (docs[i] or "").split("Insight: ", 1)[-1]
        ctype = _descriptive_type(insight, meta.get("category", "attraction"))
        vibe = meta.get("vibe_tags", "")

        summary = ""
        for attempt in range(4):
            try:
                r = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": PROMPT.format(
                        name=name, ctype=ctype, vibe=vibe, reviews=insight[:1400])}],
                    temperature=0.4, max_tokens=130,
                )
                summary = (r.choices[0].message.content or "").strip().strip('"')
                break
            except Exception as e:
                wait = 3 * (attempt + 1)
                msg = str(e).lower()
                if "rate" in msg or "429" in msg or "limit" in msg:
                    print(f"  rate-limited on {name}, waiting {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"  error on {name}: {e}")
                    time.sleep(1)

        if not summary:
            summary = _fallback_summary(name, ctype, vibe)
            failed += 1

        meta["summary"] = summary
        col.update(ids=[pid], metadatas=[meta])
        done += 1
        print(f"[{i+1}/{total}] {name}: {summary[:90]}")
        time.sleep(0.4)  # be gentle on the rate limit

    print(f"\nDone -- {done} written, {skipped} already had one, {failed} used fallback.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="regenerate even if a summary exists")
    run(force=ap.parse_args().force)
