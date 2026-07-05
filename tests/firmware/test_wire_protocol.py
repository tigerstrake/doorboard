from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_wire_protocol_host_conformance(tmp_path: Path) -> None:
    compiler = shutil.which("cc")
    assert compiler is not None, "host C compiler 'cc' is required"

    repo_root = Path(__file__).resolve().parents[2]
    protocol_dir = repo_root / "firmware/esp32-door-controller/components/door_protocol"
    binary = tmp_path / "wire_protocol_conformance"

    compile_cmd = [
        compiler,
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Werror",
        f"-I{protocol_dir / 'include'}",
        str(protocol_dir / "door_protocol.c"),
        str(repo_root / "tests/firmware/wire_protocol_conformance.c"),
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
    assert "wire protocol conformance passed" in result.stdout
