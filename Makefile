PY      ?= python3
VENV    := .venv
BIN     := $(VENV)/bin
PORT    ?= 8000

.PHONY: help setup setup-ml frontend test run serve demo dev docker docker-demo clean

help:
	@echo "make setup     - create venv, install backend + frontend deps, copy .env"
	@echo "make setup-ml  - also install sentence-transformers (CPU torch)"
	@echo "make test      - run backend unit tests"
	@echo "make run       - run the pipeline once (fetch, dedupe, cluster, enrich, digest)"
	@echo "make serve     - start the web app on :$(PORT)"
	@echo "make demo      - build UI, run the pipeline once with real feeds, then serve"
	@echo "make dev       - backend with reload + Vite dev server"
	@echo "make docker    - docker compose up --build"

$(BIN)/python:
	$(PY) -m venv $(VENV)

.env:
	cp .env.example .env
	@echo "Created .env - add OPENAI_API_KEY (or set LLM_PROVIDER=none)"

setup: $(BIN)/python .env
	$(BIN)/pip install -q -r backend/requirements.txt
	cd frontend && npm install --no-audit --no-fund

setup-ml: setup
	$(BIN)/pip install -r backend/requirements-ml.txt --extra-index-url https://download.pytorch.org/whl/cpu

frontend:
	cd frontend && npm run build

test:
	cd backend && ../$(BIN)/python -m pytest -q

run:
	cd backend && ../$(BIN)/python -m app.cli run

serve:
	cd backend && ../$(BIN)/python -m app.cli serve --port $(PORT)

demo: setup frontend run serve

dev:
	cd backend && ../$(BIN)/uvicorn app.main:app --reload --port $(PORT) & cd frontend && npm run dev

docker: .env
	docker compose up --build

docker-demo: .env
	docker compose run --rm ai-pulse python -m app.cli run && docker compose up --build

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist
