# Wiring and power

From handoff §3.3/§4. Binding rules; exact harness drawings added during M1 hardware bring-up.

## Rails

| Rail | Powers | Source |
|---|---|---|
| 5 V / 5 A (official PSU) | Pi 5 + AI HAT+ + SSD | dedicated |
| 5 V regulated (separate) | WS2812B LEDs | own supply/buck; **never Pi GPIO 5 V** |
| 5 V regulated (separate) | class-D amp + speaker | own supply/buck |
| Display power | both monitors | their own supplies/12 V line, not the Pi |

Common ground between Pi, ESP32, LED rail, and amp rail. One protected power feed + one Ethernet cross the hinge in the strain-relief loop; everything else is frame-local.

## Signal map

| Link | Connection |
|---|---|
| Pi ↔ ESP32 | UART (level-appropriate, 3.3 V) — see protocol doc |
| ESP32 → LEDs | single data GPIO (level-shift if needed) |
| ESP32 → amp | I2S/DAC per firmware brief |
| ESP32 ← button | GPIO, hardware debounce RC + firmware debounce |
| ESP32 ← piezo | conditioned input per firmware brief |
| Pi ← cameras | 2× MIPI CSI (both links used — no DSI display possible) |
| Pi → displays | 2× HDMI; touch back over USB |
| Pi ← microphone | USB (UAC) |
| Pi ← SCD41 | I2C |

## Rules

- Fuse/protect the power feed at the room side.
- No high-current device on Pi GPIO, period.
- Cable loop must survive full door swing cycles without strain (test before final install).
- Label both ends of every cable — this assembly gets removed and reinstalled.
