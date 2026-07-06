"""FastAPI application for door-media.

Routes:
  GET  /health          — service + stream health
  GET  /metrics         — Prometheus-style text metrics
  GET  /streams         — stream metadata for kiosk UIs (unauthenticated)
  GET  /recordings      — list of recordings (admin-auth)
  DELETE /recordings/{id} — delete a recording (admin-auth)

Auth: ``DOOR_MEDIA_ADMIN_TOKEN`` env var.  Empty = auth disabled (dev/CI).

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
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any, cast
from uuid import UUID

from doorboard_contracts.events import (
    SessionState,
)
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from door_media._uuid7 import uuid7
from door_media.adapters import MediaRouter, StreamInfo
from door_media.db import RecordingDB
from door_media.emitter import get_broadcast_queue
from door_media.mediamtx_router import MediaMTXRouter
from door_media.mock_router import MockMediaRouter
from door_media.service import RecordingService
from door_media.settings import Settings
from door_media.settings import settings as get_settings

logger = logging.getLogger("door_media.app")

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
        return  # auth disabled in dev/CI
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    token = auth.removeprefix("Bearer ")
    if token != cfg.admin_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin token",
        )


AdminAuth = Annotated[None, Depends(_require_admin)]


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


# ---------------------------------------------------------------------------
# Recordings (admin)
# ---------------------------------------------------------------------------


@app.get("/recordings")
async def list_recordings(
    _auth: AdminAuth,
    request: Request,
) -> list[dict]:
    svc: RecordingService = request.app.state.service
    return svc.list_recordings()


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
    session_id: str
    from_state: str
    to_state: str
    trigger: str
    trace_id: str


@app.post("/internal/session_event")
async def session_event(
    body: _SessionEventBody,
    request: Request,
) -> dict:
    """Accept a session.state_changed payload to trigger recording windows.

    This endpoint is loopback-only — door-api posts here on transitions
    that require recording lifecycle changes.  It must never sit in the
    critical button path (door-api calls this asynchronously, fire-and-forget).
    """
    svc: RecordingService = request.app.state.service

    try:
        session_id = UUID(body.session_id)
        trace_id = UUID(body.trace_id)
        to_state = SessionState(body.to_state)
        from_state = SessionState(body.from_state)
    except (ValueError, KeyError) as exc:
        logger.warning(
            "session_event_invalid",
            extra={"error": str(exc), "body": body.model_dump()},
        )
        return {"accepted": False, "reason": "invalid_state"}

    logger.info(
        "session_event_received",
        extra={
            "session_id": str(session_id),
            "from_state": from_state,
            "to_state": to_state,
            "trigger": body.trigger,
            "trace_id": str(trace_id),
        },
    )

    if to_state == SessionState.BUTTON_PRESSED and body.trigger != "doorpad.video_message_offer":
        asyncio.create_task(
            svc.start_recording(
                session_id=session_id,
                kind="bell_clip",
                trace_id=trace_id,
            ),
            name=f"start_bell_clip_{session_id}",
        )

    elif to_state == SessionState.VIDEO_MESSAGE_RECORDING:

        async def _start_video_message() -> None:
            if from_state == SessionState.VIDEO_MESSAGE_REVIEW:
                await svc.discard_recordings_for_session(
                    session_id=session_id,
                    kind="video_message",
                    trace_id=trace_id,
                )
            await svc.start_recording(
                session_id=session_id,
                kind="video_message",
                trace_id=trace_id,
            )

        asyncio.create_task(_start_video_message(), name=f"start_video_msg_{session_id}")

    elif to_state == SessionState.VIDEO_MESSAGE_REVIEW:
        for rid in _active_recording_ids(svc, session_id=session_id, kind="video_message"):
            asyncio.create_task(
                svc.finalize_recording(
                    rid,
                    consent_context="visitor_initiated",
                    trace_id=trace_id,
                ),
                name=f"finalize_video_msg_{rid}",
            )

    elif to_state == SessionState.SESSION_END and body.trigger in (
        "visitor:discard",
        "timeout:review",
        "timeout:inactivity",
    ):
        asyncio.create_task(
            svc.discard_recordings_for_session(
                session_id=session_id,
                kind="video_message",
                trace_id=trace_id,
            ),
            name=f"discard_video_msg_{session_id}",
        )

    elif to_state in (
        SessionState.SESSION_END,
        SessionState.UNANSWERED_TIMEOUT,
        SessionState.ANSWERED,
    ):
        for rid in _active_recording_ids(svc, session_id=session_id, kind="bell_clip"):
            asyncio.create_task(
                svc.finalize_recording(
                    rid,
                    consent_context="bell_event",
                    trace_id=trace_id,
                ),
                name=f"finalize_bell_clip_{rid}",
            )

    return {"accepted": True}


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
# Server-Sent Events broadcast
# ---------------------------------------------------------------------------


@app.get("/events")
async def events_sse(request: Request) -> StreamingResponse:
    """Server-Sent Events stream of all media.* and system.* events."""
    q: asyncio.Queue = get_broadcast_queue()

    async def _generate() -> AsyncGenerator[str, None]:
        # Create a per-consumer sub-queue to avoid head-of-line blocking
        local_q: asyncio.Queue = asyncio.Queue(maxsize=64)

        async def _forwarder() -> None:
            while True:
                event = await q.get()
                if local_q.full():
                    with suppress(asyncio.QueueEmpty):
                        local_q.get_nowait()
                await local_q.put(event)

        fwd = asyncio.create_task(_forwarder(), name="sse-forwarder")
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(local_q.get(), timeout=30.0)
                    data = event.model_dump_json()
                    yield f"data: {data}\n\n"
                except TimeoutError:
                    # Keepalive comment
                    yield ": keepalive\n\n"
        finally:
            fwd.cancel()

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
