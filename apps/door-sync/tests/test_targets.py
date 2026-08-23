"""The NAS mount marker — the guard that keeps ADR-0007 honest.

``nas_root`` existing and being writable proves nothing about the share being
mounted: when the NAS is down, the bare mountpoint is still there on the Pi's
microSD rootfs, so an upload writes happily to the one storage tier ADR-0007
forbids for recordings, verifies its own read-back, and licenses door-media to
delete the SSD original. These tests pin the marker check that closes that hole
and the *class* of the failure — transient, so an unmounted share retries
forever instead of dead-lettering good clips.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from door_sync.targets import (
    DEFAULT_NAS_MOUNT_MARKER,
    FilesystemNasTarget,
    TransientError,
)

pytestmark = pytest.mark.anyio


def _local_clip(tmp_path: Path) -> tuple[Path, str]:
    src = tmp_path / "ssd" / "recordings" / "clip.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    data = b"video-bytes" * 100
    src.write_bytes(data)
    return src, hashlib.sha256(data).hexdigest()


async def test_upload_refuses_an_unmounted_share_that_looks_writable(tmp_path: Path) -> None:
    """The unmounted-NAS case: directory present, writable, marker absent."""
    src, digest = _local_clip(tmp_path)
    nas_root = tmp_path / "mnt" / "nas"  # bare mountpoint on the rootfs
    nas_root.mkdir(parents=True)
    assert nas_root.is_dir()

    target = FilesystemNasTarget(nas_root)

    # TransientError, not PermanentError: the item is fine, the mount is not, so
    # this must retry forever rather than dead-letter a perfectly good clip.
    with pytest.raises(TransientError) as excinfo:
        await target.upload_and_verify(
            local_path=src, dest_key="recordings/clip.mp4", expected_sha256=digest
        )
    assert DEFAULT_NAS_MOUNT_MARKER in str(excinfo.value)

    # Nothing was written to the local disk masquerading as the share, and the
    # marker was not helpfully created on the way out.
    assert not (nas_root / "recordings").exists()
    assert not (nas_root / DEFAULT_NAS_MOUNT_MARKER).exists()


async def test_upload_proceeds_when_the_marker_is_present(tmp_path: Path) -> None:
    src, digest = _local_clip(tmp_path)
    nas_root = tmp_path / "mnt" / "nas"
    nas_root.mkdir(parents=True)
    (nas_root / DEFAULT_NAS_MOUNT_MARKER).touch()

    target = FilesystemNasTarget(nas_root)
    verified = await target.upload_and_verify(
        local_path=src, dest_key="recordings/clip.mp4", expected_sha256=digest
    )

    assert verified == digest
    assert (nas_root / "recordings" / "clip.mp4").read_bytes() == src.read_bytes()


async def test_marker_name_is_configurable(tmp_path: Path) -> None:
    src, digest = _local_clip(tmp_path)
    nas_root = tmp_path / "mnt" / "nas"
    nas_root.mkdir(parents=True)
    (nas_root / DEFAULT_NAS_MOUNT_MARKER).touch()

    target = FilesystemNasTarget(nas_root, mount_marker=".other-share")
    with pytest.raises(TransientError):
        await target.upload_and_verify(
            local_path=src, dest_key="recordings/clip.mp4", expected_sha256=digest
        )
