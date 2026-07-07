"""Presence / Weasley-clock resolution — pure logic, no DB, no I/O (T-504).

This module is deliberately side-effect-free so the precedence/expiry rules
(the part reviewers and future maintainers most need to trust) are testable
as plain function calls. `presence_engine.py` is the DB-backed layer that
persists a source registry, calls `resolve_presence` here, and emits
`status.presence_changed` only when the resolved answer changes.

Precedence is fixed and non-negotiable (docs/tasks/T-504-presence-engine.md,
docs/protocols/events.md `status.*`, ARCHITECTURE.md §9):

    manual > focus_shortcut > geofence_label > calendar > default

A source "wins" if it has a value and (its `until` is unset or still in the
future); otherwise resolution falls through to the next-lower-precedence
source — this is how "busy until 15:00" reverts to whatever's next once
15:00 passes, with no separate scheduler needed: every caller resolves
against the current `now` (see `presence_engine.sync_presence`, called from
both writes and reads).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from doorboard_contracts import PresenceLabel

logger = logging.getLogger("control_plane_api.presence")

# Lower index = higher precedence. "default" always resolves (it's the
# floor), so every other source falling through eventually lands here.
SOURCE_PRECEDENCE: tuple[str, ...] = (
    "manual",
    "focus_shortcut",
    "geofence_label",
    "calendar",
    "default",
)

# Sources whose value is *inferred* rather than explicitly declared by the
# subject. Gated by a subject's `tracking_enabled` flag (the "config flag
# per subject" the brief scopes roommate consent down to) — "manual" is
# always available because it's the subject (or an admin on their behalf)
# directly stating their own status, not inference about them.
INFERRED_SOURCES: frozenset[str] = frozenset({"focus_shortcut", "geofence_label", "calendar"})

DEFAULT_LABEL = PresenceLabel.UNKNOWN


@dataclass(frozen=True, slots=True)
class SourceEntry:
    """One source's current value, as fed into `resolve_presence`."""

    label: PresenceLabel
    until: datetime | None = None


@dataclass(frozen=True, slots=True)
class ResolvedPresence:
    """The precedence-resolved answer for one subject at one instant."""

    label: PresenceLabel
    source: str
    until: datetime | None


def resolve_presence(
    entries: Mapping[str, SourceEntry | None],
    *,
    now: datetime,
    default_label: PresenceLabel = DEFAULT_LABEL,
) -> ResolvedPresence:
    """Resolve the winning source for one subject at `now`.

    `entries` is keyed by source name (a subset of `SOURCE_PRECEDENCE`);
    callers are responsible for omitting sources that are disabled, gated by
    consent, or simply have no value — this function only implements
    precedence + expiry fallthrough, nothing else.
    """
    for source in SOURCE_PRECEDENCE:
        entry = entries.get(source)
        if entry is None:
            continue
        if entry.until is not None and entry.until <= now:
            continue  # expired: fall through to the next-lower-precedence source
        return ResolvedPresence(label=entry.label, source=source, until=entry.until)
    return ResolvedPresence(label=default_label, source="default", until=None)


# ---------------------------------------------------------------------------
# Calendar inference — stub interface only (T-504 brief: "real calendar
# wiring is a later brief"). `calendar` is not a stored registry row like
# the other sources; it's queried fresh on every resolution.
# ---------------------------------------------------------------------------


class CalendarProvider(Protocol):
    def get_label(self, subject_id: str, *, now: datetime) -> SourceEntry | None: ...


class MockCalendarProvider:
    """Dev/CI/test stand-in. Holds canned answers set directly by callers."""

    def __init__(self, canned: dict[str, SourceEntry] | None = None) -> None:
        self._canned: dict[str, SourceEntry] = dict(canned or {})

    def set_canned(self, subject_id: str, entry: SourceEntry | None) -> None:
        if entry is None:
            self._canned.pop(subject_id, None)
        else:
            self._canned[subject_id] = entry

    def get_label(self, subject_id: str, *, now: datetime) -> SourceEntry | None:
        del now
        return self._canned.get(subject_id)


# ---------------------------------------------------------------------------
# Coordinate rejection — HA webhooks (Focus shortcuts, voluntary geofence
# labels) must carry broad label strings only. ARCHITECTURE.md §9: "no raw
# GPS anywhere". Belt-and-braces on top of the webhook payload model's
# `extra="forbid"`: this scans recursively and logs exactly which field
# triggered the rejection, rather than a generic "unknown field" error.
# ---------------------------------------------------------------------------

COORDINATE_KEY_MARKERS: tuple[str, ...] = (
    "lat",
    "lon",
    "lng",
    "geo",
    "coordinate",
    "gps",
    "location",
)


class CoordinatePayloadError(ValueError):
    pass


def _scan_for_coordinate_keys(data: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            key_l = str(key).lower()
            full_path = f"{path}.{key}" if path else str(key)
            if any(marker in key_l for marker in COORDINATE_KEY_MARKERS):
                hits.append(full_path)
            hits.extend(_scan_for_coordinate_keys(value, full_path))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            hits.extend(_scan_for_coordinate_keys(item, f"{path}[{i}]"))
    return hits


def reject_coordinate_payload(raw: Mapping[str, Any], *, context: str) -> None:
    """Raise + log if `raw` contains any coordinate-shaped field, at any depth."""
    hits = _scan_for_coordinate_keys(dict(raw))
    if hits:
        logger.warning(
            "presence_webhook_coordinate_payload_rejected",
            extra={"context": context, "fields": hits},
        )
        msg = f"payload contains coordinate-bearing field(s): {', '.join(hits)}"
        raise CoordinatePayloadError(msg)


# ---------------------------------------------------------------------------
# Staleness — the wallboard tile shows a last-known label with a staleness
# hint when the NUC is unreachable (ui-kit's `Tile` already renders an
# `as_of` prop; this just decides, given the bundle's `generated_at`,
# whether the Pi-cached copy should be considered stale).
# ---------------------------------------------------------------------------


def is_stale(generated_at: datetime, *, now: datetime, max_age_s: float) -> bool:
    age_s = (now - generated_at).total_seconds()
    return age_s > max_age_s
