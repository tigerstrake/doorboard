from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
INSTALL_SCRIPT = REPOSITORY_ROOT / "deploy/pi-bird/install-avian-visitors.sh"
VERIFY_SCRIPT = REPOSITORY_ROOT / "deploy/pi-bird/verify-avian-visitors.sh"
PIN = "1b33a3cbc4f3b1fe0f9987e2a381ef970283931f"


def test_bird_node_scripts_are_valid_bash() -> None:
    subprocess.run(
        ["bash", "-n", str(INSTALL_SCRIPT), str(VERIFY_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_bird_node_scripts_fail_closed_without_explicit_marker() -> None:
    environment = os.environ.copy()
    environment.pop("DOORBOARD_BIRD_NODE", None)

    for script in (INSTALL_SCRIPT, VERIFY_SCRIPT):
        result = subprocess.run(
            [str(script)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=5,
        )
        assert result.returncode != 0
        assert "DOORBOARD_BIRD_NODE=1" in result.stderr


def test_install_and_verify_scripts_enforce_the_same_reviewed_pin() -> None:
    for script in (INSTALL_SCRIPT, VERIFY_SCRIPT):
        source = script.read_text(encoding="utf-8")
        match = re.search(r'^readonly AVIAN_COMMIT="([0-9a-f]{40})"$', source, re.MULTILINE)
        assert match is not None
        assert match.group(1) == PIN

    installer = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "update_birdnet[.]sh" in installer
    assert "disk_check[.]sh" in installer
    assert "disk_species_clean[.]sh" in installer
    assert "/etc/sudoers.d/010_caddy-nopasswd" in installer
    assert "web_terminal.service livestream.service icecast2.service" in installer
    assert "curl | bash" not in installer
