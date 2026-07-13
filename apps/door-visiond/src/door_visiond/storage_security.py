"""Checks for the dedicated encrypted enrollment volume from ADR-0009."""

from __future__ import annotations

from pathlib import Path


def is_luks_backed(
    path: Path,
    *,
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
    sys_dev_block: Path = Path("/sys/dev/block"),
) -> bool:
    """Return whether *path* is on a device-mapper LUKS mount.

    The device-mapper UUID is checked instead of trusting a mount-point name or
    marker file, either of which could accidentally exist on the unencrypted
    parent filesystem.
    """
    try:
        resolved = path.resolve()
        candidates: list[tuple[int, str]] = []
        for line in mountinfo_path.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) < 6:
                continue
            mountpoint = Path(_unescape_mount(fields[4])).resolve()
            if resolved == mountpoint or mountpoint in resolved.parents:
                candidates.append((len(mountpoint.parts), fields[2]))
        if not candidates:
            return False
        _depth, major_minor = max(candidates)
        dm_uuid = (sys_dev_block / major_minor / "dm" / "uuid").read_text(encoding="utf-8")
        return dm_uuid.strip().upper().startswith("CRYPT-LUKS")
    except OSError:
        return False


def _unescape_mount(value: str) -> str:
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")
