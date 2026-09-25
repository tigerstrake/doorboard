"""A daemon thread that runs one callable on a fixed interval until stopped.

Used for the work that must never happen inside a request: prefetching the
.ics calendar feeds and pruning notification state. Deliberately tiny — no
scheduler library, no asyncio — because the callers are synchronous code
(httpx, SQLAlchemy) and the routes run on Starlette's threadpool anyway.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger("control_plane_api.background")


class PeriodicThread:
    def __init__(
        self,
        name: str,
        *,
        interval_s: float,
        fn: Callable[[], object],
        run_immediately: bool = True,
    ) -> None:
        self._name = name
        self._interval_s = interval_s
        self._fn = fn
        self._run_immediately = run_immediately
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None

    def _run(self) -> None:
        if self._run_immediately:
            self._tick()
        while not self._stop.wait(self._interval_s):
            self._tick()

    def _tick(self) -> None:
        try:
            self._fn()
        except Exception:
            # One bad tick must not end the loop: the next interval retries.
            logger.warning("background_tick_failed", extra={"job": self._name}, exc_info=True)
