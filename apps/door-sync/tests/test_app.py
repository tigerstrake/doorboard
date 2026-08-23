"""HTTP surface: /health, /metrics, /queue, /internal/enqueue, /internal/purge.

The background drain/SSE tasks are neutralised so these tests exercise only the
request-handling surface deterministically.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from door_sync import app as app_module
from door_sync import settings as settings_module
from door_sync.engine import SyncEngine
from door_sync.sources import MediaEventSource
from fastapi.testclient import TestClient


def _neuter_background(monkeypatch) -> None:  # noqa: ANN001
    async def _noop(self) -> None:
        return None

    async def _noop_reconcile(self) -> int:
        return 0

    monkeypatch.setattr(SyncEngine, "run", _noop)
    monkeypatch.setattr(SyncEngine, "reconcile_from_media", _noop_reconcile)
    monkeypatch.setattr(MediaEventSource, "run", _noop)


@pytest.fixture
def client(tmp_path: Path, monkeypatch, helpers) -> Generator[TestClient, None, None]:
    settings_module.override_settings(
        helpers.make_settings(tmp_path, media_target="mock", admin_token="test-admin-token")
    )
    _neuter_background(monkeypatch)
    with TestClient(
        app_module.app,
        headers={"Authorization": "Bearer test-admin-token"},
    ) as c:
        yield c
    settings_module.reset_settings()


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "door-sync"
    assert body["status"] == "ok"


def _backdate_pending(client: TestClient, *, age_s: int) -> None:
    """Age every pending item by ``age_s`` — a stalled queue, in one line.

    A NAS or NUC that is merely unreachable raises only TransientError, which
    retries forever and never dead-letters, so backdating ``created_at`` is
    exactly the shape a week-long outage leaves behind.
    """
    queue = client.app.state.queue  # type: ignore[attr-defined]
    old = datetime.now(UTC) - timedelta(seconds=age_s)
    with queue._lock:  # noqa: SLF001 (test introspection)
        queue._conn.execute(  # noqa: SLF001
            "UPDATE queue_item SET created_at = ? WHERE status='pending'",
            (old.isoformat(),),
        )
        queue._conn.commit()  # noqa: SLF001


def test_health_is_degraded_when_the_queue_has_stalled(client: TestClient, helpers) -> None:
    """Zero dead-letters, nothing draining — health must not say "ok"."""
    client.post("/internal/enqueue", json={"event": helpers.make_session_event_dict()})

    assert client.get("/health").json()["status"] == "ok"

    threshold = client.app.state.cfg.pending_age_degraded_s  # type: ignore[attr-defined]
    _backdate_pending(client, age_s=threshold + 60)

    body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert client.get("/queue").json()["summary"]["dead_letter"] == 0
    assert "oldest pending" in body["detail"]


def test_health_stays_ok_for_a_backlog_inside_the_threshold(client: TestClient, helpers) -> None:
    client.post("/internal/enqueue", json={"event": helpers.make_session_event_dict()})
    threshold = client.app.state.cfg.pending_age_degraded_s  # type: ignore[attr-defined]
    _backdate_pending(client, age_s=threshold - 60)

    assert client.get("/health").json()["status"] == "ok"


def test_metrics(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "door_sync_queue_depth" in resp.text
    assert "door_sync_dead_letter_total" in resp.text


def test_internal_enqueue_event_appears_in_queue(client: TestClient, helpers) -> None:
    ev = helpers.make_session_event_dict()
    resp = client.post("/internal/enqueue", json={"event": ev})
    assert resp.status_code == 200
    assert resp.json()["enqueued"] is True

    q = client.get("/queue")
    assert q.status_code == 200
    items = q.json()["items"]
    assert any(it["item_id"] == ev["event_id"] and it["target"] == "nuc" for it in items)


def test_internal_enqueue_rejects_malformed_event(client: TestClient) -> None:
    resp = client.post("/internal/enqueue", json={"event": {"not": "an event"}})
    assert resp.status_code == 422


def test_internal_purge_enqueues_durable_forward(client: TestClient) -> None:
    resp = client.post("/internal/purge/prs_xyz")
    assert resp.status_code == 200
    assert resp.json()["enqueued"] is True

    q = client.get("/queue").json()
    purge_items = [it for it in q["items"] if it["kind"] == "purge"]
    assert len(purge_items) == 1
    assert purge_items[0]["target"] == "nuc"


def test_queue_admin_token_enforced(tmp_path: Path, monkeypatch, helpers) -> None:
    settings_module.override_settings(
        helpers.make_settings(tmp_path, media_target="mock", admin_token="secret")
    )
    _neuter_background(monkeypatch)
    try:
        with TestClient(app_module.app) as c:
            assert c.get("/queue").status_code == 401
            ok = c.get("/queue", headers={"Authorization": "Bearer secret"})
            assert ok.status_code == 200
    finally:
        settings_module.reset_settings()


def test_queue_fails_closed_without_configured_token(tmp_path: Path, monkeypatch, helpers) -> None:
    settings_module.override_settings(
        helpers.make_settings(tmp_path, media_target="mock", admin_token="")
    )
    _neuter_background(monkeypatch)
    try:
        with TestClient(app_module.app) as c:
            assert c.get("/queue").status_code == 503
    finally:
        settings_module.reset_settings()
