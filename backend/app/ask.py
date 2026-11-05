"""'Ask the news': retrieval over stored stories + an LLM answer with numbered citations."""
from __future__ import annotations

import logging
import re
from datetime import timedelta

import numpy as np
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import Item, Story, utcnow
from .embeddings import from_bytes, get_embedder
from .llm import BudgetExceeded, LLMClient, LLMUnavailable

log = logging.getLogger(__name__)

ASK_PROMPT = """You answer questions about recent AI news using ONLY the numbered stories provided.
- Cite every claim with the story number in square brackets, e.g. [2] or [1][4].
- If the stories do not contain the answer, say so plainly; do not use outside knowledge.
- Be concise (under 180 words), paraphrase, and never quote more than 15 words."""

_WORD = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9.\-+]{2,}")


def retrieve(session: Session, question: str, k: int = 10, days: int = 30) -> list[Story]:
    since = utcnow() - timedelta(days=days)
    stories = list(session.scalars(select(Story).where(Story.last_seen >= since)))
    if not stories:
        return []
    embedder = get_embedder()
    qv = embedder.encode([question])[0]
    scores: dict[int, float] = {}
    for s in stories:
        v = from_bytes(s.centroid, embedder.dim)
        scores[s.id] = float(v @ qv) if v is not None else 0.0
    words = [w.lower() for w in _WORD.findall(question)][:8]
    if words:
        conds = [Story.title.ilike(f"%{w}%") | Story.summary.ilike(f"%{w}%") for w in words]
        for sid in session.scalars(select(Story.id).where(Story.last_seen >= since, or_(*conds))):
            scores[sid] = scores.get(sid, 0) + 0.15
    by_id = {s.id: s for s in stories}
    ranked = sorted(scores, key=lambda sid: (scores[sid], by_id[sid].rank_score), reverse=True)
    return [by_id[sid] for sid in ranked[:k] if scores[sid] > 0.05]


def _links(story: Story) -> list[dict]:
    seen, out = set(), []
    for i in sorted(story.items, key=lambda i: i.published_at):
        if i.url_norm in seen:
            continue
        seen.add(i.url_norm)
        out.append({"source": i.source_name, "url": i.url, "title": i.title})
    return out


def answer(session: Session, question: str, llm: LLMClient | None = None) -> dict:
    question = question.strip()[:500]
    stories = retrieve(session, question)
    citations = [{"n": n, "story_id": s.id, "title": s.title, "date": s.last_seen.date().isoformat(),
                  "links": _links(s)} for n, s in enumerate(stories, 1)]
    if not stories:
        return {"answer": "I couldn't find any stored stories about that yet.", "citations": [], "llm": False}
    llm = llm or LLMClient(max_calls=1, max_tokens=60_000)
    if not llm.available:
        return {"answer": "No LLM is configured, so here are the most relevant stored stories.",
                "citations": citations, "llm": False}
    context = "\n\n".join(
        f"[{n}] {s.title} ({s.last_seen.date()}, {s.category}, {s.source_count} sources)\n"
        f"{s.summary or ' / '.join(i.title for i in s.items[:4])}"
        for n, s in enumerate(stories, 1)
    )
    try:
        text = llm.complete(ASK_PROMPT, f"Stories:\n{context}\n\nQuestion: {question}", purpose="ask",
                            model=get_settings().chat_model, max_output_tokens=900)
    except BudgetExceeded as exc:
        log.warning("ask skipped: %s", exc)
        return {"answer": "The LLM spending cap has been reached, so here are the most relevant stored stories.",
                "citations": citations, "llm": False}
    except (LLMUnavailable, Exception) as exc:
        log.warning("ask failed: %s", exc)
        return {"answer": f"The LLM call failed ({type(exc).__name__}); here are the most relevant stories.",
                "citations": citations, "llm": False}
    used = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
    return {"answer": text.strip(), "citations": [c for c in citations if c["n"] in used] or citations,
            "llm": True}
