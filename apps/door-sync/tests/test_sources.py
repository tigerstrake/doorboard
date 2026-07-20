"""MediaEventSource.handle_event — the real-time SSE fan-out.

Each media event drives two independent, idempotent effects: NAS archive work
(``enqueue_recording`` / ``enqueue_thumbnail``) *and* a metadata mirror to the
NUC control plane (``enqueue_event``). The mirror is what populates the NUC's
``media_mirror`` read model; without it Telegram video-message delivery finds no
recording for the session and never sends. These tests pin both effects so a
future refactor can't silently drop the NUC mirror again.
"""

from __future__ import annotations

from unittest.mock import Mock

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
