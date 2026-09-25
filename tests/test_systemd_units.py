"""The door Pi's systemd units must keep the boot contract without a network.

`deploy/pi-door/README.md` promises "cold boot -> operational door with no network and no
NUC". The units are the only place that promise is either kept or broken, and it was broken
quietly: `doorboard-enrollment-unlock.service` waited on `network-online.target` (a minute of
DHCP retries after a power cut when the router is still down), `door-visiond` was ordered after
it, `door-api` after `door-visiond`, and both kiosks after `door-api`'s `/health`. Nothing
crashed, so nothing reported it; the screens were just dark for a minute after every outage.

These tests parse `infra/systemd/*.service` and pin the properties the runbooks rely on:

* nothing door-api transitively waits on may wait on the network;
* every door unit survives a crash loop (`StartLimitIntervalSec=0`), because systemd's
  default of five starts in ten seconds parks a unit in `failed` and the health agent used
  to treat `failed` as "stopped on purpose".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
UNIT_DIR = REPO_ROOT / "infra/systemd"


def _parse_unit(path: Path) -> dict[str, dict[str, list[str]]]:
    """Minimal unit-file parser: section -> key -> every value (keys may repeat)."""
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        assert current is not None, f"{path.name}: key outside a section: {line}"
        key, _, value = line.partition("=")
        current.setdefault(key.strip(), []).append(value.strip())
    return sections


def _units() -> dict[str, dict[str, dict[str, list[str]]]]:
    return {path.name: _parse_unit(path) for path in sorted(UNIT_DIR.glob("*.service"))}


def _words(unit: dict[str, dict[str, list[str]]], section: str, key: str) -> set[str]:
    return {word for value in unit.get(section, {}).get(key, []) for word in value.split()}


def test_the_unit_directory_is_where_we_think() -> None:
    names = set(_units())
    assert {"door-api.service", "door-visiond.service", "mediamtx.service"} <= names


def _waits_on(units: dict[str, dict[str, dict[str, list[str]]]], root: str) -> set[str]:
    """Every unit `root` is ordered after, transitively (After= here, Before= there)."""
    before_edges: dict[str, set[str]] = {}
    for name, unit in units.items():
        for target in _words(unit, "Unit", "Before"):
            before_edges.setdefault(target, set()).add(name)

    seen: set[str] = set()
    frontier = [root]
    while frontier:
        name = frontier.pop()
        unit = units.get(name)
        if unit is None:
            continue
        for dep in _words(unit, "Unit", "After") | before_edges.get(name, set()):
            if dep not in seen:
                seen.add(dep)
                frontier.append(dep)
    return seen


def test_nothing_on_the_door_api_chain_waits_for_the_network() -> None:
    units = _units()
    chain = _waits_on(units, "door-api.service")
    # Sanity: the chain is real, not an empty set that would pass forever.
    assert "doorboard-storage-ready.service" in chain

    offenders: list[str] = []
    for name in sorted(chain | {"door-api.service"}):
        unit = units.get(name)
        if unit is None:
            continue
        for key in ("After", "Wants", "Requires"):
            if "network-online.target" in _words(unit, "Unit", key):
                offenders.append(f"{name}: {key}=network-online.target")
    assert not offenders, (
        "a unit door-api waits on waits for network-online.target, so the kiosks wait for "
        "DHCP after a power cut:\n  " + "\n  ".join(offenders)
    )


def test_door_api_does_not_wait_for_visiond() -> None:
    # Recognition is a best-effort loopback hop into door-api (ARCHITECTURE §5); door-api
    # must come up, and the kiosks with it, whether or not visiond has finished starting.
    after = _words(_units()["door-api.service"], "Unit", "After")
    assert "door-visiond.service" not in after


_CRASH_LOOP_UNITS = re.compile(r"^(door-[a-z-]+|mediamtx|chromium-kiosk@)\.service$")


def test_every_door_unit_survives_a_crash_loop() -> None:
    units = _units()
    checked = [name for name in units if _CRASH_LOOP_UNITS.match(name)]
    assert len(checked) >= 6, f"expected the door units, found {checked}"

    missing: list[str] = []
    for name in checked:
        section = units[name].get("Unit", {})
        interval = section.get("StartLimitIntervalSec", [])
        burst = section.get("StartLimitBurst", [])
        if not (interval == ["0"] or burst):
            missing.append(name)
    assert not missing, (
        "no start-limit policy: after five failed starts in 10 s systemd parks the unit in "
        "`failed` and stops retrying — the door stays dark until someone logs in:\n  "
        + "\n  ".join(missing)
    )


def test_the_health_agent_restarts_a_failed_unit() -> None:
    # The counterpart of the start-limit policy: `try-restart` is a no-op on a failed unit,
    # so the agent must distinguish `failed` from `inactive` and start it.
    agent = (REPO_ROOT / "scripts/doorboard-health-agent").read_text()
    assert '"is-failed"' in agent
    assert '"reset-failed"' in agent


# --- installers ------------------------------------------------------------------

INSTALLERS = {
    name: REPO_ROOT / "deploy/pi-door" / name for name in ("install-systemd", "install-home-layout")
}


def _enable_list(installer: Path) -> set[str]:
    text = installer.read_text()
    match = re.search(r"systemctl enable \\\n((?:\s+\S+ \\\n)*\s+\S+\n)", text)
    assert match, f"{installer.name}: no `systemctl enable \\` block"
    return {line.strip().rstrip("\\").strip() for line in match.group(1).splitlines()}


@pytest.mark.parametrize("installer", sorted(INSTALLERS))
def test_every_timer_is_enabled_by_the_installer(installer: str) -> None:
    # doorboard-enrollment-unlock.timer is the documented cure for the cold-boot race
    # (enrollment-encrypted-volume.md), and neither installer enabled it: a rebuilt Pi
    # booted without the NUC stayed enrollment_locked until someone noticed.
    timers = {path.name for path in UNIT_DIR.glob("*.timer")}
    assert timers, "no timers under infra/systemd"
    enabled = _enable_list(INSTALLERS[installer])
    missing = sorted(timers - enabled)
    assert not missing, f"{installer} does not enable: {missing}"
    assert "door-voice.service" in enabled, f"{installer} does not enable door-voice"


def test_root_runs_only_root_owned_copies_of_its_scripts() -> None:
    units = _units()
    for name in ("doorboard-enrollment-unlock.service", "doorboard-health-agent.service"):
        exec_start = " ".join(units[name]["Service"]["ExecStart"])
        assert "/usr/local/lib/doorboard/" in exec_start, f"{name}: {exec_start}"
        assert "/opt/doorboard/scripts" not in exec_start, f"{name}: {exec_start}"
    for installer, path in INSTALLERS.items():
        text = path.read_text()
        for script in ("doorboard-enrollment-unlock", "doorboard-health-agent"):
            assert f'scripts/{script}" /usr/local/lib/doorboard/' in text or (
                f"scripts/{script} /usr/local/lib/doorboard/" in text
            ), f"{installer} does not install a root-owned copy of {script}"


def test_the_home_layout_no_longer_sources_dotenv_in_bash() -> None:
    text = INSTALLERS["install-home-layout"].read_text()
    assert "set -a; ." not in text, "bash-sourcing .env evaluates every value as shell"
    assert "render-systemd-env" in text
    assert "EnvironmentFile=$rendered_env" in text
    assert (
        "ReadWritePaths=$repo\nEnvironment=PYTHONUNBUFFERED=1\nEnvironmentFile=\nExecStart="
        not in text
    )


def test_the_kiosk_unit_has_no_ordering_cycle_and_speaks_wayland() -> None:
    unit = _units()["chromium-kiosk@.service"]
    after = _words(unit, "Unit", "After")
    wanted_by = _words(unit, "Install", "WantedBy")
    # A target implicitly gains After= on every unit it Wants=, so After=graphical.target
    # together with WantedBy=graphical.target is a cycle systemd resolves by dropping a job.
    assert "graphical.target" not in after
    assert "graphical.target" not in wanted_by
    assert "display-manager.service" in after

    exec_start = " ".join(unit["Service"]["ExecStart"])
    for flag in (
        "--ozone-platform=wayland",
        "--overscroll-history-navigation=0",
        "--disable-pinch",
        "--noerrdialogs",
        "--touch-events=enabled",
        "--user-data-dir=/mnt/ssd/doorboard/kiosk-%i",
    ):
        assert flag in exec_start, flag
    env = " ".join(unit["Service"].get("Environment", []))
    assert "WAYLAND_DISPLAY=" in env and "XDG_RUNTIME_DIR=" in env
    assert "DISPLAY=:0" not in env


def test_the_labwc_autostart_waits_for_door_api_before_each_kiosk() -> None:
    text = (REPO_ROOT / "deploy/pi-door/labwc-autostart.example").read_text()
    assert "wait-http" in text
    assert "http://127.0.0.1:8080/health" in text
    for kiosk in ("doorpad", "wallboard"):
        assert f"http://127.0.0.1:5173/{kiosk}" in text
    assert "--user-data-dir=" in text and "/mnt/ssd/doorboard" in text


# --- ADR-0007: nothing continuous writes to the microSD ------------------------------


def test_the_journal_bind_mount_is_early_boot_and_ssd_backed() -> None:
    unit = _parse_unit(UNIT_DIR / "var-log-journal.mount")
    assert unit["Mount"]["Where"] == ["/var/log/journal"]
    assert unit["Mount"]["What"] == ["/mnt/ssd/doorboard/journal"]
    assert "bind" in _words(unit, "Mount", "Options")
    # Ordered after the SSD's own mount, never after doorboard-storage-ready: that
    # service is After=sysinit.target while the journal flush runs before it, so the
    # obvious dependency is an ordering cycle systemd resolves by dropping a job.
    assert unit["Unit"]["RequiresMountsFor"] == ["/mnt/ssd/doorboard/journal"]
    assert "doorboard-storage-ready.service" not in _words(unit, "Unit", "After")
    assert unit["Unit"]["ConditionPathIsDirectory"] == ["/mnt/ssd/doorboard/journal"]


def test_journald_is_persistent_bounded_and_installed() -> None:
    conf = (REPO_ROOT / "deploy/pi-door/journald.conf.d/doorboard.conf").read_text()
    assert "Storage=persistent" in conf
    assert re.search(r"^SystemMaxUse=\d+M$", conf, re.M)
    for installer, path in INSTALLERS.items():
        text = path.read_text()
        assert "/etc/systemd/journald.conf.d/doorboard.conf" in text, installer
        assert "infra/systemd/*.mount" in text, installer
        assert "var-log-journal.mount" in _enable_list(path), installer


def test_hailort_logs_to_the_ssd() -> None:
    env = " ".join(_units()["door-visiond.service"]["Service"].get("Environment", []))
    assert "HAILORT_LOGGER_PATH=/mnt/ssd/doorboard/visiond" in env


def test_the_storage_check_never_creates_the_data_root_on_the_card() -> None:
    script = (REPO_ROOT / "scripts/check-door-storage").read_text()
    check = script.index("is on the root filesystem")
    mkdir = script.index("root.mkdir(")
    assert mkdir > check, "mkdir before the mount check creates SSD_DATA_ROOT on the microSD"
