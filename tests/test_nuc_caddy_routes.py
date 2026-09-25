"""`infra/caddy/Caddyfile` must route every control-plane path to control-plane-api.

The matcher listed `/webhooks/*`, which no route serves, and omitted `/status/*` — so under
`--profile https` the LUKS key release (`/status/keys/enrollment`) and every presence webhook
landed on Home Assistant as a 404. That is the exact endpoint the enrollment runbook says to
put behind this proxy to stop the passphrase crossing the LAN in clear.

So: every first path segment registered on the control-plane FastAPI app (read from the
decorators, never by importing the app) must be covered by the control-plane matcher, and
every matcher prefix must correspond to a registered segment.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CADDYFILE = REPO_ROOT / "infra/caddy/Caddyfile"
CONTROL_PLANE_APP = REPO_ROOT / "apps/control-plane-api/src/control_plane_api/app.py"
_METHODS = {"get", "post", "put", "patch", "delete", "api_route", "websocket"}


def _registered_paths() -> set[str]:
    tree = ast.parse(CONTROL_PLANE_APP.read_text())
    paths: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr in _METHODS
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
            ):
                paths.add(str(decorator.args[0].value))
    return paths


def _first_segments(paths: set[str]) -> set[str]:
    return {path.split("/")[1] for path in paths}


def _matcher_patterns() -> list[str]:
    text = "\n".join(line.split("#", 1)[0] for line in CADDYFILE.read_text().splitlines())
    match = re.search(r"^\s*@control_plane_api\s+path\s+(.+)$", text, re.M)
    assert match, "no `@control_plane_api path ...` matcher in the Caddyfile"
    return match.group(1).split()


def _covers(pattern: str, path: str) -> bool:
    """Caddy path matcher semantics for the shapes this file uses: exact, `/x*`, `/x/*`."""
    if pattern.endswith("/*"):
        return path.startswith(pattern[:-1]) or path == pattern[:-2]
    if pattern.endswith("*"):
        return path.startswith(pattern[:-1])
    return path == pattern


def test_the_inputs_are_where_we_think() -> None:
    assert len(_registered_paths()) > 15
    assert len(_matcher_patterns()) >= 5


def test_every_control_plane_route_reaches_control_plane_api() -> None:
    patterns = _matcher_patterns()
    unrouted = sorted(
        path for path in _registered_paths() if not any(_covers(p, path) for p in patterns)
    )
    assert not unrouted, (
        "these control-plane routes fall through to Home Assistant under --profile https:\n  "
        + "\n  ".join(unrouted)
    )


def test_every_matcher_prefix_is_a_registered_route() -> None:
    segments = _first_segments(_registered_paths())
    dead = sorted(p for p in _matcher_patterns() if p.strip("/*").split("/")[0] not in segments)
    assert not dead, f"matcher entries no control-plane route answers: {dead}"


def test_the_key_release_endpoint_is_behind_the_proxy() -> None:
    # The whole point of fronting the control plane with TLS (enrollment-encrypted-volume.md).
    assert any(_covers(p, "/status/keys/enrollment") for p in _matcher_patterns())
