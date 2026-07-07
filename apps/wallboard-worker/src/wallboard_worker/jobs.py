from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, UTC, timedelta
import httpx

from doorboard_contracts.events import (
    AmbientFoodRecommendationEvent,
    AmbientFoodRecommendationPayload,
)
from wallboard_worker.settings import Settings
from wallboard_worker.uuid7 import uuid7
from food_recommendation.provider import FoodRecommendationProvider

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
            logger.warning(
                f"Failed to fetch ingest token, status {resp.status_code}: {resp.text}"
            )
    except Exception as e:
        logger.warning(f"Failed to connect to control plane for token: {e}")
    return ""


def run_food_recommendation(
    settings: Settings, provider: FoodRecommendationProvider, now: datetime | None = None
) -> dict | None:
    """Fetch daily food recommendation and ingest it into the control plane."""
    if now is None:
        now = datetime.now(UTC)

    target_date = now.date()
    try:
        recommendation = provider.get_daily_recommendation(target_date)
    except Exception as exc:
        logger.warning(f"Failed to get daily food recommendation for {target_date}: {exc}. Trying yesterday's fallback.")
        # Outage fallback: yesterday's recommendation
        try:
            recommendation = provider.get_daily_recommendation(target_date - timedelta(days=1))
        except Exception as fallback_exc:
            logger.error(f"Fallback food recommendation also failed: {fallback_exc}")
            return None

    # Construct payload
    payload = AmbientFoodRecommendationPayload(
        date=recommendation.date,
        title=recommendation.title,
        detail=recommendation.detail,
    )

    # Construct event
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

    # Ingest event
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
            logger.info(f"Ingested food recommendation event successfully. Title: {recommendation.title}")
            return resp.json()
        else:
            logger.error(
                f"Ingestion failed with status {resp.status_code}: {resp.text}"
            )
    except Exception as exc:
        logger.error(f"Failed to post food recommendation event: {exc}")

    return None
