"""The last ambient event of each type, so a reloaded wallboard is not blank.

Ambient channels publish on wildly different cadences: aircraft every 30 s, satellite
passes hourly, the dining recommendation **once a day**. The wallboard populates its tiles
purely from live ``ambient.*`` events arriving on ``/ws``.

The consequence, which is what prompted this: a wallboard that reloads — kiosk restart,
door-ui restart, someone refreshing the page — loses the dining recommendation and cannot get
it back for up to 24 hours. The event was produced correctly, reached the broker correctly,
crossed the bridge correctly, and landed in a browser that had already gone away.

The control plane *does* now retain ``ambient.*`` and ``status.*`` on MQTT
(``control_plane_api/mqtt.py``), but that solves a different problem: it replays the last value
to **the bridge** when door-api reconnects to the broker. It does nothing for a browser
reloading against a door-api that never dropped its MQTT connection, which is the common case
here. The two mechanisms cover the two hops and neither substitutes for the other.

So door-api remembers the most recent event per ambient type and replays them to each new
``/ws`` client, in door-api's ordinary delta envelope. A reconnecting wallboard sees exactly
what it would have seen had it been listening all along, and needs no new code path for it.

Presence (ADR-0048)
===================
``status.*`` carries presence, and ADR-0027 deliberately left it out: a client connecting
later would learn where someone was several minutes ago. That exclusion cost the wallboard
its primary tile — "Presence unavailable." after every kiosk relaunch, door-ui restart or
reload until someone's status next *changed*, which on a stable evening is hours. The label
a late client learns is the same broad label the same corridor was shown a moment earlier,
and the control plane already bounds how long a label may be believed
(``CONTROL_PLANE_PRESENCE_STALE_AFTER_S``, 30 min).

So the last ``status.*`` event is kept per ``(type, subject_id)`` — bounded to a handful of
subjects, since the id arrives off an MQTT topic — and replayed **only while fresh**: the
wall clock must be inside ``occurred_at + presence_stale_after_s`` and, when the event
carries ``until``, before it. A stale or expired label is not replayed; the tile shows
*unknown*, never the past. Nothing older than the control plane's own freshness bound is
ever handed to a client, which keeps ADR-0027's privacy stance in spirit.

Entries expire (``max_age_s``), because a stale ambient reading presented as current is worse
than an empty tile. After a multi-day outage the dining channel must come back empty rather
than confidently serving last Tuesday's lunch.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

# Just over a day. Set when the longest producer interval was the daily food job (now
# every 3 h) and kept as the ceiling: a producer on any cadence up to daily survives a
# reload, but a genuinely dead one stops being quoted after a day.
DEFAULT_MAX_AGE_S = 26 * 3600.0

# A cap on distinct remembered types. The type comes off an MQTT topic, so a renamed or
# misbehaving producer must not be able to grow this without bound.
DEFAULT_MAX_TYPES = 16

# How long a presence label stays worth replaying. Matches the control plane's own
# staleness contract (CONTROL_PLANE_PRESENCE_STALE_AFTER_S), which is the bound ADR-0048
# leans on: door-api never quotes a label the resolver itself would call stale.
DEFAULT_PRESENCE_STALE_AFTER_S = 1800.0

# Presence is per subject and the subject id arrives off a topic; a house has a handful.
DEFAULT_MAX_SUBJECTS = 8


def _utc_timestamp(value: object) -> float | None:
    """Epoch seconds for an ISO-8601 timestamp off an event dict, or None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


class AmbientCache:
    """Most-recent ``ambient.*`` event per type, and ``status.*`` per subject, age-bounded."""

    def __init__(
        self,
        *,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        max_types: int = DEFAULT_MAX_TYPES,
        presence_stale_after_s: float = DEFAULT_PRESENCE_STALE_AFTER_S,
        max_subjects: int = DEFAULT_MAX_SUBJECTS,
        monotonic_fn: Callable[[], float] | None = None,
        wall_clock_fn: Callable[[], float] | None = None,
    ) -> None:
        self._max_age_s = max_age_s
        self._max_types = max_types
        self._presence_stale_after_s = presence_stale_after_s
        self._max_subjects = max_subjects
        self._monotonic = monotonic_fn or time.monotonic
        # Presence freshness is judged against the event's own occurred_at/until, which
        # are wall-clock, so this one comparison uses the wall clock too.
        self._wall_clock = wall_clock_fn or time.time
        # Insertion-ordered so eviction drops the least recently *updated* type.
        self._entries: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._presence: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()

    def remember(self, event: dict[str, Any]) -> bool:
        """Record an event if it is an ambient or presence one. Returns whether it was kept.

        Called on the bridge's hot path, so it must not raise on a surprising shape — a
        payload that got this far is already schema-valid, but a missing ``type`` should
        cost a skip, not an exception that unwinds the bridge's read loop.
        """
        event_type = event.get("type")
        if not isinstance(event_type, str):
            return False
        if event_type.startswith("ambient."):
            self._entries.pop(event_type, None)
            self._entries[event_type] = (self._monotonic(), event)
            while len(self._entries) > self._max_types:
                self._entries.popitem(last=False)
            return True
        if event_type.startswith("status."):
            payload = event.get("payload")
            subject_id = payload.get("subject_id") if isinstance(payload, dict) else None
            if not isinstance(subject_id, str) or not subject_id:
                return False
            key = (event_type, subject_id)
            self._presence.pop(key, None)
            self._presence[key] = event
            while len(self._presence) > self._max_subjects:
                self._presence.popitem(last=False)
            return True
        return False

    def replay(self) -> list[dict[str, Any]]:
        """The events a newly connected client should be told about, oldest first.

        Oldest first so that if two types ever arrive out of order the client applies them
        in the same sequence a live listener would have. Presence follows ambient, and only
        the entries that are still fresh (ADR-0048).
        """
        self._expire()
        now = self._wall_clock()
        return [event for _, event in self._entries.values()] + [
            event for event in self._presence.values() if self._presence_is_fresh(event, now)
        ]

    def _presence_is_fresh(self, event: dict[str, Any], now: float) -> bool:
        occurred_at = _utc_timestamp(event.get("occurred_at"))
        if occurred_at is None or now >= occurred_at + self._presence_stale_after_s:
            return False
        payload = event.get("payload")
        until = payload.get("until") if isinstance(payload, dict) else None
        if until is not None:
            until_at = _utc_timestamp(until)
            # An `until` that cannot be read fails toward not replaying.
            if until_at is None or now >= until_at:
                return False
        return True

    def _expire(self) -> None:
        cutoff = self._monotonic() - self._max_age_s
        for event_type in [t for t, (at, _) in self._entries.items() if at < cutoff]:
            del self._entries[event_type]

    def forget_all(self) -> None:
        self._entries.clear()
        self._presence.clear()

    def __len__(self) -> int:
        return len(self.replay())
