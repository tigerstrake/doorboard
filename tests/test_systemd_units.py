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


_CRASH_LOOP_UNITS = re.compile(r"^(door-[a-z-]+|mediamtx)\.service$")


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
