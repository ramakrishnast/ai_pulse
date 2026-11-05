"""Group canonical items about the same event into stories (online centroid clustering)."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Item, Story, utcnow
from .embeddings import Embedder, from_bytes, to_bytes

log = logging.getLogger(__name__)

GROUP_PRIORITY = {"lab": 0, "news": 1, "newsletter": 2, "research": 3, "community": 4}


@dataclass
class ClusterState:
    centroid: np.ndarray
    last_seen: datetime
    size: int = 1
    research_only: bool = False
    members: list[int] = field(default_factory=list)


def assign_clusters(
    vecs: np.ndarray,
    times: list[datetime],
    groups: list[str],
    clusters: list[ClusterState],
    threshold: float,
    research_threshold: float,
    window: timedelta,
) -> list[int]:
    """Assign each vector (sorted oldest-first) to an existing cluster or a new one.

    Mutates/extends ``clusters`` and returns the cluster index for every vector.
    Research papers only merge with other papers when nearly identical
    (``research_threshold``) so that different papers on one topic stay separate.
    """
    out: list[int] = []
    for idx, (vec, t, group) in enumerate(zip(vecs, times, groups)):
        best, best_sim = -1, -1.0
        for ci, c in enumerate(clusters):
            if abs((t - c.last_seen).total_seconds()) > window.total_seconds():
                continue
            sim = float(c.centroid @ vec)
            need = research_threshold if (group == "research" and c.research_only) else threshold
            if sim >= need and sim > best_sim:
                best, best_sim = ci, sim
        if best == -1:
            clusters.append(ClusterState(centroid=vec.copy(), last_seen=t, size=1,
                                         research_only=group == "research", members=[idx]))
            out.append(len(clusters) - 1)
            continue
        c = clusters[best]
        merged = c.centroid * c.size + vec
        norm = np.linalg.norm(merged)
        c.centroid = merged / norm if norm else merged
        c.size += 1
        c.last_seen = max(c.last_seen, t)
        c.research_only = c.research_only and group == "research"
        c.members.append(idx)
        out.append(best)
    return out


def pick_title(items: list[Item]) -> str:
    ordered = sorted(items, key=lambda i: (GROUP_PRIORITY.get(i.source_group, 9), i.published_at))
    return ordered[0].title if ordered else "Untitled"


def rank_score(story: Story, items: list[Item], weights: dict[str, float], now: datetime | None = None) -> float:
    """Blend of LLM importance, coverage breadth, source weight, community points and recency."""
    now = now or utcnow()
    sources = {i.source_id for i in items}
    max_weight = max((weights.get(s, 1.0) for s in sources), default=1.0)
    points = max((i.points or 0 for i in items), default=0)
    age_h = max(0.0, (now - story.last_seen).total_seconds() / 3600)
    recency = math.exp(-age_h / 36)
    importance = story.importance if story.enriched else 2
    return round(
        importance * 2.0
        + math.log2(1 + len(sources)) * 1.5
        + max_weight
        + math.log10(1 + points)
        + recency * 2.0,
        4,
    )


def refresh_story(story: Story, weights: dict[str, float]) -> None:
    items = list(story.items)
    if not items:
        return
    story.first_seen = min(i.published_at for i in items)
    story.last_seen = max(i.published_at for i in items)
    story.source_count = len({i.source_id for i in items})
    if not story.enriched:
        story.title = pick_title(items)
    story.rank_score = rank_score(story, items, weights)


def cluster_new_items(
    session: Session,
    embedder: Embedder,
    threshold: float,
    research_threshold: float,
    window_days: int,
    weights: dict[str, float],
) -> dict:
    from .enrich import heuristic_category  # local import to avoid a cycle

    new_items = list(session.scalars(
        select(Item).where(Item.story_id.is_(None), Item.duplicate_of_id.is_(None))
        .order_by(Item.published_at, Item.id)
    ))
    if not new_items:
        return {"stories_new": 0, "stories_grown": 0}
    window = timedelta(days=window_days)
    since = new_items[0].published_at - window
    existing = list(session.scalars(select(Story).where(Story.last_seen >= since)))
    dim = embedder.dim
    clusters: list[ClusterState] = []
    cluster_story: list[Story | None] = []
    for s in existing:
        vec = from_bytes(s.centroid, dim)
        if vec is None:
            continue
        groups = {i.source_group for i in s.items}
        clusters.append(ClusterState(centroid=vec.copy(), last_seen=s.last_seen, size=max(1, len(s.items)),
                                     research_only=groups == {"research"}))
        cluster_story.append(s)
    n_existing = len(clusters)

    vecs = np.array([from_bytes(i.embedding, dim) for i in new_items], dtype=np.float32).reshape(-1, dim)
    assignment = assign_clusters(vecs, [i.published_at for i in new_items],
                                 [i.source_group for i in new_items], clusters,
                                 threshold, research_threshold, window)

    created: list[Story] = []
    grown: set[int] = set()
    for ci in range(len(clusters)):
        members = [new_items[k] for k, a in enumerate(assignment) if a == ci]
        if not members:
            continue
        story = cluster_story[ci] if ci < n_existing else None
        if story is None:
            first = members[0]
            story = Story(
                title=pick_title(members), first_seen=first.published_at,
                last_seen=first.published_at,
                category=heuristic_category(first.title + " " + first.snippet, first.source_group),
            )
            session.add(story)
            session.flush()
            created.append(story)
        else:
            grown.add(story.id)
        for m in members:
            m.story_id = story.id
        story.centroid = to_bytes(clusters[ci].centroid)
    session.flush()

    # duplicates whose canonical just got a story
    from .dedupe import attach_duplicates_to_stories
    attach_duplicates_to_stories(session)
    session.expire_all()
    for story in session.scalars(select(Story).where(Story.last_seen >= since)):
        refresh_story(story, weights)
    for story in created:
        refresh_story(story, weights)
    session.flush()
    stats = {"stories_new": len(created), "stories_grown": len(grown)}
    log.info("cluster: %s", stats)
    return stats
