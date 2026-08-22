"""ADR-0038: don't poll the relay when a pickup is impossible.

The door polled the enrollment relay every 5 seconds, forever, whether or not any
invite was outstanding. Measured on the live door: 74,187 polls since the last
restart, `relay_bundles_enrolled = 0`. Every one is a metered serverless
invocation, and it had consumed 75% of a 1,000,000/month free tier — at which
point the deployment is paused.

The gate is "could a bundle possibly be waiting?", which is "is an invite open?",
plus a grace window so a bundle submitted moments before an invite closes is
still collected.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from door_visiond.relay_client import RelayWorker


@dataclass
class _FakeTransport:
    polls: int = 0
    key_publishes: int = 0
    invite_registrations: int = 0

    def poll_pickup(self):
        self.polls += 1

        class _Batch:
            items: list = []

        return _Batch()

    def publish_door_key(self, _publication) -> None:
        self.key_publishes += 1

    def register_invite(self, _registration) -> None:
        self.invite_registrations += 1


@dataclass
class _FakeHandler:
    open_invites: bool = False
    collection_allowed: bool = True
    acks: list = field(default_factory=list)

    def relay_collection_allowed(self) -> bool:
        return self.collection_allowed

    def relay_has_open_invites(self) -> bool:
        return self.open_invites

    def relay_door_key_publication(self):
        return object()

    def relay_invite_registrations(self) -> list:
        return []

    def relay_handle_bundle(self, _bundle):
        raise AssertionError("no bundles in these tests")


def _worker(handler: _FakeHandler, transport: _FakeTransport, **kw) -> RelayWorker:
    return RelayWorker(
        transport=transport,
        handler=handler,
        poll_interval_s=kw.pop("poll_interval_s", 5.0),
        backoff_max_s=kw.pop("backoff_max_s", 60.0),
        **kw,
    )


async def _tick(worker: RelayWorker) -> None:
    await worker._tick()  # noqa: SLF001 - the unit under test


@pytest.mark.anyio
async def test_no_open_invite_means_no_poll():
    """The whole point: an idle door must not call the relay at all."""
    transport, handler = _FakeTransport(), _FakeHandler(open_invites=False)
    worker = _worker(handler, transport)
    for _ in range(20):
        await _tick(worker)
    assert transport.polls == 0
    assert worker._stats.polls_skipped_idle == 20  # noqa: SLF001


@pytest.mark.anyio
async def test_an_open_invite_polls_normally():
    transport, handler = _FakeTransport(), _FakeHandler(open_invites=True)
    worker = _worker(handler, transport)
    for _ in range(5):
        await _tick(worker)
    assert transport.polls == 5
    assert worker._stats.polls_skipped_idle == 0  # noqa: SLF001


@pytest.mark.anyio
async def test_polling_continues_through_the_grace_window():
    """An invite is consumed the instant a bundle is claimed.

    Stopping dead on consumption could strand a bundle that arrived in the same
    breath, so polling continues briefly after the last invite closes.
    """
    transport = _FakeTransport()
    handler = _FakeHandler(open_invites=True)
    worker = _worker(handler, transport, idle_grace_s=300.0)
    await _tick(worker)
    assert transport.polls == 1

    handler.open_invites = False  # consumed or expired
    await _tick(worker)
    assert transport.polls == 2, "should still poll inside the grace window"


@pytest.mark.anyio
async def test_polling_stops_once_the_grace_window_lapses(monkeypatch):
    transport = _FakeTransport()
    handler = _FakeHandler(open_invites=True)
    worker = _worker(handler, transport, idle_grace_s=10.0)

    clock = {"now": 1000.0}
    monkeypatch.setattr("door_visiond.relay_client.time.monotonic", lambda: clock["now"])

    await _tick(worker)
    assert transport.polls == 1

    handler.open_invites = False
    clock["now"] += 5.0
    await _tick(worker)
    assert transport.polls == 2, "still inside grace"

    clock["now"] += 20.0
    await _tick(worker)
    assert transport.polls == 2, "grace lapsed, must stop"
    assert worker._stats.polls_skipped_idle == 1  # noqa: SLF001


@pytest.mark.anyio
async def test_a_door_that_never_had_an_invite_never_polls(monkeypatch):
    """No grace on a cold start — `None` must not read as "just saw one"."""
    transport, handler = _FakeTransport(), _FakeHandler(open_invites=False)
    worker = _worker(handler, transport, idle_grace_s=300.0)
    monkeypatch.setattr("door_visiond.relay_client.time.monotonic", lambda: 0.0)
    await _tick(worker)
    assert transport.polls == 0


@pytest.mark.anyio
async def test_privacy_gate_still_wins_over_the_idle_gate():
    """P-18: collection off means no poll even with an invite open.

    Checked separately because the two gates are independent, and a refactor that
    merged them could accidentally let privacy mode be overridden.
    """
    transport = _FakeTransport()
    handler = _FakeHandler(open_invites=True, collection_allowed=False)
    worker = _worker(handler, transport)
    await _tick(worker)
    assert transport.polls == 0


@pytest.mark.anyio
async def test_the_periodic_resync_is_not_five_minutes_anymore():
    """request_resync() covers real changes, so the periodic pass is belt-and-braces.

    At 300s it was 288 needless relay calls a day even with zero invites, which is
    the same waste in miniature.
    """
    import inspect

    default = inspect.signature(RelayWorker.__init__).parameters["resync_interval_s"].default
    assert default >= 3600.0, f"periodic resync back down to {default}s"


def test_the_store_gate_matches_the_registration_query():
    """has_open_invite() must agree with open_invite_registrations().

    They are separate SQL statements over the same three conditions (unconsumed,
    unrevoked, unexpired). If they drift, the door either polls for invites it
    cannot serve or refuses to poll for ones it can.
    """
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "src/door_visiond/enrollment.py").read_text()
    conditions = re.findall(r"consumed_at IS NULL AND revoked_at IS NULL AND expires_at > \?", src)
    assert len(conditions) >= 2, "the two queries no longer share the same conditions"
