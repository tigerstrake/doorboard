"""Shared fixtures + helpers for door-visiond tests.

The sentinel technique (ADR-0009 §7): embeddings carry a recognizable ASCII
marker; tests then byte-scan files/logs to *prove* the vector never leaked.
"""

from __future__ import annotations

import logging
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from door_visiond.app import app
from door_visiond.clock import FakeClock
from door_visiond.identity_cache import IdentityCache
from door_visiond.matcher import Matcher
from door_visiond.pipeline import DetectedFace, FrameCapture, PipelineCore
from door_visiond.settings import Settings, override_settings, reset_settings
from doorboard_contracts.events import DoorboardEvent
from fastapi.testclient import TestClient

# Small model dim keeps pure-Python matching fast in tests (>= 8 for a sentinel).
TEST_DIM = 64

_SENTINEL_PREFIX = b"DOORBOARD_SENTINEL_"  # 19 bytes


def sentinel(tag: str) -> bytes:
    """Return a 32-byte ASCII sentinel (multiple of 4 → exact float32 round-trip)."""
    suffix = tag.encode("ascii").ljust(13, b"0")[:13]
    marker = _SENTINEL_PREFIX + suffix
    assert len(marker) == 32
    return marker


def scan_tree_for(root: Path, needle: bytes) -> list[Path]:
    """Return every file under *root* whose raw bytes contain *needle*."""
    hits: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and needle in path.read_bytes():
            hits.append(path)
    return hits


@pytest.fixture
def ssd_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("SSD_DATA_ROOT", str(tmp_path / "ssd"))
    monkeypatch.setenv("VISION_MODE", "mock")
    monkeypatch.setenv("DOOR_VISIOND_ADMIN_TOKEN", "")
    monkeypatch.setenv("VISIOND_MODEL_DIM", str(TEST_DIM))
    return Settings()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def collected() -> list[DoorboardEvent]:
    return []


@pytest.fixture
def core_env(
    ssd_settings: Settings, clock: FakeClock, collected: list[DoorboardEvent]
) -> tuple[PipelineCore, Matcher, IdentityCache]:
    matcher = Matcher(ssd_settings.match_threshold)
    cache = IdentityCache()
    core = PipelineCore(
        matcher=matcher,
        cache=cache,
        sink=collected.append,
        clock=clock,
        door_id="primary",
        min_face_px=ssd_settings.min_face_px,
        ttl_ms=ssd_settings.identity_cache_ttl_ms,
        cooldown_ms=ssd_settings.greeting_cooldown_ms,
        stability_window=ssd_settings.stability_window,
        stability_required=ssd_settings.stability_required,
    )
    return core, matcher, cache


def face(embedding, size_px: int = 120) -> FrameCapture:
    return FrameCapture(
        faces=(DetectedFace(size_px=size_px, embedding=embedding),), inference_ms=5.0
    )


@contextmanager
def capture_logs(name: str = "door_visiond") -> Iterator[list[logging.LogRecord]]:
    """Capture every record on the given logger tree at DEBUG."""
    logger = logging.getLogger(name)
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collector(level=logging.DEBUG)
    prev_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)


@pytest.fixture
def client(ssd_settings: Settings) -> Generator[TestClient, None, None]:
    override_settings(ssd_settings)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        reset_settings()
