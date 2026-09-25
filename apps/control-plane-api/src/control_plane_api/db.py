"""SQLAlchemy engine/session wiring.

One process-wide engine, created from `Settings.postgres_dsn`. Sessions are
created per request/operation via `session_factory()` — no implicit global
session, so tests can point the same code at an isolated schema.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all ORM models — see models.py."""


# Seconds to wait for a TCP/auth handshake before giving up. A refused port
# fails instantly; this bounds the black-holed-host case so `/health` (which
# probes the database) answers "degraded" instead of hanging past the compose
# healthcheck's own timeout.
CONNECT_TIMEOUT_S = 5


def make_engine(dsn: str) -> Engine:
    return create_engine(
        dsn,
        pool_pre_ping=True,
        future=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_S},
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """One transaction per `with` block: commit on success, rollback on any exception."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
