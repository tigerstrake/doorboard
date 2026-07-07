"""Integration: door-sync NUC target against the real control-plane-api + Postgres.

Proves the correctness invariant the brief calls out explicitly — "re-uploading
an already-ingested batch must not double-store — control-plane-api dedups by
event_id" — against the actual production engine (Postgres, not a stand-in), plus
the ADR-0009 person-purge round-trip and its idempotency.

Skipped automatically if the local Postgres from the T-501 test setup is not
reachable. CI provides it (see .github/workflows/ci.yml).
"""

from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.anyio

DSN = os.environ.get(
    "CONTROL_PLANE_TEST_DATABASE_URL",
    "postgresql+psycopg://doorboard:doorboard@localhost:5432/doorsync_scratch_test",
)


def _postgres_reachable() -> bool:
    try:
        import psycopg

        raw = DSN.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(raw, connect_timeout=2):
            return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(not _postgres_reachable(), reason="Postgres not reachable")


@requires_pg
async def test_event_dedup_and_purge_roundtrip(tmp_path, helpers) -> None:
    from control_plane_api import settings as cp_settings
    from control_plane_api import tokens as token_store
    from control_plane_api.app import app as cp_app
    from control_plane_api.db import Base, make_engine, make_session_factory, session_scope
    from control_plane_api.models import EventRow, PersonPurgeTombstoneRow
    from door_sync.engine import SyncEngine
    from door_sync.queue import UploadQueue
    from door_sync.targets import HttpNucTarget, MockMediaTarget
    from sqlalchemy import func, select

    # --- fresh schema on the scratch DB ---
    engine = make_engine(DSN)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)

    with session_scope(sf) as s:
        issued = token_store.issue_and_store(s, scope="ingest", door_id="primary")
    token = issued.raw

    prev_dsn = os.environ.get("POSTGRES_DSN")
    os.environ["POSTGRES_DSN"] = DSN
    cp_settings.reset_settings()

    try:
        # Run the control-plane app's real lifespan (builds its AppState/engine).
        async with cp_app.router.lifespan_context(cp_app):
            transport = httpx.ASGITransport(app=cp_app)
            nuc = HttpNucTarget("http://control-plane", ingest_token=token, transport=transport)

            settings = helpers.make_settings(tmp_path, media_target="mock")
            queue = UploadQueue(settings.queue_db_path)
            try:
                door_engine = SyncEngine(
                    queue=queue,
                    settings=settings,
                    media_target=MockMediaTarget(),
                    nuc_target=nuc,
                    media_client=helpers.RecordingMediaClient(),
                )

                # 1. Mirror an event; it lands in Postgres once.
                ev = helpers.make_session_event_dict()
                door_engine.enqueue_event(ev)
                await door_engine.run_once()
                mirrored = queue.get(ev["event_id"])
                assert mirrored is not None
                assert mirrored.status == "completed"

                with session_scope(sf) as s:
                    count = s.execute(select(func.count()).select_from(EventRow)).scalar_one()
                assert count == 1

                # 2. Re-send the SAME event (crash-retry): far side dedups, no dup row.
                import json

                await nuc.ingest_event(json.dumps(ev))
                with session_scope(sf) as s:
                    count = s.execute(select(func.count()).select_from(EventRow)).scalar_one()
                assert count == 1

                # 3. Person purge forwarded and idempotent on retry.
                door_engine.enqueue_purge(person_id="prs_integration", trace_id=ev["trace_id"])
                await door_engine.run_once()
                await nuc.purge_person("prs_integration")  # retry: must not error
                with session_scope(sf) as s:
                    tomb = s.get(PersonPurgeTombstoneRow, "prs_integration")
                assert tomb is not None
            finally:
                queue.close()
    finally:
        if prev_dsn is None:
            os.environ.pop("POSTGRES_DSN", None)
        else:
            os.environ["POSTGRES_DSN"] = prev_dsn
        cp_settings.reset_settings()
        Base.metadata.drop_all(engine)
        engine.dispose()
