"""Consume door-api's broadcast and speak recognised names (ADR-0034).

Deliberately a *separate process* from door-api. door-api owns
`button -> ESP32 feedback -> local UI`, and CLAUDE.md §1 says nothing new may sit
on that path — so speech synthesis, which spawns subprocesses and can wedge on a
missing audio device, lives out here where failing costs nothing.

This service only ever reads. It holds no credentials, writes no files, and makes
no call off the Pi. Its one listener is a loopback ``/health`` + ``/metrics``
server (ARCHITECTURE §3), served by asyncio itself so it adds no dependency.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime
from typing import Any

import websockets

from door_voice.policy import GreetingPolicy
from door_voice.settings import Settings
from door_voice.speech import Speaker

logger = logging.getLogger("door_voice.app")

# Only what this service acts on. door-api filters server-side, so an unrelated
# event never even reaches us.
SUBSCRIBE = ["vision.identity_stable", "vision.identity_expired"]


class VoiceService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._policy = GreetingPolicy(
            enabled=settings.enabled,
            allowed_person_ids=settings.allowed_person_ids,
            cooldown_s=settings.cooldown_s,
            quiet_hours=settings.quiet_hours,
        )
        self._speaker = Speaker(
            piper_binary=settings.piper_binary,
            piper_voice=settings.piper_voice,
            espeak_binary=settings.espeak_binary,
            aplay_binary=settings.aplay_binary,
            alsa_device=settings.alsa_device,
            timeout_s=settings.speak_timeout_s,
        )
        # One at a time: overlapping greetings would talk over each other, and
        # serialising also bounds how many TTS processes can exist at once.
        self._speaking = asyncio.Lock()
        # Observability. The /ws subscription retries silently for as long as
        # door-api is away, so "connected" is the health verdict; the counters
        # are the only record of what the speaker did.
        self._started_monotonic = time.monotonic()
        self._connected = False
        self._last_event_monotonic: float | None = None
        self.greetings_spoken = 0
        self.greetings_suppressed = 0
        self.greetings_failed = 0

    async def handle_event(self, event: dict) -> None:
        self._last_event_monotonic = time.monotonic()
        event_type = event.get("type")
        payload = event.get("payload") or {}

        if event_type == "vision.identity_expired":
            # Unenrolment / privacy mode: drop the cooldown so a re-enrolled
            # person isn't silently gated by stale state (ADR-0029 reasons).
            reason = payload.get("reason")
            person_id = payload.get("person_id")
            if reason in ("admin", "privacy_mode") and person_id:
                self._policy.forget_person(person_id)
            return

        if event_type != "vision.identity_stable":
            return

        person_id = payload.get("person_id")
        display_name = payload.get("display_name")
        if not isinstance(person_id, str):
            person_id = None
        if not isinstance(display_name, str):
            display_name = None
        now_mono = time.monotonic()
        refusal = self._policy.refusal_reason(
            person_id=person_id,
            display_name=display_name,
            now_monotonic=now_mono,
            now_local_time=datetime.now().astimezone().time(),
        )
        if refusal is not None:
            # Logged at debug: "not_opted_in" is the steady state for most
            # people and must not fill the journal.
            self.greetings_suppressed += 1
            logger.debug("greeting_suppressed", extra={"reason": refusal})
            return
        assert person_id is not None and display_name is not None  # refused above otherwise

        text = self._settings.greeting_template.format(name=display_name)
        async with self._speaking:
            # Re-check the cooldown: something may have spoken while we queued.
            if (
                self._policy.refusal_reason(
                    person_id=person_id,
                    display_name=display_name,
                    now_monotonic=time.monotonic(),
                    now_local_time=datetime.now().astimezone().time(),
                )
                is not None
            ):
                self.greetings_suppressed += 1
                return
            spoke = await self._speaker.speak(text)
            if spoke:
                self.greetings_spoken += 1
                self._policy.record_spoken(person_id, time.monotonic())
                logger.info("greeted", extra={"person_id": person_id})
            else:
                self.greetings_failed += 1

    async def run(self) -> None:
        backend = self._speaker.available_backend()
        logger.info(
            "door_voice_starting",
            extra={
                "enabled": self._settings.enabled,
                "tts_backend": backend,
                "allowed_people": len(self._settings.allowed_person_ids),
            },
        )
        if backend is None:
            # Worth saying loudly once: the Pi ships with only HDMI audio and the
            # 7" touchscreen has no speakers, so this is the expected state until
            # a USB dongle and a TTS binary are present.
            logger.warning("no_tts_backend_installed_service_will_stay_silent")

        delay = self._settings.reconnect_delay_s
        while True:
            try:
                async with websockets.connect(self._settings.door_api_ws_url) as ws:
                    await ws.send(json.dumps({"subscribe": SUBSCRIBE}))
                    self._connected = True
                    logger.info("connected", extra={"url": self._settings.door_api_ws_url})
                    # Reset on a established connection, not on a clean exit: the
                    # signal that door-api is healthy is that it accepted the
                    # subscription. Resetting only on clean exit would make a stream
                    # that ran for hours inherit the backoff from the drop before it.
                    delay = self._settings.reconnect_delay_s
                    async for raw in ws:
                        try:
                            message = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if message.get("type") != "delta":
                            continue
                        event = message.get("event")
                        if isinstance(event, dict):
                            await self.handle_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a reconnect loop must not die
                logger.warning("disconnected", extra={"error": str(exc), "retry_in_s": delay})
            finally:
                self._connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._settings.reconnect_max_delay_s)

    # -- health / metrics ---------------------------------------------------------

    def health(self) -> dict[str, Any]:
        age = (
            None
            if self._last_event_monotonic is None
            else round(time.monotonic() - self._last_event_monotonic, 1)
        )
        return {
            "service": "door-voice",
            "status": "ok" if self._connected else "degraded",
            "detail": None if self._connected else "not subscribed to door-api /ws",
            "connected": self._connected,
            "enabled": self._settings.enabled,
            "tts_backend": self._speaker.available_backend(),
            "last_event_age_s": age,
            "allowed_people": len(self._settings.allowed_person_ids),
        }

    def metrics_text(self) -> str:
        age = (
            -1.0
            if self._last_event_monotonic is None
            else (time.monotonic() - self._last_event_monotonic)
        )
        lines = [
            "# HELP door_voice_connected Subscribed to door-api /ws (1=yes, 0=no)",
            "# TYPE door_voice_connected gauge",
            f"door_voice_connected {1 if self._connected else 0}",
            "",
            "# HELP door_voice_last_event_age_s Seconds since the last identity event (-1: none)",
            "# TYPE door_voice_last_event_age_s gauge",
            f"door_voice_last_event_age_s {age:.1f}",
            "",
            "# HELP door_voice_greetings_spoken_total Greetings spoken aloud",
            "# TYPE door_voice_greetings_spoken_total counter",
            f"door_voice_greetings_spoken_total {self.greetings_spoken}",
            "",
            "# HELP door_voice_greetings_suppressed_total Greetings the policy refused",
            "# TYPE door_voice_greetings_suppressed_total counter",
            f"door_voice_greetings_suppressed_total {self.greetings_suppressed}",
            "",
            "# HELP door_voice_greetings_failed_total Greetings the synthesiser failed to say",
            "# TYPE door_voice_greetings_failed_total counter",
            f"door_voice_greetings_failed_total {self.greetings_failed}",
            "",
            "# HELP door_voice_uptime_s Service uptime in seconds",
            "# TYPE door_voice_uptime_s counter",
            f"door_voice_uptime_s {time.monotonic() - self._started_monotonic:.1f}",
            "",
        ]
        return "\n".join(lines)

    async def serve_http(
        self, *, host: str | None = None, port: int | None = None
    ) -> asyncio.Server:
        """A minimal HTTP/1.0 server for /health and /metrics on loopback.

        asyncio's own streams rather than a framework: this service is deliberately
        tiny and dependency-free, and two GET routes do not need one.
        """
        return await asyncio.start_server(
            self._handle_http,
            self._settings.health_host if host is None else host,
            self._settings.health_port if port is None else port,
        )

    async def _handle_http(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            while True:  # drain headers; nothing here reads them
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line in (b"\r\n", b"\n", b""):
                    break
            parts = request_line.decode("latin-1").split()
            method, target = (parts[0], parts[1]) if len(parts) >= 2 else ("", "")
            path = target.split("?", 1)[0]
            if method != "GET":
                status, content_type, body = 405, "text/plain", b"method not allowed\n"
            elif path == "/health":
                status, content_type = 200, "application/json"
                body = json.dumps(self.health()).encode()
            elif path == "/metrics":
                status, content_type = 200, "text/plain; version=0.0.4; charset=utf-8"
                body = self.metrics_text().encode()
            else:
                status, content_type, body = 404, "text/plain", b"not found\n"
            reason = {200: "OK", 404: "Not Found", 405: "Method Not Allowed"}[status]
            head = (
                f"HTTP/1.0 {status} {reason}\r\n"
                f"Content-Type: {content_type}\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            )
            writer.write(head.encode() + body)
            await writer.drain()
        except Exception:  # noqa: BLE001 - a bad probe must never take the service down
            logger.debug("health_request_failed", exc_info=True)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


async def main_async() -> None:
    service = VoiceService(Settings())
    server: asyncio.Server | None = None
    try:
        server = await service.serve_http()
        logger.info("health_listening", extra={"bind": service._settings.health_bind})
    except OSError as exc:
        # Health is not the job; speaking is. Log and carry on without it.
        logger.warning("health_server_unavailable", extra={"error": str(exc)})
    try:
        with contextlib.suppress(asyncio.CancelledError):
            await service.run()
    finally:
        if server is not None:
            server.close()
            await server.wait_closed()
