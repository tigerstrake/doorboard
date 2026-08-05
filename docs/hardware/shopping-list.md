# Doorboard shopping list

Compiled from `docs/hardware/bom.md`, `wiring.md`, `door-assembly.md`, and the architecture handoff. Assumes you already have the NUC, NAS, and spare Pi(s) — see "Verify against what you already own" at the end. Validate exact SKUs/availability at purchase time.

## 1. Door Pi compute

| Item | Spec | Qty |
|---|---|---|
| Raspberry Pi 5 | 8 GB RAM preferred | 1 |
| Official Pi 5 power supply | 27 W, 5 V/5 A USB-C | 1 |
| Active cooler | Official Pi 5 Active Cooler (or equivalent) | 1 |
| Case/enclosure fan | if thermal testing shows it's needed | 1 (contingency) |
| microSD card | 128 GB, A2/U3 rated | 1 |
| USB 3 SSD | SATA, ≥256 GB (512 GB preferred), USB 3 enclosure/cable | 1 |
| Raspberry Pi AI HAT+ 26 TOPS (Hailo-8) | not the 13 TOPS or "AI HAT+ 2" 40 TOPS variant | 1 |

## 2. Cameras

| Item | Spec | Qty |
|---|---|---|
| Camera Module 3 Wide NoIR | visitor camera | 1 |
| Camera Module 3 Standard | recognition camera | 1 |
| CSI ribbon cables | correct length/pitch for Pi 5 (22-pin), 2 different runs (frame-local, so measure your enclosure-to-camera distance) | 2 |

## 3. Displays

| Item | Spec | Qty |
|---|---|---|
| Wallboard monitor | 21.5–24" used/refurb 1080p IPS, VESA mount holes | 1 |
| VESA mounting plate/arm | matched to monitor's VESA pattern | 1 |
| DoorPad touchscreen | 7", HDMI input + USB touch, ~1024×600 | 1 |
| HDMI cables | 2 (one per display, length to enclosure) | 2 |
| USB cable (touch) | for DoorPad touch controller back to Pi | 1 |
| 12 V (or per-monitor spec) power supply | one per monitor unless it has an internal PSU | as needed |

## 4. ESP32 door controller and peripherals

| Item | Spec | Qty |
|---|---|---|
| ESP32-S3 dev board | with UART broken out | 1 |
| Physical bell button | momentary, panel-mount | 1 |
| WS2812B LED ring or strip | count per `effects-catalog.md` (16-LED ring referenced) | 1 |
| Class-D audio amplifier board | small, I2S or DAC input per firmware | 1 |
| Small speaker | matched to amp | 1 |
| USB UAC microphone (or I2S mic breakout) | one-way visitor audio | 1 |
| Piezo disc or accelerometer module | knock detection | 1 |
| RC debounce components (resistor/capacitor) | for button hardware debounce | a few |
| Logic-level shifter | if LED/amp signaling needs 3.3→5 V shift | 1–2 |

## 5. Sensors

| Item | Spec | Qty | Optional? |
|---|---|---|---|---|
| SCD40 or SCD41 | CO2/temp/humidity, I2C | 1 | Core |
| Door reed switch | magnetic contact | 1 | Optional |
| NFC reader module | I2C/SPI, ESP32-compatible | 1 | Optional |
| Ambient light sensor | I2C (e.g. BH1750-class) | 1 | Optional |
| PM2.5 sensor | digital/UART output (e.g. PMS-class) | 1 | Optional |
| VOC sensor | digital/I2C output | 1 | Optional |
| IR illuminator | for visitor camera low-light upgrade path | 1 | Optional |
| RTL-SDR USB dongle + antenna | ADS-B/aircraft reception (deferred feature; only if not using API-based aircraft data) | 1 | Optional |

## 6. Power and wiring

| Item | Spec | Qty |
|---|---|---|
| Regulated 5 V buck converter #1 | dedicated rail for WS2812B LEDs, sized to LED count's peak draw — never Pi GPIO | 1 |
| Regulated 5 V buck converter #2 | dedicated rail for amp + speaker | 1 |
| Inline fuse/protection | at the room-side power feed | 1 |
| Low-voltage DC power cable (or PoE, if you go that route) | one run, room → door assembly, sized for the hinge-side flex loop | 1 |
| Ethernet cable | one run, room → door assembly, same flex loop | 1 |
| Cable strain-relief / flexible conduit | for the hinge-side loop, rated for repeated door-swing flex | as needed |
| Common ground bus/terminal block | ties Pi, ESP32, LED rail, amp rail grounds together | 1 |
| Cable labels | both ends of every cable (assembly is removable/reinstallable) | 1 pack |
| Assorted JST/Dupont connectors, heat-shrink, wire | for LED, amp, sensor, and button harnesses | as needed |

## 7. Mechanical / mounting

| Item | Spec | Qty |
|---|---|---|
| 2020 or 2040 aluminum extrusion | frame rails, length per your door width/height | as needed |
| Extrusion corner brackets/fasteners | matched to extrusion profile | as needed |
| Facing panel | aluminum composite, thin plywood, or 3D-printed panels | 1 set |
| Over-door hooks (padded) | two, load-rated for full assembly weight | 2 |
| Inside-door load-spreader/tension bar | spans door width, distributes hook load | 1 |
| Cam-buckle straps | stabilize assembly against the door | 2+ |
| Felt/EVA padding | every door contact point, non-marking, removable-safe adhesive | as needed |
| Lockable enclosure/box | ventilated, houses Pi 5 + HAT + SSD + ESP32 + power distribution behind the front panel | 1 |
| Enclosure lock/latch | keeps USB/microSD physically inaccessible from the hallway | 1 |
| Visible camera/notice signage | printed label or engraved plate, privacy requirement | 1 |
| Mounting screws/standoffs | for Pi, HAT, SSD, ESP32, button, LED ring, speaker, cameras inside the enclosure | assorted |

## 8. Tools (if not already owned)

- Wire strippers/crimpers for Dupont/JST connectors
- Soldering iron + solder (for amp/speaker/LED leads)
- Multimeter
- Heat gun or lighter (heat-shrink)
- Label maker or cable tags
- Basic hand tools for extrusion assembly (Allen keys, screwdrivers)
- Scale or weights for the dead-weight structural test before electronics go in

## Verify against what you already own

Since you're reusing existing hardware, confirm these before wiring anything up:

- **NUC**: needs enough headroom to run Home Assistant + Mosquitto + PostgreSQL + control-plane-api + wallboard-worker + Caddy as Docker Compose services, plus a wired Ethernet link to the same network segment as the door Pi.
- **NAS**: needs a network share/API reachable from the NUC (and, for uploads, from the door Pi via a limited service credential) with enough free space for durable clip/photo archives.
- **Spare Pi(s)**: confirm at least one has a free USB port + nearby power for the BirdNET-Go microphone, and (if doing ADS-B) a free USB port for the RTL-SDR dongle on the second one.

## Notes

- AI HAT+ occupies the Pi 5's only PCIe lane — no NVMe HAT alongside it; SSD must be USB 3.
- Both MIPI camera ports are used and both HDMI ports are used, so the DoorPad touchscreen must be HDMI+USB, not DSI.
- Displays, LEDs, and the amp each need their own regulated supply — nothing high-current runs off Pi GPIO power.
