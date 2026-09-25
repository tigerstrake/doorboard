"""Playback tokens: a capability for one recording, one session, five minutes."""

from __future__ import annotations

import hashlib
import re

import pytest
from doorboard_auth import (
    PLAYBACK_TOKEN_MAX_TTL_S,
    derive_playback_key,
    mint_playback_token,
    verify_playback_token,
)

ADMIN = "door-media-admin-token"
RID = "01922f7e-4b1a-7c3d-9e2f-0123456789ab"
SID = "8f0c2b9e-3d4a-4f5b-8c6d-7e8f9a0b1c2d"
NOW = 1_800_000_000.0


def _mint(expires_at: int, *, admin: str = ADMIN, rid: str = RID, sid: str = SID) -> str:
    return mint_playback_token(admin, recording_id=rid, session_id=sid, expires_at=expires_at)


def _verify(token: str, *, admin: str = ADMIN, rid: str = RID, sid: str = SID, now: float = NOW):
    return verify_playback_token(admin, token, recording_id=rid, session_id=sid, now=now)


def test_the_ttl_ceiling_is_five_minutes() -> None:
    assert PLAYBACK_TOKEN_MAX_TTL_S == 300


def test_mint_then_verify() -> None:
    token = _mint(int(NOW) + 120)
    assert _verify(token)


def test_wire_shape_is_expiry_dot_unpadded_base64url_mac() -> None:
    token = _mint(int(NOW) + 60)
    assert re.fullmatch(r"\d+\.[A-Za-z0-9_-]{43}", token), token
    assert token.startswith(f"{int(NOW) + 60}.")


def test_the_key_is_domain_separated_from_the_admin_token() -> None:
    key = derive_playback_key(ADMIN)
    assert len(key) == 32
    assert key == hashlib.sha256(b"doorboard-playback:" + ADMIN.encode()).digest()
    assert key != hashlib.sha256(ADMIN.encode()).digest()


def test_bound_to_the_recording() -> None:
    token = _mint(int(NOW) + 60)
    assert not _verify(token, rid="01922f7e-4b1a-7c3d-9e2f-0123456789ac")


def test_bound_to_the_session() -> None:
    token = _mint(int(NOW) + 60)
    assert not _verify(token, sid="8f0c2b9e-3d4a-4f5b-8c6d-7e8f9a0b1c2e")


def test_bound_to_the_admin_token() -> None:
    token = _mint(int(NOW) + 60)
    assert not _verify(token, admin="a-different-door")


def test_expired_is_refused_at_the_boundary() -> None:
    token = _mint(int(NOW))
    assert not _verify(token, now=NOW)
    assert _verify(token, now=NOW - 1)


def test_a_ttl_beyond_the_ceiling_is_refused_even_when_correctly_signed() -> None:
    """A mint bug or a wrong clock must not turn a five-minute capability into a
    permanent one: the verifier enforces the ceiling, not just the minter."""
    at_ceiling = _mint(int(NOW) + PLAYBACK_TOKEN_MAX_TTL_S)
    assert _verify(at_ceiling)
    over = _mint(int(NOW) + PLAYBACK_TOKEN_MAX_TTL_S + 1)
    assert not _verify(over)
    far_future = _mint(int(NOW) + 10 * 365 * 24 * 3600)
    assert not _verify(far_future)


@pytest.mark.parametrize(
    "token",
    [
        "",
        ".",
        "abc",
        "1800000060",
        "1800000060.",
        ".AAAA",
        "-1800000060.AAAA",
        "+1800000060.AAAA",
        "1800000060.AAAA.BBBB",
        "1800000060 .AAAA",
        "18000000e1.AAAA",
    ],
)
def test_malformed_tokens_are_refused(token: str) -> None:
    assert not _verify(token)


def test_a_tampered_mac_is_refused() -> None:
    token = _mint(int(NOW) + 60)
    expiry, mac = token.split(".")
    flipped = ("A" if mac[0] != "A" else "B") + mac[1:]
    assert not _verify(f"{expiry}.{flipped}")


def test_a_changed_expiry_invalidates_the_mac() -> None:
    token = _mint(int(NOW) + 60)
    _, mac = token.split(".")
    assert not _verify(f"{int(NOW) + 120}.{mac}")
