"""Entry point: `python -m door_voice`."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from door_voice.app import main_async


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main_async())


if __name__ == "__main__":
    main()
