import json
from datetime import datetime

import pytest

from app.enrich import (
    apply_enrichment,
    heuristic_category,
    heuristic_entities,
    longest_shared_run,
    violates_copy_rules,
)
from app.llm import BudgetExceeded, LLMClient, parse_json


def test_copy_guard_detects_copied_text():
    src = ("The company said on Tuesday that the new model would be available to all developers through "
           "its API starting next week at a lower price than before")
    copied = "The company said on Tuesday that the new model would be available to all developers through its API soon."
    own = "Developers get API access to the new model next week, priced below the previous version."
    assert longest_shared_run(copied, [src]) >= 15
    assert violates_copy_rules(copied, [src])
    assert not violates_copy_rules(own, [src])


def test_copy_guard_rejects_long_quotes():
    s = 'The CEO said "this is the most important release we have ever shipped and it will change everything for everyone".'
    assert violates_copy_rules(s, [])


def test_heuristics():
    assert heuristic_category("Startup raises $50M Series B", "news") == "Funding & Business"
    assert heuristic_category("Anything at all", "research") == "Research"
    assert heuristic_category("EU lawmakers agree AI Act changes", "news") == "Policy & Safety"
    ents = heuristic_entities("OpenAI and Google DeepMind compare GPT-6 Luna with Gemini 3 Pro on NVIDIA GPUs")
    assert ents["companies"] == ["OpenAI", "Google DeepMind", "NVIDIA"]
    assert "GPT-6 Luna" in ents["models"] and "Gemini 3 Pro" in ents["models"]


def test_parse_json_tolerates_fences():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": [1,2]} hope this helps') == {"a": [1, 2]}


def test_llm_budget_cap(db):
    calls = []

    def fake(model, system, user, max_tokens, json_mode):
        calls.append(model)
        return '{"ok": true}', 100, 20

    llm = LLMClient(transport=fake, max_calls=2, max_tokens=10_000)
    assert llm.complete_json("s", "u", purpose="enrich") == {"ok": True}
    llm.complete_json("s", "u", purpose="enrich")
    with pytest.raises(BudgetExceeded):
        llm.complete_json("s", "u", purpose="enrich")
    assert len(calls) == 2 and llm.usage.input_tokens == 200 and llm.usage.output_tokens == 40

    from sqlalchemy import func, select

    from app.db import LLMUsage, session_scope
    with session_scope() as s:
        assert s.scalar(select(func.count(LLMUsage.id))) == 2  # token usage is logged


def test_apply_enrichment_validates(db):
    from app.db import Item, Story, session_scope

    with session_scope() as s:
        st = Story(title="t", first_seen=datetime.utcnow(), last_seen=datetime.utcnow())
        s.add(st)
        s.flush()
        s.add(Item(url="u", url_norm="u", title="Example Labs ships Nova-2", snippet="Nova-2 is out today",
                   source_id="x", source_name="X", published_at=datetime.utcnow(), story_id=st.id))
        s.flush()
        s.refresh(st)
        ok = apply_enrichment(s, st, {
            "title": "Example Labs ships Nova-2", "summary": "Example Labs made Nova-2 available.",
            "category": "models & releases", "importance": 9, "why_it_matters": "New option.",
            "entities": {"companies": ["Example Labs"], "models": ["Nova-2"], "people": []},
            "topics": ["Open Weights"],
        }, model="fake")
        assert ok and st.enriched and st.importance == 5 and st.category == "Models & Releases"
        assert st.tags == ["open weights"]
        assert {se.entity.name for se in st.entities} == {"Example Labs", "Nova-2", "open weights"}
        assert not apply_enrichment(s, st, {"summary": ""}, model="fake")
