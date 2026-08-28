"""Orphan finalize-temp cleanup (audit backlog: temp files leak on SIGKILL).

A `.muxed_*`/`.concat_*.txt` in the recordings dir and a `.m4a` in the audio tmp dir are cleaned
by the finalize success/error paths, but a SIGKILL/power-loss mid-finalize skips them, so they
pile up on the SSD forever. `sweep_orphan_temp_files` removes them at startup; a completed
recording must never be touched.
"""

from __future__ import annotations

from pathlib import Path

from door_media.service import sweep_orphan_temp_files


def test_sweep_removes_orphans_and_keeps_real_recordings(tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    rec.mkdir()
    audio = tmp_path / "audio-tmp"
    audio.mkdir()

    (rec / ".muxed_bell_abc.mp4").write_bytes(b"x")
    (rec / ".concat_bell_abc.txt").write_text("file 'seg'")
    (audio / "abc.m4a").write_bytes(b"x")

    # These must survive: a finished recording, its thumbnail, and a non-orphan name.
    (rec / "bell_abc.mp4").write_bytes(b"real clip")
    (rec / "bell_abc.jpg").write_bytes(b"thumb")

    removed = sweep_orphan_temp_files(rec, audio)

    assert removed == 3
    assert not (rec / ".muxed_bell_abc.mp4").exists()
    assert not (rec / ".concat_bell_abc.txt").exists()
    assert not (audio / "abc.m4a").exists()
    assert (rec / "bell_abc.mp4").read_bytes() == b"real clip"
    assert (rec / "bell_abc.jpg").exists()


def test_sweep_is_a_noop_when_nothing_to_do(tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    rec.mkdir()
    # Audio tmp dir absent (never created because no recording had audio yet) — must not raise.
    assert sweep_orphan_temp_files(rec, tmp_path / "audio-tmp") == 0
