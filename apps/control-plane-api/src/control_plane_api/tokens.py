"""Postgres-backed service token store, built on `doorboard_auth.tokens`.

`doorboard_auth` supplies the crypto/scope logic (issue, parse, verify) and
stays I/O-free; this module is the one place that persists
`ServiceTokenRecord` rows and wires FastAPI's `Authorization: Bearer ...`
header into `doorboard_auth.authenticate`. Revocation is effective
immediately because every request re-queries this table — there is no
cache, in-process or otherwise, of "is this token currently valid."
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from doorboard_auth import (
    IssuedToken,
    ServiceTokenRecord,
    ServiceTokenScope,
    TokenError,
    authenticate,
    issue_service_token,
)
from fastapi import Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_plane_api.db import session_scope
from control_plane_api.models import ServiceTokenRow


def _row_to_record(row: ServiceTokenRow) -> ServiceTokenRecord:
    return ServiceTokenRecord(
        token_id=row.token_id,
        secret_hash=row.secret_hash,
        scope=row.scope,  # type: ignore[arg-type]
        door_id=row.door_id,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        expires_at=row.expires_at,
        label=row.label,
    )


def issue_and_store(
    session: Session,
    *,
    scope: ServiceTokenScope,
    door_id: str,
    label: str | None = None,
    now: datetime | None = None,
) -> IssuedToken:
    issued = issue_service_token(scope=scope, door_id=door_id)
    row = ServiceTokenRow(
        token_id=issued.token_id,
        secret_hash=issued.secret_hash,
        scope=scope,
        door_id=door_id,
        label=label,
        created_at=now or datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return issued


def revoke(session: Session, *, token_id: str, now: datetime | None = None) -> bool:
    """Revoke a token. Idempotent: revoking an already-revoked/missing token returns False."""
    row = session.get(ServiceTokenRow, token_id)
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = now or datetime.now(UTC)
    session.flush()
    return True


def lookup(session: Session, token_id: str) -> ServiceTokenRecord | None:
    row = session.get(ServiceTokenRow, token_id)
    if row is None:
        return None
    return _row_to_record(row)


def mark_used(session: Session, *, token_id: str, now: datetime | None = None) -> None:
    row = session.get(ServiceTokenRow, token_id)
    if row is not None:
        row.last_used_at = now or datetime.now(UTC)


def list_active(session: Session, *, door_id: str | None = None) -> list[ServiceTokenRecord]:
    stmt = select(ServiceTokenRow).where(ServiceTokenRow.revoked_at.is_(None))
    if door_id is not None:
        stmt = stmt.where(ServiceTokenRow.door_id == door_id)
    rows = session.execute(stmt).scalars().all()
    return [_row_to_record(r) for r in rows]


class InvalidTokenHTTPError(HTTPException):
    def __init__(self, exc: TokenError) -> None:
        detail = {"error": {"code": "unauthorized", "message": str(exc)}}
        super().__init__(status_code=401, detail=detail)


def require_scope(
    required_scope: ServiceTokenScope,
    *,
    session_factory: sessionmaker[Session],
) -> Callable[..., ServiceTokenRecord]:
    """Build a FastAPI dependency (wrap with `Depends(...)`) gating a route on `required_scope`.

    Runs its own short transaction (lookup + `mark_used`) independent of
    whatever the route handler itself does with the database, so an auth
    failure never touches, and a slow route body never delays, the token's
    last-used bookkeeping.
    """

    def dependency(authorization: str | None = Header(default=None)) -> ServiceTokenRecord:
        prefix = "Bearer "
        if authorization is None or not authorization.startswith(prefix):
            raise InvalidTokenHTTPError(TokenError("missing bearer token"))
        raw = authorization[len(prefix) :]
        with session_scope(session_factory) as session:
            try:
                record = authenticate(
                    raw,
                    lookup=lambda tid: lookup(session, tid),
                    required_scope=required_scope,
                    now=datetime.now(UTC),
                )
            except TokenError as exc:
                raise InvalidTokenHTTPError(exc) from exc
            mark_used(session, token_id=record.token_id)
        return record

    return dependency
