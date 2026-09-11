"""Database engine and session management.

The same ORM layer runs against SQLite locally and PostgreSQL in deployment;
``DATABASE_URL`` is the only thing that changes. This matters in practice:
free-tier container disks are ephemeral, so a deployed SQLite file would lose
the dashboard's contents on the next redeploy or cold start.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _build_engine() -> Engine:
    url = settings.database_url
    kwargs: dict = {"pool_pre_ping": True, "future": True}

    if url.startswith("sqlite"):
        # The path is relative to the process CWD; make sure it exists before
        # SQLAlchemy tries to open it, otherwise the first request 500s.
        if ":memory:" not in url:
            db_path = Path(url.split("///", 1)[-1]).expanduser()
            db_path.parent.mkdir(parents=True, exist_ok=True)
        # FastAPI serves requests from a threadpool; SQLite objects are
        # otherwise pinned to their creating thread.
        kwargs["connect_args"] = {"check_same_thread": False}

    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Create tables if they do not exist. Called once on startup."""
    # Import for the side effect of registering models on Base.metadata.
    from app.models import document as _document  # noqa: F401

    Base.metadata.create_all(bind=engine)
    logger.info(
        "database initialised",
        extra={"dialect": engine.dialect.name},
    )


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
