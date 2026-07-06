"""Mosquitto auth/ACL verification for the NUC broker (T-503).

Starts the *actual* shipped config (`infra/compose/mosquitto/mosquitto.conf`
+ `acl.conf`) in a real `eclipse-mosquitto` container and drives it with
`paho-mqtt` (already a workspace dependency via control-plane-api). Requires
Docker, which this dev sandbox doesn't have but CI does — skips cleanly
when the `docker` CLI or daemon isn't available rather than failing.

Ground truth verified manually against a local Homebrew Mosquitto 2.1.2
before writing this test: an ACL-denied PUBLISH gets a normal PUBACK/no
error at the *publishing* client (QoS 0 has no reject path) — the only
observable effect is that a subscriber with read access never receives the
message. Likewise a SUBSCRIBE with no matching "read"/"readwrite" ACL entry
still gets a successful SUBACK; delivery is silently filtered at publish
time. So this test always asserts on what a legitimate subscriber actually
receives, never on the publisher's or subscriber's own return code.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest

paho = pytest.importorskip("paho.mqtt.client")
from paho.mqtt.enums import CallbackAPIVersion  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
MOSQUITTO_DIR = REPO_ROOT / "infra" / "compose" / "mosquitto"
IMAGE = "eclipse-mosquitto:2"

TEST_PASSWORDS = {
    "control-plane-api": "test-cp-pass",
    "door-pi": "test-pi-pass",
    "home-assistant": "test-ha-pass",
    "ha-discovery": "test-disco-pass",
    "healthcheck": "test-health-pass",
}


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], check=True, capture_output=True, timeout=10)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _docker_available(), reason="docker not available in this environment"
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def broker(tmp_path: Path):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir()
    data_dir.mkdir(mode=0o777)

    shutil.copy(MOSQUITTO_DIR / "mosquitto.conf", config_dir / "mosquitto.conf")
    shutil.copy(MOSQUITTO_DIR / "acl.conf", config_dir / "acl.conf")

    passwd_file = data_dir / "passwd"
    passwd_file.touch()
    for i, (user, password) in enumerate(TEST_PASSWORDS.items()):
        flag = "-c" if i == 0 else ""
        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{data_dir}:/mosquitto/data",
            IMAGE,
            "mosquitto_passwd",
        ]
        if flag:
            cmd.append(flag)
        cmd += ["-b", "/mosquitto/data/passwd", user, password]
        subprocess.run(cmd, check=True, capture_output=True)

    port = _free_port()
    container_name = f"doorboard-test-mosquitto-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "-p",
            f"{port}:1883",
            "-v",
            f"{config_dir}:/mosquitto/config",
            "-v",
            f"{data_dir}:/mosquitto/data",
            IMAGE,
            "mosquitto",
            "-c",
            "/mosquitto/config/mosquitto.conf",
        ],
        check=True,
        capture_output=True,
    )
    try:
        _wait_for_port("127.0.0.1", port, timeout_s=15)
        yield port
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)


def _wait_for_port(host: str, port: int, *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.3)
    msg = f"mosquitto never opened {host}:{port}"
    raise TimeoutError(msg)


def _connect_result(port: int, *, username: str | None, password: str | None) -> int:
    """Returns the CONNACK reason code (0 == accepted)."""
    result: dict[str, int] = {}

    client = paho.Client(CallbackAPIVersion.VERSION2)
    if username is not None:
        client.username_pw_set(username, password)

    def on_connect(_client, _userdata, _flags, reason_code, _properties):
        result["rc"] = int(reason_code)

    client.on_connect = on_connect
    client.connect("127.0.0.1", port, keepalive=5)
    client.loop_start()
    deadline = time.monotonic() + 5
    while "rc" not in result and time.monotonic() < deadline:
        time.sleep(0.05)
    client.loop_stop()
    client.disconnect()
    return result.get("rc", -1)


def test_anonymous_connection_refused(broker: int) -> None:
    rc = _connect_result(broker, username=None, password=None)
    assert rc != 0


def test_valid_credentials_accepted(broker: int) -> None:
    rc = _connect_result(
        broker, username="control-plane-api", password=TEST_PASSWORDS["control-plane-api"]
    )
    assert rc == 0


def _collect_messages(port: int, *, username: str, password: str, topic: str, duration_s: float):
    received: list[tuple[str, bytes]] = []
    client = paho.Client(CallbackAPIVersion.VERSION2)
    client.username_pw_set(username, password)
    client.on_message = lambda _c, _u, msg: received.append((msg.topic, msg.payload))
    client.on_connect = lambda c, _u, _f, _rc, _p: c.subscribe(topic)
    client.connect("127.0.0.1", port, keepalive=5)
    client.loop_start()
    time.sleep(duration_s)
    client.loop_stop()
    client.disconnect()
    return received


def _publish_once(port: int, *, username: str, password: str, topic: str, payload: str) -> None:
    client = paho.Client(CallbackAPIVersion.VERSION2)
    client.username_pw_set(username, password)
    client.connect("127.0.0.1", port, keepalive=5)
    client.loop_start()
    client.publish(topic, payload).wait_for_publish(timeout=5)
    time.sleep(0.2)
    client.loop_stop()
    client.disconnect()


def test_pi_credential_cannot_publish_outside_its_acl(broker: int) -> None:
    port = broker
    received: list[tuple[str, bytes]] = []

    def subscribe_job():
        received.extend(
            _collect_messages(
                port,
                username="home-assistant",
                password=TEST_PASSWORDS["home-assistant"],
                topic="doorboard/#",
                duration_s=2.5,
            )
        )

    sub_thread = threading.Thread(target=subscribe_job)
    sub_thread.start()
    time.sleep(0.5)

    _publish_once(
        port,
        username="door-pi",
        password=TEST_PASSWORDS["door-pi"],
        topic="doorboard/door/button_pressed",
        payload="allowed",
    )
    _publish_once(
        port,
        username="door-pi",
        password=TEST_PASSWORDS["door-pi"],
        topic="doorboard/system/storage_alert",
        payload="should-be-dropped",
    )

    sub_thread.join()

    topics_seen = {topic for topic, _payload in received}
    assert "doorboard/door/button_pressed" in topics_seen
    assert "doorboard/system/storage_alert" not in topics_seen


def test_control_plane_api_credential_is_write_only(broker: int) -> None:
    port = broker
    received: list[tuple[str, bytes]] = []

    def subscribe_job():
        received.extend(
            _collect_messages(
                port,
                username="control-plane-api",
                password=TEST_PASSWORDS["control-plane-api"],
                topic="doorboard/#",
                duration_s=2.0,
            )
        )

    sub_thread = threading.Thread(target=subscribe_job)
    sub_thread.start()
    time.sleep(0.5)

    _publish_once(
        port,
        username="door-pi",
        password=TEST_PASSWORDS["door-pi"],
        topic="doorboard/door/button_pressed",
        payload="should-not-reach-write-only-subscriber",
    )

    sub_thread.join()

    assert received == []
