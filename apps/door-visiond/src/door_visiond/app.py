"""FastAPI application for door-visiond.

Routes:
  GET  /health           — service health, mode, hailo/privacy/enrollment state
  GET  /metrics          — Prometheus-style text metrics
  GET  /current-visitor  — cached identity for the button path (204 if none)
  POST /enroll           — admin-auth, multipart images (ADR-0009 §5)
  POST /unenroll         — admin-auth, delete a person (E-5 semantics)
  POST /privacy-mode     — admin-auth, capture-layer kill switch (E-6)

Auth: ``DOOR_VISIOND_ADMIN_TOKEN``.  Empty = auth disabled (dev/CI).
None of these routes sit in the door button path.
"""

from __future__ import annotations

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
from pydantic import BaseModel

from door_visiond.enrollment import ProfileSpec
from door_visiond.logging_setup import get_logger
from door_visiond.service import (
    PrivacyModeActiveError,
    QualityTooLowError,
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


def _svc(request: Request) -> VisiondService:
    return request.app.state.service


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
    if auth.removeprefix("Bearer ") != cfg.admin_token:
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
    except PrivacyModeActiveError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="privacy_mode") from None
    except StaleConsentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "stale_consent", "current_version": exc.current_version},
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
    }


class _UnenrollBody(BaseModel):
    person_id: str


@app.post("/unenroll")
async def unenroll(_auth: AdminAuth, request: Request, body: _UnenrollBody) -> dict[str, object]:
    return _svc(request).unenroll(body.person_id)


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
