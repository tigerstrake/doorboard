"""The outboxes dead-letter poison, never an outage (2026-09 audit, lane A1, P2).

The dead-letter cap was added so a permanently-refused head (a contract-drift event
door-media answers 422 forever) stops blocking the healthy items behind it. But every
failure counted the same, so door-media being *unreachable* for ~2 minutes parked the head
as dead after ten connection refusals — permanently losing the session events behind it
and pinning ``/health`` at degraded for an ordinary outage. ARCHITECTURE.md §10 says
events queue through an outage.

Now only a definite refusal (4xx other than 408/429) counts toward the cap; transport
errors and 5xx retry with the capped backoff until a long window runs out, and the
operator can put dead rows back in play.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import os
import sqlite3
from collections.abc import Callable, Coroutine, Generator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import _classify_forward_failure, app, state  # noqa: E402
from door_api.persistence import SessionStore  # noqa: E402

OWNER = {"Authorization": "Bearer owner-token"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_ADMIN_TOKEN", "owner-token")
    # Retry immediately so a dozen attempts take milliseconds, not a minute.
    monkeypatch.setenv("DOOR_API_MEDIA_FORWARD_POLL_S", "0.001")
    monkeypatch.setenv("DOOR_API_SYNC_FORWARD_POLL_S", "0.001")
    monkeypatch.setenv("DOOR_API_MEDIA_RETRY_BASE_S", "0")
    monkeypatch.setenv("DOOR_API_SYNC_RETRY_BASE_S", "0")
    state.__init__()
    state.startup()
    yield
    state.shutdown()


class _FailingClient:
    """An httpx.AsyncClient stand-in whose every post raises the configured failure."""

    failure: Callable[[], Exception] = lambda: httpx.ConnectError("connection refused")
    posts = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def __aenter__(self) -> _FailingClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args

    async def post(self, url: str, **kwargs: Any) -> Any:
        del url, kwargs
        type(self).posts += 1
        raise type(self).failure()


def _refusal(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://door-media.test/internal/session_event")
    return httpx.HTTPStatusError(
        f"{code}", request=request, response=httpx.Response(code, request=request)
    )


def _fail_with(monkeypatch: pytest.MonkeyPatch, failure: Callable[[], Exception]) -> None:
    # A factory, not an instance: re-raising one exception object grows its traceback
    # on every raise, which made a dozen attempts take minutes.
    _FailingClient.failure = failure
    _FailingClient.posts = 0
    monkeypatch.setattr("door_api.app.httpx.AsyncClient", _FailingClient)


def _event(event_id: str) -> dict[str, Any]:
    return {"event_id": event_id, "type": "session.state_changed", "payload": {}}


async def _drive(
    forwarder: Coroutine[Any, Any, None], until: Callable[[], bool], *, what: str
) -> None:
    task = asyncio.create_task(forwarder)
    try:
        for _ in range(2000):
            if until():
                return
            await asyncio.sleep(0.002)
        raise AssertionError(f"timed out waiting for: {what}")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# -- classification -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "poison"),
    [
        (httpx.ConnectError("refused"), False),
        (httpx.ReadTimeout("slow"), False),
        (_refusal(500), False),
        (_refusal(503), False),
        (_refusal(408), False),
        (_refusal(429), False),
        (_refusal(400), True),
        (_refusal(401), True),
        (_refusal(404), True),
        (_refusal(422), True),
    ],
)
def test_only_a_definite_refusal_is_poison(exc: Exception, poison: bool) -> None:
    last_error, is_poison = _classify_forward_failure(exc)
    assert is_poison is poison
    assert last_error


# -- an outage never dead-letters ---------------------------------------------------


@pytest.mark.anyio
async def test_ten_connection_refusals_do_not_dead_letter(monkeypatch: pytest.MonkeyPatch) -> None:
    _fail_with(monkeypatch, lambda: httpx.ConnectError("connection refused"))
    state.store.enqueue_sync_event(_event("outage-1"))

    await _drive(
        state._sync_forward_loop(),
        lambda: state.sync_forward_errors >= 12,
        what="a dozen failed attempts",
    )

    assert state.store.sync_outbox_dead_total() == 0
    head = state.store.next_sync_event(10**12)
    assert head is not None and head.event_id == "outage-1", "the event is still queued"
    assert head.attempts >= 12, "attempts still grow, so the backoff does"
    assert TestClient(app).get("/health").json()["status"] == "ok"


@pytest.mark.anyio
async def test_a_5xx_is_an_outage_too(monkeypatch: pytest.MonkeyPatch) -> None:
    _fail_with(monkeypatch, lambda: _refusal(503))
    state.config = dataclasses.replace(state.config, media_forward_max_attempts=1)
    state.store.clear_with_media_event(_event("busy-1"))

    await _drive(
        state._media_forward_loop(),
        lambda: state.media_forward_errors >= 3,
        what="three failed attempts",
    )

    assert state.store.media_outbox_dead_total() == 0


@pytest.mark.anyio
@pytest.mark.parametrize("code", [408, 429])
async def test_a_timeout_or_throttle_status_keeps_retrying(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    _fail_with(monkeypatch, lambda: _refusal(code))
    state.config = dataclasses.replace(state.config, sync_forward_max_attempts=1)
    state.store.enqueue_sync_event(_event(f"slow-{code}"))

    await _drive(
        state._sync_forward_loop(),
        lambda: state.sync_forward_errors >= 3,
        what="three failed attempts",
    )

    assert state.store.sync_outbox_dead_total() == 0


# -- poison still does, and can be requeued ------------------------------------------


@pytest.mark.anyio
async def test_one_refusal_dead_letters_at_a_cap_of_one_and_requeue_revives_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fail_with(monkeypatch, lambda: _refusal(422))
    state.config = dataclasses.replace(state.config, media_forward_max_attempts=1)
    state.store.clear_with_media_event(_event("poison-1"))

    await _drive(
        state._media_forward_loop(),
        lambda: state.store.media_outbox_dead_total() == 1,
        what="the refused event to be parked",
    )

    assert state.media_forward_errors == 1, "a refusal is not retried past the cap"
    assert state.store.next_media_event(10**12) is None
    client = TestClient(app)
    health = client.get("/health").json()
    assert health["status"] == "degraded"
    assert "media=1" in health["detail"]

    # The operator's action: admin-gated, and it puts the row back as freshly queued.
    assert client.post("/admin/outbox/media/requeue-dead").status_code == 401
    assert client.post("/admin/outbox/nope/requeue-dead", headers=OWNER).status_code == 404
    resp = client.post("/admin/outbox/media/requeue-dead", headers=OWNER)
    assert resp.status_code == 200
    assert resp.json() == {"outbox": "media", "requeued": 1}
    assert state.store.media_outbox_dead_total() == 0
    revived = state.store.next_media_event(10**12)
    assert revived is not None and revived.event_id == "poison-1"
    assert revived.attempts == 0
    assert client.get("/health").json()["status"] == "ok"


@pytest.mark.anyio
async def test_the_retry_window_bounds_an_outage_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    # An outage longer than the window is dead-lettered after all -- but as a
    # deliberate, logged decision rather than a side effect of counting refusals.
    _fail_with(monkeypatch, lambda: httpx.ConnectError("connection refused"))
    state.config = dataclasses.replace(state.config, sync_retry_window_s=0.0)
    state.store.enqueue_sync_event(_event("stale-outage"))

    await _drive(
        state._sync_forward_loop(),
        lambda: state.store.sync_outbox_dead_total() == 1,
        what="the window to run out",
    )

    client = TestClient(app)
    assert "sync=1" in client.get("/health").json()["detail"]
    assert client.post("/admin/outbox/sync/requeue-dead", headers=OWNER).json() == {
        "outbox": "sync",
        "requeued": 1,
    }
    assert state.store.sync_outbox_dead_total() == 0


# -- the store ---------------------------------------------------------------------------


def test_transient_failures_grow_attempts_without_counting_toward_the_cap() -> None:
    store = SessionStore(":memory:")
    try:
        store.enqueue_sync_event(_event("mixed"))
        for attempt in range(1, 10):
            dead = store.retry_sync_event(
                "mixed",
                attempts=attempt,
                next_attempt_epoch=0.0,
                last_error="ConnectError",
                max_attempts=2,
                poison=False,
            )
            assert dead is False
        # Two refusals are what the cap of two counts; the nine outages before are not.
        assert (
            store.retry_sync_event(
                "mixed", attempts=10, next_attempt_epoch=0.0, last_error="422", max_attempts=2
            )
            is False
        )
        assert (
            store.retry_sync_event(
                "mixed", attempts=11, next_attempt_epoch=0.0, last_error="422", max_attempts=2
            )
            is True
        )
        assert store.sync_outbox_dead_total() == 1
    finally:
        store.close()


def test_migration_adds_poison_attempts_to_a_database_with_only_dead(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    legacy = sqlite3.connect(str(db_path))
    legacy.executescript(
        """
        CREATE TABLE media_outbox (
            event_id TEXT PRIMARY KEY, event_json TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, next_attempt_epoch REAL NOT NULL DEFAULT 0,
            created_epoch REAL NOT NULL, last_error TEXT, dead INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE sync_outbox (
            event_id TEXT PRIMARY KEY, event_json TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, next_attempt_epoch REAL NOT NULL DEFAULT 0,
            created_epoch REAL NOT NULL, last_error TEXT, dead INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO sync_outbox (event_id, event_json, created_epoch, dead)
        VALUES ('parked', '{"event_id": "parked", "type": "x", "payload": {}}', 0, 1);
        """
    )
    legacy.commit()
    legacy.close()

    store = SessionStore(str(db_path))
    try:
        assert store.sync_outbox_dead_total() == 1
        assert store.requeue_dead_sync_events() == 1
        item = store.next_sync_event(10**12)
        assert item is not None and item.event_id == "parked"
        assert (
            store.retry_sync_event(
                "parked", attempts=1, next_attempt_epoch=0.0, last_error="x", max_attempts=5
            )
            is False
        )
    finally:
        store.close()
