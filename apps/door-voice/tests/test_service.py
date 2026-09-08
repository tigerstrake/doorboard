"""Wiring tests: the right events reach the policy, and nothing else speaks."""

from __future__ import annotations

import asyncio

import pytest
from door_voice.app import SUBSCRIBE, VoiceService
from door_voice.settings import Settings
from door_voice.speech import Speaker, sanitize


class FakeSpeaker:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.fail = False

    def available_backend(self) -> str | None:
        return "fake"

    async def speak(self, text: str) -> bool:
        self.said.append(text)
        return not self.fail


def build(**env) -> tuple[VoiceService, FakeSpeaker]:
    settings = Settings(
        FEATURE_VOICE_GREETING=env.get("enabled", True),
        VOICE_GREETING_ALLOW=env.get("allow", "prs_tiger"),
        # Default the window off so these tests don't depend on wall-clock time.
        VOICE_GREETING_QUIET_HOURS=env.get("quiet", ""),
        VOICE_GREETING_COOLDOWN_S=env.get("cooldown", 600.0),
    )
    service = VoiceService(settings)
    speaker = FakeSpeaker()
    service._speaker = speaker  # type: ignore[assignment]
    return service, speaker


def identity(person_id: str = "prs_tiger", name: str | None = "Tiger") -> dict:
    return {
        "type": "vision.identity_stable",
        "payload": {"person_id": person_id, "display_name": name},
    }


@pytest.mark.anyio
async def test_greets_an_opted_in_person():
    service, speaker = build()
    await service.handle_event(identity())
    assert speaker.said == ["Hi Tiger"]


@pytest.mark.anyio
async def test_says_nothing_for_someone_not_opted_in():
    service, speaker = build(allow="")
    await service.handle_event(identity())
    assert speaker.said == []


@pytest.mark.anyio
async def test_says_nothing_when_disabled():
    service, speaker = build(enabled=False)
    await service.handle_event(identity())
    assert speaker.said == []


@pytest.mark.anyio
async def test_cooldown_prevents_a_second_greeting():
    service, speaker = build()
    await service.handle_event(identity())
    await service.handle_event(identity())
    assert speaker.said == ["Hi Tiger"]


@pytest.mark.anyio
async def test_a_failed_speak_does_not_start_the_cooldown():
    """If the speaker was broken, the person was never actually greeted."""
    service, speaker = build()
    speaker.fail = True
    await service.handle_event(identity())
    speaker.fail = False
    await service.handle_event(identity())
    assert speaker.said == ["Hi Tiger", "Hi Tiger"]


@pytest.mark.anyio
async def test_unenrolment_clears_the_cooldown():
    service, speaker = build()
    await service.handle_event(identity())
    await service.handle_event(
        {
            "type": "vision.identity_expired",
            "payload": {"person_id": "prs_tiger", "reason": "admin"},
        }
    )
    await service.handle_event(identity())
    assert speaker.said == ["Hi Tiger", "Hi Tiger"]


@pytest.mark.anyio
async def test_an_ordinary_expiry_does_not_clear_the_cooldown():
    """ADR-0029: "expired" just means the 2.5s face cache lapsed, which happens
    constantly while someone stands at the doorpad. It must not re-arm speech."""
    service, speaker = build()
    await service.handle_event(identity())
    await service.handle_event(
        {
            "type": "vision.identity_expired",
            "payload": {"person_id": "prs_tiger", "reason": "expired"},
        }
    )
    await service.handle_event(identity())
    assert speaker.said == ["Hi Tiger"]


@pytest.mark.anyio
async def test_unrelated_events_are_ignored():
    service, speaker = build()
    for event_type in ("door.button_pressed", "session.state_changed", "media.storage_status"):
        await service.handle_event({"type": event_type, "payload": {}})
    assert speaker.said == []


@pytest.mark.anyio
async def test_concurrent_identities_do_not_talk_over_each_other():
    """Serialised, so two arrivals can't overlap and can't double-greet."""
    service, speaker = build(allow="prs_a,prs_b")
    await asyncio.gather(
        service.handle_event(identity("prs_a", "Ada")),
        service.handle_event(identity("prs_b", "Bo")),
        service.handle_event(identity("prs_a", "Ada")),
    )
    assert sorted(speaker.said) == ["Hi Ada", "Hi Bo"]


def test_only_the_two_identity_events_are_subscribed():
    """A narrower subscription means door-api filters server-side."""
    assert SUBSCRIBE == ["vision.identity_stable", "vision.identity_expired"]


# --- speaker -------------------------------------------------------------


def test_sanitize_strips_control_characters_and_bounds_length():
    assert sanitize("  Hi   Tiger\x07 ") == "Hi Tiger"
    assert len(sanitize("x" * 500)) == 120


def test_no_backend_reports_unavailable_rather_than_raising():
    speaker = Speaker(
        piper_binary="definitely-not-a-real-binary",
        piper_voice="",
        espeak_binary="also-not-real",
        aplay_binary="aplay",
        alsa_device="",
        timeout_s=1.0,
    )
    assert speaker.available_backend() is None


@pytest.mark.anyio
async def test_speaking_with_no_backend_fails_quietly():
    speaker = Speaker(
        piper_binary="definitely-not-a-real-binary",
        piper_voice="",
        espeak_binary="also-not-real",
        aplay_binary="aplay",
        alsa_device="",
        timeout_s=1.0,
    )
    assert await speaker.speak("Hi Tiger") is False


@pytest.mark.anyio
async def test_piper_is_not_chosen_without_a_voice_model():
    """piper on PATH but no --model configured would fail on every call."""
    speaker = Speaker(
        piper_binary="sh",  # exists, stands in for an installed piper
        piper_voice="",
        espeak_binary="also-not-real",
        aplay_binary="aplay",
        alsa_device="",
        timeout_s=1.0,
    )
    assert speaker.available_backend() is None


# --- reconnect pacing -------------------------------------------------------
#
# door-voice retried a dropped door-api /ws at a flat 3s forever, the one outlier
# against the bounded backoff every other consumer uses.


class _Stop(Exception):
    """Breaks out of run()'s infinite loop from the patched sleep."""


class FakeWS:
    """Connects, accepts the subscribe, then drops."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def __aenter__(self) -> FakeWS:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def send(self, data: str) -> None:
        self.sent.append(data)

    def __aiter__(self) -> FakeWS:
        return self

    async def __anext__(self) -> str:
        raise ConnectionError("stream dropped")


def _patch_loop(monkeypatch, connect, *, stop_after: int) -> list[float]:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) >= stop_after:
            raise _Stop

    monkeypatch.setattr("door_voice.app.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("door_voice.app.websockets.connect", connect)
    return delays


@pytest.mark.anyio
async def test_reconnect_backoff_doubles_up_to_the_ceiling(monkeypatch):
    """A door-api that stays down is not retried at a fixed rate forever."""
    service, _ = build()

    def refuse(url: str) -> FakeWS:
        raise OSError("door-api is down")

    delays = _patch_loop(monkeypatch, refuse, stop_after=8)
    with pytest.raises(_Stop):
        await service.run()

    assert delays == [3.0, 6.0, 12.0, 24.0, 30.0, 30.0, 30.0, 30.0]


@pytest.mark.anyio
async def test_an_established_connection_resets_the_backoff(monkeypatch):
    """Reset on connect, not on clean exit.

    A stream that ran for hours and then dropped must reconnect promptly rather
    than inherit the backoff from whatever outage preceded it.
    """
    service, _ = build()
    attempts: list[str] = []

    def flaky(url: str) -> FakeWS:
        attempts.append(url)
        if len(attempts) <= 2:
            raise OSError("door-api is down")
        return FakeWS()

    delays = _patch_loop(monkeypatch, flaky, stop_after=4)
    with pytest.raises(_Stop):
        await service.run()

    # 3, 6 while refused; then each accepted subscription resets it to 3.
    assert delays == [3.0, 6.0, 3.0, 3.0]


def test_a_ceiling_below_the_first_delay_is_rejected():
    """Otherwise the cap silently shortens the first delay instead of bounding it."""
    with pytest.raises(ValueError, match="VOICE_RECONNECT_MAX_DELAY_S"):
        Settings(VOICE_RECONNECT_DELAY_S=10.0, VOICE_RECONNECT_MAX_DELAY_S=5.0)


def test_an_equal_ceiling_is_allowed_and_gives_a_flat_retry():
    """The old flat-3s behaviour stays reachable for anyone who wants it."""
    settings = Settings(VOICE_RECONNECT_DELAY_S=3.0, VOICE_RECONNECT_MAX_DELAY_S=3.0)
    assert settings.reconnect_max_delay_s == settings.reconnect_delay_s
