"""Post-commit fan-out runs off the request thread and never blocks ingest.

The bug: every route was `async def` and `/ingest` delivered ntfy/Telegram inline,
so one slow notification (a 40 MB video upload, a 30 s timeout) froze the event
loop — `/health` stopped answering and door-sync's next batch stalled and backed
off. Now delivery is enqueued after the ingest transaction commits and drained by
a background thread.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

import pytest
from control_plane_api import state as state_module
from control_plane_api.app import app
from control_plane_api.fanout import FanoutWorker
from control_plane_api.metrics import FANOUT_DROPPED, metrics
from control_plane_api.mqtt import RecordingMqttPublisher
from control_plane_api.notify import Notification, NotifyEngine, RecordingNotifier
from control_plane_api.service import ingest_batch
from doorboard_contracts import parse_event
from fastapi.testclient import TestClient

from .factories import build_event

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


class SlowNotifier:
    """A sender double that takes 2 s per notification, like a slow ntfy/Telegram hop."""

    def __init__(self, delay_s: float = 2.0) -> None:
        self.delay_s = delay_s
        self.sent: list[Notification] = []
        self.delivered = threading.Event()

    def notify(self, notification: Notification) -> None:
        time.sleep(self.delay_s)
        self.sent.append(notification)
        self.delivered.set()


def _critical_storage_alert() -> dict:
    return build_event("system.storage_alert", payload_overrides={"severity": "critical"})


def test_a_slow_notification_does_not_block_health_and_is_still_delivered(
    engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow = SlowNotifier(delay_s=2.0)
    monkeypatch.setattr(state_module, "build_notifier", lambda **kwargs: slow)

    with TestClient(app) as client:
        token = client.post(
            "/admin/tokens", json={"scope": "ingest", "door_id": "primary"}, headers=ADMIN_HEADERS
        ).json()["token"]
        ingest_result: dict = {}

        def ingest() -> None:
            t0 = time.monotonic()
            resp = client.post(
                "/ingest",
                json={"batch_id": "slow", "events": [_critical_storage_alert()]},
                headers={"Authorization": f"Bearer {token}"},
            )
            ingest_result["status_code"] = resp.status_code
            ingest_result["body"] = resp.json()
            ingest_result["elapsed_s"] = time.monotonic() - t0

        worker = threading.Thread(target=ingest)
        worker.start()
        time.sleep(0.2)  # the ingest is in flight (or already handed off)

        t0 = time.monotonic()
        health = client.get("/health")
        health_ms = (time.monotonic() - t0) * 1000
        worker.join(timeout=10)

        assert health.status_code == 200
        assert health_ms < 100, f"/health took {health_ms:.0f} ms while a notification was sending"
        # The ingest itself returned as soon as the row was committed, not after 2 s.
        assert ingest_result["status_code"] == 200
        assert ingest_result["body"]["results"][0]["status"] == "stored"
        assert ingest_result["elapsed_s"] < 1.5
        # ...and the notification still went out, from the worker thread.
        assert slow.delivered.wait(timeout=5), "the notification was never delivered"
        assert slow.sent[0].rule_key.startswith("storage_alert:")


def test_worker_that_is_not_started_delivers_synchronously(session_factory) -> None:
    notifier = RecordingNotifier()
    worker = FanoutWorker(
        session_factory,
        notify_engine=NotifyEngine(notifier, cooldown_s=3600, sync_stall_alert_s=14400),
    )
    assert worker.running is False
    worker.enqueue_event(parse_event(_critical_storage_alert()), now=datetime.now(UTC))
    assert len(notifier.sent) == 1  # inline: what unit tests and the CLI rely on


def test_ingest_batch_enqueues_only_stored_events(session_factory) -> None:
    notifier = RecordingNotifier()
    worker = FanoutWorker(
        session_factory,
        notify_engine=NotifyEngine(notifier, cooldown_s=3600, sync_stall_alert_s=14400),
    )
    event = _critical_storage_alert()
    ingest_batch(
        session_factory,
        [event, event],  # the second is a duplicate
        batch_id="b",
        now=datetime.now(UTC),
        mqtt_publisher=RecordingMqttPublisher(),
        fanout=worker,
    )
    assert len(notifier.sent) == 1


def test_a_full_queue_drops_and_counts_instead_of_growing(session_factory) -> None:
    release = threading.Event()

    def blocked() -> None:
        release.wait(timeout=5)

    worker = FanoutWorker(
        session_factory,
        notify_engine=NotifyEngine(RecordingNotifier(), cooldown_s=1, sync_stall_alert_s=1),
        max_queue=2,
    )
    worker.start()
    dropped_before = metrics.counter(FANOUT_DROPPED)
    try:
        assert worker.submit(blocked) is True  # taken by the thread, blocks it
        time.sleep(0.05)
        assert worker.submit(lambda: None) is True  # queued (1/2)
        assert worker.submit(lambda: None) is True  # queued (2/2)
        assert worker.submit(lambda: None) is False  # full -> dropped, counted
        assert metrics.counter(FANOUT_DROPPED) - dropped_before == 1
    finally:
        release.set()
        worker.stop()
    assert worker.running is False


def test_a_failing_job_does_not_kill_the_worker(session_factory, caplog) -> None:
    worker = FanoutWorker(
        session_factory,
        notify_engine=NotifyEngine(RecordingNotifier(), cooldown_s=1, sync_stall_alert_s=1),
    )
    worker.start()
    done = threading.Event()

    def boom() -> None:
        raise RuntimeError("telegram exploded")

    try:
        with caplog.at_level(logging.WARNING, logger="control_plane_api.fanout"):
            worker.submit(boom)
            worker.submit(done.set)
            assert done.wait(timeout=5)
    finally:
        worker.stop()
    assert any(r.getMessage() == "fanout_job_failed" for r in caplog.records)
