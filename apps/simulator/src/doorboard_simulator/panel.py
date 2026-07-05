from __future__ import annotations

import asyncio
import html
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from doorboard_simulator.scenarios import available_scenarios, result_to_json, run_scenario_name


def _page(body: str) -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Doorboard Simulator</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; max-width: 56rem; }}
    form {{ display: inline-block; margin: 0 .5rem .75rem 0; }}
    button {{ padding: .55rem .8rem; }}
    pre {{ background: #111; color: #eee; padding: 1rem; overflow: auto; }}
  </style>
</head>
<body>
  <h1>Doorboard Simulator</h1>
  {body}
</body>
</html>
""".encode()


def serve_panel(*, host: str, port: int, artifact_root: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            buttons = "\n".join(
                f'<form method="post" action="/run"><input type="hidden" name="scenario" '
                f'value="{html.escape(name)}"><button>{html.escape(name)}</button></form>'
                for name in available_scenarios()
            )
            self._send(
                _page(f"<p>Manual triggers for local hardware-free development.</p>{buttons}")
            )

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/run":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            length = int(self.headers.get("content-length", "0"))
            form = parse_qs(self.rfile.read(length).decode())
            scenario = form.get("scenario", ["basic-bell"])[0]
            result = asyncio.run(run_scenario_name(scenario, artifact_root=artifact_root))
            body = (
                '<p><a href="/">Back</a></p>'
                f"<h2>{html.escape(scenario)}</h2>"
                f"<pre>{html.escape(result_to_json(result))}</pre>"
            )
            self._send(_page(body))

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send(self, body: bytes) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"doorboard simulator panel listening on http://{host}:{port}")
    server.serve_forever()
