"""Database access: engine, pooled connections, sessions and transactions.

* The engine is created lazily, so importing this module never opens a connection and the
  application can start (and report ``degraded`` health) while PostgreSQL is down.
* All settings, including the password, come from the environment (``app.settings``).
* Transactions: use ``session_scope()`` in scripts and services that write (commit on success,
  rollback on any exception) and ``get_db()`` as the FastAPI dependency (the session is rolled
  back and closed at the end of the request; call ``commit()`` explicitly to persist).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.settings import get_settings

logger = logging.getLogger("aquamap.db")

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def build_engine(url: URL | str | None = None) -> Engine:
    """Create a configured engine (does not connect). ``url`` defaults to the settings' database."""
    settings = get_settings()
    return create_engine(
        url if url is not None else settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=True,
        echo=settings.db_echo,
        connect_args={
            "connect_timeout": settings.db_connect_timeout,
            "application_name": "aquamap",
        },
    )


def init_engine(url: URL | str | None = None) -> Engine:
    """(Re)create the process-wide engine and session factory, disposing any previous engine."""
    global _engine, _session_factory
    dispose_engine()
    _engine = build_engine(url)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    """Return the process-wide engine, creating it on first call."""
    return _engine if _engine is not None else init_engine()


def _new_session() -> Session:
    get_engine()
    assert _session_factory is not None
    return _session_factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit if the block succeeds, roll back if it raises, always close."""
    session = _new_session()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, rolled back and closed afterwards.

    Nothing is committed implicitly; write paths call ``db.commit()`` themselves.
    """
    session = _new_session()
    try:
        yield session
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def check_connection() -> bool:
    """True if the database answers ``SELECT 1``; never raises."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError as exc:
        logger.warning("Database health check failed: %s", exc.__class__.__name__)
        return False


def dispose_engine() -> None:
    """Close pooled connections (shutdown, tests, reconfiguration)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
