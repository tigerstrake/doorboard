# T-101 hardware-in-loop procedure

Maintainer bench captures are required for final acceptance. CI covers the host-side protocol conformance only.

## Equipment

- ESP32-S3 board flashed from `firmware/esp32-door-controller` with ESP-IDF `v5.3.2`
- Logic analyzer or oscilloscope with at least two channels
- Momentary button wired to `DOORBOARD_PIN_BUTTON_GPIO`
- Probe point on `DOORBOARD_PIN_LED_DATA_GPIO`
- USB-UART or Pi UART at 115200 8N1

## Button to LED latency

1. Connect channel 1 to the button GPIO and channel 2 to the LED data GPIO.
2. Boot the ESP32 with the Pi disconnected so fallback is active.
3. Press the physical button at least 100 times.
4. Measure falling edge on button GPIO to first LED data transition.
5. Attach the p95 measurement capture to the PR. Acceptance target: p95 < 30 ms.

## Pi heartbeat loss fallback

1. Start a UART sender that sends `hello` and 1 Hz `heartbeat` frames using protocol version 1.
2. Confirm ESP32 heartbeat frames report `"fallback_active":false`.
3. Stop Pi heartbeats for more than 5 seconds.
4. Confirm the next ESP32 heartbeat reports `"fallback_active":true`.
5. Press the button and confirm the fallback/generic physical effect still runs.

## Watchdog recovery

1. Flash a watchdog-test build or pause one firmware task under JTAG long enough to trip the 4 s task watchdog.
2. Confirm the ESP32 reboots.
3. Confirm boot starts in fallback state and button feedback still works with the Pi disconnected.
