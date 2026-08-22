"""Decides whether a recognised person may be greeted *aloud* right now.

Pure and side-effect free on purpose: this is the whole privacy surface of
ADR-0034, so it is worth being able to test every refusal without a speaker, a
clock, or a subprocess.

The refusals matter more than the greetings. A screen a foot away shows a name to
the person being greeted; a speaker tells everyone in a shared corridor. So the
default answer is no, and each yes has to be earned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as dtime


def parse_quiet_hours(raw: str) -> tuple[dtime, dtime] | None:
    """Parse ``"22:00-08:00"``. Returns None when quiet hours are disabled.

    An unparseable value is a configuration mistake whose safe reading is "be
    quiet", not "announce all night", so it raises rather than silently
    disabling the window.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        start_text, end_text = text.split("-", 1)
        start = dtime.fromisoformat(start_text.strip())
        end = dtime.fromisoformat(end_text.strip())
    except ValueError as exc:
        msg = f"VOICE_GREETING_QUIET_HOURS must look like '22:00-08:00', got {raw!r}"
        raise ValueError(msg) from exc
    return start, end


def in_quiet_hours(now: dtime, window: tuple[dtime, dtime] | None) -> bool:
    """Is ``now`` inside the window? Handles windows that wrap midnight."""
    if window is None:
        return False
    start, end = window
    if start == end:
        # A zero-width window would otherwise read as "never quiet"; the more
        # defensible reading of start==end is "always quiet".
        return True
    if start < end:
        return start <= now < end
    # Wraps midnight, e.g. 22:00-08:00.
    return now >= start or now < end


@dataclass
class GreetingPolicy:
    """Gates spoken greetings. One instance per process.

    ``last_spoken`` is monotonic seconds keyed by person_id, so a clock change
    can't unlock an early re-announcement.
    """

    enabled: bool
    allowed_person_ids: frozenset[str]
    cooldown_s: float
    quiet_hours: tuple[dtime, dtime] | None
    last_spoken: dict[str, float] = field(default_factory=dict)

    def refusal_reason(
        self,
        *,
        person_id: str | None,
        display_name: str | None,
        now_monotonic: float,
        now_local_time: dtime,
    ) -> str | None:
        """None means "go ahead"; otherwise a short reason, for logging.

        Returning the reason rather than a bool makes the logs say *why* the door
        stayed quiet, which is the difference between "working as configured" and
        "broken" when someone asks why it didn't greet them.
        """
        if not self.enabled:
            return "feature_disabled"
        if not person_id:
            # An unrecognised visitor has no name to say and never consented.
            return "no_person_id"
        if not display_name or not display_name.strip():
            return "no_display_name"
        if person_id not in self.allowed_person_ids:
            # Opt-in per person (ADR-0034): enrolment consent covers the screen,
            # not the corridor.
            return "not_opted_in"
        if in_quiet_hours(now_local_time, self.quiet_hours):
            return "quiet_hours"
        previous = self.last_spoken.get(person_id)
        if previous is not None and (now_monotonic - previous) < self.cooldown_s:
            return "cooldown"
        return None

    def record_spoken(self, person_id: str, now_monotonic: float) -> None:
        self.last_spoken[person_id] = now_monotonic

    def forget_person(self, person_id: str) -> None:
        """Drop a person's cooldown state — used on unenrolment (ADR-0009)."""
        self.last_spoken.pop(person_id, None)
