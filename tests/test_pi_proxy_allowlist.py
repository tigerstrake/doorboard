"""`deploy/pi-door/Caddyfile` is the door Pi's entire LAN surface (ADR-0047 §4).

Every door service binds loopback; the proxy forwards an explicit path list and 404s the
rest. That list is a security boundary, so it is parsed here rather than trusted: it may
contain only the public visitor surface ADR-0017 §2 defines, the two NUC media-inbox routes,
and the per-service `/metrics/<name>` mappings — and never `/admin/*` beyond those two,
`/ws`, `/session`, `/internal/*`, `/snapshot*`, `/recordings*` or Vite's `/@fs/*`.

The upstream ports are cross-checked against each service's `*_BIND` default in the code,
and `DOOR_API_VISITOR_PUBLIC_BASE_URL` in the deploy template must name an origin this proxy
actually listens on — the fallback QR was `http://door.local` for a year while nothing
listened on port 80 anywhere.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CADDYFILE = REPO_ROOT / "deploy/pi-door/Caddyfile"
PI_ENV_TEMPLATE = REPO_ROOT / "deploy/pi-door/doorboard.env.example"
NUC_ENV_TEMPLATE = REPO_ROOT / ".env.example"
UNIT_DIR = REPO_ROOT / "infra/systemd"

# ADR-0017 §2: session lookup, the current poll, a note, a vote, a deletion request — plus
# the visitor page itself and the hashed bundle assets it loads.
PUBLIC_VISITOR_PATHS = {
    "/visitor",
    "/visitor/*",
    "/assets/*",
    "/visitor-session",
    "/visitor-token",
    "/guestbook",
    "/polls/current",
    "/polls/*/results",
    "/polls/*/vote",
    "/social/deletion-requests",
}
# ADR-0012/0022: the only two routes control-plane-api pulls from door-api.
NUC_MEDIA_PATHS = {
    "/admin/media-inbox/*/file",
    "/admin/media-inbox/*/thumbnail",
}
# The proxy maps /metrics/<name> onto each loopback service's /metrics.
METRICS_UPSTREAMS = {
    "door-api": 8080,
    "door-visiond": 8081,
    "door-media": 8082,
    "door-sync": 8083,
    "door-voice": 8084,
    "node": 9100,
}

FORBIDDEN_PREFIXES = ("/ws", "/session", "/internal", "/snapshot", "/recordings", "/@fs", "/@vite")


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _parse_caddyfile() -> tuple[str, list[tuple[set[str], str]], bool]:
    """Return (listen address, [(paths, upstream)], has_catch_all_404)."""
    text = _strip_comments(CADDYFILE.read_text())

    site = re.search(r"^(:\d+)\s*\{", text, re.M)
    assert site, "no `:<port> {` site block"
    listen = site.group(1)

    named: dict[str, set[str]] = {}
    for name, body in re.findall(r"^\s*@(\w+)\s*\{(.*?)^\s*\}", text, re.M | re.S):
        paths = re.search(r"^\s*path\s+(.+)$", body, re.M)
        assert paths, f"@{name} has no path matcher"
        named[name] = set(paths.group(1).split())
    for name, paths in re.findall(r"^\s*@(\w+)\s+path\s+(.+)$", text, re.M):
        named[name] = set(paths.split())

    forwards: list[tuple[set[str], str]] = []
    for matcher, body in re.findall(r"^\s*handle\s+(\S+)\s*\{(.*?)^\s*\}", text, re.M | re.S):
        upstream = re.search(r"reverse_proxy\s+(\S+)", body)
        assert upstream, f"handle {matcher} does not reverse_proxy"
        paths = named[matcher[1:]] if matcher.startswith("@") else {matcher}
        forwards.append((paths, upstream.group(1)))

    catch_all = re.search(r"^\s*handle\s*\{\s*respond\s+404\s*\}", text, re.M | re.S)
    return listen, forwards, catch_all is not None


def test_the_caddyfile_is_where_we_think() -> None:
    listen, forwards, _ = _parse_caddyfile()
    assert listen == ":80"
    assert len(forwards) >= 4, forwards


def test_everything_not_listed_is_a_404() -> None:
    _, _, catch_all = _parse_caddyfile()
    assert catch_all, "the Caddyfile must end with `handle { respond 404 }`"


def test_only_the_public_surface_and_the_two_nuc_hops_are_forwarded() -> None:
    _, forwards, _ = _parse_caddyfile()
    forwarded = {path for paths, _ in forwards for path in paths}
    metrics = {f"/metrics/{name}" for name in METRICS_UPSTREAMS}
    allowed = PUBLIC_VISITOR_PATHS | NUC_MEDIA_PATHS | metrics

    extra = sorted(forwarded - allowed)
    assert not extra, f"paths forwarded to the LAN that ADR-0047 §2 does not allow: {extra}"
    missing = sorted(allowed - forwarded)
    assert not missing, f"allow-listed paths the proxy no longer forwards: {missing}"


def test_no_forbidden_prefix_leaves_the_pi() -> None:
    _, forwards, _ = _parse_caddyfile()
    leaked = sorted(
        path
        for paths, _ in forwards
        for path in paths
        if path.startswith(FORBIDDEN_PREFIXES)
        or (path.startswith("/admin") and path not in NUC_MEDIA_PATHS)
    )
    assert not leaked, f"forwarded paths that must stay loopback: {leaked}"


def test_the_nuc_media_routes_require_a_bearer_header() -> None:
    text = _strip_comments(CADDYFILE.read_text())
    block = re.search(r"@nuc_media\s*\{(.*?)^\s*\}", text, re.M | re.S)
    assert block, "the NUC media matcher must be a named block"
    assert re.search(r"^\s*header\s+Authorization\s+Bearer\*", block.group(1), re.M)


def _bind_default(service: str) -> int | None:
    if service == "door-api":
        source = REPO_ROOT / "apps/door-api/src/door_api/__main__.py"
        match = re.search(r'"DOOR_API_BIND",\s*"127\.0\.0\.1:(\d+)"', source.read_text())
        return int(match.group(1)) if match else None
    source = REPO_ROOT / f"apps/{service}/src/{service.replace('-', '_')}/settings.py"
    if not source.exists():
        return None
    match = re.search(r'default="127\.0\.0\.1:(\d+)",\s*alias="[A-Z_]+_BIND"', source.read_text())
    return int(match.group(1)) if match else None


@pytest.mark.parametrize("service", sorted(METRICS_UPSTREAMS))
def test_each_metrics_mapping_points_at_the_services_bind_port(service: str) -> None:
    _, forwards, _ = _parse_caddyfile()
    upstreams = {u for paths, u in forwards if f"/metrics/{service}" in paths}
    assert upstreams == {f"127.0.0.1:{METRICS_UPSTREAMS[service]}"}, upstreams

    if service == "node":
        return  # prometheus-node-exporter's fixed default, not a doorboard setting
    port = _bind_default(service)
    if port is None:
        pytest.skip(f"{service} has no *_BIND default yet (health endpoint pending)")
    assert port == METRICS_UPSTREAMS[service], f"{service} binds {port} but the proxy expects"


def test_the_visitor_and_nuc_routes_reach_door_api_on_its_bind_port() -> None:
    _, forwards, _ = _parse_caddyfile()
    door_api = {
        u
        for paths, u in forwards
        if paths & (PUBLIC_VISITOR_PATHS - {"/visitor", "/visitor/*", "/assets/*"})
    }
    door_api |= {u for paths, u in forwards if paths & NUC_MEDIA_PATHS}
    assert door_api == {f"127.0.0.1:{_bind_default('door-api')}"}, door_api


def _template_value(path: Path, key: str) -> str:
    match = re.search(rf"^{key}=(\S+)", path.read_text(), re.M)
    assert match, f"{path.name} does not set {key}"
    return match.group(1)


def _proxy_ports() -> set[int]:
    listen, _, _ = _parse_caddyfile()
    return {int(listen[1:])}


def test_the_lan_fallback_qr_points_at_the_proxy() -> None:
    # ADR-0047 §4(c): the LAN URL door-api mints must name a port some unit listens on.
    url = urlsplit(_template_value(PI_ENV_TEMPLATE, "DOOR_API_VISITOR_PUBLIC_BASE_URL"))
    assert url.scheme == "http" and url.hostname == "door-pi.local", url
    assert (url.port or 80) in _proxy_ports(), f"nothing on the Pi listens on {url.port}"


def test_the_nuc_pulls_media_through_the_proxy() -> None:
    url = urlsplit(_template_value(NUC_ENV_TEMPLATE, "CONTROL_PLANE_DOOR_API_BASE_URL"))
    assert url.hostname == "door-pi.local", url
    assert (url.port or 80) in _proxy_ports(), (
        f"{url.geturl()} names port {url.port}, but door-api binds loopback; the NUC reaches "
        "it only through the proxy"
    )


def test_every_door_service_still_binds_loopback_in_the_template() -> None:
    text = PI_ENV_TEMPLATE.read_text()
    binds = dict(re.findall(r"^(DOOR_[A-Z]+_BIND)=([^\s]+)", text, re.M))
    assert len(binds) >= 4, binds
    wide = {key: value for key, value in binds.items() if not value.startswith("127.0.0.1:")}
    assert not wide, f"ADR-0047 §1: every door service binds loopback; these do not: {wide}"


def test_the_proxy_unit_is_installed_and_off_the_critical_chain() -> None:
    unit = (UNIT_DIR / "door-proxy.service").read_text()
    assert "ExecStart=/usr/bin/caddy run --config /etc/doorboard/Caddyfile" in unit
    assert "After=network.target" in unit and "network-online" not in unit
    for name in ("door-api", "door-media", "door-visiond", "door-ui"):
        other = (UNIT_DIR / f"{name}.service").read_text()
        assert "door-proxy" not in other, f"{name} must not depend on the proxy"
    for installer in ("install-systemd", "install-home-layout"):
        text = (REPO_ROOT / "deploy/pi-door" / installer).read_text()
        assert "door-proxy.service" in text, f"{installer} does not enable the proxy"
        assert "/etc/doorboard/Caddyfile" in text, f"{installer} does not install the Caddyfile"
