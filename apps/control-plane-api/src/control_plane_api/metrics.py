"""In-process counters and gauges, rendered by `GET /metrics`.

A deliberately small registry rather than `prometheus_client`: the service has
a dozen series, `/metrics` must stay cheap (it used to `count(*)` the unbounded
`events` table on every scrape), and the shared `doorboard_observability`
package only ships latency histograms. Thread-safe — routes run on Starlette's
threadpool, MQTT callbacks on paho's network thread, and fan-out on its own
worker, all of which count.

Process-wide by design (like a Prometheus registry): the publisher and the
notifier count without needing a handle to `AppState`. Tests read deltas via
`counter()` or call `reset()`.
"""

from __future__ import annotations

import threading

_Labels = tuple[tuple[str, str], ...]

# Series names. Every one carries the service prefix so the NUC's Prometheus
# can scrape several services without collisions.
MQTT_CONNECTED = "control_plane_api_mqtt_connected"
MQTT_PUBLISH_FAILURES = "control_plane_api_mqtt_publish_failures_total"
INGEST_EVENTS = "control_plane_api_ingest_events_total"
NOTIFICATIONS = "control_plane_api_notifications_total"
FANOUT_DROPPED = "control_plane_api_fanout_dropped_total"
AUTH_FAILURES = "control_plane_api_auth_failures_total"
RATE_LIMITED = "control_plane_api_rate_limited_total"
ALERTS_RECEIVED = "control_plane_api_alerts_received_total"

_HELP: dict[str, tuple[str, str]] = {
    MQTT_CONNECTED: ("gauge", "1 while the MQTT broker link is up, 0 while it is down"),
    MQTT_PUBLISH_FAILURES: ("counter", "Publishes paho refused (non-zero rc), e.g. disconnected"),
    INGEST_EVENTS: ("counter", "Events received on /ingest by outcome (stored/duplicate/rejected)"),
    NOTIFICATIONS: ("counter", "Owner notifications by channel and outcome"),
    FANOUT_DROPPED: ("counter", "Fan-out jobs dropped because the background queue was full"),
    AUTH_FAILURES: ("counter", "Failed bearer authentications by route"),
    RATE_LIMITED: ("counter", "Requests refused with 429 by route"),
    ALERTS_RECEIVED: ("counter", "Alertmanager alerts accepted on /admin/alerts by status"),
}


def _key(labels: dict[str, str]) -> _Labels:
    return tuple(sorted(labels.items()))


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[_Labels, float]] = {}
        self._gauges: dict[str, dict[_Labels, float]] = {}

    def inc(self, name: str, *, by: float = 1.0, **labels: str) -> None:
        with self._lock:
            series = self._counters.setdefault(name, {})
            series[_key(labels)] = series.get(_key(labels), 0.0) + by

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self._gauges.setdefault(name, {})[_key(labels)] = float(value)

    def counter(self, name: str, **labels: str) -> float:
        with self._lock:
            return self._counters.get(name, {}).get(_key(labels), 0.0)

    def gauge(self, name: str, **labels: str) -> float | None:
        with self._lock:
            return self._gauges.get(name, {}).get(_key(labels))

    def reset(self) -> None:
        """Tests only."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()

    def render(self) -> list[str]:
        """Prometheus text-format lines (without a trailing newline)."""
        with self._lock:
            snapshot = {
                name: dict(series)
                for name, series in [*self._counters.items(), *self._gauges.items()]
            }
        lines: list[str] = []
        for name in sorted(snapshot):
            kind, help_text = _HELP.get(name, ("untyped", ""))
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            for labels, value in sorted(snapshot[name].items()):
                rendered = ",".join(f'{k}="{v}"' for k, v in labels)
                suffix = f"{{{rendered}}}" if rendered else ""
                lines.append(f"{name}{suffix} {value:g}")
            lines.append("")
        return lines


metrics = Metrics()
