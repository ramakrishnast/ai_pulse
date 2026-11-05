"""Turn raw feed/API payloads into RawItem records. Pure functions: easy to unit-test."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser
from dateutil import parser as dateparser

SNIPPET_MAX = 600

AI_KEYWORDS = re.compile(
    r"\b("
    r"ai|a\.i\.|artificial intelligence|machine learning|ml|deep learning|neural|llms?|gpt[-\w.]*|"
    r"chatgpt|openai|anthropic|claude|gemini|deepmind|llama|mistral|mixtral|qwen|deepseek|grok|xai|"
    r"copilot|transformers?|diffusion|genai|generative|foundation models?|language models?|"
    r"embeddings?|rag|agents?|agentic|inference|fine[- ]?tun\w*|rlhf|reasoning models?|"
    r"multimodal|hugging ?face|nvidia|gpus?|tpus?|cuda|pytorch|tensorflow|jax|vllm|ollama|"
    r"stable diffusion|midjourney|sora|perplexity|cohere|alignment|ai safety|chatbots?|"
    r"mcp|model context protocol|vector databases?|lora|quantiz\w*"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class RawItem:
    title: str
    url: str
    snippet: str
    published_at: datetime  # naive UTC
    points: int = 0
    comments_url: str | None = None


def utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(text: str | None, limit: int = SNIPPET_MAX) -> str:
    """Strip HTML, unescape entities, collapse whitespace, truncate on a word boundary."""
    if not text:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def parse_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
    if isinstance(value, tuple):  # feedparser struct_time (UTC)
        try:
            return datetime(*value[:6])
        except (TypeError, ValueError):
            return None
    try:
        return utc_naive(parsedate_to_datetime(value))
    except (TypeError, ValueError, IndexError):
        pass
    try:
        dt = dateparser.parse(value)
        return utc_naive(dt) if dt else None
    except (ValueError, OverflowError):
        return None


def is_ai_related(*texts: str) -> bool:
    return any(t and AI_KEYWORDS.search(t) for t in texts)


# ---------------------------------------------------------------- RSS / Atom


def parse_rss(content: bytes | str) -> list[RawItem]:
    """Parse an RSS 2.0 / Atom / RDF feed. Raises ValueError if it is not a feed at all."""
    feed = feedparser.parse(content)
    if not feed.entries and (feed.bozo or not feed.get("version")):
        raise ValueError(f"not a valid feed: {feed.get('bozo_exception') or 'no RSS/Atom content'!r}")
    items: list[RawItem] = []
    fetched = now_utc()
    for e in feed.entries:
        title = clean_text(e.get("title"), 300)
        link = e.get("link") or ""
        if not link:
            for lnk in e.get("links", []):
                if lnk.get("href"):
                    link = lnk["href"]
                    break
        if not title or not link:
            continue
        summary = e.get("summary") or ""
        if not summary and e.get("content"):
            summary = e["content"][0].get("value", "")
        published = (
            parse_date(e.get("published_parsed"))
            or parse_date(e.get("updated_parsed"))
            or parse_date(e.get("published"))
            or parse_date(e.get("updated"))
            or fetched
        )
        # Some feeds publish future dates; clamp them.
        published = min(published, fetched)
        comments = e.get("comments") if isinstance(e.get("comments"), str) else None
        items.append(RawItem(title=title, url=link.strip(), snippet=clean_text(summary),
                             published_at=published, comments_url=comments))
    return items


# ---------------------------------------------------------------- Hacker News (Algolia)


def hn_query_params(min_points: int, hours: int = 36, hits: int = 200) -> dict:
    since = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    return {
        "tags": "story",
        "numericFilters": f"created_at_i>{since},points>={min_points}",
        "hitsPerPage": str(hits),
    }


def parse_hn(payload: dict, min_points: int = 0, ai_filter: bool = True) -> list[RawItem]:
    items: list[RawItem] = []
    for hit in payload.get("hits", []):
        title = clean_text(hit.get("title") or hit.get("story_title"), 300)
        if not title:
            continue
        points = int(hit.get("points") or 0)
        if points < min_points:
            continue
        story_text = clean_text(hit.get("story_text") or "")
        if ai_filter and not is_ai_related(title, story_text):
            continue
        hn_url = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        url = hit.get("url") or hn_url
        published = parse_date(hit.get("created_at_i")) or parse_date(hit.get("created_at")) or now_utc()
        snippet = story_text or f"Hacker News discussion ({points} points, {hit.get('num_comments') or 0} comments)."
        items.append(RawItem(title=title, url=url, snippet=snippet, published_at=published,
                             points=points, comments_url=hn_url))
    return items


# ---------------------------------------------------------------- Reddit (public JSON)


def parse_reddit(payload: dict, min_score: int = 0) -> list[RawItem]:
    items: list[RawItem] = []
    for child in (payload.get("data") or {}).get("children", []):
        d = child.get("data") or {}
        if d.get("stickied") or d.get("over_18"):
            continue
        score = int(d.get("score") or 0)
        if score < min_score:
            continue
        title = clean_text(d.get("title"), 300)
        if not title:
            continue
        permalink = "https://www.reddit.com" + (d.get("permalink") or "")
        url = d.get("url_overridden_by_dest") or d.get("url") or permalink
        if d.get("is_self") or "reddit.com/gallery" in url or "redd.it" in url:
            url = permalink
        snippet = clean_text(d.get("selftext") or "") or f"Reddit discussion in r/{d.get('subreddit')} ({score} upvotes)."
        published = parse_date(d.get("created_utc")) or now_utc()
        items.append(RawItem(title=title, url=url, snippet=snippet, published_at=published,
                             points=score, comments_url=permalink))
    return items


# ---------------------------------------------------------------- Hugging Face Daily Papers


def parse_hf_papers(payload: list) -> list[RawItem]:
    items: list[RawItem] = []
    for entry in payload or []:
        paper = entry.get("paper") or {}
        pid = paper.get("id") or entry.get("id")
        title = clean_text(paper.get("title") or entry.get("title"), 300)
        if not pid or not title:
            continue
        published = (
            parse_date(entry.get("publishedAt"))
            or parse_date(paper.get("publishedAt"))
            or now_utc()
        )
        items.append(RawItem(
            title=title,
            url=f"https://huggingface.co/papers/{pid}",
            snippet=clean_text(paper.get("summary") or entry.get("summary")),
            published_at=published,
            points=int(paper.get("upvotes") or 0),
        ))
    return items
