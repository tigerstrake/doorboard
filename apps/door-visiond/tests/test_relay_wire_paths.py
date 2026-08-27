"""The Pi's relay paths must exist on the relay (ADR-0016 §6).

Everything else about the relay is tested on one side or the other: the seal
format has a cross-language vector (P-12), the relay's storage behaviour has its
own vitest suite (P-13), and the Pi's logic runs against a fake transport
(P-14…P-19).  What none of those catch is a plain mismatch — the Pi asking for
``/api/pickup`` while the route lives at ``/api/collect``, or sending PUT where
the handler is registered for POST.  That failure would only surface on real
hardware against a real deployment, which is the worst place to find it.

Since the Cloudflare move (ADR-0043 §1) the relay's API is one catch-all Function
dispatching through a routing table in ``lib/apiRouter.ts`` rather than one
``app/api/**/route.ts`` file per path.  So this reads that table and asserts each
path the transport uses is registered for the verb the transport sends.  No
network, no Node.
"""

from __future__ import annotations

import re

import pytest
from door_visiond.relay_client import HttpRelayTransport

from .conftest import REPO_ROOT

RELAY_ROUTER = REPO_ROOT / "apps" / "public-relay" / "lib" / "apiRouter.ts"

# (method, path template) for every exchange HttpRelayTransport performs. Keep in
# step with the method bodies below it.
EXPECTED_ROUTES: list[tuple[str, str]] = [
    ("PUT", "/api/door-key"),
    ("PUT", "/api/invite"),
    ("DELETE", "/api/invite/{inviteId}"),
    ("GET", "/api/pickup"),
    ("POST", "/api/pickup/ack"),
]


def _canonical(path: str) -> str:
    """Normalise a path so a `:param` (router) and a `{param}` (expected) compare equal."""
    parts = []
    for segment in path.strip("/").split("/"):
        if segment.startswith(":") or (segment.startswith("{") and segment.endswith("}")):
            parts.append("{param}")
        else:
            parts.append(segment)
    return "/" + "/".join(parts)


def _registered_routes() -> set[tuple[str, str]]:
    """Parse the `{ method: "…", segments: [ … ] }` table in apiRouter.ts."""
    source = RELAY_ROUTER.read_text(encoding="utf-8")
    routes: set[tuple[str, str]] = set()
    for method, seglist in re.findall(r'method:\s*"([A-Z]+)",\s*segments:\s*\[([^\]]*)\]', source):
        segments = re.findall(r'"([^"]+)"', seglist)
        path = _canonical("/api/" + "/".join(segments))
        routes.add((method, path))
    return routes


def test_relay_router_exists_and_parses() -> None:
    assert RELAY_ROUTER.is_file(), f"relay router not found at {RELAY_ROUTER}"
    # Sanity: the table parsed to something, so an empty set below means a real mismatch.
    assert _registered_routes(), "no routes parsed from apiRouter.ts — did its format change?"


@pytest.mark.parametrize(("method", "path"), EXPECTED_ROUTES, ids=lambda v: str(v))
def test_transport_path_is_registered_on_the_relay(method: str, path: str) -> None:
    registered = _registered_routes()
    key = (method, _canonical(path))
    assert key in registered, (
        f"the Pi sends {method} {path} but apiRouter.ts registers no such route. "
        f"Registered: {sorted(registered)}"
    )


def test_transport_sends_only_the_documented_paths() -> None:
    """Catch a new exchange added to the transport without a matching route."""
    source = (
        REPO_ROOT / "apps" / "door-visiond" / "src" / "door_visiond" / "relay_client.py"
    ).read_text(encoding="utf-8")
    # Every literal request path in the transport. The lookbehind keeps plain
    # strings and f-strings apart so an interpolated path is normalised rather
    # than captured verbatim.
    used = set(re.findall(r'(?<!f)"(/api/[^"]*)"', source))
    used |= {
        re.sub(r"\{[^}]*\}", "{inviteId}", match)
        for match in re.findall(r'f"(/api/[^"]*)"', source)
    }
    documented = {path for _method, path in EXPECTED_ROUTES}
    assert used <= documented, (
        f"relay_client.py calls undocumented paths: {sorted(used - documented)}. "
        "Add them to EXPECTED_ROUTES so this test can check the relay implements them."
    )


def test_relay_base_url_must_be_https_or_loopback() -> None:
    """Invite metadata and the device token must not cross the internet in the clear."""
    transport = HttpRelayTransport(
        base_url="http://relay.example.test", device_token="t", timeout_s=1.0
    )
    with pytest.raises(Exception, match="https"):
        transport.poll_pickup()

    # Loopback stays allowed so a local relay can be developed against.
    loopback = HttpRelayTransport(
        base_url="http://127.0.0.1:3100", device_token="t", timeout_s=0.05
    )
    with pytest.raises(Exception) as caught:
        loopback.poll_pickup()
    assert "https" not in str(caught.value)
