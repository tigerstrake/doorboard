"""Durability and bounds for the door-api to door-media transition outbox."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from door_api.config import SessionConfig
from door_api.persistence import SessionStore
from door_api.session import SessionMachine
from doorboard_contracts.events import parse_event


def _media_event(event_id: str) -> dict[str, Any]:
    return {"event_id": event_id, "type": "session.state_changed", "payload": {}}


def _sync_event(event_id: str) -> dict[str, Any]:
    return {"event_id": event_id, "type": "media.storage_status", "payload": {}}


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
            max_attempts=10,
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
            max_attempts=10,
        )

        assert store.next_media_event(4999.0) is None
        retried = store.next_media_event(5000.0)
        assert retried is not None
        assert retried.event_id == first.event_id
        assert retried.attempts == 1
    finally:
        store.close()


def test_dead_head_no_longer_wedges_media_outbox(tmp_path: Path) -> None:
    """A poison head that hits the attempt cap is parked, and the healthy item
    queued behind it is then what ``next_media_event`` returns — the wedge is gone.

    Before the dead column, a head that door-media answered 422 forever sat at
    ``rowid`` 1 and blocked every bell-clip projection behind it until the outbox
    filled to its row cap and evicted it.
    """
    store = SessionStore(str(tmp_path / "poison-media.sqlite"))
    try:
        max_attempts = 3
        store.clear_with_media_event(_media_event("poison"))
        store.clear_with_media_event(_media_event("healthy"))

        # Fail the head every attempt, the way the forward loop does: bump attempts
        # by one and make it immediately due again (next_attempt_epoch=0).
        for _ in range(max_attempts):
            head = store.next_media_event(10**12)
            assert head is not None
            assert head.event_id == "poison"
            store.retry_media_event(
                head.event_id,
                attempts=head.attempts + 1,
                next_attempt_epoch=0.0,
                last_error="422 contract drift",
                max_attempts=max_attempts,
            )

        assert store.media_outbox_dead_total() == 1
        following = store.next_media_event(10**12)
        assert following is not None
        assert following.event_id == "healthy"
        # The dead item is retained (surfaced, never silently dropped), just parked.
        assert store.media_outbox_depth() == 2
    finally:
        store.close()


def test_dead_head_no_longer_wedges_sync_outbox(tmp_path: Path) -> None:
    """Same guarantee for the sync outbox (door-sync delivery)."""
    store = SessionStore(str(tmp_path / "poison-sync.sqlite"))
    try:
        max_attempts = 2
        store.enqueue_sync_event(_sync_event("poison"))
        store.enqueue_sync_event(_sync_event("healthy"))

        for _ in range(max_attempts):
            head = store.next_sync_event(10**12)
            assert head is not None
            assert head.event_id == "poison"
            store.retry_sync_event(
                head.event_id,
                attempts=head.attempts + 1,
                next_attempt_epoch=0.0,
                last_error="422 contract drift",
                max_attempts=max_attempts,
            )

        assert store.sync_outbox_dead_total() == 1
        following = store.next_sync_event(10**12)
        assert following is not None
        assert following.event_id == "healthy"
        assert store.sync_outbox_depth() == 2
    finally:
        store.close()


def test_transient_failure_under_cap_is_not_dead_lettered(tmp_path: Path) -> None:
    """A failure count below the cap keeps retrying — the door-media/door-sync
    outage must drain on recovery, so we never park an item early."""
    store = SessionStore(str(tmp_path / "transient.sqlite"))
    try:
        store.enqueue_sync_event(_sync_event("flaky"))
        item = store.next_sync_event(10**12)
        assert item is not None
        store.retry_sync_event(
            item.event_id,
            attempts=2,
            next_attempt_epoch=5000.0,
            last_error="connect timeout",
            max_attempts=10,
        )

        assert store.sync_outbox_dead_total() == 0
        # Backoff still honoured (existing behaviour unchanged for live items).
        assert store.next_sync_event(4999.0) is None
        retried = store.next_sync_event(5000.0)
        assert retried is not None
        assert retried.event_id == "flaky"
        assert retried.attempts == 2
    finally:
        store.close()


def test_migration_adds_dead_column_without_data_loss(tmp_path: Path) -> None:
    """An outbox DB created before the dead column gains it on open, keeping rows."""
    db_path = tmp_path / "legacy.sqlite"

    # A pre-migration database: media_outbox/sync_outbox without the `dead` column.
    legacy = sqlite3.connect(str(db_path))
    legacy.executescript(
        """
        CREATE TABLE media_outbox (
            event_id TEXT PRIMARY KEY,
            event_json TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_epoch REAL NOT NULL DEFAULT 0,
            created_epoch REAL NOT NULL,
            last_error TEXT
        );
        CREATE TABLE sync_outbox (
            event_id TEXT PRIMARY KEY,
            event_json TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_epoch REAL NOT NULL DEFAULT 0,
            created_epoch REAL NOT NULL,
            last_error TEXT
        );
        """
    )
    legacy.execute(
        "INSERT INTO media_outbox (event_id, event_json, created_epoch) VALUES (?, ?, 0)",
        ("legacy-media", json.dumps(_media_event("legacy-media"))),
    )
    legacy.execute(
        "INSERT INTO sync_outbox (event_id, event_json, created_epoch) VALUES (?, ?, 0)",
        ("legacy-sync", json.dumps(_sync_event("legacy-sync"))),
    )
    legacy.commit()
    legacy.close()

    # Opening through SessionStore runs the idempotent migration.
    store = SessionStore(str(db_path))
    try:
        # dead_total runs `WHERE dead = 1`; it only succeeds if the column exists.
        assert store.media_outbox_dead_total() == 0
        assert store.sync_outbox_dead_total() == 0
        # Rows survived and the back-filled default (0) makes them live.
        assert store.media_outbox_depth() == 1
        assert store.sync_outbox_depth() == 1
        media = store.next_media_event(10**12)
        assert media is not None
        assert media.event_id == "legacy-media"
        sync = store.next_sync_event(10**12)
        assert sync is not None
        assert sync.event_id == "legacy-sync"
    finally:
        store.close()
