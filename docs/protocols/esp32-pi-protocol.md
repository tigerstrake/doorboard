# ESP32 ↔ Pi wire protocol (normative)

Transport per ADR-0006: UART primary (115200 8N1 to start; may be raised after bench testing), UDP fallback, `mock` for dev/CI. Implemented by T-102 (Pi side) and T-101 (ESP32 side).

## Framing

Newline-delimited JSON (one message per `\n`-terminated line, UTF-8, max 512 bytes). Malformed lines are dropped and counted (`rx_errors` metric) — never crash, never block.

```json
{"v": 1, "seq": 42, "t": "profile_update", "ack": null, "p": { ... }}
```

| Field | Meaning |
|---|---|
| `v` | protocol version; receiver rejects unknown major versions and reports via health |
| `seq` | sender-scoped monotonically increasing sequence number |
| `t` | message type (below) |
| `ack` | for `t:"ack"`: the `seq` being acknowledged |
| `p` | payload |

## Message types

| `t` | Direction | Ack required | Payload |
|---|---|---|---|
| `hello` | both | yes | `{fw_version\|sw_version, proto_v: 1, boot_id}` — sent on (re)connect |
| `heartbeat` | both, 1 Hz | no | `{uptime_s, fallback_active}` (ESP32 side adds `cached_profile_id`) |
| `profile_update` | Pi → ESP32 | yes | `{profile_id, ttl_ms, priority}` — ESP32 stores with local monotonic expiry `now + ttl_ms` |
| `profile_clear` | Pi → ESP32 | yes | `{reason}` |
| `effect_play` | Pi → ESP32 | yes | `{effect_id, duration_ms}` |
| `button_event` | ESP32 → Pi | yes | `{press_id, pressed_at_mono_ms, had_cached_profile, profile_id}` |
| `knock_event` | ESP32 → Pi | yes | `{pattern_id, confidence}` |
| `contact_event` | ESP32 → Pi | yes | `{state}` |
| `ack` | both | — | `{}` (references `seq` via `ack` field) |

Notes:
- TTLs, not absolute timestamps, cross the wire — the two devices never share a clock. Each side applies TTLs to its own monotonic clock.
- State-bearing messages are retransmitted (max 3×, 50 ms spacing) until acked; receivers dedupe by `(boot_id, seq)`.
- Profile IDs reference LED/audio profiles pre-flashed or pre-synced to the ESP32; the wire never carries names or personal data (ESP32 is trust-level *low*).

## Timing and fallback requirements

| Condition | ESP32 behavior |
|---|---|
| Button press, any time | Generic LED/audio feedback < 30 ms, purely local, before any transmission |
| Button press, valid cached profile | Personalized effect < 100 ms total |
| No heartbeat from Pi > 5 s | Enter fallback: clear stale profile at expiry, show fallback animation on press, mark `fallback_active` |
| Pi returns | `hello` handshake, resume normal operation |
| Own watchdog trip | Reboot to fallback state; button still works after boot |

The Pi side (door-api/door-visiond) mirrors: ESP32 heartbeat loss > 5 s ⇒ surface admin error and never fake a physical effect in the UI.

## Pi-side adapter interface

```
Esp32Transport (uart | udp | mock)
  send(msg) -> awaitable ack/timeout
  events() -> async iterator of inbound messages
  status() -> {connected, last_heartbeat_mono_ms, rx_errors, tx_retries}
```

Inbound wire messages are translated to `door.*` contract events (see events.md) exactly once, at this boundary.
