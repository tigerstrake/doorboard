"""The rendered MediaMTX config opens nothing off-host.

MediaMTX fills every key the generated file omits from its own defaults, and those
defaults listen on all interfaces: SRT on :8890 with an anonymous any-IP ``read``, WebRTC
ICE on :8189, RTP/RTCP on :8000-8001. The template claimed "all ports bound to
127.0.0.1" and that was true only of the keys it happened to list, so
``ffplay "srt://door-pi.local:8890?streamid=read:visitor"`` from the corridor played the
live door camera with no token and no log line.

These tests parse the *rendered* file with PyYAML and check it against a checked-in copy
of the pinned release's default ``mediamtx.yml`` (``fixtures/``), so a version bump that
introduces a listener fails here rather than opening a port. Refresh the fixture from
``https://raw.githubusercontent.com/bluenviron/mediamtx/v<version>/mediamtx.yml`` when
``MEDIAMTX_VERSION`` moves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from door_media.mediamtx_router import MediaMTXRouter
from door_media.settings import Settings

_FIXTURES = Path(__file__).parent / "fixtures"

# Every protocol toggle MediaMTX 1.9.3 has, and the listener addresses it governs. Spelled
# out rather than derived from key prefixes because rtpAddress/rtcpAddress belong to rtsp.
_LISTENERS: dict[str, tuple[str, ...]] = {
    "rtsp": ("rtspAddress", "rtspsAddress", "rtpAddress", "rtcpAddress"),
    "rtmp": ("rtmpAddress", "rtmpsAddress"),
    "hls": ("hlsAddress",),
    "webrtc": ("webrtcAddress", "webrtcLocalUDPAddress", "webrtcLocalTCPAddress"),
    "srt": ("srtAddress",),
    "api": ("apiAddress",),
    "metrics": ("metricsAddress",),
    "pprof": ("pprofAddress",),
    "playback": ("playbackAddress",),
}
# Ends in "Address" but is a URL MediaMTX *calls* (external auth), not a socket it opens.
_NOT_A_LISTENER = {"authHTTPAddress"}

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "[::1]"}


def _defaults(cfg: Settings) -> dict[str, Any]:
    path = _FIXTURES / f"mediamtx-{cfg.mediamtx_version}-defaults.yml"
    assert path.exists(), f"no pinned defaults for MediaMTX {cfg.mediamtx_version}: {path}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _rendered(tmp_path: Path) -> tuple[Settings, dict[str, Any]]:
    cfg = Settings(SSD_DATA_ROOT=tmp_path, MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml")
    MediaMTXRouter(cfg)._write_config()
    return cfg, yaml.safe_load(cfg.mediamtx_config_path.read_text(encoding="utf-8"))


def _is_loopback_or_closed(address: object) -> bool:
    """``''`` is MediaMTX for "no listener"; otherwise the host half must be loopback."""
    if address == "":
        return True
    if not isinstance(address, str):
        return False
    host, sep, port = address.rpartition(":")
    return bool(sep) and port.isdigit() and host in _LOOPBACK_HOSTS


def test_the_defaults_really_do_listen_on_the_lan(tmp_path: Path) -> None:
    """Pin that the fixture is the dangerous upstream file, not a sanitised copy."""
    cfg, _ = _rendered(tmp_path)
    defaults = _defaults(cfg)

    assert defaults["srt"] is True
    assert defaults["srtAddress"] == ":8890"
    assert defaults["webrtcLocalUDPAddress"] == ":8189"
    any_user = defaults["authInternalUsers"][0]
    assert any_user["user"] == "any"
    assert any_user["ips"] == []
    assert {p["action"] for p in any_user["permissions"]} >= {"read", "publish"}


def test_every_listener_the_release_has_is_known_to_this_test(tmp_path: Path) -> None:
    """A MediaMTX upgrade that adds a listener must fail here, not open a port."""
    cfg, _ = _rendered(tmp_path)
    defaults = _defaults(cfg)

    address_keys = {k for k in defaults if k.endswith("Address")} - _NOT_A_LISTENER
    covered = {addr for addrs in _LISTENERS.values() for addr in addrs}
    assert address_keys == covered, (
        f"unaccounted listener keys in the defaults: {sorted(address_keys ^ covered)}"
    )
    assert set(_LISTENERS) <= set(defaults)


def test_every_protocol_is_explicit_and_either_off_or_loopback(tmp_path: Path) -> None:
    _, rendered = _rendered(tmp_path)

    for toggle, addresses in _LISTENERS.items():
        # Explicit, never inherited: the inherited default is "on, all interfaces".
        assert toggle in rendered, f"{toggle} is inherited from the MediaMTX defaults"
        assert isinstance(rendered[toggle], bool), f"{toggle} must be a yes/no"
        for address in addresses:
            assert address in rendered, f"{address} is inherited from the MediaMTX defaults"
            assert _is_loopback_or_closed(rendered[address]), f"{address}={rendered[address]!r}"

    # Belt and braces: nothing in the file that names a socket points off-host.
    for key, value in rendered.items():
        if key.endswith("Address") and key not in _NOT_A_LISTENER:
            assert _is_loopback_or_closed(value), f"{key}={value!r}"


def test_only_what_the_door_uses_is_enabled(tmp_path: Path) -> None:
    _, rendered = _rendered(tmp_path)

    enabled = {toggle for toggle in _LISTENERS if rendered[toggle]}
    assert enabled == {"rtsp", "webrtc", "api"}
    # RTSP over TCP only: no UDP RTP/RTCP sockets, no multicast, no RTSPS listener.
    assert rendered["protocols"] == ["tcp"]
    assert rendered["encryption"] == "no"
    assert rendered["rtmpEncryption"] == "no"


def test_reads_and_publishes_are_granted_to_loopback_only(tmp_path: Path) -> None:
    """The default `any` user with `ips: []` is an anonymous any-IP read of the camera."""
    _, rendered = _rendered(tmp_path)

    assert rendered["authMethod"] == "internal"
    users = rendered["authInternalUsers"]
    assert users, "no authInternalUsers means MediaMTX's any-IP default user"
    for user in users:
        actions = {p["action"] for p in user["permissions"]}
        if actions & {"read", "publish", "playback"}:
            assert user["ips"], f"user {user['user']!r} may read/publish from any IP"
            assert set(user["ips"]) <= {"127.0.0.1", "::1"}, user["ips"]


def test_webrtc_advertises_only_the_loopback_host(tmp_path: Path) -> None:
    """A loopback-bound ICE socket with LAN candidates advertised is unreachable from the
    kiosk and reachable from nowhere; the candidate list has to match the bind."""
    _, rendered = _rendered(tmp_path)

    assert rendered["webrtcIPsFromInterfaces"] is False
    assert rendered["webrtcAdditionalHosts"] == ["127.0.0.1"]


def test_rendered_keys_all_exist_in_the_pinned_release(tmp_path: Path) -> None:
    """MediaMTX rejects unknown fields at startup, so a key the release does not know (a
    typo, or a setting from a newer version) is a crash-loop on the door, not a no-op."""
    cfg, rendered = _rendered(tmp_path)
    defaults = _defaults(cfg)

    unknown = set(rendered) - set(defaults)
    assert not unknown, f"keys MediaMTX {cfg.mediamtx_version} does not know: {sorted(unknown)}"
    path_keys = set(rendered["paths"][cfg.visitor_cam_stream])
    unknown_path_keys = path_keys - set(defaults["pathDefaults"])
    assert not unknown_path_keys, sorted(unknown_path_keys)
