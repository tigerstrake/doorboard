"""ASGI application for door-api.

Exposes the WebSocket broadcast, health/metrics endpoints, and the DoorPad
visitor-flow HTTP surface used by the local kiosk UI.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
from doorboard_contracts.enrollment_relay import (
    VisitorActionOutcome,
    VisitorQueuedAction,
    VisitorSessionSnapshot,
)
from doorboard_contracts.events import (
    DoorboardEvent,
    HealthPayload,
    HealthStatus,
    SocialDeletionRequestedEvent,
    SocialDeletionRequestedPayload,
    consent_covers_extended_personalisation,
    parse_event,
)
from doorboard_esp32_link import Esp32Transport, WireMessage
from doorboard_esp32_link.esp32 import uuid7_now
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from door_api.ambient_cache import AmbientCache
from door_api.broadcast import DisplayBroadcast
from door_api.config import SessionConfig
from door_api.esp32_link import Esp32LinkSettings, Esp32LinkSupervisor
from door_api.mqtt_bridge import MqttBridge
from door_api.persistence import SessionStore
from door_api.recognised_identity import RecognisedIdentity
from door_api.service_proxy import (
    MEDIA_ROUTES,
    VISIOND_ROUTES,
    ProxyDenied,
    ProxyRoute,
    forward,
    resolve,
)
from door_api.session import SessionMachine
from door_api.social.config import SocialConfig
from door_api.social.routes import build_social_router
from door_api.social.service import SocialService
from door_api.social.store import SocialStore
from door_api.visitor_relay import (
    HttpVisitorRelayTransport,
    VisitorRelayTransport,
    VisitorRelayWorker,
)
from door_api.visitor_relay import build_snapshot as build_visitor_snapshot
from door_api.visitor_tokens import (
    VisitorTokenClaims,
    VisitorTokenError,
    decode_visitor_token,
    encode_visitor_token,
)

logger = logging.getLogger("door_api.app")


def _visitor_reject_reason(exc: Exception) -> str:
    """Map a social-service failure to a bounded, machine-readable reason.

    Deliberately does not include the exception text: these strings travel back
    through the relay to a public page, and an exception message can quote the
    offending input.
    """
    name = type(exc).__name__
    return {
        "RateLimitedError": "rate_limited",
        "ValidationError": "rejected_content",
        "SanitizationError": "rejected_content",
        "NotFoundError": "not_found",
        "PollClosedError": "poll_closed",
        "DuplicateVoteError": "already_voted",
        "UnsupportedDeletionTargetError": "not_deletable",
    }.get(name, "door_error")


class DoorApiState:
    """State container for the FastAPI app."""

    def __init__(self, *, visitor_relay_transport: VisitorRelayTransport | None = None) -> None:
        self.config = SessionConfig.from_env()
        # The last ambient event per type, replayed to each new /ws client. Without it a
        # wallboard reload loses the once-a-day dining recommendation for up to a day
        # (ADR-0027).
        self.ambient_cache = AmbientCache(max_age_s=self.config.ambient_cache_max_age_s)
        self.broadcast = DisplayBroadcast(replay_source=self.ambient_cache.replay)
        self.store = SessionStore(
            self.config.db_path,
            media_outbox_max_rows=self.config.media_outbox_max_rows,
            sync_outbox_max_rows=self.config.sync_outbox_max_rows,
        )
        # Who the door is talking to, for the length of this interaction (ADR-0020).
        # Separate from the session state machine on purpose: the approach timer is
        # about an empty doorway, this is about a person still tapping the screen.
        self.identity = RecognisedIdentity(
            idle_ttl_s=self.config.recognised_identity_idle_ttl_s,
            interaction_ttl_s=self.config.recognised_identity_interaction_ttl_s,
        )
        self.esp32_transport: Esp32Transport | None = None
        self.effect_requests = 0
        self.effect_unavailable = 0
        self.media_forward_errors = 0
        self.media_forward_successes = 0
        self.sync_forward_errors = 0
        self.sync_forward_successes = 0
        self._esp32_event_task: asyncio.Task[None] | None = None
        self._media_forward_task: asyncio.Task[None] | None = None
        self._sync_forward_task: asyncio.Task[None] | None = None
        self._identity_sweep_task: asyncio.Task[None] | None = None
        self.mqtt_bridge: MqttBridge | None = None
        self._mqtt_bridge_task: asyncio.Task[None] | None = None
        self.esp32_link: Esp32LinkSupervisor | None = None
        self._esp32_link_task: asyncio.Task[None] | None = None

        # Visitor relay (ADR-0017). All optional: with no relay configured the QR
        # behaves exactly as before and no worker or egress exists.
        self.visitor_relay_worker: VisitorRelayWorker | None = None
        self._visitor_relay_transport: VisitorRelayTransport | None = visitor_relay_transport
        self._visitor_relay_task: asyncio.Task[None] | None = None
        self._visitor_relay_token: str | None = None
        self._visitor_relay_applied: dict[str, VisitorActionOutcome] = {}

        def on_event(event: dict[str, Any]) -> None:
            self.broadcast.send_delta(event)
            if event["type"] in ("session.state_changed", "session.started", "session.ended"):
                self.broadcast.update_snapshot(self.session_snapshot_dict())
            # Republish the public snapshot so a phone sees ring status change.
            # A flag, never a network call: the transition path must not block.
            if self.visitor_relay_worker is not None:
                self.visitor_relay_worker.request_push()
            if event["type"] == "session.ended":
                # Stop advertising a finished session, and drop the applied-action
                # memo so the next visitor starts clean.
                self._visitor_relay_token = None
                self._visitor_relay_applied.clear()

        self.machine = SessionMachine(config=self.config, store=self.store, on_event=on_event)
        self.machine.set_identity_observer(
            lambda person_id, display_name, consent_version, profile_id, accent_color: (
                self.identity.remember(
                    person_id=person_id,
                    display_name=display_name,
                    consent_version=consent_version,
                    profile_id=profile_id,
                    accent_color=accent_color,
                )
            )
        )

        self.social_config = SocialConfig.from_env()
        self.social_store = SocialStore(self.social_config.db_path)

        def on_social_event(event: dict[str, Any]) -> None:
            dropped = self.store.enqueue_sync_event(event)
            if dropped:
                self.sync_forward_errors += dropped
            self.broadcast.send_delta(event)

        self.social_service = SocialService(
            config=self.social_config,
            store=self.social_store,
            on_event=on_social_event,
        )

    def session_snapshot_dict(self) -> dict[str, Any]:
        """Session snapshot for the kiosks, plus the disclosure name (E-23).

        ``attributed_to`` is derived here rather than in the UI so the consent gate
        lives in exactly one place; a surface that computed it itself could drift
        and start attributing silently.

        ``display_name``/``profile_id`` come from the interaction-scoped identity
        (ADR-0020) rather than the session machine, so a greeting and an accent colour
        survive the approach timer. The session's own copy stays authoritative for
        session semantics — ``had_cached_profile`` still records what the *press* knew.
        """
        snapshot = self.machine.snapshot().to_dict()
        held = self.identity.current()
        if held is not None:
            snapshot["display_name"] = held.display_name
            snapshot["profile_id"] = held.profile_id
            snapshot["consent_version"] = held.consent_version
            # The chosen colour (ADR-0021), so the kiosks accent by person rather than by
            # whichever LED effect they happened to be allocated.
            snapshot["accent_color"] = held.accent_color
        return {
            **snapshot,
            "attributed_to": self.attributed_display_name(),
            # The UI shows a countdown so "why did my name disappear" is answerable
            # from the screen rather than from the logs.
            "identity_expires_in_s": round(self.identity.seconds_remaining(), 1),
        }

    def startup(self) -> None:
        """Start the machine and populate the initial snapshot."""
        self.machine.restore_from_persistence()
        self.broadcast.update_snapshot(self.session_snapshot_dict())
        self.start_esp32_event_consumer()
        self.start_esp32_link()
        self.start_media_forwarder()
        self.start_sync_forwarder()
        self.start_identity_sweeper()
        self.start_mqtt_bridge()
        self.start_visitor_relay()

    def shutdown(self) -> None:
        """Close resources."""
        if self._esp32_event_task is not None:
            self._esp32_event_task.cancel()
        if self._esp32_link_task is not None:
            self._esp32_link_task.cancel()
        if self._media_forward_task is not None:
            self._media_forward_task.cancel()
        if self._sync_forward_task is not None:
            self._sync_forward_task.cancel()
        if self._identity_sweep_task is not None:
            self._identity_sweep_task.cancel()
            self._identity_sweep_task = None
        if self._mqtt_bridge_task is not None:
            self._mqtt_bridge_task.cancel()
        if self._visitor_relay_task is not None:
            self._visitor_relay_task.cancel()
        if self.visitor_relay_worker is not None:
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop().create_task(
                    self.visitor_relay_worker.stop(), name="door-api-visitor-relay-stop"
                )
        self.machine.close()
        self.social_store.close()

    def start_mqtt_bridge(self) -> None:
        """Spawn the optional NUC ambient/presence → /ws bridge.

        Inert unless DOOR_API_MQTT_URL is configured. The connection is NEVER
        awaited here: the loop is fire-and-forget so a broker outage can't delay
        startup or touch the door interaction path (see mqtt_bridge.py).
        """
        if not self.config.mqtt_url or self._mqtt_bridge_task is not None:
            return
        self.mqtt_bridge = MqttBridge(
            url=self.config.mqtt_url,
            broadcast=self.broadcast,
            remember=self.ambient_cache.remember,
            topics=self.config.mqtt_topics,
            username=self.config.mqtt_username,
            password=self.config.mqtt_password,
        )
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            self._mqtt_bridge_task = loop.create_task(
                self.mqtt_bridge.run(),
                name="door-api-mqtt-bridge",
            )

    def start_esp32_link(self) -> None:
        """Spawn the supervisor that owns the door controller's serial link.

        Inert unless ESP32_TRANSPORT names a real transport. Like the MQTT bridge,
        the connection is NEVER awaited here: an unplugged or unflashed controller
        must not delay startup or stand in front of the door interaction path.
        """
        settings = Esp32LinkSettings(
            transport=self.config.esp32_transport,
            uart_device=self.config.esp32_uart_device,
            uart_baud=self.config.esp32_uart_baud,
            udp_local_addr=self.config.esp32_udp_local_addr,
            udp_remote_addr=self.config.esp32_udp_remote_addr,
            reconnect_base_s=self.config.esp32_reconnect_base_s,
            reconnect_max_s=self.config.esp32_reconnect_max_s,
            door_id=self.config.door_id,
        )
        opener = settings.opener()
        if opener is None or self._esp32_link_task is not None:
            return
        # A transport injected by a test or the simulator wins: it is the whole
        # point of that injection, and opening a second link would fight it.
        if self.esp32_transport is not None:
            logger.info(
                "esp32_link_supervisor_skipped",
                extra={"reason": "transport_already_attached", "target": settings.describe()},
            )
            return

        self.esp32_link = Esp32LinkSupervisor(
            opener=opener,
            attach=self.attach_esp32_transport,
            detach=self.detach_esp32_transport,
            target=settings.describe(),
            reconnect_base_s=settings.reconnect_base_s,
            reconnect_max_s=settings.reconnect_max_s,
        )
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            self._esp32_link_task = loop.create_task(
                self.esp32_link.run(),
                name="door-api-esp32-link",
            )

    def attach_esp32_transport(self, transport: Esp32Transport) -> None:
        """Adopt a live controller link and start draining its events."""
        self.esp32_transport = transport
        self.start_esp32_event_consumer()

    def detach_esp32_transport(self) -> None:
        """Drop the current link so a replacement can be adopted cleanly.

        The event consumer has to be cancelled, not merely abandoned: `events()`
        is an endless queue read, so it would otherwise sit on a dead transport
        forever and block the next `start_esp32_event_consumer()` call.
        """
        if self._esp32_event_task is not None:
            self._esp32_event_task.cancel()
            self._esp32_event_task = None
        self.esp32_transport = None

    def start_esp32_event_consumer(self) -> None:
        if self.esp32_transport is None or self._esp32_event_task is not None:
            return
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            self._esp32_event_task = loop.create_task(
                self._consume_esp32_events(),
                name="door-api-esp32-events",
            )

    async def _consume_esp32_events(self) -> None:
        assert self.esp32_transport is not None
        async for event in self.esp32_transport.events():
            self.handle_contract_event(event)

    def handle_contract_event(self, event: DoorboardEvent) -> bool:
        # `event` is a discriminated union keyed on `type`; read `event.payload`
        # inside each branch so it narrows to the concrete payload type.
        changed = False
        if event.type == "door.button_pressed":
            payload = event.payload
            changed = self.machine.handle_button_pressed(
                trace_id=event.trace_id,
                had_cached_profile=payload.had_cached_profile,
                profile_id=payload.profile_id,
            )
        elif event.type == "vision.identity_stable":
            payload = event.payload
            # The holder is updated by the machine's identity observer, wired in
            # __init__ — every path that reports an identity feeds both.
            changed = self.machine.handle_identity_stable(
                person_id=payload.person_id,
                display_name=payload.display_name,
                profile_id=payload.profile_id,
                trace_id=event.trace_id,
                consent_version=payload.consent_version,
                accent_color=payload.accent_color,
            )
            self.broadcast.send_delta(event.model_dump(mode="json"))
        elif event.type == "vision.identity_expired":
            payload = event.payload
            # Whether to drop the held identity depends on *why* it expired (ADR-0029).
            #
            # "expired" (or an older producer sending no reason at all) means door-visiond's
            # 2.5 s cache lapsed — a face left the frame, which happens constantly while
            # someone stands at the doorpad looking down at it. Clearing on that would
            # reinstate the bug this holder exists to fix (ADR-0020).
            #
            # "admin" and "privacy_mode" are the opposite: the person was unenrolled or
            # recognition was switched off. Their face data is already gone, and leaving
            # their name on the screen until an unrelated timer lapses — up to 33 s idle, or
            # two minutes mid-interaction — contradicts the deletion promise this door makes
            # to visitors in as many words (ARCHITECTURE.md §9, ADR-0009).
            if payload.reason in ("admin", "privacy_mode"):
                self.identity.forget_person(payload.person_id)
            changed = self.machine.handle_identity_expired(person_id=payload.person_id)
            self.broadcast.send_delta(event.model_dump(mode="json"))
        elif event.type == "vision.privacy_mode_changed":
            payload = event.payload
            # Recognition being switched off has to take the remembered name with it
            # (ADR-0009 §4). Previously door-api held nothing across states so there
            # was nothing to flush; now there is, and a privacy flip that left a name
            # on screen would be a privacy defect rather than a cosmetic one.
            if payload.enabled:
                self.identity.forget()
            self.broadcast.send_delta(event.model_dump(mode="json"))
        elif event.type == "door.contact_changed":
            payload = event.payload
            changed = self.machine.handle_contact_changed(state=payload.state)
        elif event.type == "media.storage_status":
            # Pure pass-through, to two places. The session machine has no opinion about disk
            # space, but two surfaces are waiting on this and neither could receive it:
            #
            #  - door-ui subscribes over /ws, and the capacity card read "Waiting for a
            #    media.storage_status update" indefinitely.
            #  - Home Assistant's "Doorboard Sync Status" entity reads
            #    `doorboard/media/storage_status`, which control-plane-api only publishes for
            #    events it has actually ingested — so the entity sat at unknown.
            #
            # The sync outbox is the route off the door (door-sync -> control plane -> MQTT
            # fan-out), and it is durable and retrying, which suits telemetry that is dull but
            # should not silently stop. Safe to leave the door: free bytes, queue depth, oldest
            # unsynced age and a recording-allowed flag carry nothing personal, so none of
            # ARCHITECTURE.md §9's constraints apply.
            as_dict = event.model_dump(mode="json")
            self.broadcast.send_delta(as_dict)
            dropped = self.store.enqueue_sync_event(as_dict)
            if dropped:
                self.sync_forward_errors += dropped
        if changed or event.type.startswith("vision."):
            self.broadcast.update_snapshot(self.session_snapshot_dict())
        return changed

    def snapshot_response(self) -> dict[str, Any]:
        return {
            "session": self.session_snapshot_dict(),
            "config": {
                "max_recording_s": self.config.max_recording_s,
                "review_timeout_s": self.config.review_timeout_s,
                "inactivity_timeout_s": self.config.inactivity_timeout_s,
                "visitor_token_ttl_s": self.config.visitor_token_ttl_s,
                "feature_photobooth": self.config.feature_photobooth,
            },
        }

    def start_identity_sweeper(self) -> None:
        """Push one snapshot when a recognised identity lapses.

        Expiry is lazy — ``RecognisedIdentity.current()`` only notices on read — and
        nothing else emits an event when the window runs out. So the badge and the named
        check-in button stayed on screen until some unrelated event happened to trigger a
        broadcast, which on a quiet door can be minutes. The visitor sees a name that the
        server has already stopped honouring.

        Cheap by construction: it broadcasts on the *edge*, when a held identity becomes
        absent, never on a tick where nothing changed.
        """
        if self._identity_sweep_task is not None:
            return
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            self._identity_sweep_task = loop.create_task(
                self._identity_sweep_loop(),
                name="door-api-identity-sweep",
            )

    async def _identity_sweep_loop(self) -> None:
        held = self.identity.current() is not None
        while True:
            await asyncio.sleep(self.config.identity_sweep_interval_s)
            now_held = self.identity.current() is not None
            if held and not now_held:
                self.broadcast.update_snapshot(self.session_snapshot_dict())
                logger.info("recognised_identity_expired")
            held = now_held

    def start_media_forwarder(self) -> None:
        if self._media_forward_task is not None:
            return
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            self._media_forward_task = loop.create_task(
                self._media_forward_loop(),
                name="door-api-media-forward",
            )

    async def _media_forward_loop(self) -> None:
        while True:
            item = self.store.next_media_event(time.time())
            if item is None:
                await asyncio.sleep(self.config.media_forward_poll_s)
                continue

            try:
                async with httpx.AsyncClient(timeout=self.config.media_timeout_s) as client:
                    response = await client.post(
                        f"{self.config.media_base_url.rstrip('/')}/internal/session_event",
                        json={"event": item.event},
                        headers=(
                            {"Authorization": f"Bearer {self.config.media_admin_token}"}
                            if self.config.media_admin_token
                            else {}
                        ),
                    )
                    response.raise_for_status()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.media_forward_errors += 1
                attempts = item.attempts + 1
                delay_s = min(
                    self.config.media_retry_max_s,
                    self.config.media_retry_base_s * (2 ** min(attempts - 1, 16)),
                )
                self.store.retry_media_event(
                    item.event_id,
                    attempts=attempts,
                    next_attempt_epoch=time.time() + delay_s,
                    last_error=type(exc).__name__,
                )
                continue

            self.store.complete_media_event(item.event_id)
            self.media_forward_successes += 1

    def start_sync_forwarder(self) -> None:
        if self._sync_forward_task is not None:
            return
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            self._sync_forward_task = loop.create_task(
                self._sync_forward_loop(),
                name="door-api-sync-forward",
            )

    async def _sync_forward_loop(self) -> None:
        while True:
            item = self.store.next_sync_event(time.time())
            if item is None:
                await asyncio.sleep(self.config.sync_forward_poll_s)
                continue

            try:
                async with httpx.AsyncClient(timeout=self.config.sync_timeout_s) as client:
                    response = await client.post(
                        f"{self.config.sync_base_url.rstrip('/')}/internal/enqueue",
                        json={"event": item.event},
                        headers=(
                            {"Authorization": f"Bearer {self.config.sync_admin_token}"}
                            if self.config.sync_admin_token
                            else {}
                        ),
                    )
                    response.raise_for_status()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.sync_forward_errors += 1
                attempts = item.attempts + 1
                delay_s = min(
                    self.config.sync_retry_max_s,
                    self.config.sync_retry_base_s * (2 ** min(attempts - 1, 16)),
                )
                self.store.retry_sync_event(
                    item.event_id,
                    attempts=attempts,
                    next_attempt_epoch=time.time() + delay_s,
                    last_error=type(exc).__name__,
                )
                continue

            self.store.complete_sync_event(item.event_id)
            self.sync_forward_successes += 1

    async def play_doorpad_effect(self, trace_id: UUID | None = None) -> dict[str, str]:
        """Emit and, when configured, send DoorPad feedback to the ESP32."""
        self.effect_requests += 1
        event_trace = trace_id or uuid4()
        event = {
            "event_id": str(uuid7_now()),
            "type": "door.effect_play",
            "source": "door-api",
            "occurred_at": datetime.now(UTC).isoformat(),
            "monotonic_ms": int(time.monotonic() * 1000),
            "door_id": self.config.door_id,
            "trace_id": str(event_trace),
            "payload": {
                "effect_id": self.config.doorpad_effect_id,
                "duration_ms": self.config.doorpad_effect_duration_ms,
            },
        }
        self.broadcast.send_delta(event)

        if self.esp32_transport is None:
            self.effect_unavailable += 1
            return {"status": "unavailable"}

        try:
            await self.esp32_transport.send(
                WireMessage(
                    v=1,
                    seq=0,
                    message_type="effect_play",
                    ack=None,
                    payload=event["payload"],
                )
            )
        except Exception:
            self.effect_unavailable += 1
            return {"status": "failed"}
        return {"status": "sent"}

    def visitor_token(self) -> dict[str, str | int]:
        snapshot = self.machine.snapshot()
        # An active session is the usual key. Failing that, a recognised person who is
        # still mid-interaction gets one keyed on their interaction id (ADR-0020): the
        # identity deliberately outlives the approach session, so "the door knows who you
        # are" and "the door will let you check in" must not disagree.
        #
        # This is what broke: with the session IDLE this returned 409, the doorpad sent an
        # empty session_token, and POST /checkins 422'd — a visitor tapped
        # "Check in as <name>" and nothing happened at all.
        session_key = snapshot.session_id
        if session_key is None:
            held = self.identity.current()
            session_key = held.interaction_id if held is not None else None
        if session_key is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No active visitor session",
            )
        expires_at = int(time.time() + self.config.visitor_token_ttl_s)
        token = encode_visitor_token(
            secret=self.config.visitor_token_secret,
            session_id=session_key,
            expires_at=expires_at,
        )

        # The QR must work for a phone on cellular, which cannot resolve the LAN
        # host — but it must also keep working with the internet down (E-19). So
        # advertise the relay only when we have *observed* it reachable recently,
        # and fall back to the LAN URL otherwise. Failure direction is toward the
        # thing that works offline.
        if self._visitor_relay_is_fresh():
            base = (
                self.config.visitor_relay_public_url or self.config.visitor_relay_base_url
            ).rstrip("/")
            url = f"{base}/v/{token}"
            via = "relay"
        else:
            url = f"{self.config.visitor_public_base_url.rstrip('/')}/visitor?token={token}"
            via = "lan"

        # Push immediately so the snapshot is there before the visitor can scan.
        self._push_visitor_snapshot_soon(token)
        return {"token": token, "url": url, "expires_at": expires_at, "via": via}

    def attributed_display_name(self) -> str | None:
        """The name to disclose on a surface that is about to attribute a write.

        E-23 requires every such surface to say whose name will be attached
        *before* the write. That obligation is only meetable if the surfaces are
        told, and it must not be met by each of them re-deriving the consent policy
        — so this is the single answer they all render.
        """
        if self.attributable_person_id() is None:
            return None
        held = self.identity.current()
        return held.display_name if held is not None else None

    def attributable_person_id(self) -> str | None:
        """The recognised person a write may be attributed to, or None.

        One gate for every attribution path — guestbook, votes, check-ins — so they
        cannot drift apart. Returns None unless somebody is currently recognised
        **and** their consent covers attribution (ADR-0018 §2, E-25): v1/v2
        enrollees agreed to a greeting, not to having their name attached to what
        they write.

        Reads the interaction-scoped identity rather than the session snapshot
        (ADR-0020). Bound to session state this returned None ten seconds after
        recognition, so tapping through to Check In lost the name that had just been
        displayed — the door greeted someone and then refused to let them be
        themselves.

        Fails closed in two ways worth naming: an unparseable or missing consent
        version is not attributable, and the identity is memory-only, so a restart
        leaves writes anonymous rather than optimistically attributed.
        """
        held = self.identity.current()
        if held is None:
            return None
        if not consent_covers_extended_personalisation(held.consent_version):
            return None
        return held.person_id

    # -- visitor relay (ADR-0017) ------------------------------------------

    def _visitor_relay_is_fresh(self) -> bool:
        """True when a relay exchange succeeded inside the freshness window."""
        if self.visitor_relay_worker is None:
            return False
        last = self.visitor_relay_worker.stats.last_success_monotonic
        if last is None:
            return False
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            return False
        return (now - last) <= self.config.visitor_relay_freshness_s

    def _push_visitor_snapshot_soon(self, token: str) -> None:
        """Remember the live token and ask the worker to publish on its next tick."""
        self._visitor_relay_token = token
        if self.visitor_relay_worker is not None:
            self.visitor_relay_worker.request_push()

    def visitor_relay_snapshot(self) -> VisitorSessionSnapshot | None:
        """Project public session state for the relay (ADR-0017 §2 allow-list).

        Returns None when there is no live session or no token has been issued —
        there is nothing public to publish, and publishing a stale session would
        let an old QR keep working.
        """
        token = self._visitor_relay_token
        if token is None:
            return None
        snapshot = self.machine.snapshot()
        if snapshot.session_id is None:
            return None
        try:
            claims = decode_visitor_token(token, secret=self.config.visitor_token_secret)
        except VisitorTokenError:
            # The token expired; stop publishing rather than advertising a session
            # a phone can no longer act on.
            self._visitor_relay_token = None
            return None
        if claims.session_id != snapshot.session_id:
            self._visitor_relay_token = None
            return None

        poll = self.social_service.get_current_poll()
        poll_payload: dict[str, Any] | None = None
        results: list[dict[str, Any]] | None = None
        if poll is not None:
            poll_payload = {
                "id": poll.id,
                "question": poll.question,
                "options": [{"id": option.id, "label": option.text} for option in poll.options],
            }
            with contextlib.suppress(Exception):
                results = self.social_service.poll_results(poll.id)

        return build_visitor_snapshot(
            session_token=token,
            session_id=str(snapshot.session_id),
            state=snapshot.state.value,
            expires_at=datetime.fromtimestamp(claims.expires_at, tz=UTC),
            poll=poll_payload,
            poll_results=results,
            # Every outcome applied for this session. A push replaces the relay's
            # snapshot, so leaving these out would wipe the receipt the visitor's
            # phone is polling for — `_visitor_relay_applied` is cleared on
            # session end, so it holds only this session's results.
            outcomes=list(self._visitor_relay_applied.values()),
            # Only when consent covers attribution, so an unattributed visitor is
            # never told a name will be attached (ADR-0018 §2, E-23).
            attributed_to=self.attributed_display_name(),
        )

    def visitor_relay_apply(self, action: VisitorQueuedAction) -> VisitorActionOutcome:
        """Apply one relay-collected visitor write through the normal social path.

        Content authority stays with ``SocialService`` (E-18): same sanitiser, same
        rate limits as the LAN path. This method only translates and reports.

        Never raises — a bad action becomes a `rejected` outcome so one visitor
        cannot stall the queue for the next.
        """
        kind: str = "note"
        try:
            token = self._visitor_relay_token
            if token is None:
                return VisitorActionOutcome(
                    action_id=action.action_id,
                    kind="note",
                    status="rejected",
                    reason="session_mismatch",
                )
            # Applying the same action_id twice (a duplicate delivery after a
            # missed ack) must not double-post; the relay leases rather than
            # deletes, so this is a real case, not a theoretical one.
            if action.action_id in self._visitor_relay_applied:
                return self._visitor_relay_applied[action.action_id]

            # Bucket rate limits per session rather than per real IP: the relay
            # deliberately does not forward visitor IP addresses, and we would
            # rather not collect them anyway.
            pseudo_ip = f"relay:{action.session_id}"

            attributed = self.attributable_person_id()

            if action.note is not None:
                kind = "note"
                entry = self.social_service.create_guestbook_entry(
                    text=action.note.text,
                    author_label="Left via phone",
                    ip=pseudo_ip,
                    session_token=token,
                    trace_id=str(uuid4()),
                    person_id=attributed,
                )
                outcome = VisitorActionOutcome(
                    action_id=action.action_id, kind=kind, status="applied", entry_id=entry.id
                )
            elif action.vote is not None:
                kind = "vote"
                self.social_service.cast_vote(
                    poll_id=action.vote.poll_id,
                    option_id=action.vote.option_id,
                    ip=pseudo_ip,
                    session_token=token,
                    trace_id=str(uuid4()),
                    person_id=attributed,
                )
                outcome = VisitorActionOutcome(
                    action_id=action.action_id, kind=kind, status="applied"
                )
            elif action.deletion_request is not None:
                kind = "deletion_request"
                self.social_service.request_deletion(
                    target_kind=action.deletion_request.target_kind,
                    target_id=action.deletion_request.target_id,
                    ip=pseudo_ip,
                    session_token=token,
                    trace_id=str(uuid4()),
                )
                outcome = VisitorActionOutcome(
                    action_id=action.action_id, kind=kind, status="applied"
                )
            else:
                return VisitorActionOutcome(
                    action_id=action.action_id,
                    kind=kind,
                    status="rejected",
                    reason="empty_action",
                )
        except Exception as exc:
            reason = _visitor_reject_reason(exc)
            logger.warning(
                "visitor_relay_action_rejected",
                extra={"action_id": action.action_id, "kind": kind, "reason": reason},
            )
            outcome = VisitorActionOutcome(
                action_id=action.action_id, kind=kind, status="rejected", reason=reason
            )

        self._visitor_relay_applied[action.action_id] = outcome
        # Bounded: one door session cannot accumulate more than the relay's own cap.
        if len(self._visitor_relay_applied) > 256:
            for stale in list(self._visitor_relay_applied)[:128]:
                del self._visitor_relay_applied[stale]
        return outcome

    def visitor_relay_status(self) -> dict[str, Any]:
        if not self.config.visitor_relay_base_url or not self.config.visitor_relay_device_token:
            return {"configured": False, "status": "disabled"}
        worker = self.visitor_relay_worker
        if worker is None:
            return {"configured": True, "status": "stopped"}
        stats = worker.stats
        return {
            "configured": True,
            "status": "ok" if self._visitor_relay_is_fresh() else "degraded",
            "pushes_ok": stats.pushes_ok,
            "polls_ok": stats.polls_ok,
            "polls_failed": stats.polls_failed,
            "actions_applied": stats.actions_applied,
            "actions_rejected": stats.actions_rejected,
            "consecutive_failures": stats.consecutive_failures,
            "last_error": stats.last_error,
            "qr_target": "relay" if self._visitor_relay_is_fresh() else "lan",
        }

    def start_visitor_relay(self) -> None:
        """Start the visitor relay worker, if one is configured.

        Nothing here may prevent door-api from serving: the visitor QR working
        remotely is a convenience, and the session state machine must not depend
        on it.
        """
        if not self.config.visitor_relay_base_url or not self.config.visitor_relay_device_token:
            return
        transport = self._visitor_relay_transport or HttpVisitorRelayTransport(
            base_url=self.config.visitor_relay_base_url,
            device_token=self.config.visitor_relay_device_token,
            timeout_s=self.config.visitor_relay_timeout_s,
        )
        self.visitor_relay_worker = VisitorRelayWorker(
            transport=transport,
            handler=self,
            poll_interval_s=self.config.visitor_relay_poll_interval_s,
            backoff_max_s=self.config.visitor_relay_backoff_max_s,
        )
        # startup() is also called from synchronous contexts (tests, and any future
        # non-ASGI entry point), where there is no loop to attach to. Same pattern
        # as the MQTT bridge: build the object, start the loop only if there is one.
        # A worker that never starts simply leaves the QR on its LAN fallback.
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            self._visitor_relay_task = loop.create_task(
                self.visitor_relay_worker.start(), name="door-api-visitor-relay-start"
            )

    def verify_visitor_token(self, token: str) -> VisitorTokenClaims:
        try:
            claims = decode_visitor_token(token, secret=self.config.visitor_token_secret)
        except VisitorTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"code": "invalid_visitor_token", "message": str(exc)}},
            ) from exc
        # A token is live if it names either the current session or the interaction the
        # door is still holding (ADR-0020). Minting had to learn the second case; so does
        # verification, or a recognised person gets a token that is refused on use —
        # which is what the reported check-in failure became after only half the fix.
        snapshot = self.machine.snapshot()
        held = self.identity.current()
        valid_keys = {
            key
            for key in (
                snapshot.session_id,
                held.interaction_id if held is not None else None,
            )
            if key is not None
        }
        if claims.session_id not in valid_keys:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "inactive_visitor_session",
                        "message": "visitor session is no longer active",
                    }
                },
            )
        return claims

    def photo_session_id(self) -> UUID:
        snapshot = self.machine.snapshot()
        if snapshot.session_id is not None:
            return snapshot.session_id
        return uuid4()


state = DoorApiState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.startup()
    yield
    state.shutdown()


app = FastAPI(lifespan=lifespan)


# Paths whose use means "a person is interacting with the door right now", so the
# recognised identity should outlive the approach timer (ADR-0020). A middleware rather
# than a `touch()` in each handler because there are a dozen of these and more coming:
# one forgotten call would look like the intermittent version of the bug this fixes.
# The social write routes sit at the top level, not under /social — checking in POSTs to
# /checkins, the guestbook to /guestbook, a vote to /polls/{id}/vote. Listing only
# ("/doorpad", "/social", "/visitor") meant the actual writes never re-armed the identity
# window, so somebody who took their time on the check-in screen was forgotten mid-flow.
_INTERACTION_PATH_PREFIXES = (
    "/doorpad",
    "/social",
    "/visitor",
    "/checkins",
    "/guestbook",
    "/polls",
)


@app.middleware("http")
async def _extend_identity_on_interaction(request: Request, call_next: Any) -> Any:
    """Re-arm the identity window when the doorpad or a visitor surface is used.

    Reads (`GET`) count: paging through screens is interaction, and the doorpad polls
    its own state as the visitor moves. Deliberately excludes `/admin` (the owner's
    phone is not a person at the door) and `/internal/events` (recognition itself must
    not extend its own window, or one match would hold a name indefinitely).
    """
    if request.method != "OPTIONS" and request.url.path.startswith(_INTERACTION_PATH_PREFIXES):
        state.identity.touch()
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=list(state.config.cors_origins),
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(
    build_social_router(
        lambda: state.social_service,
        # Gated, not raw: attribution requires consent that covers it (ADR-0018).
        state.attributable_person_id,
        lambda token: state.verify_visitor_token(token).session_id,
    )
)


class PhotoBoothSessionBody(BaseModel):
    session_id: str


class GalleryApproveBody(BaseModel):
    tags: list[str] = []
    wallboard_moment: bool = False


class GalleryTagsBody(BaseModel):
    tags: list[str] = []
    wallboard_moment: bool | None = None


class WallboardFocusBody(BaseModel):
    # One of ``WALLBOARD_FOCUS_CHANNELS`` or the literal ``"ambient"`` (return to
    # the full grid). Mirrors the doorpad's ``createWallboardFocusRequest`` input.
    channel: str


# Non-``vision.*`` events this route also accepts, named one at a time.
#
# ``media.storage_status`` is here because door-media emits it every 30 s, door-ui *subscribes*
# to it, and nothing carried it between the two: door-api forwards session events *to*
# door-media and nothing comes back, so the capacity card sat on "Waiting for a
# media.storage_status update; no capacity is being guessed." permanently. Same shape as the
# greeting bug this route was created to fix.
#
# Listed rather than allowing ``media.*`` wholesale: the other media events assert that a
# recording exists or was deleted, which is a claim about durable state. Read-only capacity
# telemetry is not.
_INTERNAL_EVENT_TYPES = frozenset({"media.storage_status"})


# Focusable wallboard tiles — kept in lockstep with the ``WallboardFocusChannel``
# ids in apps/door-ui/src/wallboardChannelModel.ts. ``"ambient"`` (return to the
# default grid) is accepted by the endpoint but is not itself a focus channel.
WALLBOARD_FOCUS_CHANNELS = frozenset(
    {
        "aircraft",
        "satellite",
        "scoreboard",
        "birds",
        "printer",
        "food",
        "poll",
        "guestbook",
        "moments",
        "about",
    }
)
# Matches WALLBOARD_FOCUS_TIMEOUT_MS in wallboardChannelModel.ts: a focus auto-
# returns to ambient after 2 minutes (the wallboard keys off ``expiresAt``).
WALLBOARD_FOCUS_TIMEOUT_MS = 120_000


def _require_photobooth() -> None:
    if not state.config.feature_photobooth:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="photo booth disabled")


def _require_admin(authorization: str | None = Header(default=None)) -> None:
    configured = state.social_config.admin_token
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin authentication is not configured",
        )
    prefix = "Bearer "
    presented = (
        authorization[len(prefix) :] if authorization and authorization.startswith(prefix) else ""
    )
    if not presented or not secrets.compare_digest(presented, configured):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")


def _rows_from_recordings_response(data: Any) -> list[dict[str, Any]]:
    rows = data.get("recordings", []) if isinstance(data, dict) else data
    return [dict(row) for row in rows if isinstance(row, dict)]


def _media_auth_headers() -> dict[str, str]:
    token = state.config.media_admin_token
    return {"Authorization": f"Bearer {token}"} if token else {}


def _sync_auth_headers() -> dict[str, str]:
    token = state.config.sync_admin_token
    return {"Authorization": f"Bearer {token}"} if token else {}


@app.get("/health", response_model=HealthPayload)
async def health() -> HealthPayload:
    return HealthPayload(service="door-api", status=HealthStatus.OK, detail=None)


@app.get("/metrics")
async def metrics() -> Response:
    data = {**state.machine.metrics.to_dict(), **state.social_service.metrics.to_dict()}
    data.update(
        {
            "door_api_doorpad_effect_requests_total": state.effect_requests,
            "door_api_doorpad_effect_unavailable_total": state.effect_unavailable,
            "door_api_media_forward_errors_total": state.media_forward_errors,
            "door_api_media_forward_successes_total": state.media_forward_successes,
            "door_api_media_outbox_depth": state.store.media_outbox_depth(),
            "door_api_media_outbox_dropped_total": state.store.media_outbox_dropped_total(),
            "door_api_sync_forward_errors_total": state.sync_forward_errors,
            "door_api_sync_forward_successes_total": state.sync_forward_successes,
            "door_api_sync_outbox_depth": state.store.sync_outbox_depth(),
            "door_api_sync_outbox_dropped_total": state.store.sync_outbox_dropped_total(),
            "door_api_mqtt_bridge_enabled": int(state.mqtt_bridge is not None),
            "door_api_mqtt_bridge_messages_received_total": (
                state.mqtt_bridge.messages_received if state.mqtt_bridge else 0
            ),
            "door_api_mqtt_bridge_messages_broadcast_total": (
                state.mqtt_bridge.messages_broadcast if state.mqtt_bridge else 0
            ),
            "door_api_mqtt_bridge_parse_errors_total": (
                state.mqtt_bridge.parse_errors if state.mqtt_bridge else 0
            ),
            "door_api_mqtt_bridge_broadcast_errors_total": (
                state.mqtt_bridge.broadcast_errors if state.mqtt_bridge else 0
            ),
            # Distinguishes "no controller configured" from "configured and
            # failing", which is the first question to ask during bring-up.
            "door_api_esp32_link_enabled": int(state.esp32_link is not None),
            "door_api_esp32_transport_attached": int(state.esp32_transport is not None),
            **(
                state.esp32_link.metrics()
                if state.esp32_link is not None
                else {
                    "door_api_esp32_link_connected": 0,
                    "door_api_esp32_link_connects_total": 0,
                    "door_api_esp32_link_reopens_total": 0,
                    "door_api_esp32_link_open_failures_total": 0,
                    "door_api_esp32_link_idle_timeouts_total": 0,
                }
            ),
        }
    )
    lines = [
        "# TYPE door_api_media_outbox_depth gauge",
        "# TYPE door_api_sync_outbox_depth gauge",
        *[f"{name} {value}" for name, value in data.items()],
        "",
    ]
    return Response(
        content="\n".join(lines),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/session")
async def get_session() -> dict[str, Any]:
    return state.snapshot_response()


def _require_internal_event_token(authorization: str | None = Header(default=None)) -> None:
    configured = state.config.internal_event_token
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="internal event ingest is not configured",
        )
    prefix = "Bearer "
    presented = (
        authorization[len(prefix) :] if authorization and authorization.startswith(prefix) else ""
    )
    if not presented or not secrets.compare_digest(presented, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid internal event token"
        )


@app.post(
    "/internal/events",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(_require_internal_event_token)],
)
async def internal_events(payload: dict[str, Any]) -> dict[str, Any]:
    """Ingest one contract event from another Pi-local service (door-visiond).

    This is the hop that lets a recognised face reach the screen. door-visiond emits
    ``vision.identity_stable``; the session machine turns it into
    ``APPROACH_DETECTED``; door-ui renders the greeting from the resulting ``/ws``
    delta. Without it the event never left door-visiond's process, so a recognised
    person got the ESP32 light and a silent wallboard — the greeting ADR-0018 §3
    calls the entire point of the feature, and a T-303 deliverable.

    Narrow on purpose:

    - **``vision.*``, plus the named exceptions in ``_INTERNAL_EVENT_TYPES``.** The route
      can never be used to fake a button press, a contact change, or a session transition —
      the door's own inputs stay on the ESP32 link, which is the trust boundary that gives
      them meaning. Additions are listed one type at a time rather than by prefix, so
      widening this is always a deliberate act.
    - **Token required, 503 when unset.** An open identity ingest would let anything
      that can reach door-api assert who is standing at the door, and identity is
      what personalisation reads. Loopback binding is not the control here: the
      kiosk browsers run on this Pi too.
    - **202 whether or not state changed.** The caller is a fire-and-forget
      forwarder; a no-op must not read as a failure worth retrying.
    """
    try:
        event = parse_event(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid event envelope"
        ) from exc
    if event.type not in _INTERNAL_EVENT_TYPES and not event.type.startswith("vision."):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"event type {event.type} is not accepted on this route",
        )
    changed = state.handle_contract_event(event)
    return {"accepted": True, "changed": changed}


@app.post("/doorpad/ring")
async def doorpad_ring() -> dict[str, Any]:
    trace_id = uuid4()
    accepted = state.machine.handle_button_pressed(
        trace_id=trace_id,
        trigger="doorpad.touch_ring",
        entry="touch",
    )
    effect = await state.play_doorpad_effect(trace_id)
    return {"accepted": accepted, "effect": effect, **state.snapshot_response()}


@app.post("/doorpad/activity")
async def doorpad_activity() -> dict[str, Any]:
    """ "Somebody is using me" — sent by the doorpad on touch, so identity persists.

    The doorpad is a single-page app: moving from the home screen to Check In is
    client-side and reaches no route, so server-side writes alone cannot tell that
    someone is mid-interaction. Without this, reading the screen for fifteen seconds
    silently loses the name that was just greeted.

    The `touch()` itself happens in the interaction middleware; this route exists so the
    UI has something to call that does nothing else. It cannot create or extend a
    *session*, and it cannot resurrect an expired identity (see
    :meth:`RecognisedIdentity.touch`), so an idle doorpad cannot hold a stranger's name
    on screen by polling.
    """
    return {
        "identity_expires_in_s": round(state.identity.seconds_remaining(), 1),
        **state.snapshot_response(),
    }


@app.post("/doorpad/session/end")
async def doorpad_session_end() -> dict[str, Any]:
    accepted = state.machine.handle_session_end(trigger="visitor:end")
    # "Done" means finished, so the name goes with the session rather than lingering for
    # the interaction window. Ordered after the transition because the middleware has
    # already touched the identity on the way in — forgetting last is what sticks.
    state.identity.forget()
    return {"accepted": accepted, **state.snapshot_response()}


@app.post("/admin/session/answer", dependencies=[Depends(_require_admin)])
async def admin_session_answer() -> dict[str, Any]:
    accepted = state.machine.handle_answered(trigger="owner:answered")
    return {"accepted": accepted, **state.snapshot_response()}


@app.post("/admin/session/cannot-answer", dependencies=[Depends(_require_admin)])
async def admin_session_cannot_answer() -> dict[str, Any]:
    accepted = state.machine.handle_unanswered(trigger="owner:cannot_answer")
    return {"accepted": accepted, **state.snapshot_response()}


@app.post("/admin/session/end", dependencies=[Depends(_require_admin)])
async def admin_session_end() -> dict[str, Any]:
    accepted = state.machine.handle_session_end(trigger="admin:reset")
    # An owner resetting the door expects a clean slate, including the held name.
    state.identity.forget()
    return {"accepted": accepted, **state.snapshot_response()}


@app.post("/doorpad/video-message/offer")
async def video_message_offer() -> dict[str, Any]:
    trace_id = uuid4()
    accepted = state.machine.handle_video_message_offer(trace_id=trace_id)
    effect = await state.play_doorpad_effect(trace_id)
    return {"accepted": accepted, "effect": effect, **state.snapshot_response()}


@app.post("/doorpad/video-message/start")
async def video_message_start() -> dict[str, Any]:
    if state.machine.state.name not in {
        "VIDEO_MESSAGE_OFFERED",
        "VIDEO_MESSAGE_REVIEW",
    }:
        state.machine.handle_video_message_offer(trace_id=uuid4())
    accepted = state.machine.handle_video_message_start()
    return {"accepted": accepted, **state.snapshot_response()}


@app.post("/doorpad/video-message/stop")
async def video_message_stop() -> dict[str, Any]:
    accepted = state.machine.handle_video_message_stop()
    return {"accepted": accepted, **state.snapshot_response()}


class VideoMessageSaveBody(BaseModel):
    # Optional chosen recipient keys for per-recipient routing (ADR-0014). The
    # DoorPad Tiger/Adam/both buttons (a follow-up PR) POST these; when omitted
    # the message is a legacy broadcast to all configured chats.
    recipients: list[str] | None = None


@app.post("/doorpad/video-message/save")
async def video_message_save(body: VideoMessageSaveBody | None = None) -> dict[str, Any]:
    recipients = body.recipients if body is not None else None
    accepted = state.machine.handle_video_message_save(recipients=recipients)
    return {"accepted": accepted, **state.snapshot_response()}


@app.post("/doorpad/video-message/discard")
async def video_message_discard() -> dict[str, Any]:
    accepted = state.machine.handle_video_message_discard()
    if not accepted and state.machine.state.name != "IDLE":
        accepted = state.machine.handle_admin_reset()
    return {"accepted": accepted, **state.snapshot_response()}


@app.get("/doorpad/video-message/latest")
async def latest_video_message() -> dict[str, Any]:
    snapshot = state.machine.snapshot()
    if snapshot.session_id is None:
        return {"recording": None}
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.get(
                f"{state.config.media_base_url.rstrip('/')}/recordings",
                headers=_media_auth_headers(),
            )
            resp.raise_for_status()
            rows = _rows_from_recordings_response(resp.json())
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="door-media unavailable",
        ) from exc

    recording = _latest_video_message_recording(rows, snapshot.session_id)
    if recording is None:
        return {"recording": None}
    recording["playback_url"] = (
        f"{state.config.media_public_base_url.rstrip('/')}/recordings/"
        f"{recording['recording_id']}/file?session_id={snapshot.session_id}"
    )
    return {"recording": recording}


@app.post("/doorpad/photo-booth/capture")
async def photo_booth_capture() -> dict[str, Any]:
    _require_photobooth()
    session_id = state.photo_session_id()
    trace_id = uuid4()
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.post(
                f"{state.config.media_base_url.rstrip('/')}/photos/capture",
                json={"session_id": str(session_id), "trace_id": str(trace_id)},
            )
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="door-media unavailable",
        ) from exc
    photo = body["photo"]
    photo["review_url"] = (
        f"{state.config.media_public_base_url.rstrip('/')}{photo['review_url']}"
        if photo.get("review_url", "").startswith("/")
        else photo.get("review_url")
    )
    return {"photo": photo, "session_id": str(session_id)}


@app.post("/doorpad/photo-booth/{recording_id}/save")
async def photo_booth_save(recording_id: str, body: PhotoBoothSessionBody) -> dict[str, Any]:
    _require_photobooth()
    trace_id = uuid4()
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.post(
                f"{state.config.media_base_url.rstrip('/')}/photos/{recording_id}/save",
                json={"session_id": body.session_id, "trace_id": str(trace_id)},
            )
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="photo not found") from exc
        raise HTTPException(status_code=503, detail="door-media unavailable") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="door-media unavailable") from exc
    recording = payload["recording"]
    if recording.get("playback_url", "").startswith("/"):
        recording["playback_url"] = (
            f"{state.config.media_public_base_url.rstrip('/')}{recording['playback_url']}"
        )
    return {"recording": recording}


@app.post("/doorpad/photo-booth/{recording_id}/discard")
async def photo_booth_discard(recording_id: str, body: PhotoBoothSessionBody) -> dict[str, Any]:
    _require_photobooth()
    trace_id = uuid4()
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.post(
                f"{state.config.media_base_url.rstrip('/')}/photos/{recording_id}/discard",
                json={"session_id": body.session_id, "trace_id": str(trace_id)},
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="photo not found") from exc
        raise HTTPException(status_code=503, detail="door-media unavailable") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="door-media unavailable") from exc
    return {"discarded": recording_id}


@app.get("/visitor-token")
async def visitor_token() -> dict[str, str | int]:
    return state.visitor_token()


@app.post("/doorpad/enroll-invite", status_code=status.HTTP_201_CREATED)
async def doorpad_enroll_invite() -> dict[str, Any]:
    """Mint a self-service enrollment invite for whoever is at the doorpad (ADR-0019).

    A forwarder, not a policy: every cap, the locked-volume check and privacy mode all
    live in door-visiond, which owns the enrollment store. This exists because the
    kiosks connect to door-api and nothing else (ARCHITECTURE.md §7) — the doorpad
    cannot call door-visiond itself, and giving the kiosk browser a second base URL and
    a credential to go with it is exactly what §7 exists to prevent.

    Unauthenticated, like the bell and the guestbook: standing at the door is the
    authorization (ADR-0019 §1). The response carries the invite URL, which is the only
    copy of its secret — same as the admin path.
    """
    try:
        async with httpx.AsyncClient(timeout=state.config.visiond_timeout_s) as client:
            resp = await client.post(
                f"{state.config.visiond_base_url.rstrip('/')}/self-enroll/invites",
            )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="door-visiond unavailable",
        ) from exc
    if resp.status_code == status.HTTP_201_CREATED:
        body: dict[str, Any] = resp.json()
        return body
    # Refusals are passed through with their status and reason intact, so the doorpad
    # can say "enrollment is closed for the next hour" rather than "something failed".
    if resp.status_code in (
        status.HTTP_409_CONFLICT,
        status.HTTP_429_TOO_MANY_REQUESTS,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ):
        detail: Any = "enrollment unavailable"
        with contextlib.suppress(Exception):
            detail = resp.json().get("detail", detail)
        headers = (
            {"Retry-After": resp.headers["Retry-After"]} if "Retry-After" in resp.headers else None
        )
        raise HTTPException(status_code=resp.status_code, detail=detail, headers=headers)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="door-visiond refused the request",
    )


@app.get("/visitor-relay-status")
async def visitor_relay_status() -> dict[str, Any]:
    """Whether the visitor QR is currently pointing at the relay or the LAN.

    Public: it reports reachability and counters, never visitor content.
    """
    return state.visitor_relay_status()


@app.get("/visitor-session")
async def visitor_session(token: str) -> dict[str, Any]:
    claims = state.verify_visitor_token(token)
    snapshot = state.machine.snapshot()
    return {
        "session_id": str(claims.session_id),
        "expires_at": claims.expires_at,
        "state": snapshot.state.value,
        # So the page can disclose attribution before the visitor writes (E-23).
        "attributed_to": state.attributed_display_name(),
    }


@app.get("/admin/media-inbox", dependencies=[Depends(_require_admin)])
async def admin_media_inbox() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.get(
                f"{state.config.media_base_url.rstrip('/')}/recordings",
                headers=_media_auth_headers(),
            )
            resp.raise_for_status()
            rows = _rows_from_recordings_response(resp.json())
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="door-media unavailable",
        ) from exc
    return {
        "recordings": [row for row in rows if row.get("kind") == "video_message"],
    }


@app.get(
    "/admin/media-inbox/{recording_id}/file",
    dependencies=[Depends(_require_admin)],
)
async def admin_media_inbox_file(recording_id: str) -> Response:
    row = await _media_recording(recording_id)
    if row is None or row.get("kind") != "video_message" or not row.get("session_id"):
        raise HTTPException(status_code=404, detail="video message not found")
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            response = await client.get(
                f"{state.config.media_base_url.rstrip('/')}/recordings/{recording_id}/file",
                params={"session_id": row["session_id"]},
                headers=_media_auth_headers(),
            )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="door-media unavailable") from exc
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="video message file not found")
    if response.status_code >= 400:
        raise HTTPException(status_code=503, detail="door-media unavailable")
    return Response(
        content=response.content,
        media_type="video/mp4",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get(
    "/admin/media-inbox/{recording_id}/thumbnail",
    dependencies=[Depends(_require_admin)],
)
async def admin_media_inbox_thumbnail(recording_id: str) -> Response:
    """Proxy a recording's thumbnail still, for the owner's ring notification.

    Unlike the ``/file`` route beside it this accepts any ``kind``: that one exists for
    the DoorPad's video-message review and is deliberately scoped to ``video_message``,
    whereas the picture the owner wants on a bell press comes from a ``bell_clip``.

    Still admin-authenticated. A thumbnail is a frame of whoever was at the door, so it
    is exactly the kind of thing ARCHITECTURE.md §2 keeps off low-trust surfaces.
    """
    row = await _media_recording(recording_id)
    if row is None:
        raise HTTPException(status_code=404, detail="recording not found")
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            response = await client.get(
                f"{state.config.media_base_url.rstrip('/')}/recordings/{recording_id}/thumbnail",
                headers=_media_auth_headers(),
            )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="door-media unavailable") from exc
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="thumbnail not found")
    if response.status_code >= 400:
        raise HTTPException(status_code=503, detail="door-media unavailable")
    return Response(
        content=response.content,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.api_route(
    "/admin/visiond/{path:path}",
    methods=["GET", "POST"],
    dependencies=[Depends(_require_admin)],
)
async def admin_visiond_proxy(path: str, request: Request) -> Response:
    """Reach door-visiond's admin surface from the owner's browser (ADR-0024).

    door-visiond binds loopback, so the admin page could not talk to it from anything but
    the Pi's own browser — and it rendered those failures as facts: "Enrolled Members (0)"
    for a door with two people enrolled, "Relay not configured" for a configured relay.

    Allow-listed per method+path in `service_proxy`, not open forwarding: door-api's admin
    token must not become a skeleton key for every route door-visiond ever grows.
    """
    return await _proxy_to_service(
        request=request,
        path=f"/{path}",
        routes=VISIOND_ROUTES,
        base_url=state.config.visiond_base_url,
        token=state.config.visiond_admin_token,
        timeout_s=state.config.visiond_timeout_s,
        service="door-visiond",
    )


@app.api_route(
    "/admin/door-media/{path:path}",
    methods=["GET"],
    dependencies=[Depends(_require_admin)],
)
async def admin_media_proxy(path: str, request: Request) -> Response:
    """The one door-media route the admin page needs that is not already first-class."""
    return await _proxy_to_service(
        request=request,
        path=f"/{path}",
        routes=MEDIA_ROUTES,
        base_url=state.config.media_base_url,
        token=state.config.media_admin_token,
        timeout_s=state.config.media_timeout_s,
        service="door-media",
    )


async def _proxy_to_service(
    *,
    request: Request,
    path: str,
    routes: tuple[ProxyRoute, ...],
    base_url: str,
    token: str,
    timeout_s: float,
    service: str,
) -> Response:
    try:
        resolve(routes, request.method, path)
    except ProxyDenied as exc:
        # 403 rather than 404: the caller authenticated fine, this route is simply not
        # something the admin surface may reach.
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    body = await request.body()
    try:
        response = await forward(
            base_url=base_url,
            token=token,
            method=request.method,
            path=path,
            query=str(request.url.query),
            timeout_s=timeout_s,
            body=body or None,
            content_type=request.headers.get("content-type"),
        )
    except Exception as exc:
        # An explicit 503 with the service named, so the page can say "could not reach
        # door-visiond" instead of drawing an empty list.
        raise HTTPException(status_code=503, detail=f"{service} unavailable") from exc

    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/json"),
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/admin/recordings", dependencies=[Depends(_require_admin)])
async def admin_recordings(
    kind: str | None = None,
    sync_status: str | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> dict[str, Any]:
    params: dict[str, str | int] = {"limit": max(1, min(limit, 100))}
    if kind:
        params["kind"] = kind
    if sync_status:
        params["sync_status"] = sync_status
    if cursor:
        params["cursor"] = cursor
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            response = await client.get(
                f"{state.config.media_base_url.rstrip('/')}/recordings",
                params=params,
                headers=_media_auth_headers(),
            )
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="door-media unavailable") from exc


@app.delete(
    "/admin/recordings/{recording_id}",
    dependencies=[Depends(_require_admin)],
)
async def admin_recording_delete(recording_id: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            response = await client.delete(
                f"{state.config.media_base_url.rstrip('/')}/recordings/{recording_id}",
                headers=_media_auth_headers(),
            )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="door-media unavailable") from exc
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="recording not found")
    if response.status_code >= 400:
        raise HTTPException(status_code=503, detail="door-media unavailable")
    return response.json()


@app.get("/admin/gallery/photos", dependencies=[Depends(_require_admin)])
async def admin_gallery_photos() -> dict[str, Any]:
    _require_photobooth()
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            media_resp = await client.get(
                f"{state.config.media_base_url.rstrip('/')}/recordings",
                params={"kind": "photo_booth"},
                headers=_media_auth_headers(),
            )
            media_resp.raise_for_status()
            sync_resp = await client.get(
                f"{state.config.sync_base_url.rstrip('/')}/internal/gallery/photos",
                headers=_sync_auth_headers(),
            )
            sync_resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="gallery unavailable") from exc
    recordings = _rows_from_recordings_response(media_resp.json())
    approved = {
        photo["recording_id"]: photo
        for photo in sync_resp.json().get("photos", [])
        if isinstance(photo, dict)
    }
    photos = []
    for row in recordings:
        if row.get("sync_status") == "deleted":
            continue
        gallery = approved.get(row.get("recording_id"))
        photos.append(
            {
                **row,
                "gallery": gallery,
                "gallery_status": gallery.get("status") if gallery else "pending",
                "tags": gallery.get("tags", []) if gallery else [],
                "wallboard_moment": bool(gallery.get("wallboard_moment")) if gallery else False,
            }
        )
    return {"photos": photos}


@app.post(
    "/admin/gallery/photos/{recording_id}/approve",
    dependencies=[Depends(_require_admin)],
)
async def admin_gallery_approve(recording_id: str, body: GalleryApproveBody) -> dict[str, Any]:
    _require_photobooth()
    row = await _media_recording(recording_id)
    if (
        row is None
        or row.get("kind") != "photo_booth"
        or not row.get("path")
        or not row.get("sha256")
    ):
        raise HTTPException(status_code=404, detail="photo not found")
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.post(
                f"{state.config.sync_base_url.rstrip('/')}/internal/gallery/photos/"
                f"{recording_id}/approve",
                json={
                    "local_path": row["path"],
                    "thumbnail_path": row.get("thumbnail_path"),
                    "consent_metadata_path": row.get("consent_metadata_path"),
                    "sha256": row["sha256"],
                    "tags": body.tags,
                    "approved_by": "owner",
                    "wallboard_moment": body.wallboard_moment,
                },
                headers=_sync_auth_headers(),
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="gallery unavailable") from exc


@app.patch(
    "/admin/gallery/photos/{recording_id}/tags",
    dependencies=[Depends(_require_admin)],
)
async def admin_gallery_tags(recording_id: str, body: GalleryTagsBody) -> dict[str, Any]:
    _require_photobooth()
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.patch(
                f"{state.config.sync_base_url.rstrip('/')}/internal/gallery/photos/"
                f"{recording_id}/tags",
                json={"tags": body.tags, "wallboard_moment": body.wallboard_moment},
                headers=_sync_auth_headers(),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="photo not found") from exc
        raise HTTPException(status_code=503, detail="gallery unavailable") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="gallery unavailable") from exc


@app.delete(
    "/admin/gallery/photos/{recording_id}",
    dependencies=[Depends(_require_admin)],
)
async def admin_gallery_delete(recording_id: str) -> dict[str, Any]:
    _require_photobooth()
    trace_id = uuid4()
    deletion_event = SocialDeletionRequestedEvent(
        event_id=uuid7_now(),
        type="social.deletion_requested",
        source="door-api",
        occurred_at=datetime.now(UTC),
        monotonic_ms=int(time.monotonic() * 1000),
        door_id=state.config.door_id,
        trace_id=trace_id,
        payload=SocialDeletionRequestedPayload(target_kind="photo", target_id=recording_id),
    )
    media_deleted = False
    gallery_deleted = False
    async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
        media_resp = await client.delete(
            f"{state.config.media_base_url.rstrip('/')}/recordings/{recording_id}",
            headers=_media_auth_headers(),
        )
        if media_resp.status_code not in (200, 404):
            raise HTTPException(status_code=503, detail="door-media unavailable")
        media_deleted = media_resp.status_code == 200
        sync_resp = await client.post(
            f"{state.config.sync_base_url.rstrip('/')}/internal/social-deletion",
            json={"event": deletion_event.model_dump(mode="json")},
            headers=_sync_auth_headers(),
        )
        if sync_resp.status_code >= 500:
            raise HTTPException(status_code=503, detail="gallery unavailable")
        if sync_resp.status_code < 400:
            gallery_deleted = bool(sync_resp.json().get("deleted"))
    state.broadcast.send_delta(
        {
            "type": "social.deletion_requested",
            "payload": deletion_event.payload.model_dump(mode="json"),
            "trace_id": str(trace_id),
        }
    )
    return {
        "recording_id": recording_id,
        "media_deleted": media_deleted,
        "gallery_deleted": gallery_deleted,
    }


@app.get("/wallboard/moments")
async def wallboard_moments() -> dict[str, Any]:
    _require_photobooth()
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.get(
                f"{state.config.sync_base_url.rstrip('/')}/internal/gallery/moments",
                headers=_sync_auth_headers(),
            )
            resp.raise_for_status()
            photos = resp.json().get("photos", [])
    except Exception:
        photos = []
    return {
        "photos": [
            {
                "recording_id": p["recording_id"],
                "tags": p.get("tags", []),
                "approved_at": p.get("approved_at"),
                "thumbnail_path": p.get("gallery_thumbnail_path"),
            }
            for p in photos
            if isinstance(p, dict) and p.get("status") == "approved"
        ]
    }


@app.post("/wallboard/focus")
async def wallboard_focus(body: WallboardFocusBody) -> dict[str, Any]:
    """Route a doorpad "focus a tile" request to the physically-separate wallboard.

    The doorpad (touchscreen) and the wallboard (a second HDMI display) run as
    two independent chromium instances with different ``--user-data-dir``
    profiles, so the old localStorage + ``storage``-event signal never crosses
    between them. Both surfaces DO connect to this service's ``/ws``, so we reuse
    that transport: the doorpad POSTs here and we fan a lightweight, EPHEMERAL
    UI-control message out to every ``/ws`` client (the wallboard picks it up).

    This is deliberately NOT a contract ``DoorboardEvent`` and is never persisted
    — it is a transient display-control message, mirroring the doorpad's
    ``createWallboardFocusRequest`` semantics (ambient → mode "ambient", no
    channel, no expiry; otherwise mode "focus" with a 2-minute ``expiresAt``).
    """
    channel = body.channel
    if channel != "ambient" and channel not in WALLBOARD_FOCUS_CHANNELS:
        raise HTTPException(status_code=400, detail=f"unknown wallboard channel: {channel!r}")

    is_ambient = channel == "ambient"
    now_ms = int(time.time() * 1000)
    message = {
        "type": "wallboard.focus_changed",
        "channel": None if is_ambient else channel,
        "mode": "ambient" if is_ambient else "focus",
        "requestId": str(uuid4()),
        "requestedAt": now_ms,
        "expiresAt": None if is_ambient else now_ms + WALLBOARD_FOCUS_TIMEOUT_MS,
    }
    state.broadcast.send_delta(message)
    return {"ok": True, "focus": message}


async def _approved_wallboard_photos() -> dict[str, dict[str, Any]]:
    """Map recording_id -> owner-approved, wallboard-eligible gallery photo.

    This is the same consent gate the Moments tile uses (``/wallboard/moments``):
    ``list_wallboard_moments`` in door-sync returns only photos that are
    ``status == "approved"``, ``approved_by == "owner"``, and flagged
    ``wallboard_moment``. Photos merely archived in the private gallery (not
    flagged for the wallboard) never surface here, so raw check-in photos stay
    private until the owner explicitly approves them for public display.
    """
    async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
        resp = await client.get(
            f"{state.config.sync_base_url.rstrip('/')}/internal/gallery/moments",
            headers=_sync_auth_headers(),
        )
        resp.raise_for_status()
        photos = resp.json().get("photos", [])
    return {
        p["recording_id"]: p
        for p in photos
        if isinstance(p, dict) and p.get("recording_id") and p.get("status") == "approved"
    }


@app.get("/admin/visitor-collage", dependencies=[Depends(_require_admin)])
async def admin_visitor_collage() -> dict[str, Any]:
    """Owner-only year-end "who's stopped by" collage + fun stats.

    This is deliberately NOT a public wallboard route: the collage collects
    silently all year and is only revealed on-demand (e.g. the last day of
    school) via the owner-only ``/reveal#<token>`` page, which calls this
    endpoint with the ``DOOR_API_SOCIAL_ADMIN_TOKEN`` bearer token. It must
    never surface on the public 27" wallboard, so it fails closed behind
    ``_require_admin`` (503 if no token is configured, 401 without a valid one).

    Stats are count-only aggregates over non-deleted check-ins (no images, no
    person_id). Photos are the intersection of check-ins that reference a photo
    (``checkins.photo_recording_id``) with owner-approved, wallboard-eligible
    gallery photos — so only photos the owner has explicitly approved for
    display ever appear.
    """
    stats = state.social_service.visitor_collage_stats()

    photos: list[dict[str, Any]] = []
    if state.config.feature_photobooth:
        try:
            approved = await _approved_wallboard_photos()
        except Exception:
            # Private gallery unavailable — degrade to stats-only, never leak.
            approved = {}
        if approved:
            for checkin in state.social_service.list_checkin_photos(limit=500):
                recording_id = checkin.photo_recording_id
                gallery = approved.get(recording_id) if recording_id else None
                if gallery is None:
                    continue
                photos.append(
                    {
                        "recording_id": recording_id,
                        "thumbnail_path": gallery.get("gallery_thumbnail_path"),
                        "label": checkin.label,
                        "created_at": checkin.created_at,
                    }
                )
    return {"stats": stats, "photos": photos}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = state.broadcast.make_client_queue()
    filters = ["*"]

    async def _send() -> None:
        while True:
            msg = await queue.get()
            try:
                decoded = json.loads(msg)
                event_type = decoded.get("event", {}).get("type")
                if (
                    decoded.get("type") != "delta"
                    or not isinstance(event_type, str)
                    or _matches_event_filters(event_type, filters)
                ):
                    await websocket.send_text(msg)
            finally:
                queue.task_done()

    async def _receive() -> None:
        nonlocal filters
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            requested = message.get("subscribe") if isinstance(message, dict) else None
            if (
                isinstance(requested, list)
                and 0 < len(requested) <= 64
                and all(
                    isinstance(pattern, str) and 0 < len(pattern) <= 128 for pattern in requested
                )
            ):
                filters = list(requested)

    tasks = {
        asyncio.create_task(_send(), name="door-api-ws-send"),
        asyncio.create_task(_receive(), name="door-api-ws-receive"),
    }
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            with contextlib.suppress(WebSocketDisconnect):
                task.result()
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    finally:
        state.broadcast.remove_client(queue)


def _matches_event_filters(event_type: str, filters: list[str]) -> bool:
    return any(
        pattern in ("", "*")
        or (pattern.endswith(".*") and event_type.startswith(pattern[:-1]))
        or event_type == pattern
        for pattern in filters
    )


def _latest_video_message_recording(
    rows: list[dict[str, Any]],
    session_id: UUID,
) -> dict[str, Any] | None:
    matches = [
        dict(row)
        for row in rows
        if row.get("session_id") == str(session_id)
        and row.get("kind") == "video_message"
        and row.get("path")
        and row.get("sync_status") != "deleted"
    ]
    if not matches:
        return None
    matches.sort(key=lambda row: row.get("finalized_at_utc") or row.get("started_at_utc") or "")
    return matches[-1]


async def _media_recording(recording_id: str) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=state.config.media_timeout_s) as client:
            resp = await client.get(
                f"{state.config.media_base_url.rstrip('/')}/recordings",
                headers=_media_auth_headers(),
            )
            resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="door-media unavailable") from exc
    for row in _rows_from_recordings_response(resp.json()):
        if row.get("recording_id") == recording_id:
            return row
    return None


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("door_api.app:app", host="0.0.0.0", port=8000, reload=True)
