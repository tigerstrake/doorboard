# ESP32 Repair and Diagnostics

**Status:** Verified
**Walkthrough Date:** 2026-07-08 (Simulated/mock mode verified; hardware-specific steps marked and deferred)

## The Guarantee This Runbook Protects

Button debouncing and real-time physical feedback (LED & buzzer/speaker) must occur within **30 ms** entirely on-chip. The ESP32 does not wait on the Pi or NUC to fire generic button press chimes and animations. If the Pi connection is lost, it falls back to a safe "unavailable" indicator while preserving button feedback.

---

## Symptoms

- **No physical response:** Pressing the bell button does not light up the LED ring or sound the buzzer/speaker, but the DoorPad/kiosk UI works.
- **Pi connection lost (Fallback State):** The LED ring displays a continuous pulsing amber/blue wave or generic indicator, showing that it cannot communicate with the Pi.
- **Link errors logged on Pi:** `door-api` logs show UART link errors, checksum errors, or heartbeat timeout failures.
- **Offline metric:** `esp32_link_connected` is `0` on the Pi `/metrics` endpoint.

---

## Diagnosis

1. **Verify Serial Device presence:**
   On the Pi, ensure the serial interface is detected:
   ```bash
   ls -la /dev/ttyAMA0
   ```
   Check group ownership. The user running `door-api` must be in the `dialout` group to write to UART:
   ```bash
   groups doorboard-admin  # should include dialout
   ```
2. **Check Link Metrics:**
   Query the `/metrics` endpoint of the `door-api` service:
   ```bash
   curl -s http://127.0.0.1:8080/metrics | grep esp32_link
   ```
   - `esp32_link_connected`: Should be `1`. If `0`, communication is down.
   - `esp32_link_rx_errors_total`: Increments on malformed frames or JSON parsing issues.
   - `esp32_link_tx_timeouts_total` / `esp32_link_tx_retries_total`: Increments if the ESP32 is not acknowledging commands from the Pi.
3. **Verify Wire Connection (Hardware-specific):**
   - Check RX/TX cross-wiring: Pi TX (GPIO 14) connects to ESP32 RX; Pi RX (GPIO 15) connects to ESP32 TX.
   - Ensure a common ground wire is securely connected between the Pi and ESP32.
   - Verify external power: The amp and LED ring must be powered from an external 5V regulator, NOT the Pi's GPIO pins, to avoid voltage sags.

---

## Step-by-Step Fix (Firmware Reflash)

If the ESP32 microcontroller is unresponsive, has corrupted flash, or needs a firmware update:

1. **Setup Toolchain:**
   Ensure the ESP-IDF toolchain (`v5.3.2`) is installed on the flashing machine (or the Pi if dev tools are available).
2. **Build the Firmware:**
   Navigate to the firmware directory:
   ```bash
   cd /home/doorboard-admin/doorboard/firmware/esp32-door-controller
   idf.py set-target esp32s3
   idf.py build
   ```
3. **Flash the Chip (Hardware-specific step):**
   Connect the ESP32-S3's USB-to-UART port to the host system.
   ```bash
   idf.py -p /dev/ttyUSB0 flash
   ```
   *(Replace `/dev/ttyUSB0` with the actual serial port of the programmer on the host).*
4. **Monitor Boot output:**
   Run the serial monitor to check for boot loops or panics:
   ```bash
   idf.py -p /dev/ttyUSB0 monitor
   ```

---

## Fallback Verification

To verify that the ESP32 correctly enters degraded/fallback mode when the Pi is down:
1. Stop the `door-api` service on the Pi:
   ```bash
   sudo systemctl stop door-api
   ```
2. Wait 5 seconds (heartbeat timeout).
3. **Observation:**
   - The ESP32 LED ring should transition to the "fallback/unavailable" animation (e.g., slow pulsing blue or amber).
   - Press the physical bell button.
   - **Result:** The ESP32 must still instantly trigger the local generic chime/LED response within 30 ms, proving the local fallback path is operational.
4. Restart the `door-api` service:
   ```bash
   sudo systemctl start door-api
   ```
5. Wait 2 seconds. The LED ring must return to normal idle state, indicating the link has re-established.
