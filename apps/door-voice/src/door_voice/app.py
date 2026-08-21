"""Consume door-api's broadcast and speak recognised names (ADR-0034).

Deliberately a *separate process* from door-api. door-api owns
`button -> ESP32 feedback -> local UI`, and CLAUDE.md §1 says nothing new may sit
on that path — so speech synthesis, which spawns subprocesses and can wedge on a
missing audio device, lives out here where failing costs nothing.

This service only ever reads. It holds no credentials, writes no files, and makes
no call off the Pi.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime

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

    async def handle_event(self, event: dict) -> None:
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
            logger.debug("greeting_suppressed", extra={"reason": refusal})
            return

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
                return
            spoke = await self._speaker.speak(text)
            if spoke:
                self._policy.record_spoken(person_id, time.monotonic())
                logger.info("greeted", extra={"person_id": person_id})

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

        while True:
            try:
                async with websockets.connect(self._settings.door_api_ws_url) as ws:
                    await ws.send(json.dumps({"subscribe": SUBSCRIBE}))
                    logger.info("connected", extra={"url": self._settings.door_api_ws_url})
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
                logger.warning("disconnected", extra={"error": str(exc)})
            await asyncio.sleep(self._settings.reconnect_delay_s)


async def main_async() -> None:
    service = VoiceService(Settings())
    with contextlib.suppress(asyncio.CancelledError):
        await service.run()
