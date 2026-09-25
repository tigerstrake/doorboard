"""Latency measurement harness — CLI runner.

Times the ARCHITECTURE.md §4 paths that can be exercised on a machine with no
door hardware — through the real door-api and door-media code, in-process — and
produces p50/p95/p99 tables. The paths that cannot be timed here are reported as
N/A by name rather than approximated. The shared logic (BUDGET_P95_MS,
SIMULATOR_NA, check_regressions, build_report, build_json) lives in
doorboard_observability.harness_core so tests can import it without path
manipulation.

Usage::

    # Run in-process (default):
    python -m tests.performance.harness

    # Save a new baseline (the per-stat median of three runs):
    python -m tests.performance.harness --save-baseline --runs 3

    # Specify output paths:
    python -m tests.performance.harness \\
        --json-out /tmp/latency.json \\
        --report-out /tmp/latency.txt

    # Hardware mode (real bench devices via T-101/T-102 adapters):
    python -m tests.performance.harness --mode hardware
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import secrets
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from doorboard_contracts import DoorboardEvent, parse_event
from doorboard_esp32_link import Esp32TransportStatus, WireMessage
from doorboard_esp32_link.esp32 import uuid7_now
from doorboard_observability.harness_core import (
    BUDGET_P95_MS,
    build_json,
    build_report,
    check_budgets,
    check_regressions,
    load_baseline,
    median_baseline,
    write_baseline,
)

# The committed baseline lives alongside this runner.
BASELINE_PATH = Path(__file__).parent / "baseline.json"

# 50 iterations give the nearest-rank p95 the 48th-ordered value —
# statistically meaningful without being slow in CI.
ITERATIONS = 50


# ---------------------------------------------------------------------------
# bell_to_visitor_mode: door-api, in-process
# ---------------------------------------------------------------------------


class _QueueEsp32Transport:
    """The controller link reduced to its inbound queue.

    door-api's consumer task (``DoorApiState._consume_esp32_events``) iterates
    ``events()`` exactly as it does over the UART transport. The harness feeds the
    queue from the calling thread, so the timed window starts where a decoded frame
    enters that same queue on the Pi. Outbound traffic (effects, profile pushes) is
    not part of this path and is refused loudly rather than swallowed.
    """

    def __init__(self) -> None:
        self._inbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._consuming = threading.Event()

    def events(self) -> AsyncIterator[DoorboardEvent]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[DoorboardEvent]:
        self._loop = asyncio.get_running_loop()
        self._consuming.set()
        while True:
            # Contract validation happens here, inside the consumer, as it does in
            # Esp32ProtocolTransport.events() — it is part of the measured path.
            yield parse_event(await self._inbound.get())

    def push(self, wire: dict[str, Any]) -> None:
        """Hand a decoded frame to the consumer from any thread."""
        if not self._consuming.wait(timeout=5.0) or self._loop is None:
            raise RuntimeError("door-api never started its ESP32 event consumer")
        self._loop.call_soon_threadsafe(self._inbound.put_nowait, wire)

    def status(self) -> Esp32TransportStatus:
        return Esp32TransportStatus(
            connected=True, last_heartbeat_mono_ms=None, rx_errors=0, tx_retries=0
        )

    def make_message(self, message_type: str, payload: Mapping[str, object]) -> WireMessage:
        raise NotImplementedError("the harness link is inbound-only")

    async def send(self, msg: WireMessage) -> WireMessage:
        raise NotImplementedError("the harness link is inbound-only")

    async def send_event(self, event: DoorboardEvent) -> WireMessage:
        raise NotImplementedError("the harness link is inbound-only")


def _button_pressed_wire() -> dict[str, Any]:
    """A ``door.button_pressed`` envelope as the ESP32 link would decode it."""
    return {
        "event_id": str(uuid7_now()),
        "type": "door.button_pressed",
        "source": "esp32-link",
        "occurred_at": datetime.now(UTC).isoformat(),
        "monotonic_ms": time.monotonic_ns() // 1_000_000,
        "door_id": "primary",
        "trace_id": str(uuid4()),
        "payload": {"press_id": str(uuid4()), "had_cached_profile": False, "profile_id": None},
    }


@contextlib.contextmanager
def _environ(overrides: Mapping[str, str]) -> Iterator[None]:
    saved = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _receive_state_changed(websocket: Any, *, to_state: str) -> dict[str, Any]:
    """Read /ws frames until the ``session.state_changed`` delta into ``to_state``."""
    while True:
        frame = json.loads(websocket.receive_text())
        if frame.get("type") != "delta":
            continue
        event = frame["event"]
        if event["type"] == "session.state_changed" and event["payload"]["to_state"] == to_state:
            return event


def measure_bell_to_visitor_mode(iterations: int, data_dir: Path) -> list[float]:
    """Time ``door.button_pressed`` → ``session.state_changed(VISITOR_MODE)`` on ``/ws``.

    What runs: door-api's real FastAPI app under Starlette's TestClient with its
    lifespan, the real ``SessionMachine`` over the real SQLite ``SessionStore`` on disk
    in ``data_dir`` (WAL, ``synchronous=FULL`` — the Pi's settings), the real ESP32
    event-consumer task reading a queue-backed link, and the real ``/ws`` endpoint.
    The clock starts when the decoded button frame is handed to the consumer's queue
    and stops when the ``VISITOR_MODE`` delta has been read from the socket.

    Not on the path: the UART itself, and uvicorn's WebSocket framing — TestClient's
    in-memory socket stands in for it. The harness's two thread hops (inject, read)
    are overhead the Pi does not pay, so the number here is a slight over-estimate.

    Every iteration is a fresh session: it is ended through ``POST /admin/session/end``
    and the next press waits for the ``IDLE`` delta. ``SESSION_END → IDLE`` normally
    lingers three seconds; that is zeroed here so fifty sessions take well under a
    minute, and it is outside the timed window either way.
    """
    from fastapi.testclient import TestClient

    db_path = str(data_dir / "session.sqlite")
    admin_token = secrets.token_urlsafe(16)
    overrides = {
        "DOOR_API_DB_PATH": db_path,
        "DOOR_API_SOCIAL_DB_PATH": str(data_dir / "social.sqlite"),
        "DOOR_API_SOCIAL_ADMIN_TOKEN": admin_token,
        "DOOR_API_SESSION_END_LINGER_S": "0",
        # Nothing listens here: the media/sync forwarders fail fast and back off
        # instead of reaching a real door-media or door-sync on the dev box.
        "DOOR_API_MEDIA_BASE_URL": "http://127.0.0.1:9",
        "DOOR_API_SYNC_BASE_URL": "http://127.0.0.1:9",
        "ESP32_TRANSPORT": "mock",
        "DOOR_API_MQTT_URL": "",
        "DOOR_API_VISITOR_RELAY_BASE_URL": "",
    }
    samples: list[float] = []
    with _environ(overrides):
        from door_api.app import app, state

        # The module-level state is built at import time. In a fresh process that
        # already happened under this environment; under pytest it may predate it.
        if state.config.db_path != db_path:
            state.shutdown()
            state.__init__()
        link = _QueueEsp32Transport()
        # No loop is running yet, so this only records the link; the lifespan's
        # startup() creates the consumer task on the app's loop.
        state.attach_esp32_transport(link)

        with TestClient(app) as client, client.websocket_connect("/ws") as websocket:
            headers = {"Authorization": f"Bearer {admin_token}"}
            for _ in range(iterations):
                t0 = time.perf_counter()
                link.push(_button_pressed_wire())
                _receive_state_changed(websocket, to_state="VISITOR_MODE")
                samples.append((time.perf_counter() - t0) * 1000.0)

                ended = client.post("/admin/session/end", headers=headers)
                if ended.status_code != 200 or not ended.json()["accepted"]:
                    raise RuntimeError(f"could not end the session: {ended.text}")
                _receive_state_changed(websocket, to_state="IDLE")
    return samples


# ---------------------------------------------------------------------------
# bell_to_recording_event: door-media, in-process
# ---------------------------------------------------------------------------


def _mediamtx_api(cfg: Any) -> Callable[[httpx.Request], httpx.Response]:
    """A MediaMTX control API whose visitor path is live — the §4 precondition."""
    live_path = f"/v3/paths/get/{cfg.visitor_cam_stream}"

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == live_path:
            return httpx.Response(
                200,
                json={"name": cfg.visitor_cam_stream, "ready": True, "readers": []},
            )
        return httpx.Response(404, json={"error": "path not found"})

    return handle


def _button_pressed_state_change(session_id: UUID) -> dict[str, Any]:
    """The ``session.state_changed`` door-api forwards to door-media on a press."""
    return {
        "event_id": str(uuid7_now()),
        "type": "session.state_changed",
        "source": "door-api",
        "occurred_at": datetime.now(UTC).isoformat(),
        "monotonic_ms": time.monotonic_ns() // 1_000_000,
        "door_id": "primary",
        "trace_id": str(uuid4()),
        "payload": {
            "session_id": str(session_id),
            "from_state": "IDLE",
            "to_state": "BUTTON_PRESSED",
            "trigger": "door.button_pressed",
        },
    }


async def measure_bell_to_recording_event(iterations: int, data_dir: Path) -> list[float]:
    """Time the session event reaching door-media → ``media.recording_started`` emitted.

    What runs: door-media's real FastAPI app on an in-loop ASGI transport (so the
    route, the admin-token dependency and the Pydantic body validation are all on
    the path), the real ``MediaMTXRouter`` with its MediaMTX control API mocked to
    report the visitor stream live, the real ``RecordingDB`` on disk in ``data_dir``
    (WAL, ``synchronous=FULL``), and the real ``RecordingService``. The clock starts
    as ``POST /internal/session_event`` is issued with the ``BUTTON_PRESSED``
    transition door-api forwards on a press, and stops when the response is back —
    by which point ``media.recording_started`` has been put on the broadcast queue
    (asserted) and the processed-event mark committed after it in the same handler.

    Not on the path: uvicorn and the loopback HTTP hop door-api's forwarder pays
    (ASGITransport calls the app directly), and MediaMTX itself — it writes segments
    continuously and ``start_recording`` only opens a window over them, so nothing
    is asked of it here. Audio capture is off, as it is by default.

    Each window is discarded after its sample so the active-recording cap never
    turns a later press into a refused one.
    """
    from door_media.app import app
    from door_media.db import RecordingDB
    from door_media.emitter import subscribe_broadcast_queue, unsubscribe_broadcast_queue
    from door_media.mediamtx_router import MediaMTXRouter
    from door_media.service import RecordingService
    from door_media.settings import Settings

    admin_token = secrets.token_urlsafe(16)
    cfg = Settings(
        **{
            "MEDIA_MODE": "mediamtx",
            "SSD_DATA_ROOT": str(data_dir),
            "DOOR_MEDIA_MIN_FREE_BYTES": "1",
            "DOOR_MEDIA_ADMIN_TOKEN": admin_token,
            "DOOR_API_INTERNAL_EVENT_TOKEN": "",
            "MEDIA_AUDIO_ENABLED": "false",
            "MEDIAMTX_API": "http://mediamtx.harness.invalid",
        }
    )
    router = MediaMTXRouter(cfg)
    # The router's control-API client is the only thing that would reach a real
    # MediaMTX; swap it for the mock before anything is asked of it.
    await router._http.aclose()
    router._http = httpx.AsyncClient(
        base_url=cfg.mediamtx_api, transport=httpx.MockTransport(_mediamtx_api(cfg))
    )
    if not await router.health_check():
        raise RuntimeError("the mocked MediaMTX API did not report the visitor stream live")

    db = RecordingDB(cfg.ssd_data_root / "door_media.db")
    service = RecordingService(router=router, db=db, settings=cfg)
    # What _lifespan installs, minus router.start(): that spawns the MediaMTX
    # binary and the camera pipeline, neither of which this path touches.
    app.state.cfg = cfg
    app.state.router = router
    app.state.db = db
    app.state.service = service

    emitted = subscribe_broadcast_queue()
    samples: list[float] = []
    await service.start()
    try:
        headers = {"Authorization": f"Bearer {admin_token}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://door-media.local"
        ) as client:
            for _ in range(iterations):
                session_id = uuid4()
                body = {"event": _button_pressed_state_change(session_id)}

                t0 = time.perf_counter()
                response = await client.post("/internal/session_event", json=body, headers=headers)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0

                if response.status_code != 200 or response.json().get("duplicate"):
                    raise RuntimeError(f"session event not accepted: {response.text}")
                started = _drain_recording_started(emitted)
                if started is None or started.payload.session_id != session_id:
                    raise RuntimeError("media.recording_started was not emitted for the press")
                samples.append(elapsed_ms)

                await service.discard_recording(
                    started.payload.recording_id, trace_id=started.trace_id
                )
    finally:
        await service.stop()
        unsubscribe_broadcast_queue(emitted)
        await router._http.aclose()
        db.close()
    return samples


def _drain_recording_started(queue: asyncio.Queue[Any]) -> Any | None:
    """The ``media.recording_started`` event now on the queue, skipping telemetry."""
    while True:
        try:
            event = queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
        if event.type == "media.recording_started":
            return event


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def collect_samples(iterations: int = ITERATIONS) -> dict[str, list[float]]:
    """Measure every path the harness can, returning path name → millisecond samples.

    The paths in ``SIMULATOR_NA`` get no samples on purpose: the report labels them
    N/A by name, with the reason, instead of timing a stand-in and calling it a pass.
    """
    samples: dict[str, list[float]] = {path: [] for path in BUDGET_P95_MS}

    with tempfile.TemporaryDirectory(prefix="doorboard-harness-") as tmpdir:
        root = Path(tmpdir)
        samples["bell_to_visitor_mode"] = measure_bell_to_visitor_mode(
            iterations, root / "door-api"
        )
        samples["bell_to_recording_event"] = asyncio.run(
            measure_bell_to_recording_event(iterations, root / "door-media")
        )

    return samples


# ---------------------------------------------------------------------------
# Hardware adapter stub
# ---------------------------------------------------------------------------


def run_hardware() -> dict[str, list[float]]:
    """Hardware-mode runner stub.

    In hardware mode the bench operator connects real devices and drives
    them via the adapter interfaces (T-101/T-102).  The full procedure is
    documented in tests/hardware-in-loop/T-104-latency-harness.md.

    This stub raises NotImplementedError to prevent accidental CI use.
    """
    msg = (
        "Hardware mode is not yet implemented. "
        "See tests/hardware-in-loop/T-104-latency-harness.md for the bench procedure."
    )
    raise NotImplementedError(msg)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _main(args: argparse.Namespace) -> int:
    collect: Callable[[], dict[str, list[float]]] = (
        run_hardware if args.mode == "hardware" else collect_samples
    )
    runs = [collect() for _ in range(args.runs)]
    # The gate judges the last run; extra runs only feed the baseline's median.
    samples = runs[-1]

    baseline = load_baseline(BASELINE_PATH)
    if baseline is None and BASELINE_PATH.exists():
        print(f"Warning: could not load baseline from {BASELINE_PATH}", file=sys.stderr)

    regressions = check_regressions(samples, baseline) if baseline else []
    budget_failures = check_budgets(samples)

    report_json = build_json(samples, regressions, budget_failures=budget_failures)
    report_text = build_report(
        samples, baseline=baseline, regressions=regressions, budget_failures=budget_failures
    )
    report_json["measurement_mode"] = args.mode
    report_json["hardware_acceptance"] = args.mode == "hardware"
    if args.mode == "simulator":
        report_text = (
            "IN-PROCESS CODE-PATH REGRESSION REPORT\n"
            "Real door-api and door-media code on this machine, not the door: "
            "not physical latency, not hardware acceptance.\n\n" + report_text
        )

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report_json, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"JSON report written to {out}", file=sys.stderr)

    if args.report_out:
        out = Path(args.report_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report_text, encoding="utf-8")
        print(f"Text report written to {out}", file=sys.stderr)

    print(report_text)

    if args.save_baseline:
        write_baseline(BASELINE_PATH, median_baseline([build_json(run, []) for run in runs]))
        print(f"Baseline saved to {BASELINE_PATH} ({len(runs)} run(s))", file=sys.stderr)

    return 1 if regressions or budget_failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Doorboard latency harness — p50/p95/p99 tables per ARCHITECTURE.md §4 path."
    )
    parser.add_argument(
        "--mode",
        choices=["simulator", "hardware"],
        default="simulator",
        help="Run in-process on this machine (default) or against the real hardware bench.",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="Overwrite baseline.json with this run (or the per-stat median of --runs).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        metavar="N",
        help="Collect N times; the gate judges the last run, the baseline takes the median.",
    )
    parser.add_argument("--json-out", metavar="PATH", default=None)
    parser.add_argument("--report-out", metavar="PATH", default=None)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    sys.exit(_main(args))


if __name__ == "__main__":
    main()
