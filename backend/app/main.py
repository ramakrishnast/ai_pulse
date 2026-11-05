"""FastAPI app: JSON API + the built React frontend + optional password + optional schedule."""
from __future__ import annotations

import base64
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import get_settings
from .db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ai_pulse")


def _start_scheduler():
    cron = get_settings().schedule_cron.strip()
    if not cron:
        return None
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    from . import runner

    sched = BackgroundScheduler(timezone=get_settings().app_timezone)
    sched.add_job(lambda: runner.start_background_run("schedule"), CronTrigger.from_crontab(cron))
    sched.start()
    log.info("scheduled daily run: %s (%s)", cron, get_settings().app_timezone)
    return sched


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    sched = _start_scheduler()
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(title="AI Pulse", lifespan=lifespan)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    password = get_settings().app_password
    if not password or request.url.path == "/healthz":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            _, _, supplied = base64.b64decode(header[6:]).decode().partition(":")
            if secrets.compare_digest(supplied, password):
                return await call_next(request)
        except Exception:
            pass
    return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="AI Pulse"'})


@app.get("/healthz")
def healthz():
    return {"ok": True}


app.include_router(router)

_dist = Path(get_settings().frontend_dist)
if (_dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "not found"}, status_code=404)
    candidate = (_dist / path).resolve()
    if path and candidate.is_file() and _dist.resolve() in candidate.parents:
        return FileResponse(candidate)
    index = _dist / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "frontend not built; run `make frontend` (API is at /api)"}, status_code=503)
