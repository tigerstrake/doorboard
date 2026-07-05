from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doorboard_contracts import DoorboardEvent

from doorboard_simulator.clock import SimClock
from doorboard_simulator.esp32 import FakeEsp32Transport
from doorboard_simulator.events import EventFactory, event_to_log
from doorboard_simulator.ids import SESSION_ID
from doorboard_simulator.media import FakeMediaRouter
from doorboard_simulator.outages import OutageState
from doorboard_simulator.vision import FakeVisionPipeline, PersonScript

SCENARIO_DIR = Path(__file__).resolve().parents[2] / "scenarios"
DEFAULT_ARTIFACT_ROOT = Path(".simulator-artifacts")


@dataclass(frozen=True)
class Scenario:
    name: str
    steps: list[dict[str, Any]]


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    events: list[DoorboardEvent]
    log: list[dict[str, Any]]


def load_scenario(path: Path) -> Scenario:
    data = json.loads(path.read_text(encoding="utf-8"))
    name = data["name"]
    steps = data["steps"]
    if not isinstance(name, str) or not isinstance(steps, list):
        msg = f"invalid scenario file: {path}"
        raise ValueError(msg)
    return Scenario(name=name, steps=steps)


def scenario_path(name: str) -> Path:
    path = SCENARIO_DIR / f"{name}.json"
    if not path.exists():
        msg = f"unknown scenario: {name}"
        raise FileNotFoundError(msg)
    return path


def available_scenarios() -> list[str]:
    return sorted(path.stem for path in SCENARIO_DIR.glob("*.json"))


class ScenarioRunner:
    def __init__(self, *, artifact_root: Path = DEFAULT_ARTIFACT_ROOT) -> None:
        self.clock = SimClock()
        self.outages = OutageState()
        self.events = EventFactory(self.clock)
        self.esp32 = FakeEsp32Transport(self.clock, self.events)
        self.vision = FakeVisionPipeline(self.clock, self.events, self.outages)
        self.media = FakeMediaRouter(
            self.clock, self.events, self.outages, artifact_root=artifact_root
        )
        self._emitted: list[DoorboardEvent] = []

    async def run(self, scenario: Scenario) -> ScenarioResult:
        for step in scenario.steps:
            at_ms = step.get("at_ms")
            if isinstance(at_ms, int):
                self.clock.advance_to(at_ms)
            await self._run_step(step)
        log = [event_to_log(event) for event in self._emitted]
        return ScenarioResult(name=scenario.name, events=list(self._emitted), log=log)

    async def _run_step(self, step: dict[str, Any]) -> None:
        action = step["action"]
        if action == "pi_hello":
            await self.esp32.send(
                self.esp32.make_message(
                    "hello",
                    {"sw_version": "doorboard-simulator", "proto_v": 1, "boot_id": "pi-sim-boot"},
                )
            )
            return
        if action == "pi_heartbeat":
            await self.esp32.send(
                self.esp32.make_message(
                    "heartbeat",
                    {"uptime_s": self.clock.monotonic_ms // 1000, "fallback_active": False},
                )
            )
            msg = await self.esp32.heartbeat_from_esp32()
            self._emit(self.esp32.to_contract_event(msg))
            return
        if action == "button":
            msg = await self.esp32.emit_button_press()
            self._emit(self.esp32.to_contract_event(msg))
            return
        if action == "known_visitor":
            person = PersonScript(
                person_id=str(step["person_id"]),
                display_name=str(step["display_name"]),
                profile_id=str(step["profile_id"]),
            )
            self._emit(await self.vision.face_visible())
            stable = await self.vision.identity_stable(person)
            if stable is not None:
                self._emit(stable)
                stable_payload = stable.model_dump(mode="python")["payload"]
                ttl_ms = int(stable_payload["expires_at_monotonic_ms"]) - self.clock.monotonic_ms
                await self.esp32.send(
                    self.esp32.make_message(
                        "profile_update",
                        {
                            "profile_id": str(stable_payload["profile_id"]),
                            "ttl_ms": ttl_ms,
                            "priority": "normal",
                        },
                    )
                )
            return
        if action == "unknown_visitor":
            self._emit(
                await self.vision.face_visible(
                    face_count=int(step.get("face_count", 1)),
                    largest_face_px=int(step.get("largest_face_px", 140)),
                )
            )
            return
        if action == "identity_expired":
            self._emit(await self.vision.identity_expired(str(step["person_id"])))
            return
        if action == "hailo_down":
            self.outages.set_hailo(False)
            self._emit(await self.vision.pipeline_status())
            return
        if action == "hailo_up":
            self.outages.set_hailo(True)
            self._emit(await self.vision.pipeline_status())
            return
        if action == "nuc_down":
            self.outages.set_nuc(False)
            self._emit_service_health("control-plane-api", "down", "simulated NUC outage")
            return
        if action == "nuc_up":
            self.outages.set_nuc(True)
            self._emit_service_health("control-plane-api", "ok", None)
            return
        if action == "nas_down":
            self.outages.set_nas(False)
            self._emit_storage_status()
            return
        if action == "nas_up":
            self.outages.set_nas(True)
            self._emit_storage_status()
            return
        if action == "storage_full":
            self.outages.set_storage_full(True)
            status = self.media.storage_status()
            self._emit(
                self.events.make(
                    "system.storage_alert",
                    {
                        "host": "door-pi",
                        "mount": "/mnt/door-ssd",
                        "free_bytes": status.free_bytes,
                        "severity": "critical",
                    },
                )
            )
            self._emit_storage_status()
            return
        if action == "storage_ok":
            self.outages.set_storage_full(False)
            self._emit_storage_status()
            return
        if action == "record_bell":
            if not self.media.storage_status().recording_allowed:
                return
            handle = await self.media.start_recording(
                session_id=SESSION_ID,
                kind="bell_clip",
                stream="visitor-main",
            )
            self._emit(
                self.events.make(
                    "media.recording_started",
                    {
                        "recording_id": handle.recording_id,
                        "session_id": handle.session_id,
                        "kind": handle.kind,
                        "stream": handle.stream,
                    },
                )
            )
            duration_ms = int(step.get("duration_ms", 1_000))
            self.clock.advance_by(duration_ms)
            finalized = await self.media.finalize_recording(handle, consent_context="bell_event")
            self._emit(
                self.events.make(
                    "media.recording_finalized",
                    {
                        "recording_id": finalized.recording_id,
                        "path": finalized.path,
                        "duration_s": finalized.duration_s,
                        "size_bytes": finalized.size_bytes,
                        "sha256": finalized.sha256,
                        "consent_context": finalized.consent_context,
                    },
                )
            )
            return
        msg = f"unknown scenario action: {action}"
        raise ValueError(msg)

    def _emit(self, event: DoorboardEvent) -> None:
        self._emitted.append(event)

    def _emit_service_health(self, service: str, status: str, detail: str | None) -> None:
        self._emit(
            self.events.make(
                "system.service_health",
                {"service": service, "status": status, "detail": detail},
            )
        )

    def _emit_storage_status(self) -> None:
        status = self.media.storage_status()
        self._emit(
            self.events.make(
                "media.storage_status",
                {
                    "free_bytes": status.free_bytes,
                    "queue_depth": status.queue_depth,
                    "oldest_unsynced_s": status.oldest_unsynced_s,
                    "recording_allowed": status.recording_allowed,
                },
            )
        )


async def run_scenario_name(
    name: str,
    *,
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
) -> ScenarioResult:
    scenario = load_scenario(scenario_path(name))
    runner = ScenarioRunner(artifact_root=artifact_root)
    return await runner.run(scenario)


def result_to_json(result: ScenarioResult) -> str:
    return (
        json.dumps({"scenario": result.name, "events": result.log}, indent=2, sort_keys=True) + "\n"
    )
