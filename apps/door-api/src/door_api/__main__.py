"""CLI entry point for the local door-api service."""

from __future__ import annotations

import argparse
import logging.config
import os

import uvicorn
from doorboard_observability.logging_json import json_logging_config


def _bind() -> tuple[str, int]:
    raw = os.environ.get("DOOR_API_BIND", "127.0.0.1:8080")
    host, separator, port = raw.rpartition(":")
    if not separator or not host:
        raise ValueError("DOOR_API_BIND must be host:port")
    return host, int(port)


def _configure_logging() -> None:
    logging.config.dictConfig(json_logging_config("door-api"))


def main() -> None:
    _configure_logging()
    default_host, default_port = _bind()
    parser = argparse.ArgumentParser(description="door-api service")
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=default_port)
    args = parser.parse_args()
    uvicorn.run(
        "door_api.app:app",
        host=args.host,
        port=args.port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
