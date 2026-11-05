"""Full pipeline + API with a mocked HTTP layer and a fake LLM (no network)."""
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

STORIES = [
    ("OpenAI releases GPT-6 Luna model for developers", "OpenAI's cheaper Luna model arrives in the API."),
    ("Robotics startup raises $200M Series C for humanoid robots", "The round values the company at $2B."),
    ("EU regulators publish draft AI Act guidance on general purpose models", "Guidance covers transparency."),
]


def rss(items):
    now = datetime.now(timezone.utc)
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link><description>{d}</description>"
        f"<pubDate>{format_datetime(now - timedelta(hours=h))}</pubDate></item>"
        for h, (t, u, d) in enumerate(items, 1)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'


def handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    if host == "lab.test":
        return httpx.Response(200, text=rss([(STORIES[0][0], "https://lab.test/luna?utm_source=rss", STORIES[0][1])]))
    if host == "news.test":
        return httpx.Response(200, text=rss([
            ("OpenAI launches GPT-6 Luna, a cheaper model for developers", "https://news.test/a", "Luna is cheaper."),
            (STORIES[1][0], "https://news.test/b", STORIES[1][1]),
            (STORIES[2][0], "https://news.test/c", STORIES[2][1]),
        ]))
    if host == "mirror.test":  # same article as lab.test, different tracking params -> exact dup
        return httpx.Response(200, text=rss([(STORIES[0][0], "https://www.lab.test/luna/?ref=hn", STORIES[0][1])]))
    if host == "broken.test":
        return httpx.Response(503)
    if host == "garbage.test":
        return httpx.Response(200, text="<html>nope</html>")
    if host == "hn.test":
        ts = int(datetime.now(timezone.utc).timestamp()) - 600
        return httpx.Response(200, json={"hits": [
            {"objectID": "1", "title": "GPT-6 Luna released by OpenAI for developers", "url": "https://lab.test/luna",
             "points": 300, "num_comments": 100, "created_at_i": ts}]})
    return httpx.Response(404)


SOURCES = """
defaults: {retries: 1, timeout_seconds: 5}
sources:
  - {id: lab, name: Lab Blog, type: rss, url: "https://lab.test/feed", group: lab, weight: 1.5}
  - {id: news, name: News Site, type: rss, url: "https://news.test/feed", group: news}
  - {id: mirror, name: Mirror, type: rss, url: "https://mirror.test/feed", group: newsletter}
  - {id: broken, name: Broken, type: rss, url: "https://broken.test/feed", group: news}
  - {id: garbage, name: Garbage, type: rss, url: "https://garbage.test/feed", group: news}
  - {id: hn, name: Hacker News, type: hn, url: "https://hn.test/api", group: community, min_points: 50, ai_filter: true}
  - {id: disabled-src, name: Disabled, type: rss, url: "https://off.test/feed", group: news, enabled: false}
"""


def fake_llm(model, system, user, max_tokens, json_mode):
    try:
        data = json.loads(user)
    except json.JSONDecodeError:  # ask-the-news prompt is plain text
        return "Luna is the cheaper model [1].", 300, 50
    if "stories" in data and data["stories"] and "sources" in data["stories"][0]:
        out = []
        for st in data["stories"]:
            head = st["sources"][0]["headline"]
            cat = ("Funding & Business" if "raises" in head else "Policy & Safety" if "EU" in head
                   else "Models & Releases")
            out.append({"id": st["id"], "title": f"Summary title {st['id']}",
                        "summary": f"Paraphrased overview number {st['id']} drawn from {len(st['sources'])} outlets.",
                        "category": cat, "importance": 5 if "Luna" in head else 3,
                        "why_it_matters": "Useful signal for builders.",
                        "entities": {"companies": ["OpenAI"] if "OpenAI" in head else [], "models": [], "people": []},
                        "topics": ["pricing"]})
        return json.dumps({"stories": out}), 500, 200
    if "rising_this_week" in data:
        ids = [s["id"] for s in data["stories"]]
        return json.dumps({"headline": "A cheaper model leads the day.",
                           "top": [{"id": i, "line": f"Line for {i}."} for i in ids],
                           "topics": [{"title": f"Idea {n}", "angle": "Angle.", "story_ids": ids[:1]} for n in range(3)]}), 800, 300
    return "Luna is the cheaper model [1].", 300, 50


@pytest.fixture()
def pipeline_env(db, tmp_path, monkeypatch):
    from app import config

    path = tmp_path / "sources.yaml"
    path.write_text(SOURCES)
    monkeypatch.setenv("SOURCES_FILE", str(path))
    monkeypatch.setenv("TODAY_WINDOW_HOURS", "48")
    config.get_settings.cache_clear()
    return db


def test_full_pipeline_and_api(pipeline_env):
    from fastapi.testclient import TestClient

    from app import runner
    from app.db import Item, Run, SourceStatus, Story, session_scope
    from app.llm import LLMClient
    from app.main import app

    llm = LLMClient(transport=fake_llm, max_calls=10)
    run_id = runner.run_pipeline(trigger="test", transport=httpx.MockTransport(handler), llm=llm)

    with session_scope() as s:
        run = s.get(Run, run_id)
        assert run.status == "ok", run.error
        assert run.stats["sources_ok"] == 4 and run.stats["sources_failed"] == 2
        assert set(run.stats["errors"]) == {"broken", "garbage"}
        assert s.get(SourceStatus, "broken").consecutive_failures == 1
        # lab + mirror + hn point at the same URL -> stored once
        assert s.query(Item).filter(Item.url_norm == "https://lab.test/luna").count() == 1
        stories = s.query(Story).all()
        assert len(stories) == 3, [st.title for st in stories]
        luna = max(stories, key=lambda st: st.source_count)
        assert luna.source_count == 2 and luna.enriched and luna.importance == 5
        assert run.stats["llm_calls"] == 2  # one enrich batch + one digest call

    assert runner.has_run_today()
    client = TestClient(app)
    status = client.get("/api/status").json()
    assert status["has_run_today"] and status["started_run"] is False  # already ran today

    stories = client.get("/api/stories", params={"sort": "importance"}).json()
    assert stories["total"] == 3 and stories["stories"][0]["importance"] == 5
    assert client.get("/api/stories", params={"category": "Funding & Business"}).json()["total"] == 1
    assert client.get("/api/stories", params={"q": "humanoid"}).json()["total"] == 1
    assert client.get("/api/stories", params={"source": "lab"}).json()["total"] == 1
    assert client.get("/api/stories", params={"min_importance": 4}).json()["total"] == 1

    sid = stories["stories"][0]["id"]
    detail = client.get(f"/api/stories/{sid}").json()
    assert len(detail["story"]["sources"]) == 2 and isinstance(detail["related"], list)
    assert client.post(f"/api/stories/{sid}/bookmark", json={"value": True}).json()["bookmarked"]
    assert client.post(f"/api/stories/{sid}/read", json={"value": True}).json()["read"]
    assert client.get("/api/stories", params={"bookmarked": True}).json()["total"] == 1
    assert client.get("/api/stories", params={"unread": True}).json()["total"] == 2

    digest = client.get("/api/digest").json()["digest"]
    assert digest["headline"] and len(digest["top"]) == 3 and len(digest["topics"]) == 3

    trends = client.get("/api/trends").json()
    assert any(r["name"] == "OpenAI" for r in trends["top_companies"])

    srcs = {s["id"]: s for s in client.get("/api/sources").json()["sources"]}
    assert srcs["broken"]["last_error"] == "HTTP 503" and srcs["disabled-src"]["enabled"] is False
    assert client.get("/api/usage").json()["usage"]


def test_ask_without_llm_returns_citations(pipeline_env):
    from app import runner
    from app.ask import answer
    from app.db import session_scope
    from app.llm import LLMClient

    runner.run_pipeline(trigger="test", transport=httpx.MockTransport(handler), llm=LLMClient(transport=None))
    with session_scope() as s:
        res = answer(s, "What did OpenAI release?", llm=LLMClient())
        assert res["llm"] is False and res["citations"]
        assert any("lab.test" in link["url"] for c in res["citations"] for link in c["links"])
        res = answer(s, "What did OpenAI release?", llm=LLMClient(transport=fake_llm))
        assert res["llm"] and "[1]" in res["answer"] and res["citations"][0]["n"] == 1


def test_heuristic_digest_without_llm(pipeline_env):
    from app import runner
    from app.db import Digest, Story, session_scope
    from app.llm import LLMClient

    runner.run_pipeline(trigger="test", transport=httpx.MockTransport(handler), llm=LLMClient())
    with session_scope() as s:
        d = s.query(Digest).one()
        assert d.model == "heuristic" and len(d.top) == 3
        assert all(not st.enriched for st in s.query(Story))
        assert all(st.entities is not None for st in s.query(Story))


def test_password_protection(pipeline_env, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app

    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    config.get_settings.cache_clear()
    client = TestClient(app)
    assert client.get("/api/trends").status_code == 401
    assert client.get("/api/trends", auth=("me", "s3cret")).status_code == 200
    assert client.get("/healthz").status_code == 200
