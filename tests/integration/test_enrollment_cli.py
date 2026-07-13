import os
import sys
import threading
import time

import pytest
import uvicorn
from click.testing import CliRunner

# Add repository root to sys.path to find tools directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))


# Set up test environment variables before importing apps
@pytest.fixture(scope="module", autouse=True)
def setup_test_env(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("cli_test_ssd")
    os.environ["SSD_DATA_ROOT"] = str(tmp_dir)
    os.environ["DOOR_VISIOND_ADMIN_TOKEN"] = "test-vision-admin"
    os.environ["DOOR_MEDIA_ADMIN_TOKEN"] = "test-media-admin"
    os.environ["VISION_MODE"] = "mock"
    os.environ["DOOR_VISIOND_BIND"] = "127.0.0.1:8089"
    os.environ["DOOR_MEDIA_BIND"] = "127.0.0.1:8092"
    yield


# Start background servers
@pytest.fixture(scope="module")
def run_servers(setup_test_env):
    from door_media.app import app as media_app
    from door_visiond.app import app as visiond_app

    def start_visiond():
        uvicorn.run(visiond_app, host="127.0.0.1", port=8089, log_level="warning")

    def start_media():
        uvicorn.run(media_app, host="127.0.0.1", port=8092, log_level="warning")

    t1 = threading.Thread(target=start_visiond, daemon=True)
    t2 = threading.Thread(target=start_media, daemon=True)
    t1.start()
    t2.start()

    # Wait for uvicorn to bind and start accepting connections
    time.sleep(1.5)
    yield "http://127.0.0.1:8089", "http://127.0.0.1:8092"


def test_cli_guided_enroll_and_unenroll(run_servers):
    visiond_url, media_url = run_servers
    import importlib.util

    cli_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../tools/enrollment-cli/enroll.py")
    )
    spec = importlib.util.spec_from_file_location("enroll", cli_path)
    enroll_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(enroll_module)
    cli = enroll_module.cli

    runner = CliRunner()

    # Test Guided Enroll
    # Inputs:
    # 1. consent_confirmed: 'y'
    # 2. image captures: press enter 3 times (since images_count=3)
    # 3. display_name: 'TestCLIUser'
    # 4. profile_id: 'mint_pulse'
    # 5. color: '#00ff00'
    # 6. sound: ''
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
            "test-vision-admin",
            "--media-token",
            "test-media-admin",
        ],
        input="\n".join(inputs) + "\n",
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Enrollment successful!" in result.output
    assert "Generated Person ID: prs_" in result.output

    # Extract Person ID from output
    # e.g., "Generated Person ID: prs_xxxxxx"
    person_id = None
    for line in result.output.splitlines():
        if "Generated Person ID:" in line:
            person_id = line.split("Generated Person ID:")[-1].strip()
            break

    assert person_id is not None
    assert person_id.startswith("prs_")

    # Test Unenroll
    unenroll_result = runner.invoke(
        cli,
        [
            "unenroll",
            person_id,
            "--visiond-url",
            visiond_url,
            "--token",
            "test-vision-admin",
        ],
    )
    assert unenroll_result.exit_code == 0, f"Unenroll failed: {unenroll_result.output}"
    assert f"Successfully unenrolled {person_id}" in unenroll_result.output
