# Bill of materials

From handoff §4. Validate exact part availability/versions at purchase time.

## Door appliance

| Item | Spec | Notes |
|---|---|---|
| Raspberry Pi 5 | 8 GB preferred | dual HDMI, 2× MIPI, USB 3, GbE, PCIe for Hailo |
| Power supply | official 27 W (5 V/5 A) or equivalent | non-negotiable under Hailo+camera load |
| Cooling | active cooler; enclosure fan if testing demands | required under camera/Hailo/browser load |
| AI accelerator | Raspberry Pi AI HAT+ **26 TOPS** (Hailo-8) | 13 TOPS insufficient headroom; 40 TOPS AI HAT+ 2 not selected (ADR-scope: generative workloads, unproven pipeline) |
| microSD | 128 GB | OS/recovery only (ADR-0007) |
| SSD | USB 3 SATA, ≥256 GB (512 GB preferred) | active recordings, SQLite, enrollment data |
| Visitor camera | Camera Module 3 **Wide NoIR** | wide framing, video messages, IR-illuminator upgrade path |
| Recognition camera | Camera Module 3 **Standard** | more facial pixels; chest/face height; aimed at 0.8–1.8 m standing zone |
| Wallboard display | 21.5–24" used/refurb 1080p IPS, VESA | HDMI-1 |
| DoorPad display | 7" HDMI capacitive touch, ~1024×600 | HDMI-2 + USB touch (no DSI — both MIPI links are cameras) |
| Door controller | ESP32-S3 dev board | UART to Pi |
| LED | WS2812B ring/strip | own 5 V rail, common ground |
| Audio out | class-D amp + small speaker | own rail |
| Microphone | USB UAC (or I2S) | one-way video-message audio in v1 |
| Knock sensor | piezo/accelerometer on door | local pattern matching on ESP32 |
| CO2/temp/humidity | SCD40/SCD41 (I2C) | room side |
| Optional | reed switch, NFC reader, ambient light, PM2.5, VOC | feature-flagged |

## Constraints (binding)

- AI HAT+ consumes the PCIe lane → no NVMe HAT; SSD is USB 3.
- Both MIPI links are cameras; both HDMI ports are displays; touchscreen is HDMI+USB.
- No analog sensors without an ADC plan; avoid hallway PIR/mmWave (consent).
- Displays, LEDs, amp: separate regulated rails, common ground — never Pi GPIO power.

## Other hosts

Intel NUC (control plane), Pi 4 + USB mic (BirdNET), optional Pi 3/4 + RTL-SDR (ADS-B, deferred), NAS, over-door mounting hardware (see door-assembly.md).
