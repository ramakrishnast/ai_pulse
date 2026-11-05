import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
FIXTURES = Path(__file__).parent / "fixtures"

# Tests never touch the network-only pieces: hashing embeddings, no LLM key.
os.environ["EMBEDDING_BACKEND"] = "hashing"
os.environ["LLM_PROVIDER"] = "none"
os.environ["SMTP_HOST"] = ""
os.environ["APP_PASSWORD"] = ""


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from app import config, db as dbmod, embeddings

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    config.get_settings.cache_clear()
    dbmod.reset_engine()
    dbmod.init_db()
    embeddings.set_embedder(embeddings.HashingEmbedder())
    yield dbmod
    dbmod.reset_engine()
    config.get_settings.cache_clear()


@pytest.fixture()
def fixture_bytes():
    return lambda name: (FIXTURES / name).read_bytes()
