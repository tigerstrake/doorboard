"""Service-token primitives: issuance, verification, revocation.

Pure logic, no I/O and no storage. A consuming service (currently
control-plane-api) persists ``ServiceTokenRecord`` rows in its own database
and wires ``ServiceTokenAuthenticator`` to that storage. Keeping this package
I/O-free means the crypto/scope logic is unit-testable without a database and
reusable by any future token-issuing service.

Token shape: ``dbt_<token_id>.<secret>``. ``token_id`` is public (used as the
storage lookup key so verification is O(1), not a scan-and-compare over every
issued token) and ``secret`` is the CSPRNG-generated part that is never
stored in plaintext — only its SHA-256 hex digest is persisted. Only the
issuing call ever sees the raw secret. The separator between ``token_id``
and ``secret`` is ``.`` rather than ``_`` because both halves come from
``secrets.token_urlsafe``, whose alphabet includes ``_`` — a second
underscore inside either half would make a naive ``split("_")`` ambiguous.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

TOKEN_PREFIX = "dbt"

# Scopes a Pi-side client may hold. "ingest" covers both event-batch
# ingestion and the person-data purge call (both are Pi-automation-initiated
# calls into the trusted control plane); "upload" is door-sync's media
# upload credential; "config" is used to fetch (never write) a door's config
# bundle.
ServiceTokenScope = Literal["ingest", "upload", "config"]

_TOKEN_ID_BYTES = 9
_SECRET_BYTES = 32

TokenLookup = Callable[[str], "ServiceTokenRecord | None"]


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """Result of issuing a new token. ``raw`` is shown to the caller exactly once."""

    token_id: str
    raw: str
    secret_hash: str


@dataclass(frozen=True, slots=True)
class ParsedToken:
    token_id: str
    secret: str


@dataclass(frozen=True, slots=True)
class ServiceTokenRecord:
    """What a store persists for one issued token. No raw secret ever appears here."""

    token_id: str
    secret_hash: str
    scope: ServiceTokenScope
    door_id: str
    created_at: datetime
    revoked_at: datetime | None = None
    expires_at: datetime | None = None
    label: str | None = None


class TokenError(Exception):
    """Base class for all token verification failures."""


class TokenMalformedError(TokenError):
    pass


class TokenNotFoundError(TokenError):
    pass


class TokenRevokedError(TokenError):
    pass


class TokenExpiredError(TokenError):
    pass


class TokenScopeError(TokenError):
    pass


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def issue_service_token(*, scope: ServiceTokenScope, door_id: str) -> IssuedToken:
    """Generate a new token. Callers persist a ``ServiceTokenRecord`` from the result."""
    del scope, door_id  # not encoded in the raw token; carried by the stored record
    token_id = secrets.token_urlsafe(_TOKEN_ID_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    return IssuedToken(
        token_id=token_id,
        raw=f"{TOKEN_PREFIX}_{token_id}.{secret}",
        secret_hash=hash_secret(secret),
    )


def parse_presented_token(raw: str) -> ParsedToken:
    """Split a presented token into its lookup key and secret. Raises on malformed input."""
    prefix = f"{TOKEN_PREFIX}_"
    if not raw.startswith(prefix):
        msg = "malformed service token"
        raise TokenMalformedError(msg)
    remainder = raw[len(prefix) :]
    token_id, sep, secret = remainder.partition(".")
    if not sep or not token_id or not secret:
        msg = "malformed service token"
        raise TokenMalformedError(msg)
    return ParsedToken(token_id=token_id, secret=secret)


def verify_secret(secret: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(secret), expected_hash)


def authenticate(
    raw: str,
    *,
    lookup: TokenLookup,
    required_scope: ServiceTokenScope,
    now: datetime,
) -> ServiceTokenRecord:
    """Verify a presented raw token against a record returned by ``lookup``.

    ``lookup`` is a callback (typically a thin wrapper around a DB query by
    ``token_id``) rather than a full store interface, so callers don't need
    to implement a class just to authenticate one request.
    """
    parsed = parse_presented_token(raw)
    record = lookup(parsed.token_id)
    if record is None:
        msg = "unknown token"
        raise TokenNotFoundError(msg)
    if not verify_secret(parsed.secret, record.secret_hash):
        msg = "unknown token"
        raise TokenNotFoundError(msg)
    if record.revoked_at is not None:
        msg = "token revoked"
        raise TokenRevokedError(msg)
    if record.expires_at is not None and now >= record.expires_at:
        msg = "token expired"
        raise TokenExpiredError(msg)
    if record.scope != required_scope:
        msg = f"token scope {record.scope!r} does not permit {required_scope!r}"
        raise TokenScopeError(msg)
    return record
