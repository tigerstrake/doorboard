"""FastAPI application for control-plane-api (the NUC control plane, T-501).

Routes:
  GET    /health                      — service health
  GET    /metrics                     — Prometheus-style text metrics
  POST   /ingest                      — batch event ingest (ingest-scoped token)
  GET    /config/door/{door_id}       — versioned config bundle (config-scoped token)
  PUT    /config/door/{door_id}       — update a door's config bundle (admin)
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
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select

from control_plane_api import tokens as token_store
from control_plane_api.bundles import get_or_create_bundle, update_bundle
from control_plane_api.db import session_scope
from control_plane_api.models import EventRow, SocialItemRow
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
    batch_id: str
    events: list[dict]


@app.post("/ingest")
async def ingest(
    body: IngestRequest,
    request: Request,
    _token: Annotated[ServiceTokenRecord, Depends(_ingest_auth)],
) -> dict:
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
    _token: Annotated[ServiceTokenRecord, Depends(_config_auth)],
) -> dict:
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
