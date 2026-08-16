"""Who the door currently believes it is talking to, for the length of one interaction.

ARCHITECTURE.md §5's identity cache is 2.5 s and door-api's approach session expires
after 10 s. Both are correct for what they were built for: the cache exists so a bell
press gets a personalised effect with no recognition latency, and the approach timer
exists so an empty doorway returns to ambient.

Neither survives a person *using* the doorpad. Recognition lands, the session goes
`APPROACH_DETECTED`, and ten seconds later it is `IDLE` with the name cleared — so by
the time someone has read the screen, tapped Check In and reached the buttons, the door
has forgotten them. The observable bug was "it greeted me and then still said only
recognised visitors can check in as themselves", which reads as recognition failing when
in fact it succeeded and then timed out.

This holder is that missing lifetime, and it is deliberately *activity*-scoped rather
than simply longer (ADR-0020):

- Recognised, nobody touches anything: expires on ``idle_ttl_s``, the old behaviour. A
  passer-by does not leave their name sitting in memory.
- Recognised, then the doorpad is used: every touch re-arms ``interaction_ttl_s``, so the
  identity lasts as long as the interaction and no longer.

It holds an identity *reference* — ``person_id``, display name, consent version, profile
— never an embedding or a frame, so nothing here is biometric data under ADR-0009. It is
memory-only by design: a recognised identity must not survive a restart, because the
person it names may have left (ADR-0020 §"Consequences").

Personalisation, never authorisation (ADR-0005 §3): every consumer of this uses it to
choose a greeting, an accent colour, or a name to attribute a voluntary write to. No
caller may reach an access decision, and ADR-0009 P-11 continues to enforce that.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RecognisedPerson:
    person_id: str
    display_name: str
    consent_version: str | None
    profile_id: str | None


class RecognisedIdentity:
    """The recognised person, held for the length of the interaction they are having."""

    def __init__(
        self,
        *,
        idle_ttl_s: float,
        interaction_ttl_s: float,
        monotonic_fn: Callable[[], float] | None = None,
    ) -> None:
        self._idle_ttl_s = idle_ttl_s
        self._interaction_ttl_s = interaction_ttl_s
        self._monotonic = monotonic_fn or time.monotonic
        self._person: RecognisedPerson | None = None
        self._expires_at: float = 0.0

    def remember(
        self,
        *,
        person_id: str,
        display_name: str,
        consent_version: str | None,
        profile_id: str | None,
    ) -> None:
        """Record a freshly recognised person, or refresh the one already held.

        A *different* person arriving replaces the held identity outright rather than
        extending it: two people at the door means the newest match is the better guess,
        and merging them would attribute one person's writes to the other.
        """
        previous = self.current()
        self._person = RecognisedPerson(
            person_id=person_id,
            display_name=display_name,
            consent_version=consent_version,
            profile_id=profile_id,
        )
        # Someone mid-interaction keeps their longer window when re-recognised; a new
        # arrival starts on the short one until they actually touch something.
        if previous is not None and previous.person_id == person_id:
            self._expires_at = max(self._expires_at, self._monotonic() + self._idle_ttl_s)
        else:
            self._expires_at = self._monotonic() + self._idle_ttl_s

    def touch(self) -> None:
        """Re-arm the interaction window: somebody is using the doorpad right now.

        Only extends an identity that is still live. A touch can never resurrect an
        expired one, or the first tap after a long gap would silently re-attribute the
        session to whoever was last seen.
        """
        if self.current() is None:
            return
        self._expires_at = max(self._expires_at, self._monotonic() + self._interaction_ttl_s)

    def current(self) -> RecognisedPerson | None:
        if self._person is None:
            return None
        if self._monotonic() >= self._expires_at:
            self._person = None
            self._expires_at = 0.0
            return None
        return self._person

    def forget(self) -> None:
        """Drop the identity now — privacy mode, unenrollment, or session end."""
        self._person = None
        self._expires_at = 0.0

    def forget_person(self, person_id: str) -> bool:
        """Drop the identity only if it names *person_id* (unenrollment propagation)."""
        held = self.current()
        if held is not None and held.person_id == person_id:
            self.forget()
            return True
        return False

    def seconds_remaining(self) -> float:
        """How much longer the identity is held, for /health and the UI countdown."""
        if self.current() is None:
            return 0.0
        return max(0.0, self._expires_at - self._monotonic())
