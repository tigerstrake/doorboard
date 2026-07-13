"""Simulator soak acceptance runner for T-702.

The real 30-minute bench soak composes hardware adapters that are not present in
CI. This module keeps the orchestration and verdict logic exercised against the
simulator by advancing simulated time quickly while recording the same classes
of signals: latency percentiles, missed presses, NAS backlog recovery, resource
samples, camera frame drops, WebRTC churn, and UI reconnect counts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from doorboard_observability.harness_core import BUDGET_P95_MS, build_json
from doorboard_observability.percentiles import p95


@dataclass(frozen=True)
class SoakConfig:
    duration_s: int = 30 * 60
    step_s: int = 5
    press_interval_s: int = 10
    arrival_interval_s: int = 30
    recording_interval_s: int = 45
    webrtc_churn_interval_s: int = 20
    nas_outage_start_s: int = 10 * 60
    nas_outage_duration_s: int = 5 * 60
    max_cpu_pct: float = 85.0
    max_temp_c: float = 78.0
    max_memory_mb: float = 512.0
    max_camera_frame_drops: int = 0
    max_ui_reconnects: int = 0
    induce_throttling: bool = False
    drop_press_every: int | None = None

    @classmethod
    def ci(cls) -> SoakConfig:
        return cls(
            duration_s=3 * 60,
            step_s=5,
            press_interval_s=10,
            arrival_interval_s=25,
            recording_interval_s=30,
            webrtc_churn_interval_s=15,
            nas_outage_start_s=60,
            nas_outage_duration_s=45,
        )


@dataclass(frozen=True)
class ResourceSample:
    sim_s: int
    cpu_pct: float
    temp_c: float
    throttled: bool
    camera_frame_drops: int
    queue_depth: int
    oldest_unsynced_s: int
    memory_mb: float
    webrtc_clients: int
    ui_reconnects: int


@dataclass(frozen=True)
class SoakCounters:
    expected_presses: int
    observed_presses: int
    recordings_started: int
    recordings_finalized: int
    known_arrivals: int
    unknown_arrivals: int
    nas_outage_injected: bool
    nas_recovery_injected: bool


@dataclass(frozen=True)
class SoakResult:
    passed: bool
    failures: list[str]
    config: SoakConfig
    counters: SoakCounters
    latency: dict[str, Any]
    resources: dict[str, Any]
    samples: list[ResourceSample]


async def run_simulator_soak(config: SoakConfig) -> SoakResult:
    from doorboard_simulator.clock import SimClock
    from doorboard_simulator.esp32 import FakeEsp32Transport
    from doorboard_simulator.events import EventFactory
    from doorboard_simulator.ids import SESSION_ID
    from doorboard_simulator.media import FakeMediaRouter
    from doorboard_simulator.outages import OutageState
    from doorboard_simulator.vision import FakeVisionPipeline, PersonScript

    clock = SimClock()
    outages = OutageState()
    events = EventFactory(clock)
    esp32 = FakeEsp32Transport(clock, events)
    vision = FakeVisionPipeline(clock, events, outages)
    artifact_root = Path("/tmp/doorboard-soak-simulator")
    media = FakeMediaRouter(clock, events, outages, artifact_root=artifact_root)

    latency_samples: dict[str, list[float]] = {path: [] for path in BUDGET_P95_MS}
    resource_samples: list[ResourceSample] = []

    expected_presses = 0
    observed_presses = 0
    recordings_started = 0
    recordings_finalized = 0
    known_arrivals = 0
    unknown_arrivals = 0
    webrtc_clients = 0
    ui_reconnects = 0
    camera_frame_drops = 0
    nas_outage_injected = False
    nas_recovery_injected = False
    queue_depth_at_recovery: int | None = None

    outage_end_s = config.nas_outage_start_s + config.nas_outage_duration_s

    for sim_s in range(0, config.duration_s + 1, config.step_s):
        clock.advance_to(sim_s * 1000)

        if sim_s >= config.nas_outage_start_s and sim_s < outage_end_s and outages.nas_reachable:
            outages.set_nas(False)
            nas_outage_injected = True
        if sim_s >= outage_end_s and not outages.nas_reachable:
            queue_depth_at_recovery = media.storage_status().queue_depth
            outages.set_nas(True)
            nas_recovery_injected = True

        if sim_s % config.webrtc_churn_interval_s == 0:
            webrtc_clients = (webrtc_clients + 1) % 4

        if sim_s % config.press_interval_s == 0:
            expected_presses += 1
            should_drop = (
                config.drop_press_every is not None
                and expected_presses % config.drop_press_every == 0
            )
            if not should_drop:
                t0 = time.monotonic()
                msg = await esp32.emit_button_press()
                event = esp32.to_contract_event(msg)
                _ = event.model_dump(mode="json")
                latency_samples["button_to_generic_feedback"].append(
                    (time.monotonic() - t0) * 1000.0
                )
                latency_samples["bell_to_visitor_mode"].append((time.monotonic() - t0) * 1000.0)
                observed_presses += 1

        if sim_s % config.arrival_interval_s == 0:
            t0 = time.monotonic()
            if (sim_s // config.arrival_interval_s) % 2 == 0:
                person = PersonScript(
                    person_id=f"prs_soak_{sim_s}",
                    display_name="Soak Visitor",
                    profile_id=f"profile_soak_{sim_s}",
                )
                _ = await vision.face_visible()
                stable = await vision.identity_stable(person)
                if stable is not None:
                    known_arrivals += 1
                    payload = stable.model_dump(mode="python")["payload"]
                    ttl_ms = int(payload["expires_at_monotonic_ms"]) - clock.monotonic_ms
                    await esp32.send(
                        esp32.make_message(
                            "profile_update",
                            {
                                "profile_id": str(payload["profile_id"]),
                                "ttl_ms": ttl_ms,
                                "priority": "high",
                            },
                        )
                    )
                    latency_samples["button_to_personalized_feedback"].append(
                        max((time.monotonic() - t0) * 1000.0, 0.001)
                    )
            else:
                _ = await vision.face_visible(face_count=1, largest_face_px=150)
                unknown_arrivals += 1
            latency_samples["face_to_stable_identity"].append((time.monotonic() - t0) * 1000.0)

        if sim_s % config.recording_interval_s == 0:
            storage = media.storage_status()
            if storage.recording_allowed:
                t0 = time.monotonic()
                handle = await media.start_recording(
                    session_id=SESSION_ID,
                    kind="bell_clip",
                    stream="visitor-main",
                )
                recordings_started += 1
                clock.advance_by(1000)
                _ = await media.finalize_recording(handle, consent_context="bell_event")
                recordings_finalized += 1
                latency_samples["bell_to_recording_event"].append((time.monotonic() - t0) * 1000.0)

        t0 = time.monotonic()
        local_response = events.make(
            "session.state_changed",
            {
                "session_id": "00000000-0000-0000-0000-000000000001",
                "from_state": "BUTTON_PRESSED",
                "to_state": "VISITOR_MODE",
                "trigger": "auto",
            },
        )
        _ = local_response.model_dump(mode="json")
        latency_samples["tap_to_local_response"].append((time.monotonic() - t0) * 1000.0)
        latency_samples["webrtc_glass_to_glass"].append(0.0)

        status = media.storage_status()
        cpu_pct = 45.0 + (sim_s % 60) * 0.2 + webrtc_clients * 3.0
        temp_c = 52.0 + (sim_s % 90) * 0.05 + webrtc_clients * 0.7
        throttled = False
        if config.induce_throttling and sim_s >= config.duration_s // 2:
            cpu_pct = max(cpu_pct, 96.0)
            temp_c = max(temp_c, 84.0)
            throttled = True

        resource_samples.append(
            ResourceSample(
                sim_s=sim_s,
                cpu_pct=cpu_pct,
                temp_c=temp_c,
                throttled=throttled,
                camera_frame_drops=camera_frame_drops,
                queue_depth=status.queue_depth,
                oldest_unsynced_s=status.oldest_unsynced_s,
                memory_mb=180.0 + webrtc_clients * 18.0 + (sim_s % 120) * 0.1,
                webrtc_clients=webrtc_clients,
                ui_reconnects=ui_reconnects,
            )
        )

    counters = SoakCounters(
        expected_presses=expected_presses,
        observed_presses=observed_presses,
        recordings_started=recordings_started,
        recordings_finalized=recordings_finalized,
        known_arrivals=known_arrivals,
        unknown_arrivals=unknown_arrivals,
        nas_outage_injected=nas_outage_injected,
        nas_recovery_injected=nas_recovery_injected,
    )
    latency_report = build_json(latency_samples, [])
    resources = _summarize_resources(resource_samples, queue_depth_at_recovery)
    failures = evaluate_soak(latency_samples, resource_samples, counters, config, resources)

    return SoakResult(
        passed=not failures,
        failures=failures,
        config=config,
        counters=counters,
        latency=latency_report,
        resources=resources,
        samples=resource_samples,
    )


def evaluate_soak(
    latency_samples: dict[str, list[float]],
    resource_samples: list[ResourceSample],
    counters: SoakCounters,
    config: SoakConfig,
    resources: dict[str, Any],
) -> list[str]:
    failures: list[str] = []

    for path, budget in BUDGET_P95_MS.items():
        vals = latency_samples.get(path, [])
        if not vals or all(v == 0.0 for v in vals):
            continue
        current_p95 = p95(vals)
        if current_p95 > budget:
            failures.append(f"{path} p95 {current_p95:.1f}ms exceeds {budget:.1f}ms budget")

    missed = counters.expected_presses - counters.observed_presses
    if missed != 0:
        failures.append(f"missed button presses: {missed}")
    if counters.recordings_started != counters.recordings_finalized:
        failures.append("recording start/finalize count mismatch")
    if not counters.nas_outage_injected or not counters.nas_recovery_injected:
        failures.append("NAS outage and recovery were not both injected")

    if resources["max_cpu_pct"] > config.max_cpu_pct:
        failures.append(
            f"CPU max {resources['max_cpu_pct']:.1f}% exceeds {config.max_cpu_pct:.1f}%"
        )
    if resources["max_temp_c"] > config.max_temp_c:
        failures.append(
            f"temperature max {resources['max_temp_c']:.1f}C exceeds {config.max_temp_c:.1f}C"
        )
    if resources["throttled"]:
        failures.append("thermal throttling observed")
    if resources["max_memory_mb"] > config.max_memory_mb:
        failures.append(
            f"memory max {resources['max_memory_mb']:.1f}MB exceeds {config.max_memory_mb:.1f}MB"
        )
    if resources["max_camera_frame_drops"] > config.max_camera_frame_drops:
        failures.append("camera frame drops observed")
    if resources["max_ui_reconnects"] > config.max_ui_reconnects:
        failures.append("UI reconnects observed")
    if resources["final_queue_depth"] > resources["queue_depth_at_recovery"]:
        failures.append("NAS backlog grew after recovery")

    return failures


def result_to_json(result: SoakResult) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "measurement_mode": "simulator",
        "hardware_acceptance": False,
        "passed": result.passed,
        "failures": result.failures,
        "config": asdict(result.config),
        "counters": asdict(result.counters),
        "latency": result.latency,
        "resources": result.resources,
        "samples": [asdict(sample) for sample in result.samples],
    }


def build_soak_report(result: SoakResult) -> str:
    lines = [
        "Doorboard simulator regression soak",
        "===================================",
        "Not valid for physical performance or hardware acceptance.",
        "",
        f"Verdict: {'PASS' if result.passed else 'FAIL'}",
        f"Duration: {result.config.duration_s}s simulated",
        "",
        "Counters",
        "--------",
        f"Button presses: {result.counters.observed_presses}/{result.counters.expected_presses}",
        f"Recordings: {result.counters.recordings_finalized}/{result.counters.recordings_started}",
        (
            f"Arrivals: {result.counters.known_arrivals} known, "
            f"{result.counters.unknown_arrivals} unknown"
        ),
        (
            f"NAS outage/recovery: {result.counters.nas_outage_injected}/"
            f"{result.counters.nas_recovery_injected}"
        ),
        "",
        "Latency p95",
        "-----------",
    ]

    for path, entry in sorted(result.latency["paths"].items()):
        if entry.get("simulator_na"):
            lines.append(f"{path}: simulator N/A")
            continue
        lines.append(
            f"{path}: {entry['p95_ms']:.2f}ms "
            f"(budget {entry['budget_p95_ms']:.0f}ms, n={entry['count']})"
        )

    lines.extend(
        [
            "",
            "Resources",
            "---------",
            f"CPU max: {result.resources['max_cpu_pct']:.1f}%",
            f"Temp max: {result.resources['max_temp_c']:.1f}C",
            f"Memory max: {result.resources['max_memory_mb']:.1f}MB",
            f"Queue depth final/recovery: {result.resources['final_queue_depth']}/"
            f"{result.resources['queue_depth_at_recovery']}",
            f"Camera frame drops max: {result.resources['max_camera_frame_drops']}",
            f"UI reconnects max: {result.resources['max_ui_reconnects']}",
            f"Queue depth graph: {_sparkline([s.queue_depth for s in result.samples])}",
            f"CPU graph: {_sparkline([s.cpu_pct for s in result.samples])}",
            "",
        ]
    )

    if result.failures:
        lines.append("Failures")
        lines.append("--------")
        lines.extend(f"- {failure}" for failure in result.failures)
        lines.append("")

    return "\n".join(lines)


def write_result(result: SoakResult, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "soak-verdict.json"
    report_path = output_dir / "soak-report.txt"
    json_path.write_text(
        json.dumps(result_to_json(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(build_soak_report(result), encoding="utf-8")
    return json_path, report_path


def _summarize_resources(
    samples: list[ResourceSample], queue_depth_at_recovery: int | None
) -> dict[str, Any]:
    final_queue_depth = samples[-1].queue_depth if samples else 0
    return {
        "max_cpu_pct": max((sample.cpu_pct for sample in samples), default=0.0),
        "max_temp_c": max((sample.temp_c for sample in samples), default=0.0),
        "throttled": any(sample.throttled for sample in samples),
        "max_camera_frame_drops": max((sample.camera_frame_drops for sample in samples), default=0),
        "max_queue_depth": max((sample.queue_depth for sample in samples), default=0),
        "queue_depth_at_recovery": (
            queue_depth_at_recovery if queue_depth_at_recovery is not None else 0
        ),
        "final_queue_depth": final_queue_depth,
        "max_memory_mb": max((sample.memory_mb for sample in samples), default=0.0),
        "max_webrtc_clients": max((sample.webrtc_clients for sample in samples), default=0),
        "max_ui_reconnects": max((sample.ui_reconnects for sample in samples), default=0),
    }


def _sparkline(values: list[float | int], *, width: int = 40) -> str:
    if not values:
        return ""
    if len(values) > width:
        stride = len(values) / width
        values = [values[int(i * stride)] for i in range(width)]
    lo = min(values)
    hi = max(values)
    if hi == lo:
        return "-" * len(values)
    return "".join("#" if value > lo else "." for value in values)


async def _main(args: argparse.Namespace) -> int:
    config = SoakConfig.ci() if args.profile == "ci" else SoakConfig()
    result = await run_simulator_soak(config)
    json_path, report_path = write_result(result, Path(args.output_dir))
    print(build_soak_report(result))
    print(f"JSON verdict written to {json_path}")
    print(f"Text report written to {report_path}")
    return 0 if result.passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Doorboard simulator regression soak runner.")
    parser.add_argument("--profile", choices=["ci", "full"], default="ci")
    parser.add_argument(
        "--output-dir",
        default="tests/performance/results/latest-simulator-soak",
        help="Directory for soak-verdict.json and soak-report.txt.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
