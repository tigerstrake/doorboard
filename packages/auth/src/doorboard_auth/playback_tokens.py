"""Short-lived capability tokens for the DoorPad's own playback/review routes.

door-media serves a visitor's just-recorded video message and photo-booth still back
to the DoorPad (``/recordings/{id}/file``, ``/photos/{id}/review``,
``/photos/{id}/file``). Those routes used to be gated on ``session_id`` alone, but
``session_id`` is broadcast by design -- every ``/ws`` client, ``/session``, the
visitor's own phone -- so it is knowledge, not authorization: with any LAN path to
door-media, a visitor's private message was fetchable until retention deleted it.

A playback token is an HMAC over exactly one ``(recording_id, session_id,
expires_at)`` and lives at most :data:`PLAYBACK_TOKEN_MAX_TTL_S`. The key is derived
from ``DOOR_MEDIA_ADMIN_TOKEN``, which door-api and door-media both already hold
(door-api needs it for ``/internal/session_event``), so no new secret moves to the
door: door-api mints the token into ``playback_url``, door-media verifies it with its
own copy. Pure logic, no I/O; the only clock is the ``now`` the caller passes.

Wire shape: ``<expires_at>.<base64url(HMAC-SHA256(key, "rid|sid|exp"))>`` with the
key ``sha256(b"doorboard-playback:" + admin_token)``. Both sides implement this file,
so its constants and byte layout are the contract.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

# Long enough for the review screen's countdown and a reload; short enough that a
# leaked URL is worthless by the time anyone could act on it.
PLAYBACK_TOKEN_MAX_TTL_S = 300

_KEY_DOMAIN = b"doorboard-playback:"


def derive_playback_key(admin_token: str) -> bytes:
    """The HMAC key, domain-separated from the admin token itself."""
    return hashlib.sha256(_KEY_DOMAIN + admin_token.encode()).digest()


def mint_playback_token(
    admin_token: str, *, recording_id: str, session_id: str, expires_at: int
) -> str:
    """``"<expires_at>.<mac>"`` binding exactly this recording and session until ``expires_at``."""
    mac = _mac(admin_token, recording_id=recording_id, session_id=session_id, expires_at=expires_at)
    return f"{expires_at}.{mac}"


def verify_playback_token(
    admin_token: str,
    token: str,
    *,
    recording_id: str,
    session_id: str,
    now: float,
) -> bool:
    """True only for a well-formed, unexpired token minted for this recording and session.

    Also refuses a token whose expiry lies further than :data:`PLAYBACK_TOKEN_MAX_TTL_S`
    beyond ``now``: the minter is trusted, but a mint bug (or a wrong clock) must not
    turn a five-minute capability into a permanent one. Constant-time comparison.
    """
    expires_text, sep, presented_mac = token.partition(".")
    if not sep or not presented_mac or not expires_text.isdigit():
        return False
    expires_at = int(expires_text)
    if expires_at <= now or expires_at - now > PLAYBACK_TOKEN_MAX_TTL_S:
        return False
    expected = _mac(
        admin_token, recording_id=recording_id, session_id=session_id, expires_at=expires_at
    )
    return hmac.compare_digest(expected.encode(), presented_mac.encode())


def _mac(admin_token: str, *, recording_id: str, session_id: str, expires_at: int) -> str:
    message = f"{recording_id}|{session_id}|{expires_at}".encode()
    digest = hmac.new(derive_playback_key(admin_token), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")
