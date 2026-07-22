"""FastAPI application for door-media.

Routes:
  GET  /health          — service + stream health
  GET  /metrics         — Prometheus-style text metrics
  GET  /streams         — stream metadata for kiosk UIs (unauthenticated)
  GET  /recordings      — list of recordings (admin-auth)
  DELETE /recordings/{id} — delete a recording (admin-auth)

Auth: ``DOOR_MEDIA_ADMIN_TOKEN`` env var. Empty closes protected routes.

Session event integration:
  - ``POST /internal/session_event`` accepts ``session.state_changed`` payloads
    to trigger recording windows.  This endpoint is loopback-only; it is NOT
    exposed via the external bind address in production (nginx/Caddy handles
    routing).

All events broadcast over ``GET /events`` (SSE) so kiosk UIs and door-sync
can consume them without polling.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, cast
from uuid import UUID

from doorboard_contracts.events import (
    SessionState,
    SessionStateChangedEvent,
)
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from door_media._uuid7 import uuid7
from door_media.adapters import MediaRouter, StreamInfo
from door_media.db import RecordingDB
from door_media.emitter import subscribe_broadcast_queue, unsubscribe_broadcast_queue
from door_media.mediamtx_router import MediaMTXRouter
from door_media.mock_router import MockMediaRouter
from door_media.service import RecordingService
from door_media.settings import Settings
from door_media.settings import settings as get_settings

logger = logging.getLogger("door_media.app")

# A tiny 1x1 black JPEG returned by GET /snapshot when no live frame is
# available (mock mode, a not-yet-live stream, or an ffmpeg failure/timeout).
# Keeping the endpoint a valid image on failure means door-visiond's face
# pipeline degrades to "no faces" instead of erroring — the door path is
# never blocked or 500'd on a missing frame.
_PLACEHOLDER_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x37\xff\xd9"  # noqa: E501

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

_RECORDING_TRIGGER_STATES: set[SessionState] = {
    SessionState.BUTTON_PRESSED,
    SessionState.VIDEO_MESSAGE_RECORDING,
}
_FINALIZE_STATES: set[SessionState] = {
    SessionState.SESSION_END,
    SessionState.VIDEO_MESSAGE_SAVED,
    SessionState.UNANSWERED_TIMEOUT,
    SessionState.ANSWERED,
}


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    cfg = get_settings()
    cfg.recordings_root.mkdir(parents=True, exist_ok=True)
    cfg.segments_root.mkdir(parents=True, exist_ok=True)
    cfg.thumbnails_root.mkdir(parents=True, exist_ok=True)

    # Build router
    if cfg.media_mode == "mock":
        router: MediaRouter = MockMediaRouter(cfg)
        logger.info("media_mode_mock")
    else:
        mtx = MediaMTXRouter(cfg)
        await mtx.start()
        router = mtx  # type: ignore[assignment]
        logger.info("media_mode_mediamtx")

    # Build DB
    db = RecordingDB(cfg.ssd_data_root / "door_media.db")

    # Build service
    svc = RecordingService(router=router, db=db, settings=cfg)
    await svc.start()

    app.state.cfg = cfg
    app.state.router = router
    app.state.db = db
    app.state.service = svc

    logger.info("door_media_ready", extra={"mode": cfg.media_mode})

    yield

    # Shutdown
    await svc.stop()
    if isinstance(router, MediaMTXRouter):
        await router.stop()
    db.close()
    logger.info("door_media_shutdown")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


app = FastAPI(
    title="door-media",
    version="0.0.0",
    lifespan=_lifespan,
    docs_url=None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    # Scoped to the admin-UI dev origins (mirrors door-api); a wildcard with
    # credentials is invalid per the CORS spec and wrong for a service handling
    # biometric/recording data. Production serves the UI same-origin via Caddy.
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def _svc(request: Request) -> RecordingService:
    return request.app.state.service


def _cfg(request: Request) -> Settings:
    return request.app.state.cfg


def _router_dep(request: Request) -> MediaRouter:
    return request.app.state.router


def _require_admin(request: Request) -> None:
    cfg: Settings = request.app.state.cfg
    if not cfg.admin_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin authentication is not configured",
        )
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    token = auth.removeprefix("Bearer ")
    if not secrets.compare_digest(token, cfg.admin_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin token",
        )


AdminAuth = Annotated[None, Depends(_require_admin)]


def _require_photobooth(request: Request) -> None:
    cfg: Settings = request.app.state.cfg
    if not cfg.feature_photobooth:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="photo booth disabled")


PhotoBoothEnabled = Annotated[None, Depends(_require_photobooth)]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health(
    request: Request,
) -> dict[str, Any]:
    router: MediaRouter = request.app.state.router
    cfg: Settings = request.app.state.cfg

    stream_up = await router.health_check()
    return {
        "service": "door-media",
        "status": "ok" if stream_up else "degraded",
        "detail": None if stream_up else "MediaMTX stream not live",
        "mode": cfg.media_mode,
        "stream_up": stream_up,
        "door_id": cfg.door_id,
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

_start_time = time.monotonic()


@app.get("/metrics")
async def metrics(request: Request) -> Response:
    cfg: Settings = request.app.state.cfg
    router: MediaRouter = request.app.state.router
    db: RecordingDB = request.app.state.db

    stream_up = await router.health_check()
    streams = router.stream_info()
    webrtc_clients = sum(s.webrtc_clients for s in streams)

    pending = db.list_finalized_pending_sync()
    oldest_age = db.oldest_unsynced_age_s()

    try:
        import shutil as _shutil

        du = _shutil.disk_usage(cfg.ssd_data_root)
        ssd_free = du.free
    except OSError:
        ssd_free = 0

    uptime_s = time.monotonic() - _start_time

    lines = [
        "# HELP door_media_stream_up MediaMTX stream liveness (1=up, 0=down)",
        "# TYPE door_media_stream_up gauge",
        f"door_media_stream_up {1 if stream_up else 0}",
        "",
        "# HELP door_media_webrtc_clients Active WebRTC consumer count",
        "# TYPE door_media_webrtc_clients gauge",
        f"door_media_webrtc_clients {webrtc_clients}",
        "",
        "# HELP door_media_ssd_free_bytes Free bytes on SSD mount",
        "# TYPE door_media_ssd_free_bytes gauge",
        f"door_media_ssd_free_bytes {ssd_free}",
        "",
        "# HELP door_media_sync_queue_depth Recordings awaiting sync upload",
        "# TYPE door_media_sync_queue_depth gauge",
        f"door_media_sync_queue_depth {len(pending)}",
        "",
        "# HELP door_media_oldest_unsynced_s Age (s) of the oldest unsynced recording",
        "# TYPE door_media_oldest_unsynced_s gauge",
        f"door_media_oldest_unsynced_s {oldest_age}",
        "",
        "# HELP door_media_uptime_s Service uptime in seconds",
        "# TYPE door_media_uptime_s counter",
        f"door_media_uptime_s {uptime_s:.1f}",
        "",
    ]
    return Response(
        content="\n".join(lines),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------


@app.get("/streams")
async def streams(request: Request) -> list[dict]:
    router: MediaRouter = request.app.state.router
    stream_info_async = getattr(router, "stream_info_async", None)
    if callable(stream_info_async):
        async_stream_info = cast(Callable[[], Awaitable[list[StreamInfo]]], stream_info_async)
        infos = await async_stream_info()
    else:
        infos = router.stream_info()
    return [
        {
            "name": s.name,
            "whep_url": s.whep_url,
            "stream_up": s.stream_up,
            "webrtc_clients": s.webrtc_clients,
        }
        for s in infos
    ]


@app.get("/snapshot")
async def snapshot(request: Request) -> Response:
    """Return a single current JPEG frame from the live visitor camera.

    Unauthenticated (matches prior behaviour): door-visiond's HardwareBackend
    polls this on an interval for face frames. In mediamtx mode the frame is
    grabbed read-only from the live RTSP stream via ffmpeg (it does not disturb
    the WHEP live stream or segment recording). Best-effort: in mock mode, when
    the stream is not yet live, or on any ffmpeg failure/timeout, a tiny
    placeholder JPEG is returned so the face/door path is never blocked or 500'd.

    The ``X-Snapshot-Source`` header is ``live`` for a real frame and
    ``placeholder`` for the fallback, so consumers/monitoring can tell them
    apart without inspecting pixels.
    """
    router: MediaRouter = request.app.state.router
    frame: bytes | None = None
    try:
        frame = await router.snapshot()
    except Exception:
        # A snapshot must never bring down the door path — degrade to placeholder.
        logger.warning("snapshot_failed", exc_info=True)
        frame = None

    if frame:
        return Response(
            content=frame,
            media_type="image/jpeg",
            headers={"X-Snapshot-Source": "live"},
        )
    return Response(
        content=_PLACEHOLDER_JPEG,
        media_type="image/jpeg",
        headers={"X-Snapshot-Source": "placeholder"},
    )


# ---------------------------------------------------------------------------
# Recordings (admin)
# ---------------------------------------------------------------------------


@app.get("/recordings")
async def list_recordings(
    _auth: AdminAuth,
    request: Request,
    kind: str | None = None,
    sync_status: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict:
    svc: RecordingService = request.app.state.service
    recordings, next_cursor = svc.list_recordings(
        kind=kind,
        sync_status=sync_status,
        limit=limit,
        cursor=cursor,
    )
    return {
        "recordings": recordings,
        "next_cursor": next_cursor,
    }


@app.delete("/recordings/{recording_id}")
async def delete_recording(
    recording_id: str,
    _auth: AdminAuth,
    request: Request,
) -> dict:
    svc: RecordingService = request.app.state.service
    try:
        rid = UUID(recording_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid recording_id UUID",
        ) from None
    trace_id = uuid7()
    ok = svc.delete_recording(rid, trace_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recording {recording_id} not found",
        )
    return {"deleted": recording_id}


@app.get("/recordings/{recording_id}/file")
async def recording_file(
    recording_id: str,
    session_id: str,
    request: Request,
) -> FileResponse:
    """Serve a just-recorded visitor message back to the local DoorPad review UI."""
    try:
        rid = UUID(recording_id)
        sid = UUID(session_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid recording_id or session_id UUID",
        ) from None

    db: RecordingDB = request.app.state.db
    cfg: Settings = request.app.state.cfg
    row = db.get(rid)
    if (
        row is None
        or row.session_id != str(sid)
        or row.kind != "video_message"
        or row.path is None
        or row.sync_status == "deleted"
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")

    path = cfg.ssd_data_root / row.path
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording file missing")
    return FileResponse(path, media_type="video/mp4")


# ---------------------------------------------------------------------------
# Internal session event endpoint
# ---------------------------------------------------------------------------


class _SessionEventBody(BaseModel):
    event: SessionStateChangedEvent


@app.post("/internal/session_event")
async def session_event(
    body: _SessionEventBody,
    _auth: AdminAuth,
    request: Request,
) -> dict:
    """Accept a session.state_changed payload to trigger recording windows.

    This endpoint is loopback-only — door-api posts here on transitions
    that require recording lifecycle changes.  It must never sit in the
    critical button path (door-api calls this asynchronously, fire-and-forget).
    """
    svc: RecordingService = request.app.state.service
    db: RecordingDB = request.app.state.db
    event = body.event
    if db.has_processed_session_event(event.event_id):
        return {"accepted": True, "duplicate": True}

    session_id = event.payload.session_id
    trace_id = event.trace_id
    to_state = event.payload.to_state
    from_state = event.payload.from_state
    trigger = event.payload.trigger

    logger.info(
        "session_event_received",
        extra={
            "session_id": str(session_id),
            "from_state": from_state,
            "to_state": to_state,
            "trigger": trigger,
            "trace_id": str(trace_id),
        },
    )

    if to_state == SessionState.BUTTON_PRESSED and trigger != "doorpad.video_message_offer":
        if not _active_recording_ids(svc, session_id=session_id, kind="bell_clip"):
            await svc.start_recording(
                session_id=session_id,
                kind="bell_clip",
                trace_id=trace_id,
            )

    elif to_state == SessionState.VIDEO_MESSAGE_RECORDING:
        if from_state == SessionState.VIDEO_MESSAGE_REVIEW:
            await svc.discard_recordings_for_session(
                session_id=session_id,
                kind="video_message",
                trace_id=trace_id,
            )
        if not _active_recording_ids(svc, session_id=session_id, kind="video_message"):
            await svc.start_recording(
                session_id=session_id,
                kind="video_message",
                trace_id=trace_id,
            )

    elif to_state == SessionState.VIDEO_MESSAGE_REVIEW:
        for rid in _active_recording_ids(svc, session_id=session_id, kind="video_message"):
            finalized = await svc.finalize_recording(
                rid,
                consent_context="visitor_initiated",
                trace_id=trace_id,
            )
            if not finalized:
                raise HTTPException(status_code=503, detail="video finalization failed")

    elif to_state == SessionState.SESSION_END and trigger in (
        "visitor:discard",
        "timeout:review",
        "timeout:inactivity",
    ):
        await svc.discard_recordings_for_session(
            session_id=session_id,
            kind="video_message",
            trace_id=trace_id,
        )

    elif to_state in (
        SessionState.SESSION_END,
        SessionState.UNANSWERED_TIMEOUT,
        SessionState.ANSWERED,
    ):
        for rid in _active_recording_ids(svc, session_id=session_id, kind="bell_clip"):
            finalized = await svc.finalize_recording(
                rid,
                consent_context="bell_event",
                trace_id=trace_id,
            )
            if not finalized:
                raise HTTPException(status_code=503, detail="bell clip finalization failed")

    db.mark_session_event_processed(event.event_id)
    return {"accepted": True, "duplicate": False}


def _active_recording_ids(
    svc: RecordingService,
    *,
    session_id: UUID,
    kind: str | None,
) -> list[UUID]:
    return [
        rid
        for rid, handle in svc._active_handles.items()  # type: ignore[attr-defined]
        if getattr(handle, "session_id", None) == session_id
        and (kind is None or getattr(handle, "kind", None) == kind)
    ]


# ---------------------------------------------------------------------------
# Sync upload completed (from door-sync)
# ---------------------------------------------------------------------------


class _SyncCompletedBody(BaseModel):
    recording_id: str
    verified_sha256: str
    item_id: str
    attempts: int


@app.post("/internal/sync_completed")
async def sync_completed(
    body: _SyncCompletedBody,
    _auth: AdminAuth,
    request: Request,
) -> dict:
    """Called by door-sync when a checksum-verified upload succeeds."""
    svc: RecordingService = request.app.state.service
    try:
        recording_id = UUID(body.recording_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid recording_id UUID",
        ) from None
    svc.on_sync_upload_completed(
        recording_id=recording_id,
        verified_sha256=body.verified_sha256,
    )
    return {"acknowledged": True}


# ---------------------------------------------------------------------------
# Explicit photo-booth still capture
# ---------------------------------------------------------------------------


class _PhotoCaptureBody(BaseModel):
    session_id: str
    trace_id: str


@app.post("/photos/capture")
async def capture_photo(
    body: _PhotoCaptureBody,
    _enabled: PhotoBoothEnabled,
    request: Request,
) -> dict:
    svc: RecordingService = request.app.state.service
    try:
        session_id = UUID(body.session_id)
        trace_id = UUID(body.trace_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid session_id or trace_id UUID",
        ) from None
    photo = await svc.capture_photo_for_review(session_id=session_id, trace_id=trace_id)
    if photo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Photo capture unavailable",
        )
    return {
        "photo": {
            "recording_id": str(photo.recording_id),
            "session_id": str(photo.session_id),
            "review_path": photo.review_path,
            "review_url": photo.review_url_path,
            "size_bytes": photo.size_bytes,
            "sha256": photo.sha256,
        }
    }


@app.get("/photos/{recording_id}/review")
async def photo_review_file(
    recording_id: str,
    session_id: str,
    _enabled: PhotoBoothEnabled,
    request: Request,
) -> FileResponse:
    svc: RecordingService = request.app.state.service
    cfg: Settings = request.app.state.cfg
    try:
        rid = UUID(recording_id)
        sid = UUID(session_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid recording_id or session_id UUID",
        ) from None
    captured = svc.review_photo(rid, session_id=sid)
    if captured is None or captured.session_id != sid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    path = cfg.ssd_data_root / captured.path
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo file missing")
    return FileResponse(path, media_type="image/jpeg")


@app.post("/photos/{recording_id}/save")
async def save_photo(
    recording_id: str,
    body: _PhotoCaptureBody,
    _enabled: PhotoBoothEnabled,
    request: Request,
) -> dict:
    svc: RecordingService = request.app.state.service
    try:
        rid = UUID(recording_id)
        session_id = UUID(body.session_id)
        trace_id = UUID(body.trace_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid recording_id, session_id, or trace_id UUID",
        ) from None
    recording = await svc.save_photo(rid, session_id=session_id, trace_id=trace_id)
    if recording is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    recording["playback_url"] = f"/photos/{recording_id}/file?session_id={session_id}"
    return {"recording": recording}


@app.post("/photos/{recording_id}/discard")
async def discard_photo(
    recording_id: str,
    body: _PhotoCaptureBody,
    _enabled: PhotoBoothEnabled,
    request: Request,
) -> dict:
    svc: RecordingService = request.app.state.service
    try:
        rid = UUID(recording_id)
        session_id = UUID(body.session_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid recording_id or session_id UUID",
        ) from None
    discarded = await svc.discard_photo(rid, session_id=session_id)
    if not discarded:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    return {"discarded": recording_id}


@app.get("/photos/{recording_id}/file")
async def saved_photo_file(
    recording_id: str,
    session_id: str,
    _enabled: PhotoBoothEnabled,
    request: Request,
) -> FileResponse:
    try:
        rid = UUID(recording_id)
        sid = UUID(session_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid recording_id or session_id UUID",
        ) from None

    db: RecordingDB = request.app.state.db
    cfg: Settings = request.app.state.cfg
    row = db.get(rid)
    if (
        row is None
        or row.session_id != str(sid)
        or row.kind != "photo_booth"
        or row.path is None
        or row.sync_status == "deleted"
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")

    path = cfg.ssd_data_root / row.path
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo file missing")
    return FileResponse(path, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# Server-Sent Events broadcast
# ---------------------------------------------------------------------------


@app.get("/events")
async def events_sse(request: Request, _auth: AdminAuth) -> StreamingResponse:
    """Server-Sent Events stream of all media.* and system.* events."""
    q = subscribe_broadcast_queue()

    async def _generate() -> AsyncGenerator[str, None]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    data = event.model_dump_json()
                    yield f"data: {data}\n\n"
                except TimeoutError:
                    # Keepalive comment
                    yield ": keepalive\n\n"
        finally:
            unsubscribe_broadcast_queue(q)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
