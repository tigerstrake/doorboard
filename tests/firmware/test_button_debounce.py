from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_button_debounce_host_conformance(tmp_path: Path) -> None:
    compiler = shutil.which("cc")
    assert compiler is not None, "host C compiler 'cc' is required"

    repo_root = Path(__file__).resolve().parents[2]
    button_dir = repo_root / "firmware/esp32-door-controller/components/door_button"
    binary = tmp_path / "button_conformance"

    compile_cmd = [
        compiler,
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Werror",
        f"-I{button_dir / 'include'}",
        str(button_dir / "door_button.c"),
        str(repo_root / "tests/firmware/button_conformance.c"),
        "-o",
        str(binary),
    ]

    subprocess.run(compile_cmd, check=True, cwd=repo_root)
    result = subprocess.run(
        [str(binary)],
        check=True,
        cwd=repo_root,
        text=True,
        capture_output=True,
    )
    assert "button debounce conformance passed" in result.stdout
