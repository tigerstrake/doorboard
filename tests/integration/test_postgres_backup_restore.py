"""Backup/restore verification for the NUC `postgres-backup` job (T-503).

Exercises the exact commands `infra/compose/backup/pg-backup.sh` runs
(`pg_dump ... | gzip`, then a plain-SQL restore) against a real local
Postgres — the same server control-plane-api's own tests require (see
apps/control-plane-api/README.md "Running tests locally"). This is the
"restore cleanly" half of T-503's acceptance criteria; CI has an equivalent
Postgres service container (.github/workflows/ci.yml), so this runs for real
there too, not just as documentation.

Uses its own throwaway databases (never `doorboard_test`) so it can run
alongside control-plane-api's suite without interfering.
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import uuid

import pytest
import sqlalchemy as sa

ADMIN_DSN = os.environ.get(
    "CONTROL_PLANE_TEST_ADMIN_DSN",
    "postgresql+psycopg://doorboard:doorboard@localhost:5432/postgres",
)
PG_HOST = os.environ.get("CONTROL_PLANE_TEST_PG_HOST", "localhost")
PG_PORT = os.environ.get("CONTROL_PLANE_TEST_PG_PORT", "5432")
PG_USER = os.environ.get("CONTROL_PLANE_TEST_PG_USER", "doorboard")
PG_PASSWORD = os.environ.get("CONTROL_PLANE_TEST_PG_PASSWORD", "doorboard")

pytestmark = pytest.mark.skipif(
    shutil.which("pg_dump") is None or shutil.which("psql") is None,
    reason="pg_dump/psql not available in this environment",
)


def _admin_engine() -> sa.Engine:
    return sa.create_engine(ADMIN_DSN, future=True, isolation_level="AUTOCOMMIT")


def _sqlalchemy_dsn_for(dbname: str) -> str:
    return f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{dbname}"


def _cli_dsn_for(dbname: str) -> str:
    # pg_dump/psql understand libpq URIs, not SQLAlchemy's `+psycopg` scheme.
    return f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{dbname}"


def _pg_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PGPASSWORD"] = PG_PASSWORD
    return env


@pytest.fixture
def source_db() -> str:
    name = f"doorboard_backup_src_{uuid.uuid4().hex[:8]}"
    admin = _admin_engine()
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield name
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))


@pytest.fixture
def restore_target_db() -> str:
    name = f"doorboard_backup_dst_{uuid.uuid4().hex[:8]}"
    yield name
    admin = _admin_engine()
    with admin.connect() as conn:
        conn.execute(
            sa.text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": name},
        )
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))


def _seed(dbname: str) -> list[tuple[int, str]]:
    """Write a small, verifiable dataset — mirrors an append-only events table."""
    engine = sa.create_engine(_sqlalchemy_dsn_for(dbname), future=True)
    rows = [(1, "door.button_pressed"), (2, "system.storage_alert"), (3, "session.ended")]
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE backup_probe (id INT PRIMARY KEY, event_type TEXT)"))
        for row_id, event_type in rows:
            conn.execute(
                sa.text("INSERT INTO backup_probe (id, event_type) VALUES (:id, :t)"),
                {"id": row_id, "t": event_type},
            )
    engine.dispose()
    return rows


def _dump_gzip(dbname: str, dest_path: str) -> None:
    # Matches infra/compose/backup/pg-backup.sh's run_backup() exactly.
    dump = subprocess.run(
        [
            "pg_dump",
            "--dbname",
            _cli_dsn_for(dbname),
            "--format=plain",
            "--no-owner",
            "--no-privileges",
        ],
        check=True,
        capture_output=True,
        env=_pg_env(),
    )
    with gzip.open(dest_path, "wb") as fh:
        fh.write(dump.stdout)


def _restore_gzip(dump_path: str, dbname: str) -> None:
    with gzip.open(dump_path, "rb") as fh:
        sql = fh.read()
    subprocess.run(
        ["psql", _cli_dsn_for(dbname)],
        input=sql,
        check=True,
        capture_output=True,
        env=_pg_env(),
    )


def test_dump_restores_cleanly_with_matching_rows(tmp_path, source_db, restore_target_db) -> None:
    seeded = _seed(source_db)

    dump_path = tmp_path / "doorboard-test.sql.gz"
    _dump_gzip(source_db, str(dump_path))
    assert dump_path.stat().st_size > 0

    admin = _admin_engine()
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{restore_target_db}"'))

    _restore_gzip(str(dump_path), restore_target_db)

    restored_engine = sa.create_engine(_sqlalchemy_dsn_for(restore_target_db), future=True)
    with restored_engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT id, event_type FROM backup_probe ORDER BY id")).all()
    restored_engine.dispose()

    assert [tuple(r) for r in rows] == seeded


def test_dump_is_deterministic_enough_to_checksum(tmp_path, source_db) -> None:
    """Two dumps of an unchanged database must restore to identical data —
    the property `docs/runbooks/nas-backup-restore.md`'s verification step
    relies on (compare a checksum, not byte-for-byte dump equality, since
    pg_dump embeds a timestamp comment)."""
    _seed(source_db)

    first = tmp_path / "first.sql.gz"
    second = tmp_path / "second.sql.gz"
    _dump_gzip(source_db, str(first))
    _dump_gzip(source_db, str(second))

    with gzip.open(first, "rb") as fh:
        first_sql = fh.read().decode()
    with gzip.open(second, "rb") as fh:
        second_sql = fh.read().decode()

    # Strip pg_dump's per-run timestamp comment and random restrict-token
    # lines before comparing (the token guards `\restrict`/`\unrestrict`
    # meta-commands added in newer pg_dump versions and differs every run).
    unstable_prefixes = ("-- Dumped", "\\restrict", "\\unrestrict")

    def _stable_lines(sql: str) -> list[str]:
        return [line for line in sql.splitlines() if not line.startswith(unstable_prefixes)]

    assert _stable_lines(first_sql) == _stable_lines(second_sql)
