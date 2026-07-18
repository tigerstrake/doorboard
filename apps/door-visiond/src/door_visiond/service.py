"""VisiondService — wires the pipeline, enrollment, cache, and privacy mode.

This is the single object the FastAPI app talks to.  It never sits in the door
button path and never waits on the NUC.  Recognition is personalization only,
never authorization (ADR-0005 §3).
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from doorboard_contracts.events import DoorboardEvent
from doorboard_esp32_link import Esp32Transport, wire_message_from_event

from door_visiond._uuid7 import uuid7
from door_visiond.clock import Clock, SystemClock
from door_visiond.compat import CompatResult, check_compatibility
from door_visiond.consent import load_consent_statement
from door_visiond.embedder import Embedder, HailoEmbedder, MockEmbedder
from door_visiond.embedding import Embedding
from door_visiond.enrollment import EnrollmentStore, ProfileSpec
from door_visiond.events import (
    EventEmitter,
    make_door_profile_clear,
    make_door_profile_update,
    make_pipeline_status,
    make_privacy_mode_changed,
)
from door_visiond.identity_cache import CurrentVisitor, IdentityCache
from door_visiond.logging_setup import get_logger
from door_visiond.matcher import Matcher
from door_visiond.pipeline import (
    DisabledBackend,
    HardwareBackend,
    PipelineCore,
    ScriptedBackend,
    VisionBackend,
    default_mock_script,
)
from door_visiond.privacy_store import PrivacyStore
from door_visiond.purge_outbox import PurgeOutbox
from door_visiond.settings import Settings
from door_visiond.storage_security import is_luks_backed

logger = get_logger("door_visiond.service")

_HARDWARE_MODES = frozenset({"single-camera", "dual-camera", "hardware"})


# ---------------------------------------------------------------------------
# Errors (mapped to HTTP status codes by the app)
# ---------------------------------------------------------------------------


class EnrollError(Exception):
    """Base class for enrollment failures."""


class PrivacyModeActiveError(EnrollError):
    """Enrollment refused because privacy mode is active (409)."""


class EnrollmentLockedError(EnrollError):
    """Encrypted enrollment storage is unavailable (503)."""


class StaleConsentError(EnrollError):
    def __init__(self, current_version: str) -> None:
        self.current_version = current_version
        super().__init__(f"stale consent version; current is {current_version!r}")


class QualityTooLowError(EnrollError):
    def __init__(self, qualities: list[float]) -> None:
        self.qualities = qualities
        super().__init__("all captured faces are below the enrollment quality threshold")


@dataclass(frozen=True)
class EnrollResult:
    person_id: str
    embeddings_created: int
    quality: list[float]


class VisiondService:
    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        embedder: Embedder | None = None,
        backend: VisionBackend | None = None,
        emitter: EventEmitter | None = None,
        esp32_transport: Esp32Transport | None = None,
    ) -> None:
        self._settings = settings
        self._clock: Clock = clock or SystemClock()
        self._emitter = emitter or EventEmitter(settings.door_id)
        self._esp32_transport = esp32_transport
        self._esp32_seq = 0
        self._esp32_profile_updates_acked = 0
        self._esp32_profile_clears_acked = 0
        self._esp32_profile_send_failures = 0
        self._esp32_profile_last_error: str | None = None
        self._esp32_tasks: set[asyncio.Task[None]] = set()

        self._enrollment_locked = settings.require_encrypted_enrollment and not is_luks_backed(
            settings.enrollment_root
        )
        # Never fall through to the unencrypted parent filesystem while the
        # dedicated volume is locked. The in-memory store keeps read paths
        # operational but cannot retain biometric data.
        self._store = EnrollmentStore(
            ":memory:" if self._enrollment_locked else settings.enrollment_db_path
        )
        self._privacy_store = PrivacyStore(settings.privacy_state_path)
        self._purge_outbox = PurgeOutbox(settings.purge_outbox_path)
        self._purge_task: asyncio.Task[None] | None = None
        self._purges_delivered = 0
        self._purges_failed = 0
        self._privacy_state_degraded = False
        self._pipeline_errors = 0
        self._pipeline_consecutive_errors = 0
        self._runtime_degraded_detail: str | None = None

        # Startup compatibility check → effective mode.
        self._compat: CompatResult = check_compatibility(
            mode=settings.vision_mode,
            expected_runtime=settings.hailo_runtime_version,
            expected_model_id=settings.model_id,
            expected_dim=settings.model_dim,
        )
        if settings.vision_mode in _HARDWARE_MODES and not self._compat.ok:
            self._effective_mode = "disabled"
            logger.warning("hailo_incompatible_degraded", extra={"detail": self._compat.detail})
        else:
            self._effective_mode = settings.vision_mode
        if self._enrollment_locked:
            self._effective_mode = "disabled"
            logger.warning("encrypted_enrollment_storage_locked")

        self._embedder: Embedder = embedder or self._build_embedder()

        self._matcher = Matcher(settings.match_threshold)
        self._cache = IdentityCache()
        self._core = PipelineCore(
            matcher=self._matcher,
            cache=self._cache,
            sink=self._emitter.emit,
            clock=self._clock,
            door_id=settings.door_id,
            min_face_px=settings.min_face_px,
            ttl_ms=settings.identity_cache_ttl_ms,
            cooldown_ms=settings.greeting_cooldown_ms,
            stability_window=settings.stability_window,
            stability_required=settings.stability_required,
            cache_update_sink=self._on_cache_refresh,
            cache_clear_sink=self._on_cache_clear,
        )

        self._backend: VisionBackend = backend or self._build_backend()
        self._privacy_enabled = False
        self._run_task: asyncio.Task[None] | None = None
        self._running = False

        # cache hit-rate bookkeeping
        self._cache_lookups = 0
        self._cache_hits = 0

    # -- construction helpers ----------------------------------------------

    def _build_embedder(self) -> Embedder:
        if self._effective_mode in _HARDWARE_MODES:
            return HailoEmbedder(dim=self._settings.model_dim, model_id=self._settings.model_id)
        return MockEmbedder(dim=self._settings.model_dim)

    def _build_backend(self) -> VisionBackend:
        if self._effective_mode == "disabled":
            return DisabledBackend(interval_ms=self._settings.frame_interval_ms)
        if self._effective_mode in _HARDWARE_MODES:
            return HardwareBackend(mode=self._effective_mode, embedder=self._embedder)
        # mock
        return ScriptedBackend(
            default_mock_script(self._settings.model_dim),
            mode="mock",
            interval_ms=self._settings.frame_interval_ms,
        )

    # -- lifecycle ---------------------------------------------------------

    def startup(self) -> None:
        """Prepare storage and restore privacy state BEFORE any frame is captured."""
        self._settings.visiond_root.mkdir(parents=True, exist_ok=True)
        if not self._enrollment_locked:
            self._wipe_enroll_tmp()

        # Restore persisted privacy flag first (P-8): the backend must not
        # capture until this is applied.
        state = self._privacy_store.load()
        self._privacy_enabled = state.enabled
        self._privacy_state_degraded = state.changed_by == "fail_closed"
        self._backend.set_capturing(not self._privacy_enabled)

        self._reload_matcher()
        self._emit_pipeline_status()
        logger.info(
            "visiond_startup",
            extra={
                "mode": self._effective_mode,
                "privacy_enabled": self._privacy_enabled,
                "enrolled": self._matcher.enrolled_count,
                "compat": self._compat.detail,
            },
        )

    async def start(self) -> None:
        self.startup()
        self._running = True
        self._run_task = asyncio.create_task(self._run_loop(), name="visiond-run-loop")
        self._purge_task = asyncio.create_task(self._purge_loop(), name="visiond-purge-outbox")

    async def stop(self) -> None:
        self._running = False
        if self._run_task is not None:
            self._run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._run_task
        if self._purge_task is not None:
            self._purge_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._purge_task
        if self._esp32_tasks:
            await asyncio.gather(*self._esp32_tasks, return_exceptions=True)
        await self._backend.close()
        self._purge_outbox.close()
        self._store.close()

    async def _purge_loop(self) -> None:
        while self._running:
            for item in self._purge_outbox.pending():
                try:
                    await asyncio.to_thread(self._deliver_purge, item.person_id)
                except Exception as exc:
                    attempts = item.attempts + 1
                    delay = min(2 ** min(attempts, 8), self._settings.purge_retry_max_s)
                    self._purge_outbox.mark_failed(
                        item.person_id,
                        attempts=attempts,
                        delay_s=delay,
                        error=str(exc),
                    )
                    self._purges_failed += 1
                    logger.warning(
                        "archive_purge_delivery_failed",
                        extra={"person_id": item.person_id, "attempts": attempts},
                    )
                else:
                    self._purge_outbox.mark_delivered(item.person_id)
                    self._purges_delivered += 1
            await asyncio.sleep(self._settings.purge_worker_interval_s)

    def _deliver_purge(self, person_id: str) -> None:
        from urllib.parse import quote

        url = (
            f"{self._settings.sync_base_url.rstrip('/')}/internal/purge/{quote(person_id, safe='')}"
        )
        headers = (
            {"Authorization": f"Bearer {self._settings.sync_admin_token}"}
            if self._settings.sync_admin_token
            else {}
        )
        request = urllib.request.Request(  # noqa: S310
            url,
            data=b"",
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._settings.sync_timeout_s) as response:  # noqa: S310
            if not 200 <= response.status < 300:
                raise RuntimeError(f"door-sync purge returned HTTP {response.status}")

    async def _run_loop(self) -> None:
        logger.info("visiond_run_loop_started")
        while self._running:
            try:
                capture = await self._backend.next_capture()
                self._core.tick()
                if capture is not None:
                    self._core.process_capture(capture)
                self._pipeline_consecutive_errors = 0
            except asyncio.CancelledError:
                break
            except Exception as exc:  # a bad frame must never kill the loop
                self._pipeline_errors += 1
                self._pipeline_consecutive_errors += 1
                if self._pipeline_consecutive_errors >= 3:
                    await self._degrade_failed_backend(exc)
                    continue
                logger.warning(
                    "vision_backend_frame_failed",
                    extra={
                        "error_class": type(exc).__name__,
                        "consecutive": self._pipeline_consecutive_errors,
                    },
                )
                await asyncio.sleep(0.05)

    async def _degrade_failed_backend(self, exc: Exception) -> None:
        failed_backend = self._backend
        self._backend = DisabledBackend(interval_ms=self._settings.frame_interval_ms)
        self._effective_mode = "disabled"
        self._runtime_degraded_detail = f"vision backend failed: {type(exc).__name__}"
        self._pipeline_consecutive_errors = 0
        with contextlib.suppress(Exception):
            await failed_backend.close()
        self._emit_pipeline_status()
        logger.error(
            "vision_backend_degraded_to_disabled",
            extra={"error_class": type(exc).__name__},
        )

    def _wipe_enroll_tmp(self) -> None:
        root = self._settings.enroll_tmp_root
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)

    def _reload_matcher(self) -> None:
        enrolled = self._store.load_enrolled()
        self._matcher.refresh(enrolled)
        self._core.on_matcher_refreshed({p.person_id for p in enrolled})

    def _emit_pipeline_status(self) -> None:
        status = self._backend.status()
        # Effective mode wins over the raw backend mode after degradation.
        self._emitter.emit(
            make_pipeline_status(
                clock=self._clock,
                door_id=self._settings.door_id,
                trace_id=uuid7(),
                mode="disabled" if self._privacy_enabled else self._effective_mode,
                hailo_ok=status.hailo_ok and not self._privacy_enabled,
                fps=0.0 if self._privacy_enabled else status.fps,
                inference_ms_p50=self._core.inference_ms_p50(),
            )
        )

    # -- enrollment --------------------------------------------------------

    def enroll(
        self,
        *,
        display_name: str,
        consent_version: str,
        consent_confirmed: bool,
        images: list[bytes],
        profile: ProfileSpec,
    ) -> EnrollResult:
        if self._enrollment_locked:
            raise EnrollmentLockedError
        if self._privacy_enabled:
            raise PrivacyModeActiveError

        expected = load_consent_statement(self._settings.consent_statement_path).version
        if not consent_confirmed or consent_version != expected:
            raise StaleConsentError(expected)
        if not images:
            raise QualityTooLowError([])

        req_id = uuid7().hex
        tmp_dir = self._settings.enroll_tmp_root / f"enroll-{req_id}"
        try:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            embeddings: list[tuple[Embedding, str, float]] = []
            qualities: list[float] = []
            for i, image in enumerate(images):
                # Raw image is transient: written to tmp, embedded, then wiped.
                img_path = tmp_dir / f"img-{i}.bin"
                img_path.write_bytes(image)
                emb, quality = self._embedder.embed(img_path.read_bytes())
                qualities.append(quality)
                if quality >= self._settings.min_enroll_quality:
                    embeddings.append((emb, self._embedder.model_id, quality))

            if not embeddings:
                raise QualityTooLowError(qualities)

            person_id = self._store.enroll(
                display_name=display_name,
                consent_version=consent_version,
                consent_at=self._clock.utc_now(),
                embeddings=embeddings,
                profile=profile,
            )
        finally:
            # E-1/§1: raw enrollment images never survive the request.
            shutil.rmtree(tmp_dir, ignore_errors=True)

        self._reload_matcher()
        return EnrollResult(
            person_id=person_id,
            embeddings_created=len(embeddings),
            quality=qualities,
        )

    def unenroll(self, person_id: str) -> dict[str, object]:
        if self._enrollment_locked:
            raise EnrollmentLockedError
        newly_queued = self._purge_outbox.enqueue(person_id)
        existed = self._store.unenroll(person_id)
        self._reload_matcher()
        # Flush the cache if the unenrolled person is the current visitor (E-5 →
        # T-303 propagates the ESP32 profile_clear + NUC archive purge).
        current = self._cache.peek()
        if current is not None and current.person_id == person_id:
            self._core.clear_cache_and_notify(reason="admin")
        logger.info(
            "unenroll_archive_purge_queued",
            extra={"person_id": person_id, "newly_queued": newly_queued},
        )
        return {"deleted": existed, "archive_purge": "queued"}

    # -- privacy mode ------------------------------------------------------

    def set_privacy_mode(self, *, enabled: bool, changed_by: str) -> None:
        self._privacy_store.save(enabled=enabled, changed_by=changed_by)
        self._privacy_state_degraded = False
        self._privacy_enabled = enabled
        # E-6: kill/enable capture at the frame source.
        self._backend.set_capturing(not enabled)
        if enabled:
            self._core.clear_cache_and_notify(reason="privacy_mode")
        self._emitter.emit(
            make_privacy_mode_changed(
                clock=self._clock,
                door_id=self._settings.door_id,
                trace_id=uuid7(),
                enabled=enabled,
                changed_by=changed_by,
            )
        )
        self._emit_pipeline_status()

    # -- reads -------------------------------------------------------------

    def current_visitor(self) -> dict[str, object] | None:
        self._cache_lookups += 1
        visitor = self._cache.current(self._clock.monotonic_ms())
        if visitor is None:
            return None
        self._cache_hits += 1
        return {
            "person_id": visitor.person_id,
            "display_name": visitor.display_name,
            "expires_at_monotonic_ms": visitor.expires_at_monotonic_ms,
        }

    def cache_hit_rate(self) -> float:
        if self._cache_lookups == 0:
            return 0.0
        return self._cache_hits / self._cache_lookups

    # -- ESP32 profile mirroring ------------------------------------------

    def _on_cache_refresh(self, visitor: CurrentVisitor, priority: str, trace_id) -> None:
        event = make_door_profile_update(
            clock=self._clock,
            door_id=self._settings.door_id,
            trace_id=trace_id,
            profile_id=visitor.profile_id,
            expires_at_monotonic_ms=visitor.expires_at_monotonic_ms,
            priority=priority,
        )
        self._submit_esp32_profile_event(event)

    def _on_cache_clear(self, _visitor: CurrentVisitor, reason: str, trace_id) -> None:
        event = make_door_profile_clear(
            clock=self._clock,
            door_id=self._settings.door_id,
            trace_id=trace_id,
            reason=reason,
        )
        self._submit_esp32_profile_event(event)

    def _submit_esp32_profile_event(self, event: DoorboardEvent) -> None:
        if self._esp32_transport is None:
            return

        async def _send() -> None:
            await self._send_esp32_profile_event(event)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_send())
            return

        task = loop.create_task(_send(), name=f"visiond-esp32-{event.type}")
        self._esp32_tasks.add(task)
        task.add_done_callback(self._esp32_tasks.discard)

    async def _send_esp32_profile_event(self, event: DoorboardEvent) -> None:
        assert self._esp32_transport is not None
        msg = wire_message_from_event(
            event,
            seq=self._next_esp32_seq(),
            now_mono_ms=self._clock.monotonic_ms(),
        )
        try:
            await self._esp32_transport.send(msg)
        except Exception as exc:
            self._esp32_profile_send_failures += 1
            self._esp32_profile_last_error = exc.__class__.__name__
            logger.warning(
                "esp32_profile_send_failed",
                extra={"event_type": event.type, "error_class": exc.__class__.__name__},
            )
            return
        self._esp32_profile_last_error = None
        if msg.message_type == "profile_update":
            self._esp32_profile_updates_acked += 1
        elif msg.message_type == "profile_clear":
            self._esp32_profile_clears_acked += 1

    def _next_esp32_seq(self) -> int:
        self._esp32_seq += 1
        return self._esp32_seq

    @property
    def effective_mode(self) -> str:
        return "disabled" if self._privacy_enabled else self._effective_mode

    @property
    def privacy_enabled(self) -> bool:
        return self._privacy_enabled

    @property
    def core(self) -> PipelineCore:
        return self._core

    @property
    def compat(self) -> CompatResult:
        return self._compat

    def health(self) -> dict[str, object]:
        status = self._backend.status()
        hailo_ok = status.hailo_ok and not self._privacy_enabled
        enrollment_locked = self._enrollment_locked
        esp32_status = self._esp32_transport.status() if self._esp32_transport is not None else None
        esp32_profile_warning = (
            f"profile push failed: {self._esp32_profile_last_error}"
            if self._esp32_profile_last_error is not None
            else None
        )
        healthy = (
            esp32_profile_warning is None
            and not self._privacy_state_degraded
            and self._runtime_degraded_detail is None
            and not self._enrollment_locked
        )
        return {
            "service": "door-visiond",
            "status": "ok" if healthy else "degraded",
            "mode": self.effective_mode,
            "configured_mode": self._settings.vision_mode,
            "hailo_ok": hailo_ok,
            "privacy_enabled": self._privacy_enabled,
            "enrolled": self._matcher.enrolled_count,
            "enrollment_locked": enrollment_locked,
            "compat": self._compat.detail,
            "runtime_warning": self._runtime_degraded_detail,
            "door_id": self._settings.door_id,
            "esp32_connected": esp32_status.connected if esp32_status is not None else None,
            "esp32_profile_push_status": "degraded" if esp32_profile_warning else "ok",
            "esp32_profile_warning": esp32_profile_warning,
            "privacy_state_status": "invalid_fail_closed" if self._privacy_state_degraded else "ok",
            "archive_purge_queue_depth": self._purge_outbox.depth(),
        }

    def metrics_snapshot(self) -> dict[str, float]:
        snap = self._core.metrics_snapshot()
        snap["cache_hit_rate"] = self.cache_hit_rate()
        snap["enrolled"] = float(self._matcher.enrolled_count)
        snap["esp32_profile_updates_acked"] = float(self._esp32_profile_updates_acked)
        snap["esp32_profile_clears_acked"] = float(self._esp32_profile_clears_acked)
        snap["esp32_profile_send_failures"] = float(self._esp32_profile_send_failures)
        snap["archive_purge_queue_depth"] = float(self._purge_outbox.depth())
        snap["archive_purges_delivered"] = float(self._purges_delivered)
        snap["archive_purges_failed"] = float(self._purges_failed)
        snap["pipeline_errors"] = float(self._pipeline_errors)
        return snap

    def now_utc(self) -> datetime:
        return self._clock.utc_now()
