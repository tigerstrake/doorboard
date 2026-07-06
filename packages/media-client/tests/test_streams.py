from __future__ import annotations

import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar, cast

import pytest
from doorboard_media_client import DoorMediaClient, DoorMediaClientError


class _Handler(BaseHTTPRequestHandler):
    payload: ClassVar[object] = [
        {
            "name": "visitor",
            "whep_url": "mock:/visitor",
            "stream_up": True,
            "webrtc_clients": 0,
        }
    ]
    status: ClassVar[int] = 200

    def do_GET(self) -> None:
        if self.path != "/streams":
            self.send_response(404)
            self.end_headers()
            return

        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _server(payload: object, *, status: int = 200) -> Generator[str]:
    _Handler.payload = payload
    _Handler.status = status
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = cast(tuple[str, int], server.server_address)
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_list_streams_parses_metadata() -> None:
    with _server(
        [
            {
                "name": "visitor",
                "whep_url": "mock:/visitor",
                "stream_up": True,
                "webrtc_clients": 2,
            }
        ]
    ) as url:
        client = DoorMediaClient(url)
        streams = client.list_streams()

    assert streams[0].name == "visitor"
    assert streams[0].whep_url == "mock:/visitor"
    assert streams[0].stream_up is True
    assert streams[0].webrtc_clients == 2


def test_get_stream_returns_none_for_missing_stream() -> None:
    with _server([]) as url:
        assert DoorMediaClient(url).get_stream("visitor") is None


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "visitor"},
        [{"name": "visitor", "whep_url": "mock:/visitor", "stream_up": "yes", "webrtc_clients": 0}],
        [{"name": "visitor", "whep_url": "mock:/visitor", "stream_up": True, "webrtc_clients": -1}],
    ],
)
def test_list_streams_rejects_well_formed_invalid_payloads(payload: object) -> None:
    with _server(payload) as url:
        client = DoorMediaClient(url)
        with pytest.raises(DoorMediaClientError):
            client.list_streams()


def test_list_streams_wraps_http_errors() -> None:
    with _server({"detail": "down"}, status=503) as url:
        client = DoorMediaClient(url)
        with pytest.raises(DoorMediaClientError):
            client.list_streams()
