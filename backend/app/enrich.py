"""LLM enrichment: paraphrased summary, category, importance, why-it-matters, entities.

Also contains the offline heuristics (category, entities) used before/without an LLM,
and the copy guard that rejects summaries reusing long runs of source text.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import Entity, Story, StoryEntity, utcnow
from .llm import BudgetExceeded, LLMClient

log = logging.getLogger(__name__)

CATEGORIES = [
    "Models & Releases",
    "Research",
    "Agents & Tools",
    "Funding & Business",
    "Policy & Safety",
    "Infrastructure & Chips",
    "Open Source",
]

_CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    ("Funding & Business", re.compile(
        r"\b(rais(es|ed|ing)|funding|series [a-f]|valuation|valued at|acqui(res|red|sition)|ipo|revenue|"
        r"investors?|invests?|investment|layoffs?|hires?|ceo|deal|partnership|earnings|startup|billion|million)\b", re.I)),
    ("Policy & Safety", re.compile(
        r"\b(regulat\w*|laws?|lawmakers?|legislat\w*|ai act|lawsuit|sues?|court|copyright|policy|senate|congress|eu ai act|ban(s|ned)?|"
        r"safety|alignment|misuse|deepfakes?|privacy|security|jailbreak\w*|ethic\w*|governance|government|"
        r"election|risk|executive order)\b", re.I)),
    ("Infrastructure & Chips", re.compile(
        r"\b(gpus?|tpus?|chips?|semiconductor|nvidia|amd|tsmc|data ?cent(er|re)s?|compute|cluster|"
        r"inference (engine|server)|datacenter|h100|h200|b200|blackwell|power grid|energy|cuda|kernels?)\b", re.I)),
    ("Agents & Tools", re.compile(
        r"\b(agents?|agentic|copilot|assistant|plugin|sdk|api|tool(s|ing)?|framework|ide|coding|cli|"
        r"mcp|workflow|automation|browser use|computer use)\b", re.I)),
    ("Open Source", re.compile(
        r"\b(open[- ]source|open[- ]weights?|apache 2\.0|mit license|github|hugging ?face|gguf|ollama|"
        r"llama\.cpp|vllm|released on hf|local(ly)?)\b", re.I)),
    ("Models & Releases", re.compile(
        r"\b(launch(es|ed)?|releas(es|ed|e)|introduc(es|ing|ed)|announc(es|ed)|unveil\w*|rolls? out|"
        r"new model|gpt-?\d|claude|gemini|llama|mistral|qwen|deepseek|grok|model)\b", re.I)),
]


def heuristic_category(text: str, group: str = "news") -> str:
    if group == "research":
        return "Research"
    for cat, pattern in _CATEGORY_RULES:
        if pattern.search(text or ""):
            return cat
    return "Research" if re.search(r"\b(paper|study|researchers?|benchmark|dataset)\b", text or "", re.I) else "Models & Releases"


# ---------------------------------------------------------------- entities

KNOWN_COMPANIES = [
    "OpenAI", "Anthropic", "Google DeepMind", "Google", "DeepMind", "Meta", "Microsoft", "Apple",
    "Amazon", "AWS", "NVIDIA", "AMD", "Intel", "TSMC", "Mistral AI", "Mistral", "Cohere", "xAI",
    "Hugging Face", "Perplexity", "Stability AI", "Midjourney", "Runway", "Character.AI", "Inflection",
    "Databricks", "Snowflake", "Scale AI", "Salesforce", "IBM", "Oracle", "Alibaba", "Baidu",
    "Tencent", "ByteDance", "DeepSeek", "Moonshot AI", "Zhipu", "MiniMax", "Samsung", "Qualcomm",
    "Cerebras", "Groq", "SambaNova", "CoreWeave", "Together AI", "Fireworks AI", "Replit", "Cursor",
    "Anysphere", "GitHub", "LangChain", "LlamaIndex", "Pinecone", "Weaviate", "Ollama", "Figure",
    "Tesla", "Waymo", "Arm", "SoftBank", "Reka", "AI21 Labs", "Allen Institute", "Ai2", "Sakana AI",
]
_COMPANY_ALIASES = {"deepmind": "Google DeepMind", "mistral": "Mistral AI", "aws": "Amazon",
                    "facebook": "Meta", "meta ai": "Meta", "ai2": "Allen Institute", "anysphere": "Cursor",
                    "x.ai": "xAI", "nvidia": "NVIDIA"}
_MODEL_RE = re.compile(
    r"\b(GPT-?\d(?:\.\d)?(?:[- ](?i:o|mini|nano|turbo|pro|sol|luna|astra|codex)\b)*|o\d(?:-mini|-pro)?|"
    r"Claude(?: (?:Opus|Sonnet|Haiku|Fable))?(?: \d(?:\.\d)?)?|Gemini(?: \d(?:\.\d)?)?(?: (?:Pro|Flash|Ultra|Nano))?|"
    r"Llama ?\d(?:\.\d)?|Qwen ?\d(?:\.\d)?(?:-\w+)?|DeepSeek[- ](?:V\d(?:\.\d)?|R\d)|Grok ?\d(?:\.\d)?|"
    r"Mistral (?:Large|Medium|Small)(?: \d)?|Mixtral|Codestral|Gemma ?\d?|Phi-?\d(?:\.\d)?|Sora(?: \d)?|"
    r"Veo ?\d?|Imagen ?\d?|Stable Diffusion(?: \d(?:\.\d)?)?|Kimi(?: K\d)?|Command ?R\+?|Nemotron)\b"
)


def entity_key(name: str) -> str:
    return re.sub(r"[^a-z0-9+.]+", " ", name.lower()).strip()


def heuristic_entities(text: str) -> dict[str, list[str]]:
    companies: list[str] = []
    for c in KNOWN_COMPANIES:
        if re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", text):
            canonical = _COMPANY_ALIASES.get(c.lower(), c)
            if canonical not in companies:
                companies.append(canonical)
    if "Google DeepMind" in companies and "Google" in companies:
        companies.remove("Google")
    models = []
    for m in _MODEL_RE.findall(text):
        m = m.strip()
        if m and m not in models and m.lower() not in ("claude", "gemini", "model"):
            models.append(m)
    return {"companies": companies[:6], "models": models[:6], "people": []}


def set_entities(session: Session, story: Story, entities: dict[str, list[str]], topics: list[str] | None = None) -> None:
    pairs: list[tuple[str, str]] = []
    for kind, names in (("company", entities.get("companies", [])), ("model", entities.get("models", [])),
                        ("person", entities.get("people", [])), ("topic", topics or [])):
        for raw in names or []:
            if not isinstance(raw, str):
                continue
            name = raw.strip()[:120]
            if kind == "company":
                name = _COMPANY_ALIASES.get(name.lower(), name)
            if kind == "topic":
                name = name.lower()
            if len(name) >= 2:
                pairs.append((kind, name))
    story.entities.clear()
    session.flush()
    seen: set[int] = set()
    for kind, name in pairs:
        key = entity_key(name)
        if not key:
            continue
        ent = session.scalar(select(Entity).where(Entity.key == key, Entity.kind == kind))
        if ent is None:
            ent = Entity(name=name, key=key, kind=kind)
            session.add(ent)
            session.flush()
        if ent.id not in seen:
            seen.add(ent.id)
            story.entities.append(StoryEntity(entity_id=ent.id))
    story.tags = [n for k, n in pairs if k == "topic"][:8]


# ---------------------------------------------------------------- copy guard

_WORD_RE = re.compile(r"[a-z0-9']+")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def longest_shared_run(summary: str, sources: list[str]) -> int:
    """Length (in words) of the longest word sequence the summary shares with any source."""
    s = _words(summary)
    best = 0
    for src in sources:
        t = _words(src)
        if not t or not s:
            continue
        prev = [0] * (len(t) + 1)
        for i in range(1, len(s) + 1):
            cur = [0] * (len(t) + 1)
            si = s[i - 1]
            for j in range(1, len(t) + 1):
                if si == t[j - 1]:
                    cur[j] = prev[j - 1] + 1
                    if cur[j] > best:
                        best = cur[j]
            prev = cur
    return best


def long_quotes(summary: str, max_words: int = 15) -> list[str]:
    quotes = re.findall(r"[\"“”]([^\"“”]+)[\"“”]", summary)
    return [q for q in quotes if len(q.split()) > max_words]


def violates_copy_rules(summary: str, sources: list[str], max_words: int = 15) -> bool:
    return bool(long_quotes(summary, max_words)) or longest_shared_run(summary, sources) >= max_words


# ---------------------------------------------------------------- LLM enrichment

SYSTEM_PROMPT = f"""You are the editor of AI Pulse, a daily AI news briefing for applied AI engineers.
For each story you receive (a cluster of headlines + short snippets from different outlets about one event) write:
- "title": a neutral, specific headline in your own words (max 14 words).
- "summary": 2-3 sentences paraphrasing what happened and the key facts. Write it in your own words.
  Never copy sentences from the snippets. Do not quote more than 10 consecutive words from any source.
  If the snippets are thin, say only what can be supported; do not invent numbers or claims.
- "category": exactly one of {json.dumps(CATEGORIES)}.
- "importance": integer 1-5 for an applied AI engineer (5 = changes what you build or use this month,
  e.g. a major frontier model/API release or price change; 3 = notable; 1 = minor/niche).
- "why_it_matters": one line (max 25 words) on why it matters to an applied AI engineer.
- "entities": {{"companies": [...], "models": [...], "people": [...]}} explicitly mentioned (canonical names).
- "topics": 1-4 short lowercase topic tags (e.g. "reasoning", "rag", "ai coding", "robotics", "evals").
Return JSON: {{"stories": [{{"id": <id>, "title": ..., "summary": ..., "category": ..., "importance": ...,
"why_it_matters": ..., "entities": {{...}}, "topics": [...]}}]}} with one entry per input story, same ids."""


def _story_payload(story: Story) -> dict:
    items = sorted(story.items, key=lambda i: i.published_at)[:5]
    return {
        "id": story.id,
        "sources": [
            {"outlet": i.source_name, "headline": i.title, "snippet": (i.snippet or "")[:450]}
            for i in items
        ],
    }


def _match_category(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    for c in CATEGORIES:
        if v == c.lower():
            return c
    for c in CATEGORIES:
        if v.split()[0:1] and v.split()[0] in c.lower():
            return c
    return None


def apply_enrichment(session: Session, story: Story, data: dict, model: str) -> bool:
    """Validate and store one enrichment result. Returns False if rejected."""
    summary = str(data.get("summary") or "").strip()
    if not summary:
        return False
    source_texts = [f"{i.title}. {i.snippet}" for i in story.items]
    if violates_copy_rules(summary, source_texts):
        log.warning("enrich: story %s summary rejected by copy guard", story.id)
        return False
    title = str(data.get("title") or "").strip()
    if title:
        story.title = title[:200]
    story.summary = summary[:1200]
    story.category = _match_category(data.get("category")) or story.category
    try:
        story.importance = max(1, min(5, int(data.get("importance", 2))))
    except (TypeError, ValueError):
        story.importance = 2
    story.why_it_matters = str(data.get("why_it_matters") or "").strip()[:300]
    ents = data.get("entities") if isinstance(data.get("entities"), dict) else {}
    topics = data.get("topics") if isinstance(data.get("topics"), list) else []
    set_entities(session, story, ents, topics)
    story.enriched = True
    story.enriched_model = model
    return True


def heuristic_pass(session: Session, stories: list[Story]) -> None:
    """Give unenriched stories offline entities so search/trends still work."""
    for story in stories:
        if story.enriched or story.entities:
            continue
        text = " ".join(f"{i.title}. {i.snippet}" for i in story.items)
        set_entities(session, story, heuristic_entities(text))


def enrich_stories(session: Session, llm: LLMClient, weights: dict[str, float]) -> dict:
    from .cluster import refresh_story

    settings = get_settings()
    since = utcnow() - timedelta(days=settings.cluster_window_days)
    pending = list(session.scalars(
        select(Story).where(Story.enriched.is_(False), Story.last_seen >= since)
        .order_by(Story.rank_score.desc()).limit(settings.enrich_max_stories_per_run)
    ))
    stats = {"enrich_candidates": len(pending), "enriched": 0, "rejected": 0, "llm_errors": 0,
             "budget_stopped": False}
    heuristic_pass(session, pending)
    session.flush()
    if not llm.available or not pending:
        return stats
    size = max(1, settings.enrich_batch_size)
    for start in range(0, len(pending), size):
        batch = pending[start:start + size]
        user = json.dumps({"stories": [_story_payload(s) for s in batch]}, ensure_ascii=False)
        try:
            out = llm.complete_json(SYSTEM_PROMPT, user, purpose="enrich",
                                    max_output_tokens=min(settings.llm_max_output_tokens, 450 * len(batch) + 300))
        except BudgetExceeded as exc:
            log.warning("enrich: stopping, %s", exc)
            stats["budget_stopped"] = True
            break
        except Exception as exc:
            log.warning("enrich: batch failed: %s", exc)
            stats["llm_errors"] += 1
            continue
        results = out.get("stories", out) if isinstance(out, dict) else out
        by_id = {}
        for r in results if isinstance(results, list) else []:
            if isinstance(r, dict) and "id" in r:
                try:
                    by_id[int(r["id"])] = r
                except (TypeError, ValueError):
                    continue
        for story in batch:
            data = by_id.get(story.id)
            if data and apply_enrichment(session, story, data, llm.settings.llm_model):
                stats["enriched"] += 1
                refresh_story(story, weights)
            else:
                stats["rejected"] += 1
        session.flush()
    return stats
