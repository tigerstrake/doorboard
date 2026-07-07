from __future__ import annotations

import abc
import contextlib
import logging
from datetime import datetime, timedelta
from typing import Any, Literal

import httpx
from pydantic import BaseModel

logger = logging.getLogger("doorboard.printer")

PrinterState = Literal["idle", "printing", "paused", "error", "offline"]


class PrinterConfig(BaseModel):
    octoprint_url: str
    octoprint_api_key: str
    camera_stream_url: str = ""


class PrinterProvider(abc.ABC):
    @abc.abstractmethod
    def get_status(self, now: datetime) -> dict[str, Any]:
        """Fetch read-only printer status (state, job name, progress, ETA)."""
        pass


class OctoPrintProvider(PrinterProvider):
    def __init__(self, config: PrinterConfig) -> None:
        self.config = config

    def get_status(self, now: datetime) -> dict[str, Any]:
        url = f"{self.config.octoprint_url.rstrip('/')}/api/job"
        headers = {"X-Api-Key": self.config.octoprint_api_key}

        try:
            resp = httpx.get(url, headers=headers, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                state_str = data.get("state", "Offline")
                job = data.get("job") or {}
                progress = data.get("progress") or {}

                # Map state strings to schema states
                state: PrinterState = "offline"
                state_lower = state_str.lower()

                if "printing" in state_lower:
                    state = "printing"
                elif "paused" in state_lower:
                    state = "paused"
                elif "operational" in state_lower:
                    state = "idle"
                elif "error" in state_lower or "fail" in state_lower:
                    state = "error"
                elif "offline" in state_lower:
                    state = "offline"
                else:
                    state = "idle"

                job_name = job.get("file", {}).get("name")
                progress_pct = progress.get("completion")
                print_time_left = progress.get("printTimeLeft")

                eta = None
                if state in ("printing", "paused") and print_time_left is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        eta = now + timedelta(seconds=int(print_time_left))

                # Clean up fields if not active
                if state not in ("printing", "paused"):
                    job_name = None
                    progress_pct = None
                    eta = None

                return {
                    "state": state,
                    "job_name": job_name,
                    "progress_pct": progress_pct,
                    "eta": eta,
                }
            else:
                logger.warning(
                    f"OctoPrint returned status {resp.status_code}. Treating as offline."
                )
        except Exception as e:
            logger.warning(f"Failed to query OctoPrint: {e}. Treating as offline.")

        return {
            "state": "offline",
            "job_name": None,
            "progress_pct": None,
            "eta": None,
        }


class MockPrinterProvider(PrinterProvider):
    def __init__(self, force_state: PrinterState | None = None) -> None:
        self.force_state = force_state

    def get_status(self, now: datetime) -> dict[str, Any]:
        state = self.force_state or "printing"
        if state == "printing":
            return {
                "state": "printing",
                "job_name": "benchy_0.2mm_pla.gcode",
                "progress_pct": 64.5,
                "eta": now + timedelta(minutes=45),
            }
        elif state == "paused":
            return {
                "state": "paused",
                "job_name": "benchy_0.2mm_pla.gcode",
                "progress_pct": 42.0,
                "eta": now + timedelta(hours=1),
            }
        elif state == "idle":
            return {
                "state": "idle",
                "job_name": None,
                "progress_pct": None,
                "eta": None,
            }
        elif state == "error":
            return {
                "state": "error",
                "job_name": None,
                "progress_pct": None,
                "eta": None,
            }
        else:
            return {
                "state": "offline",
                "job_name": None,
                "progress_pct": None,
                "eta": None,
            }
