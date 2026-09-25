"""tools/enrollment-cli against the real door-visiond and door-media apps, over HTTP.

The CLI is a stand-alone script that reaches the two services with plain
``httpx.get``/``httpx.post`` calls, so this serves both apps with uvicorn the way
the Pi does — but on kernel-assigned loopback ports (two pytest processes on one
machine cannot collide), and each server is stopped and joined when the module
finishes, so nothing keeps writing to pytest's capture after it closes.

Environment goes through ``MonkeyPatch`` so ``VISION_MODE=mock`` and the admin
tokens do not leak into every test collected after this one.
"""

from __future__ import annotations

import importlib.util
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
from click.testing import CliRunner
from fastapi import FastAPI

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = REPO_ROOT / "tools" / "enrollment-cli" / "enroll.py"

VISIOND_TOKEN = "test-vision-admin"
MEDIA_TOKEN = "test-media-admin"


def _loopback_listener() -> socket.socket:
    """A listening socket on a port the kernel picked; uvicorn adopts it as-is."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


@contextmanager
def _serve(app: FastAPI) -> Iterator[str]:
    sock = _loopback_listener()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, name=f"uvicorn-{app.title}-{port}"
    )
    thread.start()
    try:
        deadline = time.monotonic() + 15.0
        while not server.started:
            if not thread.is_alive():
                raise RuntimeError(f"{app.title} exited before it finished starting")
            if time.monotonic() > deadline:
                raise RuntimeError(f"{app.title} did not start within 15 s")
            time.sleep(0.02)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15.0)
        sock.close()


@pytest.fixture(scope="module")
def served_apps(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SSD_DATA_ROOT", str(tmp_path_factory.mktemp("cli_test_ssd")))
        mp.setenv("DOOR_VISIOND_ADMIN_TOKEN", VISIOND_TOKEN)
        mp.setenv("DOOR_MEDIA_ADMIN_TOKEN", MEDIA_TOKEN)
        mp.setenv("VISION_MODE", "mock")

        from door_media import settings as media_settings
        from door_media.app import app as media_app
        from door_visiond import settings as visiond_settings
        from door_visiond.app import app as visiond_app

        # Both services cache Settings() in a module singleton, and another suite in the
        # same session may already have filled it from its own environment.
        media_settings.reset_settings()
        visiond_settings.reset_settings()
        try:
            with _serve(visiond_app) as visiond_url, _serve(media_app) as media_url:
                yield visiond_url, media_url
        finally:
            media_settings.reset_settings()
            visiond_settings.reset_settings()


def _load_cli():  # noqa: ANN202 — a click.Group loaded from a script, not a package
    spec = importlib.util.spec_from_file_location("enroll", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.cli


def test_cli_guided_enroll_and_unenroll(served_apps: tuple[str, str]) -> None:
    visiond_url, media_url = served_apps
    cli = _load_cli()
    runner = CliRunner()

    # Guided enroll, answering every prompt in order:
    #   consent, three captures (Enter each), display name, profile, colour, no sound.
    inputs = [
        "y",  # consent
        "",  # capture 1
        "",  # capture 2
        "",  # capture 3
        "TestCLIUser",
        "mint_pulse",
        "#00ff00",
        "",  # no sound
    ]
    result = runner.invoke(
        cli,
        [
            "enroll",
            "--visiond-url",
            visiond_url,
            "--media-url",
            media_url,
            "--images-count",
            "3",
            "--token",
            VISIOND_TOKEN,
            "--media-token",
            MEDIA_TOKEN,
        ],
        input="\n".join(inputs) + "\n",
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Enrollment successful!" in result.output
    assert "Generated Person ID: prs_" in result.output

    person_id = None
    for line in result.output.splitlines():
        if "Generated Person ID:" in line:
            person_id = line.split("Generated Person ID:")[-1].strip()
            break

    assert person_id is not None
    assert person_id.startswith("prs_")

    unenroll_result = runner.invoke(
        cli,
        [
            "unenroll",
            person_id,
            "--visiond-url",
            visiond_url,
            "--token",
            VISIOND_TOKEN,
        ],
    )
    assert unenroll_result.exit_code == 0, f"Unenroll failed: {unenroll_result.output}"
    assert f"Successfully unenrolled {person_id}" in unenroll_result.output


def test_cli_refuses_to_enrol_without_a_frame_unless_mock_frames_are_allowed(
    served_apps: tuple[str, str],
) -> None:
    """A snapshot failure used to be papered over with placeholder bytes on every run,
    hardware included, leaving door-visiond's quality gate as the only thing between a
    dead camera and a garbage enrollment. The fallback is now opt-in."""
    visiond_url, _media_url = served_apps
    cli = _load_cli()
    runner = CliRunner()
    # A media URL nothing listens on: every capture fails.
    dead_media = "http://127.0.0.1:9"
    common = ["--visiond-url", visiond_url, "--media-url", dead_media, "--images-count", "1"]
    common += ["--token", VISIOND_TOKEN, "--media-token", MEDIA_TOKEN]
    inputs = "y\n\nNoFrame\nmint_pulse\n#00ff00\n\n"

    refused = runner.invoke(cli, ["enroll", *common], input=inputs)
    assert refused.exit_code != 0
    assert "--allow-mock-frames" in refused.output
    assert "Enrollment successful" not in refused.output

    allowed = runner.invoke(cli, ["enroll", *common, "--allow-mock-frames"], input=inputs)
    assert allowed.exit_code == 0, allowed.output
    assert "Substituting placeholder image bytes" in allowed.output
    assert "Enrollment successful!" in allowed.output
