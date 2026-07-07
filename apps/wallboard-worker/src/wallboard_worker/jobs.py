from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime

import httpx
from birdnet.provider import BirdProvider
from satellites.provider import SatelliteProvider
from doorboard_contracts.events import (
    AmbientBirdSpeciesSummary,
    AmbientSatellitePassEvent,
    AmbientSatellitePassPayload,
    AmbientBirdSummaryEvent,
    AmbientBirdSummaryPayload,
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


def run_bird_summary(
    settings: Settings, provider: BirdProvider, now: datetime | None = None
) -> dict | None:
    """Query BirdNET detections and ingest a summary event into the control plane."""
    if now is None:
        now = datetime.now(UTC)

    try:
        total, top = provider.get_summary(now)
    except Exception as exc:
        logger.error(f"BirdNET summary job failed to retrieve data: {exc}")
        # Return none to degrade the tile (showing stale data) without crashing the system
        return None

    # Construct payload
    top_species_models = [
        AmbientBirdSpeciesSummary(
            name=s["name"],
            count=s["count"],
            confidence_avg=s["confidence_avg"],
        )
        for s in top
    ]

    payload = AmbientBirdSummaryPayload(
        window="today",
        top_species=top_species_models,
        total_detections=total,
    )

    # Construct event
    event = AmbientBirdSummaryEvent(
        event_id=uuid7(),
        type="ambient.bird_summary",
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
    batch = {"batch_id": f"worker-bird-{int(time.time())}", "events": [raw_event]}

    try:
        resp = httpx.post(url, json=batch, headers=headers, timeout=5.0)
        if resp.status_code == 200:
            logger.info(f"Ingested bird summary event successfully. Total detections: {total}")
            return resp.json()
        else:
            logger.error(f"Ingestion failed with status {resp.status_code}: {resp.text}")
    except Exception as exc:
        logger.error(f"Failed to post bird summary event: {exc}")

    return None


def run_daily_collage(settings: Settings, now: datetime | None = None) -> None:
    """Daily collage job stub.

    Generates an image grid from detection thumbnails if BirdNET-Go exposes them,
    else falls back to species list card.
    """
    logger.info("Daily collage job stub executed successfully.")


def run_satellite_passes(
    settings: Settings, provider: SatelliteProvider, now: datetime | None = None
) -> dict | None:
    """Calculate the next visible satellite pass and ingest it into the control plane."""
    if now is None:
        now = datetime.now(UTC)

    try:
        pass_data = provider.get_next_pass(now)
    except Exception as exc:
        logger.error(f"Satellite pass prediction job failed: {exc}")
        # Degradation path: return None so no new event is posted and the tile is marked stale
        return None

    if pass_data is None:
        logger.info("No visible satellite passes predicted in the next 24 hours.")
        return None

    # Construct payload
    payload = AmbientSatellitePassPayload(
        satellite=pass_data["satellite"],
        rise_at=pass_data["rise_at"],
        max_elevation_deg=pass_data["max_elevation_deg"],
        direction=pass_data["direction"],
        visible=pass_data["visible"],
    )

    # Construct event
    event = AmbientSatellitePassEvent(
        event_id=uuid7(),
        type="ambient.satellite_pass",
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
    batch = {"batch_id": f"worker-satellites-{int(time.time())}", "events": [raw_event]}

    try:
        resp = httpx.post(url, json=batch, headers=headers, timeout=5.0)
        if resp.status_code == 200:
            logger.info(
                f"Ingested satellite pass event successfully: "
                f"{pass_data['satellite']} rise_at={pass_data['rise_at']}"
            )
            return resp.json()
        else:
            logger.error(f"Ingestion failed with status {resp.status_code}: {resp.text}")
    except Exception as exc:
        logger.error(f"Failed to post satellite pass event: {exc}")

    return None
