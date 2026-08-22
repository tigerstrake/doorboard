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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
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
    # A recurring local-time window (ADR-0037). Deliberately the weakest real
    # signal: it is a standing assumption about a time of day, so anything that
    # actually knows something — a Focus, a location, a class — outranks it.
    "schedule",
    "default",
)

# Sources whose value is *inferred* rather than explicitly declared by the
# subject. Gated by a subject's `tracking_enabled` flag (the "config flag
# per subject" the brief scopes roommate consent down to) — "manual" is
# always available because it's the subject (or an admin on their behalf)
# directly stating their own status, not inference about them.
INFERRED_SOURCES: frozenset[str] = frozenset(
    {"focus_shortcut", "geofence_label", "calendar", "schedule"}
)

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
# Nightly schedule inference (ADR-0037). Same shape as CalendarProvider and
# likewise NOT a stored source: it is computed live from the clock, which is what
# makes it reappear the instant a higher source is cleared. A stored row would
# have to be re-triggered, and could get stuck.
# ---------------------------------------------------------------------------


class ScheduleProvider(Protocol):
    def get_label(self, subject_id: str, *, now: datetime) -> SourceEntry | None: ...


def parse_window(raw: str) -> tuple[dtime, dtime] | None:
    """Parse ``"23:00-07:00"`` in LOCAL time. Empty disables the schedule.

    An unparseable value raises rather than silently disabling: the safe failure
    for "is the door meant to say Recovery at night" is a loud one, not a door
    that quietly never does.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        start_text, end_text = text.split("-", 1)
        return dtime.fromisoformat(start_text.strip()), dtime.fromisoformat(end_text.strip())
    except ValueError as exc:
        msg = f"PRESENCE_SCHEDULE_WINDOW must look like '23:00-07:00', got {raw!r}"
        raise ValueError(msg) from exc


class NightlyScheduleProvider:
    """Reports a label inside a recurring local-time window.

    Returns `until` = the window's end, so the engine expires it on schedule with
    no background task — the same mechanism that makes "busy until 15:00" revert.
    """

    def __init__(
        self,
        window: tuple[dtime, dtime],
        *,
        label: PresenceLabel,
        subject_ids: Sequence[str] | None = None,
    ) -> None:
        self._start, self._end = window
        self._label = label
        # None = every subject. A window is a household-level habit, but the
        # roommate should not inherit it just because they share the door.
        self._subject_ids = frozenset(subject_ids) if subject_ids else None

    def get_label(self, subject_id: str, *, now: datetime) -> SourceEntry | None:
        if self._subject_ids is not None and subject_id not in self._subject_ids:
            return None
        local = now.astimezone()
        if not self._inside(local.time()):
            return None
        return SourceEntry(label=self._label, until=self._window_end_after(local))

    def _inside(self, moment: dtime) -> bool:
        if self._start == self._end:
            # A zero-width window would read as "never"; the defensible reading of
            # start == end is "always", matching how quiet hours behave elsewhere.
            return True
        if self._start < self._end:
            return self._start <= moment < self._end
        return moment >= self._start or moment < self._end  # wraps midnight

    def _window_end_after(self, local: datetime) -> datetime:
        end_today = local.replace(
            hour=self._end.hour, minute=self._end.minute, second=0, microsecond=0
        )
        if end_today <= local:
            end_today += timedelta(days=1)
        return end_today.astimezone(UTC)


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
