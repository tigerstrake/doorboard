"""The monitoring stack must name metrics that exist and scrape ports that listen.

`ESP32Offline` queried `esp32_link_connected` for two milestones while door-api emitted
`door_api_esp32_link_connected`; `PiThrottling` queried a node_exporter nobody deployed; and
every door job targeted a Docker service name on the wrong host. None of it failed loudly — an
alert on a metric that does not exist simply never fires.

So: every metric name in `alert.rules.yml` and the Grafana dashboard must be emitted by some
exporter under `apps/**` (found by reading the source, never by running it), except the
Prometheus built-in `up` and the `node_*` / `alertmanager_*` families, which are allowed only
while `prometheus.yml` actually scrapes those jobs. And every scrape target must be a port the
service binds by default — for the door jobs, the proxy port plus a `/metrics/<service>` path
the Caddyfile maps onto that service's `*_BIND` default.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MONITORING = REPO_ROOT / "infra/monitoring"
RULES = MONITORING / "alert.rules.yml"
DASHBOARD = MONITORING / "grafana/dashboards/doorboard.json"
PROMETHEUS = MONITORING / "prometheus.yml"
CADDYFILE = REPO_ROOT / "deploy/pi-door/Caddyfile"
APPS = REPO_ROOT / "apps"

# Identifiers with at least one underscore that are not inside a quoted string. PromQL
# functions (`increase`, `humanize1024`) and label names (`chip`, `type`) have none.
_IDENT = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_QUOTED = re.compile(r'"[^"]*"|\'[^\']*\'')
# Families that come from something other than a doorboard service.
_EXTERNAL_FAMILIES = {"node_": "node", "alertmanager_": "alertmanager"}


def _metric_names(expr: str) -> set[str]:
    bare = _QUOTED.sub('""', expr)
    names = set(_IDENT.findall(bare))
    if re.search(r"\bup\b", bare):
        names.add("up")
    return names


def _rule_exprs() -> dict[str, str]:
    data = yaml.safe_load(RULES.read_text())
    return {rule["alert"]: rule["expr"] for group in data["groups"] for rule in group["rules"]}


def _dashboard_exprs() -> dict[str, str]:
    exprs: dict[str, str] = {}

    def walk(node: object, title: str) -> None:
        if isinstance(node, dict):
            title = str(node.get("title", title))
            if "expr" in node:
                exprs[f"{title}: {node['expr']}"] = str(node["expr"])
            for value in node.values():
                walk(value, title)
        elif isinstance(node, list):
            for value in node:
                walk(value, title)

    walk(json.loads(DASHBOARD.read_text()), "dashboard")
    return exprs


def _exported_names() -> set[str]:
    """Every metric a doorboard service can emit, read from the source.

    door-visiond builds names as ``door_visiond_<key>`` from its snapshot dict; the others
    write the full name either after ``# TYPE`` or as a dict key / f-string prefix.
    """
    names: set[str] = set()
    visiond_keys: set[str] = set()
    for path in APPS.glob("*/src/**/*.py"):
        text = path.read_text()
        names |= set(re.findall(r"# TYPE (\S+)", text))
        literals = set(re.findall(r'"([a-z][a-z0-9_]+)"', text))
        names |= literals
        if "door-visiond" in path.parts:
            visiond_keys |= literals
    names |= {f"door_visiond_{key}" for key in visiond_keys}
    return names


def _scrape_jobs() -> dict[str, dict]:
    data = yaml.safe_load(PROMETHEUS.read_text())
    return {job["job_name"]: job for job in data["scrape_configs"]}


def test_the_files_are_where_we_think() -> None:
    assert len(_rule_exprs()) >= 6
    assert len(_dashboard_exprs()) >= 6
    assert len(_exported_names()) > 50
    assert {"door-api", "door-visiond", "door-media", "door-sync"} <= set(_scrape_jobs())


def _check_names(label: str, expr: str) -> list[str]:
    exported = _exported_names()
    jobs = _scrape_jobs()
    problems: list[str] = []
    for name in sorted(_metric_names(expr)):
        if name == "up" or name in exported:
            continue
        family = next(
            (job for prefix, job in _EXTERNAL_FAMILIES.items() if name.startswith(prefix)), None
        )
        if family is not None:
            if family not in jobs:
                problems.append(f"{label}: {name} needs a `{family}` scrape job in prometheus.yml")
            continue
        problems.append(f"{label}: no exporter under apps/ emits {name}")
    return problems


@pytest.mark.parametrize("alert", sorted(_rule_exprs()))
def test_every_alert_rule_names_a_metric_something_emits(alert: str) -> None:
    problems = _check_names(alert, _rule_exprs()[alert])
    assert not problems, "\n".join(problems)


def test_every_dashboard_panel_names_a_metric_something_emits() -> None:
    problems = [p for label, expr in _dashboard_exprs().items() for p in _check_names(label, expr)]
    assert not problems, "\n".join(problems)


# --- scrape targets -------------------------------------------------------------


def _bind_port(service: str) -> int | None:
    if service == "door-api":
        source = APPS / "door-api/src/door_api/__main__.py"
        match = re.search(r'"DOOR_API_BIND",\s*"127\.0\.0\.1:(\d+)"', source.read_text())
        return int(match.group(1)) if match else None
    module = service.replace("-", "_")
    source = APPS / service / "src" / module / "settings.py"
    if not source.exists():
        return None
    match = re.search(
        r'default="(?:127\.0\.0\.1|0\.0\.0\.0):(\d+)",\s*alias="[A-Z_]+_BIND"', source.read_text()
    )
    return int(match.group(1)) if match else None


def _proxy_metrics_map() -> tuple[int, dict[str, int]]:
    """(proxy listen port, {/metrics/<name>: upstream port}) from the Caddyfile."""
    text = "\n".join(line.split("#", 1)[0] for line in CADDYFILE.read_text().splitlines())
    listen = re.search(r"^(:\d+)\s*\{", text, re.M)
    assert listen, "no site block in the Caddyfile"
    mapping: dict[str, int] = {}
    for path, body in re.findall(r"^\s*handle\s+(/metrics/\S+)\s*\{(.*?)^\s*\}", text, re.M | re.S):
        upstream = re.search(r"reverse_proxy\s+127\.0\.0\.1:(\d+)", body)
        assert upstream, f"{path} has no loopback upstream"
        mapping[path] = int(upstream.group(1))
    return int(listen.group(1)[1:]), mapping


def _targets(job: dict) -> list[str]:
    return [t for sc in job.get("static_configs", []) for t in sc.get("targets", [])]


@pytest.mark.parametrize("job_name", sorted(_scrape_jobs()))
def test_every_scrape_target_port_is_one_the_service_listens_on(job_name: str) -> None:
    job = _scrape_jobs()[job_name]
    targets = _targets(job)
    assert targets, f"{job_name} has no static targets"
    proxy_port, proxy_map = _proxy_metrics_map()

    if job_name in ("prometheus", "alertmanager"):
        assert all(t.endswith(":9090") or t.endswith(":9093") for t in targets), targets
        return
    if job_name == "control-plane-api":
        assert targets == [f"control-plane-api:{_bind_port('control-plane-api')}"], targets
        return

    # Door-side jobs go through the proxy: target = proxy port, path = /metrics/<service>.
    assert targets == [f"door-pi.local:{proxy_port}"], (
        f"{job_name}: door services bind loopback; scrape them through the proxy on "
        f":{proxy_port}, not {targets}"
    )
    path = job.get("metrics_path")
    assert path in proxy_map, f"{job_name}: metrics_path {path!r} is not mapped in the Caddyfile"
    if job_name == "node":
        return
    port = _bind_port(job_name)
    if port is None:
        pytest.skip(f"{job_name} has no *_BIND default yet")
    assert proxy_map[path] == port, (
        f"{job_name}: proxy forwards {path} to :{proxy_map[path]}, service binds :{port}"
    )


def test_the_alertmanager_receiver_is_the_control_plane_admin_alerts_route() -> None:
    config = yaml.safe_load((MONITORING / "alertmanager.yml").read_text())
    urls = [w["url"] for r in config["receivers"] for w in r.get("webhook_configs", [])]
    assert urls == ["http://control-plane-api:8090/admin/alerts"], urls
