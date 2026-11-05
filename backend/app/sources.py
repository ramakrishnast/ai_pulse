"""Load and validate the source list from sources.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import get_settings

VALID_TYPES = {"rss", "hn", "reddit", "hf_papers"}
VALID_GROUPS = {"lab", "research", "news", "community", "newsletter"}


@dataclass
class Source:
    id: str
    name: str
    type: str
    url: str
    group: str = "news"
    enabled: bool = True
    max_items: int = 30
    ai_filter: bool = False
    weight: float = 1.0
    min_points: int = 0
    min_score: int = 0
    timeout_seconds: int = 20
    retries: int = 2
    max_age_days: int = 7
    note: str = ""
    extra: dict = field(default_factory=dict)


class SourceConfigError(ValueError):
    pass


def parse_sources(data: dict) -> list[Source]:
    defaults = data.get("defaults") or {}
    seen: set[str] = set()
    out: list[Source] = []
    known = set(Source.__dataclass_fields__) - {"extra"}
    for i, raw in enumerate(data.get("sources") or []):
        for key in ("id", "name", "type", "url"):
            if not raw.get(key):
                raise SourceConfigError(f"source #{i} is missing '{key}'")
        if raw["type"] not in VALID_TYPES:
            raise SourceConfigError(f"source {raw['id']}: unknown type {raw['type']!r}")
        if raw["id"] in seen:
            raise SourceConfigError(f"duplicate source id {raw['id']!r}")
        seen.add(raw["id"])
        merged = {**{k: v for k, v in defaults.items() if k in known}, **raw}
        group = merged.get("group", "news")
        if group not in VALID_GROUPS:
            raise SourceConfigError(f"source {raw['id']}: unknown group {group!r}")
        kwargs = {k: v for k, v in merged.items() if k in known}
        kwargs["extra"] = {k: v for k, v in merged.items() if k not in known}
        out.append(Source(**kwargs))
    return out


def load_sources(path: str | None = None, include_disabled: bool = False) -> list[Source]:
    path = path or get_settings().sources_file
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    sources = parse_sources(data)
    return sources if include_disabled else [s for s in sources if s.enabled]
