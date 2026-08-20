"""Owner-facing proxy for the Pi-local services the browser cannot reach (ADR-0024).

door-visiond and door-media bind ``127.0.0.1`` on purpose: ARCHITECTURE.md §2 keeps admin
surfaces off the house LAN, and door-visiond's is the biometric enrollment API. door-api
binds ``0.0.0.0`` because the kiosks and the owner's laptop need it.

The admin page called those loopback services *directly from the browser*, which works only
when the browser is the Pi's own. From a laptop every enrollment, arrival-log, invite and
privacy panel failed — and failed **silently**, rendering "Enrolled Members (0)" for a door
with two enrolled people and "Relay not configured" for a configured relay. A wrong fact is
worse than an error, because there is nothing to investigate.

So door-api forwards them, holding the service tokens server-side. This is the pattern
ADR-0019 established for doorpad enrollment and ADR-0022 for bell thumbnails; this module
generalises it for the owner's admin surface.

**Allow-listed, not open.** An open ``/admin/visiond/{path:path}`` forwarder would let
anything holding door-api's admin token reach *any* door-visiond route — including routes
added later by someone who never considered this proxy. Each entry below is a deliberate
decision that the owner's browser may invoke it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger("door_api.service_proxy")


@dataclass(frozen=True)
class ProxyRoute:
    """One method+path the owner's admin page may reach on a Pi-local service."""

    method: str
    pattern: re.Pattern[str]
    description: str


def _route(method: str, path_regex: str, description: str) -> ProxyRoute:
    return ProxyRoute(method=method, pattern=re.compile(f"^{path_regex}$"), description=description)


# door-visiond routes the admin page needs. Read-mostly; the three writes are the ones the
# owner performs from that page and nothing else here is reachable.
#
# Deliberately absent:
#   * /enroll — multipart face images. The at-door and phone-relay flows own enrollment
#     (ADR-0016/0019); proxying raw face uploads through a second service would put
#     biometric payloads on a LAN-exposed route for no gain.
#   * /metrics — door-api has its own, and Prometheus text is not something the admin
#     page reads.
#
# /health IS allowed: the enrollment panel needs `privacy_enabled` to render the
# recognition toggle, and behind admin auth the owner seeing their own door's health is
# the point of the page. It was previously fetched by a hardcoded
# `http://<host>:8081/health` inside the panel, bypassing the API client entirely.
VISIOND_ROUTES: tuple[ProxyRoute, ...] = (
    _route("GET", r"/people", "who is enrolled"),
    _route("GET", r"/current-visitor", "who the door is looking at, for the enroll preview"),
    _route("GET", r"/consent", "the current consent statement + version"),
    _route("GET", r"/health", "privacy-mode state for the recognition toggle"),
    _route("GET", r"/relay-status", "whether phone enrollment is configured and reachable"),
    _route("GET", r"/invites", "outstanding enrollment invites"),
    _route("GET", r"/visits", "the arrival log"),
    _route("GET", r"/visits/counts", "per-person arrival counts"),
    _route("POST", r"/invites", "mint an enrollment invite"),
    _route("POST", r"/invites/[A-Za-z0-9_-]+/revoke", "revoke an invite"),
    _route("POST", r"/unenroll", "delete a person and their face data"),
    _route("POST", r"/visits/purge", "erase arrival history"),
    _route("POST", r"/privacy-mode", "turn recognition off or on"),
)

# door-media: only the still. Recordings already have first-class door-api routes, and the
# live WebRTC view cannot be proxied this way at all — it needs a direct peer connection to
# MediaMTX on :8889, so the camera panel stays Pi-only (ADR-0024 §Consequences).
MEDIA_ROUTES: tuple[ProxyRoute, ...] = (
    _route("GET", r"/snapshot", "a current still, for the enrollment preview"),
)


class ProxyDenied(Exception):
    """The requested method+path is not on the allow-list."""


def resolve(routes: tuple[ProxyRoute, ...], method: str, path: str) -> ProxyRoute:
    """The matching allow-list entry, or raise.

    Paths are matched whole and anchored. A trailing query string is the caller's business
    and is forwarded verbatim; it is not part of the match, so an allow-listed path cannot
    be widened by appending one.
    """
    for route in routes:
        if route.method == method.upper() and route.pattern.match(path):
            return route
    raise ProxyDenied(f"{method.upper()} {path} is not proxied")


async def forward(
    *,
    base_url: str,
    token: str,
    method: str,
    path: str,
    query: str,
    timeout_s: float,
    body: bytes | None = None,
    content_type: str | None = None,
) -> httpx.Response:
    """Forward one allow-listed request, attaching the service's own admin token.

    The browser never sees that token: it presents door-api's, and door-api presents the
    service's. That is the whole point — the kiosk profile and the owner's laptop gain the
    *capability* without gaining the credential (ADR-0019 §"Credentials do not move").
    """
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        return await client.request(
            method.upper(),
            url,
            params=query or None,
            content=body,
            headers=headers,
        )
