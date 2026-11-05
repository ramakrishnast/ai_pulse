from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import select

from app.dedupe import dedupe_new_items, find_near_duplicates, normalize_title
from app.embeddings import HashingEmbedder


def unit(*xs):
    v = np.array(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_find_near_duplicates_against_refs_and_earlier_new_items():
    refs = np.stack([unit(1, 0, 0)])
    new = np.stack([unit(0.99, 0.05, 0), unit(0, 1, 0), unit(0.01, 1, 0), unit(0, 0, 1)])
    out = find_near_duplicates(new, ["a", "b", "c", "d"], refs, ["r"], threshold=0.95)
    assert out == [0, None, 2, None]  # 2 -> first new item (offset by 1 ref) == index 2


def test_title_match_counts_as_duplicate_even_if_vectors_differ():
    refs = np.stack([unit(1, 0)])
    new = np.stack([unit(0, 1)])
    out = find_near_duplicates(new, ["OpenAI launches GPT-6 Luna | TechCrunch"], refs,
                               ["OpenAI launches GPT-6 Luna"], threshold=0.99)
    assert out == [0]


def test_normalize_title():
    assert normalize_title("Show HN: My LLM tool") == "my llm tool"
    assert normalize_title("[R] Sparse Attention!") == "sparse attention"


def test_hashing_embedder_similarity_ordering():
    e = HashingEmbedder()
    a, b, c = e.encode([
        "OpenAI releases GPT-6 Luna, a cheaper model for developers",
        "OpenAI launches GPT-6 Luna, its cheaper model for developers",
        "Court rules on copyright lawsuit against image generator",
    ])
    assert float(a @ b) > 0.6 > float(a @ c)


def test_dedupe_new_items_db(db):
    from app.db import Item, session_scope

    now = datetime.utcnow()
    with session_scope() as s:
        s.add_all([
            Item(url="https://a.com/1", url_norm="https://a.com/1", title="Example Labs releases Nova-2 open model",
                 snippet="Nova-2 weights released under Apache 2.0", source_id="a", source_name="A",
                 published_at=now - timedelta(hours=2)),
            Item(url="https://b.com/1", url_norm="https://b.com/1", title="Example Labs releases Nova-2 open model",
                 snippet="The Nova-2 weights are released under Apache 2.0", source_id="b", source_name="B",
                 published_at=now - timedelta(hours=1)),
            Item(url="https://c.com/1", url_norm="https://c.com/1", title="Regulators propose new AI liability rules",
                 snippet="Draft rules", source_id="c", source_name="C", published_at=now),
        ])
    with session_scope() as s:
        n = dedupe_new_items(s, HashingEmbedder(), threshold=0.8)
    assert n == 1
    with session_scope() as s:
        items = {i.source_id: i for i in s.scalars(select(Item))}
        assert items["b"].duplicate_of_id == items["a"].id
        assert items["a"].duplicate_of_id is None and items["c"].duplicate_of_id is None
