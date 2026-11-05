"""Application settings, loaded from environment variables / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_DIR / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- storage
    database_url: str = f"sqlite:///{REPO_DIR / 'data' / 'ai_pulse.db'}"
    sources_file: str = str(BACKEND_DIR / "sources.yaml")

    # --- when to run
    # The pipeline runs at most once per local calendar day, triggered by the
    # first visit of the day. Set SCHEDULE_CRON (e.g. "0 6 * * *") to also run
    # it on a schedule, which is useful on a server when the email digest is on.
    app_timezone: str = "UTC"
    auto_run_on_open: bool = True
    schedule_cron: str = ""
    stale_run_minutes: int = 45  # a "running" run older than this is considered dead

    # --- LLM
    llm_provider: str = "openai"  # openai | anthropic | none
    llm_model: str = "gpt-6-luna"
    llm_digest_model: str = ""  # defaults to llm_model
    llm_chat_model: str = ""  # defaults to llm_model
    openai_api_key: str = ""
    openai_base_url: str = ""
    anthropic_api_key: str = ""
    llm_max_calls_per_run: int = 40
    llm_max_tokens_per_run: int = 400_000
    llm_max_output_tokens: int = 4000
    llm_timeout_seconds: int = 90
    enrich_batch_size: int = 8
    enrich_max_stories_per_run: int = 200
    enrich_window_days: int = 7  # summarize stories seen within this many days

    # --- embeddings / dedupe / clustering
    embedding_backend: str = "auto"  # auto | sentence-transformers | hashing
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Cosine-similarity thresholds. Leave unset to use the embedding backend's
    # tuned defaults (MiniLM: 0.90 / 0.70, hashing fallback: 0.85 / 0.50).
    dedupe_threshold: float | None = None
    cluster_threshold: float | None = None
    cluster_window_days: int = 3

    # --- fetch
    fetch_concurrency: int = 10
    user_agent: str = "AI-Pulse/1.0 (personal news dashboard)"

    # --- UI / server
    app_password: str = ""  # if set, HTTP Basic auth (any username) protects the app
    frontend_dist: str = str(REPO_DIR / "frontend" / "dist")
    today_window_hours: int = 36

    # --- email digest (optional)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""
    smtp_starttls: bool = True
    public_base_url: str = "http://localhost:8000"

    @property
    def digest_model(self) -> str:
        return self.llm_digest_model or self.llm_model

    @property
    def chat_model(self) -> str:
        return self.llm_chat_model or self.llm_model

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_to)


@lru_cache
def get_settings() -> Settings:
    return Settings()
