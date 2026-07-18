"""FastAPI application for door-sync.

Routes:
  GET  /health                    — service health
  GET  /metrics                   — Prometheus-style queue metrics
  GET  /queue                     — queue snapshot for the T-203 admin gauges (admin)
  POST /internal/enqueue          — mirror a contract event to the NUC (loopback)
  POST /internal/purge/{person_id}— durably forward an ADR-0009 person-purge (loopback)

door-sync sits entirely off the door critical path. ``/internal/*`` is
loopback-only (door-api / door-visiond call it fire-and-forget); it must never
be reachable such that a caller blocks a visitor interaction waiting on it.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any

from doorboard_contracts.events import SocialDeletionRequestedEvent, parse_event
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, ValidationError

from door_sync._uuid7 import uuid7
from door_sync.engine import SyncEngine
from door_sync.gallery import (
    FilesystemGalleryStore,
    GalleryPhotoInput,
    GalleryStore,
    MockGalleryStore,
)
from door_sync.media_client import HttpMediaClient
from door_sync.queue import UploadQueue
from door_sync.settings import Settings
from door_sync.settings import settings as get_settings
from door_sync.sources import MediaEventSource
from door_sync.targets import FilesystemNasTarget, HttpNucTarget, MockMediaTarget

logger = logging.getLogger("door_sync.app")


def build_engine(cfg: Settings, queue: UploadQueue) -> SyncEngine:
    if cfg.media_target == "mock":
        media_target: Any = MockMediaTarget()
    else:
        media_target = FilesystemNasTarget(Path(cfg.nas_sync_target))
    nuc_target = HttpNucTarget(cfg.control_plane_url, ingest_token=cfg.ingest_token)
    media_client = HttpMediaClient(
        cfg.door_media_url,
        admin_token=cfg.door_media_admin_token,
    )
    return SyncEngine(
        queue=queue,
        settings=cfg,
        media_target=media_target,
        nuc_target=nuc_target,
        media_client=media_client,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    cfg = get_settings()
    queue = UploadQueue(cfg.queue_db_path)
    engine = build_engine(cfg, queue)
    if cfg.media_target == "mock":
        gallery: GalleryStore = MockGalleryStore()
    else:
        gallery = FilesystemGalleryStore(
            nas_root=Path(cfg.nas_sync_target),
            ssd_data_root=cfg.ssd_data_root,
            syncable_roots=cfg.syncable_roots,
        )
    source = MediaEventSource(
        engine,
        base_url=cfg.door_media_url,
        admin_token=cfg.door_media_admin_token,
    )

    app.state.cfg = cfg
    app.state.queue = queue
    app.state.engine = engine
    app.state.gallery = gallery

    # Startup crash-recovery + safety-net reconciliation, then background drain.
    await engine.finalize_licenses()
    await engine.reconcile_from_media()
    tasks = [
        asyncio.create_task(engine.run(), name="sync-drain"),
        asyncio.create_task(source.run(), name="media-sse"),
    ]
    logger.info("door_sync_ready", extra={"door_id": cfg.door_id})

    yield

    engine.stop()
    source.stop()
    for t in tasks:
        t.cancel()
        with suppress(asyncio.CancelledError):
            await t
    queue.close()
    logger.info("door_sync_shutdown")


app = FastAPI(
    title="door-sync",
    version="0.0.0",
    lifespan=_lifespan,
    docs_url=None,
    redoc_url=None,
)


def _cfg(request: Request) -> Settings:
    return request.app.state.cfg


def _queue(request: Request) -> UploadQueue:
    return request.app.state.queue


def _engine(request: Request) -> SyncEngine:
    return request.app.state.engine


def _gallery(request: Request) -> GalleryStore:
    return request.app.state.gallery


def _require_admin(request: Request) -> None:
    cfg: Settings = request.app.state.cfg
    if not cfg.admin_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin authentication is not configured",
        )
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else ""
    if not token or not secrets.compare_digest(token, cfg.admin_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")


AdminAuth = Annotated[None, Depends(_require_admin)]


# ---------------------------------------------------------------------------
# Health / metrics
# ---------------------------------------------------------------------------

_start_time = time.monotonic()


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    cfg = _cfg(request)
    stats = _queue(request).stats(now_epoch=time.time())
    degraded = stats.dead_letter > 0
    return {
        "service": "door-sync",
        "status": "degraded" if degraded else "ok",
        "detail": f"{stats.dead_letter} dead-lettered" if degraded else None,
        "door_id": cfg.door_id,
    }


@app.get("/metrics")
async def metrics(request: Request) -> Response:
    stats = _queue(request).stats(now_epoch=time.time())
    uptime_s = time.monotonic() - _start_time
    lines = [
        "# HELP door_sync_queue_depth Items pending upload",
        "# TYPE door_sync_queue_depth gauge",
        f"door_sync_queue_depth {stats.pending}",
        "",
        "# HELP door_sync_oldest_pending_s Age (s) of the oldest pending item",
        "# TYPE door_sync_oldest_pending_s gauge",
        f"door_sync_oldest_pending_s {stats.oldest_pending_age_s}",
        "",
        "# HELP door_sync_dead_letter_total Items in dead-letter (need attention)",
        "# TYPE door_sync_dead_letter_total gauge",
        f"door_sync_dead_letter_total {stats.dead_letter}",
        "",
        "# HELP door_sync_completed_total Items completed and awaiting prune",
        "# TYPE door_sync_completed_total gauge",
        f"door_sync_completed_total {stats.completed}",
        "",
    ]
    for target, by_status in sorted(stats.per_target.items()):
        for st, n in sorted(by_status.items()):
            lines.append(f'door_sync_queue_items{{target="{target}",status="{st}"}} {n}')
    lines += [
        "",
        "# HELP door_sync_uptime_s Service uptime in seconds",
        "# TYPE door_sync_uptime_s counter",
        f"door_sync_uptime_s {uptime_s:.1f}",
        "",
    ]
    return Response(content="\n".join(lines), media_type="text/plain; version=0.0.4; charset=utf-8")


# ---------------------------------------------------------------------------
# Queue visibility (admin) — feeds the T-203 gauges
# ---------------------------------------------------------------------------


@app.get("/queue")
async def get_queue(request: Request, _auth: AdminAuth, limit: int = 200) -> dict:
    q = _queue(request)
    stats = q.stats(now_epoch=time.time())
    items = q.list_items(limit=limit)
    return {
        "summary": {
            "pending": stats.pending,
            "completed": stats.completed,
            "dead_letter": stats.dead_letter,
            "oldest_pending_age_s": stats.oldest_pending_age_s,
            "per_target": stats.per_target,
        },
        "items": [
            {
                "item_id": it.item_id,
                "kind": it.kind,
                "target": it.target,
                "status": it.status,
                "recording_id": it.recording_id,
                "attempts": it.attempts,
                "permanent_failures": it.permanent_failures,
                "next_attempt_at": it.next_attempt_at,
                "error_class": it.error_class,
                "last_error": it.last_error,
                "created_at": it.created_at,
            }
            for it in items
        ],
    }


# ---------------------------------------------------------------------------
# Internal enqueue endpoints (loopback)
# ---------------------------------------------------------------------------


class _EnqueueEventBody(BaseModel):
    event: dict


@app.post("/internal/enqueue")
async def internal_enqueue(body: _EnqueueEventBody, request: Request, _auth: AdminAuth) -> dict:
    """Mirror one contract event to the NUC. door-api posts session/social events
    here fire-and-forget; a bad event is rejected now, not dead-lettered later."""
    engine = _engine(request)
    try:
        newly = engine.enqueue_event(body.event)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"enqueued": newly}


@app.post("/internal/purge/{person_id}")
async def internal_purge(person_id: str, request: Request, _auth: AdminAuth) -> dict:
    """Durably forward an ADR-0009 person-purge to the NUC. Never blocks unenroll:
    local deletion already happened in door-visiond; this is eventually-consistent."""
    engine = _engine(request)
    newly = engine.enqueue_purge(person_id=person_id, trace_id=str(uuid7()))
    return {"enqueued": newly, "person_id": person_id}


# ---------------------------------------------------------------------------
# Private gallery projection (admin/internal)
# ---------------------------------------------------------------------------


class _GalleryApproveBody(BaseModel):
    local_path: str
    thumbnail_path: str | None = None
    consent_metadata_path: str | None = None
    sha256: str
    tags: list[str] = []
    approved_by: str = "owner"
    wallboard_moment: bool = False


class _GalleryTagsBody(BaseModel):
    tags: list[str] = []
    wallboard_moment: bool | None = None


class _DeletionEventBody(BaseModel):
    event: dict


@app.get("/internal/gallery/photos")
async def internal_gallery_photos(request: Request, _auth: AdminAuth) -> dict:
    return {"photos": [p.to_dict() for p in _gallery(request).list_photos()]}


@app.get("/internal/gallery/moments")
async def internal_gallery_moments(request: Request, _auth: AdminAuth) -> dict:
    return {"photos": [p.to_dict() for p in _gallery(request).list_wallboard_moments()]}


@app.post("/internal/gallery/photos/{recording_id}/approve")
async def internal_gallery_approve(
    recording_id: str,
    body: _GalleryApproveBody,
    request: Request,
    _auth: AdminAuth,
) -> dict:
    row = _gallery(request).ingest_approved_photo(
        GalleryPhotoInput(
            recording_id=recording_id,
            local_path=body.local_path,
            thumbnail_path=body.thumbnail_path,
            consent_metadata_path=body.consent_metadata_path,
            sha256=body.sha256,
            tags=tuple(_clean_tags(body.tags)),
            approved_by=body.approved_by,
            wallboard_moment=body.wallboard_moment,
        )
    )
    return {"photo": row.to_dict()}


@app.patch("/internal/gallery/photos/{recording_id}/tags")
async def internal_gallery_tags(
    recording_id: str,
    body: _GalleryTagsBody,
    request: Request,
    _auth: AdminAuth,
) -> dict:
    row = _gallery(request).update_tags(
        recording_id,
        tags=tuple(_clean_tags(body.tags)),
        wallboard_moment=body.wallboard_moment,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="photo not found")
    return {"photo": row.to_dict()}


@app.delete("/internal/gallery/photos/{recording_id}")
async def internal_gallery_delete(
    recording_id: str,
    request: Request,
    _auth: AdminAuth,
) -> dict:
    deleted = _gallery(request).delete_photo(recording_id)
    return {"deleted": deleted, "recording_id": recording_id}


@app.post("/internal/social-deletion")
async def internal_social_deletion(
    body: _DeletionEventBody,
    request: Request,
    _auth: AdminAuth,
) -> dict:
    try:
        event = parse_event(body.event)
        if not isinstance(event, SocialDeletionRequestedEvent):
            raise ValueError("expected social.deletion_requested")
        if event.payload.target_kind != "photo":
            return {"handled": False}
        deleted = _gallery(request).delete_photo(event.payload.target_id)
    except (TypeError, ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"handled": True, "deleted": deleted}


def _clean_tags(tags: list[str]) -> list[str]:
    clean: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = raw.strip().lower()
        if not tag or len(tag) > 40 or tag in seen:
            continue
        clean.append(tag)
        seen.add(tag)
        if len(clean) >= 12:
            break
    return clean
