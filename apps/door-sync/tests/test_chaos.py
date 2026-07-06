"""Crash-consistency (chaos) tests — the unforgiving T-502 property.

Acceptance: "random kill -9 during every phase (enqueue, mid-upload, pre-verify,
post-verify) → zero lost items, zero premature deletions, zero duplicates on the
far side."

A ``kill -9`` is modelled by a :class:`CrashSignal` (a ``BaseException``, so it
sails past the engine's ``except TransientError/PermanentError`` exactly like an
uncaught SIGKILL) raised at an injectable seam, after which the queue is closed
and **reopened from disk** and a fresh engine resumes. The archive is a real
filesystem (:class:`FilesystemNasTarget`) so duplicates and partial writes are
observable across restarts, and door-media is modelled by
:class:`VerifyingDoorMedia`, which raises the instant it is asked to license a
deletion whose verified archive copy is not already present — turning "premature
deletion" into a failure at the moment of the violation.
"""

from __future__ import annotations

import contextlib
import random
from pathlib import Path

import pytest
from door_sync.engine import SyncEngine
from door_sync.queue import UploadQueue
from door_sync.settings import Settings
from door_sync.targets import FilesystemNasTarget, MockNucTarget

pytestmark = pytest.mark.anyio


class CrashSignal(BaseException):
    """Stand-in for SIGKILL — not an ``Exception``, so the engine cannot catch it."""


class Chaos:
    def __init__(self, budget: int | None) -> None:
        # None disables injection (recovery run). Otherwise crash on the
        # ``budget``-th seam tick.
        self.remaining = budget

    def tick(self) -> None:
        if self.remaining is None:
            return
        self.remaining -= 1
        if self.remaining == 0:
            raise CrashSignal


class ChaosNasTarget(FilesystemNasTarget):
    def __init__(self, nas_root: Path, chaos: Chaos) -> None:
        super().__init__(nas_root)
        self._chaos = chaos

    async def upload_and_verify(self, *, local_path, dest_key, expected_sha256):  # noqa: ANN001
        import asyncio

        self._chaos.tick()  # mid-upload (before any bytes are written)
        return await asyncio.to_thread(self._chaos_upload, local_path, dest_key, expected_sha256)

    def _chaos_upload(self, local_path: Path, dest_key: str, expected_sha256: str) -> str:
        import os
        import shutil

        from door_sync.targets import PermanentError, TransientError, sha256_file

        if not local_path.exists():
            raise PermanentError("missing")
        if not self._nas_root.exists():
            raise TransientError("nas down")
        dest = self._nas_root / dest_key
        tmp = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with local_path.open("rb") as s, tmp.open("wb") as o:
            shutil.copyfileobj(s, o)
            o.flush()
            os.fsync(o.fileno())
        os.replace(tmp, dest)
        self._chaos.tick()  # pre-verify (dest written, not yet verified)
        actual = sha256_file(dest)
        if actual != expected_sha256:
            raise PermanentError("mismatch")
        self._chaos.tick()  # post-verify (about to return success)
        return actual


class VerifyingDoorMedia:
    def __init__(self, nas_root: Path, dest_keys: dict[str, str], chaos: Chaos) -> None:
        self._nas_root = nas_root
        self._dest_keys = dest_keys
        self._chaos = chaos
        self.licensed: set[str] = set()

    async def list_pending_clips(self) -> list[dict]:
        return []

    async def notify_synced(self, *, recording_id, verified_sha256, item_id, attempts) -> None:  # noqa: ANN001
        from door_sync.targets import sha256_file

        self._chaos.tick()  # post-mark-completed, pre-notify
        rid = str(recording_id)
        dest = self._nas_root / self._dest_keys[rid]
        if not dest.exists() or sha256_file(dest) != verified_sha256:
            msg = f"PREMATURE DELETION LICENSE for {rid}"
            raise AssertionError(msg)
        self.licensed.add(rid)
        self._chaos.tick()  # door-media accepted, before engine marks licensed


def _settings(tmp_path: Path) -> Settings:
    # Spread via a dict so pyright doesn't type-check alias-vs-field kwargs.
    opts: dict[str, object] = {
        "ssd_data_root": tmp_path / "ssd",
        "media_target": "nas",
        "nas_sync_target": str(tmp_path / "nas"),
        "backoff_base_s": 0.0,
        "backoff_max_s": 0.0,
        "max_permanent_attempts": 5,
        "completed_retention_s": 10_000,
    }
    return Settings(**opts)  # type: ignore[arg-type]


def _make_clips(ssd: Path, n: int) -> list[tuple[str, str, str]]:
    import hashlib

    out = []
    for i in range(n):
        rid = f"00000000-0000-7000-8000-{i:012d}"
        rel = f"recordings/{rid}.mp4"
        p = ssd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        data = f"clip-{i}".encode() * (50 + i)
        p.write_bytes(data)
        out.append((rid, rel, hashlib.sha256(data).hexdigest()))
    return out


def _build(settings: Settings, chaos: Chaos, dest_keys: dict[str, str]):
    Path(settings.nas_sync_target).mkdir(parents=True, exist_ok=True)  # the "mount"
    queue = UploadQueue(settings.queue_db_path)
    nas = ChaosNasTarget(Path(settings.nas_sync_target), chaos)
    door_media = VerifyingDoorMedia(Path(settings.nas_sync_target), dest_keys, chaos)
    engine = SyncEngine(
        queue=queue,
        settings=settings,
        media_target=nas,
        nuc_target=MockNucTarget(),
        media_client=door_media,
    )
    return engine, queue, door_media


async def _drain(engine: SyncEngine, queue: UploadQueue) -> None:
    for _ in range(200):
        await engine.run_once()
        stats = queue.stats(now_epoch=1e12)
        if stats.pending == 0 and not queue.items_awaiting_license():
            return
    msg = "drain did not converge"
    raise AssertionError(msg)


def _assert_invariants(settings: Settings, clips: list[tuple[str, str, str]]) -> None:
    nas_root = Path(settings.nas_sync_target)
    # Reopen the queue read-only-ish to inspect final state.
    q = UploadQueue(settings.queue_db_path)
    try:
        for rid, rel, sha in clips:
            item = q.get(rid)
            assert item is not None, f"lost item {rid}"
            assert item.status == "completed", f"{rid} status {item.status}"
            assert item.licensed == 1, f"{rid} not licensed"
            assert item.verified_sha256 == sha
            dest = nas_root / rel
            assert dest.exists(), f"archive missing for {rid}"
            from door_sync.targets import sha256_file

            assert sha256_file(dest) == sha
    finally:
        q.close()
    # Zero duplicates / no partial files left behind.
    part_files = list(nas_root.rglob("*.part"))
    assert part_files == [], f"partial writes left: {part_files}"
    archived = list((nas_root / "recordings").glob("*.mp4"))
    assert len(archived) == len(clips)


async def test_crash_at_every_phase_single_clip(tmp_path: Path) -> None:
    """Exhaustive: crash at each seam (budget 1..6) for one clip, recover, verify."""
    for budget in range(1, 7):
        run_dir = tmp_path / f"b{budget}"
        settings = _settings(run_dir)
        clips = _make_clips(settings.ssd_data_root, 1)
        dest_keys = {rid: rel for rid, rel, _ in clips}

        engine, queue, _dm = _build(settings, Chaos(budget), dest_keys)
        for rid, rel, sha in clips:
            engine.enqueue_recording(recording_id=rid, local_path=rel, sha256=sha, trace_id=rid)
        with contextlib.suppress(CrashSignal):
            await _drain(engine, queue)
        queue.close()

        # Restart: fresh engine, no injection, resume from disk.
        engine2, queue2, _dm2 = _build(settings, Chaos(None), dest_keys)
        await engine2.finalize_licenses()  # startup recovery
        await _drain(engine2, queue2)
        queue2.close()

        _assert_invariants(settings, clips)


async def test_random_kill_100_iterations(tmp_path: Path) -> None:
    """100 iterations: enqueue several clips, crash at a random seam, recover,
    assert zero lost / zero premature-deletion / zero duplicate."""
    rng = random.Random(1337)
    for it in range(100):
        run_dir = tmp_path / f"it{it}"
        settings = _settings(run_dir)
        n = rng.randint(1, 4)
        clips = _make_clips(settings.ssd_data_root, n)
        dest_keys = {rid: rel for rid, rel, _ in clips}

        engine, queue, _dm = _build(settings, Chaos(rng.randint(1, 3 * n + 2)), dest_keys)
        for rid, rel, sha in clips:
            engine.enqueue_recording(recording_id=rid, local_path=rel, sha256=sha, trace_id=rid)
        with contextlib.suppress(CrashSignal):
            await _drain(engine, queue)
        queue.close()

        engine2, queue2, _dm2 = _build(settings, Chaos(None), dest_keys)
        await engine2.finalize_licenses()
        await _drain(engine2, queue2)
        queue2.close()

        _assert_invariants(settings, clips)


async def test_crash_during_enqueue_loses_nothing(tmp_path: Path) -> None:
    """A crash mid-enqueue (before all items are queued) is survivable because the
    source re-delivers on restart; idempotent enqueue means no duplicate rows."""
    settings = _settings(tmp_path)
    clips = _make_clips(settings.ssd_data_root, 3)
    dest_keys = {rid: rel for rid, rel, _ in clips}

    engine, queue, _dm = _build(settings, Chaos(None), dest_keys)
    # Simulate a crash after only the first enqueue commits.
    rid, rel, sha = clips[0]
    engine.enqueue_recording(recording_id=rid, local_path=rel, sha256=sha, trace_id=rid)
    queue.close()

    # Restart: the source re-delivers ALL items (including the one already queued).
    engine2, queue2, _dm2 = _build(settings, Chaos(None), dest_keys)
    for rid, rel, sha in clips:
        engine2.enqueue_recording(recording_id=rid, local_path=rel, sha256=sha, trace_id=rid)
    await _drain(engine2, queue2)
    queue2.close()

    _assert_invariants(settings, clips)
