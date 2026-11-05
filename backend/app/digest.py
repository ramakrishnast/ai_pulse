"""Daily briefing: top-10 'what matters today' plus 3 article ideas based on the day's trends."""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import Digest, Story, utcnow
from .llm import BudgetExceeded, LLMClient
from .trends import compute_trends

log = logging.getLogger(__name__)

DIGEST_PROMPT = """You write the morning briefing of AI Pulse for an applied AI engineer who has 5 minutes.
You get today's candidate stories (already summarized, with ids) and this week's rising entities/topics.
Return JSON:
{
  "headline": "one sentence (max 25 words) capturing the day's overall theme",
  "top": [{"id": <story id>, "line": "one crisp sentence (max 30 words): what happened + why it matters"}],
  "topics": [{"title": "a compelling blog/article title", "angle": "2 sentences: the thesis and what to cover", "story_ids": [ids]}]
}
Rules: "top" has up to 10 entries ordered by importance to an applied AI engineer, only ids from the input,
no duplicates of the same event. "topics" has exactly 3 distinct ideas grounded in today's stories and trends,
useful for a practitioner audience. Write everything in your own words."""


def candidate_stories(session: Session, hours: int, limit: int = 40) -> list[Story]:
    since = utcnow() - timedelta(hours=hours)
    stories = list(session.scalars(
        select(Story).where(Story.last_seen >= since).order_by(Story.rank_score.desc()).limit(limit)
    ))
    if len(stories) < 10:  # quiet day: widen the window
        stories = list(session.scalars(
            select(Story).where(Story.last_seen >= since - timedelta(days=2))
            .order_by(Story.rank_score.desc()).limit(limit)
        ))
    return stories


def _first_sentence(text: str) -> str:
    m = re.match(r"(.+?[.!?])(\s|$)", text or "")
    return (m.group(1) if m else text or "").strip()


def heuristic_digest(stories: list[Story], trends: dict) -> dict:
    def line(s: Story) -> str:
        if s.why_it_matters or s.summary:
            return s.why_it_matters or _first_sentence(s.summary)
        outlets = sorted({i.source_name for i in s.items})
        return f"Covered by {', '.join(outlets[:3])}{' and others' if len(outlets) > 3 else ''}."

    top = [{"story_id": s.id, "line": line(s)} for s in stories[:10]]
    topics = []
    for r in trends.get("rising", [])[:3]:
        related = [s.id for s in stories if any(se.entity_id == r["id"] for se in s.entities)][:4]
        topics.append({
            "title": f"Why everyone is talking about {r['name']} this week",
            "angle": f"{r['name']} appeared in {r['this_week']} stories this week vs {r['last_week']} last week. "
                     "Explain what changed and what it means for teams building with AI.",
            "story_ids": related,
        })
    headline = ""
    if stories:
        cats = {}
        for s in stories[:10]:
            cats[s.category] = cats.get(s.category, 0) + 1
        main = max(cats, key=cats.get)
        n = len(stories)
        headline = (f"{n} {'story' if n == 1 else 'stories'} tracked today; "
                    f"the biggest share is {main.lower()}.")
    return {"headline": headline, "top": top, "topics": topics}


def build_digest(session: Session, date_str: str, llm: LLMClient | None) -> Digest:
    settings = get_settings()
    stories = candidate_stories(session, settings.today_window_hours)
    trends = compute_trends(session)
    result = heuristic_digest(stories, trends)
    model = "heuristic"
    if llm is not None and llm.available and stories:
        payload = {
            "stories": [{
                "id": s.id, "title": s.title, "category": s.category, "importance": s.importance,
                "summary": s.summary or "(no summary)", "why": s.why_it_matters, "source_count": s.source_count,
            } for s in stories],
            "rising_this_week": [{"name": r["name"], "kind": r["kind"], "this_week": r["this_week"],
                                  "last_week": r["last_week"]} for r in trends["rising"][:10]],
        }
        try:
            out = llm.complete_json(DIGEST_PROMPT, json.dumps(payload, ensure_ascii=False), purpose="digest",
                                    model=settings.digest_model, max_output_tokens=2500)
            valid = {s.id for s in stories}
            top, seen = [], set()
            for t in out.get("top", []):
                try:
                    sid = int(t.get("id"))
                except (TypeError, ValueError, AttributeError):
                    continue
                if sid in valid and sid not in seen and t.get("line"):
                    seen.add(sid)
                    top.append({"story_id": sid, "line": str(t["line"])[:300]})
            topics = []
            for t in out.get("topics", [])[:3]:
                if isinstance(t, dict) and t.get("title"):
                    ids = [int(i) for i in t.get("story_ids", []) if str(i).isdigit() and int(i) in valid]
                    topics.append({"title": str(t["title"])[:200], "angle": str(t.get("angle", ""))[:600],
                                   "story_ids": ids})
            if top:
                result = {"headline": str(out.get("headline", ""))[:300], "top": top[:10],
                          "topics": topics or result["topics"]}
                model = settings.digest_model
        except BudgetExceeded as exc:
            log.warning("digest: %s; using heuristic digest", exc)
        except Exception as exc:
            log.warning("digest: LLM failed (%s); using heuristic digest", exc)

    digest = session.scalar(select(Digest).where(Digest.date == date_str)) or Digest(date=date_str)
    digest.headline = result["headline"]
    digest.top = result["top"]
    digest.topics = result["topics"]
    digest.model = model
    digest.created_at = utcnow()
    session.add(digest)
    session.flush()
    return digest
