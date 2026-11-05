"""Database engine, session factory and ORM models (SQLite by default, Postgres-ready)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Item(Base):
    """One raw article/post/paper as fetched from a source."""

    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    url_norm: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    title: Mapped[str] = mapped_column(Text)
    snippet: Mapped[str] = mapped_column(Text, default="")
    source_id: Mapped[str] = mapped_column(String(100), index=True)
    source_name: Mapped[str] = mapped_column(String(200))
    source_group: Mapped[str] = mapped_column(String(50), default="news")
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # naive UTC
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    points: Mapped[int] = mapped_column(Integer, default=0)  # HN points / reddit score / HF upvotes
    comments_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"), nullable=True)
    story_id: Mapped[int | None] = mapped_column(ForeignKey("stories.id"), nullable=True, index=True)

    story: Mapped["Story | None"] = relationship(back_populates="items")


class Story(Base):
    """A cluster of items covering the same news event."""

    __tablename__ = "stories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(50), default="Research", index=True)
    importance: Mapped[int] = mapped_column(Integer, default=2, index=True)
    why_it_matters: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    enriched: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    enriched_model: Mapped[str] = mapped_column(String(100), default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime, index=True)  # earliest item published_at
    last_seen: Mapped[datetime] = mapped_column(DateTime, index=True)  # latest item published_at
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    source_count: Mapped[int] = mapped_column(Integer, default=1)
    rank_score: Mapped[float] = mapped_column(Float, default=0.0)
    centroid: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    bookmarked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    items: Mapped[list[Item]] = relationship(back_populates="story", order_by="Item.published_at")
    entities: Mapped[list["StoryEntity"]] = relationship(
        back_populates="story", cascade="all, delete-orphan"
    )


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("key", "kind", name="uq_entity_key_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(200), index=True)  # lowercase normalized
    kind: Mapped[str] = mapped_column(String(20), index=True)  # company | model | person | topic


class StoryEntity(Base):
    __tablename__ = "story_entities"

    story_id: Mapped[int] = mapped_column(ForeignKey("stories.id"), primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), primary_key=True)

    story: Mapped[Story] = relationship(back_populates="entities")
    entity: Mapped[Entity] = relationship()


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String(10), unique=True, index=True)  # YYYY-MM-DD local
    headline: Mapped[str] = mapped_column(Text, default="")
    top: Mapped[list] = mapped_column(JSON, default=list)  # [{story_id, line}]
    topics: Mapped[list] = mapped_column(JSON, default=list)  # [{title, angle, story_ids}]
    model: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    trigger: Mapped[str] = mapped_column(String(20), default="cli")  # open | manual | cli | schedule
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | ok | error
    stage: Mapped[str] = mapped_column(String(50), default="starting")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    purpose: Mapped[str] = mapped_column(String(30))  # enrich | digest | ask
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SourceStatus(Base):
    __tablename__ = "source_status"

    source_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_attempt: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_item_count: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)


Index("ix_items_source_published", Item.source_id, Item.published_at)

# ---------------------------------------------------------------- engine/session

_engine = None
_SessionLocal: sessionmaker | None = None


def _make_engine(url: str):
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def init_db(url: str | None = None):
    """Create the engine (once) and all tables. Returns the engine."""
    global _engine, _SessionLocal
    url = url or get_settings().database_url
    if _engine is None or _engine.url.render_as_string(hide_password=False) != url:
        if _engine is not None:
            _engine.dispose()
        _engine = _make_engine(url)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (used by tests)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def _new_session():
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator:
    session = _new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session():
    """FastAPI dependency."""
    session = _new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
