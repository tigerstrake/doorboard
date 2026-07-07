from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime

import httpx
from aircraft.provider import AircraftProvider
from doorboard_contracts.events import (
    AmbientAircraftNearby,
    AmbientAircraftSummaryEvent,
    AmbientAircraftSummaryPayload,
)

from wallboard_worker.settings import Settings
from wallboard_worker.uuid7 import uuid7

logger = logging.getLogger("doorboard.wallboard_worker.jobs")


def get_ingest_token(settings: Settings) -> str:
    """Fetch an ingest token from the control plane API using the configured admin token."""
    if not settings.control_plane_admin_token:
        logger.debug("No CONTROL_PLANE_ADMIN_TOKEN configured, returning empty token")
        return ""

    url = f"{settings.control_plane_url.rstrip('/')}/admin/tokens"
    headers = {"Authorization": f"Bearer {settings.control_plane_admin_token}"}
    try:
        resp = httpx.post(
            url,
            json={"scope": "ingest", "door_id": settings.door_id},
            headers=headers,
            timeout=5.0,
        )
        if resp.status_code == 200:
            return resp.json()["token"]
        else:
            logger.warning(f"Failed to fetch ingest token, status {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.warning(f"Failed to connect to control plane for token: {e}")
    return ""


def run_aircraft_summary(
    settings: Settings, provider: AircraftProvider, now: datetime | None = None
) -> dict | None:
    """Fetch nearby aircraft data and ingest it into the control plane."""
    if now is None:
        now = datetime.now(UTC)

    try:
        aircraft_list = provider.get_nearby_aircraft(now)
    except Exception as exc:
        logger.error(f"Aircraft summary job failed: {exc}")
        # Return None to indicate failure (stale path)
        return None

    # Construct payload
    nearby_models = [
        AmbientAircraftNearby(
            callsign=ac["callsign"],
            altitude_ft=ac["altitude_ft"],
            distance_km=ac["distance_km"],
            heading=ac["heading"],
        )
        for ac in aircraft_list
    ]

    payload = AmbientAircraftSummaryPayload(
        nearby=nearby_models,
        as_of=now,
    )

    # Construct event
    event = AmbientAircraftSummaryEvent(
        event_id=uuid7(),
        type="ambient.aircraft_summary",
        source="wallboard-worker",
        occurred_at=now,
        monotonic_ms=int(time.monotonic() * 1000),
        door_id=settings.door_id,
        trace_id=uuid.uuid4(),
        payload=payload,
    )

    # Ingest event
    url = f"{settings.control_plane_url.rstrip('/')}/ingest"
    token = get_ingest_token(settings)
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    raw_event = event.model_dump(mode="json")
    batch = {"batch_id": f"worker-aircraft-{int(time.time())}", "events": [raw_event]}

    try:
        resp = httpx.post(url, json=batch, headers=headers, timeout=5.0)
        if resp.status_code == 200:
            logger.info(
                f"Ingested aircraft summary event successfully. Count: {len(aircraft_list)}"
            )
            return resp.json()
        else:
            logger.error(f"Ingestion failed with status {resp.status_code}: {resp.text}")
    except Exception as exc:
        logger.error(f"Failed to post aircraft summary event: {exc}")

    return None
