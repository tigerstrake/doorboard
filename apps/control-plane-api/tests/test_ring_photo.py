"""A picture of whoever rang, sent after the ring text (ADR-0022).

The owner asked for the text immediately and the picture "as soon as that is possible".
Those are different moments — the ring alert fires on `RINGING`, but a still only exists
once door-media has finalized the bell clip and cut a thumbnail — so this is a second,
independent message rather than a slower first one. These tests pin that independence and
the kinds it must refuse to send.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from control_plane_api.models import MediaMirrorRow
from control_plane_api.telegram import RingPhotoDelivery
from doorboard_contracts import parse_event

from .factories import build_event

NOW = datetime(2026, 8, 17, 14, 5, 0, tzinfo=UTC)
RID = "33333333-3333-4333-8333-333333333333"
SID = "44444444-4444-4444-8444-444444444444"
JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"


class RecordingSender:
    def __init__(self) -> None:
        self.photos: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.videos: list[dict[str, Any]] = []

    def send_photo(
        self, *, photo: bytes, filename: str, caption: str, chat_ids: list[str] | None = None
    ) -> None:
        self.photos.append(
            {"photo": photo, "filename": filename, "caption": caption, "chat_ids": chat_ids}
        )

    def send_video(
        self, *, video: bytes, filename: str, caption: str, chat_ids: list[str] | None = None
    ) -> None:
        self.videos.append({"video": video})

    def send_message(self, *, text: str, chat_ids: list[str] | None = None) -> None:
        self.messages.append({"text": text, "chat_ids": chat_ids})


class FakeThumbnails:
    def __init__(self, data: bytes | None) -> None:
        self._data = data
        self.calls: list[str] = []

    def fetch(self, recording_id: str) -> bytes | None:
        self.calls.append(recording_id)
        return self._data


def _mirror(
    session_factory,
    *,
    recording_id: str = RID,
    kind: str = "bell_clip",
    deleted_at: datetime | None = None,
) -> None:
    with session_factory() as session:
        session.add(
            MediaMirrorRow(
                recording_id=recording_id,
                session_id=SID,
                kind=kind,
                path="recordings/bell.mp4",
                thumbnail_path="thumbnails/bell.jpg",
                deleted_at=deleted_at,
                updated_at=NOW,
            )
        )
        session.commit()


def _thumbnail_event(recording_id: str = RID):
    return parse_event(
        build_event(
            "media.thumbnail_ready",
            payload_overrides={"recording_id": recording_id, "path": "thumbnails/bell.jpg"},
        )
    )


def _run(session_factory, delivery: RingPhotoDelivery, event) -> None:
    with session_factory() as session:
        delivery.on_event(session, event, now=NOW)


def _delivery(source: FakeThumbnails | None = None) -> tuple[RingPhotoDelivery, RecordingSender]:
    sender = RecordingSender()
    return RingPhotoDelivery(sender=sender, source=source or FakeThumbnails(JPEG)), sender


class TestSendsThePicture:
    def test_a_bell_clip_thumbnail_is_sent(self, session_factory) -> None:
        _mirror(session_factory)
        delivery, sender = _delivery()

        _run(session_factory, delivery, _thumbnail_event())

        assert len(sender.photos) == 1
        assert sender.photos[0]["photo"] == JPEG
        assert sender.photos[0]["filename"].endswith(".jpg")

    def test_the_caption_stands_alone(self, session_factory) -> None:
        """It may land well after the text alert, so it cannot read as a fragment."""
        _mirror(session_factory)
        delivery, sender = _delivery()

        _run(session_factory, delivery, _thumbnail_event())

        caption = sender.photos[0]["caption"]
        assert "door" in caption.lower()

    def test_it_goes_to_the_default_chats(self, session_factory) -> None:
        """Not per-recipient routed: this is an owner alert, not a visitor's message."""
        _mirror(session_factory)
        delivery, sender = _delivery()

        _run(session_factory, delivery, _thumbnail_event())

        assert sender.photos[0]["chat_ids"] is None


class TestRefusesEverythingElse:
    def test_a_photo_booth_thumbnail_is_never_sent(self, session_factory) -> None:
        """A visitor's photo-booth still arriving on the owner's phone unbidden would be
        a privacy defect, not a feature — it has its own consented flow."""
        _mirror(session_factory, kind="photo_booth")
        delivery, sender = _delivery()

        _run(session_factory, delivery, _thumbnail_event())

        assert sender.photos == []

    def test_a_video_message_thumbnail_is_never_sent(self, session_factory) -> None:
        """Video messages deliver as video via ADR-0012; a still would duplicate them."""
        _mirror(session_factory, kind="video_message")
        delivery, sender = _delivery()

        _run(session_factory, delivery, _thumbnail_event())

        assert sender.photos == []

    def test_a_deleted_recording_is_not_sent(self, session_factory) -> None:
        _mirror(session_factory, deleted_at=NOW)
        delivery, sender = _delivery()

        _run(session_factory, delivery, _thumbnail_event())

        assert sender.photos == []

    def test_an_unmirrored_recording_is_not_guessed_at(self, session_factory) -> None:
        """Without the row we cannot tell a bell clip from a photo-booth still."""
        delivery, sender = _delivery()

        _run(session_factory, delivery, _thumbnail_event("99999999-9999-4999-8999-999999999999"))

        assert sender.photos == []

    def test_other_event_types_are_ignored(self, session_factory) -> None:
        _mirror(session_factory)
        delivery, sender = _delivery()
        ringing = parse_event(
            build_event("session.state_changed", payload_overrides={"to_state": "RINGING"})
        )

        _run(session_factory, delivery, ringing)

        # The text alert for this event is notify.py's job, not this one's.
        assert sender.photos == []
        assert sender.messages == []


class TestFailSafes:
    def test_a_failed_fetch_sends_nothing_and_does_not_raise(self, session_factory) -> None:
        """The owner keeps the text alert; only the picture is lost."""
        _mirror(session_factory)
        delivery, sender = _delivery(FakeThumbnails(None))

        _run(session_factory, delivery, _thumbnail_event())

        assert sender.photos == []

    def test_unconfigured_is_a_silent_no_op(self, session_factory) -> None:
        delivery = RingPhotoDelivery(sender=None, source=FakeThumbnails(JPEG))

        assert delivery.enabled is False
        _run(session_factory, delivery, _thumbnail_event())

    def test_no_source_is_a_silent_no_op(self, session_factory) -> None:
        sender = RecordingSender()
        delivery = RingPhotoDelivery(sender=sender, source=None)

        assert delivery.enabled is False
        _run(session_factory, delivery, _thumbnail_event())

        assert sender.photos == []

    def test_the_thumbnail_is_fetched_only_for_a_bell_clip(self, session_factory) -> None:
        """Kind is checked before the fetch, so an ignored kind costs no HTTP call."""
        _mirror(session_factory, kind="photo_booth")
        source = FakeThumbnails(JPEG)
        delivery, _ = _delivery(source)

        _run(session_factory, delivery, _thumbnail_event())

        assert source.calls == []
