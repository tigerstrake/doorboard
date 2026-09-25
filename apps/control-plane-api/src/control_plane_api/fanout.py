"""Post-commit fan-out off the request thread: notifications and Telegram media.

`/ingest` used to deliver owner notifications (ntfy, 5 s timeout) and Telegram
media (pull a clip from door-api, ≤30 s; upload it to N chats, ≤30 s each)
inside the request, and — every route being `async def` — on the event loop.
One 40 MB video message froze the whole service for up to a minute: door-sync's
next batch stalled and backed off, `/health` stopped answering, the ring photo
waited.

Now ingest commits each event, publishes to MQTT (paho's `publish` is a queue
hand-off, not I/O), and enqueues delivery here. A single daemon thread drains
the queue with its own DB sessions; failures are logged and never reach the
request. The queue is bounded: if delivery falls further behind than
`max_queue` events the newest job is dropped and counted rather than letting
memory grow while door-sync keeps retrying.

Not started == synchronous. A worker whose `start()` was never called runs each
job inline in `submit`, which is what unit tests and the token CLI want.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from collections.abc import Callable
from datetime import datetime

from doorboard_contracts import DoorboardEvent
from sqlalchemy.orm import Session, sessionmaker

from control_plane_api.db import session_scope
from control_plane_api.metrics import FANOUT_DROPPED, metrics
from control_plane_api.notify import NotifyEngine
from control_plane_api.telegram import RingPhotoDelivery, VideoMessageDelivery

logger = logging.getLogger("control_plane_api.fanout")

DEFAULT_MAX_QUEUE = 256


class FanoutWorker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        notify_engine: NotifyEngine,
        video_delivery: VideoMessageDelivery | None = None,
        ring_photo_delivery: RingPhotoDelivery | None = None,
        max_queue: int = DEFAULT_MAX_QUEUE,
    ) -> None:
        self._session_factory = session_factory
        self._notify_engine = notify_engine
        self._video_delivery = video_delivery
        self._ring_photo_delivery = ring_photo_delivery
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue(maxsize=max_queue)
        self._thread: threading.Thread | None = None

    @property
    def notify_engine(self) -> NotifyEngine:
        return self._notify_engine

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="fanout", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        if self._thread is None:
            return
        # A full queue means the thread is stuck in a job; the daemon flag lets the
        # process exit regardless.
        with contextlib.suppress(queue.Full):
            self._queue.put(None, timeout=timeout_s)
        self._thread.join(timeout=timeout_s)
        self._thread = None

    # -- submission ---------------------------------------------------------

    def submit(self, job: Callable[[], None]) -> bool:
        """Queue `job` for the worker; run it inline when no worker is running.

        Returns False (and counts the drop) when the bounded queue is full.
        """
        if self._thread is None:
            self._safe_run(job)
            return True
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            metrics.inc(FANOUT_DROPPED)
            logger.warning("fanout_queue_full_job_dropped", extra={"pending": self.pending})
            return False
        return True

    def enqueue_event(self, event: DoorboardEvent, *, now: datetime) -> bool:
        def job() -> None:
            self.deliver(event, now=now)

        return self.submit(job)

    # -- delivery -----------------------------------------------------------

    def deliver(self, event: DoorboardEvent, *, now: datetime) -> None:
        """Every post-commit delivery for one stored event, each in its own session.

        Each step has its own try/except: a Telegram failure must not cost the ntfy
        ping, and the ring photo must not cost the video delivery (ADR-0022).
        """
        try:
            with session_scope(self._session_factory) as notify_session:
                self._notify_engine.on_event(notify_session, event, now=now)
        except Exception:
            logger.warning("notify_fanout_failed", exc_info=True)
        if self._video_delivery is not None:
            try:
                with session_scope(self._session_factory) as delivery_session:
                    self._video_delivery.on_event(delivery_session, event, now=now)
            except Exception:
                logger.warning("video_delivery_fanout_failed", exc_info=True)
        if self._ring_photo_delivery is not None:
            try:
                with session_scope(self._session_factory) as photo_session:
                    self._ring_photo_delivery.on_event(photo_session, event, now=now)
            except Exception:
                logger.warning("ring_photo_delivery_fanout_failed", exc_info=True)

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            self._safe_run(job)

    @staticmethod
    def _safe_run(job: Callable[[], None]) -> None:
        try:
            job()
        except Exception:
            logger.warning("fanout_job_failed", exc_info=True)
