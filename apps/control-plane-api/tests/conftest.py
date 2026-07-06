"""Fixtures for control-plane-api tests.

Tests run against a real local Postgres (see ../README.md "Running tests"),
not SQLite — Alembic/idempotency behavior this service depends on (e.g.
unique-constraint-triggered IntegrityError on a duplicate PK) must be
verified against the actual production database engine, not a stand-in.
Each test gets a freshly recreated schema (drop_all + create_all) rather
than a shared fixture-scoped one, trading a little speed for zero
cross-test state leakage.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from control_plane_api.app import app
from control_plane_api.db import Base, make_session_factory
from control_plane_api.settings import Settings, override_settings, reset_settings
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

TEST_DATABASE_URL = os.environ.get(
    "CONTROL_PLANE_TEST_DATABASE_URL",
    "postgresql+psycopg://doorboard:doorboard@localhost:5432/doorboard_test",
)


def _drop_alembic_version_table(eng) -> None:
    # Alembic's own bookkeeping table isn't part of Base.metadata, so
    # drop_all() never touches it — leaving it behind would make the next
    # test's `alembic upgrade head` a silent no-op against a database whose
    # actual tables were just dropped.
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))


@pytest.fixture
def engine():
    eng = create_engine(TEST_DATABASE_URL, future=True)
    _drop_alembic_version_table(eng)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    _drop_alembic_version_table(eng)
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


class TestSettings(Settings):
    postgres_dsn: str = TEST_DATABASE_URL
    admin_token: str = "test-admin-token"
    notify_cooldown_s: int = 3600
    sync_stall_alert_s: int = 4 * 3600


@pytest.fixture(autouse=True)
def _setup_env(engine):
    """Recreate the schema (via `engine`) then point Settings at the same DB."""
    override_settings(TestSettings())
    yield
    reset_settings()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c
