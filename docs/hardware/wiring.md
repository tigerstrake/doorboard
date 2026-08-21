# Wiring and power

From handoff §3.3/§4. The rules are binding. Pin numbers below are generated from
[`doorboard_pinout.h`](../../firmware/esp32-door-controller/main/include/doorboard_pinout.h)
— that header is the source of truth, and if this document ever disagrees with it,
the header wins and this file is the bug.

## Read this before you strip a wire

Not everything on the shopping list has software behind it yet. Wiring a device
that nothing reads is the most demoralising way to spend an evening, so:

| Device | Status | Wire it now? |
|---|---|---|
| Bell button | Implemented — ISR, 40 ms debounce, emits `door.button_pressed` | **Yes** |
| WS2812B LED ring (16) | Implemented — RMT driver, effects catalogue | **Yes** |
| Speaker via class-D amp | Implemented — I2S 16-bit mono @ 22.05 kHz | **Yes** |
| Pi ↔ ESP32 link | Implemented — UART1 @ 115200, framed protocol | **Yes** |
| Cameras (×2), displays (×2), USB mic | Implemented on the Pi side | **Yes** |
| **Piezo knock sensor** | **Stub only.** The sensors task has `threshold_crossed = false` hardcoded and never reads the ADC; `CONFIG_DOORBOARD_ENABLE_KNOCK_DETECTION` defaults off | Wire it if you like, but expect silence |
| **SCD41 CO₂/temp/humidity** | **No code at all.** No I2C driver, no service, no event | Not yet |
| **Reed switch** | `door.contact_changed` exists in `packages/contracts`, but nothing produces it | Not yet |
| NFC, ambient light, PM2.5, VOC, IR illuminator | Shopping-list options, no code | Not yet |

## Rails

| Rail | Powers | Source |
|---|---|---|
| 5 V / 5 A (official PSU) | Pi 5 + AI HAT+ + SSD | dedicated |
| 5 V regulated (separate) | WS2812B LEDs | own supply/buck; **never Pi GPIO 5 V** |
| 5 V regulated (separate) | class-D amp + speaker | own supply/buck |
| Display power | both monitors | their own supplies/12 V line, not the Pi |

Common ground between Pi, ESP32, LED rail, and amp rail. One protected power feed +
one Ethernet cross the hinge in the strain-relief loop; everything else is
frame-local.

16 WS2812B at full white is roughly 16 × 60 mA ≈ **1 A**, and the effects catalogue
can drive them bright. Size the LED buck for that peak even though typical draw is
far lower — a sagging LED rail browns out the ESP32 next to it.

## ESP32-S3 connections

Every pin below comes from `doorboard_pinout.h`. Nothing else on the ESP32 is used
by the firmware, so remaining GPIOs are free for future sensors.

| Peripheral | ESP32-S3 pin | Wiring detail |
|---|---|---|
| Bell button | **GPIO 4** | Switch between GPIO 4 and **GND**. Configured input with **internal pull-up**, negative-edge interrupt — so it is **active LOW** and needs *no* external pull-up. Add the RC debounce anyway (e.g. 10 kΩ series + 100 nF to GND); firmware debounces 40 ms on top. |
| WS2812B data | **GPIO 18** | Single data line, WS2812 timing, GRB order, non-inverted, 16 LEDs. 3.3 V logic into a 5 V-powered strip is marginal — use a level shifter, or the common trick of a 330 Ω series resistor and keeping the run short. LED **+5 V and GND go to their own buck**, ground tied to the ESP32's. |
| Amp — bit clock | **GPIO 16** | I2S standard mode, MSB-first slot |
| Amp — word select | **GPIO 17** | Also called LRCLK / LRC |
| Amp — data out | **GPIO 15** | DIN on most amp boards. 16-bit **mono** @ 22.05 kHz |
| Amp — MCLK | *not used* | Leave unconnected; the firmware sets `I2S_GPIO_UNUSED`. If your amp board requires MCLK, it is the wrong board for this firmware. |
| UART → Pi | **GPIO 43 (TX)** | To Pi **RX**. UART1, 115200 8N1. |
| UART ← Pi | **GPIO 44 (RX)** | From Pi **TX**. Cross them — TX to RX, RX to TX. |
| Piezo (future) | **GPIO 7** (ADC1 ch 6) | Not read by any code yet. Piezo disc through a clamp/divider network so the ADC never sees more than 3.3 V. |

Both boards run 3.3 V logic, so the UART needs no level shifting — but confirm your
ESP32-S3 board exposes GPIO 43/44 as pads. On many S3 dev boards those are the
**USB-serial-JTAG** pins, which is exactly why the firmware puts the Pi link on
UART1 rather than UART0: flashing over USB and talking to the Pi can then coexist.
If your board hard-wires 43/44 to the USB bridge, move the link to two free GPIOs
and update the header — do not fight the board.

## Pi 5 connections

| Link | Connection |
|---|---|
| Recognition camera (Std) | MIPI CSI port 0 |
| Visitor camera (Wide NoIR) | MIPI CSI port 1 |
| Wallboard monitor | HDMI 0 |
| DoorPad touchscreen | HDMI 1, touch back over USB |
| Microphone | USB (UAC) |
| ESP32 link | UART on the 40-pin header — Pi TX → ESP32 GPIO 44, Pi RX ← ESP32 GPIO 43, plus common GND |
| SSD | USB 3 (blue port) — the AI HAT+ takes the only PCIe lane, so no NVMe |
| SCD41 (when implemented) | I2C on the 40-pin header |

Both CSI ports and both HDMI ports are consumed, which is why the DoorPad must be
HDMI + USB touch rather than DSI.

## Rules

- Fuse/protect the power feed at the room side.
- No high-current device on Pi GPIO, period. LEDs and the amp get their own rails.
- Tie all grounds together at one point. A missing common ground between the ESP32
  and an LED or amp rail is the single most common cause of "the LEDs flicker and
  the board resets".
- Cable loop must survive full door swing cycles without strain (test before final
  install).
- Label both ends of every cable — this assembly gets removed and reinstalled.

## Bring-up order

Wire and prove one thing at a time; a harness built all at once is a harness
debugged all at once.

1. **ESP32 alone, USB-powered.** Flash it, confirm it boots and logs.
2. **Button.** Press it and watch for `button_event` frames on UART1. Nothing else
   need be connected.
3. **UART to the Pi.** Confirm door-api receives `door.button_pressed` and the
   session machine reaches `VISITOR_MODE`. This is the critical path — get it solid
   before anything decorative.
4. **LEDs on their own rail.** Watch for brownouts under a white-flash effect.
5. **Amp and speaker.** Check for I2S hiss with no signal before assuming a bad amp.
6. **Pi peripherals** — cameras, displays, mic — each verified independently.
7. **Everything in the enclosure**, then re-run the whole chain, because dressing
   cables is itself a way to break them.

Steps 1–3 give you a working doorbell. Everything after that is enhancement, and
the architecture is deliberately built so a failure in 4–7 cannot stop 1–3.
