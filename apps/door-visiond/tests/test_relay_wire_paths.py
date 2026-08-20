"""The Pi's relay paths must exist on the relay (ADR-0016 §6).

Everything else about the relay is tested on one side or the other: the seal
format has a cross-language vector (P-12), the relay's storage behaviour has its
own vitest suite (P-13), and the Pi's logic runs against a fake transport
(P-14…P-19).  What none of those catch is a plain mismatch — the Pi asking for
``/api/pickup`` while the route lives at ``/api/collect``, or sending PUT where
the handler exports POST.  That failure would only surface on real hardware
against a real deployment, which is the worst place to find it.

So this reads the relay's route files and asserts each path the transport uses
exists and exports the verb the transport sends.  No network, no Node.
"""

from __future__ import annotations

import re

import pytest
from door_visiond.relay_client import HttpRelayTransport

from .conftest import REPO_ROOT

RELAY_API_ROOT = REPO_ROOT / "apps" / "public-relay" / "app" / "api"

# (method, path template) for every exchange HttpRelayTransport performs. Keep in
# step with the method bodies below it.
EXPECTED_ROUTES: list[tuple[str, str]] = [
    ("PUT", "/api/door-key"),
    ("PUT", "/api/invite"),
    ("DELETE", "/api/invite/{inviteId}"),
    ("GET", "/api/pickup"),
    ("POST", "/api/pickup/ack"),
]


def _route_file_for(path: str):
    """Map a URL path to its Next.js App Router `route.ts`."""
    segments = [segment for segment in path.strip("/").split("/")[1:] if segment]
    directory = RELAY_API_ROOT
    for segment in segments:
        if segment.startswith("{") and segment.endswith("}"):
            # A dynamic segment is a [param] directory; the param name is the
            # relay's business, so accept whichever bracketed directory exists.
            candidates = [child for child in directory.iterdir() if child.name.startswith("[")]
            assert len(candidates) == 1, f"expected one dynamic segment under {directory}"
            directory = candidates[0]
        else:
            directory = directory / segment
    return directory / "route.ts"


def test_relay_api_directory_exists() -> None:
    assert RELAY_API_ROOT.is_dir(), f"relay routes not found at {RELAY_API_ROOT}"


@pytest.mark.parametrize(("method", "path"), EXPECTED_ROUTES, ids=lambda v: str(v))
def test_transport_path_exists_on_the_relay(method: str, path: str) -> None:
    route_file = _route_file_for(path)
    assert route_file.is_file(), f"the Pi calls {method} {path} but {route_file} does not exist"

    source = route_file.read_text(encoding="utf-8")
    exported = set(re.findall(r"export async function ([A-Z]+)\s*\(", source))
    assert method in exported, (
        f"the Pi sends {method} {path} but {route_file.name} exports {sorted(exported)}"
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
