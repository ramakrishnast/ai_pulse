"""Pipeline orchestration and the 'run once per day, on first visit' trigger."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .cluster import cluster_new_items
from .config import get_settings
from .db import Run, session_scope, utcnow
from .dedupe import dedupe_new_items
from .digest import build_digest
from .embeddings import get_embedder, thresholds
from .emailer import send_digest
from .enrich import enrich_stories
from .fetcher import fetch_all, store_results
from .llm import LLMClient
from .sources import load_sources

log = logging.getLogger(__name__)

_run_lock = threading.Lock()
_thread: threading.Thread | None = None


def local_today() -> str:
    tz = ZoneInfo(get_settings().app_timezone)
    return datetime.now(tz).date().isoformat()


def _set_stage(run_id: int, stage: str, **stats) -> None:
    with session_scope() as s:
        run = s.get(Run, run_id)
        run.stage = stage
        if stats:
            run.stats = {**(run.stats or {}), **stats}
    log.info("run %s: %s", run_id, stage)


def run_pipeline(trigger: str = "cli", transport=None, llm: LLMClient | None = None) -> int:
    """Run fetch -> dedupe -> cluster -> enrich -> digest synchronously. Returns the run id.

    Only one run executes at a time (per process); concurrent callers wait for the lock.
    """
    settings = get_settings()
    with _run_lock:
        t0 = time.monotonic()
        with session_scope() as s:
            run = Run(date=local_today(), trigger=trigger, status="running", stage="fetching", stats={})
            s.add(run)
            s.flush()
            run_id = run.id
        try:
            sources = load_sources()
            weights = {src.id: src.weight for src in sources}
            results = asyncio.run(fetch_all(sources, transport=transport))
            with session_scope() as s:
                fetch_stats = store_results(s, results)
            _set_stage(run_id, "deduplicating", **fetch_stats)

            embedder = get_embedder()
            dedupe_thr, cluster_thr = thresholds(embedder)
            with session_scope() as s:
                dups = dedupe_new_items(s, embedder, dedupe_thr, settings.cluster_window_days)
            _set_stage(run_id, "clustering", duplicates=dups, embedder=embedder.name)

            with session_scope() as s:
                cstats = cluster_new_items(s, embedder, cluster_thr, dedupe_thr - 0.03,
                                           settings.cluster_window_days, weights)
            _set_stage(run_id, "enriching", **cstats)

            llm = llm or LLMClient(run_id=run_id)
            llm.run_id = run_id
            llm.defer_logging = True
            with session_scope() as s:
                estats = enrich_stories(s, llm, weights)
            llm.flush_usage()
            _set_stage(run_id, "digest", **estats, llm_available=llm.available)

            with session_scope() as s:
                digest = build_digest(s, local_today(), llm)
                emailed = send_digest(s, digest) if settings.email_enabled else False
            llm.flush_usage()
            with session_scope() as s:
                run = s.get(Run, run_id)
                run.status = "ok"
                run.stage = "done"
                run.finished_at = utcnow()
                run.stats = {**(run.stats or {}), "emailed": emailed, "llm_calls": llm.usage.calls,
                             "llm_input_tokens": llm.usage.input_tokens,
                             "llm_output_tokens": llm.usage.output_tokens,
                             "llm_cost_usd": round(llm.run_cost, 5),
                             "seconds": round(time.monotonic() - t0, 1)}
            log.info("run %s finished in %.1fs | LLM calls=%d tokens in=%d out=%d", run_id,
                     time.monotonic() - t0, llm.usage.calls, llm.usage.input_tokens, llm.usage.output_tokens)
        except Exception as exc:
            log.exception("run %s failed", run_id)
            with session_scope() as s:
                run = s.get(Run, run_id)
                run.status = "error"
                run.error = f"{type(exc).__name__}: {exc}"[:1000]
                run.finished_at = utcnow()
        return run_id


def _stale_cutoff() -> datetime:
    return utcnow() - timedelta(minutes=get_settings().stale_run_minutes)


def current_run() -> dict | None:
    with session_scope() as s:
        run = s.scalar(select(Run).order_by(Run.id.desc()).limit(1))
        if run is None:
            return None
        if run.status == "running" and run.started_at < _stale_cutoff() and not is_running():
            run.status = "error"
            run.error = "interrupted (server restarted?)"
            run.finished_at = utcnow()
        return {"id": run.id, "date": run.date, "status": run.status, "stage": run.stage,
                "trigger": run.trigger, "started_at": run.started_at.isoformat() + "Z",
                "finished_at": run.finished_at.isoformat() + "Z" if run.finished_at else None,
                "stats": run.stats or {}, "error": run.error}


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def has_run_today() -> bool:
    today = local_today()
    with session_scope() as s:
        return s.scalar(
            select(Run.id).where(Run.date == today, Run.status == "ok").limit(1)
        ) is not None


def start_background_run(trigger: str) -> bool:
    """Start a pipeline run in a background thread unless one is already running."""
    global _thread
    if is_running() or _run_lock.locked():
        return False
    _thread = threading.Thread(target=run_pipeline, kwargs={"trigger": trigger}, daemon=True,
                               name="ai-pulse-run")
    _thread.start()
    return True


def _recent_failure() -> bool:
    """True if a run failed in the last 10 minutes (avoid retry storms on every page load)."""
    with session_scope() as s:
        return s.scalar(
            select(Run.id).where(Run.status == "error", Run.started_at >= utcnow() - timedelta(minutes=10)).limit(1)
        ) is not None


def ensure_daily_run() -> bool:
    """Called when someone opens the site: start today's run if it hasn't happened yet."""
    if not get_settings().auto_run_on_open or is_running() or has_run_today() or _recent_failure():
        return False
    return start_background_run("open")


def wait_for_background(timeout: float | None = None) -> None:
    if _thread is not None:
        _thread.join(timeout)
