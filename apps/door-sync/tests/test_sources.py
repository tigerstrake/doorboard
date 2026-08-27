"""MediaEventSource.handle_event — the real-time SSE fan-out.

Each media event drives two independent, idempotent effects: NAS archive work
(``enqueue_recording`` / ``enqueue_thumbnail``) *and* a metadata mirror to the
NUC control plane (``enqueue_event``). The mirror is what populates the NUC's
``media_mirror`` read model; without it Telegram video-message delivery finds no
recording for the session and never sends. These tests pin both effects so a
future refactor can't silently drop the NUC mirror again.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import pytest
from door_sync.engine import SyncEngine
from door_sync.sources import MediaEventSource


def _make_source() -> tuple[MediaEventSource, Mock]:
    engine = Mock(spec=SyncEngine)
    source = MediaEventSource(engine, base_url="http://door-media")
    return source, engine


def test_recording_started_mirrors_to_nuc_only(helpers) -> None:
    """`media.recording_started` is metadata only: mirror it, no NAS artifact."""
    source, engine = _make_source()
    event = helpers.make_media_recording_started_dict()

    source.handle_event(event)

    engine.enqueue_event.assert_called_once_with(event)
    engine.enqueue_recording.assert_not_called()
    engine.enqueue_thumbnail.assert_not_called()


def test_recording_finalized_archives_to_nas_and_mirrors_to_nuc(helpers) -> None:
    """`media.recording_finalized` must both enqueue the NAS clip AND mirror to the NUC."""
    source, engine = _make_source()
    event = helpers.make_media_recording_finalized_dict()
    payload = event["payload"]

    source.handle_event(event)

    engine.enqueue_recording.assert_called_once_with(
        recording_id=payload["recording_id"],
        local_path=payload["path"],
        sha256=payload["sha256"],
        trace_id=event["trace_id"],
    )
    engine.enqueue_event.assert_called_once_with(event)


def test_thumbnail_ready_archives_to_nas_and_mirrors_to_nuc(helpers) -> None:
    """`media.thumbnail_ready` must both enqueue the NAS thumbnail AND mirror to the NUC."""
    source, engine = _make_source()
    event = helpers.make_media_thumbnail_ready_dict()
    payload = event["payload"]

    source.handle_event(event)

    engine.enqueue_thumbnail.assert_called_once_with(
        recording_id=payload["recording_id"],
        local_path=payload["path"],
        trace_id=event["trace_id"],
    )
    engine.enqueue_event.assert_called_once_with(event)


def test_unknown_event_type_is_ignored(helpers) -> None:
    """A non-media event (e.g. session.*) triggers no enqueues on this source."""
    source, engine = _make_source()

    source.handle_event(helpers.make_session_event_dict())

    engine.enqueue_recording.assert_not_called()
    engine.enqueue_thumbnail.assert_not_called()
    engine.enqueue_event.assert_not_called()


def _a_real_validation_error() -> Exception:
    """A genuine parse_event ValidationError — the exact error a schema-invalid frame
    raises inside enqueue_event, which the old per-frame catch (JSONDecodeError/KeyError)
    let escape."""
    from doorboard_contracts.events import parse_event

    try:
        parse_event({"type": "bogus.not_a_real_event", "payload": {}})
    except Exception as exc:  # noqa: BLE001
        return exc
    raise AssertionError("parse_event unexpectedly accepted a bogus event")


def test_a_schema_invalid_frame_is_dropped_not_stream_killing(helpers) -> None:
    """A frame that fails contract validation must NOT tear the clip-sync stream down.

    parse_event raises pydantic ValidationError, which the old JSONDecodeError/KeyError-only
    catch missed, so one bad event escaped to the reconnect path and killed the stream —
    mislogged as `media_sse_disconnected`. _handle_frame now swallows any per-frame error.
    """
    source, engine = _make_source()
    engine.enqueue_event.side_effect = _a_real_validation_error()
    event = helpers.make_media_recording_started_dict()

    # Must not raise — the stream survives a bad frame.
    source._handle_frame(json.dumps(event))
    engine.enqueue_event.assert_called_once()


def test_a_non_json_frame_is_dropped(helpers) -> None:
    source, engine = _make_source()
    source._handle_frame("this is not json {{{")
    engine.enqueue_event.assert_not_called()


def test_a_good_frame_is_handled(helpers) -> None:
    source, engine = _make_source()
    event = helpers.make_media_recording_started_dict()
    source._handle_frame(json.dumps(event))
    engine.enqueue_event.assert_called_once_with(event)


# -- reconcile on reconnect (SSE has no replay) -----------------------------


@pytest.mark.anyio
async def test_first_connection_does_not_reconcile() -> None:
    """The startup reconcile (in the lifespan) covers the first connection, so the source must
    not reconcile again on its first connect."""
    source, engine = _make_source()
    engine.reconcile_from_media = AsyncMock(return_value=0)
    await source._on_connected()
    engine.reconcile_from_media.assert_not_called()


@pytest.mark.anyio
async def test_reconnect_reconciles_to_close_the_gap() -> None:
    """Every RE-connection reconciles, to recover events door-media finalized during the gap
    (SSE never replays them)."""
    source, engine = _make_source()
    engine.reconcile_from_media = AsyncMock(return_value=3)
    await source._on_connected()  # first connect — covered by startup reconcile
    await source._on_connected()  # reconnect
    await source._on_connected()  # reconnect again
    assert engine.reconcile_from_media.await_count == 2


@pytest.mark.anyio
async def test_a_reconcile_failure_does_not_kill_the_stream() -> None:
    """A reconcile that fails on reconnect is logged and swallowed; a later reconcile catches
    up. It must never propagate and tear the stream down."""
    source, engine = _make_source()
    engine.reconcile_from_media = AsyncMock(side_effect=RuntimeError("door-media hiccup"))
    await source._on_connected()  # first connect
    await source._on_connected()  # reconnect — reconcile raises internally, must not propagate
    engine.reconcile_from_media.assert_awaited_once()
