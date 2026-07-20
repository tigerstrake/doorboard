"""FastAPI routes for guestbook/poll/checkin CRUD, the visitor deletion
endpoint, and the /admin moderation panel API.

Admin auth note
----------------
``packages/auth`` (session-based admin authentication per api-conventions.md)
does not exist yet in this codebase — it is a stub package with no admin
session/token implementation. Building it is explicitly out of scope for the
gemini tier (GEMINI.md: "never touch ... auth/token code"). To keep
``/admin/social/*`` non-public in the meantime, this module gates those
routes behind a single shared-secret bearer token (``DOOR_API_SOCIAL_ADMIN_TOKEN``),
compared with ``secrets.compare_digest``. If the token is unset, admin routes
fail closed (503) rather than opening up. This is a placeholder — replace
``require_admin`` with the real ``packages/auth`` dependency once that
package lands. Flagged for Claude-tier review; see the escalation issue
referenced in the PR description.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from door_api.social.errors import (
    AlreadyVotedError,
    NotFoundError,
    PollClosedError,
    RateLimitedError,
    UnsupportedDeletionTargetError,
)
from door_api.social.sanitize import SanitizationError
from door_api.social.service import SocialService
from door_api.social.store import Checkin, GuestbookEntry, Poll


def _trace_id(request: Request) -> str:
    return request.headers.get("X-Trace-Id") or str(uuid4())


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _error(code: str, message: str, trace_id: str) -> dict:
    return {"error": {"code": code, "message": message, "trace_id": trace_id}}


def _raise_for_domain_error(exc: Exception, trace_id: str) -> None:
    if isinstance(exc, RateLimitedError):
        raise HTTPException(status_code=429, detail=_error("rate_limited", str(exc), trace_id))
    if isinstance(exc, SanitizationError):
        raise HTTPException(status_code=422, detail=_error("invalid_input", str(exc), trace_id))
    if isinstance(exc, AlreadyVotedError):
        raise HTTPException(status_code=409, detail=_error("already_voted", str(exc), trace_id))
    if isinstance(exc, PollClosedError):
        raise HTTPException(status_code=409, detail=_error("poll_closed", str(exc), trace_id))
    if isinstance(exc, NotFoundError):
        raise HTTPException(status_code=404, detail=_error("not_found", str(exc), trace_id))
    if isinstance(exc, UnsupportedDeletionTargetError):
        raise HTTPException(
            status_code=400, detail=_error("unsupported_target", str(exc), trace_id)
        )
    raise exc


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class GuestbookCreateRequest(BaseModel):
    text: str
    author_label: str | None = None
    session_token: str = Field(min_length=1, max_length=200)


class PollVoteRequest(BaseModel):
    option_id: str
    session_token: str = Field(min_length=1, max_length=200)


class CheckinCreateRequest(BaseModel):
    # No client-supplied person_id: attribution is only ever derived
    # server-side from the current session's cached identity (see
    # get_current_person_id in build_social_router) — never trusted from
    # the request body. A client claiming to be a specific enrolled person
    # would otherwise let any visitor attribute check-ins to anyone.
    label: str | None = None
    # Optional reference to a visitor-captured photo_booth recording
    # (see ADR-0013). The photo itself lives in the photo-booth/gallery
    # pipeline; the check-in only stores the reference.
    photo_recording_id: str | None = None
    session_token: str = Field(min_length=1, max_length=200)


class DeletionRequest(BaseModel):
    target_kind: str
    target_id: str
    session_token: str = Field(min_length=1, max_length=200)


class AdminPollCreateRequest(BaseModel):
    question: str
    options: list[str]


def _guestbook_to_public_dict(entry: GuestbookEntry) -> dict:
    return {
        "id": entry.id,
        "text": entry.text,
        "author_label": entry.author_label,
        "created_at": entry.created_at,
    }


def _guestbook_to_admin_dict(entry: GuestbookEntry) -> dict:
    return {
        **_guestbook_to_public_dict(entry),
        "status": entry.status,
        "deleted_at": entry.deleted_at,
    }


def _poll_to_dict(poll: Poll) -> dict:
    return {
        "id": poll.id,
        "question": poll.question,
        "status": poll.status,
        "created_at": poll.created_at,
        "closed_at": poll.closed_at,
        "options": [{"id": o.id, "text": o.text} for o in poll.options],
    }


def _checkin_to_dict(checkin: Checkin) -> dict:
    return {
        "id": checkin.id,
        "person_id": checkin.person_id,
        "label": checkin.label,
        "photo_recording_id": checkin.photo_recording_id,
        "created_at": checkin.created_at,
    }


def build_social_router(
    get_service: Callable[[], SocialService],
    get_current_person_id: Callable[[], str | None],
    verify_visitor_token: Callable[[str], UUID],
) -> APIRouter:
    """Build the public + admin social router.

    Takes getters rather than fixed instances so the router (built once, at
    import time) always operates on *current* state — needed because tests
    rebuild ``DoorApiState`` between cases. ``get_current_person_id`` reads
    the door-api session machine's cached identity (populated only from real
    ``vision.identity_stable`` events for enrolled, consenting people) —
    check-in attribution is derived from this, never from client input.
    """

    router = APIRouter()

    def verified_session_key(token: str, trace_id: str) -> str:
        try:
            return str(verify_visitor_token(token))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=401,
                detail=_error("invalid_visitor_token", "invalid visitor token", trace_id),
            ) from exc

    def require_admin(authorization: str | None = Header(default=None)) -> None:
        configured = get_service().config.admin_token
        if not configured:
            raise HTTPException(
                status_code=503,
                detail=_error(
                    "admin_not_configured",
                    "admin interface is not configured on this device",
                    str(uuid4()),
                ),
            )
        prefix = "Bearer "
        presented = ""
        if authorization is not None and authorization.startswith(prefix):
            presented = authorization[len(prefix) :]
        if not presented or not secrets.compare_digest(presented, configured):
            raise HTTPException(
                status_code=401,
                detail=_error("unauthorized", "invalid or missing admin token", str(uuid4())),
            )

    # ------------------------------------------------------------------
    # Guestbook (public)
    # ------------------------------------------------------------------

    @router.post("/guestbook", status_code=201)
    def create_guestbook_entry(body: GuestbookCreateRequest, request: Request) -> dict:
        service = get_service()
        trace_id = _trace_id(request)
        session_key = verified_session_key(body.session_token, trace_id)
        try:
            entry = service.create_guestbook_entry(
                text=body.text,
                author_label=body.author_label,
                ip=_client_ip(request),
                session_token=session_key,
                trace_id=trace_id,
            )
        except (RateLimitedError, SanitizationError) as exc:
            _raise_for_domain_error(exc, trace_id)
            raise
        return _guestbook_to_public_dict(entry)

    @router.get("/guestbook")
    def list_guestbook_entries(
        request: Request, limit: int = 20, cursor: str | None = None
    ) -> dict:
        service = get_service()
        limit = min(max(limit, 1), service.config.max_list_limit)
        entries = service.list_public_guestbook_entries(limit=limit, cursor=cursor)
        return {"entries": [_guestbook_to_public_dict(e) for e in entries]}

    # ------------------------------------------------------------------
    # Polls (public)
    # ------------------------------------------------------------------

    @router.get("/polls/current")
    def get_current_poll() -> dict:
        poll = get_service().get_current_poll()
        if poll is None:
            return {"poll": None}
        return {"poll": _poll_to_dict(poll)}

    @router.get("/polls/{poll_id}/results")
    def get_poll_results(poll_id: str, request: Request) -> dict:
        service = get_service()
        trace_id = _trace_id(request)
        try:
            results = service.poll_results(poll_id)
        except NotFoundError as exc:
            _raise_for_domain_error(exc, trace_id)
            raise
        return {"poll_id": poll_id, "results": results}

    @router.post("/polls/{poll_id}/vote", status_code=201)
    def cast_vote(poll_id: str, body: PollVoteRequest, request: Request) -> dict:
        service = get_service()
        trace_id = _trace_id(request)
        session_key = verified_session_key(body.session_token, trace_id)
        try:
            service.cast_vote(
                poll_id=poll_id,
                option_id=body.option_id,
                ip=_client_ip(request),
                session_token=session_key,
                trace_id=trace_id,
            )
        except (RateLimitedError, NotFoundError, AlreadyVotedError, PollClosedError) as exc:
            _raise_for_domain_error(exc, trace_id)
            raise
        return {"poll_id": poll_id, "option_id": body.option_id}

    # ------------------------------------------------------------------
    # Check-ins (public)
    # ------------------------------------------------------------------

    @router.post("/checkins", status_code=201)
    def create_checkin(body: CheckinCreateRequest, request: Request) -> dict:
        service = get_service()
        trace_id = _trace_id(request)
        session_key = verified_session_key(body.session_token, trace_id)
        try:
            checkin = service.create_checkin(
                person_id=get_current_person_id(),
                label=body.label,
                photo_recording_id=body.photo_recording_id,
                ip=_client_ip(request),
                session_token=session_key,
                trace_id=trace_id,
            )
        except (RateLimitedError, SanitizationError) as exc:
            _raise_for_domain_error(exc, trace_id)
            raise
        return _checkin_to_dict(checkin)

    @router.get("/checkins")
    def list_checkins(limit: int = 20, cursor: str | None = None) -> dict:
        service = get_service()
        limit = min(max(limit, 1), service.config.max_list_limit)
        checkins = service.list_checkins(limit=limit, cursor=cursor)
        return {"checkins": [_checkin_to_dict(c) for c in checkins]}

    @router.get("/checkins/stats/most-frequent")
    def most_frequent_visitor() -> dict:
        return {"stat": get_service().most_frequent_visitor_stat()}

    # ------------------------------------------------------------------
    # Deletion requests (public, visitor-initiated)
    # ------------------------------------------------------------------

    @router.post("/social/deletion-requests", status_code=202)
    def request_deletion(body: DeletionRequest, request: Request) -> dict:
        service = get_service()
        trace_id = _trace_id(request)
        session_key = verified_session_key(body.session_token, trace_id)
        try:
            service.request_deletion(
                target_kind=body.target_kind,
                target_id=body.target_id,
                ip=_client_ip(request),
                session_token=session_key,
                trace_id=trace_id,
                actor="visitor",
            )
        except (
            RateLimitedError,
            NotFoundError,
            UnsupportedDeletionTargetError,
        ) as exc:
            _raise_for_domain_error(exc, trace_id)
            raise
        return {"target_kind": body.target_kind, "target_id": body.target_id, "status": "deleted"}

    # ------------------------------------------------------------------
    # Admin moderation panel
    # ------------------------------------------------------------------

    @router.get("/admin/guestbook", dependencies=[Depends(require_admin)])
    def admin_list_guestbook(
        status: str = "pending", limit: int = 20, cursor: str | None = None
    ) -> dict:
        service = get_service()
        limit = min(max(limit, 1), service.config.max_list_limit)
        entries = service.list_admin_guestbook_entries(status=status, limit=limit, cursor=cursor)
        return {"entries": [_guestbook_to_admin_dict(e) for e in entries]}

    @router.post("/admin/guestbook/{entry_id}/approve", dependencies=[Depends(require_admin)])
    def admin_approve_guestbook(entry_id: str, request: Request) -> dict:
        service = get_service()
        trace_id = _trace_id(request)
        try:
            service.approve_guestbook_entry(entry_id, actor="admin")
        except NotFoundError as exc:
            _raise_for_domain_error(exc, trace_id)
            raise
        return {"id": entry_id, "status": "approved"}

    @router.delete("/admin/guestbook/{entry_id}", dependencies=[Depends(require_admin)])
    def admin_delete_guestbook(entry_id: str, request: Request) -> dict:
        service = get_service()
        trace_id = _trace_id(request)
        try:
            service.request_deletion(
                target_kind="guestbook",
                target_id=entry_id,
                ip=_client_ip(request),
                session_token="admin",
                trace_id=trace_id,
                actor="admin",
            )
        except NotFoundError as exc:
            _raise_for_domain_error(exc, trace_id)
            raise
        return {"id": entry_id, "status": "deleted"}

    @router.get("/admin/polls", dependencies=[Depends(require_admin)])
    def admin_list_polls(limit: int = 20) -> dict:
        service = get_service()
        limit = min(max(limit, 1), service.config.max_list_limit)
        return {"polls": [_poll_to_dict(p) for p in service.list_polls(limit=limit)]}

    @router.post("/admin/polls", status_code=201, dependencies=[Depends(require_admin)])
    def admin_create_poll(body: AdminPollCreateRequest, request: Request) -> dict:
        service = get_service()
        trace_id = _trace_id(request)
        try:
            poll = service.create_poll(question=body.question, options=body.options, actor="admin")
        except SanitizationError as exc:
            _raise_for_domain_error(exc, trace_id)
            raise
        return _poll_to_dict(poll)

    @router.post("/admin/polls/{poll_id}/close", dependencies=[Depends(require_admin)])
    def admin_close_poll(poll_id: str, request: Request) -> dict:
        service = get_service()
        trace_id = _trace_id(request)
        try:
            service.close_poll(poll_id, actor="admin")
        except NotFoundError as exc:
            _raise_for_domain_error(exc, trace_id)
            raise
        return {"id": poll_id, "status": "closed"}

    @router.get("/admin/checkins/stats/most-frequent", dependencies=[Depends(require_admin)])
    def admin_most_frequent_visitor() -> dict:
        return {"stat": get_service().most_frequent_visitor_stat()}

    @router.delete("/admin/checkins/{checkin_id}", dependencies=[Depends(require_admin)])
    def admin_delete_checkin(checkin_id: str, request: Request) -> dict:
        service = get_service()
        trace_id = _trace_id(request)
        try:
            service.request_deletion(
                target_kind="checkin",
                target_id=checkin_id,
                ip=_client_ip(request),
                session_token="admin",
                trace_id=trace_id,
                actor="admin",
            )
        except (NotFoundError, RateLimitedError) as exc:
            _raise_for_domain_error(exc, trace_id)
            raise
        return {"id": checkin_id, "status": "deleted"}

    @router.get("/admin/social/moderation-log", dependencies=[Depends(require_admin)])
    def admin_moderation_log(limit: int = 50) -> dict:
        service = get_service()
        limit = min(max(limit, 1), service.config.max_list_limit)
        return {"entries": service.moderation_log(limit=limit)}

    return router
