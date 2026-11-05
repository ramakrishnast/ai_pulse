"""Command line: `python -m app.cli run` (one pipeline run) or `python -m app.cli serve`."""
from __future__ import annotations

import argparse
import json
import logging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ai-pulse")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the pipeline once (fetch, dedupe, cluster, enrich, digest)")
    sub.add_parser("init-db", help="create database tables")
    serve = sub.add_parser("serve", help="start the web app")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .db import init_db

    init_db()
    if args.cmd == "run":
        from .runner import current_run, run_pipeline

        run_pipeline(trigger="cli")
        print(json.dumps(current_run(), indent=2))
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
