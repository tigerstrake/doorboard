"""Persistent privacy-mode flag (ADR-0009 §4).

The flag lives on the SSD and is restored on boot *before* the first frame is
captured, so a device that reboots in privacy mode never captures a frame until
the persisted state is applied (proven by test P-8).
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
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
        """Return persisted state, failing closed when an existing flag is unreadable."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw.get("enabled"), bool):
                raise ValueError("privacy enabled flag must be boolean")
            return PrivacyState(
                enabled=raw["enabled"],
                changed_by=str(raw.get("changed_by", "config")),
                updated_at=str(raw.get("updated_at", "")),
            )
        except FileNotFoundError:
            return PrivacyState(enabled=False, changed_by="default", updated_at="")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.error("privacy_state_invalid_fail_closed", extra={"error": str(exc)})
            return PrivacyState(enabled=True, changed_by="fail_closed", updated_at="")

    def save(self, *, enabled: bool, changed_by: str) -> PrivacyState:
        state = PrivacyState(
            enabled=enabled,
            changed_by=changed_by,
            updated_at=datetime.now(UTC).isoformat(),
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "enabled": state.enabled,
                "changed_by": state.changed_by,
                "updated_at": state.updated_at,
            },
            separators=(",", ":"),
        )
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self._path.name}.",
            dir=self._path.parent,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
            directory_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary)
        logger.info("privacy_state_saved", extra={"enabled": enabled, "changed_by": changed_by})
        return state
