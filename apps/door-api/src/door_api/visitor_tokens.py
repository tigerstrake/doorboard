from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from uuid import UUID


class VisitorTokenError(ValueError):
    pass


@dataclass(frozen=True)
class VisitorTokenClaims:
    session_id: UUID
    expires_at: int


def encode_visitor_token(*, secret: str, session_id: UUID, expires_at: int) -> str:
    payload = {"v": 1, "session_id": str(session_id), "exp": expires_at}
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(secret.encode(), encoded, hashlib.sha256).digest()
    return f"{_b64encode(encoded)}.{_b64encode(signature)}"


def decode_visitor_token(
    token: str,
    *,
    secret: str,
    now_epoch: float | None = None,
) -> VisitorTokenClaims:
    try:
        encoded_payload, encoded_signature = token.split(".", maxsplit=1)
        payload_bytes = _b64decode(encoded_payload)
        signature = _b64decode(encoded_signature)
    except (ValueError, UnicodeError) as exc:
        raise VisitorTokenError("malformed visitor token") from exc

    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise VisitorTokenError("invalid visitor token signature")

    try:
        payload = json.loads(payload_bytes)
        if payload.get("v") != 1:
            raise ValueError("unsupported token version")
        session_id = UUID(payload["session_id"])
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VisitorTokenError("invalid visitor token claims") from exc

    now = time.time() if now_epoch is None else now_epoch
    if expires_at <= now:
        raise VisitorTokenError("visitor token expired")
    return VisitorTokenClaims(session_id=session_id, expires_at=expires_at)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(encoded: str) -> bytes:
    padding = "=" * (-len(encoded) % 4)
    return base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
