# ESP32 LED and Audio Effects Catalog

This document details the pre-flashed effects library in the ESP32 firmware (`esp32-door-controller`). The wire protocol never carries raw animation or audio sample data; instead, it transmits string identifiers (e.g. `effect_id` or `profile_id`), which are translated on-device to the pre-compiled sequences below.

## Memory Budget (Audio Tone Sequences)

Rather than compiling heavy PCM audio samples into the flash, audio cues are represented as compact **frequency/duration tone sequences**. 

* **Tone representation:** 8 bytes per tone (`uint32_t frequency_hz` + `uint32_t duration_ms`).
* **Storage allocation:** Maximum of 16 tones per cue.
* **Flash usage:** 11 active effects with statically declared tone sequences occupy less than **700 bytes** of flash memory, comfortably within the ESP32-S3's partition limits.

---

## Effects Catalog Table

| Effect ID | Intended Use | Duration | LED Pattern | Audio Cue |
|---|---|---|---|---|
| `generic_press` | Immediate tactile confirmation when the button is pressed (Pi offline or cache empty) | 300 ms (10 ticks) | All pixels start at white (255, 255, 255) and fade out linearly. | 1-tone chime (880 Hz for 80 ms) |
| `fallback` | Alert indicating degraded status when the Pi heartbeat is lost (> 5 s) | 600 ms (20 ticks) | Dull orange (R=128, G=32, B=0) pulsing in intensity. | 2-tone alert (220 Hz for 150 ms, then 180 Hz for 150 ms) |
| `boot` | Successful startup signal upon ESP32 reboot / initialization | 900 ms (30 ticks) | A rotating cyan dot (R=0, G=192, B=192) that leaves a trail, then fades out. | Ascending 3-tone chime (523 Hz for 100 ms, 659 Hz for 100 ms, 784 Hz for 200 ms) |
| `privacy_mode` | Visual confirmation when privacy mode is toggled (face recognition disabled) | 1200 ms (40 ticks) | Magenta (R=128, G=0, B=128) breathing animation. | Descending 2-tone chime (587 Hz for 150 ms, 440 Hz for 200 ms) |
| `error_admin` | Indication of admin errors or system warnings | 600 ms (20 ticks) | Flashing bright red (R=255, G=0, B=0) alternating every 150 ms. | Harsh double-beep (330 Hz for 100 ms, silence for 50 ms, 330 Hz for 100 ms) |
| `blue_wave` | (Profile 1) Wave-like motion for user profile recognition feedback | 900 ms (30 ticks) | A bright blue (R=0, G=0, B=255) peak rotating around the ring with a tail. | Ascending wave arpeggio (440 Hz, 554 Hz, 659 Hz, 880 Hz) |
| `green_pulse` | (Profile 2) Pulse motion for user profile recognition feedback | 600 ms (20 ticks) | All green (R=0, G=255, B=0) pulsing twice in intensity. | Rapid dual beep (988 Hz for 60 ms, silence for 40 ms, 988 Hz for 80 ms) |
| `sunrise` | (Profile 3) Slower warm greeting theme | 1200 ms (40 ticks) | Red (bottom) rising to orange and yellow (top), mimicking a sunrise. | Warm rising arpeggio (349 Hz, 440 Hz, 523 Hz, 587 Hz) |
| `mint_pulse` | (Profile 4) Clean, modern pulse theme | 600 ms (20 ticks) | Cool mint green (R=30, G=255, B=150) pulsing twice in intensity. | Crisp beep-boop (880 Hz for 80 ms, 1047 Hz for 120 ms) |
| `rainbow` | (Profile 5) Celebratory rainbow greeting theme | 1500 ms (50 ticks) | Rotating full rainbow spectrum around the 16 LEDs. | Fast arpeggio (262 Hz, 330 Hz, 392 Hz, 523 Hz, 659 Hz, 784 Hz) |
| `sparkle` | (Profile 6) Playful greeting theme | 900 ms (30 ticks) | Random pixels flashing gold/white (R=255, G=220, B=120) with decay. | Playful twin chime (1175 Hz for 50 ms, 1318 Hz for 50 ms, 1568 Hz for 100 ms) |
