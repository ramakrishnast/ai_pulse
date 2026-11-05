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


def test_lab_blog_posts_are_tagged_with_the_lab(db):
    from app.db import Item, Story, session_scope
    from app.enrich import heuristic_pass, lab_companies

    with session_scope() as s:
        st = Story(title="t", first_seen=datetime.utcnow(), last_seen=datetime.utcnow())
        s.add(st)
        s.flush()
        s.add(Item(url="u2", url_norm="u2", title="Strengthening our defenses", snippet="",
                   source_id="anthropic-research", source_name="Anthropic Research", source_group="lab",
                   published_at=datetime.utcnow(), story_id=st.id))
        s.add(Item(url="u3", url_norm="u3", title="Strengthening defenses", snippet="",
                   source_id="hn", source_name="Hacker News", source_group="community",
                   published_at=datetime.utcnow(), story_id=st.id))
        s.flush()
        s.refresh(st)
        assert lab_companies(st) == ["Anthropic"]
        heuristic_pass(s, [st])
        assert [se.entity.name for se in st.entities] == ["Anthropic"]


def test_dollar_budget_and_model_guard(db, monkeypatch):
    from app import config
    from app.llm import LLMClient, LLMUnavailable, budget_status, ledger

    monkeypatch.setenv("LLM_PRICE_INPUT_PER_MTOK", "1.0")
    monkeypatch.setenv("LLM_PRICE_OUTPUT_PER_MTOK", "10.0")
    monkeypatch.setenv("LLM_BUDGET_USD_TOTAL", "0.05")
    monkeypatch.setenv("LLM_BUDGET_USD_PER_RUN", "0.03")
    config.get_settings.cache_clear()
    ledger.reset()

    def fake(model, system, user, max_tokens, json_mode):
        return "{}", 1000, 1000  # $0.001 + $0.01 = $0.011 per call

    # model guard: only gpt-6-luna may be called
    with pytest.raises(LLMUnavailable):
        LLMClient(transport=fake).complete("s", "u", purpose="ask", model="gpt-6-sol")

    # per-run cap: the worst-case estimate (1000 output tokens = $0.01) must fit in what is left
    from app.db import Run, session_scope
    with session_scope() as s:
        r = Run(date="2026-11-05", trigger="test")
        s.add(r)
        s.flush()
        run_id = r.id
    run = LLMClient(transport=fake, run_id=run_id, max_calls=100)
    run.complete("s", "u", purpose="enrich", max_output_tokens=1000)
    run.complete("s", "u", purpose="enrich", max_output_tokens=1000)
    with pytest.raises(BudgetExceeded):  # $0.022 spent + $0.010 worst case > $0.03
        run.complete("s", "u", purpose="enrich", max_output_tokens=1000)

    # total cap persists across clients and restarts (spend is rebuilt from the usage log)
    ledger.reset()
    assert abs(budget_status()["spent_usd"] - 0.022) < 1e-6
    other = LLMClient(transport=fake, max_calls=100)
    other.complete("s", "u", purpose="ask", max_output_tokens=1000)
    other.complete("s", "u", purpose="ask", max_output_tokens=1000)  # $0.044 total
    with pytest.raises(BudgetExceeded):
        other.complete("s", "u", purpose="ask", max_output_tokens=1000)
    assert budget_status()["spent_usd"] <= 0.05
    config.get_settings.cache_clear()
