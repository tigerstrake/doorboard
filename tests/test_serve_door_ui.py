"""The kiosk static server: what may be framed, cached and faked (ADR-0049).

Every door-ui path is unframable (X-Frame-Options DENY). The one exception is the
games directory: the wallboard embeds Body Surfers from the other loopback name so it
runs in its own renderer process, which only works if those pages allow the wallboard's
two origins as frame ancestors. A missing game file must be a real 404, never the SPA
shell, and game HTML must not be cached any more than the kiosk shell is.
"""

from __future__ import annotations

import importlib.util
import threading
import urllib.error
import urllib.request
from collections.abc import Generator
from email.message import Message
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "serve_door_ui.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("serve_door_ui", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server(tmp_path: Path) -> Generator[str, None, None]:
    (tmp_path / "index.html").write_text("<!doctype html><title>kiosk</title>")
    game = tmp_path / "games" / "body-surfers"
    (game / "assets").mkdir(parents=True)
    (game / "index.html").write_text("<!doctype html><title>game</title>")
    (game / "assets" / "app.js").write_text("console.log('game')")
    (game / "vision.wasm").write_bytes(b"\0asm\1\0\0\0")

    module = _load_script()

    def handler(*args: object, **kwargs: object) -> object:
        return module.SpaHandler(*args, directory=str(tmp_path), **kwargs)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)  # type: ignore[arg-type]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url: str) -> tuple[int, Message]:
    request = urllib.request.Request(url, headers={"Accept": "text/html"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.headers


def test_kiosk_paths_stay_unframable(server: str) -> None:
    for path in ("/", "/wallboard", "/index.html"):
        status, headers = _get(server + path)
        assert status == 200, path
        assert headers["X-Frame-Options"] == "DENY", path
        assert headers["Content-Security-Policy"] is None, path


def test_game_pages_are_framable_only_by_the_wallboard_origins(server: str) -> None:
    port = server.rsplit(":", 1)[1]
    for path in ("/games/body-surfers/", "/games/body-surfers/index.html"):
        status, headers = _get(server + path)
        assert status == 200, path
        assert headers["X-Frame-Options"] is None, path
        assert headers["Content-Security-Policy"] == (
            f"frame-ancestors http://127.0.0.1:{port} http://localhost:{port}"
        ), path
        assert headers["Cache-Control"] == "no-store", path


def test_game_assets_are_cached_and_wasm_has_its_type(server: str) -> None:
    status, headers = _get(server + "/games/body-surfers/assets/app.js")
    assert status == 200
    assert headers["Cache-Control"] == "public, max-age=3600"

    status, headers = _get(server + "/games/body-surfers/vision.wasm")
    assert status == 200
    assert headers["Content-Type"] == "application/wasm"


def test_a_missing_game_file_is_a_404_not_the_kiosk_shell(server: str) -> None:
    status, headers = _get(server + "/games/body-surfers/models/missing.task")
    assert status == 404
    assert headers["X-Frame-Options"] is None

    # The kiosk itself keeps its SPA fallback.
    status, _ = _get(server + "/doorpad")
    assert status == 200
