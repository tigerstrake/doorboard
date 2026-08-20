"""The last ambient event of each type, so a reloaded wallboard is not blank.

Ambient channels publish on wildly different cadences: aircraft every 30 s, satellite
passes hourly, the dining recommendation **once a day**. The wallboard populates its tiles
purely from live ``ambient.*`` events arriving on ``/ws``, and the control plane publishes to
MQTT without ``retain``, so nothing anywhere holds a last value.

The consequence, which is what prompted this: a wallboard that reloads — kiosk restart,
door-ui restart, someone refreshing the page — loses the dining recommendation and cannot get
it back for up to 24 hours. The event was produced correctly, reached the broker correctly,
crossed the bridge correctly, and landed in a browser that had already gone away.

So door-api remembers the most recent event per ambient type and replays them to each new
``/ws`` client, in door-api's ordinary delta envelope. A reconnecting wallboard sees exactly
what it would have seen had it been listening all along, and needs no new code path for it.

Scope and privacy
=================
``ambient.*`` only — deliberately **not** ``status.*``, which carries presence. Presence is
personal, it has its own staleness contract on the control plane, and caching it here would
change a live-only signal into a remembered one: a client connecting later would learn where
someone was several minutes ago, which it would otherwise never have been told. Aircraft
overhead, tonight's satellite pass and what is for lunch carry no such weight.

Entries expire (``max_age_s``), because a stale ambient reading presented as current is worse
than an empty tile. After a multi-day outage the dining channel must come back empty rather
than confidently serving last Tuesday's lunch.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

# Just over the longest producer interval (the daily food job at 86400 s), so a normal
# once-a-day cadence always survives but a genuinely dead producer stops being quoted.
DEFAULT_MAX_AGE_S = 26 * 3600.0

# A cap on distinct remembered types. The type comes off an MQTT topic, so a renamed or
# misbehaving producer must not be able to grow this without bound.
DEFAULT_MAX_TYPES = 16


class AmbientCache:
    """Most-recent ``ambient.*`` event per type, with an age bound."""

    def __init__(
        self,
        *,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        max_types: int = DEFAULT_MAX_TYPES,
        monotonic_fn: Callable[[], float] | None = None,
    ) -> None:
        self._max_age_s = max_age_s
        self._max_types = max_types
        self._monotonic = monotonic_fn or time.monotonic
        # Insertion-ordered so eviction drops the least recently *updated* type.
        self._entries: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    def remember(self, event: dict[str, Any]) -> bool:
        """Record an event if it is an ambient one. Returns whether it was kept.

        Called on the bridge's hot path, so it must not raise on a surprising shape — a
        payload that got this far is already schema-valid, but a missing ``type`` should
        cost a skip, not an exception that unwinds the bridge's read loop.
        """
        event_type = event.get("type")
        if not isinstance(event_type, str) or not event_type.startswith("ambient."):
            return False
        self._entries.pop(event_type, None)
        self._entries[event_type] = (self._monotonic(), event)
        while len(self._entries) > self._max_types:
            self._entries.popitem(last=False)
        return True

    def replay(self) -> list[dict[str, Any]]:
        """The events a newly connected client should be told about, oldest first.

        Oldest first so that if two types ever arrive out of order the client applies them
        in the same sequence a live listener would have.
        """
        self._expire()
        return [event for _, event in self._entries.values()]

    def _expire(self) -> None:
        cutoff = self._monotonic() - self._max_age_s
        for event_type in [t for t, (at, _) in self._entries.items() if at < cutoff]:
            del self._entries[event_type]

    def forget_all(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        self._expire()
        return len(self._entries)
