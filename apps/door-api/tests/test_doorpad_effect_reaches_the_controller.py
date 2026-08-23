"""The DoorPad ring has to actually reach the LEDs.

Both bugs pinned here were silent: `play_doorpad_effect` returned
``{"status": "sent"}`` in each case, so every log line and metric said the door had
given feedback while the hardware sat dark. ARCHITECTURE.md §10 is explicit that the
Pi must never pretend a physical effect occurred, and "the transport acked it" is not
evidence that it did.

**Sequence numbers.** The outbound message was hand-built with ``seq=0`` every time.
The controller dedupes inbound frames by ``(boot_id, seq)`` in a 16-entry ring and
acks duplicates *before* discarding them, so the first ring after boot played and
every ring after that was thrown away — the common case being a visitor tapping
twice. Only the transport can allocate a correct `seq`, so the message must come from
its factory.

**Effect id.** The configured default named an effect the firmware does not have.
`door_effect_from_name` returns ``DOOR_EFFECT_NONE`` for an unknown name and
`on_effect_play_received` drops the message, so the ring produced nothing at all.
The name is checked against the firmware's own table rather than a copy of it.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

from doorboard_contracts.events import DoorboardEvent
from doorboard_esp32_link import Esp32TransportStatus, WireMessage

# Importing door_api.app builds the module-level state, which needs a database
# before it can be constructed. Same preamble as test_esp32_link.py.
os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import state  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
EFFECTS_C = (
    REPO_ROOT
    / "firmware"
    / "esp32-door-controller"
    / "components"
    / "door_effects"
    / "door_effects.c"
)


class _RecordingTransport:
    """A transport that allocates sequence numbers the way the real one does."""

    def __init__(self) -> None:
        self.sent: list[WireMessage] = []
        self._seq = 0

    def make_message(self, message_type: str, payload: Mapping[str, object]) -> WireMessage:
        self._seq += 1
        return WireMessage(v=1, seq=self._seq, message_type=message_type, ack=None, payload=payload)

    async def send(self, msg: WireMessage) -> WireMessage:
        self.sent.append(msg)
        return WireMessage(v=1, seq=0, message_type="ack", ack=msg.seq, payload={})

    def events(self) -> AsyncIterator[DoorboardEvent]:
        raise NotImplementedError

    def status(self) -> Esp32TransportStatus:
        return Esp32TransportStatus(
            connected=True, last_heartbeat_mono_ms=None, rx_errors=0, tx_retries=0
        )


def _firmware_effect_names() -> set[str]:
    """The names `door_effect_from_name` actually accepts, read from the firmware."""
    source = EFFECTS_C.read_text(encoding="utf-8")
    body = source.split("door_effect_id_t door_effect_from_name", 1)[1].split("\n}", 1)[0]
    return set(re.findall(r'strcmp\(name,\s*"([^"]+)"\)', body))


def test_repeated_rings_get_fresh_sequence_numbers() -> None:
    """A double-tap must reach the controller twice, not be deduped into one."""
    transport = _RecordingTransport()
    state.attach_esp32_transport(transport)
    try:
        results = asyncio.run(_ring_twice())
    finally:
        state.detach_esp32_transport()

    assert results == [{"status": "sent"}, {"status": "sent"}]
    assert [msg.message_type for msg in transport.sent] == ["effect_play", "effect_play"]

    seqs = [msg.seq for msg in transport.sent]
    assert len(set(seqs)) == 2, f"repeated rings reused a sequence number: {seqs}"
    assert seqs[1] > seqs[0], f"sequence numbers must advance, got {seqs}"
    assert 0 not in seqs, "seq 0 is the hand-built sentinel this test exists to prevent"


async def _ring_twice() -> list[dict[str, str]]:
    return [await state.play_doorpad_effect(), await state.play_doorpad_effect()]


def test_configured_doorpad_effect_exists_in_the_firmware() -> None:
    """An effect id the firmware cannot resolve is acked and then silently dropped."""
    names = _firmware_effect_names()
    assert "generic_press" in names, "sanity: the firmware table was parsed"
    assert state.config.doorpad_effect_id in names, (
        f"doorpad_effect_id {state.config.doorpad_effect_id!r} is not a firmware effect; "
        f"door_effect_from_name would return DOOR_EFFECT_NONE. Known: {sorted(names)}"
    )
