#!/usr/bin/env python3
"""Serve the built door UI with SPA fallback on loopback."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

# Static games embedded in the wallboard (ADR-0049). The wallboard frames them from the
# *other* loopback name (127.0.0.1 <-> localhost) so Chromium runs the game in its own
# renderer process, which means they are the only paths here allowed inside a frame.
GAMES_PREFIX = "/games/"


def _is_game_path(path: str) -> bool:
    return path.startswith(GAMES_PREFIX)


class SpaHandler(SimpleHTTPRequestHandler):
    # Python's mimetypes reads the host's /etc/mime.types, which does not list .wasm on
    # every distro; without application/wasm the browser refuses streaming compilation.
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".wasm": "application/wasm"}

    def do_GET(self) -> None:  # noqa: N802
        if urlsplit(self.path).path == "/healthz":
            body = b"ok\n"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        path = urlsplit(self.path).path
        requested = Path(self.translate_path(path))
        if not requested.exists():
            # A missing game asset is a 404, not the kiosk shell: index.html served as
            # a script or a .wasm only fails later and far less legibly.
            if _is_game_path(path):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if "text/html" in self.headers.get("Accept", "text/html"):
                self.path = "/index.html"
        super().do_GET()

    def end_headers(self) -> None:
        # The shell and the health probe must never be served from a cache: a stale
        # index.html pins the kiosk to old asset hashes after a deploy, and a cached
        # /healthz would tell doorboard-health-agent the server is up when it is not.
        path = urlsplit(self.path).path
        is_game = _is_game_path(path)
        # A game's own HTML shell (or its directory, which serves index.html) pins its
        # hashed assets the same way.
        game_shell = is_game and (path.endswith(".html") or path.endswith("/"))
        if path in ("/", "/index.html", "/healthz") or game_shell:
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        address = self.server.server_address
        if is_game and isinstance(address, tuple):
            # Framable only by this server's own two loopback origins (the wallboard);
            # every other path stays unframable.
            port = address[1]
            self.send_header(
                "Content-Security-Policy",
                f"frame-ancestors http://127.0.0.1:{port} http://localhost:{port}",
            )
        else:
            self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", default="apps/door-ui/dist")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5173)
    args = parser.parse_args()
    directory = Path(args.directory).resolve()
    if not (directory / "index.html").is_file():
        raise SystemExit(f"built door UI not found at {directory}")

    def handler(*handler_args: object, **kwargs: object) -> SpaHandler:
        return SpaHandler(*handler_args, directory=str(directory), **kwargs)

    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
