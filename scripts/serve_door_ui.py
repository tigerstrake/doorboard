#!/usr/bin/env python3
"""Serve the built door UI with SPA fallback on loopback."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


class SpaHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if urlsplit(self.path).path == "/healthz":
            body = b"ok\n"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        requested = Path(self.translate_path(urlsplit(self.path).path))
        if not requested.exists() and "text/html" in self.headers.get("Accept", "text/html"):
            self.path = "/index.html"
        super().do_GET()

    def end_headers(self) -> None:
        if urlsplit(self.path).path in ("/", "/index.html"):
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
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
