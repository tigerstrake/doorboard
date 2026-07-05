# esp32-door-controller — real-time physical I/O firmware

**Plane:** real-time door (the hardest real-time tier) · **Target:** ESP32-S3, ESP-IDF/FreeRTOS (ADR-0003) · **Tasks:** T-101, T-102 (protocol), T-104 (latency verification)

The component that makes the door feel instant. Trust level: **low** — it holds no secrets, no names, only opaque profile IDs and pre-flashed effects.

## Hardware attached

Bell button (debounced GPIO), WS2812B LED ring, speaker via class-D amp (I2S/DAC), piezo/accelerometer for secret-knock detection, optional: door reed switch, NFC reader, ambient light sensor. Power: LEDs/amp on their own regulated rail, common ground — never from Pi GPIO.

## Behavior requirements

| Requirement | Detail |
|---|---|
| Generic feedback | Button press → LED+sound < **30 ms**, purely local, before any transmission (ISR-driven, no network/UART wait) |
| Personalized feedback | Valid cached profile → personalized effect < **100 ms** total |
| Profile cache | Single active profile: `{profile_id, expiry on local monotonic clock}` pushed by Pi (TTL-based, ADR-0006); expired = generic |
| Knock detection | On-device pattern matching (piezo), emits `knock_event` — no hallway audio streaming, ever |
| Fallback | Pi heartbeat lost > 5 s → fallback animation/unavailable state; button keeps giving generic feedback |
| Watchdog | Hardware watchdog; reboot lands in fallback state with working button |
| Telemetry | 1 Hz heartbeat with uptime, fw version, cached profile, fallback flag |

## Architecture notes

- FreeRTOS tasks: `input` (highest priority, ISR + debounce), `effects` (LED/audio rendering), `link` (UART protocol, acks/retransmit, heartbeats), `sensors` (knock/reed/light).
- Effects are a pre-flashed table keyed by `effect_id`/`profile_id`; the wire never carries animation data in v1.
- Wire protocol: [docs/protocols/esp32-pi-protocol.md](../../docs/protocols/esp32-pi-protocol.md). Protocol logic must be host-testable (pure C module + host-side unit tests) — CI has no device; hardware-in-loop tests live in `tests/hardware-in-loop/`.

## T-101 implementation

Pinned build toolchain: ESP-IDF `v5.3.2`.

```sh
cd firmware/esp32-door-controller
idf.py set-target esp32s3
idf.py build
```

The project skeleton is intentionally split along the required four tasks:

| Task | Responsibility |
|---|---|
| `input` | Button GPIO ISR, firmware debounce, immediate local `generic_press` effect scheduling, then link event enqueue |
| `effects` | Placeholder `generic_press`, `fallback`, `blue_wave`, and `green_pulse` physical effects |
| `link` | UART framing, ack/retry, inbound profile/effect handling, outbound button/knock/heartbeat frames |
| `sensors` | Piezo threshold stub behind `CONFIG_DOORBOARD_ENABLE_KNOCK_DETECTION` |

The wire protocol engine lives in `components/door_protocol`. It is pure C and includes only standard library headers, so host-side tests compile it directly without ESP-IDF or FreeRTOS. The module owns:

- newline-delimited JSON framing with a 512-byte maximum;
- protocol version rejection and `rx_errors`;
- ack generation for state-bearing Pi messages;
- outbound retry state, 3 retries at 50 ms spacing;
- `(boot_id, seq)` dedupe for inbound Pi messages;
- single cached profile with local monotonic expiry;
- heartbeat-loss fallback after 5 seconds.

The firmware starts in fallback state after boot. Button feedback is local and is scheduled before any UART frame is queued, preserving the critical path when the Pi, NUC, NAS, or network is unavailable.

Pin assignments are centralized in `main/include/doorboard_pinout.h`. Bench bring-up may adjust that header when the exact ESP32-S3 board is selected.
