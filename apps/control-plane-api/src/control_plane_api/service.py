"""Batch ingest orchestration: one DB transaction per event, then best-effort fan-out.

Each event commits independently (rather than one transaction for the whole
batch) so a later event's failure can never roll back an earlier event's
already-reported "stored" result, and so MQTT/notify fan-out — which must
never block or fail ingestion — only ever fires for durably committed
writes.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from control_plane_api.db import session_scope
from control_plane_api.ingest import ingest_one
from control_plane_api.mqtt import MqttPublisher
from control_plane_api.notify import NotifyEngine

logger = logging.getLogger("control_plane_api.service")


def ingest_batch(
    session_factory: sessionmaker[Session],
    raw_events: list[dict],
    *,
    batch_id: str | None,
    now: datetime,
    mqtt_publisher: MqttPublisher,
    notify_engine: NotifyEngine,
) -> list[dict]:
    results = []
    for raw in raw_events:
        with session_scope(session_factory) as session:
            outcome = ingest_one(session, raw, batch_id=batch_id, now=now)

        item: dict = {"event_id": outcome.event_id, "status": outcome.status}
        if outcome.error is not None:
            item["error"] = outcome.error
        results.append(item)

        if outcome.status == "stored" and outcome.event is not None:
            try:
                mqtt_publisher.publish_event(outcome.event)
            except Exception:
                logger.warning("mqtt_fanout_failed", exc_info=True)
            try:
                with session_scope(session_factory) as notify_session:
                    notify_engine.on_event(notify_session, outcome.event, now=now)
            except Exception:
                logger.warning("notify_fanout_failed", exc_info=True)

    return results
