"""Alembic migration cycle: empty -> head -> downgrade one -> head again, clean.

Acceptance criterion (T-501 brief). Runs against the same real Postgres the
other tests use (see conftest.py), via alembic's Python API rather than a
subprocess so it shares the test process's env var overrides.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from control_plane_api.db import Base
from sqlalchemy import inspect

APP_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def alembic_config(engine, monkeypatch: pytest.MonkeyPatch) -> Config:
    Base.metadata.drop_all(engine)  # start from a genuinely empty database
    # render_as_string(hide_password=False), NOT str(engine.url): str() masks
    # the password as "***", which a trust-auth local Postgres accepts but a
    # password-auth Postgres (CI) rejects with "password authentication failed".
    dsn = engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", dsn)
    cfg = Config(str(APP_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(APP_ROOT / "migrations"))
    return cfg


def _table_names(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def test_upgrade_downgrade_upgrade_cycle_is_clean(alembic_config: Config, engine) -> None:
    assert _table_names(engine) == set()

    command.upgrade(alembic_config, "head")
    after_upgrade = _table_names(engine)
    assert after_upgrade == set(Base.metadata.tables) | {"alembic_version"}

    # `-1` undoes exactly the latest revision, not the whole chain — as more
    # migrations accumulate (T-504 added 0002) this is no longer the same as
    # `base`. What must always hold: something goes away, `alembic_version`
    # itself never does, and re-upgrading exactly restores `after_upgrade`.
    command.downgrade(alembic_config, "-1")
    after_downgrade = _table_names(engine)
    assert after_downgrade < after_upgrade
    assert "alembic_version" in after_downgrade

    command.upgrade(alembic_config, "head")
    after_reupgrade = _table_names(engine)
    assert after_reupgrade == after_upgrade


def test_downgrade_to_base_removes_every_model_table(alembic_config: Config, engine) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    remaining = _table_names(engine) - {"alembic_version"}
    assert remaining == set()
    for table_name in Base.metadata.tables:
        assert table_name not in remaining


def test_migration_schema_matches_orm_metadata(alembic_config: Config, engine) -> None:
    """Guards against models.py and the migration drifting apart over time."""
    command.upgrade(alembic_config, "head")
    inspector = inspect(engine)
    live_tables = set(inspector.get_table_names())
    assert live_tables == set(Base.metadata.tables) | {"alembic_version"}
    for table_name, table in Base.metadata.tables.items():
        live_columns = {c["name"] for c in inspector.get_columns(table_name)}
        model_columns = {c.name for c in table.columns}
        assert live_columns == model_columns, table_name
