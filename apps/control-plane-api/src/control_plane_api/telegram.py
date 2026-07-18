"""Telegram delivery of saved visitor video messages (NUC-only).

Runs on the control plane (NUC) — the only tier permitted to hold outbound
integration secrets (ARCHITECTURE.md §2 trust model, ADR-0012); the door Pi
never sends to Telegram. Delivery is best-effort and off the door critical
path: it fires from the ingest fan-out (``service.ingest_batch``), never from
a bell press, and every failure is swallowed with a warning so it can never
affect ingestion.

Trigger: a session's transition to ``VIDEO_MESSAGE_SAVED`` — *not*
``VIDEO_MESSAGE_REVIEW`` — so a message the visitor discards (or that times
out) is never sent (ADR-0005 / ARCHITECTURE.md §9 privacy). The clip's bytes
are pulled on demand from door-api's admin media endpoint
(``GET /admin/media-inbox/{id}/file``, bearer-token) and streamed straight to
Telegram; no new copy of visitor media is persisted on the NUC.

Everything is transport-behind-a-Protocol so the trigger/lookup logic is
unit-testable without a network call (mirrors ``notify.py``). The feature is
disabled — a silent no-op — unless a bot token, at least one chat id, and
door-api media credentials are all configured, exactly like ``NtfyNotifier``
falling back to ``NullNotifier``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

from doorboard_contracts import DoorboardEvent
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane_api.models import MediaMirrorRow

logger = logging.getLogger("control_plane_api.telegram")

_SAVED_STATE = "VIDEO_MESSAGE_SAVED"
_VIDEO_KIND = "video_message"


class TelegramSender(Protocol):
    def send_video(self, *, video: bytes, filename: str, caption: str) -> None: ...

    def send_message(self, *, text: str) -> None: ...


class VideoSource(Protocol):
    """Fetches the finalized clip bytes for a recording (returns None on failure)."""

    def fetch(self, recording_id: str) -> bytes | None: ...


class TelegramClient:
    """Sends to one or more Telegram chats via the Bot API."""

    def __init__(
        self,
        *,
        bot_token: str,
        chat_ids: list[str],
        api_base_url: str = "https://api.telegram.org",
        timeout_s: float = 30.0,
    ) -> None:
        self._token = bot_token
        self._chat_ids = list(chat_ids)
        self._base_url = api_base_url.rstrip("/")
        self._timeout_s = timeout_s

    def _method_url(self, method: str) -> str:
        return f"{self._base_url}/bot{self._token}/{method}"

    def send_message(self, *, text: str) -> None:
        import httpx

        for chat_id in self._chat_ids:
            try:
                resp = httpx.post(
                    self._method_url("sendMessage"),
                    data={"chat_id": chat_id, "text": text},
                    timeout=self._timeout_s,
                )
                self._log_non_ok(resp, chat_id, "sendMessage")
            except Exception:
                logger.warning(
                    "telegram_send_message_failed", extra={"chat_id": chat_id}, exc_info=True
                )

    def send_video(self, *, video: bytes, filename: str, caption: str) -> None:
        import httpx

        for chat_id in self._chat_ids:
            try:
                resp = httpx.post(
                    self._method_url("sendVideo"),
                    data={"chat_id": chat_id, "caption": caption},
                    files={"video": (filename, video, "video/mp4")},
                    timeout=self._timeout_s,
                )
                self._log_non_ok(resp, chat_id, "sendVideo")
            except Exception:
                logger.warning(
                    "telegram_send_video_failed", extra={"chat_id": chat_id}, exc_info=True
                )

    @staticmethod
    def _log_non_ok(resp: object, chat_id: str, method: str) -> None:
        # Telegram returns HTTP 200 with {"ok": false, "description": ...} for
        # application-level failures (e.g. a wrong chat_id), so a raise-for-status
        # alone would miss them.
        ok = getattr(resp, "status_code", 0) == 200
        if ok:
            try:
                ok = bool(resp.json().get("ok", False))  # type: ignore[attr-defined]
            except Exception:
                ok = False
        if not ok:
            logger.warning("telegram_api_not_ok", extra={"chat_id": chat_id, "method": method})


class DoorApiVideoSource:
    """Pulls a video message's bytes from door-api's admin media endpoint."""

    def __init__(self, *, base_url: str, admin_token: str, timeout_s: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._admin_token = admin_token
        self._timeout_s = timeout_s

    def fetch(self, recording_id: str) -> bytes | None:
        import httpx

        url = f"{self._base_url}/admin/media-inbox/{recording_id}/file"
        try:
            resp = httpx.get(
                url,
                headers={"Authorization": f"Bearer {self._admin_token}"},
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            return resp.content
        except Exception:
            logger.warning(
                "door_api_media_fetch_failed",
                extra={"recording_id": recording_id},
                exc_info=True,
            )
            return None


class VideoMessageDelivery:
    """Sends a saved visitor video message to Telegram (best-effort no-op if unconfigured)."""

    def __init__(
        self,
        *,
        sender: TelegramSender | None = None,
        source: VideoSource | None = None,
        max_video_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self._sender = sender
        self._source = source
        self._max_video_bytes = max_video_bytes

    @property
    def enabled(self) -> bool:
        return self._sender is not None and self._source is not None

    def on_event(self, session: Session, event: DoorboardEvent, *, now: datetime) -> None:
        if self._sender is None or self._source is None:
            return
        if event.type != "session.state_changed":
            return
        if str(event.payload.to_state) != _SAVED_STATE:
            return

        session_id = str(event.payload.session_id)
        row = self._lookup_video_recording(session, session_id)
        if row is None or not row.recording_id:
            logger.info("telegram_video_no_recording", extra={"session_id": session_id})
            return

        caption = _build_caption(row, event)
        size_bytes = row.size_bytes or 0
        if size_bytes > self._max_video_bytes:
            mb = size_bytes / 1_000_000
            self._sender.send_message(
                text=(
                    f"{caption}\n(Clip is {mb:.0f} MB — too large for Telegram; "
                    f"open it from the admin video inbox.)"
                )
            )
            return

        video = self._source.fetch(row.recording_id)
        if video is None:
            logger.warning("telegram_video_fetch_failed", extra={"recording_id": row.recording_id})
            return

        self._sender.send_video(
            video=video,
            filename=f"video_message_{row.recording_id}.mp4",
            caption=caption,
        )
        logger.info("telegram_video_sent", extra={"recording_id": row.recording_id})

    @staticmethod
    def _lookup_video_recording(session: Session, session_id: str) -> MediaMirrorRow | None:
        stmt = (
            select(MediaMirrorRow)
            .where(
                MediaMirrorRow.session_id == session_id,
                MediaMirrorRow.kind == _VIDEO_KIND,
                MediaMirrorRow.deleted_at.is_(None),
            )
            .order_by(MediaMirrorRow.updated_at.desc())
        )
        return session.scalars(stmt).first()


def _build_caption(row: MediaMirrorRow, event: DoorboardEvent) -> str:
    parts = ["📹 New video message at the door"]
    if row.duration_s:
        parts.append(f"({row.duration_s:.0f}s)")
    occurred_at = getattr(event, "occurred_at", None)
    if isinstance(occurred_at, datetime):
        parts.append(f"at {occurred_at:%H:%M}")
    return " ".join(parts)


def build_telegram_sender(
    *, bot_token: str, chat_ids: list[str], api_base_url: str
) -> TelegramSender | None:
    if not bot_token or not chat_ids:
        return None
    return TelegramClient(bot_token=bot_token, chat_ids=chat_ids, api_base_url=api_base_url)


def build_video_source(*, door_api_base_url: str, door_api_admin_token: str) -> VideoSource | None:
    if not door_api_base_url or not door_api_admin_token:
        return None
    return DoorApiVideoSource(base_url=door_api_base_url, admin_token=door_api_admin_token)
