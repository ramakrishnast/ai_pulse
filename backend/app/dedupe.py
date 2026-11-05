"""Near-duplicate detection.

Stage 1 (exact) happens at insert time: items are keyed by their normalized URL.
Stage 2 (here): items whose title+snippet embeddings are nearly identical (cosine >=
threshold) or whose normalized titles match are marked as duplicates of an earlier
"canonical" item. Duplicates are kept as extra source links on the canonical's story.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

import numpy as np
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import Item
from .embeddings import Embedder, from_bytes, item_text, to_bytes

log = logging.getLogger(__name__)

_TITLE_NORM_RE = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"^(show hn|ask hn|tell hn|launch hn)\s*:\s*", "", t)
    t = re.sub(r"\s*[\|\-–—:]\s*(techcrunch|the verge|wired|ars technica|venturebeat|the decoder|the register|mit technology review)$", "", t)
    t = re.sub(r"^\[[a-z]{1,3}\]\s*", "", t)  # reddit "[R]", "[D]" prefixes
    return _TITLE_NORM_RE.sub(" ", t).strip()


def find_near_duplicates(
    new_vecs: np.ndarray,
    new_titles: list[str],
    ref_vecs: np.ndarray,
    ref_titles: list[str],
    threshold: float,
) -> list[int | None]:
    """For each new vector return the index of its canonical match, or None.

    Indices < len(ref) point into the reference set; indices >= len(ref) point at an
    earlier *new* item (offset by len(ref)) that was itself kept as canonical. New items
    are processed in order, so pass them sorted oldest-first.
    """
    n_ref = len(ref_titles)
    dim = new_vecs.shape[1] if new_vecs.size else (ref_vecs.shape[1] if ref_vecs.size else 1)
    pool_vecs = list(ref_vecs) if n_ref else []
    pool_titles = [normalize_title(t) for t in ref_titles]
    pool_index = list(range(n_ref))  # pool position -> global index
    title_lookup = {t: i for i, t in enumerate(pool_titles) if t}
    result: list[int | None] = []
    for j, (vec, title) in enumerate(zip(new_vecs, new_titles)):
        nt = normalize_title(title)
        match: int | None = None
        if nt and nt in title_lookup and len(nt) > 12:
            match = title_lookup[nt]
        elif pool_vecs:
            sims = np.asarray(pool_vecs, dtype=np.float32).reshape(-1, dim) @ vec
            best = int(np.argmax(sims))
            if sims[best] >= threshold:
                match = pool_index[best]
        result.append(match)
        if match is None:
            pool_vecs.append(vec)
            pool_index.append(n_ref + j)
            if nt:
                title_lookup.setdefault(nt, n_ref + j)
    return result


def embed_missing(session: Session, embedder: Embedder, items: list[Item]) -> None:
    todo = [i for i in items if from_bytes(i.embedding, embedder.dim) is None]
    if not todo:
        return
    vecs = embedder.encode([item_text(i.title, i.snippet) for i in todo])
    for item, vec in zip(todo, vecs):
        item.embedding = to_bytes(vec)
    session.flush()


def dedupe_new_items(session: Session, embedder: Embedder, threshold: float, window_days: int = 3) -> int:
    """Mark unprocessed items that duplicate an existing or earlier item. Returns count."""
    new_items = list(session.scalars(
        select(Item).where(Item.story_id.is_(None), Item.duplicate_of_id.is_(None))
        .order_by(Item.published_at, Item.id)
    ))
    if not new_items:
        return 0
    since = min(i.published_at for i in new_items) - timedelta(days=window_days)
    refs = list(session.scalars(
        select(Item).where(
            Item.story_id.is_not(None), Item.duplicate_of_id.is_(None), Item.published_at >= since,
        )
    ))
    embed_missing(session, embedder, new_items + refs)
    dim = embedder.dim
    ref_vecs = np.array([from_bytes(r.embedding, dim) for r in refs], dtype=np.float32).reshape(-1, dim)
    new_vecs = np.array([from_bytes(i.embedding, dim) for i in new_items], dtype=np.float32).reshape(-1, dim)
    matches = find_near_duplicates(new_vecs, [i.title for i in new_items], ref_vecs,
                                   [r.title for r in refs], threshold)
    pool = refs + new_items
    count = 0
    for item, match in zip(new_items, matches):
        if match is None:
            continue
        canonical = pool[match]
        while canonical.duplicate_of_id is not None:  # follow chains
            canonical = session.get(Item, canonical.duplicate_of_id)
        if canonical.id == item.id:
            continue
        item.duplicate_of_id = canonical.id
        if canonical.story_id is not None:
            item.story_id = canonical.story_id
        count += 1
    session.flush()
    log.info("dedupe: %d near-duplicates among %d new items", count, len(new_items))
    return count


def attach_duplicates_to_stories(session: Session) -> None:
    """After clustering, give duplicates the story of their canonical item."""
    dups = session.scalars(
        select(Item).where(Item.duplicate_of_id.is_not(None), or_(Item.story_id.is_(None)))
    )
    for d in dups:
        canonical = session.get(Item, d.duplicate_of_id)
        if canonical is not None and canonical.story_id is not None:
            d.story_id = canonical.story_id
    session.flush()
