from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import select

from app.cluster import ClusterState, assign_clusters, cluster_new_items, pick_title
from app.dedupe import dedupe_new_items
from app.embeddings import HashingEmbedder


def unit(*xs):
    v = np.array(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


T0 = datetime(2026, 11, 4, 12)


def test_assign_clusters_groups_similar_vectors():
    vecs = np.stack([unit(1, 0, 0), unit(0.9, 0.1, 0), unit(0, 1, 0), unit(0.1, 0.95, 0)])
    times = [T0 + timedelta(hours=h) for h in range(4)]
    clusters: list[ClusterState] = []
    out = assign_clusters(vecs, times, ["news"] * 4, clusters, threshold=0.8, research_threshold=0.95,
                          window=timedelta(days=3))
    assert out == [0, 0, 1, 1]
    assert clusters[0].size == 2 and abs(np.linalg.norm(clusters[0].centroid) - 1) < 1e-5


def test_assign_clusters_respects_time_window():
    vecs = np.stack([unit(1, 0), unit(1, 0)])
    clusters: list[ClusterState] = []
    out = assign_clusters(vecs, [T0, T0 + timedelta(days=5)], ["news", "news"], clusters,
                          threshold=0.5, research_threshold=0.9, window=timedelta(days=3))
    assert out == [0, 1]


def test_research_papers_need_higher_similarity():
    vecs = np.stack([unit(1, 0.0), unit(1, 0.4)])  # cos ~0.93
    clusters: list[ClusterState] = []
    out = assign_clusters(vecs, [T0, T0], ["research", "research"], clusters, threshold=0.8,
                          research_threshold=0.97, window=timedelta(days=3))
    assert out == [0, 1]
    clusters = []
    out = assign_clusters(vecs, [T0, T0], ["research", "news"], clusters, threshold=0.8,
                          research_threshold=0.97, window=timedelta(days=3))
    assert out == [0, 0]  # a news article about the paper joins it


def test_existing_cluster_is_extended():
    clusters = [ClusterState(centroid=unit(1, 0), last_seen=T0, size=3)]
    out = assign_clusters(np.stack([unit(0.95, 0.05)]), [T0 + timedelta(hours=5)], ["news"], clusters,
                          threshold=0.8, research_threshold=0.9, window=timedelta(days=3))
    assert out == [0] and clusters[0].size == 4 and clusters[0].last_seen == T0 + timedelta(hours=5)


def test_cluster_new_items_db_end_to_end(db):
    from app.db import Item, Story, session_scope

    now = datetime.utcnow()
    rows = [
        ("openai", "lab", "OpenAI releases GPT-6 Luna model for developers", "Cheaper and faster API model"),
        ("verge", "news", "OpenAI launches GPT-6 Luna, a cheaper model for developers", "The new model is cheaper"),
        ("hn", "community", "GPT-6 Luna released by OpenAI for developers", "HN discussion"),
        ("tc", "news", "Robotics startup raises $200M Series C to build humanoid robots", "Funding round led by VCs"),
        ("arxiv", "research", "Sparse attention for long context transformers", "We propose a method"),
    ]
    with session_scope() as s:
        for k, (src, group, title, snip) in enumerate(rows):
            s.add(Item(url=f"https://{src}.com/{k}", url_norm=f"https://{src}.com/{k}", title=title, snippet=snip,
                       source_id=src, source_name=src, source_group=group, published_at=now - timedelta(hours=5 - k)))
    emb = HashingEmbedder()
    with session_scope() as s:
        dedupe_new_items(s, emb, threshold=0.9)
        stats = cluster_new_items(s, emb, threshold=emb.cluster_threshold, research_threshold=0.87, window_days=3,
                                  weights={"openai": 1.5})
    assert stats["stories_new"] == 3
    with session_scope() as s:
        stories = s.scalars(select(Story)).all()
        luna = next(st for st in stories if "Luna" in st.title)
        assert luna.source_count == 3
        assert luna.title == "OpenAI releases GPT-6 Luna model for developers"  # lab source preferred
        funding = next(st for st in stories if "Robotics" in st.title)
        assert funding.category == "Funding & Business"
        assert all(i.story_id is not None for i in s.scalars(select(Item)))


def test_pick_title_prefers_primary_sources():
    from app.db import Item

    a = Item(title="HN title", source_group="community", published_at=T0)
    b = Item(title="Lab title", source_group="lab", published_at=T0 + timedelta(hours=1))
    assert pick_title([a, b]) == "Lab title"
