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
  PATCH  /status/presence/{subject_id}               — set tracking_enabled; may create (admin)
  DELETE /status/presence/{subject_id}               — remove subject, rows, retained topic (admin)
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
  POST   /admin/alerts                — Alertmanager webhook -> owner notification channels (admin)

Every route is a plain `def`: Starlette runs those on its threadpool, so the
synchronous SQLAlchemy/httpx work inside them cannot stall the event loop the
way it did when they were `async def` (one Telegram video upload froze
`/health` and door-sync's next batch for up to a minute). Sessions are
per-request (`session_scope`), the engine pool and paho's `publish` are
thread-safe, and anything slower than a DB round-trip is handed to
`AppState.fanout`.

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

import functools
import logging
import logging.config
import secrets
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from doorboard_auth import ServiceTokenRecord, ServiceTokenScope
from doorboard_config import DoorConfigSettings
from doorboard_contracts import PresenceLabel
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request, status
from fastapi.responses import Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Engine, select, text

from control_plane_api import presence_engine
from control_plane_api import tokens as token_store
from control_plane_api.bundles import get_or_create_bundle, update_bundle
from control_plane_api.db import session_scope
from control_plane_api.deletion import scrub_social_item
from control_plane_api.http_limits import BodySizeLimitMiddleware
from control_plane_api.metrics import (
    ALERTS_RECEIVED,
    AUTH_FAILURES,
    MQTT_CONNECTED,
    RATE_LIMITED,
    metrics,
)
from control_plane_api.models import SocialItemRow
from control_plane_api.notify import notification_for_alert
from control_plane_api.presence import CoordinatePayloadError, reject_coordinate_payload
from control_plane_api.presence_engine import (
    DEFAULT_SUBJECT_IDS,
    SUBJECT_ID_PATTERN,
    InvalidSourceError,
    UnknownSubjectError,
)
from control_plane_api.purge import purge_person
from control_plane_api.service import ingest_batch
from control_plane_api.settings import Settings
from control_plane_api.settings import settings as get_settings
from control_plane_api.state import AppState

logger = logging.getLogger("control_plane_api.app")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    cfg = get_settings()
    # The retained-presence republish is NOT done here: it runs from the MQTT
    # publisher's on_connected hook (`AppState.republish_presence`), because in
    # this lifespan turn the socket does not exist yet and paho drops the
    # publish — and the hook also covers every broker reconnect.
    app.state.app_state = AppState(cfg)
    # Presence subjects are an allow-list; the defaults must exist before the first
    # webhook. Best-effort: a database that is down at boot shows up on /health.
    try:
        with session_scope(app.state.app_state.session_factory) as session:
            presence_engine.seed_default_subjects(session, now=datetime.now(UTC))
    except Exception:
        logger.warning("presence_subject_seed_failed", exc_info=True)
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


def _peer_ip(request: Request) -> str:
    # The socket peer, never a forwarded header: the LAN bind is the trust
    # boundary here and anyone on it could forge X-Forwarded-For.
    return request.client.host if request.client is not None else "unknown"


auth_logger = logging.getLogger("control_plane_api.auth")


def _refuse_if_rate_limited(request: Request, *, scheme: str) -> None:
    state = _state(request)
    peer_ip = _peer_ip(request)
    if state.auth_limiter.is_blocked(peer_ip):
        metrics.inc(RATE_LIMITED, route=request.url.path)
        auth_logger.warning(
            "auth_rate_limited",
            extra={"route": request.url.path, "peer_ip": peer_ip, "scheme": scheme},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed authentication attempts from this address",
            headers={"Retry-After": "60"},
        )


def _auth_failed(request: Request, *, scheme: str, presented: bool) -> None:
    """Log a failed bearer check (never the secret) and count it toward the limit.

    Only a presented-but-wrong token counts as a guess; a request with no
    Authorization header at all is logged but not counted, so an unauthenticated
    probe cannot lock the owner's own address out.
    """
    peer_ip = _peer_ip(request)
    metrics.inc(AUTH_FAILURES, route=request.url.path)
    failures = _state(request).auth_limiter.record_failure(peer_ip) if presented else 0
    auth_logger.warning(
        "auth_failed",
        extra={
            "route": request.url.path,
            "peer_ip": peer_ip,
            "scheme": scheme,
            "reason": "invalid_token" if presented else "missing_token",
            "failures_in_window": failures,
        },
    )


def _bearer(authorization: str | None) -> str:
    prefix = "Bearer "
    if authorization is not None and authorization.startswith(prefix):
        return authorization[len(prefix) :]
    return ""


def _require_admin(request: Request, authorization: str | None = Header(default=None)) -> None:
    cfg: Settings = _state(request).settings
    if not cfg.admin_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin interface is not configured on this device",
        )
    _refuse_if_rate_limited(request, scheme="admin")
    presented = _bearer(authorization)
    if not presented or not secrets.compare_digest(presented, cfg.admin_token):
        _auth_failed(request, scheme="admin", presented=bool(presented))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")


AdminAuth = Annotated[None, Depends(_require_admin)]


def _require_enrollment_key_token(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    """Gate the enrollment key-release endpoint with its own token (ADR-0009 §6).

    Deliberately NOT the admin token: the door Pi holds this narrow token only,
    so a stolen (powered, on-network) Pi can fetch the LUKS key but nothing else.
    Both the key and its token must be configured, else the feature is absent (404).
    """
    cfg: Settings = _state(request).settings
    if not cfg.enrollment_key or not cfg.enrollment_key_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="enrollment key release is not configured",
        )
    _refuse_if_rate_limited(request, scheme="enrollment_key")
    presented = _bearer(authorization)
    if not presented or not secrets.compare_digest(presented, cfg.enrollment_key_token):
        _auth_failed(request, scheme="enrollment_key", presented=bool(presented))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid enrollment key token"
        )


EnrollmentKeyAuth = Annotated[None, Depends(_require_enrollment_key_token)]


def _scoped_token_auth(scope: ServiceTokenScope):
    def dependency(
        request: Request, authorization: str | None = Header(default=None)
    ) -> ServiceTokenRecord:
        dep = token_store.require_scope(scope, session_factory=_state(request).session_factory)
        return dep(request, authorization)

    return dependency


_ingest_auth = _scoped_token_auth("ingest")
_config_auth = _scoped_token_auth("config")


# ---------------------------------------------------------------------------
# Health / metrics
# ---------------------------------------------------------------------------


def _database_ok(engine: Engine) -> bool:
    """`SELECT 1` under a short statement timeout; False on any failure."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SET LOCAL statement_timeout = 2000"))
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("health_database_check_failed", extra={"error_class": type(exc).__name__})
        return False


@app.get("/health")
def health(request: Request) -> dict[str, Any]:
    """Honest health: probes what the service depends on instead of returning a constant.

    `status` is "ok" or "degraded" and `checks` says which dependency is down. The
    HTTP status stays 200 either way: the compose healthcheck only looks at the
    status code, and restart-looping the control plane because the broker or a
    calendar feed is unreachable would not fix either — it would just take
    `/ingest` and the admin panel down with them.
    """
    state = _state(request)
    checks: dict[str, bool | None] = {
        "database": _database_ok(state.engine),
        # None = not configured (dev/CI without MQTT), which is not "down".
        "mqtt": state.mqtt_publisher.connected,
        "calendar": getattr(state.calendar_provider, "healthy", None),
    }
    failing = [name for name, ok in checks.items() if ok is False]
    return {
        "service": "control-plane-api",
        "status": "degraded" if failing else "ok",
        "detail": ", ".join(f"{name} unavailable" for name in failing) or None,
        "door_id": state.settings.door_id,
        "checks": checks,
    }


@app.get("/status/keys/enrollment")
def enrollment_key(request: Request, _auth: EnrollmentKeyAuth) -> dict[str, str]:
    """Release the door Pi's LUKS enrollment passphrase (ADR-0009 §6, Option C).

    The Pi calls this once at boot to unlock its encrypted visiond/ volume and
    caches the key only in RAM. Log the release (never the key itself).
    """
    cfg = _state(request).settings
    logging.getLogger("control_plane_api.keys").info(
        "enrollment_key_released", extra={"door_id": cfg.door_id, "peer_ip": _peer_ip(request)}
    )
    return {"key": cfg.enrollment_key}


_start_time = time.monotonic()


def _events_estimate(engine: Engine) -> int | None:
    """Row estimate for `events` from the planner's statistics — O(1), never a scan.

    `count(*)` walked the whole unbounded table on every scrape. `reltuples` is
    refreshed by autovacuum/ANALYZE and is -1 before the first ANALYZE; a scrape
    must never fail because the database is momentarily unavailable.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SET LOCAL statement_timeout = 2000"))
            estimate = conn.execute(
                text("SELECT reltuples FROM pg_class WHERE oid = 'events'::regclass")
            ).scalar()
        return max(0, int(estimate or 0))
    except Exception as exc:
        logger.warning("metrics_database_unavailable", extra={"error_class": type(exc).__name__})
        return None


@app.get("/metrics")
def metrics_endpoint(request: Request) -> Response:
    state = _state(request)
    link = state.mqtt_publisher.connected
    if link is not None:
        metrics.set_gauge(MQTT_CONNECTED, 1 if link else 0)
    lines: list[str] = []
    estimate = _events_estimate(state.engine)
    if estimate is not None:
        lines += [
            "# HELP control_plane_api_events_total Events stored (estimate: pg_class.reltuples)",
            "# TYPE control_plane_api_events_total gauge",
            f"control_plane_api_events_total {estimate}",
            "",
        ]
    lines += metrics.render()
    uptime_s = time.monotonic() - _start_time
    lines += [
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
def ingest(
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
        fanout=state.fanout,
    )
    return {"batch_id": body.batch_id, "results": results}


# ---------------------------------------------------------------------------
# Config distribution
# ---------------------------------------------------------------------------


@app.get("/config/door/{door_id}")
def get_config(
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
def put_config(door_id: str, body: dict, request: Request, _auth: AdminAuth) -> dict:
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


# Path form of a subject id: validated before the handler runs, so a 65-char id is
# a 422 rather than a `DataError` 500 out of the database.
SubjectId = Annotated[str, Path(pattern=SUBJECT_ID_PATTERN)]


def _require_known_subject(session, subject_id: str) -> None:
    if not presence_engine.subject_exists(session, subject_id):
        raise HTTPException(status_code=404, detail=f"unknown presence subject {subject_id!r}")


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

    subject_id: str = Field(pattern=SUBJECT_ID_PATTERN)
    # Nullable, but REQUIRED — you have to say what you mean. `null` CLEARS this
    # source, which is what lets a lower-precedence source show through again
    # (ADR-0037): a Focus-off that wrote "available" would outrank the nightly
    # schedule and pin the door to Available all night. Making it optional
    # instead would mean a client that forgot the field silently wiped presence.
    label: PresenceLabel | None
    until: AwareDatetime | None = None

    @field_validator("until")
    @classmethod
    def _until_to_utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None


@app.get("/status/presence")
def list_presence(request: Request, _auth: AdminAuth) -> dict:
    state = _state(request)
    now = datetime.now(UTC)
    with session_scope(state.session_factory) as session:
        statuses = presence_engine.list_subject_statuses(
            session,
            now=now,
            door_id=state.settings.door_id,
            calendar_provider=state.calendar_provider,
            schedule_provider=state.schedule_provider,
            mqtt_publisher=state.mqtt_publisher,
            history_max_rows=state.settings.presence_history_max_rows,
        )
    return {"subjects": [_subject_status_to_dict(s) for s in statuses]}


@app.get("/status/presence/bundle")
def get_presence_bundle(
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
            schedule_provider=state.schedule_provider,
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
def get_presence(subject_id: SubjectId, request: Request, _auth: AdminAuth) -> dict:
    state = _state(request)
    with session_scope(state.session_factory) as session:
        _require_known_subject(session, subject_id)
        item = presence_engine.get_subject_status(
            session,
            subject_id=subject_id,
            now=datetime.now(UTC),
            door_id=state.settings.door_id,
            calendar_provider=state.calendar_provider,
            schedule_provider=state.schedule_provider,
            mqtt_publisher=state.mqtt_publisher,
            history_max_rows=state.settings.presence_history_max_rows,
        )
    return _subject_status_to_dict(item)


@app.get("/status/presence/{subject_id}/history")
def get_presence_history(
    subject_id: SubjectId, request: Request, _auth: AdminAuth, limit: int = 100
) -> dict:
    state = _state(request)
    with session_scope(state.session_factory) as session:
        _require_known_subject(session, subject_id)
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
def patch_presence_subject(
    subject_id: SubjectId, body: PresenceSubjectConfigRequest, request: Request, _auth: AdminAuth
) -> dict:
    """The one route that may CREATE a subject: an admin adding a third resident."""
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
            schedule_provider=state.schedule_provider,
            mqtt_publisher=state.mqtt_publisher,
            history_max_rows=state.settings.presence_history_max_rows,
        )
    return _subject_status_to_dict(item)


@app.post("/status/presence/{subject_id}/override")
def set_presence_override(
    subject_id: SubjectId, body: PresenceOverrideRequest, request: Request, _auth: AdminAuth
) -> dict:
    """Manual override endpoint — admin UI and one-tap phone shortcut both hit this."""
    state = _state(request)
    now = datetime.now(UTC)
    with session_scope(state.session_factory) as session:
        _require_known_subject(session, subject_id)
        presence_engine.set_manual_override(
            session, subject_id=subject_id, label=body.label, until=body.until, now=now
        )
        item = presence_engine.get_subject_status(
            session,
            subject_id=subject_id,
            now=now,
            door_id=state.settings.door_id,
            calendar_provider=state.calendar_provider,
            schedule_provider=state.schedule_provider,
            mqtt_publisher=state.mqtt_publisher,
            history_max_rows=state.settings.presence_history_max_rows,
        )
    return _subject_status_to_dict(item)


@app.delete("/status/presence/{subject_id}/override")
def clear_presence_override(subject_id: SubjectId, request: Request, _auth: AdminAuth) -> dict:
    state = _state(request)
    now = datetime.now(UTC)
    with session_scope(state.session_factory) as session:
        _require_known_subject(session, subject_id)
        presence_engine.clear_manual_override(session, subject_id=subject_id, now=now)
        item = presence_engine.get_subject_status(
            session,
            subject_id=subject_id,
            now=now,
            door_id=state.settings.door_id,
            calendar_provider=state.calendar_provider,
            schedule_provider=state.schedule_provider,
            mqtt_publisher=state.mqtt_publisher,
            history_max_rows=state.settings.presence_history_max_rows,
        )
    return _subject_status_to_dict(item)


@app.patch("/status/presence/{subject_id}/sources/{source}")
def patch_presence_source(
    subject_id: SubjectId,
    source: str,
    body: PresenceSourceToggleRequest,
    request: Request,
    _auth: AdminAuth,
) -> dict:
    state = _state(request)
    now = datetime.now(UTC)
    try:
        with session_scope(state.session_factory) as session:
            _require_known_subject(session, subject_id)
            presence_engine.set_source_enabled(
                session, subject_id=subject_id, source=source, enabled=body.enabled, now=now
            )
            item = presence_engine.get_subject_status(
                session,
                subject_id=subject_id,
                now=now,
                door_id=state.settings.door_id,
                calendar_provider=state.calendar_provider,
                schedule_provider=state.schedule_provider,
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
    # A label with no expiry never falls through on its own. Give webhook-set
    # labels a bounded life so a phone that dies mid-Focus decays to the next
    # source instead of pinning the door (see PRESENCE_WEBHOOK_MAX_AGE_S). Only
    # when setting: a clear (label=None) needs no expiry, and an explicit `until`
    # from the caller is honoured as-is.
    until = payload.until
    if until is None and payload.label is not None:
        until = now + timedelta(seconds=state.settings.presence_webhook_max_age_s)
    with session_scope(state.session_factory) as session:
        # Allow-list: a webhook may update a subject, never invent one. Anything a
        # LAN client can reach HA's inbound webhook with lands here.
        _require_known_subject(session, payload.subject_id)
        presence_engine.set_source_value(
            session,
            subject_id=payload.subject_id,
            source=source,
            label=payload.label,
            until=until,
            now=now,
        )
        item = presence_engine.get_subject_status(
            session,
            subject_id=payload.subject_id,
            now=now,
            door_id=state.settings.door_id,
            calendar_provider=state.calendar_provider,
            schedule_provider=state.schedule_provider,
            mqtt_publisher=state.mqtt_publisher,
            history_max_rows=state.settings.presence_history_max_rows,
        )
    return _subject_status_to_dict(item)


@app.delete("/status/presence/{subject_id}")
def delete_presence_subject(subject_id: SubjectId, request: Request, _auth: AdminAuth) -> dict:
    """Remove a subject: its config, sources and history, and the broker's retained label."""
    if subject_id in DEFAULT_SUBJECT_IDS:
        raise HTTPException(
            status_code=409, detail=f"{subject_id!r} is a configured default subject"
        )
    state = _state(request)
    try:
        with session_scope(state.session_factory) as session:
            counts = presence_engine.delete_subject(session, subject_id=subject_id)
    except UnknownSubjectError as exc:
        raise HTTPException(status_code=404, detail="unknown presence subject") from exc
    presence_engine.clear_retained_presence(state.mqtt_publisher, subject_id)
    logger.info("presence_subject_deleted", extra={"subject_id": subject_id, **counts})
    return {"subject_id": subject_id, "status": "deleted", **counts}


@app.post("/status/presence/webhook/focus-shortcut")
def presence_webhook_focus_shortcut(body: dict, request: Request, _auth: AdminAuth) -> dict:
    return _handle_presence_webhook(request, body, source="focus_shortcut")


@app.post("/status/presence/webhook/geofence-label")
def presence_webhook_geofence_label(body: dict, request: Request, _auth: AdminAuth) -> dict:
    return _handle_presence_webhook(request, body, source="geofence_label")


# ---------------------------------------------------------------------------
# Person-data purge (ADR-0009 §3.4)
# ---------------------------------------------------------------------------


@app.delete("/people/{person_id}/events")
def delete_person_events(
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
        "social_items_purged": result.social_items_purged,
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
        # Erase, don't flag: the mirror's text/label and the raw event payload both
        # go, exactly as a visitor's own deletion request does (ADR-0005 §6).
        scrub_social_item(session, row, now=datetime.now(UTC), reason="moderation")
    return {"kind": kind, "item_id": item_id, "status": "deleted"}


@app.get("/admin/social/guestbook")
def admin_list_guestbook(request: Request, _auth: AdminAuth) -> dict:
    return _list_social_items(request, "guestbook")


@app.delete("/admin/social/guestbook/{item_id}")
def admin_delete_guestbook(item_id: str, request: Request, _auth: AdminAuth) -> dict:
    return _delete_social_item(request, "guestbook", item_id)


@app.get("/admin/social/checkins")
def admin_list_checkins(request: Request, _auth: AdminAuth) -> dict:
    return _list_social_items(request, "checkin")


@app.delete("/admin/social/checkins/{item_id}")
def admin_delete_checkin(item_id: str, request: Request, _auth: AdminAuth) -> dict:
    return _delete_social_item(request, "checkin", item_id)


# ---------------------------------------------------------------------------
# Alertmanager webhook (admin bearer) — infra/monitoring/alertmanager.yml posts here
# ---------------------------------------------------------------------------


class AlertmanagerAlert(BaseModel):
    """One alert of the Alertmanager v4 webhook payload. Unknown fields are ignored."""

    model_config = ConfigDict(extra="ignore")

    status: Literal["firing", "resolved"] = "firing"
    labels: dict[str, str] = Field(default_factory=dict, max_length=64)
    annotations: dict[str, str] = Field(default_factory=dict, max_length=64)
    startsAt: str | None = None  # Alertmanager's own field name


class AlertmanagerWebhook(BaseModel):
    model_config = ConfigDict(extra="ignore")

    alerts: list[AlertmanagerAlert] = Field(max_length=100)


@app.post("/admin/alerts")
def admin_alerts(body: AlertmanagerWebhook, request: Request, _auth: AdminAuth) -> dict:
    """Fan each firing/resolved alert out through whichever owner channels are configured.

    Delivery goes through the fan-out worker so a slow ntfy/Telegram hop cannot
    hold Alertmanager's request open (it retries on timeout, which would double
    the message).
    """
    state = _state(request)
    notifier = state.notify_engine.notifier
    for alert in body.alerts:
        notification = notification_for_alert(
            status=alert.status, labels=alert.labels, annotations=alert.annotations
        )
        metrics.inc(ALERTS_RECEIVED, status=alert.status)
        state.fanout.submit(functools.partial(notifier.notify, notification))
    logger.info("alerts_received", extra={"count": len(body.alerts)})
    return {"accepted": len(body.alerts)}


# ---------------------------------------------------------------------------
# Token management (admin)
# ---------------------------------------------------------------------------


class IssueTokenRequest(BaseModel):
    scope: ServiceTokenScope
    door_id: str
    label: str | None = None


@app.post("/admin/tokens", status_code=201)
def admin_issue_token(body: IssueTokenRequest, request: Request, _auth: AdminAuth) -> dict:
    state = _state(request)
    with session_scope(state.session_factory) as session:
        issued = token_store.issue_and_store(
            session, scope=body.scope, door_id=body.door_id, label=body.label
        )
    return {"token_id": issued.token_id, "token": issued.raw}


@app.delete("/admin/tokens/{token_id}")
def admin_revoke_token(token_id: str, request: Request, _auth: AdminAuth) -> dict:
    state = _state(request)
    with session_scope(state.session_factory) as session:
        revoked = token_store.revoke(session, token_id=token_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="token not found or already revoked")
    return {"token_id": token_id, "status": "revoked"}


@app.get("/admin/tokens")
def admin_list_tokens(request: Request, _auth: AdminAuth) -> dict:
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
