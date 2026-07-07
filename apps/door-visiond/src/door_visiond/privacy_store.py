"""Persistent privacy-mode flag (ADR-0009 §4).

The flag lives on the SSD and is restored on boot *before* the first frame is
captured, so a device that reboots in privacy mode never captures a frame until
the persisted state is applied (proven by test P-8).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from door_visiond.logging_setup import get_logger

logger = get_logger("door_visiond.privacy_store")


@dataclass(frozen=True)
class PrivacyState:
    enabled: bool
    changed_by: str
    updated_at: str


class PrivacyStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> PrivacyState:
        """Return the persisted privacy state; default (disabled) if unset/corrupt."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return PrivacyState(
                enabled=bool(raw["enabled"]),
                changed_by=str(raw.get("changed_by", "config")),
                updated_at=str(raw.get("updated_at", "")),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return PrivacyState(enabled=False, changed_by="default", updated_at="")

    def save(self, *, enabled: bool, changed_by: str) -> PrivacyState:
        state = PrivacyState(
            enabled=enabled,
            changed_by=changed_by,
            updated_at=datetime.now(UTC).isoformat(),
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {
                    "enabled": state.enabled,
                    "changed_by": state.changed_by,
                    "updated_at": state.updated_at,
                }
            ),
            encoding="utf-8",
        )
        logger.info("privacy_state_saved", extra={"enabled": enabled, "changed_by": changed_by})
        return state
