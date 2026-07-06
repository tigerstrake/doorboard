"""Simulator-driven integration test: ingest -> store -> notify.

Uses `doorboard_simulator`'s real scenario runner (the same fixture other
services' integration tests rely on) rather than hand-built events, so this
exercises the actual shapes door-sync would batch-upload in production.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from control_plane_api.models import MediaMirrorRow, NotificationStateRow
from doorboard_simulator.scenarios import run_scenario_name
from fastapi.testclient import TestClient
from sqlalchemy import select

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


def _ingest_token(client: TestClient) -> str:
    resp = client.post(
        "/admin/tokens", json={"scope": "ingest", "door_id": "primary"}, headers=ADMIN_HEADERS
    )
    return resp.json()["token"]


def test_basic_bell_scenario_ingests_and_populates_media_mirror(
    client: TestClient, session_factory, tmp_path: Path
) -> None:
    result = asyncio.run(run_scenario_name("basic-bell", artifact_root=tmp_path))
    events = [e.model_dump(mode="json") for e in result.events]
    assert events  # scenario actually produced something

    token = _ingest_token(client)
    resp = client.post(
        "/ingest",
        json={"batch_id": "basic-bell-run", "events": events},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert all(r["status"] == "stored" for r in resp.json()["results"])

    finalized = next(e for e in result.events if e.type == "media.recording_finalized")
    with session_factory() as session:
        row = session.get(MediaMirrorRow, str(finalized.payload.recording_id))
    assert row is not None
    assert row.sha256 == finalized.payload.sha256
    assert row.duration_s == finalized.payload.duration_s


def test_storage_low_scenario_ingests_and_fires_storage_alert_notification(
    client: TestClient, session_factory, tmp_path: Path
) -> None:
    result = asyncio.run(run_scenario_name("storage-low", artifact_root=tmp_path))
    events = [e.model_dump(mode="json") for e in result.events]
    critical_alerts = [
        e
        for e in events
        if e["type"] == "system.storage_alert" and e["payload"]["severity"] == "critical"
    ]
    assert critical_alerts  # sanity: the scenario really does emit one

    token = _ingest_token(client)
    resp = client.post(
        "/ingest",
        json={"batch_id": "storage-low-run", "events": events},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert all(r["status"] == "stored" for r in resp.json()["results"])

    with session_factory() as session:
        fired = (
            session.execute(
                select(NotificationStateRow).where(
                    NotificationStateRow.rule_key.like("storage_alert:%")
                )
            )
            .scalars()
            .all()
        )
    assert len(fired) == 1
