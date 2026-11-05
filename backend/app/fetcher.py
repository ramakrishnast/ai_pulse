"""Async fetching of every configured source, with timeouts, retries and per-source health."""
from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import Item, SourceStatus, utcnow
from .parsers import (
    RawItem,
    hn_query_params,
    is_ai_related,
    now_utc,
    parse_hf_papers,
    parse_hn,
    parse_reddit,
    parse_rss,
)
from .sources import Source
from .urlnorm import normalize_url

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass
class FetchResult:
    source: Source
    items: list[RawItem] = field(default_factory=list)
    error: str = ""
    status_code: int | None = None

    @property
    def ok(self) -> bool:
        return not self.error


async def _get(client: httpx.AsyncClient, source: Source, params: dict | None = None) -> httpx.Response:
    """GET with retries + exponential backoff. Raises the last error."""
    last_exc: Exception | None = None
    for attempt in range(source.retries + 1):
        try:
            resp = await client.get(source.url, params=params, timeout=source.timeout_seconds)
            if resp.status_code in RETRYABLE_STATUS and attempt < source.retries:
                retry_after = resp.headers.get("retry-after", "")
                delay = float(retry_after) if retry_after.isdigit() else 2 ** attempt
                await asyncio.sleep(min(delay, 15) + random.random())
                continue
            resp.raise_for_status()
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and status not in RETRYABLE_STATUS:
                raise
            if attempt < source.retries:
                await asyncio.sleep(2 ** attempt + random.random())
    assert last_exc is not None
    raise last_exc


def parse_payload(source: Source, body: bytes) -> list[RawItem]:
    if source.type == "rss":
        items = parse_rss(body)
    elif source.type == "hn":
        items = parse_hn(json.loads(body), min_points=source.min_points, ai_filter=source.ai_filter)
    elif source.type == "reddit":
        items = parse_reddit(json.loads(body), min_score=source.min_score)
    elif source.type == "hf_papers":
        items = parse_hf_papers(json.loads(body))
    else:  # pragma: no cover - validated in sources.py
        raise ValueError(f"unknown source type {source.type}")
    return filter_items(source, items)


def filter_items(source: Source, items: list[RawItem]) -> list[RawItem]:
    cutoff = now_utc() - timedelta(days=source.max_age_days)
    out = [i for i in items if i.published_at >= cutoff]
    if source.ai_filter and source.type != "hn":  # hn filtered during parsing
        out = [i for i in out if is_ai_related(i.title, i.snippet)]
    out.sort(key=lambda i: i.published_at, reverse=True)
    return out[: source.max_items]


async def fetch_source(client: httpx.AsyncClient, source: Source, sem: asyncio.Semaphore) -> FetchResult:
    async with sem:
        try:
            params = hn_query_params(source.min_points) if source.type == "hn" else None
            resp = await _get(client, source, params)
            items = parse_payload(source, resp.content)
            log.info("fetched %-22s %3d items", source.id, len(items))
            return FetchResult(source, items, status_code=resp.status_code)
        except httpx.HTTPStatusError as exc:
            msg = f"HTTP {exc.response.status_code}"
            log.warning("fetch %s failed: %s", source.id, msg)
            return FetchResult(source, error=msg, status_code=exc.response.status_code)
        except Exception as exc:  # timeouts, DNS, bad XML/JSON, ...
            msg = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("fetch %s failed: %s", source.id, msg)
            return FetchResult(source, error=msg)


async def fetch_all(sources: list[Source], transport: httpx.AsyncBaseTransport | None = None) -> list[FetchResult]:
    settings = get_settings()
    sem = asyncio.Semaphore(settings.fetch_concurrency)
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "application/rss+xml, application/atom+xml, application/json, text/xml;q=0.9, */*;q=0.5",
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, transport=transport,
                                 timeout=30) as client:
        return await asyncio.gather(*(fetch_source(client, s, sem) for s in sources))


def store_results(session: Session, results: list[FetchResult]) -> dict:
    """Insert new items (skipping URLs we already have) and update per-source health."""
    stats = {"sources_ok": 0, "sources_failed": 0, "items_seen": 0, "items_new": 0, "errors": {}}
    known = set()
    for r in results:
        status = session.get(SourceStatus, r.source.id) or SourceStatus(source_id=r.source.id)
        status.last_attempt = utcnow()
        if r.ok:
            status.last_success = utcnow()
            status.last_error = ""
            status.last_item_count = len(r.items)
            status.consecutive_failures = 0
            stats["sources_ok"] += 1
        else:
            status.last_error = r.error
            status.consecutive_failures = (status.consecutive_failures or 0) + 1
            stats["sources_failed"] += 1
            stats["errors"][r.source.id] = r.error
        session.add(status)

        norms = {normalize_url(i.url): i for i in r.items}
        norms.pop("", None)
        stats["items_seen"] += len(norms)
        if not norms:
            continue
        existing = set(session.scalars(select(Item.url_norm).where(Item.url_norm.in_(list(norms)))))
        for norm, raw in norms.items():
            if norm in existing or norm in known:
                if norm in existing and raw.points:
                    item = session.scalar(select(Item).where(Item.url_norm == norm))
                    if item and raw.points > (item.points or 0):
                        item.points = raw.points
                continue
            known.add(norm)
            session.add(Item(
                url=raw.url, url_norm=norm, title=raw.title, snippet=raw.snippet,
                source_id=r.source.id, source_name=r.source.name, source_group=r.source.group,
                published_at=raw.published_at, points=raw.points, comments_url=raw.comments_url,
            ))
            stats["items_new"] += 1
    session.flush()
    return stats
