from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_effects_library_host_conformance(tmp_path: Path) -> None:
    compiler = shutil.which("cc")
    assert compiler is not None, "host C compiler 'cc' is required"

    repo_root = Path(__file__).resolve().parents[2]
    effects_dir = repo_root / "firmware/esp32-door-controller/components/door_effects"
    binary = tmp_path / "effects_conformance"

    compile_cmd = [
        compiler,
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Werror",
        f"-I{effects_dir / 'include'}",
        str(effects_dir / "door_effects.c"),
        str(repo_root / "tests/firmware/effects_conformance.c"),
        "-o",
        str(binary),
        "-lm",  # Link math library
    ]

    subprocess.run(compile_cmd, check=True, cwd=repo_root)
    result = subprocess.run(
        [str(binary)],
        check=True,
        cwd=repo_root,
        text=True,
        capture_output=True,
    )
    assert "door effects library conformance passed" in result.stdout
