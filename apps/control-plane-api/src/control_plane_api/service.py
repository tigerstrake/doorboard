"""Batch ingest orchestration: one DB transaction per event, then best-effort fan-out.

Each event commits independently (rather than one transaction for the whole
batch) so a later event's failure can never roll back an earlier event's
already-reported "stored" result, and so MQTT/notify fan-out — which must
never block or fail ingestion — only ever fires for durably committed
writes.

MQTT is published inline (paho's `publish` hands the message to its network
thread and returns). Everything that does real I/O — ntfy, Telegram, the
door-api media pull — goes through `FanoutWorker`, which delivers from a
background thread so the request returns as soon as the rows are committed.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from control_plane_api.db import session_scope
from control_plane_api.fanout import FanoutWorker
from control_plane_api.ingest import ingest_one
from control_plane_api.metrics import INGEST_EVENTS, metrics
from control_plane_api.mqtt import MqttPublisher

logger = logging.getLogger("control_plane_api.service")


def ingest_batch(
    session_factory: sessionmaker[Session],
    raw_events: list[dict],
    *,
    batch_id: str | None,
    now: datetime,
    mqtt_publisher: MqttPublisher,
    fanout: FanoutWorker,
) -> list[dict]:
    results = []
    for raw in raw_events:
        with session_scope(session_factory) as session:
            outcome = ingest_one(session, raw, batch_id=batch_id, now=now)

        metrics.inc(INGEST_EVENTS, status=outcome.status)
        item: dict = {"event_id": outcome.event_id, "status": outcome.status}
        if outcome.error is not None:
            item["error"] = outcome.error
        results.append(item)

        if outcome.status == "stored" and outcome.event is not None:
            try:
                mqtt_publisher.publish_event(outcome.event)
            except Exception:
                logger.warning("mqtt_fanout_failed", exc_info=True)
            fanout.enqueue_event(outcome.event, now=now)

    return results
