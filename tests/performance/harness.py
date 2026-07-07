"""Latency measurement harness — CLI runner.

Drives the simulator through all ARCHITECTURE.md §4 paths and produces
p50/p95/p99 tables.  The shared logic (BUDGET_P95_MS, check_regressions,
build_report, build_json) lives in doorboard_observability.harness_core
so tests can import it without path manipulation.

Usage::

    # Run against the simulator (default):
    python -m tests.performance.harness

    # Save a new baseline:
    python -m tests.performance.harness --save-baseline

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
import json
import sys
import tempfile
import time
from pathlib import Path

from doorboard_observability.harness_core import (
    BUDGET_P95_MS,
    build_json,
    build_report,
    check_regressions,
    load_baseline,
    save_baseline,
)

# The committed baseline lives alongside this runner.
BASELINE_PATH = Path(__file__).parent / "baseline.json"


# ---------------------------------------------------------------------------
# Simulator-based measurement
# ---------------------------------------------------------------------------


async def run_simulator() -> dict[str, list[float]]:
    """Drive the simulator through the latency scenario and collect raw samples.

    Returns a dict mapping path name → list of millisecond durations.
    The simulator uses a wall-clock-free SimClock; we measure Pi-side
    processing overhead with time.monotonic() so the harness exercises real
    code paths even though no hardware is present.
    """
    samples: dict[str, list[float]] = {path: [] for path in BUDGET_P95_MS}

    with tempfile.TemporaryDirectory(prefix="doorboard-harness-") as tmpdir:
        await _run_simulator_loop(
            iterations=50,
            artifact_root=Path(tmpdir),
            samples=samples,
        )

    return samples


async def _run_simulator_loop(
    iterations: int,
    artifact_root: Path,
    samples: dict[str, list[float]],
) -> None:
    """Inner measurement loop.

    50 iterations give the nearest-rank p95 the 48th-ordered value —
    statistically meaningful without being slow in CI.
    """
    from doorboard_simulator.clock import SimClock
    from doorboard_simulator.esp32 import FakeEsp32Transport
    from doorboard_simulator.events import EventFactory
    from doorboard_simulator.ids import SESSION_ID
    from doorboard_simulator.media import FakeMediaRouter
    from doorboard_simulator.outages import OutageState
    from doorboard_simulator.vision import FakeVisionPipeline, PersonScript

    for i in range(iterations):
        clock = SimClock(monotonic_ms=i * 10_000)  # disjoint time windows
        outages = OutageState()
        events = EventFactory(clock)
        esp32 = FakeEsp32Transport(clock, events)

        # ----------------------------------------------------------------
        # 1. button_to_generic_feedback
        #    Measures: ESP32 emits button event → Pi converts to contract event.
        #    Hardware path adds UART serialization and ISR latency.
        # ----------------------------------------------------------------
        t0 = time.monotonic()
        msg = await esp32.emit_button_press()
        _ = esp32.to_contract_event(msg)
        samples["button_to_generic_feedback"].append((time.monotonic() - t0) * 1000.0)

        # ----------------------------------------------------------------
        # 2. button_to_personalized_feedback
        #    Same as above but with a cached profile loaded first.
        # ----------------------------------------------------------------
        clock2 = SimClock(monotonic_ms=(i + 100) * 10_000)
        events2 = EventFactory(clock2)
        esp32b = FakeEsp32Transport(clock2, events2)
        vision2 = FakeVisionPipeline(clock2, events2, outages)
        person = PersonScript(
            person_id=f"prs_bench_{i}",
            display_name="BenchUser",
            profile_id=f"profile_{i}",
        )
        stable = await vision2.identity_stable(person)
        if stable is not None:
            pl = stable.model_dump(mode="python")["payload"]
            ttl_ms = int(pl["expires_at_monotonic_ms"]) - clock2.monotonic_ms
            await esp32b.send(
                esp32b.make_message(
                    "profile_update",
                    {
                        "profile_id": str(pl["profile_id"]),
                        "ttl_ms": ttl_ms,
                        "priority": "high",
                    },
                )
            )

        t0 = time.monotonic()
        msg_b = await esp32b.emit_button_press()
        _ = esp32b.to_contract_event(msg_b)
        samples["button_to_personalized_feedback"].append((time.monotonic() - t0) * 1000.0)

        # ----------------------------------------------------------------
        # 3. face_to_stable_identity
        #    face_visible → identity_stable event creation.
        # ----------------------------------------------------------------
        clock3 = SimClock(monotonic_ms=(i + 200) * 10_000)
        events3 = EventFactory(clock3)
        vision3 = FakeVisionPipeline(clock3, events3, outages)
        person3 = PersonScript(
            person_id=f"prs_face_{i}",
            display_name="FaceUser",
            profile_id=f"profile_face_{i}",
        )
        t0 = time.monotonic()
        _ = await vision3.face_visible()
        _ = await vision3.identity_stable(person3)
        samples["face_to_stable_identity"].append((time.monotonic() - t0) * 1000.0)

        # ----------------------------------------------------------------
        # 4. bell_to_recording_event
        #    start_recording + finalize_recording (1 s clip).
        #    SimClock advances 1000 ms inside; wall-clock is processing only.
        # ----------------------------------------------------------------
        clock4 = SimClock(monotonic_ms=(i + 300) * 10_000)
        events4 = EventFactory(clock4)
        media4 = FakeMediaRouter(clock4, events4, outages, artifact_root=artifact_root)

        t0 = time.monotonic()
        handle = await media4.start_recording(
            session_id=SESSION_ID,
            kind="bell_clip",
            stream="visitor-main",
        )
        clock4.advance_by(1000)
        _ = await media4.finalize_recording(handle, consent_context="bell_event")
        samples["bell_to_recording_event"].append((time.monotonic() - t0) * 1000.0)

        # ----------------------------------------------------------------
        # 5. bell_to_visitor_mode
        #    Proxy: button_event processing + state-machine dispatch.
        #    Full end-to-end needs door-api WebSocket (measured on bench).
        # ----------------------------------------------------------------
        t0 = time.monotonic()
        msg_v = await esp32.emit_button_press()
        ev = esp32.to_contract_event(msg_v)
        _ = ev.model_dump(mode="json")
        samples["bell_to_visitor_mode"].append((time.monotonic() - t0) * 1000.0)

        # ----------------------------------------------------------------
        # 6. tap_to_local_response
        #    No touchscreen in simulator — measures event creation overhead.
        # ----------------------------------------------------------------
        t0 = time.monotonic()
        ev2 = events.make(
            "session.state_changed",
            {
                "session_id": "00000000-0000-0000-0000-000000000001",
                "from_state": "BUTTON_PRESSED",
                "to_state": "VISITOR_MODE",
                "trigger": "auto",
            },
        )
        _ = ev2.model_dump(mode="json")
        samples["tap_to_local_response"].append((time.monotonic() - t0) * 1000.0)

        # ----------------------------------------------------------------
        # 7. webrtc_glass_to_glass
        #    Requires MediaMTX + WebRTC — not simulatable.
        #    Sentinel 0.0 → "simulator N/A" in the report.
        # ----------------------------------------------------------------
        samples["webrtc_glass_to_glass"].append(0.0)


# ---------------------------------------------------------------------------
# Hardware adapter stub
# ---------------------------------------------------------------------------


async def run_hardware() -> dict[str, list[float]]:
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


async def _main(args: argparse.Namespace) -> int:
    if args.mode == "hardware":
        samples = await run_hardware()
    else:
        samples = await run_simulator()

    baseline = load_baseline(BASELINE_PATH)
    if baseline is None and BASELINE_PATH.exists():
        print(f"Warning: could not load baseline from {BASELINE_PATH}", file=sys.stderr)

    regressions = check_regressions(samples, baseline) if baseline else []

    report_json = build_json(samples, regressions)
    report_text = build_report(samples, baseline=baseline, regressions=regressions)

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
        save_baseline(BASELINE_PATH, samples, regressions)
        print(f"Baseline saved to {BASELINE_PATH}", file=sys.stderr)

    return 1 if regressions else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Doorboard latency harness — p50/p95/p99 tables per ARCHITECTURE.md §4 path."
    )
    parser.add_argument(
        "--mode",
        choices=["simulator", "hardware"],
        default="simulator",
        help="Run against simulator (default) or real hardware bench.",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="Overwrite baseline.json with current results.",
    )
    parser.add_argument("--json-out", metavar="PATH", default=None)
    parser.add_argument("--report-out", metavar="PATH", default=None)
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
