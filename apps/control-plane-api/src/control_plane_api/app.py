"""FastAPI application for control-plane-api (the NUC control plane, T-501).

Routes:
  GET    /health                      — service health
  GET    /metrics                     — Prometheus-style text metrics
  POST   /ingest                      — batch event ingest (ingest-scoped token)
  GET    /config/door/{door_id}       — versioned config bundle (config-scoped token)
  PUT    /config/door/{door_id}       — update a door's config bundle (admin)
  GET    /status/presence                          — list current presence, all subjects (admin)
  GET    /status/presence/bundle                    — Pi-cacheable presence bundle (config-scoped)
  GET    /status/presence/{subject_id}              — current presence + per-source detail (admin)
  GET    /status/presence/{subject_id}/history       — label-change history (admin)
  PATCH  /status/presence/{subject_id}               — set tracking_enabled (admin)
  POST   /status/presence/{subject_id}/override      — set manual override (admin UI + one-tap)
  DELETE /status/presence/{subject_id}/override      — clear manual override (admin)
  PATCH  /status/presence/{subject_id}/sources/{src} — per-source enable/disable (admin)
  POST   /status/presence/webhook/focus-shortcut     — HA Focus-shortcut ingestion (admin token)
  POST   /status/presence/webhook/geofence-label      — HA geofence-label ingestion (admin token)
  DELETE /people/{person_id}/events   — person-data purge (ingest-scoped token; ADR-0009 §3.4)
  GET    /admin/social/guestbook      — moderation list (admin)
  DELETE /admin/social/guestbook/{id} — moderation delete (admin)
  GET    /admin/social/checkins       — moderation list (admin)
  DELETE /admin/social/checkins/{id}  — moderation delete (admin)
  POST   /admin/tokens                — issue a Pi-scoped service token (admin)
  DELETE /admin/tokens/{token_id}     — revoke a service token (admin)
  GET    /admin/tokens                — list active service tokens (admin)

Admin auth note: see packages/auth README — session-based admin auth doesn't
exist yet, so `/admin/*` is gated by a shared bearer secret
(`CONTROL_PLANE_ADMIN_TOKEN`), same stopgap door-api's social panel uses.
`/status/presence/*` (except the Pi-facing bundle) reuses the same admin
token rather than adding a new `ServiceTokenScope` — HA and any phone
shortcut hitting the webhook/override routes both run/originate on the
trusted NUC side of ADR-0002's trust boundary, so this is the same stopgap,
not a new one. Replace when real admin auth lands (see T-501 README note).
"""

from __future__ import annotations

import logging
import logging.config
import secrets
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from doorboard_auth import ServiceTokenRecord, ServiceTokenScope
from doorboard_config import DoorConfigSettings
from doorboard_contracts import PresenceLabel
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select

from control_plane_api import presence_engine
from control_plane_api import tokens as token_store
from control_plane_api.bundles import get_or_create_bundle, update_bundle
from control_plane_api.db import session_scope
from control_plane_api.http_limits import BodySizeLimitMiddleware
from control_plane_api.models import EventRow, SocialItemRow
from control_plane_api.presence import CoordinatePayloadError, reject_coordinate_payload
from control_plane_api.presence_engine import InvalidSourceError
from control_plane_api.purge import purge_person
from control_plane_api.service import ingest_batch
from control_plane_api.settings import Settings
from control_plane_api.settings import settings as get_settings
from control_plane_api.state import AppState

logger = logging.getLogger("control_plane_api.app")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    cfg = get_settings()
    app.state.app_state = AppState(cfg)
    logger.info("control_plane_api_ready", extra={"door_id": cfg.door_id})
    yield
    app.state.app_state.dispose()
    logger.info("control_plane_api_shutdown")


app = FastAPI(
    title="control-plane-api",
    version="0.0.0",
    lifespan=_lifespan,
    docs_url=None,
    redoc_url=None,
)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=1024 * 1024)


def _state(request: Request) -> AppState:
    return request.app.state.app_state


def _require_admin(request: Request, authorization: str | None = Header(default=None)) -> None:
    cfg: Settings = _state(request).settings
    if not cfg.admin_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin interface is not configured on this device",
        )
    prefix = "Bearer "
    presented = ""
    if authorization is not None and authorization.startswith(prefix):
        presented = authorization[len(prefix) :]
    if not presented or not secrets.compare_digest(presented, cfg.admin_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")


AdminAuth = Annotated[None, Depends(_require_admin)]


def _scoped_token_auth(scope: ServiceTokenScope):
    def dependency(
        request: Request, authorization: str | None = Header(default=None)
    ) -> ServiceTokenRecord:
        dep = token_store.require_scope(scope, session_factory=_state(request).session_factory)
        return dep(authorization)

    return dependency


_ingest_auth = _scoped_token_auth("ingest")
_config_auth = _scoped_token_auth("config")


# ---------------------------------------------------------------------------
# Health / metrics
# ---------------------------------------------------------------------------


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    cfg = _state(request).settings
    return {"service": "control-plane-api", "status": "ok", "detail": None, "door_id": cfg.door_id}


_start_time = time.monotonic()


@app.get("/metrics")
async def metrics(request: Request) -> Response:
    with session_scope(_state(request).session_factory) as session:
        event_count = session.execute(select(func.count()).select_from(EventRow)).scalar_one()

    uptime_s = time.monotonic() - _start_time
    lines = [
        "# HELP control_plane_api_events_total Total events durably stored",
        "# TYPE control_plane_api_events_total counter",
        f"control_plane_api_events_total {event_count}",
        "",
        "# HELP control_plane_api_uptime_s Service uptime in seconds",
        "# TYPE control_plane_api_uptime_s counter",
        f"control_plane_api_uptime_s {uptime_s:.1f}",
        "",
    ]
    return Response(content="\n".join(lines), media_type="text/plain; version=0.0.4; charset=utf-8")


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(min_length=1, max_length=128)
    events: list[dict] = Field(max_length=200)


@app.post("/ingest")
async def ingest(
    body: IngestRequest,
    request: Request,
    token: Annotated[ServiceTokenRecord, Depends(_ingest_auth)],
) -> dict:
    if any(event.get("door_id") not in (None, token.door_id) for event in body.events):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="token is not valid for an event from another door",
        )
    state = _state(request)
    now = datetime.now(UTC)
    results = ingest_batch(
        state.session_factory,
        body.events,
        batch_id=body.batch_id,
        now=now,
        mqtt_publisher=state.mqtt_publisher,
        notify_engine=state.notify_engine,
    )
    return {"batch_id": body.batch_id, "results": results}


# ---------------------------------------------------------------------------
# Config distribution
# ---------------------------------------------------------------------------


@app.get("/config/door/{door_id}")
async def get_config(
    door_id: str,
    request: Request,
    token: Annotated[ServiceTokenRecord, Depends(_config_auth)],
) -> dict:
    if token.door_id != door_id:
        raise HTTPException(status_code=403, detail="token is not valid for this door")
    state = _state(request)
    with session_scope(state.session_factory) as session:
        bundle = get_or_create_bundle(session, door_id=door_id, now=datetime.now(UTC))
    return bundle.model_dump(mode="json")


@app.put("/config/door/{door_id}")
async def put_config(door_id: str, body: dict, request: Request, _auth: AdminAuth) -> dict:
    try:
        new_settings = DoorConfigSettings.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    state = _state(request)
    with session_scope(state.session_factory) as session:
        bundle = update_bundle(
            session, door_id=door_id, settings=new_settings, now=datetime.now(UTC)
        )
    return bundle.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Presence / Weasley clock (T-504)
#
# `/status/presence/bundle` is Pi-facing (config-scoped token, same trust
# tier as `/config/door/{door_id}`) — it's the "data bundle" the wallboard
# caches so its presence tile keeps showing a last-known label with a
# staleness hint through a NUC outage. Everything else here is admin-only
# (see the module docstring's admin-auth note).
#
# Route ordering note: `/status/presence/bundle` must be registered before
# `/status/presence/{subject_id}` — both are a 3-segment GET, and Starlette
# matches path templates in registration order, so the dynamic route would
# otherwise swallow "bundle" as a subject_id.
# ---------------------------------------------------------------------------


def _source_status_to_dict(item: presence_engine.SourceStatus) -> dict:
    return {
        "source": item.source,
        "label": item.label.value if item.label is not None else None,
        "until": item.until.isoformat() if item.until is not None else None,
        "enabled": item.enabled,
        "stored": item.stored,
    }


def _subject_status_to_dict(item: presence_engine.SubjectStatus) -> dict:
    return {
        "subject_id": item.subject_id,
        "tracking_enabled": item.tracking_enabled,
        "label": item.label.value,
        "source": item.source,
        "until": item.until.isoformat() if item.until is not None else None,
        "sources": [_source_status_to_dict(s) for s in item.sources],
    }


class PresenceOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: PresenceLabel
    until: AwareDatetime | None = None

    @field_validator("until")
    @classmethod
    def _until_to_utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None


class PresenceSourceToggleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class PresenceSubjectConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tracking_enabled: bool


class PresenceWebhookRequest(BaseModel):
    """HA Focus-shortcut / voluntary-geofence-label webhook body. Label strings only."""

    model_config = ConfigDict(extra="forbid")

    subject_id: str
    label: PresenceLabel
    until: AwareDatetime | None = None

    @field_validator("until")
    @classmethod
    def _until_to_utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None


@app.get("/status/presence")
async def list_presence(request: Request, _auth: AdminAuth) -> dict:
    state = _state(request)
    now = datetime.now(UTC)
    with session_scope(state.session_factory) as session:
        statuses = presence_engine.list_subject_statuses(
            session,
            now=now,
            door_id=state.settings.door_id,
            calendar_provider=state.calendar_provider,
            mqtt_publisher=state.mqtt_publisher,
            history_max_rows=state.settings.presence_history_max_rows,
        )
    return {"subjects": [_subject_status_to_dict(s) for s in statuses]}


@app.get("/status/presence/bundle")
async def get_presence_bundle(
    request: Request,
    token: Annotated[ServiceTokenRecord, Depends(_config_auth)],
) -> dict:
    state = _state(request)
    now = datetime.now(UTC)
    with session_scope(state.session_factory) as session:
        statuses = presence_engine.list_subject_statuses(
            session,
            now=now,
            door_id=token.door_id,
            calendar_provider=state.calendar_provider,
            mqtt_publisher=state.mqtt_publisher,
            history_max_rows=state.settings.presence_history_max_rows,
        )
    return {
        "door_id": token.door_id,
        "generated_at": now.isoformat(),
        "stale_after_s": state.settings.presence_stale_after_s,
        "subjects": {
            s.subject_id: {
                "label": s.label.value,
                "source": s.source,
                "until": s.until.isoformat() if s.until is not None else None,
            }
            for s in statuses
        },
    }


@app.get("/status/presence/{subject_id}")
async def get_presence(subject_id: str, request: Request, _auth: AdminAuth) -> dict:
    state = _state(request)
    with session_scope(state.session_factory) as session:
        item = presence_engine.get_subject_status(
            session,
            subject_id=subject_id,
            now=datetime.now(UTC),
            door_id=state.settings.door_id,
            calendar_provider=state.calendar_provider,
            mqtt_publisher=state.mqtt_publisher,
            history_max_rows=state.settings.presence_history_max_rows,
        )
    return _subject_status_to_dict(item)


@app.get("/status/presence/{subject_id}/history")
async def get_presence_history(
    subject_id: str, request: Request, _auth: AdminAuth, limit: int = 100
) -> dict:
    state = _state(request)
    with session_scope(state.session_factory) as session:
        rows = presence_engine.list_history(session, subject_id=subject_id, limit=limit)
    return {
        "subject_id": subject_id,
        "history": [
            {
                "label": row.label,
                "source": row.source,
                "until": row.until.isoformat() if row.until is not None else None,
                "occurred_at": row.occurred_at.isoformat(),
            }
            for row in rows
        ],
    }


@app.patch("/status/presence/{subject_id}")
async def patch_presence_subject(
    subject_id: str, body: PresenceSubjectConfigRequest, request: Request, _auth: AdminAuth
) -> dict:
    state = _state(request)
    now = datetime.now(UTC)
    with session_scope(state.session_factory) as session:
        presence_engine.set_subject_tracking_enabled(
            session, subject_id=subject_id, enabled=body.tracking_enabled, now=now
        )
        item = presence_engine.get_subject_status(
            session,
            subject_id=subject_id,
            now=now,
            door_id=state.settings.door_id,
            calendar_provider=state.calendar_provider,
            mqtt_publisher=state.mqtt_publisher,
            history_max_rows=state.settings.presence_history_max_rows,
        )
    return _subject_status_to_dict(item)


@app.post("/status/presence/{subject_id}/override")
async def set_presence_override(
    subject_id: str, body: PresenceOverrideRequest, request: Request, _auth: AdminAuth
) -> dict:
    """Manual override endpoint — admin UI and one-tap phone shortcut both hit this."""
    state = _state(request)
    now = datetime.now(UTC)
    with session_scope(state.session_factory) as session:
        presence_engine.set_manual_override(
            session, subject_id=subject_id, label=body.label, until=body.until, now=now
        )
        item = presence_engine.get_subject_status(
            session,
            subject_id=subject_id,
            now=now,
            door_id=state.settings.door_id,
            calendar_provider=state.calendar_provider,
            mqtt_publisher=state.mqtt_publisher,
            history_max_rows=state.settings.presence_history_max_rows,
        )
    return _subject_status_to_dict(item)


@app.delete("/status/presence/{subject_id}/override")
async def clear_presence_override(subject_id: str, request: Request, _auth: AdminAuth) -> dict:
    state = _state(request)
    now = datetime.now(UTC)
    with session_scope(state.session_factory) as session:
        presence_engine.clear_manual_override(session, subject_id=subject_id, now=now)
        item = presence_engine.get_subject_status(
            session,
            subject_id=subject_id,
            now=now,
            door_id=state.settings.door_id,
            calendar_provider=state.calendar_provider,
            mqtt_publisher=state.mqtt_publisher,
            history_max_rows=state.settings.presence_history_max_rows,
        )
    return _subject_status_to_dict(item)


@app.patch("/status/presence/{subject_id}/sources/{source}")
async def patch_presence_source(
    subject_id: str,
    source: str,
    body: PresenceSourceToggleRequest,
    request: Request,
    _auth: AdminAuth,
) -> dict:
    state = _state(request)
    now = datetime.now(UTC)
    try:
        with session_scope(state.session_factory) as session:
            presence_engine.set_source_enabled(
                session, subject_id=subject_id, source=source, enabled=body.enabled, now=now
            )
            item = presence_engine.get_subject_status(
                session,
                subject_id=subject_id,
                now=now,
                door_id=state.settings.door_id,
                calendar_provider=state.calendar_provider,
                mqtt_publisher=state.mqtt_publisher,
                history_max_rows=state.settings.presence_history_max_rows,
            )
    except InvalidSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _subject_status_to_dict(item)


def _handle_presence_webhook(request: Request, raw: dict, *, source: str) -> dict:
    try:
        reject_coordinate_payload(raw, context=f"webhook:{source}")
    except CoordinatePayloadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        payload = PresenceWebhookRequest.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    state = _state(request)
    now = datetime.now(UTC)
    with session_scope(state.session_factory) as session:
        presence_engine.set_source_value(
            session,
            subject_id=payload.subject_id,
            source=source,
            label=payload.label,
            until=payload.until,
            now=now,
        )
        item = presence_engine.get_subject_status(
            session,
            subject_id=payload.subject_id,
            now=now,
            door_id=state.settings.door_id,
            calendar_provider=state.calendar_provider,
            mqtt_publisher=state.mqtt_publisher,
            history_max_rows=state.settings.presence_history_max_rows,
        )
    return _subject_status_to_dict(item)


@app.post("/status/presence/webhook/focus-shortcut")
async def presence_webhook_focus_shortcut(body: dict, request: Request, _auth: AdminAuth) -> dict:
    return _handle_presence_webhook(request, body, source="focus_shortcut")


@app.post("/status/presence/webhook/geofence-label")
async def presence_webhook_geofence_label(body: dict, request: Request, _auth: AdminAuth) -> dict:
    return _handle_presence_webhook(request, body, source="geofence_label")


# ---------------------------------------------------------------------------
# Person-data purge (ADR-0009 §3.4)
# ---------------------------------------------------------------------------


@app.delete("/people/{person_id}/events")
async def delete_person_events(
    person_id: str,
    request: Request,
    _token: Annotated[ServiceTokenRecord, Depends(_ingest_auth)],
) -> dict:
    state = _state(request)
    with session_scope(state.session_factory) as session:
        result = purge_person(session, person_id=person_id, now=datetime.now(UTC))
    return {
        "person_id": result.person_id,
        "events_deleted": result.events_deleted,
        "checkins_deleted": result.checkins_deleted,
        "status": "purged",
    }


# ---------------------------------------------------------------------------
# Social moderation (admin)
# ---------------------------------------------------------------------------


def _social_item_to_dict(row: SocialItemRow) -> dict:
    return {
        "kind": row.kind,
        "item_id": row.item_id,
        "door_id": row.door_id,
        "text": row.text,
        "author_label": row.author_label,
        "person_id": row.person_id,
        "label": row.label,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None,
        "deleted_reason": row.deleted_reason,
    }


def _list_social_items(request: Request, kind: str) -> dict:
    state = _state(request)
    with session_scope(state.session_factory) as session:
        stmt = (
            select(SocialItemRow)
            .where(SocialItemRow.kind == kind)
            .order_by(SocialItemRow.created_at)
        )
        rows = session.execute(stmt).scalars().all()
        return {"items": [_social_item_to_dict(r) for r in rows]}


def _delete_social_item(request: Request, kind: str, item_id: str) -> dict:
    state = _state(request)
    with session_scope(state.session_factory) as session:
        row = session.get(SocialItemRow, (kind, item_id))
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        now = datetime.now(UTC)
        row.status = "deleted"
        row.deleted_at = now
        row.deleted_reason = "moderation"
        row.updated_at = now
    return {"kind": kind, "item_id": item_id, "status": "deleted"}


@app.get("/admin/social/guestbook")
async def admin_list_guestbook(request: Request, _auth: AdminAuth) -> dict:
    return _list_social_items(request, "guestbook")


@app.delete("/admin/social/guestbook/{item_id}")
async def admin_delete_guestbook(item_id: str, request: Request, _auth: AdminAuth) -> dict:
    return _delete_social_item(request, "guestbook", item_id)


@app.get("/admin/social/checkins")
async def admin_list_checkins(request: Request, _auth: AdminAuth) -> dict:
    return _list_social_items(request, "checkin")


@app.delete("/admin/social/checkins/{item_id}")
async def admin_delete_checkin(item_id: str, request: Request, _auth: AdminAuth) -> dict:
    return _delete_social_item(request, "checkin", item_id)


# ---------------------------------------------------------------------------
# Token management (admin)
# ---------------------------------------------------------------------------


class IssueTokenRequest(BaseModel):
    scope: ServiceTokenScope
    door_id: str
    label: str | None = None


@app.post("/admin/tokens", status_code=201)
async def admin_issue_token(body: IssueTokenRequest, request: Request, _auth: AdminAuth) -> dict:
    state = _state(request)
    with session_scope(state.session_factory) as session:
        issued = token_store.issue_and_store(
            session, scope=body.scope, door_id=body.door_id, label=body.label
        )
    return {"token_id": issued.token_id, "token": issued.raw}


@app.delete("/admin/tokens/{token_id}")
async def admin_revoke_token(token_id: str, request: Request, _auth: AdminAuth) -> dict:
    state = _state(request)
    with session_scope(state.session_factory) as session:
        revoked = token_store.revoke(session, token_id=token_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="token not found or already revoked")
    return {"token_id": token_id, "status": "revoked"}


@app.get("/admin/tokens")
async def admin_list_tokens(request: Request, _auth: AdminAuth) -> dict:
    state = _state(request)
    with session_scope(state.session_factory) as session:
        records = token_store.list_active(session)
    return {
        "tokens": [
            {
                "token_id": r.token_id,
                "scope": r.scope,
                "door_id": r.door_id,
                "label": r.label,
                "created_at": r.created_at.isoformat(),
            }
            for r in records
        ]
    }
