"""Telegram video-message delivery: trigger logic, transport, and fail-safes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from control_plane_api.models import MediaMirrorRow
from control_plane_api.settings import Settings
from control_plane_api.telegram import (
    DoorApiVideoSource,
    TelegramClient,
    VideoMessageDelivery,
)
from doorboard_contracts import parse_event

from .factories import build_event

NOW = datetime(2026, 7, 18, 17, 30, 0, tzinfo=UTC)
SID = "11111111-1111-4111-8111-111111111111"
RID = "22222222-2222-4222-8222-222222222222"


class RecordingSender:
    """TelegramSender test double. Records the chat_ids each call targeted
    (None = the sender's default / all chats)."""

    def __init__(self) -> None:
        self.videos: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    def send_video(
        self, *, video: bytes, filename: str, caption: str, chat_ids: list[str] | None = None
    ) -> None:
        self.videos.append(
            {"video": video, "filename": filename, "caption": caption, "chat_ids": chat_ids}
        )

    def send_message(self, *, text: str, chat_ids: list[str] | None = None) -> None:
        self.messages.append({"text": text, "chat_ids": chat_ids})


class FakeSource:
    """VideoSource test double."""

    def __init__(self, data: bytes | None) -> None:
        self._data = data
        self.calls: list[str] = []

    def fetch(self, recording_id: str) -> bytes | None:
        self.calls.append(recording_id)
        return self._data


def _add_recording(
    session_factory,
    *,
    session_id: str = SID,
    recording_id: str = RID,
    kind: str = "video_message",
    size_bytes: int = 2000,
    duration_s: float = 6.0,
    deleted_at: datetime | None = None,
) -> None:
    with session_factory() as session:
        session.add(
            MediaMirrorRow(
                recording_id=recording_id,
                session_id=session_id,
                kind=kind,
                path="recordings/video_message.mp4",
                size_bytes=size_bytes,
                duration_s=duration_s,
                deleted_at=deleted_at,
                updated_at=NOW,
            )
        )
        session.commit()


def _saved_event(session_id: str = SID, *, recipients: list[str] | None = None):
    overrides: dict[str, Any] = {"to_state": "VIDEO_MESSAGE_SAVED", "session_id": session_id}
    if recipients is not None:
        overrides["recipients"] = recipients
    return parse_event(build_event("session.state_changed", payload_overrides=overrides))


# Per-recipient routing config (ADR-0014): Tiger has a chat id; Adam is a known
# recipient with no chat configured yet (blank).
RECIPIENT_MAP = {"tiger": "8397445760", "adam": ""}


def _run(session_factory, delivery: VideoMessageDelivery, event) -> None:
    with session_factory() as session:
        delivery.on_event(session, event, now=NOW)


def test_saved_video_message_is_sent(session_factory) -> None:
    _add_recording(session_factory)
    sender, source = RecordingSender(), FakeSource(b"MP4BYTES")
    delivery = VideoMessageDelivery(sender=sender, source=source)

    _run(session_factory, delivery, _saved_event())

    assert source.calls == [RID]
    assert len(sender.videos) == 1
    sent = sender.videos[0]
    assert sent["video"] == b"MP4BYTES"
    assert RID in sent["filename"] and sent["filename"].endswith(".mp4")
    assert "video message" in sent["caption"].lower()


def test_non_saved_state_change_is_ignored(session_factory) -> None:
    _add_recording(session_factory)
    sender, source = RecordingSender(), FakeSource(b"x")
    delivery = VideoMessageDelivery(sender=sender, source=source)

    event = parse_event(
        build_event(
            "session.state_changed",
            payload_overrides={"to_state": "VISITOR_MODE", "session_id": SID},
        )
    )
    _run(session_factory, delivery, event)

    assert sender.videos == [] and source.calls == []


def test_unrelated_event_type_is_ignored(session_factory) -> None:
    _add_recording(session_factory)
    sender, source = RecordingSender(), FakeSource(b"x")
    delivery = VideoMessageDelivery(sender=sender, source=source)

    _run(session_factory, delivery, parse_event(build_event("session.started")))

    assert sender.videos == [] and source.calls == []


def test_no_matching_recording_sends_nothing(session_factory) -> None:
    # A saved event for a session with no projected video_message recording.
    sender, source = RecordingSender(), FakeSource(b"x")
    delivery = VideoMessageDelivery(sender=sender, source=source)

    _run(session_factory, delivery, _saved_event())

    assert sender.videos == [] and source.calls == []


def test_deleted_recording_is_not_sent(session_factory) -> None:
    # Discarded/purged clips carry deleted_at and must never be delivered.
    _add_recording(session_factory, deleted_at=NOW)
    sender, source = RecordingSender(), FakeSource(b"x")
    delivery = VideoMessageDelivery(sender=sender, source=source)

    _run(session_factory, delivery, _saved_event())

    assert sender.videos == [] and source.calls == []


def test_bell_clip_is_not_sent(session_factory) -> None:
    # Only visitor video messages are delivered, never bell clips.
    _add_recording(session_factory, kind="bell_clip")
    sender, source = RecordingSender(), FakeSource(b"x")
    delivery = VideoMessageDelivery(sender=sender, source=source)

    _run(session_factory, delivery, _saved_event())

    assert sender.videos == [] and source.calls == []


def test_disabled_delivery_is_a_noop(session_factory) -> None:
    _add_recording(session_factory)
    delivery = VideoMessageDelivery(sender=None, source=None)
    assert delivery.enabled is False
    # Must not raise even though nothing is configured.
    _run(session_factory, delivery, _saved_event())


def test_oversized_video_falls_back_to_text(session_factory) -> None:
    _add_recording(session_factory, size_bytes=200_000_000)
    sender, source = RecordingSender(), FakeSource(b"x")
    delivery = VideoMessageDelivery(sender=sender, source=source, max_video_bytes=50 * 1024 * 1024)

    _run(session_factory, delivery, _saved_event())

    assert sender.videos == []  # not uploaded
    assert source.calls == []  # not even fetched
    assert len(sender.messages) == 1 and "too large" in sender.messages[0]["text"].lower()


def test_fetch_failure_sends_nothing(session_factory) -> None:
    _add_recording(session_factory)
    sender, source = RecordingSender(), FakeSource(None)  # fetch returns None
    delivery = VideoMessageDelivery(sender=sender, source=source)

    _run(session_factory, delivery, _saved_event())

    assert source.calls == [RID]
    assert sender.videos == []


# ── per-recipient routing (ADR-0014) ───────────────────────────────────────


def test_recipients_none_broadcasts_to_all(session_factory) -> None:
    # Legacy path: no recipients chosen => the delivery does not narrow the
    # target, so the sender falls back to all configured chats (chat_ids=None).
    _add_recording(session_factory)
    sender, source = RecordingSender(), FakeSource(b"MP4")
    delivery = VideoMessageDelivery(sender=sender, source=source, recipient_map=RECIPIENT_MAP)

    _run(session_factory, delivery, _saved_event(recipients=None))

    assert len(sender.videos) == 1
    assert sender.videos[0]["chat_ids"] is None  # None => sender's default (all)


def test_recipients_tiger_sends_only_to_tiger(session_factory) -> None:
    _add_recording(session_factory)
    sender, source = RecordingSender(), FakeSource(b"MP4")
    delivery = VideoMessageDelivery(sender=sender, source=source, recipient_map=RECIPIENT_MAP)

    _run(session_factory, delivery, _saved_event(recipients=["tiger"]))

    assert len(sender.videos) == 1
    assert sender.videos[0]["chat_ids"] == ["8397445760"]
    assert source.calls == [RID]


def test_recipients_tiger_and_adam_sends_only_to_configured(session_factory) -> None:
    # Adam's chat id is blank, so ["tiger", "adam"] resolves to Tiger only.
    _add_recording(session_factory)
    sender, source = RecordingSender(), FakeSource(b"MP4")
    delivery = VideoMessageDelivery(sender=sender, source=source, recipient_map=RECIPIENT_MAP)

    _run(session_factory, delivery, _saved_event(recipients=["tiger", "adam"]))

    assert len(sender.videos) == 1
    assert sender.videos[0]["chat_ids"] == ["8397445760"]


def test_recipient_with_blank_chat_id_sends_to_nobody(session_factory, caplog) -> None:
    # Routing to Adam alone (blank chat id): saved on door-api, nothing sent,
    # no error, and the skip is logged.
    _add_recording(session_factory)
    sender, source = RecordingSender(), FakeSource(b"MP4")
    delivery = VideoMessageDelivery(sender=sender, source=source, recipient_map=RECIPIENT_MAP)

    import logging

    with caplog.at_level(logging.INFO):
        _run(session_factory, delivery, _saved_event(recipients=["adam"]))

    assert sender.videos == []
    assert sender.messages == []
    assert source.calls == []  # not even fetched
    assert "telegram_video_recipient_unconfigured" in caplog.text


def test_unknown_recipient_key_is_ignored(session_factory, caplog) -> None:
    # An unknown key is logged and skipped; here it is the only recipient, so
    # nothing is sent (no error).
    _add_recording(session_factory)
    sender, source = RecordingSender(), FakeSource(b"MP4")
    delivery = VideoMessageDelivery(sender=sender, source=source, recipient_map=RECIPIENT_MAP)

    import logging

    with caplog.at_level(logging.INFO):
        _run(session_factory, delivery, _saved_event(recipients=["nobody"]))

    assert sender.videos == []
    assert source.calls == []
    assert "telegram_video_recipient_unknown" in caplog.text


def test_unknown_key_mixed_with_known_still_sends_to_known(session_factory) -> None:
    _add_recording(session_factory)
    sender, source = RecordingSender(), FakeSource(b"MP4")
    delivery = VideoMessageDelivery(sender=sender, source=source, recipient_map=RECIPIENT_MAP)

    _run(session_factory, delivery, _saved_event(recipients=["nobody", "tiger"]))

    assert len(sender.videos) == 1
    assert sender.videos[0]["chat_ids"] == ["8397445760"]


def test_oversized_video_routes_text_pointer_to_chosen_recipient(session_factory) -> None:
    # The too-large fallback also honors routing: only Tiger gets the pointer.
    _add_recording(session_factory, size_bytes=200_000_000)
    sender, source = RecordingSender(), FakeSource(b"x")
    delivery = VideoMessageDelivery(
        sender=sender,
        source=source,
        max_video_bytes=50 * 1024 * 1024,
        recipient_map=RECIPIENT_MAP,
    )

    _run(session_factory, delivery, _saved_event(recipients=["tiger"]))

    assert sender.videos == []
    assert len(sender.messages) == 1
    assert sender.messages[0]["chat_ids"] == ["8397445760"]


# ── transport ────────────────────────────────────────────────────────────


class _Resp:
    def __init__(
        self, *, status_code: int = 200, payload: dict | None = None, content: bytes = b""
    ):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}
        self.content = content

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_client_send_video_posts_multipart_to_each_chat(monkeypatch) -> None:
    import httpx

    calls: list[dict[str, Any]] = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)

    client = TelegramClient(
        bot_token="TОKEN", chat_ids=["111", "222"], api_base_url="https://tg.example"
    )
    client.send_video(video=b"MP4", filename="clip.mp4", caption="hi")

    assert len(calls) == 2
    assert calls[0]["url"] == "https://tg.example/botTОKEN/sendVideo"
    assert calls[0]["data"]["chat_id"] == "111"
    assert calls[0]["data"]["caption"] == "hi"
    assert calls[0]["files"]["video"][0] == "clip.mp4"
    assert calls[0]["files"]["video"][1] == b"MP4"
    assert calls[1]["data"]["chat_id"] == "222"


def test_client_send_video_targets_explicit_chat_subset(monkeypatch) -> None:
    import httpx

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: calls.append({"url": url, **kw}) or _Resp()
    )

    client = TelegramClient(
        bot_token="T", chat_ids=["111", "222"], api_base_url="https://tg.example"
    )
    # An explicit subset overrides the configured chats (ADR-0014 routing).
    client.send_video(video=b"MP4", filename="clip.mp4", caption="hi", chat_ids=["222"])

    assert len(calls) == 1
    assert calls[0]["data"]["chat_id"] == "222"


def test_client_swallows_transport_errors(monkeypatch) -> None:
    import httpx

    def boom(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", boom)
    client = TelegramClient(bot_token="t", chat_ids=["1"], api_base_url="https://tg.example")
    # Must not raise — delivery is best-effort.
    client.send_video(video=b"x", filename="c.mp4", caption="")
    client.send_message(text="hi")


def test_door_api_source_fetches_bytes(monkeypatch) -> None:
    import httpx

    seen: dict[str, Any] = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers")
        return _Resp(content=b"VIDEO")

    monkeypatch.setattr(httpx, "get", fake_get)
    source = DoorApiVideoSource(base_url="http://door.local:8080", admin_token="secret")

    assert source.fetch(RID) == b"VIDEO"
    assert seen["url"] == f"http://door.local:8080/admin/media-inbox/{RID}/file"
    assert seen["headers"]["Authorization"] == "Bearer secret"


def test_door_api_source_returns_none_on_error(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(status_code=404))
    source = DoorApiVideoSource(base_url="http://door.local:8080", admin_token="secret")
    assert source.fetch(RID) is None


def test_chat_id_list_parsing() -> None:
    cfg = Settings(TELEGRAM_CHAT_IDS=" 111, 222 ,, 333 ")
    assert cfg.telegram_chat_id_list == ["111", "222", "333"]
    assert Settings(TELEGRAM_CHAT_IDS="").telegram_chat_id_list == []


def test_video_message_recipient_map_parsing() -> None:
    # "key:chatid" comma-separated; a blank chat id (Adam) => known recipient,
    # no chat configured yet.
    cfg = Settings(VIDEO_MESSAGE_RECIPIENTS="tiger:8397445760,adam:")
    assert cfg.video_message_recipient_map == {"tiger": "8397445760", "adam": ""}
    # Empty config => no recipients (pure broadcast).
    assert Settings(VIDEO_MESSAGE_RECIPIENTS="").video_message_recipient_map == {}
    # Whitespace, a bare key, and case are all normalized.
    cfg2 = Settings(VIDEO_MESSAGE_RECIPIENTS=" Tiger : 8397445760 , adam ")
    assert cfg2.video_message_recipient_map == {"tiger": "8397445760", "adam": ""}
