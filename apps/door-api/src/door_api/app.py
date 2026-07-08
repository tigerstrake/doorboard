"""ASGI application for door-api.

Exposes the WebSocket broadcast, health/metrics endpoints, and the DoorPad
visitor-flow HTTP surface used by the local kiosk UI.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
from doorboard_contracts.events import (
    DoorboardEvent,
    SocialDeletionRequestedEvent,
    SocialDeletionRequestedPayload,
)
from doorboard_esp32_link import Esp32Transport, WireMessage
from doorboard_esp32_link.esp32 import uuid7_now
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from door_api.broadcast import DisplayBroadcast
from door_api.config import SessionConfig
from door_api.persistence import SessionStore
from door_api.session import SessionMachine
from door_api.social.config import SocialConfig
from door_api.social.routes import build_social_router
from door_api.social.service import SocialService
from door_api.social.store import SocialStore


class DoorApiState:
    """State container for the FastAPI app."""

    def __init__(self) -> None:
        self.broadcast = DisplayBroadcast()
        self.config = SessionConfig.from_env()
        self.store = SessionStore(self.config.db_path)
        self.esp32_transport: Esp32Transport | None = None
        self.effect_requests = 0
        self.effect_unavailable = 0
        self.media_forward_errors = 0
        self._esp32_event_task: asyncio.Task[None] | None = None

        def on_event(event: dict[str, Any]) -> None:
            self.broadcast.send_delta(event)
            if event["type"] in ("session.state_changed", "session.started", "session.ended"):
                self.broadcast.update_snapshot(self.machine.snapshot().to_dict())
            if event["type"] == "session.state_changed":
                self.forward_session_event_to_media(event)

        self.machine = SessionMachine(config=self.config, store=self.store, on_event=on_event)

        self.social_config = SocialConfig.from_env()
        self.social_store = SocialStore(self.social_config.db_path)
        self.social_service = SocialService(
            config=self.social_config,
            store=self.social_store,
            on_event=self.broadcast.send_delta,
        )

    def startup(self) -> None:
        """Start the machine and populate the initial snapshot."""
        self.machine.restore_from_persistence()
        self.broadcast.update_snapshot(self.machine.snapshot().to_dict())
        self.start_esp32_event_consumer()

    def shutdown(self) -> None:
        """Close resources."""
        if self._esp32_event_task is not None:
            self._esp32_event_task.cancel()
        self.machine.close()
        self.social_store.close()

    def start_esp32_event_consumer(self) -> None:
        if self.esp32_transport is None or self._esp32_event_task is not None:
            return
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            self._esp32_event_task = loop.create_task(
                self._consume_esp32_events(),
                name="door-api-esp32-events",
            )

    async def _consume_esp32_events(self) -> None:
        assert self.esp32_transport is not None
        async for event in self.esp32_transport.events():
            self.handle_contract_event(event)

    def handle_contract_event(self, event: DoorboardEvent) -> bool:
        # `event` is a discriminated union keyed on `type`; read `event.payload`
        # inside each branch so it narrows to the concrete payload type.
        changed = False
        if event.type == "door.button_pressed":
            payload = event.payload
            changed = self.machine.handle_button_pressed(
                trace_id=event.trace_id,
                had_cached_profile=payload.had_cached_profile,
                profile_id=payload.profile_id,
            )
        elif event.type == "vision.identity_stable":
            payload = event.payload
            changed = self.machine.handle_identity_stable(
                person_id=payload.person_id,
                display_name=payload.display_name,
                profile_id=payload.profile_id,
                trace_id=event.trace_id,
            )
            self.broadcast.send_delta(event.model_dump(mode="json"))
        elif event.type == "vision.identity_expired":
            payload = event.payload
            changed = self.machine.handle_identity_expired(person_id=payload.person_id)
            self.broadcast.send_delta(event.model_dump(mode="json"))
        elif event.type == "door.contact_changed":
            payload = event.payload
            changed = self.machine.handle_contact_changed(state=payload.state)
        if changed or event.type.startswith("vision."):
            self.broadcast.update_snapshot(self.machine.snapshot().to_dict())
        return changed

    def snapshot_response(self) -> dict[str, Any]:
        return {
            "session": self.machine.snapshot().to_dict(),
            "config": {
                "max_recording_s": self.config.max_recording_s,
                "review_timeout_s": self.config.review_timeout_s,
                "inactivity_timeout_s": self.config.inactivity_timeout_s,
                "visitor_token_ttl_s": self.config.visitor_token_ttl_s,
                "feature_photobooth": self.config.feature_photobooth,
            },
        }

    def forward_session_event_to_media(self, event: dict[str, Any]) -> None:
        """Forward session deltas to door-media without blocking transitions."""
        payload = event.get("payload") or {}
        body = {
            "session_id": payload.get("session_id"),
            "from_state": payload.get("from_state"),
            "to_state": payload.get("to_state"),
            "trigger": payload.get("trigger"),
            "trace_id": event.get("trace_id"),
        }
        if not all(body.values()):
            return

        async def _post() -> None:
            try:
                async with httpx.AsyncClient(timeout=self.config.media_timeout_s) as client:
                    await client.post(
                        f"{self.config.media_base_url.rstrip('/')}/internal/session_event",
                        json=body,
                    )
            except Exception:
                self.media_forward_errors += 1

        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(_post(), name="door-api-media-forward")

    async def play_doorpad_effect(self, trace_id: UUID | None = None) -> dict[str, str]:
        """Emit and, when configured, send DoorPad feedback to the ESP32."""
        self.effect_requests += 1
        event_trace = trace_id or uuid4()
        event = {
            "event_id": str(uuid7_now()),
            "type": "door.effect_play",
            "source": "door-api",
            "occurred_at": datetime.now(UTC).isoformat(),
            "monotonic_ms": int(time.monotonic() * 1000),
            "door_id": self.config.door_id,
            "trace_id": str(event_trace),
            "payload": {
                "effect_id": self.config.doorpad_effect_id,
                "duration_ms": self.config.doorpad_effect_duration_ms,
            },
        }
        self.broadcast.send_delta(event)

        if self.esp32_transport is None:
            self.effect_unavailable += 1
            return {"status": "unavailable"}

        try:
            await self.esp32_transport.send(
                WireMessage(
                    v=1,
                    seq=0,
                    message_type="effect_play",
                    ack=None,
                    payload=event["payload"],
                )
            )
        except Exception:
            self.effect_unavailable += 1
            return {"status": "failed"}
        return {"status": "sent"}

    def visitor_token(self) -> dict[str, str | int]:
        snapshot = self.machine.snapshot()
        if snapshot.session_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No active visitor session",
            )
        expires_at = int(time.time() + self.config.visitor_token_ttl_s)
        payload = {
            "v": 1,
            "session_id": str(snapshot.session_id),
            "exp": expires_at,
        }
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        signature = hmac.new(
            self.config.visitor_token_secret.encode(),
            payload_json,
            hashlib.sha256,
        ).digest()
        token = f"{_b64(payload_json)}.{_b64(signature)}"
        url = f"{self.config.visitor_public_base_url.rstrip('/')}/visitor?token={token}"
        return {"token": token, "url": url, "expires_at": expires_at}

    def photo_session_id(self) -> UUID:
        snapshot = self.machine.snapshot()
        if snapshot.session_id is not None:
            return snapshot.session_id
        return uuid4()


state = DoorApiState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.startup()
    yield
    state.shutdown()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(
    build_social_router(
        lambda: state.social_service,
        lambda: state.machine.snapshot().person_id,
    )
)


class PhotoBoothSessionBody(BaseModel):
    session_id: str


class GalleryApproveBody(BaseModel):
    tags: list[str] = []
    wallboard_moment: bool = False


class GalleryTagsBody(BaseModel):
    tags: list[str] = []
    wallboard_moment: bool | None = None


def _require_photobooth() -> None:
    if not state.config.feature_photobooth:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="photo booth disabled")


def _rows_from_recordings_response(data: Any) -> list[dict[str, Any]]:
    rows = data.get("recordings", []) if isinstance(data, dict) else data
    return [dict(row) for row in rows if isinstance(row, dict)]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> JSONResponse:
    data = {**state.machine.metrics.to_dict(), **state.social_service.metrics.to_dict()}
    data.update(
        {
            "door_api_doorpad_effect_requests_total": state.effect_requests,
            "door_api_doorpad_effect_unavailable_total": state.effect_unavailable,
            "door_api_media_forward_errors_total": state.media_forward_errors,
        }
    )
    return JSONResponse(content=data)


@app.get("/session")
async def get_session() -> dict[str, Any]:
    return state.snapshot_response()


@app.post("/doorpad/ring")
async def doorpad_ring() -> dict[str, Any]:
    trace_id = uuid4()
    accepted = state.machine.handle_button_pressed(
        trace_id=trace_id,
        trigger="doorpad.touch_ring",
        entry="touch",
    )
    effect = await state.play_doorpad_effect(trace_id)
    return {"accepted": accepted, "effect": effect, **state.snapshot_response()}


@app.post("/doorpad/video-message/offer")
async def video_message_offer() -> dict[str, Any]:
    trace_id = uuid4()
    accepted = state.machine.handle_video_message_offer(trace_id=trace_id)
    effect = await state.play_doorpad_effect(trace_id)
    return {"accepted": accepted, "effect": effect, **state.snapshot_response()}


@app.post("/doorpad/video-message/start")
async def video_message_start() -> dict[str, Any]:
    if state.machine.state.name not in {
        "VIDEO_MESSAGE_OFFERED",
        "VIDEO_MESSAGE_REVIEW",
    }:
        state.machine.handle_video_message_offer(trace_id=uuid4())
    accepted = state.machine.handle_video_message_start()
    return {"accepted": accepted, **state.snapshot_response()}


@app.post("/doorpad/video-message/stop")
async def video_message_stop() -> dict[str, Any]:
    accepted = state.machine.handle_video_message_stop()
    return {"accepted": accepted, **state.snapshot_response()}


@app.post("/doorpad/video-message/save")
async def video_message_save() -> dict[str, Any]:
    accepted = state.machine.handle_video_message_save()
    return {"accepted": accepted, **state.snapshot_response()}


@app.post("/doorpad/video-message/discard")
async def video_message_discard() -> dict[str, Any]:
    accepted = state.machine.handle_video_message_discard()
    if not accepted and state.machine.state.name != "IDLE":
        accepted = state.machine.handle_admin_reset()
    return {"accepted": accepted, **state.snapshot_response()}


@app.get("/doorpad/video-message/latest")
async def latest_video_message() -> dict[str, Any]:
    snapshot = state.machine.snapshot()
    if snapshot.session_id is None:
        return {"recording": None}
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.get(f"{state.config.media_base_url.rstrip('/')}/recordings")
            resp.raise_for_status()
            rows = _rows_from_recordings_response(resp.json())
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="door-media unavailable",
        ) from exc

    recording = _latest_video_message_recording(rows, snapshot.session_id)
    if recording is None:
        return {"recording": None}
    recording["playback_url"] = (
        f"{state.config.media_public_base_url.rstrip('/')}/recordings/"
        f"{recording['recording_id']}/file?session_id={snapshot.session_id}"
    )
    return {"recording": recording}


@app.post("/doorpad/photo-booth/capture")
async def photo_booth_capture() -> dict[str, Any]:
    _require_photobooth()
    session_id = state.photo_session_id()
    trace_id = uuid4()
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.post(
                f"{state.config.media_base_url.rstrip('/')}/photos/capture",
                json={"session_id": str(session_id), "trace_id": str(trace_id)},
            )
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="door-media unavailable",
        ) from exc
    photo = body["photo"]
    photo["review_url"] = (
        f"{state.config.media_public_base_url.rstrip('/')}{photo['review_url']}"
        if photo.get("review_url", "").startswith("/")
        else photo.get("review_url")
    )
    return {"photo": photo, "session_id": str(session_id)}


@app.post("/doorpad/photo-booth/{recording_id}/save")
async def photo_booth_save(recording_id: str, body: PhotoBoothSessionBody) -> dict[str, Any]:
    _require_photobooth()
    trace_id = uuid4()
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.post(
                f"{state.config.media_base_url.rstrip('/')}/photos/{recording_id}/save",
                json={"session_id": body.session_id, "trace_id": str(trace_id)},
            )
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="photo not found") from exc
        raise HTTPException(status_code=503, detail="door-media unavailable") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="door-media unavailable") from exc
    recording = payload["recording"]
    if recording.get("playback_url", "").startswith("/"):
        recording["playback_url"] = (
            f"{state.config.media_public_base_url.rstrip('/')}{recording['playback_url']}"
        )
    return {"recording": recording}


@app.post("/doorpad/photo-booth/{recording_id}/discard")
async def photo_booth_discard(recording_id: str, body: PhotoBoothSessionBody) -> dict[str, Any]:
    _require_photobooth()
    trace_id = uuid4()
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.post(
                f"{state.config.media_base_url.rstrip('/')}/photos/{recording_id}/discard",
                json={"session_id": body.session_id, "trace_id": str(trace_id)},
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="photo not found") from exc
        raise HTTPException(status_code=503, detail="door-media unavailable") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="door-media unavailable") from exc
    return {"discarded": recording_id}


@app.get("/visitor-token")
async def visitor_token() -> dict[str, str | int]:
    return state.visitor_token()


@app.get("/admin/media-inbox")
async def admin_media_inbox() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.get(f"{state.config.media_base_url.rstrip('/')}/recordings")
            resp.raise_for_status()
            rows = _rows_from_recordings_response(resp.json())
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="door-media unavailable",
        ) from exc
    return {
        "recordings": [row for row in rows if row.get("kind") == "video_message"],
    }


@app.get("/admin/gallery/photos")
async def admin_gallery_photos() -> dict[str, Any]:
    _require_photobooth()
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            media_resp = await client.get(
                f"{state.config.media_base_url.rstrip('/')}/recordings",
                params={"kind": "photo_booth"},
            )
            media_resp.raise_for_status()
            sync_resp = await client.get(
                f"{state.config.sync_base_url.rstrip('/')}/internal/gallery/photos"
            )
            sync_resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="gallery unavailable") from exc
    recordings = _rows_from_recordings_response(media_resp.json())
    approved = {
        photo["recording_id"]: photo
        for photo in sync_resp.json().get("photos", [])
        if isinstance(photo, dict)
    }
    photos = []
    for row in recordings:
        if row.get("sync_status") == "deleted":
            continue
        gallery = approved.get(row.get("recording_id"))
        photos.append(
            {
                **row,
                "gallery": gallery,
                "gallery_status": gallery.get("status") if gallery else "pending",
                "tags": gallery.get("tags", []) if gallery else [],
                "wallboard_moment": bool(gallery.get("wallboard_moment")) if gallery else False,
            }
        )
    return {"photos": photos}


@app.post("/admin/gallery/photos/{recording_id}/approve")
async def admin_gallery_approve(recording_id: str, body: GalleryApproveBody) -> dict[str, Any]:
    _require_photobooth()
    row = await _media_recording(recording_id)
    if (
        row is None
        or row.get("kind") != "photo_booth"
        or not row.get("path")
        or not row.get("sha256")
    ):
        raise HTTPException(status_code=404, detail="photo not found")
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.post(
                f"{state.config.sync_base_url.rstrip('/')}/internal/gallery/photos/"
                f"{recording_id}/approve",
                json={
                    "local_path": row["path"],
                    "thumbnail_path": row.get("thumbnail_path"),
                    "consent_metadata_path": row.get("consent_metadata_path"),
                    "sha256": row["sha256"],
                    "tags": body.tags,
                    "approved_by": "owner",
                    "wallboard_moment": body.wallboard_moment,
                },
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="gallery unavailable") from exc


@app.patch("/admin/gallery/photos/{recording_id}/tags")
async def admin_gallery_tags(recording_id: str, body: GalleryTagsBody) -> dict[str, Any]:
    _require_photobooth()
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.patch(
                f"{state.config.sync_base_url.rstrip('/')}/internal/gallery/photos/"
                f"{recording_id}/tags",
                json={"tags": body.tags, "wallboard_moment": body.wallboard_moment},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="photo not found") from exc
        raise HTTPException(status_code=503, detail="gallery unavailable") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="gallery unavailable") from exc


@app.delete("/admin/gallery/photos/{recording_id}")
async def admin_gallery_delete(recording_id: str) -> dict[str, Any]:
    _require_photobooth()
    trace_id = uuid4()
    deletion_event = SocialDeletionRequestedEvent(
        event_id=uuid7_now(),
        type="social.deletion_requested",
        source="door-api",
        occurred_at=datetime.now(UTC),
        monotonic_ms=int(time.monotonic() * 1000),
        door_id=state.config.door_id,
        trace_id=trace_id,
        payload=SocialDeletionRequestedPayload(target_kind="photo", target_id=recording_id),
    )
    media_deleted = False
    gallery_deleted = False
    async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
        media_resp = await client.delete(
            f"{state.config.media_base_url.rstrip('/')}/recordings/{recording_id}"
        )
        if media_resp.status_code not in (200, 404):
            raise HTTPException(status_code=503, detail="door-media unavailable")
        media_deleted = media_resp.status_code == 200
        sync_resp = await client.post(
            f"{state.config.sync_base_url.rstrip('/')}/internal/social-deletion",
            json={"event": deletion_event.model_dump(mode="json")},
        )
        if sync_resp.status_code >= 500:
            raise HTTPException(status_code=503, detail="gallery unavailable")
        if sync_resp.status_code < 400:
            gallery_deleted = bool(sync_resp.json().get("deleted"))
    state.broadcast.send_delta(
        {
            "type": "social.deletion_requested",
            "payload": deletion_event.payload.model_dump(mode="json"),
            "trace_id": str(trace_id),
        }
    )
    return {
        "recording_id": recording_id,
        "media_deleted": media_deleted,
        "gallery_deleted": gallery_deleted,
    }


@app.get("/wallboard/moments")
async def wallboard_moments() -> dict[str, Any]:
    _require_photobooth()
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.get(
                f"{state.config.sync_base_url.rstrip('/')}/internal/gallery/moments"
            )
            resp.raise_for_status()
            photos = resp.json().get("photos", [])
    except Exception:
        photos = []
    return {
        "photos": [
            {
                "recording_id": p["recording_id"],
                "tags": p.get("tags", []),
                "approved_at": p.get("approved_at"),
                "thumbnail_path": p.get("gallery_thumbnail_path"),
            }
            for p in photos
            if isinstance(p, dict) and p.get("status") == "approved"
        ]
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = state.broadcast.make_client_queue()
    try:
        while True:
            msg = await queue.get()
            await websocket.send_text(msg)
            queue.task_done()
    except WebSocketDisconnect:
        pass
    finally:
        state.broadcast.remove_client(queue)


def _latest_video_message_recording(
    rows: list[dict[str, Any]],
    session_id: UUID,
) -> dict[str, Any] | None:
    matches = [
        dict(row)
        for row in rows
        if row.get("session_id") == str(session_id)
        and row.get("kind") == "video_message"
        and row.get("path")
        and row.get("sync_status") != "deleted"
    ]
    if not matches:
        return None
    matches.sort(key=lambda row: row.get("finalized_at_utc") or row.get("started_at_utc") or "")
    return matches[-1]


async def _media_recording(recording_id: str) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.get(f"{state.config.media_base_url.rstrip('/')}/recordings")
            resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="door-media unavailable") from exc
    for row in _rows_from_recordings_response(resp.json()):
        if row.get("recording_id") == recording_id:
            return row
    return None


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("door_api.app:app", host="0.0.0.0", port=8000, reload=True)
