"""Table-driven transition tests for the visitor session state machine.

Coverage targets from the T-401 brief:
- Every legal transition edge exercised.
- Every illegal transition attempt proven side-effect-free.
- Restart-mid-session (kill -9 → restore).
- Kiosk reload-rejoin (snapshot-on-connect).
- Double-press during active session.
- Button press during recording.
- Timeout races.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from door_api.broadcast import DisplayBroadcast
from door_api.config import SessionConfig
from door_api.persistence import SessionStore
from door_api.session import SessionMachine
from doorboard_contracts import LEGAL_SESSION_TRANSITIONS, SessionState

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class EventCollector:
    """Collects events emitted by the state machine."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def clear(self) -> None:
        self.events.clear()

    def last(self) -> dict[str, Any]:
        assert self.events, "no events emitted"
        return self.events[-1]

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == event_type]


def make_machine(
    *,
    config: SessionConfig | None = None,
    mono_ms: int = 0,
) -> tuple[SessionMachine, EventCollector, SessionStore]:
    """Create a fresh state machine with in-memory persistence and a fake clock."""
    cfg = config or SessionConfig(db_path=":memory:")
    store = SessionStore(":memory:")
    collector = EventCollector()
    machine = SessionMachine(config=cfg, store=store, on_event=collector)

    clock_ms = mono_ms

    def fake_mono() -> int:
        return clock_ms

    machine.set_monotonic_fn(fake_mono)
    return machine, collector, store


def advance_clock(machine: SessionMachine, ms: int) -> None:
    """Advance the fake monotonic clock by the given milliseconds."""
    current = machine._monotonic_ms_fn()
    new_ms = current + ms

    def make_fn(val: int) -> Any:
        return lambda: val

    machine.set_monotonic_fn(make_fn(new_ms))


# ---------------------------------------------------------------------------
# §1 — Table-driven: every legal transition edge
# ---------------------------------------------------------------------------


class TestLegalTransitions:
    """Verify that every edge in LEGAL_SESSION_TRANSITIONS is accepted."""

    @pytest.mark.parametrize(
        "from_state,to_state",
        [(from_s, to_s) for from_s, tos in LEGAL_SESSION_TRANSITIONS.items() for to_s in tos],
        ids=[
            f"{from_s.value}->{to_s.value}"
            for from_s, tos in LEGAL_SESSION_TRANSITIONS.items()
            for to_s in tos
        ],
    )
    def test_legal_transition(self, from_state: SessionState, to_state: SessionState) -> None:
        machine, collector, _ = make_machine()

        # Force the machine into from_state (bypass normal flow for exhaustive coverage).
        machine._state = from_state
        if from_state != SessionState.IDLE:
            machine._session_id = uuid4()
            machine._trace_id = uuid4()

        result = machine.transition(to_state, f"test:{from_state.value}_to_{to_state.value}")

        assert result is True, f"transition {from_state} → {to_state} should be legal"
        # The machine should now be in to_state, unless to_state is IDLE
        # (which clears the session).
        if to_state == SessionState.IDLE:
            assert machine.state == SessionState.IDLE
        else:
            assert machine.state == to_state

        # A session.state_changed event must have been emitted.
        changed = collector.of_type("session.state_changed")
        assert len(changed) >= 1
        last_changed = changed[-1]
        assert last_changed["payload"]["from_state"] == from_state.value
        assert last_changed["payload"]["to_state"] == to_state.value


# ---------------------------------------------------------------------------
# §2 — Table-driven: every illegal transition is side-effect-free
# ---------------------------------------------------------------------------


class TestIllegalTransitions:
    """Every pair NOT in the legal table must be rejected with no side effects."""

    @pytest.mark.parametrize(
        "from_state,to_state",
        [
            (from_s, to_s)
            for from_s in SessionState
            for to_s in SessionState
            if to_s not in LEGAL_SESSION_TRANSITIONS.get(from_s, ())
        ],
        ids=[
            f"{from_s.value}->{to_s.value}"
            for from_s in SessionState
            for to_s in SessionState
            if to_s not in LEGAL_SESSION_TRANSITIONS.get(from_s, ())
        ],
    )
    def test_illegal_transition_is_side_effect_free(
        self,
        from_state: SessionState,
        to_state: SessionState,
    ) -> None:
        machine, collector, store = make_machine()

        # Set up state.
        machine._state = from_state
        if from_state != SessionState.IDLE:
            machine._session_id = uuid4()
            machine._trace_id = uuid4()

        state_before = machine.state
        session_id_before = machine.session_id
        events_before = len(collector.events)
        metrics_before = machine.metrics.transitions

        trigger = f"test:illegal_{from_state.value}_to_{to_state.value}"
        result = machine.transition(to_state, trigger)

        assert result is False
        assert machine.state == state_before, "state must not change on illegal transition"
        assert machine.session_id == session_id_before
        assert len(collector.events) == events_before, "no events on illegal transition"
        assert machine.metrics.transitions == metrics_before
        assert machine.metrics.illegal_transitions > 0


# ---------------------------------------------------------------------------
# §3 — Happy path: full visitor flow
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_full_session_idle_to_idle(self) -> None:
        """IDLE → APPROACH → IDENTITY → BUTTON → VISITOR → RINGING →
        ANSWERED → OFFER → RECORDING → REVIEW → SAVED → END → IDLE."""
        machine, collector, _ = make_machine()

        # 1. Identity detected
        assert machine.handle_identity_stable(
            person_id="prs_1", display_name="Alice", profile_id="wave"
        )
        assert machine.state == SessionState.APPROACH_DETECTED

        # 2. Identity cached
        assert machine.handle_identity_stable(
            person_id="prs_1", display_name="Alice", profile_id="wave"
        )
        assert machine.state == SessionState.IDENTITY_CACHED

        # 3. Button press → BUTTON_PRESSED → VISITOR_MODE
        assert machine.handle_button_pressed()
        assert machine.state == SessionState.VISITOR_MODE

        # 4. VISITOR_MODE → RINGING
        assert machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")
        assert machine.state == SessionState.RINGING

        # 5. RINGING → ANSWERED
        assert machine.handle_answered()
        assert machine.state == SessionState.ANSWERED

        # 6. ANSWERED → VIDEO_MESSAGE_OFFERED
        assert machine.transition(SessionState.VIDEO_MESSAGE_OFFERED, "auto:answer_to_offer")
        assert machine.state == SessionState.VIDEO_MESSAGE_OFFERED

        # 7. Start recording
        assert machine.handle_video_message_start()
        assert machine.state == SessionState.VIDEO_MESSAGE_RECORDING

        # 8. Stop recording
        assert machine.handle_video_message_stop()
        assert machine.state == SessionState.VIDEO_MESSAGE_REVIEW

        # 9. Save
        assert machine.handle_video_message_save()
        assert machine.state == SessionState.VIDEO_MESSAGE_SAVED

        # 10. SESSION_END
        assert machine.transition(SessionState.SESSION_END, "auto:saved_to_end")
        assert machine.state == SessionState.SESSION_END

        # 11. Back to IDLE
        assert machine.transition(SessionState.IDLE, "auto:end_to_idle")
        assert machine.state == SessionState.IDLE

        # Verify event emissions.
        started = collector.of_type("session.started")
        assert len(started) == 1
        assert started[0]["payload"]["entry"] == "approach"

        ended = collector.of_type("session.ended")
        assert len(ended) == 1

    def test_button_press_from_idle(self) -> None:
        """Button press from IDLE → BUTTON_PRESSED → VISITOR_MODE (instant)."""
        machine, collector, _ = make_machine()

        assert machine.handle_button_pressed()
        assert machine.state == SessionState.VISITOR_MODE

        # Must emit session.started with entry="button"
        started = collector.of_type("session.started")
        assert len(started) == 1
        assert started[0]["payload"]["entry"] == "button"

        # Must emit two state_changed: IDLE→BUTTON_PRESSED, BUTTON_PRESSED→VISITOR_MODE
        changed = collector.of_type("session.state_changed")
        assert len(changed) == 2
        assert changed[0]["payload"]["to_state"] == "BUTTON_PRESSED"
        assert changed[1]["payload"]["to_state"] == "VISITOR_MODE"

    def test_unanswered_path(self) -> None:
        """IDLE → BUTTON → VISITOR → RINGING → UNANSWERED → OFFER → END → IDLE."""
        machine, collector, _ = make_machine()
        machine.handle_button_pressed()

        machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")
        machine.transition(SessionState.UNANSWERED_TIMEOUT, "timeout:ring")
        assert machine.state == SessionState.UNANSWERED_TIMEOUT

        machine.transition(SessionState.VIDEO_MESSAGE_OFFERED, "auto:unanswered_to_offer")
        assert machine.state == SessionState.VIDEO_MESSAGE_OFFERED

        machine.handle_video_message_discard()
        assert machine.state == SessionState.SESSION_END

        machine.transition(SessionState.IDLE, "auto:end_to_idle")
        assert machine.state == SessionState.IDLE

        ended = collector.of_type("session.ended")
        assert len(ended) == 1
        assert ended[0]["payload"]["outcome"] == "abandoned"


# ---------------------------------------------------------------------------
# §4 — Restart mid-session
# ---------------------------------------------------------------------------


class TestRestartMidSession:
    def test_kill_9_mid_ringing_restores(self) -> None:
        """Kill -9 during RINGING → restart resumes the session correctly."""
        machine, collector, store = make_machine()

        # Get to RINGING state.
        machine.handle_button_pressed()
        machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")
        assert machine.state == SessionState.RINGING
        session_id = machine.session_id

        # "Kill -9" — create a new machine from the same store.
        machine2, collector2, _ = make_machine()
        # Re-use the same store (simulating same DB file).
        machine2.store = store

        # Advance clock a bit (but within inactivity timeout).
        advance_clock(machine2, 5_000)
        machine2.restore_from_persistence()

        assert machine2.state == SessionState.RINGING
        assert machine2.session_id == session_id

    def test_kill_9_after_inactivity_timeout_expires_to_idle(self) -> None:
        """If inactivity timeout elapsed during the crash, restore → IDLE."""
        config = SessionConfig(db_path=":memory:", inactivity_timeout_s=10.0)
        machine, collector, store = make_machine(config=config)

        machine.handle_button_pressed()
        machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")
        assert machine.state == SessionState.RINGING

        # "Kill -9" — create a new machine, clock far in the future.
        machine2, collector2, _ = make_machine(config=config)
        machine2.store = store
        advance_clock(machine2, 20_000)  # 20 seconds > 10s inactivity timeout
        machine2.restore_from_persistence()

        assert machine2.state == SessionState.IDLE

    def test_no_persisted_session_starts_idle(self) -> None:
        """Fresh start with no persisted session → IDLE."""
        machine, _, _ = make_machine()
        machine.restore_from_persistence()
        assert machine.state == SessionState.IDLE
        assert machine.session_id is None

    def test_os_reboot_expires_persisted_session_conservatively(self) -> None:
        machine, _collector, store = make_machine(mono_ms=50_000)
        machine.set_boot_id_fn(lambda: "boot-one")
        machine.handle_button_pressed()
        machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")

        machine2, _collector2, _ = make_machine(mono_ms=1_000)
        machine2.store = store
        machine2.set_boot_id_fn(lambda: "boot-two")
        machine2.restore_from_persistence()

        assert machine2.state == SessionState.IDLE
        assert machine2.session_id is None

    def test_monotonic_counter_reset_expires_legacy_session(self) -> None:
        machine, _collector, store = make_machine(mono_ms=50_000)
        machine.handle_button_pressed()
        machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")
        persisted = store.load()
        assert persisted is not None
        legacy = replace(persisted, meta_json="{}")
        store.save(legacy)

        machine2, _collector2, _ = make_machine(mono_ms=1_000)
        machine2.store = store
        machine2.restore_from_persistence()

        assert machine2.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# §5 — Kiosk reload rejoin
# ---------------------------------------------------------------------------


class TestKioskReloadRejoin:
    def test_reload_during_review_rejoins(self) -> None:
        """A kiosk browser reload during VIDEO_MESSAGE_REVIEW gets a snapshot
        with the current state."""
        machine, collector, _ = make_machine()
        broadcast = DisplayBroadcast()

        # Build up to REVIEW state.
        machine.handle_button_pressed()
        machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")
        machine.transition(SessionState.UNANSWERED_TIMEOUT, "timeout:ring")
        machine.transition(SessionState.VIDEO_MESSAGE_OFFERED, "auto:unanswered_to_offer")
        machine.handle_video_message_start()
        machine.handle_video_message_stop()
        assert machine.state == SessionState.VIDEO_MESSAGE_REVIEW

        # Update broadcast snapshot.
        broadcast.update_snapshot(machine.snapshot().to_dict())

        # Simulate kiosk reload — new client connects.
        queue = broadcast.make_client_queue()
        import json

        msg = queue.get_nowait()
        data = json.loads(msg)
        assert data["type"] == "snapshot"
        assert data["state"]["state"] == "VIDEO_MESSAGE_REVIEW"

        broadcast.remove_client(queue)

    def test_snapshot_reflects_identity(self) -> None:
        """Snapshot includes person_id and display_name."""
        machine, _, _ = make_machine()
        machine.handle_identity_stable(person_id="prs_1", display_name="Bob", profile_id="spark")
        machine.handle_button_pressed()
        snap = machine.snapshot()
        assert snap.person_id == "prs_1"
        assert snap.display_name == "Bob"
        assert snap.profile_id == "spark"
        assert snap.state == SessionState.VISITOR_MODE


# ---------------------------------------------------------------------------
# §6 — Double-press
# ---------------------------------------------------------------------------


class TestDoublePress:
    def test_double_press_during_visitor_mode_ignored(self) -> None:
        """Second button press during VISITOR_MODE is a no-op."""
        machine, collector, _ = make_machine()
        machine.handle_button_pressed()
        assert machine.state == SessionState.VISITOR_MODE
        events_before = len(collector.events)

        result = machine.handle_button_pressed()
        assert result is False
        assert machine.state == SessionState.VISITOR_MODE
        assert len(collector.events) == events_before

    def test_double_press_during_ringing_ignored(self) -> None:
        """Second button press during RINGING is a no-op."""
        machine, _, _ = make_machine()
        machine.handle_button_pressed()
        machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")
        assert machine.state == SessionState.RINGING

        result = machine.handle_button_pressed()
        assert result is False
        assert machine.state == SessionState.RINGING

    def test_double_press_during_recording_ignored(self) -> None:
        """Button press during VIDEO_MESSAGE_RECORDING is ignored."""
        machine, _, _ = make_machine()
        machine.handle_button_pressed()
        machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")
        machine.transition(SessionState.UNANSWERED_TIMEOUT, "timeout:ring")
        machine.transition(SessionState.VIDEO_MESSAGE_OFFERED, "auto:unanswered_to_offer")
        machine.handle_video_message_start()
        assert machine.state == SessionState.VIDEO_MESSAGE_RECORDING

        result = machine.handle_button_pressed()
        assert result is False
        assert machine.state == SessionState.VIDEO_MESSAGE_RECORDING


# ---------------------------------------------------------------------------
# §7 — Press during recording (brief: "press-during-recording")
# ---------------------------------------------------------------------------


class TestPressDuringRecording:
    def test_button_during_recording_does_not_restart_session(self) -> None:
        """A button press during recording should not create a new session."""
        machine, collector, _ = make_machine()
        machine.handle_button_pressed()
        session_id = machine.session_id

        machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")
        machine.transition(SessionState.UNANSWERED_TIMEOUT, "timeout:ring")
        machine.transition(SessionState.VIDEO_MESSAGE_OFFERED, "auto:unanswered_to_offer")
        machine.handle_video_message_start()
        assert machine.state == SessionState.VIDEO_MESSAGE_RECORDING

        machine.handle_button_pressed()
        assert machine.session_id == session_id
        assert machine.state == SessionState.VIDEO_MESSAGE_RECORDING


# ---------------------------------------------------------------------------
# §8 — Identity flow
# ---------------------------------------------------------------------------


class TestIdentityFlow:
    def test_identity_stable_then_expired_returns_to_idle(self) -> None:
        """IDLE → APPROACH → identity expired → IDLE."""
        machine, _, _ = make_machine()
        machine.handle_identity_stable(person_id="prs_1", display_name="Alice", profile_id="wave")
        assert machine.state == SessionState.APPROACH_DETECTED

        machine.handle_identity_expired(person_id="prs_1")
        assert machine.state == SessionState.IDLE

    def test_identity_cached_then_expired_goes_to_approach(self) -> None:
        """IDENTITY_CACHED → identity expired → APPROACH_DETECTED."""
        machine, _, _ = make_machine()
        machine.handle_identity_stable(person_id="prs_1", display_name="Alice", profile_id="wave")
        machine.handle_identity_stable(person_id="prs_1", display_name="Alice", profile_id="wave")
        assert machine.state == SessionState.IDENTITY_CACHED

        machine.handle_identity_expired(person_id="prs_1")
        assert machine.state == SessionState.APPROACH_DETECTED

    def test_identity_expired_wrong_person_ignored(self) -> None:
        """Identity expired for a different person is ignored."""
        machine, _, _ = make_machine()
        machine.handle_identity_stable(person_id="prs_1", display_name="Alice", profile_id="wave")
        assert machine.state == SessionState.APPROACH_DETECTED

        result = machine.handle_identity_expired(person_id="prs_other")
        assert result is False
        assert machine.state == SessionState.APPROACH_DETECTED

    def test_button_press_propagates_cached_profile_flag(self) -> None:
        """door.button_pressed cache metadata stays on the local session envelope."""
        machine, collector, _ = make_machine()

        assert machine.handle_button_pressed(had_cached_profile=True, profile_id="blue_wave")

        assert machine.snapshot().had_cached_profile is True
        assert machine.snapshot().profile_id == "blue_wave"
        changed = collector.of_type("session.state_changed")
        started = collector.of_type("session.started")
        assert all(event["source"] == "door-api" for event in changed)
        assert started[0]["door_id"] == machine.config.door_id
        assert "had_cached_profile" not in changed[0]
        assert "had_cached_profile" not in changed[0]["payload"]

    def test_late_identity_mid_session_updates_display_only(self) -> None:
        """Late recognition enriches the active session without a new transition."""
        machine, collector, _ = make_machine()
        assert machine.handle_button_pressed()
        events_before = len(collector.events)
        session_id = machine.session_id

        changed = machine.handle_identity_stable(
            person_id="prs_1",
            display_name="Alice",
            profile_id="blue_wave",
        )

        assert changed is False
        assert machine.session_id == session_id
        assert machine.state == SessionState.VISITOR_MODE
        assert machine.snapshot().display_name == "Alice"
        assert len(collector.events) == events_before


# ---------------------------------------------------------------------------
# §9 — Door contact
# ---------------------------------------------------------------------------


class TestDoorContact:
    def test_door_open_during_ringing_answers(self) -> None:
        """door.contact_changed(open) during RINGING → ANSWERED."""
        machine, _, _ = make_machine()
        machine.handle_button_pressed()
        machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")

        result = machine.handle_contact_changed(state="open")
        assert result is True
        assert machine.state == SessionState.ANSWERED

    def test_door_open_not_ringing_ignored(self) -> None:
        """door.contact_changed(open) not during RINGING is ignored."""
        machine, _, _ = make_machine()
        result = machine.handle_contact_changed(state="open")
        assert result is False


# ---------------------------------------------------------------------------
# §10 — Admin reset
# ---------------------------------------------------------------------------


class TestAdminReset:
    def test_admin_reset_from_ringing(self) -> None:
        """Admin reset during active session → SESSION_END."""
        machine, collector, _ = make_machine()
        machine.handle_button_pressed()
        machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")

        machine.handle_admin_reset()
        assert machine.state == SessionState.SESSION_END

        ended = collector.of_type("session.ended")
        assert len(ended) == 1
        assert ended[0]["payload"]["outcome"] == "reset"

    def test_admin_reset_from_idle_no_op(self) -> None:
        """Admin reset when already IDLE is a no-op."""
        machine, collector, _ = make_machine()
        result = machine.handle_admin_reset()
        assert result is False
        assert machine.state == SessionState.IDLE

    def test_admin_reset_from_session_end_goes_to_idle(self) -> None:
        """Admin reset from SESSION_END → IDLE."""
        machine, _, _ = make_machine()
        machine.handle_button_pressed()
        machine.transition(SessionState.SESSION_END, "test:to_end")
        assert machine.state == SessionState.SESSION_END

        machine.handle_admin_reset()
        assert machine.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# §11 — Broadcast
# ---------------------------------------------------------------------------


class TestBroadcast:
    def test_delta_sent_to_clients(self) -> None:
        """Transitions emit deltas to connected broadcast clients."""
        broadcast = DisplayBroadcast()
        queue = broadcast.make_client_queue()

        import json

        # Consume the snapshot.
        snapshot_msg = queue.get_nowait()
        data = json.loads(snapshot_msg)
        assert data["type"] == "snapshot"

        # Send a delta.
        broadcast.send_delta({"test": True})
        delta_msg = queue.get_nowait()
        delta_data = json.loads(delta_msg)
        assert delta_data["type"] == "delta"
        assert delta_data["event"]["test"] is True

        broadcast.remove_client(queue)
        assert broadcast.client_count == 0

    def test_slow_client_dropped(self) -> None:
        """A client with a full queue is dropped on next broadcast."""
        broadcast = DisplayBroadcast()
        queue = broadcast.make_client_queue()

        # Fill the queue (maxsize=64, +1 for snapshot).
        for _ in range(64):
            broadcast.send_delta({"fill": True})

        # One more should cause the queue to be full and client dropped.
        broadcast.send_delta({"overflow": True})
        assert broadcast.client_count == 0

        broadcast.remove_client(queue)


# ---------------------------------------------------------------------------
# §12 — Video message re-record
# ---------------------------------------------------------------------------


class TestVideoMessageRerecord:
    def test_rerecord_from_review(self) -> None:
        """VIDEO_MESSAGE_REVIEW → VIDEO_MESSAGE_RECORDING (re-record)."""
        machine, _, _ = make_machine()
        machine.handle_button_pressed()
        machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")
        machine.transition(SessionState.UNANSWERED_TIMEOUT, "timeout:ring")
        machine.transition(SessionState.VIDEO_MESSAGE_OFFERED, "auto:unanswered_to_offer")
        machine.handle_video_message_start()
        machine.handle_video_message_stop()
        assert machine.state == SessionState.VIDEO_MESSAGE_REVIEW

        assert machine.handle_video_message_rerecord()
        assert machine.state == SessionState.VIDEO_MESSAGE_RECORDING


# ---------------------------------------------------------------------------
# §13 — Metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_metrics_count_transitions(self) -> None:
        """Metrics counters increment correctly."""
        machine, _, _ = make_machine()

        machine.handle_button_pressed()
        assert machine.metrics.transitions == 2  # BUTTON_PRESSED + VISITOR_MODE
        assert machine.metrics.sessions_started == 1

        # Illegal transition.
        machine.transition(SessionState.IDLE, "test:illegal")
        assert machine.metrics.illegal_transitions == 1

        machine.transition(SessionState.SESSION_END, "test:end")
        assert machine.metrics.sessions_ended == 1

        d = machine.metrics.to_dict()
        assert d["session_transitions_total"] == 3
        assert d["session_illegal_transitions_total"] == 1
        assert d["session_sessions_started_total"] == 1
        assert d["session_sessions_ended_total"] == 1


# ---------------------------------------------------------------------------
# §14 — Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_session_persisted_after_transition(self) -> None:
        """Each transition persists the new state."""
        machine, _, store = make_machine()
        machine.handle_button_pressed()

        persisted = store.load()
        assert persisted is not None
        assert persisted.state == SessionState.VISITOR_MODE
        assert persisted.session_id == machine.session_id

    def test_idle_clears_persistence(self) -> None:
        """Transition to IDLE clears the persisted session."""
        machine, _, store = make_machine()
        machine.handle_button_pressed()
        machine.transition(SessionState.SESSION_END, "test:end")
        machine.transition(SessionState.IDLE, "auto:end_to_idle")

        persisted = store.load()
        assert persisted is None

    def test_session_end_is_persisted(self) -> None:
        """SESSION_END is persisted (so restart can transition to IDLE)."""
        machine, _, store = make_machine()
        machine.handle_button_pressed()
        machine.transition(SessionState.SESSION_END, "test:end")

        persisted = store.load()
        assert persisted is not None
        assert persisted.state == SessionState.SESSION_END


# ---------------------------------------------------------------------------
# §15 — Timeout races
# ---------------------------------------------------------------------------


class TestTimeoutRaces:
    def test_button_press_cancels_approach_timeout(self) -> None:
        """Button press during APPROACH_DETECTED cancels the approach timeout."""
        machine, _, _ = make_machine()
        machine.handle_identity_stable(person_id="prs_1", display_name="Alice", profile_id="wave")
        assert machine.state == SessionState.APPROACH_DETECTED

        # Button press should transition immediately, not wait for timeout.
        machine.handle_button_pressed()
        assert machine.state == SessionState.VISITOR_MODE

    def test_answer_during_ringing_cancels_ring_timeout(self) -> None:
        """Answering during RINGING should cancel the ring timeout."""
        machine, _, _ = make_machine()
        machine.handle_button_pressed()
        machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")

        # Answer cancels the ring timeout.
        machine.handle_answered()
        assert machine.state == SessionState.ANSWERED

    def test_transition_from_already_transitioned_state_fails(self) -> None:
        """If a timer fires for a state we've already left, it fails safely."""
        machine, _, _ = make_machine()
        machine.handle_button_pressed()
        machine.transition(SessionState.RINGING, "auto:visitor_mode_ring")
        machine.handle_answered()
        assert machine.state == SessionState.ANSWERED

        # A late UNANSWERED_TIMEOUT transition attempt should fail.
        result = machine.transition(SessionState.UNANSWERED_TIMEOUT, "timeout:ring")
        assert result is False
        assert machine.state == SessionState.ANSWERED


# ---------------------------------------------------------------------------
# §16 — Timer scheduling (async)
# ---------------------------------------------------------------------------


class TestTimerAsync:
    def test_visitor_mode_auto_rings(self) -> None:
        """VISITOR_MODE auto-transitions to RINGING after the configured delay."""
        config = SessionConfig(db_path=":memory:", visitor_mode_auto_ring_s=0.05)
        machine, collector, _ = make_machine(config=config)

        async def run() -> None:
            machine.handle_button_pressed()
            assert machine.state == SessionState.VISITOR_MODE
            await asyncio.sleep(0.1)
            assert machine.state == SessionState.RINGING

        asyncio.run(run())

    def test_session_end_auto_idles(self) -> None:
        """SESSION_END auto-transitions to IDLE after the linger period."""
        config = SessionConfig(db_path=":memory:", session_end_linger_s=0.05)
        machine, collector, _ = make_machine(config=config)

        async def run() -> None:
            machine.handle_button_pressed()
            machine.transition(SessionState.SESSION_END, "test:end")
            assert machine.state == SessionState.SESSION_END
            await asyncio.sleep(0.1)
            assert machine.state == SessionState.IDLE

        asyncio.run(run())

    def test_ring_timeout_to_unanswered(self) -> None:
        """RINGING auto-transitions to UNANSWERED_TIMEOUT after ring_timeout_s."""
        config = SessionConfig(
            db_path=":memory:",
            visitor_mode_auto_ring_s=0.02,
            ring_timeout_s=0.05,
        )
        machine, _, _ = make_machine(config=config)

        async def run() -> None:
            machine.handle_button_pressed()
            await asyncio.sleep(0.05)  # Wait for auto-ring
            assert machine.state == SessionState.RINGING
            await asyncio.sleep(0.1)  # Wait for ring timeout
            assert machine.state == SessionState.UNANSWERED_TIMEOUT

        asyncio.run(run())


# ---------------------------------------------------------------------------
# §17 — Pairing invariant
# ---------------------------------------------------------------------------


class TestPairingInvariant:
    def test_started_ended_pairing_across_scenarios(self, tmp_path) -> None:
        """Every scenario's events fed into SessionMachine must produce equal
        started/ended counts.
        """
        from doorboard_simulator.scenarios import available_scenarios, run_scenario_name

        async def run_invariant():
            for scenario in available_scenarios():
                config = SessionConfig(db_path=":memory:")
                machine, collector, _ = make_machine(config=config)

                result = await run_scenario_name(scenario, artifact_root=tmp_path / scenario)

                for event in result.log:
                    mono_ms = event["monotonic_ms"]
                    machine.set_monotonic_fn(lambda m=mono_ms: m)

                    typ = event["type"]
                    payload = event.get("payload", {})

                    if typ == "door.button_pressed":
                        machine.handle_button_pressed()
                    elif typ == "vision.identity_stable":
                        machine.handle_identity_stable(
                            person_id=payload["person_id"],
                            display_name=payload.get("display_name", ""),
                            profile_id=payload.get("profile_id", ""),
                        )
                    elif typ == "vision.identity_expired":
                        machine.handle_identity_expired(person_id=payload["person_id"])
                    elif typ == "door.contact_changed":
                        machine.handle_contact_changed(state=payload["state"])

                # Ensure the machine settles to IDLE (force expiration if needed)
                if machine.state != SessionState.IDLE:
                    machine._expire_to_idle("timeout:inactivity")

                started = len(collector.of_type("session.started"))
                ended = len(collector.of_type("session.ended"))
                assert started == ended, (
                    f"Scenario {scenario} failed pairing invariant: "
                    f"{started} started, {ended} ended"
                )

        asyncio.run(run_invariant())


class TestAdr0009P11:
    def test_no_identity_imports_in_authorization_path(self) -> None:
        """P-11: door-api may use identity for greeting/display, never authorization."""
        root = Path(__file__).resolve().parents[1] / "src" / "door_api"
        forbidden = (
            "door_visiond.matcher",
            "door_visiond.pipeline",
            "door_visiond.enrollment",
            "Matcher(",
            "match_result",
            "identity_author",
            "access_decision",
            "unlock",
        )
        offenders: list[str] = []
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                if needle in text:
                    offenders.append(f"{path.relative_to(root)}:{needle}")
        assert offenders == []


class TestDoorPadVideoMessageAbandonment:
    def test_fifty_video_message_abandonments_reset_cleanly(self) -> None:
        """Repeated offer/start/review/discard cycles leave no stuck session."""
        machine, _, _ = make_machine()

        for _ in range(50):
            assert machine.handle_video_message_offer()
            assert machine.state == SessionState.VIDEO_MESSAGE_OFFERED
            assert machine.handle_video_message_start()
            assert machine.state == SessionState.VIDEO_MESSAGE_RECORDING
            assert machine.handle_video_message_stop()
            assert machine.state == SessionState.VIDEO_MESSAGE_REVIEW
            assert machine.handle_video_message_discard()
            assert machine.state == SessionState.SESSION_END
            assert machine.transition(SessionState.IDLE, "auto:end_to_idle")
            assert machine.state == SessionState.IDLE
            assert machine.session_id is None
