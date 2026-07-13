from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from aircraft.provider import AircraftProvider
from birdnet.provider import BirdProvider
from doorboard_contracts.events import (
    AmbientAircraftNearby,
    AmbientAircraftSummaryEvent,
    AmbientAircraftSummaryPayload,
    AmbientBirdSpeciesSummary,
    AmbientBirdSummaryEvent,
    AmbientBirdSummaryPayload,
    AmbientFoodRecommendationEvent,
    AmbientFoodRecommendationPayload,
    AmbientPrinterStatusEvent,
    AmbientPrinterStatusPayload,
    AmbientSatellitePassEvent,
    AmbientSatellitePassPayload,
)
from food_recommendation.provider import (
    FoodRecommendationCache,
    FoodRecommendationProvider,
    Recommendation,
)
from printer.provider import PrinterProvider
from satellites.provider import SatelliteProvider

from wallboard_worker.settings import Settings
from wallboard_worker.uuid7 import uuid7

logger = logging.getLogger("doorboard.wallboard_worker.jobs")


def get_ingest_token(settings: Settings) -> str:
    """Return one process-scoped ingest token, bootstrapping once in dev if needed."""
    if settings.ingest_token:
        return settings.ingest_token
    if not settings.control_plane_admin_token:
        logger.error("No WALLBOARD_WORKER_INGEST_TOKEN configured")
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
            settings.ingest_token = str(resp.json()["token"])
            return settings.ingest_token
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


def run_printer_status(
    settings: Settings, provider: PrinterProvider, now: datetime | None = None
) -> dict | None:
    """Fetch printer status and ingest it into the control plane."""
    if now is None:
        now = datetime.now(UTC)

    try:
        status = provider.get_status(now)
    except Exception as exc:
        logger.error(f"Printer status job failed to retrieve data: {exc}")
        # Degradation: fallback to offline status
        status = {
            "state": "offline",
            "job_name": None,
            "progress_pct": None,
            "eta": None,
        }

    # Construct payload
    payload = AmbientPrinterStatusPayload(
        state=status["state"],
        job_name=status["job_name"],
        progress_pct=status["progress_pct"],
        eta=status["eta"],
    )

    # Construct event
    event = AmbientPrinterStatusEvent(
        event_id=uuid7(),
        type="ambient.printer_status",
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
    batch = {"batch_id": f"worker-printer-{int(time.time())}", "events": [raw_event]}

    try:
        resp = httpx.post(url, json=batch, headers=headers, timeout=5.0)
        if resp.status_code == 200:
            logger.info(f"Ingested printer status event successfully. State: {status['state']}")
            return resp.json()
        else:
            logger.error(f"Ingestion failed with status {resp.status_code}: {resp.text}")
    except Exception as exc:
        logger.error(f"Failed to post printer status event: {exc}")

    return None


def run_food_recommendation(
    settings: Settings,
    provider: FoodRecommendationProvider,
    now: datetime | None = None,
    cache: FoodRecommendationCache | None = None,
) -> dict | None:
    """Fetch the daily food recommendation and ingest it into the control plane."""
    if now is None:
        now = datetime.now(UTC)
    if cache is None:
        cache = FoodRecommendationCache(settings.food_cache_path)

    recommendation: Recommendation | None
    try:
        recommendation = provider.get_daily_recommendation()
        cache.save(recommendation)
    except Exception as exc:
        logger.error(f"Food recommendation job failed to retrieve data: {exc}")
        yesterday = now.date() - timedelta(days=1)
        recommendation = cache.load_for_date(yesterday)
        if recommendation is None:
            logger.warning("No yesterday food recommendation cache available for fallback.")
            return None
        logger.info(
            "Using yesterday's cached food recommendation fallback: "
            f"{recommendation.title} ({recommendation.date})"
        )

    payload = AmbientFoodRecommendationPayload(
        date=recommendation.date,
        title=recommendation.title,
        detail=recommendation.detail,
        provider=recommendation.provider,
    )

    event = AmbientFoodRecommendationEvent(
        event_id=uuid7(),
        type="ambient.food_recommendation",
        source="wallboard-worker",
        occurred_at=now,
        monotonic_ms=int(time.monotonic() * 1000),
        door_id=settings.door_id,
        trace_id=uuid.uuid4(),
        payload=payload,
    )

    url = f"{settings.control_plane_url.rstrip('/')}/ingest"
    token = get_ingest_token(settings)
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    raw_event = event.model_dump(mode="json")
    batch = {"batch_id": f"worker-food-{int(time.time())}", "events": [raw_event]}

    try:
        resp = httpx.post(url, json=batch, headers=headers, timeout=5.0)
        if resp.status_code == 200:
            logger.info(
                f"Ingested food recommendation event successfully. Title: {recommendation.title}"
            )
            return resp.json()
        else:
            logger.error(f"Ingestion failed with status {resp.status_code}: {resp.text}")
    except Exception as exc:
        logger.error(f"Failed to post food recommendation event: {exc}")

    return None
