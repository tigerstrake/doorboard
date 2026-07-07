from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from control_plane_api import models  # noqa: F401 — registers tables on Base.metadata
from control_plane_api.db import Base
from control_plane_api.settings import settings as get_settings
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    # `disable_existing_loggers=False` — the fileConfig default (True) would
    # otherwise permanently disable every logger already created in this
    # process (e.g. `control_plane_api.presence`) the first time a migration
    # runs, which only surfaces later as unrelated tests' `caplog` assertions
    # silently seeing zero records for the rest of the pytest session.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _get_url() -> str:
    # Tests point migrations at a disposable database without touching
    # Settings/.env — same override the app itself never needs, since the
    # app always goes through Settings.
    return os.environ.get("ALEMBIC_DATABASE_URL") or get_settings().postgres_dsn


def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _get_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
