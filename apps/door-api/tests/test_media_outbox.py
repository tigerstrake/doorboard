"""Durability and bounds for the door-api to door-media transition outbox."""

from __future__ import annotations

from pathlib import Path

from door_api.config import SessionConfig
from door_api.persistence import SessionStore
from door_api.session import SessionMachine
from doorboard_contracts.events import parse_event


def _machine(db_path: Path, *, max_rows: int = 4096) -> tuple[SessionMachine, SessionStore]:
    store = SessionStore(
        str(db_path),
        media_outbox_max_rows=max_rows,
        sync_outbox_max_rows=max_rows,
    )
    machine = SessionMachine(
        config=SessionConfig(db_path=str(db_path)),
        store=store,
        on_event=lambda _event: None,
    )
    machine.set_monotonic_fn(lambda: 1000)
    machine.set_boot_id_fn(lambda: "test-boot")
    return machine, store


def test_outbox_survives_process_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "session.sqlite"
    machine, store = _machine(db_path)
    assert machine.handle_button_pressed()
    assert store.media_outbox_depth() == 2
    assert store.sync_outbox_depth() == 3
    store.close()

    reopened = SessionStore(str(db_path))
    try:
        item = reopened.next_media_event(10**12)
        assert item is not None
        event = parse_event(item.event)
        assert event.type == "session.state_changed"
        assert event.source == "door-api"
        assert reopened.media_outbox_depth() == 2
        assert reopened.sync_outbox_depth() == 3
    finally:
        reopened.close()


def test_outbox_is_bounded_and_accounts_for_drops(tmp_path: Path) -> None:
    machine, store = _machine(tmp_path / "bounded.sqlite", max_rows=2)
    try:
        assert machine.handle_button_pressed()
        assert machine.handle_admin_reset()
        assert store.media_outbox_depth() == 2
        assert store.media_outbox_dropped_total() >= 1
        assert store.sync_outbox_depth() == 2
        assert store.sync_outbox_dropped_total() >= 1
    finally:
        store.close()


def test_sync_retry_preserves_causal_order(tmp_path: Path) -> None:
    machine, store = _machine(tmp_path / "sync-ordered.sqlite")
    try:
        assert machine.handle_button_pressed()
        first = store.next_sync_event(10**12)
        assert first is not None
        first_event = parse_event(first.event)
        assert first_event.type == "session.state_changed"

        store.retry_sync_event(
            first.event_id,
            attempts=1,
            next_attempt_epoch=5000.0,
            last_error="unavailable",
        )
        assert store.next_sync_event(4999.0) is None
        retried = store.next_sync_event(5000.0)
        assert retried is not None
        assert retried.event_id == first.event_id
    finally:
        store.close()


def test_retry_preserves_transition_order(tmp_path: Path) -> None:
    machine, store = _machine(tmp_path / "ordered.sqlite")
    try:
        assert machine.handle_button_pressed()
        first = store.next_media_event(10**12)
        assert first is not None
        store.retry_media_event(
            first.event_id,
            attempts=1,
            next_attempt_epoch=5000.0,
            last_error="unavailable",
        )

        assert store.next_media_event(4999.0) is None
        retried = store.next_media_event(5000.0)
        assert retried is not None
        assert retried.event_id == first.event_id
        assert retried.attempts == 1
    finally:
        store.close()
