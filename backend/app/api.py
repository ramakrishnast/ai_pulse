"""JSON API consumed by the React frontend."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from . import runner
from .ask import answer
from .config import get_settings
from .db import Digest, Entity, Item, LLMUsage, SourceStatus, Story, StoryEntity, get_session, utcnow
from .embeddings import from_bytes, get_embedder
from .enrich import CATEGORIES
from .llm import LLMClient, budget_status
from .sources import load_sources
from .trends import compute_trends

router = APIRouter(prefix="/api")


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def story_card(s: Story) -> dict:
    seen, sources = set(), []
    for i in sorted(s.items, key=lambda i: i.published_at):
        if i.url_norm in seen:
            continue
        seen.add(i.url_norm)
        sources.append({"source_id": i.source_id, "source": i.source_name, "group": i.source_group,
                        "url": i.url, "title": i.title, "published_at": iso(i.published_at),
                        "points": i.points, "comments_url": i.comments_url})
    entities: dict[str, list[str]] = {"company": [], "model": [], "person": [], "topic": []}
    for se in s.entities:
        entities.setdefault(se.entity.kind, []).append(se.entity.name)
    return {
        "id": s.id, "title": s.title, "summary": s.summary, "category": s.category,
        "importance": s.importance, "why_it_matters": s.why_it_matters, "enriched": s.enriched,
        "first_seen": iso(s.first_seen), "last_seen": iso(s.last_seen), "source_count": s.source_count,
        "rank_score": s.rank_score, "bookmarked": s.bookmarked, "read": s.read_at is not None,
        "entities": entities, "sources": sources,
    }


def _load(q):
    return q.options(selectinload(Story.items), selectinload(Story.entities).selectinload(StoryEntity.entity))


@router.get("/status")
def status(session: Session = Depends(get_session)):
    """The frontend calls this on every page load; it starts today's run if needed."""
    started = runner.ensure_daily_run()
    s = get_settings()
    llm = LLMClient(log_usage=False)
    return {
        "today": runner.local_today(),
        "started_run": started,
        "running": runner.is_running(),
        "has_run_today": runner.has_run_today(),
        "run": runner.current_run(),
        "llm": {"provider": s.llm_provider, "model": s.llm_model, "available": llm.available,
                "budget": budget_status(s)},
        "counts": {"stories": session.scalar(select(func.count(Story.id))),
                   "items": session.scalar(select(func.count(Item.id)))},
        "categories": CATEGORIES,
    }


@router.post("/refresh")
def refresh():
    return {"started": runner.start_background_run("manual"), "running": runner.is_running()}


@router.get("/digest")
def get_digest(day: str | None = Query(None, alias="date"), session: Session = Depends(get_session)):
    q = select(Digest).order_by(Digest.date.desc()).limit(1) if not day else select(Digest).where(Digest.date == day)
    d = session.scalar(q)
    if d is None:
        return {"digest": None}
    ids = [t["story_id"] for t in d.top]
    stories = {s.id: s for s in session.scalars(_load(select(Story).where(Story.id.in_(ids))))} if ids else {}
    return {"digest": {
        "date": d.date, "headline": d.headline, "model": d.model, "created_at": iso(d.created_at),
        "top": [{"line": t["line"], "story": story_card(stories[t["story_id"]])}
                for t in d.top if t["story_id"] in stories],
        "topics": d.topics,
    }}


def _parse_day(value: str | None, end: bool = False) -> datetime | None:
    if not value:
        return None
    d = date.fromisoformat(value)
    return datetime.combine(d, time.max if end else time.min)


@router.get("/stories")
def list_stories(
    category: str | None = None,
    source: str | None = None,
    q: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_importance: int = 0,
    sort: str = "rank",  # rank | importance | recency
    scope: str = "today",  # today | all
    bookmarked: bool = False,
    unread: bool = False,
    limit: int = Query(150, le=500),
    offset: int = 0,
    session: Session = Depends(get_session),
):
    stmt = select(Story)
    if scope == "today" and not (date_from or date_to or q or bookmarked):
        stmt = stmt.where(Story.last_seen >= utcnow() - timedelta(hours=get_settings().today_window_hours))
    if category:
        stmt = stmt.where(Story.category == category)
    if min_importance:
        stmt = stmt.where(Story.importance >= min_importance)
    if bookmarked:
        stmt = stmt.where(Story.bookmarked.is_(True))
    if unread:
        stmt = stmt.where(Story.read_at.is_(None))
    if df := _parse_day(date_from):
        stmt = stmt.where(Story.last_seen >= df)
    if dt := _parse_day(date_to, end=True):
        stmt = stmt.where(Story.first_seen <= dt)
    if source:
        stmt = stmt.where(Story.id.in_(select(Item.story_id).where(Item.source_id == source)))
    if q:
        for term in q.split()[:6]:
            like = f"%{term}%"
            stmt = stmt.where(or_(
                Story.title.ilike(like), Story.summary.ilike(like), Story.why_it_matters.ilike(like),
                Story.id.in_(select(Item.story_id).where(or_(Item.title.ilike(like), Item.snippet.ilike(like)))),
                Story.id.in_(select(StoryEntity.story_id).join(Entity).where(Entity.name.ilike(like))),
            ))
    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    order = {
        "importance": (Story.importance.desc(), Story.rank_score.desc()),
        "recency": (Story.last_seen.desc(),),
    }.get(sort, (Story.rank_score.desc(),))
    stories = session.scalars(_load(stmt.order_by(*order).limit(limit).offset(offset))).all()
    return {"total": total, "stories": [story_card(s) for s in stories]}


@router.get("/stories/{story_id}")
def get_story(story_id: int, session: Session = Depends(get_session)):
    story = session.scalar(_load(select(Story).where(Story.id == story_id)))
    if story is None:
        raise HTTPException(404, "story not found")
    return {"story": story_card(story), "related": related_stories(session, story)}


def related_stories(session: Session, story: Story, limit: int = 6) -> list[dict]:
    """Rank nearby stories by text similarity plus shared companies/models/topics."""
    embedder = get_embedder()
    vec = from_bytes(story.centroid, embedder.dim)
    my_entities = {se.entity_id for se in story.entities}
    window = timedelta(days=14)
    others = session.scalars(_load(select(Story).where(
        Story.id != story.id, Story.last_seen >= story.last_seen - window,
        Story.last_seen <= story.last_seen + window)))
    scored = []
    for o in others:
        ov = from_bytes(o.centroid, embedder.dim)
        sim = float(np.dot(vec, ov)) if vec is not None and ov is not None else 0.0
        shared = len(my_entities & {se.entity_id for se in o.entities})
        score = sim + 0.15 * min(shared, 3) + (0.05 if o.category == story.category else 0.0)
        if sim > 0.2 or shared:
            scored.append((score, o))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"id": o.id, "title": o.title, "category": o.category, "importance": o.importance,
             "last_seen": iso(o.last_seen), "score": round(s, 3)} for s, o in scored[:limit]]


class FlagBody(BaseModel):
    value: bool = True


@router.post("/stories/{story_id}/bookmark")
def bookmark(story_id: int, body: FlagBody, session: Session = Depends(get_session)):
    story = session.get(Story, story_id) or _404()
    story.bookmarked = body.value
    return {"id": story_id, "bookmarked": story.bookmarked}


@router.post("/stories/{story_id}/read")
def mark_read(story_id: int, body: FlagBody, session: Session = Depends(get_session)):
    story = session.get(Story, story_id) or _404()
    story.read_at = utcnow() if body.value else None
    return {"id": story_id, "read": story.read_at is not None}


def _404():
    raise HTTPException(404, "story not found")


@router.get("/trends")
def trends(session: Session = Depends(get_session)):
    return compute_trends(session)


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@router.post("/ask")
def ask(body: AskBody, session: Session = Depends(get_session)):
    return answer(session, body.question)


@router.get("/sources")
def sources(session: Session = Depends(get_session)):
    statuses = {st.source_id: st for st in session.scalars(select(SourceStatus))}
    counts = dict(session.execute(select(Item.source_id, func.count(Item.id)).group_by(Item.source_id)).all())
    out = []
    for src in load_sources(include_disabled=True):
        st = statuses.get(src.id)
        out.append({
            "id": src.id, "name": src.name, "type": src.type, "group": src.group, "url": src.url,
            "enabled": src.enabled, "note": src.note, "total_items": counts.get(src.id, 0),
            "last_success": iso(st.last_success) if st else None,
            "last_attempt": iso(st.last_attempt) if st else None,
            "last_error": st.last_error if st else "",
            "last_item_count": st.last_item_count if st else 0,
        })
    return {"sources": out}


@router.get("/usage")
def usage(session: Session = Depends(get_session)):
    rows = session.execute(
        select(LLMUsage.run_id, LLMUsage.purpose, LLMUsage.model, func.count(LLMUsage.id),
               func.sum(LLMUsage.input_tokens), func.sum(LLMUsage.output_tokens))
        .group_by(LLMUsage.run_id, LLMUsage.purpose, LLMUsage.model)
        .order_by(LLMUsage.run_id.desc()).limit(60)
    ).all()
    return {"usage": [{"run_id": r[0], "purpose": r[1], "model": r[2], "calls": r[3],
                       "input_tokens": r[4] or 0, "output_tokens": r[5] or 0} for r in rows]}
