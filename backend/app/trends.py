"""Entity/topic mention tracking: what is rising this week vs last week."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Entity, Story, StoryEntity, utcnow


def _mentions(session: Session, start: datetime, end: datetime) -> dict[int, int]:
    rows = session.execute(
        select(StoryEntity.entity_id, func.count(StoryEntity.story_id))
        .join(Story, Story.id == StoryEntity.story_id)
        .where(Story.first_seen >= start, Story.first_seen < end)
        .group_by(StoryEntity.entity_id)
    ).all()
    return {eid: n for eid, n in rows}


def compute_trends(session: Session, now: datetime | None = None, top_n: int = 12) -> dict:
    now = now or utcnow()
    this_start, last_start = now - timedelta(days=7), now - timedelta(days=14)
    this_week = _mentions(session, this_start, now)
    last_week = _mentions(session, last_start, this_start)
    ids = set(this_week) | set(last_week)
    entities = {e.id: e for e in session.scalars(select(Entity).where(Entity.id.in_(ids)))} if ids else {}

    def row(eid: int) -> dict:
        e = entities[eid]
        cur, prev = this_week.get(eid, 0), last_week.get(eid, 0)
        return {"id": eid, "name": e.name, "kind": e.kind, "this_week": cur, "last_week": prev,
                "delta": cur - prev, "growth": round((cur - prev) / max(prev, 1), 2)}

    rows = [row(eid) for eid in ids if eid in entities]
    rising = sorted((r for r in rows if r["this_week"] >= 2 and r["delta"] > 0),
                    key=lambda r: (r["delta"], r["growth"], r["this_week"]), reverse=True)[:top_n]
    by_kind = defaultdict(list)
    for r in sorted(rows, key=lambda r: r["this_week"], reverse=True):
        if r["this_week"] > 0 and len(by_kind[r["kind"]]) < top_n:
            by_kind[r["kind"]].append(r)

    # daily series for the top entities (14 days) for charts
    top_ids = [r["id"] for r in (by_kind["company"][:5] + by_kind["model"][:5] + by_kind["topic"][:5])]
    days = [(now - timedelta(days=d)).date() for d in range(13, -1, -1)]
    series: dict[int, Counter] = {eid: Counter() for eid in top_ids}
    if top_ids:
        for eid, first_seen in session.execute(
            select(StoryEntity.entity_id, Story.first_seen)
            .join(Story, Story.id == StoryEntity.story_id)
            .where(StoryEntity.entity_id.in_(top_ids), Story.first_seen >= last_start)
        ):
            series[eid][first_seen.date()] += 1
    category_counts = Counter()
    category_last = Counter()
    for cat, first_seen in session.execute(
        select(Story.category, Story.first_seen).where(Story.first_seen >= last_start)
    ):
        (category_counts if first_seen >= this_start else category_last)[cat] += 1

    return {
        "generated_at": now.isoformat(),
        "rising": rising,
        "top_companies": by_kind["company"],
        "top_models": by_kind["model"],
        "top_people": by_kind["person"],
        "top_topics": by_kind["topic"],
        "categories": [{"category": c, "this_week": category_counts[c], "last_week": category_last[c]}
                       for c in sorted(set(category_counts) | set(category_last),
                                       key=lambda c: -category_counts[c])],
        "series": {
            "days": [d.isoformat() for d in days],
            "lines": [{"id": eid, "name": entities[eid].name, "kind": entities[eid].kind,
                       "counts": [series[eid][d] for d in days]} for eid in top_ids if eid in entities],
        },
    }
