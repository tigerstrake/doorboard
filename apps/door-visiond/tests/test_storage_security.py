from __future__ import annotations

from pathlib import Path

from door_visiond.storage_security import is_luks_backed


def _write_fixture(tmp_path: Path, *, dm_uuid: str) -> tuple[Path, Path]:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "36 25 253:2 / /mnt/vision-enrollment rw,relatime - ext4 /dev/mapper/vision rw\n",
        encoding="utf-8",
    )
    sys_dev = tmp_path / "sys-dev-block"
    dm_dir = sys_dev / "253:2" / "dm"
    dm_dir.mkdir(parents=True)
    (dm_dir / "uuid").write_text(dm_uuid, encoding="utf-8")
    return mountinfo, sys_dev


def test_accepts_device_mapper_luks_mount(tmp_path: Path) -> None:
    mountinfo, sys_dev = _write_fixture(tmp_path, dm_uuid="CRYPT-LUKS2-abc-vision")
    assert is_luks_backed(
        Path("/mnt/vision-enrollment/doorboard"),
        mountinfo_path=mountinfo,
        sys_dev_block=sys_dev,
    )


def test_rejects_plain_device_mapper_mount(tmp_path: Path) -> None:
    mountinfo, sys_dev = _write_fixture(tmp_path, dm_uuid="LVM-some-volume")
    assert not is_luks_backed(
        Path("/mnt/vision-enrollment"),
        mountinfo_path=mountinfo,
        sys_dev_block=sys_dev,
    )


def test_rejects_missing_mount_metadata(tmp_path: Path) -> None:
    assert not is_luks_backed(
        Path("/mnt/vision-enrollment"),
        mountinfo_path=tmp_path / "missing",
        sys_dev_block=tmp_path,
    )
