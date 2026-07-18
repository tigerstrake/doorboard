from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wallboard_worker.jobs import get_ingest_token
from wallboard_worker.scheduler import ScheduledJob, Scheduler, build_jobs
from wallboard_worker.settings import Settings


def test_scheduler_isolates_job_failure_and_updates_heartbeat(tmp_path: Path) -> None:
    calls: list[str] = []

    def fail() -> None:
        calls.append("fail")
        raise RuntimeError("job failed")

    def succeed() -> None:
        calls.append("succeed")

    heartbeat = tmp_path / "heartbeat"
    scheduler = Scheduler(
        Settings(WALLBOARD_WORKER_HEARTBEAT_PATH=heartbeat),
        jobs=[
            ScheduledJob("failing", 10.0, fail),
            ScheduledJob("healthy", 10.0, succeed),
        ],
    )

    scheduler.run_once()

    assert calls == ["fail", "succeed"]
    assert heartbeat.exists()


def test_build_jobs_registers_only_enabled_features() -> None:
    settings = Settings(
        FEATURE_BIRDNET=True,
        FEATURE_SATELLITES=False,
        FEATURE_AIRCRAFT=True,
        FEATURE_PRINTER=False,
        FEATURE_FOOD=True,
        WALLBOARD_WORKER_INGEST_TOKEN="test-ingest-token",
    )

    names = {job.name for job in build_jobs(settings, force_mock=True)}

    assert names == {
        "bird-summary",
        "daily-collage",
        "aircraft-summary",
        "food-recommendation",
    }


@patch("httpx.post")
def test_bootstrap_ingest_token_is_reused(mock_post: MagicMock) -> None:
    response = MagicMock(status_code=200)
    response.json.return_value = {"token": "tok_once"}
    mock_post.return_value = response
    settings = Settings(CONTROL_PLANE_ADMIN_TOKEN="dev-admin")

    assert get_ingest_token(settings) == "tok_once"
    assert get_ingest_token(settings) == "tok_once"
    assert mock_post.call_count == 1


def test_preissued_ingest_token_never_uses_admin_endpoint() -> None:
    settings = Settings(
        WALLBOARD_WORKER_INGEST_TOKEN="tok_preissued",
        CONTROL_PLANE_ADMIN_TOKEN="must-not-be-used",
    )
    with patch("httpx.post") as post:
        assert get_ingest_token(settings) == "tok_preissued"
    post.assert_not_called()
