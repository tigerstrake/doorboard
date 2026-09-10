"""The moderation log is age-bounded (audit 2026-09, P3).

It gained a row for every public write and every moderation action and had no retention
at all — an append-only, person-adjacent audit table growing forever on the stealable
Pi's SSD. Rows older than ``DOOR_API_SOCIAL_MODERATION_LOG_RETENTION_DAYS`` (default 90)
are pruned at startup and once a day, and the row count is on /metrics.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from door_api.social.config import SocialConfig
from door_api.social.service import SocialService
from door_api.social.store import SocialStore
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state  # noqa: E402

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _append(store: SocialStore, log_id: str, *, age_days: float) -> None:
    store.append_moderation_log(
        log_id=log_id,
        target_kind="guestbook",
        target_id=f"entry-{log_id}",
        action="created",
        actor="visitor",
        created_at=_iso(NOW - timedelta(days=age_days)),
    )


def _service(**overrides: Any) -> SocialService:
    config = SocialConfig(db_path=":memory:", **overrides)
    return SocialService(config=config, store=SocialStore(config.db_path), on_event=lambda _: None)


# -- store ---------------------------------------------------------------------


def test_store_prunes_only_rows_older_than_the_cutoff() -> None:
    store = SocialStore(":memory:")
    _append(store, "ancient", age_days=400)
    _append(store, "old", age_days=91)
    _append(store, "recent", age_days=1)
    assert store.moderation_log_count() == 3

    deleted = store.prune_moderation_log(older_than=_iso(NOW - timedelta(days=90)))

    assert deleted == 2
    assert store.moderation_log_count() == 1
    assert [row["id"] for row in store.list_moderation_log(limit=10)] == ["recent"]


# -- service -------------------------------------------------------------------


def test_service_prunes_by_the_configured_retention_and_counts_it() -> None:
    service = _service(moderation_log_retention_days=30)
    _append(service.store, "old", age_days=31)
    _append(service.store, "kept", age_days=29)

    assert service.prune_moderation_log(now=NOW) == 1
    assert service.store.moderation_log_count() == 1
    assert service.metrics.to_dict()["social_moderation_log_pruned_total"] == 1
    # Idempotent: a second pass finds nothing.
    assert service.prune_moderation_log(now=NOW) == 0


def test_zero_retention_means_keep_everything() -> None:
    service = _service(moderation_log_retention_days=0)
    _append(service.store, "ancient", age_days=4000)

    assert service.prune_moderation_log(now=NOW) == 0
    assert service.store.moderation_log_count() == 1


def test_the_default_retention_is_ninety_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    monkeypatch.delenv("DOOR_API_SOCIAL_MODERATION_LOG_RETENTION_DAYS", raising=False)
    config = SocialConfig.from_env()
    assert config.moderation_log_retention_days == 90
    assert config.moderation_log_prune_interval_s == 86_400.0

    monkeypatch.setenv("DOOR_API_SOCIAL_MODERATION_LOG_RETENTION_DAYS", "7")
    assert SocialConfig.from_env().moderation_log_retention_days == 7


# -- app -------------------------------------------------------------------------


@pytest.fixture
def fresh_state(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_MODERATION_LOG_RETENTION_DAYS", "30")
    state.__init__()
    yield
    state.shutdown()


def test_startup_prunes_the_log_and_metrics_report_the_row_count(fresh_state: None) -> None:
    # Rows written before this boot; the old one predates the retention window.
    _append(state.social_store, "old", age_days=45)
    _append(state.social_store, "kept", age_days=2)

    state.startup()

    assert state.social_store.moderation_log_count() == 1
    metrics = TestClient(app).get("/metrics").text
    assert "door_api_social_moderation_log_rows 1" in metrics
    assert "social_moderation_log_pruned_total 1" in metrics
    assert "# TYPE door_api_social_moderation_log_rows gauge" in metrics


@pytest.mark.anyio
async def test_the_daily_pruner_keeps_pruning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_MODERATION_LOG_RETENTION_DAYS", "30")
    monkeypatch.setenv("DOOR_API_SOCIAL_MODERATION_LOG_PRUNE_INTERVAL_S", "0.01")
    state.__init__()
    state.startup()  # inside a running loop, so the task is created
    try:
        assert state._moderation_prune_task is not None
        _append(state.social_store, "old", age_days=45)
        for _ in range(200):
            if state.social_store.moderation_log_count() == 0:
                break
            await asyncio.sleep(0.01)
        assert state.social_store.moderation_log_count() == 0
    finally:
        state.shutdown()
        assert state._moderation_prune_task is None
