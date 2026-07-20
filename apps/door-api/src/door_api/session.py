"""Visitor session state machine — the spine of the visitor experience.

Owns the normative transition table from ``packages/contracts``, validates
every transition, emits ``session.state_changed``/``session.started``/
``session.ended`` events, manages expiry timers, and persists state to SQLite
for restart-resilience.

Design decisions
----------------
- **No network dependency on the critical path.** ``door.button_pressed`` →
  ``VISITOR_MODE`` is entirely local: no HTTP, no MQTT, no awaits on anything
  remote.
- **Monotonic time for all expiry/duration math.** Wall-clock is used only
  in the emitted events' ``occurred_at`` field.
- **Illegal transitions are side-effect-free.** They are counted in metrics
  and logged, but never modify state.
- **Auto-expiry timers** are reconstructed on restart from persisted
  ``last_transition_monotonic_ms`` + configured timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from doorboard_contracts import LEGAL_SESSION_TRANSITIONS, SessionState
from doorboard_contracts.events import (
    SessionEndedEvent,
    SessionEndedPayload,
    SessionStartedEvent,
    SessionStartedPayload,
    SessionStateChangedEvent,
    SessionStateChangedPayload,
)
from doorboard_esp32_link.esp32 import uuid7_now

from door_api.config import SessionConfig
from door_api.persistence import PersistedSession, SessionStore

logger = logging.getLogger("door-api.session")


def _system_boot_id() -> str:
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if boot_id:
            return boot_id
    except OSError:
        pass
    boot_epoch_s = round((time.time_ns() - time.monotonic_ns()) / 1_000_000_000)
    return f"boot-epoch-{boot_epoch_s}"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# Callback invoked on every transition with the state-changed payload dict.
# The owner (FastAPI app) wires this to WebSocket broadcast + event emission.
type TransitionCallback = Callable[[dict[str, Any]], None]


@dataclass
class SessionSnapshot:
    """A point-in-time snapshot of the session, safe to send over WebSocket."""

    session_id: UUID | None
    state: SessionState
    person_id: str | None
    display_name: str | None
    profile_id: str | None
    had_cached_profile: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": str(self.session_id) if self.session_id else None,
            "state": self.state.value,
            "person_id": self.person_id,
            "display_name": self.display_name,
            "profile_id": self.profile_id,
            "had_cached_profile": self.had_cached_profile,
        }


class IllegalTransitionError(Exception):
    """Raised (and caught internally) when a transition is not in the legal table."""

    def __init__(self, from_state: SessionState, to_state: SessionState, trigger: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.trigger = trigger
        super().__init__(
            f"illegal transition {from_state.value} → {to_state.value} (trigger: {trigger})"
        )


# ---------------------------------------------------------------------------
# Metrics — simple counters for GET /metrics
# ---------------------------------------------------------------------------


@dataclass
class SessionMetrics:
    transitions: int = 0
    illegal_transitions: int = 0
    sessions_started: int = 0
    sessions_ended: int = 0
    timer_fires: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "session_transitions_total": self.transitions,
            "session_illegal_transitions_total": self.illegal_transitions,
            "session_sessions_started_total": self.sessions_started,
            "session_sessions_ended_total": self.sessions_ended,
            "session_timer_fires_total": self.timer_fires,
        }


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


@dataclass
class _TimerState:
    """Tracks the active auto-expiry timer task."""

    task: asyncio.Task[None] | None = None
    target_state: SessionState | None = None
    trigger: str = ""


@dataclass
class SessionMachine:
    """The visitor session state machine.

    Thread-safety: this class is designed to run on a single asyncio event loop.
    All public methods must be called from the same loop.
    """

    config: SessionConfig
    store: SessionStore
    on_event: TransitionCallback
    metrics: SessionMetrics = field(default_factory=SessionMetrics)

    # Internal state — loaded from persistence or defaults.
    _state: SessionState = field(default=SessionState.IDLE, init=False)
    _session_id: UUID | None = field(default=None, init=False)
    _trace_id: UUID | None = field(default=None, init=False)
    _person_id: str | None = field(default=None, init=False)
    _display_name: str | None = field(default=None, init=False)
    _profile_id: str | None = field(default=None, init=False)
    _had_cached_profile: bool = field(default=False, init=False)
    # Whether this session was ever answered. Distinguishes a genuine missed
    # bell (rung, never answered) from an answered session that idled out at the
    # video-message offer — both can reach VIDEO_MESSAGE_OFFERED. See
    # ``_outcome_for_end``.
    _was_answered: bool = field(default=False, init=False)
    _started_at_mono_ms: int = field(default=0, init=False)
    _last_transition_mono_ms: int = field(default=0, init=False)
    _timer: _TimerState = field(default_factory=_TimerState, init=False)
    _monotonic_ms_fn: Callable[[], int] = field(init=False)
    _boot_id_fn: Callable[[], str] = field(init=False)

    def __post_init__(self) -> None:
        self._monotonic_ms_fn = lambda: int(time.monotonic() * 1000)
        self._boot_id_fn = _system_boot_id

    # ---------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------

    def set_monotonic_fn(self, fn: Callable[[], int]) -> None:
        """Override the monotonic clock source (for testing / simulation)."""
        self._monotonic_ms_fn = fn

    def set_boot_id_fn(self, fn: Callable[[], str]) -> None:
        """Override the OS boot identity source for tests."""
        self._boot_id_fn = fn

    def restore_from_persistence(self) -> None:
        """Load persisted state and reconstruct timers. Call once at startup."""
        persisted = self.store.load()
        if persisted is None:
            self._state = SessionState.IDLE
            self._session_id = None
            logger.info(
                json.dumps(
                    {
                        "service": "door-api",
                        "event_id": "session_restore",
                        "detail": "no persisted session, starting IDLE",
                    }
                )
            )
            return

        self._state = persisted.state
        self._session_id = persisted.session_id
        self._trace_id = persisted.trace_id
        self._person_id = persisted.person_id
        self._display_name = persisted.display_name
        self._profile_id = persisted.profile_id
        self._started_at_mono_ms = persisted.started_at_monotonic_ms
        self._last_transition_mono_ms = persisted.last_transition_monotonic_ms
        meta: dict[str, object] = {}
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            loaded_meta = json.loads(persisted.meta_json)
            if isinstance(loaded_meta, dict):
                meta = loaded_meta
                self._had_cached_profile = bool(meta.get("had_cached_profile", False))
                self._was_answered = bool(meta.get("was_answered", False))

        now_ms = self._monotonic_ms_fn()
        persisted_boot_id = meta.get("boot_id")
        boot_changed = (
            isinstance(persisted_boot_id, str) and persisted_boot_id != self._boot_id_fn()
        )
        monotonic_reset = now_ms < persisted.last_transition_monotonic_ms
        if boot_changed or monotonic_reset:
            logger.warning(
                "session_expired_after_boot_epoch_change",
                extra={
                    "session_id": str(persisted.session_id),
                    "boot_changed": boot_changed,
                    "monotonic_reset": monotonic_reset,
                },
            )
            self._expire_to_idle("boot_epoch_changed_on_restore")
            return
        elapsed_s = (now_ms - persisted.last_transition_monotonic_ms) / 1000.0

        logger.info(
            json.dumps(
                {
                    "service": "door-api",
                    "event_id": "session_restore",
                    "session_id": str(persisted.session_id),
                    "state": persisted.state.value,
                    "elapsed_since_last_s": round(elapsed_s, 2),
                }
            )
        )

        # If inactivity timeout has already elapsed, expire to IDLE synchronously.
        if self._state != SessionState.IDLE and elapsed_s >= self.config.inactivity_timeout_s:
            logger.info(
                json.dumps(
                    {
                        "service": "door-api",
                        "event_id": "session_expired_on_restore",
                        "session_id": str(persisted.session_id),
                    }
                )
            )
            self._expire_to_idle("inactivity_expired_on_restore")
            return

        # Otherwise, schedule the remaining time for the current state's timer.
        self._schedule_timer_for_state(self._state, elapsed_s)

    def snapshot(self) -> SessionSnapshot:
        """Return the current session state as a snapshot for WebSocket."""
        return SessionSnapshot(
            session_id=self._session_id,
            state=self._state,
            person_id=self._person_id,
            display_name=self._display_name,
            profile_id=self._profile_id,
            had_cached_profile=self._had_cached_profile,
        )

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def session_id(self) -> UUID | None:
        return self._session_id

    # ---------------------------------------------------------------------------
    # Transition logic
    # ---------------------------------------------------------------------------

    def transition(
        self,
        to_state: SessionState,
        trigger: str,
        *,
        trace_id: UUID | None = None,
        person_id: str | None = None,
        display_name: str | None = None,
        profile_id: str | None = None,
        had_cached_profile: bool | None = None,
        session_entry: Literal["button", "touch", "approach"] | None = None,
    ) -> bool:
        """Attempt a state transition.

        Returns True if the transition was executed, False if it was illegal.
        Illegal transitions are side-effect-free: no state change, no event,
        no persistence write. They are counted and logged.
        """
        from_state = self._state

        # Validate against the contracts transition table.
        legal_targets = LEGAL_SESSION_TRANSITIONS.get(from_state, ())
        if to_state not in legal_targets:
            self.metrics.illegal_transitions += 1
            logger.warning(
                json.dumps(
                    {
                        "service": "door-api",
                        "event_id": "illegal_transition",
                        "from_state": from_state.value,
                        "to_state": to_state.value,
                        "trigger": trigger,
                    }
                )
            )
            return False

        now_ms = self._monotonic_ms_fn()

        # Cancel any pending timer.
        self._cancel_timer()

        # Starting a new session?
        if from_state == SessionState.IDLE:
            self._session_id = uuid4()
            self._trace_id = trace_id or uuid4()
            self._started_at_mono_ms = now_ms
            self._person_id = person_id
            self._display_name = display_name
            self._profile_id = profile_id
            self._had_cached_profile = bool(had_cached_profile)
            self._was_answered = False
        else:
            # Allow identity to be updated (e.g., late recognition during APPROACH_DETECTED).
            if person_id is not None:
                self._person_id = person_id
            if display_name is not None:
                self._display_name = display_name
            if profile_id is not None:
                self._profile_id = profile_id
            if to_state == SessionState.BUTTON_PRESSED and had_cached_profile is not None:
                self._had_cached_profile = had_cached_profile

        self._state = to_state
        if to_state == SessionState.ANSWERED:
            self._was_answered = True
        self._last_transition_mono_ms = now_ms
        self.metrics.transitions += 1

        # Emit session.state_changed event.
        assert self._session_id is not None
        assert self._trace_id is not None

        state_event = SessionStateChangedEvent(
            event_id=uuid7_now(),
            type="session.state_changed",
            source="door-api",
            occurred_at=datetime.now(UTC),
            monotonic_ms=now_ms,
            door_id=self.config.door_id,
            trace_id=self._trace_id,
            payload=SessionStateChangedPayload(
                session_id=self._session_id,
                from_state=from_state,
                to_state=to_state,
                trigger=trigger,
            ),
        )
        state_event_dict = state_event.model_dump(mode="json")
        sync_events = [state_event_dict]
        self.on_event(state_event_dict)

        # Emit session.started on first non-IDLE transition.
        if from_state == SessionState.IDLE:
            self.metrics.sessions_started += 1
            entry = session_entry or (
                "button" if to_state == SessionState.BUTTON_PRESSED else "approach"
            )
            started_event = SessionStartedEvent(
                event_id=uuid7_now(),
                type="session.started",
                source="door-api",
                occurred_at=datetime.now(UTC),
                monotonic_ms=now_ms,
                door_id=self.config.door_id,
                trace_id=self._trace_id,
                payload=SessionStartedPayload(
                    session_id=self._session_id,
                    entry=entry,
                ),
            )
            started_event_dict = started_event.model_dump(mode="json")
            sync_events.append(started_event_dict)
            self.on_event(started_event_dict)

        # Emit session.ended on transition to SESSION_END, or direct to IDLE from active.
        if to_state == SessionState.SESSION_END or (
            to_state == SessionState.IDLE
            and from_state not in (SessionState.IDLE, SessionState.SESSION_END)
        ):
            self.metrics.sessions_ended += 1
            outcome = self._outcome_for_end(from_state, trigger)
            ended_event = SessionEndedEvent(
                event_id=uuid7_now(),
                type="session.ended",
                source="door-api",
                occurred_at=datetime.now(UTC),
                monotonic_ms=now_ms,
                door_id=self.config.door_id,
                trace_id=self._trace_id,
                payload=SessionEndedPayload(
                    session_id=self._session_id,
                    outcome=outcome,
                ),
            )
            ended_event_dict = ended_event.model_dump(mode="json")
            sync_events.append(ended_event_dict)
            self.on_event(ended_event_dict)

        # Persist.
        if to_state == SessionState.IDLE:
            media_dropped, sync_dropped = self.store.clear_with_delivery_events(
                media_event=state_event_dict,
                sync_events=sync_events,
            )
            self._session_id = None
            self._trace_id = None
            self._person_id = None
            self._display_name = None
            self._profile_id = None
            self._had_cached_profile = False
            self._was_answered = False
        else:
            # Persist SESSION_END as well so restart can finish its transition.
            media_dropped, sync_dropped = self._persist(
                media_event=state_event_dict,
                sync_events=sync_events,
            )

        if media_dropped:
            logger.error("media_outbox_capacity_drop", extra={"dropped": media_dropped})
        if sync_dropped:
            logger.error("sync_outbox_capacity_drop", extra={"dropped": sync_dropped})

        # Schedule auto-transition timer for the new state.
        self._schedule_timer_for_state(to_state)

        return True

    # ---------------------------------------------------------------------------
    # Event handlers — wired to incoming events from simulator/real hardware
    # ---------------------------------------------------------------------------

    def handle_button_pressed(
        self,
        *,
        trace_id: UUID | None = None,
        trigger: str = "door.button_pressed",
        entry: Literal["button", "touch"] = "button",
        had_cached_profile: bool | None = None,
        profile_id: str | None = None,
    ) -> bool:
        """Handle a ``door.button_pressed`` event.

        From IDLE: starts a new session with BUTTON_PRESSED, then immediately
        transitions to VISITOR_MODE (no network, no awaits).
        From APPROACH_DETECTED/IDENTITY_CACHED: transitions to BUTTON_PRESSED,
        then immediately to VISITOR_MODE.
        From any other state: ignored (double-press during active session).
        """
        current = self._state

        # Only IDLE, APPROACH_DETECTED, IDENTITY_CACHED can transition to BUTTON_PRESSED.
        if current not in (
            SessionState.IDLE,
            SessionState.APPROACH_DETECTED,
            SessionState.IDENTITY_CACHED,
        ):
            # Double-press during active session — log and ignore.
            logger.info(
                json.dumps(
                    {
                        "service": "door-api",
                        "event_id": "button_press_ignored",
                        "state": current.value,
                        "reason": "already in active session",
                    }
                )
            )
            return False

        cached = (
            had_cached_profile
            if had_cached_profile is not None
            else current in (SessionState.APPROACH_DETECTED, SessionState.IDENTITY_CACHED)
            and self._profile_id is not None
        )
        button_profile_id = profile_id if cached else None

        # Transition to BUTTON_PRESSED.
        ok = self.transition(
            SessionState.BUTTON_PRESSED,
            trigger,
            trace_id=trace_id,
            session_entry=entry,
            profile_id=button_profile_id,
            had_cached_profile=cached,
        )
        if not ok:
            return False  # pragma: no cover — should not happen given the guard above

        # Immediately transition to VISITOR_MODE (the critical path — must be local and instant).
        return self.transition(SessionState.VISITOR_MODE, "auto:button_to_visitor")

    def handle_identity_stable(
        self,
        *,
        person_id: str,
        display_name: str,
        profile_id: str,
        trace_id: UUID | None = None,
    ) -> bool:
        """Handle a ``vision.identity_stable`` event."""
        current = self._state

        if current == SessionState.IDLE:
            return self.transition(
                SessionState.APPROACH_DETECTED,
                "vision.identity_stable",
                trace_id=trace_id,
                person_id=person_id,
                display_name=display_name,
                profile_id=profile_id,
            )

        if current == SessionState.APPROACH_DETECTED:
            return self.transition(
                SessionState.IDENTITY_CACHED,
                "vision.identity_stable",
                person_id=person_id,
                display_name=display_name,
                profile_id=profile_id,
            )

        # During an active session, identity updates are noted but don't
        # cause a state transition.
        if person_id is not None:
            self._person_id = person_id
        if display_name is not None:
            self._display_name = display_name
        if profile_id is not None:
            self._profile_id = profile_id

        self._persist()
        return False

    def handle_identity_expired(self, *, person_id: str) -> bool:
        """Handle a ``vision.identity_expired`` event."""
        current = self._state

        # IDENTITY_CACHED/APPROACH_DETECTED can revert toward IDLE.
        if current == SessionState.IDENTITY_CACHED and self._person_id == person_id:
            return self.transition(
                SessionState.APPROACH_DETECTED,
                "vision.identity_expired",
            )
        if current == SessionState.APPROACH_DETECTED and self._person_id == person_id:
            return self.transition(SessionState.IDLE, "vision.identity_expired")

        return False

    def handle_contact_changed(self, *, state: str) -> bool:
        """Handle a ``door.contact_changed`` event (door opened → ANSWERED)."""
        if state == "open" and self._state == SessionState.RINGING:
            return self.transition(SessionState.ANSWERED, "door.contact_changed:open")
        return False

    def handle_answered(self, *, trigger: str = "owner_action") -> bool:
        """Handle an explicit answer action (e.g., from doorpad UI)."""
        if self._state == SessionState.RINGING:
            return self.transition(SessionState.ANSWERED, trigger)
        return False

    def handle_unanswered(self, *, trigger: str = "owner:cannot_answer") -> bool:
        """Move an active ring to the existing unanswered visitor path."""
        if self._state == SessionState.RINGING:
            return self.transition(SessionState.UNANSWERED_TIMEOUT, trigger)
        return False

    def handle_session_end(self, *, trigger: str) -> bool:
        """End an active session without inventing a new state or event shape."""
        if self._state == SessionState.IDLE:
            return False
        if self._state == SessionState.SESSION_END:
            return self.transition(SessionState.IDLE, trigger)
        return self.transition(SessionState.SESSION_END, trigger)

    def handle_video_message_offer(
        self,
        *,
        trace_id: UUID | None = None,
        trigger: str = "doorpad.video_message_offer",
    ) -> bool:
        """Move the active session to VIDEO_MESSAGE_OFFERED for the DoorPad flow.

        The contracts table has no direct IDLE -> VIDEO_MESSAGE_OFFERED edge, so a
        visitor-initiated message starts a touch session and advances through the
        legal local states without waiting on the ring timeout.
        """
        changed = False

        if self._state in (
            SessionState.IDLE,
            SessionState.APPROACH_DETECTED,
            SessionState.IDENTITY_CACHED,
        ):
            changed = (
                self.handle_button_pressed(
                    trace_id=trace_id,
                    trigger=trigger,
                    entry="touch",
                )
                or changed
            )

        if self._state == SessionState.BUTTON_PRESSED:
            changed = (
                self.transition(
                    SessionState.VISITOR_MODE,
                    "auto:touch_to_visitor",
                )
                or changed
            )

        if self._state == SessionState.VISITOR_MODE:
            changed = (
                self.transition(
                    SessionState.RINGING,
                    "doorpad:video_offer_ring_skip",
                )
                or changed
            )

        if self._state == SessionState.RINGING:
            changed = (
                self.transition(
                    SessionState.UNANSWERED_TIMEOUT,
                    "doorpad:video_offer_unanswered",
                )
                or changed
            )

        if self._state in (SessionState.ANSWERED, SessionState.UNANSWERED_TIMEOUT):
            changed = (
                self.transition(
                    SessionState.VIDEO_MESSAGE_OFFERED,
                    "doorpad:video_offer",
                )
                or changed
            )

        if self._state == SessionState.VIDEO_MESSAGE_OFFERED:
            return True
        return changed

    def handle_video_message_start(self) -> bool:
        """Visitor chose to record a video message."""
        if self._state == SessionState.VIDEO_MESSAGE_REVIEW:
            return self.transition(
                SessionState.VIDEO_MESSAGE_RECORDING,
                "visitor:rerecord",
            )
        if self._state == SessionState.VIDEO_MESSAGE_OFFERED:
            return self.transition(
                SessionState.VIDEO_MESSAGE_RECORDING,
                "visitor:record_start",
            )
        return False

    def handle_video_message_stop(self) -> bool:
        """Visitor stopped recording."""
        if self._state == SessionState.VIDEO_MESSAGE_RECORDING:
            return self.transition(
                SessionState.VIDEO_MESSAGE_REVIEW,
                "visitor:record_stop",
            )
        return False

    def handle_video_message_rerecord(self) -> bool:
        """Visitor chose to re-record."""
        if self._state == SessionState.VIDEO_MESSAGE_REVIEW:
            return self.transition(
                SessionState.VIDEO_MESSAGE_RECORDING,
                "visitor:rerecord",
            )
        return False

    def handle_video_message_save(self) -> bool:
        """Visitor confirmed the video message."""
        if self._state == SessionState.VIDEO_MESSAGE_REVIEW:
            return self.transition(
                SessionState.VIDEO_MESSAGE_SAVED,
                "visitor:save",
            )
        return False

    def handle_video_message_discard(self) -> bool:
        """Visitor discarded the video message."""
        if self._state in (
            SessionState.VIDEO_MESSAGE_REVIEW,
            SessionState.VIDEO_MESSAGE_RECORDING,
            SessionState.VIDEO_MESSAGE_OFFERED,
        ):
            return self.transition(SessionState.SESSION_END, "visitor:discard")
        return False

    def handle_admin_reset(self) -> bool:
        """Admin-initiated session reset."""
        if self._state == SessionState.IDLE:
            return False
        if self._state == SessionState.SESSION_END:
            return self.transition(SessionState.IDLE, "admin:reset")
        return self.transition(SessionState.SESSION_END, "admin:reset")

    # ---------------------------------------------------------------------------
    # Auto-expiry timer management
    # ---------------------------------------------------------------------------

    def _schedule_timer_for_state(
        self,
        state: SessionState,
        already_elapsed_s: float = 0.0,
    ) -> None:
        """Schedule the appropriate auto-transition timer for the given state."""
        timeout_s: float | None = None
        target: SessionState | None = None
        trigger: str = ""

        if state == SessionState.APPROACH_DETECTED:
            timeout_s = self.config.approach_timeout_s
            target = SessionState.IDLE
            trigger = "timeout:approach"
        elif state == SessionState.IDENTITY_CACHED:
            timeout_s = self.config.approach_timeout_s
            target = SessionState.IDLE
            trigger = "timeout:identity_cached"
        elif state == SessionState.VISITOR_MODE:
            timeout_s = self.config.visitor_mode_auto_ring_s
            target = SessionState.RINGING
            trigger = "auto:visitor_mode_ring"
        elif state == SessionState.RINGING:
            timeout_s = self.config.ring_timeout_s
            target = SessionState.UNANSWERED_TIMEOUT
            trigger = "timeout:ring"
        elif state == SessionState.ANSWERED:
            timeout_s = self.config.offer_delay_s
            target = SessionState.SESSION_END
            trigger = "auto:answered_to_end"
        elif state == SessionState.UNANSWERED_TIMEOUT:
            timeout_s = self.config.offer_delay_s
            target = SessionState.VIDEO_MESSAGE_OFFERED
            trigger = "auto:unanswered_to_offer"
        elif state == SessionState.VIDEO_MESSAGE_RECORDING:
            timeout_s = self.config.max_recording_s
            target = SessionState.VIDEO_MESSAGE_REVIEW
            trigger = "timeout:max_recording"
        elif state == SessionState.VIDEO_MESSAGE_REVIEW:
            timeout_s = self.config.review_timeout_s
            target = SessionState.SESSION_END
            trigger = "timeout:review"
        elif state == SessionState.VIDEO_MESSAGE_SAVED:
            timeout_s = self.config.saved_linger_s
            target = SessionState.SESSION_END
            trigger = "auto:saved_to_end"
        elif state == SessionState.SESSION_END:
            timeout_s = self.config.session_end_linger_s
            target = SessionState.IDLE
            trigger = "auto:end_to_idle"
        elif state in (SessionState.BUTTON_PRESSED, SessionState.VIDEO_MESSAGE_OFFERED):
            # BUTTON_PRESSED transitions immediately to VISITOR_MODE in handle_button_pressed.
            # VIDEO_MESSAGE_OFFERED waits for visitor action; has inactivity fallback.
            pass

        if timeout_s is not None and target is not None:
            remaining_s = max(0.0, timeout_s - already_elapsed_s)
            self._start_timer(remaining_s, target, trigger)
        elif state not in (SessionState.IDLE,):
            # Schedule the global inactivity timeout as a fallback
            # for states without a specific timer.
            remaining_s = max(0.0, self.config.inactivity_timeout_s - already_elapsed_s)
            if state == SessionState.VIDEO_MESSAGE_OFFERED:
                self._start_timer(
                    remaining_s,
                    SessionState.SESSION_END,
                    "timeout:inactivity",
                )

    def _start_timer(self, delay_s: float, target: SessionState, trigger: str) -> None:
        """Start a timer that will auto-transition after delay_s seconds."""
        self._cancel_timer()
        self._timer.target_state = target
        self._timer.trigger = trigger

        async def _fire() -> None:
            await asyncio.sleep(delay_s)
            self.metrics.timer_fires += 1
            self.transition(target, trigger)

        try:
            loop = asyncio.get_running_loop()
            self._timer.task = loop.create_task(_fire(), name=f"session-timer-{trigger}")
        except RuntimeError:
            # No running event loop (e.g., sync tests). Timer not scheduled.
            pass

    def _cancel_timer(self) -> None:
        """Cancel the current auto-transition timer if one is pending."""
        if self._timer.task is not None and not self._timer.task.done():
            self._timer.task.cancel()
        self._timer.task = None
        self._timer.target_state = None
        self._timer.trigger = ""

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _persist(
        self,
        *,
        media_event: dict[str, Any] | None = None,
        sync_events: list[dict[str, Any]] | None = None,
    ) -> tuple[int, int]:
        """Write current state to SQLite."""
        assert self._session_id is not None
        assert self._trace_id is not None
        persisted = PersistedSession(
            session_id=self._session_id,
            state=self._state,
            trace_id=self._trace_id,
            person_id=self._person_id,
            display_name=self._display_name,
            profile_id=self._profile_id,
            started_at_monotonic_ms=self._started_at_mono_ms,
            last_transition_monotonic_ms=self._last_transition_mono_ms,
            meta_json=json.dumps(
                {
                    "timer_target": (
                        self._timer.target_state.value if self._timer.target_state else None
                    ),
                    "timer_trigger": self._timer.trigger or None,
                    "had_cached_profile": self._had_cached_profile,
                    "was_answered": self._was_answered,
                    "boot_id": self._boot_id_fn(),
                }
            ),
        )
        if media_event is None:
            self.store.save(persisted)
            return 0, 0
        return self.store.save_with_delivery_events(
            persisted,
            media_event=media_event,
            sync_events=sync_events or [],
        )

    def _expire_to_idle(self, trigger: str) -> None:
        """Force-expire the session to IDLE (used on restore when inactivity exceeded)."""
        if self._state == SessionState.SESSION_END:
            self.transition(SessionState.IDLE, trigger)
        elif self._state != SessionState.IDLE:
            # Two-step: current → SESSION_END → IDLE
            self.transition(SessionState.SESSION_END, trigger)
            self.transition(SessionState.IDLE, f"auto:{trigger}_to_idle")

    _SessionOutcome = Literal[
        "answered",
        "unanswered_timeout",
        "message_left",
        "abandoned",
        "reset",
    ]

    @staticmethod
    def _outcome_for_trigger(trigger: str) -> SessionMachine._SessionOutcome:
        """Map a trigger string to a session.ended outcome."""
        mapping: dict[str, SessionMachine._SessionOutcome] = {
            "auto:saved_to_end": "message_left",
            "admin:reset": "reset",
            "timeout:ring": "unanswered_timeout",
            "door.answered": "answered",
            "door.contact_changed": "answered",
            "owner:answered": "answered",
            "auto:answered_to_end": "answered",
            "visitor:end": "abandoned",
            "visitor:discard": "abandoned",
            "timeout:review": "abandoned",
            "timeout:max_recording": "abandoned",
            "timeout:inactivity": "abandoned",
            "vision.identity_expired": "abandoned",
        }
        if trigger in mapping:
            return mapping[trigger]

        logger.warning(
            json.dumps(
                {
                    "service": "door-api",
                    "event_id": "unknown_session_end_trigger",
                    "trigger": trigger,
                }
            )
        )
        return "abandoned"

    def _outcome_for_end(
        self, from_state: SessionState, trigger: str
    ) -> SessionMachine._SessionOutcome:
        """Resolve the ``session.ended`` outcome, accounting for a missed bell.

        A bell that is rung but never answered flows
        ``RINGING → UNANSWERED_TIMEOUT → VIDEO_MESSAGE_OFFERED`` and, if the
        visitor leaves no message, ends via the *silent* inactivity fallback
        (``timeout:inactivity``). That is a missed bell, so it must report
        ``unanswered_timeout`` — the outcome the control-plane ``missed_bell``
        notify rule keys on — rather than the generic ``abandoned``.

        ``VIDEO_MESSAGE_OFFERED`` is also reachable from ``ANSWERED`` via the
        DoorPad ``doorpad:video_offer`` flow, so this is gated on
        ``_was_answered``: an answered session that idles out at the offer is not
        a missed bell and keeps ``abandoned``. An actively declined/ended offer
        (``visitor:discard``/``visitor:end``) and a saved message
        (``auto:saved_to_end``) use different triggers and are unaffected. A
        message can never have been saved on this path — saving happens only
        later, from ``VIDEO_MESSAGE_REVIEW`` — so "no message" is guaranteed.
        """
        if (
            from_state == SessionState.VIDEO_MESSAGE_OFFERED
            and trigger == "timeout:inactivity"
            and not self._was_answered
        ):
            return "unanswered_timeout"
        return self._outcome_for_trigger(trigger)

    def close(self) -> None:
        """Cancel timers and close the store."""
        self._cancel_timer()
        self.store.close()
