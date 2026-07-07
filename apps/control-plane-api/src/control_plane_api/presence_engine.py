"""Presence engine: DB-backed source registry + change-only event emission.

`presence.py` is the pure precedence/expiry resolver; this module is the
persistence and orchestration layer around it —

  - `presence_sources`: per-(subject_id, source) registry row (label/until/
    enabled). `calendar` never stores a label/until here (it's queried live
    from a `CalendarProvider`); its row only carries the `enabled` flag.
  - `presence_subjects`: per-subject `tracking_enabled` flag, gating the
    *inferred* sources (focus_shortcut, geofence_label, calendar) — the
    "config flag per subject" the brief scopes roommate consent down to.
    "manual" is never gated by it: a subject (or an admin acting for them)
    stating their own status directly is not the kind of inference consent
    is about.
  - `sync_presence`: resolve "now", and if that differs from the
    last-recorded (label, source, until) for this subject, emit exactly one
    `status.presence_changed` event through the same `ingest_one` path
    `/ingest` uses (idempotent storage + `presence_history` projection),
    then best-effort MQTT fan-out. Called from both writes (a source
    changed) *and* reads (admin/bundle GET) — the latter is what makes
    "busy until 15:00" revert on schedule without a background scheduler:
    every caller resolves against the current `now`.

Two subjects are auto-provisioned on first touch (owner, roommate — the
Weasley-clock pair ARCHITECTURE.md §9 and the wallboard spec name), mirroring
`bundles.get_or_create_bundle`'s "auto-create sane defaults on first read"
pattern. Any other `subject_id` still works — it just isn't listed until it
has a row.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from doorboard_contracts import PresenceLabel
from doorboard_contracts.events import StatusPresenceChangedEvent, StatusPresenceChangedPayload
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from control_plane_api._uuid7 import uuid7
from control_plane_api.ingest import ingest_one
from control_plane_api.models import PresenceHistoryRow, PresenceSourceRow, PresenceSubjectRow
from control_plane_api.mqtt import MqttPublisher
from control_plane_api.presence import (
    SOURCE_PRECEDENCE,
    CalendarProvider,
    ResolvedPresence,
    SourceEntry,
    resolve_presence,
)

logger = logging.getLogger("control_plane_api.presence_engine")

DEFAULT_SUBJECT_IDS: tuple[str, ...] = ("owner", "roommate")

# Sources with a real registry row holding label/until. "calendar" is
# deliberately excluded — its value always comes live from the
# `CalendarProvider`; a row for it (if any) only ever carries `enabled`.
STORED_VALUE_SOURCES: frozenset[str] = frozenset(
    {"manual", "focus_shortcut", "geofence_label", "default"}
)


class InvalidSourceError(ValueError):
    pass


def _require_valid_source(source: str) -> None:
    if source not in SOURCE_PRECEDENCE:
        msg = f"unknown presence source {source!r}; must be one of {SOURCE_PRECEDENCE}"
        raise InvalidSourceError(msg)


def _require_stored_source(source: str) -> None:
    _require_valid_source(source)
    if source not in STORED_VALUE_SOURCES:
        msg = f"source {source!r} has no directly settable value (queried live instead)"
        raise InvalidSourceError(msg)


# ---------------------------------------------------------------------------
# Subject config (tracking_enabled)
# ---------------------------------------------------------------------------


def get_or_create_subject(
    session: Session, subject_id: str, *, now: datetime
) -> PresenceSubjectRow:
    row = session.get(PresenceSubjectRow, subject_id)
    if row is None:
        row = PresenceSubjectRow(subject_id=subject_id, tracking_enabled=True, updated_at=now)
        session.add(row)
        session.flush()
    return row


def set_subject_tracking_enabled(
    session: Session, *, subject_id: str, enabled: bool, now: datetime
) -> PresenceSubjectRow:
    row = get_or_create_subject(session, subject_id, now=now)
    row.tracking_enabled = enabled
    row.updated_at = now
    session.flush()
    return row


def list_known_subject_ids(session: Session) -> list[str]:
    from_subjects = session.execute(select(PresenceSubjectRow.subject_id)).scalars().all()
    from_sources = session.execute(select(PresenceSourceRow.subject_id).distinct()).scalars().all()
    extra = sorted((set(from_subjects) | set(from_sources)) - set(DEFAULT_SUBJECT_IDS))
    return [*DEFAULT_SUBJECT_IDS, *extra]


# ---------------------------------------------------------------------------
# Source registry (label/until/enabled per subject+source)
# ---------------------------------------------------------------------------


def _get_source_row(session: Session, *, subject_id: str, source: str) -> PresenceSourceRow | None:
    return session.get(PresenceSourceRow, (subject_id, source))


def list_source_rows(session: Session, subject_id: str) -> dict[str, PresenceSourceRow]:
    stmt = select(PresenceSourceRow).where(PresenceSourceRow.subject_id == subject_id)
    rows = session.execute(stmt).scalars().all()
    return {row.source: row for row in rows}


def set_source_value(
    session: Session,
    *,
    subject_id: str,
    source: str,
    label: PresenceLabel | None,
    until: datetime | None,
    now: datetime,
) -> PresenceSourceRow:
    """Upsert a stored source's value. Never touches `enabled`."""
    _require_stored_source(source)
    row = _get_source_row(session, subject_id=subject_id, source=source)
    if row is None:
        row = PresenceSourceRow(
            subject_id=subject_id, source=source, label=None, until=None, enabled=True
        )
        session.add(row)
    row.label = label.value if label is not None else None
    row.until = until
    row.updated_at = now
    session.flush()
    return row


def clear_source_value(session: Session, *, subject_id: str, source: str, now: datetime) -> None:
    set_source_value(session, subject_id=subject_id, source=source, label=None, until=None, now=now)


def set_manual_override(
    session: Session,
    *,
    subject_id: str,
    label: PresenceLabel,
    until: datetime | None,
    now: datetime,
) -> PresenceSourceRow:
    """Manual override endpoint's write path (admin UI + one-tap). Always re-enables."""
    row = set_source_value(
        session, subject_id=subject_id, source="manual", label=label, until=until, now=now
    )
    row.enabled = True
    session.flush()
    return row


def clear_manual_override(session: Session, *, subject_id: str, now: datetime) -> None:
    clear_source_value(session, subject_id=subject_id, source="manual", now=now)


def set_source_enabled(
    session: Session, *, subject_id: str, source: str, enabled: bool, now: datetime
) -> PresenceSourceRow:
    """Per-source enable/disable (admin UI). Never touches label/until."""
    _require_valid_source(source)
    row = _get_source_row(session, subject_id=subject_id, source=source)
    if row is None:
        row = PresenceSourceRow(
            subject_id=subject_id,
            source=source,
            label=None,
            until=None,
            enabled=enabled,
            updated_at=now,
        )
        session.add(row)
    else:
        row.enabled = enabled
        row.updated_at = now
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _build_entries(
    session: Session,
    *,
    subject_id: str,
    now: datetime,
    calendar_provider: CalendarProvider,
) -> tuple[dict[str, SourceEntry | None], bool]:
    source_rows = list_source_rows(session, subject_id)
    subject_row = session.get(PresenceSubjectRow, subject_id)
    tracking_enabled = True if subject_row is None else subject_row.tracking_enabled

    entries: dict[str, SourceEntry | None] = {}
    for source in SOURCE_PRECEDENCE:
        if source == "calendar":
            cal_row = source_rows.get("calendar")
            if not tracking_enabled or (cal_row is not None and not cal_row.enabled):
                entries[source] = None
            else:
                entries[source] = calendar_provider.get_label(subject_id, now=now)
            continue
        if source in ("focus_shortcut", "geofence_label") and not tracking_enabled:
            entries[source] = None
            continue
        row = source_rows.get(source)
        if row is None or not row.enabled or row.label is None:
            entries[source] = None
        else:
            entries[source] = SourceEntry(label=PresenceLabel(row.label), until=row.until)
    return entries, tracking_enabled


def resolve_current(
    session: Session, *, subject_id: str, now: datetime, calendar_provider: CalendarProvider
) -> ResolvedPresence:
    entries, _tracking_enabled = _build_entries(
        session, subject_id=subject_id, now=now, calendar_provider=calendar_provider
    )
    return resolve_presence(entries, now=now)


def _latest_history_row(session: Session, subject_id: str) -> PresenceHistoryRow | None:
    stmt = (
        select(PresenceHistoryRow)
        .where(PresenceHistoryRow.subject_id == subject_id)
        .order_by(PresenceHistoryRow.occurred_at.desc(), PresenceHistoryRow.recorded_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def _trim_history(session: Session, subject_id: str, *, keep: int) -> None:
    """Retention cap: keep only the `keep` most recent rows for this subject."""
    keep_ids_stmt = (
        select(PresenceHistoryRow.event_id)
        .where(PresenceHistoryRow.subject_id == subject_id)
        .order_by(PresenceHistoryRow.occurred_at.desc(), PresenceHistoryRow.recorded_at.desc())
        .limit(keep)
    )
    keep_ids = session.execute(keep_ids_stmt).scalars().all()
    if not keep_ids:
        return
    session.execute(
        delete(PresenceHistoryRow).where(
            PresenceHistoryRow.subject_id == subject_id,
            PresenceHistoryRow.event_id.not_in(keep_ids),
        )
    )


def _build_presence_event(
    *, subject_id: str, resolved: ResolvedPresence, now: datetime, door_id: str
) -> StatusPresenceChangedEvent:
    return StatusPresenceChangedEvent(
        event_id=uuid7(),
        type="status.presence_changed",
        source="control-plane-api",
        occurred_at=now,
        monotonic_ms=int(time.monotonic() * 1000),
        door_id=door_id,
        trace_id=uuid4(),
        payload=StatusPresenceChangedPayload(
            subject_id=subject_id,
            label=resolved.label,
            source=resolved.source,  # type: ignore[arg-type]
            until=resolved.until,
        ),
    )


def sync_presence(
    session: Session,
    *,
    subject_id: str,
    now: datetime,
    door_id: str,
    calendar_provider: CalendarProvider,
    mqtt_publisher: MqttPublisher,
    history_max_rows: int,
) -> ResolvedPresence:
    """Resolve `subject_id` at `now`; emit iff changed. Always returns the resolution."""
    get_or_create_subject(session, subject_id, now=now)
    resolved = resolve_current(
        session, subject_id=subject_id, now=now, calendar_provider=calendar_provider
    )

    last = _latest_history_row(session, subject_id)
    changed = (
        last is None
        or last.label != resolved.label.value
        or last.source != resolved.source
        or last.until != resolved.until
    )
    if not changed:
        return resolved

    event = _build_presence_event(
        subject_id=subject_id, resolved=resolved, now=now, door_id=door_id
    )
    outcome = ingest_one(session, event.model_dump(mode="json"), batch_id=None, now=now)
    if outcome.status != "stored":
        # Should be unreachable (event_id is freshly generated every call) —
        # but never let a presence resolve raise on a dedupe race.
        logger.warning("presence_event_not_stored", extra={"status": outcome.status})
        return resolved

    session.flush()
    _trim_history(session, subject_id, keep=history_max_rows)
    try:
        mqtt_publisher.publish_event(event)
    except Exception:
        logger.warning("presence_mqtt_fanout_failed", exc_info=True)
    return resolved


# ---------------------------------------------------------------------------
# Read-side composition (admin listing + Pi-facing bundle)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceStatus:
    source: str
    label: PresenceLabel | None
    until: datetime | None
    enabled: bool
    stored: bool  # False for "calendar" — its value is live, not persisted


@dataclass(frozen=True, slots=True)
class SubjectStatus:
    subject_id: str
    tracking_enabled: bool
    label: PresenceLabel
    source: str
    until: datetime | None
    sources: tuple[SourceStatus, ...]


def get_subject_status(
    session: Session,
    *,
    subject_id: str,
    now: datetime,
    door_id: str,
    calendar_provider: CalendarProvider,
    mqtt_publisher: MqttPublisher,
    history_max_rows: int,
) -> SubjectStatus:
    resolved = sync_presence(
        session,
        subject_id=subject_id,
        now=now,
        door_id=door_id,
        calendar_provider=calendar_provider,
        mqtt_publisher=mqtt_publisher,
        history_max_rows=history_max_rows,
    )
    source_rows = list_source_rows(session, subject_id)
    subject_row = get_or_create_subject(session, subject_id, now=now)

    sources: list[SourceStatus] = []
    for source in SOURCE_PRECEDENCE:
        row = source_rows.get(source)
        if source == "calendar":
            live_entry = calendar_provider.get_label(subject_id, now=now)
            sources.append(
                SourceStatus(
                    source=source,
                    label=live_entry.label if live_entry is not None else None,
                    until=live_entry.until if live_entry is not None else None,
                    enabled=row.enabled if row is not None else True,
                    stored=False,
                )
            )
            continue
        sources.append(
            SourceStatus(
                source=source,
                label=PresenceLabel(row.label)
                if row is not None and row.label is not None
                else None,
                until=row.until if row is not None else None,
                enabled=row.enabled if row is not None else True,
                stored=True,
            )
        )

    return SubjectStatus(
        subject_id=subject_id,
        tracking_enabled=subject_row.tracking_enabled,
        label=resolved.label,
        source=resolved.source,
        until=resolved.until,
        sources=tuple(sources),
    )


def list_subject_statuses(
    session: Session,
    *,
    now: datetime,
    door_id: str,
    calendar_provider: CalendarProvider,
    mqtt_publisher: MqttPublisher,
    history_max_rows: int,
) -> list[SubjectStatus]:
    return [
        get_subject_status(
            session,
            subject_id=subject_id,
            now=now,
            door_id=door_id,
            calendar_provider=calendar_provider,
            mqtt_publisher=mqtt_publisher,
            history_max_rows=history_max_rows,
        )
        for subject_id in list_known_subject_ids(session)
    ]


def list_history(
    session: Session, *, subject_id: str, limit: int = 100
) -> list[PresenceHistoryRow]:
    stmt = (
        select(PresenceHistoryRow)
        .where(PresenceHistoryRow.subject_id == subject_id)
        .order_by(PresenceHistoryRow.occurred_at.desc(), PresenceHistoryRow.recorded_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())
