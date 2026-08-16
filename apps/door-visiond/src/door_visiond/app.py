"""FastAPI application for door-visiond.

Routes:
  GET  /health           — service health, mode, hailo/privacy/enrollment state
  GET  /metrics          — Prometheus-style text metrics
  GET  /current-visitor  — cached identity for the button path (204 if none)
  POST /enroll           — admin-auth, multipart images (ADR-0009 §5)
  POST /unenroll         — admin-auth, delete a person (E-5 semantics)
  POST /privacy-mode     — admin-auth, capture-layer kill switch (E-6)
  POST /invites          — admin-auth, mint a remote-enrollment invite (ADR-0016 §4)
  POST /self-enroll/invites — NO auth, the doorpad minting its own invite (ADR-0019)
  GET  /invites          — admin-auth, list invites and their state
  POST /invites/{id}/revoke — admin-auth, revoke an unconsumed invite
  GET  /relay-status     — admin-auth, remote-enrollment relay reachability
  POST /relay-key/rotate — admin-auth, mint a fresh sealing keypair
  GET  /visits           — admin-auth, arrival history (ADR-0018; never public)
  GET  /visits/counts    — admin-auth, per-person visit totals
  POST /visits/purge     — admin-auth, forget arrival history

Auth: ``DOOR_VISIOND_ADMIN_TOKEN``. Empty closes protected routes.
None of these routes sit in the door button path.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from door_visiond.consent import ConsentStatementUnavailable, load_consent_statement
from door_visiond.enrollment import ProfileSpec
from door_visiond.logging_setup import get_logger
from door_visiond.service import (
    DisplayNameTakenError,
    EnrollmentLockedError,
    PrivacyModeActiveError,
    QualityTooLowError,
    SelfEnrollClosedError,
    StaleConsentError,
    VisiondService,
)
from door_visiond.settings import Settings
from door_visiond.settings import settings as get_settings

logger = get_logger("door_visiond.app")

_VALID_CHANGED_BY = {"admin", "schedule", "physical"}


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    cfg = get_settings()
    svc = VisiondService(cfg)
    await svc.start()
    app.state.cfg = cfg
    app.state.service = svc
    logger.info("door_visiond_ready", extra={"mode": svc.effective_mode})
    try:
        yield
    finally:
        await svc.stop()
        logger.info("door_visiond_shutdown")


app = FastAPI(
    title="door-visiond",
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


def _svc(request: Request) -> VisiondService:
    return request.app.state.service


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
    if not secrets.compare_digest(auth.removeprefix("Bearer "), cfg.admin_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin token")


AdminAuth = Annotated[None, Depends(_require_admin)]

_start_time = time.monotonic()


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return _svc(request).health()


@app.get("/metrics")
async def metrics(request: Request) -> Response:
    svc = _svc(request)
    snap = svc.metrics_snapshot()
    uptime_s = time.monotonic() - _start_time
    lines: list[str] = []
    for key, value in snap.items():
        metric = f"door_visiond_{key}"
        lines.append(f"# TYPE {metric} gauge")
        lines.append(f"{metric} {value}")
    lines.append("# TYPE door_visiond_uptime_s counter")
    lines.append(f"door_visiond_uptime_s {uptime_s:.1f}")
    lines.append("")
    return Response(
        content="\n".join(lines),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/current-visitor", response_model=None)
async def current_visitor(request: Request) -> Response | dict[str, object]:
    visitor = _svc(request).current_visitor()
    if visitor is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return visitor


@app.post("/enroll", status_code=status.HTTP_201_CREATED)
async def enroll(
    _auth: AdminAuth,
    request: Request,
    display_name: Annotated[str, Form()],
    consent_version: Annotated[str, Form()],
    consent_confirmed: Annotated[bool, Form()],
    profile_id: Annotated[str, Form()],
    color: Annotated[str, Form()],
    images: Annotated[list[UploadFile], File()],
    sound: Annotated[str | None, Form()] = None,
) -> dict[str, object]:
    svc = _svc(request)
    image_bytes = [await f.read() for f in images]
    try:
        result = svc.enroll(
            display_name=display_name,
            consent_version=consent_version,
            consent_confirmed=consent_confirmed,
            images=image_bytes,
            profile=ProfileSpec(profile_id=profile_id, color=color, sound=sound),
        )
    except EnrollmentLockedError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="encrypted enrollment storage is locked",
        ) from None
    except PrivacyModeActiveError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="privacy_mode") from None
    except StaleConsentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "stale_consent", "current_version": exc.current_version},
        ) from None
    except DisplayNameTakenError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "display_name_taken", "display_name": exc.display_name},
        ) from None
    except QualityTooLowError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "quality_too_low", "quality": exc.qualities},
        ) from None
    return {
        "person_id": result.person_id,
        "embeddings_created": result.embeddings_created,
        "quality": result.quality,
        "profile_id": result.profile_id,
        "profile_reassigned": result.profile_reassigned,
    }


class _UnenrollBody(BaseModel):
    person_id: str


@app.post("/unenroll")
async def unenroll(_auth: AdminAuth, request: Request, body: _UnenrollBody) -> dict[str, object]:
    try:
        return _svc(request).unenroll(body.person_id)
    except EnrollmentLockedError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="encrypted enrollment storage is locked",
        ) from None


class _PrivacyBody(BaseModel):
    enabled: bool
    changed_by: str


@app.post("/privacy-mode")
async def privacy_mode(_auth: AdminAuth, request: Request, body: _PrivacyBody) -> dict[str, object]:
    if body.changed_by not in _VALID_CHANGED_BY:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"changed_by must be one of {sorted(_VALID_CHANGED_BY)}",
        )
    _svc(request).set_privacy_mode(enabled=body.enabled, changed_by=body.changed_by)
    return {"enabled": body.enabled}


@app.get("/people")
async def list_people(_auth: AdminAuth, request: Request) -> list[dict[str, Any]]:
    svc = _svc(request)
    if svc.health()["enrollment_locked"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="encrypted enrollment storage is locked",
        )
    return svc._store.list_people()


class _InviteBody(BaseModel):
    """``label`` is the admin's own note (e.g. "Tiger's phone").

    It stays on the door device — the relay is never told it (ADR-0016 §1).
    """

    label: str | None = Field(default=None, max_length=64)


@app.post("/self-enroll/invites", status_code=status.HTTP_201_CREATED)
async def create_self_enroll_invite(request: Request) -> dict[str, object]:
    """Mint an invite for whoever is standing at the doorpad (ADR-0019).

    Deliberately unauthenticated, and the only route here that is: door-visiond binds
    to loopback, so reaching this means running on the Pi, and the doorpad reaches it
    through door-api rather than holding a credential of its own. Presence is the
    authorization; the caps in the service are what make that safe.

    Takes no body on purpose — a visitor has no admin note to write, and accepting a
    caller-supplied label would let them forge one that looks owner-minted and so
    escape the hourly cap, which is counted by label.
    """
    try:
        return _svc(request).create_self_enroll_invite()
    except SelfEnrollClosedError as exc:
        code = (
            status.HTTP_429_TOO_MANY_REQUESTS
            if exc.reason == "rate_limited"
            else status.HTTP_409_CONFLICT
        )
        headers = {"Retry-After": str(exc.retry_after_s)} if exc.retry_after_s is not None else None
        raise HTTPException(
            status_code=code,
            detail={"error": "self_enroll_closed", "reason": exc.reason},
            headers=headers,
        ) from None
    except PrivacyModeActiveError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="privacy_mode") from None
    except EnrollmentLockedError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="encrypted enrollment storage is locked",
        ) from None
    except ConsentStatementUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="canonical consent statement unavailable",
        ) from None


@app.post("/invites", status_code=status.HTTP_201_CREATED)
async def create_invite(_auth: AdminAuth, request: Request, body: _InviteBody) -> dict[str, object]:
    """Mint a single-use remote-enrollment invite (ADR-0016 §4).

    The response contains the one and only copy of the invite secret, inside the
    URL. It is never stored or logged, so a lost URL means minting a new invite.
    """
    try:
        return _svc(request).create_invite(label=body.label)
    except PrivacyModeActiveError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="privacy_mode") from None
    except EnrollmentLockedError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="encrypted enrollment storage is locked",
        ) from None
    except ConsentStatementUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="canonical consent statement unavailable",
        ) from None


@app.get("/invites")
async def list_invites(
    _auth: AdminAuth,
    request: Request,
    include_closed: bool = False,
) -> list[dict[str, object]]:
    return _svc(request).list_invites(include_closed=include_closed)


@app.post("/invites/{invite_id}/revoke")
async def revoke_invite(_auth: AdminAuth, request: Request, invite_id: str) -> dict[str, object]:
    return _svc(request).revoke_invite(invite_id)


@app.get("/relay-status")
async def relay_status(_auth: AdminAuth, request: Request) -> dict[str, object]:
    return _svc(request).relay_status()


@app.post("/relay-key/rotate")
async def rotate_relay_key(_auth: AdminAuth, request: Request) -> dict[str, object]:
    """Mint a fresh sealing keypair. Outstanding QR codes stop verifying (E-10)."""
    try:
        return _svc(request).rotate_relay_key()
    except EnrollmentLockedError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="encrypted enrollment storage is locked",
        ) from None


@app.get("/visits")
async def list_visits(
    _auth: AdminAuth,
    request: Request,
    limit: int = 200,
    person_id: str | None = None,
) -> list[dict[str, Any]]:
    """Arrival history (ADR-0018 §1).

    Admin-only, and deliberately so: this is presence data, and ADR-0005 §5 keeps
    visitor logs off public routes (E-24). There is no public counterpart.
    """
    try:
        return _svc(request).list_visits(limit=max(1, min(limit, 1000)), person_id=person_id)
    except EnrollmentLockedError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="encrypted enrollment storage is locked",
        ) from None


@app.get("/visits/counts")
async def visit_counts(_auth: AdminAuth, request: Request) -> list[dict[str, Any]]:
    """Per-person totals for the admin panel. Never a public payload (E-24)."""
    try:
        return _svc(request).visit_counts()
    except EnrollmentLockedError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="encrypted enrollment storage is locked",
        ) from None


@app.post("/visits/purge")
async def purge_visits(
    _auth: AdminAuth, request: Request, person_id: str | None = None
) -> dict[str, object]:
    """Forget arrival history, for one person or everyone, keeping enrollment."""
    try:
        return _svc(request).purge_visits(person_id=person_id)
    except EnrollmentLockedError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="encrypted enrollment storage is locked",
        ) from None


@app.get("/consent")
async def get_consent(request: Request) -> dict[str, str]:
    svc = _svc(request)
    try:
        statement = load_consent_statement(svc._settings.consent_statement_path)
    except ConsentStatementUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="canonical consent statement unavailable",
        ) from exc
    return {"text": statement.text, "version": statement.version}
