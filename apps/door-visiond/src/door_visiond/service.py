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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from doorboard_contracts.enrollment_relay import (
    DoorKeyPublication,
    InviteRegistration,
    PickupAck,
    SealedBundle,
)
from doorboard_contracts.events import DoorboardEvent
from doorboard_esp32_link import Esp32Transport, wire_message_from_event

from door_visiond._uuid7 import uuid7
from door_visiond.clock import Clock, SystemClock
from door_visiond.compat import CompatResult, check_compatibility
from door_visiond.consent import load_consent_statement
from door_visiond.embedder import Embedder, HailoEmbedder, MockEmbedder
from door_visiond.embedding import Embedding
from door_visiond.enrollment import (
    EnrollmentStore,
    InviteConsumption,
    InviteUnusableError,
    ProfileSpec,
    hash_invite_secret,
)
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
from door_visiond.relay_client import (
    HttpRelayTransport,
    RelayTransport,
    RelayWorker,
)
from door_visiond.relay_seal import RelayKeyring, SealError
from door_visiond.settings import Settings
from door_visiond.storage_security import is_luks_backed

if TYPE_CHECKING:
    from door_visiond.hailo_pipeline import HailoFacePipeline

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
        relay_transport: RelayTransport | None = None,
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

        # Remote enrollment (ADR-0016). All optional: with no relay configured
        # none of this starts and the at-door flow is untouched. The keyring is
        # opened lazily so a locked enrollment volume never causes a keypair to be
        # written to the unencrypted parent filesystem.
        self._relay_keyring: RelayKeyring | None = None
        self._relay_transport: RelayTransport | None = relay_transport
        self._relay_worker: RelayWorker | None = None
        self._relay_tasks: set[asyncio.Task[None]] = set()
        # Shared Hailo face pipeline (built once, lazily, for hardware modes so
        # the VDevice + models are reused by both the embedder and the backend).
        self._hailo_pipeline: HailoFacePipeline | None = None

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

    def _get_hailo_pipeline(self) -> HailoFacePipeline:
        """Build the shared face pipeline once (lazy import keeps CI safe)."""
        if self._hailo_pipeline is None:
            from door_visiond.hailo_pipeline import HailoFacePipeline

            self._hailo_pipeline = HailoFacePipeline(
                detector_hef_path=str(self._settings.detector_hef_path),
                recognizer_hef_path=str(self._settings.recognizer_hef_path),
                model_id=self._settings.model_id,
                dim=self._settings.model_dim,
            )
        return self._hailo_pipeline

    def _build_embedder(self) -> Embedder:
        if self._effective_mode in _HARDWARE_MODES:
            return HailoEmbedder(
                dim=self._settings.model_dim,
                model_id=self._settings.model_id,
                pipeline=self._get_hailo_pipeline(),
            )
        return MockEmbedder(dim=self._settings.model_dim)

    def _build_backend(self) -> VisionBackend:
        if self._effective_mode == "disabled":
            return DisabledBackend(interval_ms=self._settings.frame_interval_ms)
        if self._effective_mode in _HARDWARE_MODES:
            return HardwareBackend(
                mode=self._effective_mode,
                embedder=self._embedder,
                snapshot_url=self._settings.snapshot_url,
                snapshot_timeout_s=self._settings.snapshot_timeout_s,
                pipeline=self._get_hailo_pipeline(),
                interval_ms=self._settings.frame_interval_ms,
            )
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
        await self._start_relay_worker()

    async def _start_relay_worker(self) -> None:
        """Start the remote-enrollment poller, if a relay is configured.

        Nothing here is allowed to prevent the service from coming up: remote
        enrollment is a convenience, and the door path must not depend on it.
        """
        if self._relay_transport is None:
            if not self._settings.relay_enabled:
                return
            self._relay_transport = HttpRelayTransport(
                base_url=self._settings.relay_base_url,
                device_token=self._settings.relay_device_token,
                timeout_s=self._settings.relay_timeout_s,
            )
        if self._enrollment_locked:
            logger.warning("relay_worker_not_started_storage_locked")
            return
        try:
            # Prune sealing keys retired longer ago than the relay's own TTL (E-12).
            self._keyring().prune_retired(older_than_s=self._settings.relay_retired_key_ttl_s)
        except (SealError, EnrollmentLockedError) as exc:
            logger.error("relay_keyring_unavailable", extra={"error_class": type(exc).__name__})
            return
        self._relay_worker = RelayWorker(
            transport=self._relay_transport,
            handler=self,
            poll_interval_s=self._settings.relay_poll_interval_s,
            backoff_max_s=self._settings.relay_backoff_max_s,
        )
        await self._relay_worker.start()

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
        if self._relay_worker is not None:
            await self._relay_worker.stop()
        if self._relay_tasks:
            await asyncio.gather(*self._relay_tasks, return_exceptions=True)
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
        invite: InviteConsumption | None = None,
    ) -> EnrollResult:
        """Embed and store one person.

        ``invite`` is set only on the remote path (ADR-0016): it is validated and
        consumed in the same transaction as the insert, so this method is the one
        place both enrollment paths converge — and therefore the one place the
        transient-image guarantee (§1) has to hold.
        """
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
                invite=invite,
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
        # Write-ahead ordering (intentional — do NOT reorder): durably queue the
        # remote-archive purge BEFORE the local delete. The purge outbox is a
        # crash-safe, retrying queue (_purge_loop), so once enqueued the NUC
        # archive is guaranteed to be purged eventually. A crash or error between
        # the two leaves at worst a transient LOCAL copy, which self-heals when
        # the admin retries (enqueue is idempotent). The reverse order (delete
        # then enqueue) would risk a crash after the local delete but before the
        # intent is recorded -> the remote archive is never purged: a permanent
        # remote leak, the dangerous direction for a deletion invariant.
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

    # -- remote enrollment relay (ADR-0016) --------------------------------

    def _keyring(self) -> RelayKeyring:
        """Lazily open the door sealing keyring.

        Lazy because the key lives on the encrypted volume: constructing it during
        __init__ would generate a keypair on the unencrypted parent filesystem
        whenever the volume happens to be locked at boot.
        """
        if self._enrollment_locked:
            raise EnrollmentLockedError
        if self._relay_keyring is None:
            self._relay_keyring = RelayKeyring(self._settings.relay_key_path)
        return self._relay_keyring

    def create_invite(self, *, label: str | None = None) -> dict[str, object]:
        """Mint a single-use invite and return the URL a phone can open.

        The secret appears in the returned URL and is never stored or logged; the
        fragment carries the key fingerprint so the client can detect a relay that
        substituted its own key (E-10). Fragments are not sent to servers.
        """
        if self._privacy_enabled:
            raise PrivacyModeActiveError
        keyring = self._keyring()
        # Invite expiry is wall-clock UTC, not the injectable/monotonic clock: the
        # phone, the relay, and the Pi are three different hosts comparing the same
        # instant, so a monotonic value would be meaningless between them. Same
        # reasoning as door-api's visitor tokens.
        expires_at = datetime.now(UTC) + timedelta(seconds=self._settings.relay_invite_ttl_s)
        invite_id, secret = self._store.create_invite(
            expires_at=expires_at,
            label=label,
            max_images=self._settings.relay_max_images,
        )
        base = self._settings.relay_invite_base_url
        url = f"{base}/e/{invite_id}.{secret}#k={keyring.fingerprint}"
        registration = InviteRegistration(
            invite_id=invite_id,
            secret_sha256=hash_invite_secret(secret),
            expires_at=expires_at,
            max_images=self._settings.relay_max_images,
        )
        # Register outbound so the relay can reject junk before it reaches us. A
        # failure here is not fatal: the worker's resync re-registers open invites,
        # and the QR stays valid because the Pi's own table is authoritative.
        if self._relay_worker is not None:
            self._submit_relay_registration(registration)
        logger.info("relay_invite_minted", extra={"invite_id": invite_id})
        return {
            "invite_id": invite_id,
            "url": url,
            "expires_at": expires_at.isoformat(),
            "max_images": self._settings.relay_max_images,
            "relay_configured": self._settings.relay_enabled,
            "door_key_fingerprint": keyring.fingerprint,
        }

    def _submit_relay_registration(self, registration: InviteRegistration) -> None:
        async def _send() -> None:
            try:
                await asyncio.to_thread(self._relay_transport_register, registration)
            except Exception as exc:
                logger.warning(
                    "relay_invite_registration_failed",
                    extra={"invite_id": registration.invite_id, "error_class": type(exc).__name__},
                )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(_send(), name="visiond-relay-invite-register")
        self._relay_tasks.add(task)
        task.add_done_callback(self._relay_tasks.discard)

    def _relay_transport_register(self, registration: InviteRegistration) -> None:
        if self._relay_transport is not None:
            self._relay_transport.register_invite(registration)

    def revoke_invite(self, invite_id: str) -> dict[str, object]:
        revoked = self._store.revoke_invite(invite_id)
        if revoked and self._relay_transport is not None:
            # Best-effort: the Pi's table already refuses the invite, so a failed
            # relay call cannot authorize anything.
            with contextlib.suppress(Exception):
                self._relay_transport.revoke_invite(invite_id)
        return {"revoked": revoked}

    def list_invites(self, *, include_closed: bool = False) -> list[dict[str, object]]:
        return self._store.list_invites(include_closed=include_closed)

    def rotate_relay_key(self) -> dict[str, object]:
        keyring = self._keyring()
        key_id = keyring.rotate()
        if self._relay_worker is not None:
            self._relay_worker.request_resync()
        return {"door_key_id": key_id, "fingerprint": keyring.fingerprint}

    # -- RelayHandler protocol (called by RelayWorker) ---------------------

    def relay_collection_allowed(self) -> bool:
        return not self._privacy_enabled and not self._enrollment_locked

    def relay_door_key_publication(self) -> DoorKeyPublication:
        statement = load_consent_statement(self._settings.consent_statement_path)
        return self._keyring().publication(
            consent_version=statement.version,
            consent_text=statement.text,
        )

    def relay_invite_registrations(self) -> list[InviteRegistration]:
        return [
            InviteRegistration(
                invite_id=invite_id,
                secret_sha256=secret_sha256,
                expires_at=datetime.fromisoformat(expires_at),
                max_images=max_images,
            )
            for invite_id, secret_sha256, expires_at, max_images in (
                self._store.open_invite_registrations()
            )
        ]

    def relay_handle_bundle(self, bundle: SealedBundle) -> PickupAck:
        """Open a sealed bundle and enroll the person inside it.

        Never raises: every failure becomes an ack with a machine-readable reason,
        so one bad bundle cannot stall the queue or kill the worker. Reasons never
        contain user data (P-19).
        """
        try:
            opened = self._keyring().open_bundle(bundle)
        except SealError as exc:
            logger.warning(
                "relay_bundle_unsealable",
                extra={"bundle_id": bundle.bundle_id, "reason": exc.reason},
            )
            return PickupAck(bundle_id=bundle.bundle_id, outcome="rejected", reason=exc.reason)
        except EnrollmentLockedError:
            return PickupAck(
                bundle_id=bundle.bundle_id, outcome="failed", reason="enrollment_storage_locked"
            )

        manifest = opened.manifest
        # The relay's copy of invite_id is untrusted; the sealed manifest holds the
        # secret only a genuine invite holder could know (E-11).
        invite = InviteConsumption(
            invite_id=bundle.invite_id,
            secret_sha256=hash_invite_secret(manifest.invite_secret),
        )
        max_images = self._store.invite_max_images(bundle.invite_id)
        if max_images is None:
            return PickupAck(
                bundle_id=bundle.bundle_id, outcome="rejected", reason="unknown_invite"
            )
        if len(opened.images) > max_images:
            return PickupAck(
                bundle_id=bundle.bundle_id, outcome="rejected", reason="too_many_images"
            )

        try:
            result = self.enroll(
                display_name=manifest.display_name,
                consent_version=manifest.consent_version,
                consent_confirmed=manifest.consent_confirmed,
                images=list(opened.images),
                profile=ProfileSpec(
                    profile_id=manifest.profile.profile_id,
                    color=manifest.profile.color,
                    sound=manifest.profile.sound,
                ),
                invite=invite,
            )
        except InviteUnusableError as exc:
            return PickupAck(bundle_id=bundle.bundle_id, outcome="rejected", reason=exc.reason)
        except StaleConsentError:
            return PickupAck(bundle_id=bundle.bundle_id, outcome="rejected", reason="stale_consent")
        except QualityTooLowError:
            return PickupAck(bundle_id=bundle.bundle_id, outcome="failed", reason="quality_too_low")
        except PrivacyModeActiveError:
            return PickupAck(bundle_id=bundle.bundle_id, outcome="failed", reason="privacy_mode")
        except EnrollmentLockedError:
            return PickupAck(
                bundle_id=bundle.bundle_id, outcome="failed", reason="enrollment_storage_locked"
            )
        except Exception as exc:
            logger.error(
                "relay_enroll_failed",
                extra={"bundle_id": bundle.bundle_id, "error_class": type(exc).__name__},
            )
            return PickupAck(bundle_id=bundle.bundle_id, outcome="failed", reason="internal_error")

        logger.info(
            "relay_enrollment_completed",
            extra={
                "bundle_id": bundle.bundle_id,
                "person_id": result.person_id,
                "embeddings": result.embeddings_created,
            },
        )
        return PickupAck(bundle_id=bundle.bundle_id, outcome="enrolled")

    def relay_status(self) -> dict[str, object]:
        if not self._settings.relay_enabled:
            return {"configured": False, "status": "disabled"}
        stats = self._relay_worker.stats if self._relay_worker is not None else None
        if stats is None:
            return {"configured": True, "status": "stopped"}
        return {
            "configured": True,
            "status": "degraded" if stats.degraded else "ok",
            "polls_ok": stats.polls_ok,
            "polls_failed": stats.polls_failed,
            "bundles_enrolled": stats.bundles_enrolled,
            "bundles_rejected": stats.bundles_rejected,
            "consecutive_failures": stats.consecutive_failures,
            "last_error": stats.last_error,
            "last_success_at": stats.last_success_at,
        }

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
        relay = self.relay_status()
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
            # Deliberately NOT part of `healthy`: an unreachable relay must not
            # make the door service look broken (ADR-0016 §6).
            "relay_status": relay["status"],
            "relay_configured": relay["configured"],
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
        relay_stats = self._relay_worker.stats if self._relay_worker is not None else None
        snap["relay_enabled"] = 1.0 if self._settings.relay_enabled else 0.0
        snap["relay_polls_ok"] = float(relay_stats.polls_ok if relay_stats else 0)
        snap["relay_polls_failed"] = float(relay_stats.polls_failed if relay_stats else 0)
        snap["relay_bundles_enrolled"] = float(relay_stats.bundles_enrolled if relay_stats else 0)
        snap["relay_bundles_rejected"] = float(relay_stats.bundles_rejected if relay_stats else 0)
        snap["relay_consecutive_failures"] = float(
            relay_stats.consecutive_failures if relay_stats else 0
        )
        return snap

    def now_utc(self) -> datetime:
        return self._clock.utc_now()
