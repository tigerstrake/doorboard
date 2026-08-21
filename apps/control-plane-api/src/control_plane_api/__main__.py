"""CLI entry point for control-plane-api.

Usage:
    uv run python -m control_plane_api
    uv run python -m control_plane_api --host 0.0.0.0 --port 8090
"""

from __future__ import annotations

import argparse
import logging.config

import uvicorn
from doorboard_observability.logging_json import json_logging_config

from control_plane_api.settings import settings


def _configure_logging() -> None:
    logging.config.dictConfig(json_logging_config("control-plane-api"))


def main() -> None:
    _configure_logging()

    parser = argparse.ArgumentParser(description="control-plane-api service")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    cfg = settings()
    host = args.host or cfg.host
    port = args.port or cfg.port

    uvicorn.run("control_plane_api.app:app", host=host, port=port, log_config=None)


if __name__ == "__main__":
    main()
