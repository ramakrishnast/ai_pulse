import json
from datetime import datetime, timedelta

import pytest

from app.fetcher import filter_items
from app.parsers import RawItem, clean_text, is_ai_related, parse_hf_papers, parse_hn, parse_reddit, parse_rss
from app.sources import SourceConfigError, Source, load_sources, parse_sources
from app.urlnorm import normalize_url


def test_parse_rss(fixture_bytes):
    items = parse_rss(fixture_bytes("sample_rss.xml"))
    assert len(items) == 2  # item without link skipped
    first = items[0]
    assert first.title.startswith("Example Labs releases Nova-2")
    assert first.published_at == datetime(2026, 11, 3, 14, 30)
    assert "<" not in first.snippet and "alert" not in first.snippet
    assert "Nova-2 & its weights" in first.snippet


def test_parse_atom_timezones_and_content(fixture_bytes):
    items = parse_rss(fixture_bytes("sample_atom.xml"))
    assert [i.url for i in items] == ["https://blog.example.org/2026/Nov/4/agents/",
                                      "https://blog.example.org/2026/Nov/3/garden/"]
    assert items[1].published_at == datetime(2026, 11, 3, 6, 0)  # +02:00 converted to UTC
    assert items[1].snippet == "Tomatoes and peppers."


def test_parse_rss_rejects_garbage():
    with pytest.raises(ValueError):
        parse_rss(b"<html><body>not a feed</body>")


def test_parse_hn_filters_points_and_keywords(fixture_bytes):
    items = parse_hn(json.loads(fixture_bytes("hn.json")), min_points=50, ai_filter=True)
    titles = [i.title for i in items]
    assert "The history of the bicycle" not in titles
    assert all("OpenAI announces" not in t for t in titles)  # below min points
    ask = next(i for i in items if i.title.startswith("Ask HN"))
    assert ask.url == "https://news.ycombinator.com/item?id=1003"  # self post -> HN link
    assert ask.comments_url == ask.url
    assert items[0].points == 250


def test_parse_reddit(fixture_bytes):
    items = parse_reddit(json.loads(fixture_bytes("reddit.json")), min_score=50)
    assert [i.title for i in items] == ["[R] New paper on sparse attention", "Running a 70B model on two consumer GPUs"]
    assert items[0].url == "https://arxiv.org/abs/2611.00001v2"
    assert items[1].url == "https://www.reddit.com/r/LocalLLaMA/comments/ghi/running/"
    assert "llama.cpp" in items[1].snippet


def test_parse_hf_papers(fixture_bytes):
    items = parse_hf_papers(json.loads(fixture_bytes("hf_papers.json")))
    assert [i.url for i in items] == ["https://huggingface.co/papers/2611.00001", "https://huggingface.co/papers/2611.00002"]
    assert items[0].points == 42


def test_clean_text_truncates_on_word_boundary():
    out = clean_text("word " * 500, limit=50)
    assert len(out) <= 51 and out.endswith("…")


def test_ai_keyword_filter():
    assert is_ai_related("Anthropic ships a new Claude model")
    assert is_ai_related("Fine-tuning LLMs on a budget")
    assert not is_ai_related("The history of the bicycle")
    assert not is_ai_related("Email from Gail about the trail")  # no substring false positives


def test_filter_items_age_and_limit():
    now = datetime.utcnow()
    src = Source(id="s", name="S", type="rss", url="u", max_items=2, max_age_days=3, ai_filter=True)
    items = [RawItem("AI model one", "u1", "", now - timedelta(hours=1)),
             RawItem("AI model two", "u2", "", now - timedelta(hours=2)),
             RawItem("AI model three", "u3", "", now - timedelta(hours=3)),
             RawItem("Old AI news", "u4", "", now - timedelta(days=10)),
             RawItem("Gardening tips", "u5", "", now)]
    assert [i.url for i in filter_items(src, items)] == ["u1", "u2"]


@pytest.mark.parametrize("raw,expected", [
    ("https://www.Example.com/a/b/?utm_source=x&id=2&fbclid=abc#frag", "https://example.com/a/b?id=2"),
    ("http://example.com/a/", "https://example.com/a"),
    ("https://example.com/?b=2&a=1", "https://example.com?a=1&b=2"),
    ("https://arxiv.org/pdf/2611.00001v3.pdf", "https://arxiv.org/abs/2611.00001"),
    ("https://export.arxiv.org/abs/2611.00001v1", "https://arxiv.org/abs/2611.00001"),
    ("https://m.example.com/story/amp", "https://example.com/story"),
    ("https://news.ycombinator.com/item?id=1003", "https://news.ycombinator.com/item?id=1003"),
])
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_sources_yaml_is_valid():
    sources = load_sources(include_disabled=True)
    assert len(sources) >= 30
    assert len({s.id for s in sources}) == len(sources)
    assert {"lab", "research", "news", "community", "newsletter"} <= {s.group for s in sources}


def test_sources_validation():
    with pytest.raises(SourceConfigError):
        parse_sources({"sources": [{"id": "a", "name": "A", "type": "nope", "url": "u"}]})
    with pytest.raises(SourceConfigError):
        parse_sources({"sources": [{"id": "a", "name": "A", "type": "rss", "url": "u"}] * 2})
    out = parse_sources({"defaults": {"max_items": 5}, "sources": [{"id": "a", "name": "A", "type": "rss", "url": "u"}]})
    assert out[0].max_items == 5
