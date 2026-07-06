"""Thin door-media HTTP client used by diagnostics and tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


class DoorMediaClientError(RuntimeError):
    """Raised when door-media stream metadata cannot be fetched or parsed."""


@dataclass(frozen=True, slots=True)
class StreamMetadata:
    name: str
    whep_url: str
    stream_up: bool
    webrtc_clients: int


class DoorMediaClient:
    """Small synchronous client for door-media's diagnostics-safe HTTP API."""

    def __init__(self, base_url: str, *, timeout_s: float = 3.0) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._timeout_s = timeout_s

    def list_streams(self) -> list[StreamMetadata]:
        payload = self._get_json("streams")
        return _parse_streams(payload)

    def get_stream(self, name: str) -> StreamMetadata | None:
        for stream in self.list_streams():
            if stream.name == name:
                return stream
        return None

    def _get_json(self, path: str) -> Any:
        url = urljoin(self._base_url, path)
        request = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self._timeout_s) as response:
                status = response.status
                body = response.read()
        except HTTPError as exc:
            raise DoorMediaClientError(f"door-media returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise DoorMediaClientError(f"door-media request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise DoorMediaClientError("door-media request timed out") from exc

        if status < 200 or status >= 300:
            raise DoorMediaClientError(f"door-media returned HTTP {status}")

        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DoorMediaClientError("door-media returned invalid JSON") from exc


def _parse_streams(payload: Any) -> list[StreamMetadata]:
    if not isinstance(payload, list):
        raise DoorMediaClientError("stream metadata payload must be a list")

    streams: list[StreamMetadata] = []
    for raw_item in cast(list[object], payload):
        if not isinstance(raw_item, dict):
            raise DoorMediaClientError("stream metadata entry must be an object")
        item = cast(dict[str, object], raw_item)
        name = item.get("name")
        whep_url = item.get("whep_url")
        stream_up = item.get("stream_up")
        webrtc_clients = item.get("webrtc_clients")
        if (
            not isinstance(name, str)
            or not isinstance(whep_url, str)
            or not isinstance(stream_up, bool)
            or not isinstance(webrtc_clients, int)
            or webrtc_clients < 0
        ):
            raise DoorMediaClientError("stream metadata entry has invalid fields")
        streams.append(
            StreamMetadata(
                name=name,
                whep_url=whep_url,
                stream_up=stream_up,
                webrtc_clients=webrtc_clients,
            )
        )
    return streams
