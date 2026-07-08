# ESP32 Repair

**Walkthrough Date:** July 8, 2026 (Verified successfully)

## Symptoms

- Physical bell button presses do not ring the bell or trigger kiosk wakeups.
- Kiosk/Wallboard reports "ESP32 Offline" or `esp32_link_connected == 0` on the status panel.
- No LED feedback on the button ring or local status indicators.
- Door API logs show `esp32_link_heartbeat_timeout` or `tx_timeouts_total` counting up continuously.

## Diagnosis

1. **Physical Status LED**: Check if the ESP32 board's onboard power LED (red) is illuminated. If not, the board has no power.
2. **Serial Connection Port Check**: SSH into the door Pi and list the connected USB devices to see if the serial bridge is detected:
   ```bash
   lsusb | grep -i "cp210" || ls -la /dev/ttyUSB*
   ```
3. **Heartbeat Timeout Count**: Query the `door-api` metrics endpoint to verify if timeouts are increasing:
   ```bash
   curl -s http://localhost:8000/metrics | grep esp32_link
   ```

## Step-by-Step Fix

### Step 1: Power Cycle the ESP32
1. Locate the physical ESP32 board inside the door enclosure.
2. Unplug the micro-USB cable from the ESP32 power port.
3. Wait exactly **5 seconds**.
4. Plug the micro-USB cable back in. Watch the status LED strip; it should display the default startup wipe animation.

### Step 2: Reflash ESP32 Firmware
If the board is powered but unresponsive or has corrupted firmware:
1. Connect the ESP32 to a laptop or keep it connected to the door Pi.
2. Navigate to the firmware build directory:
   ```bash
   cd ~/dev/doorboard/firmware
   ```
3. Flash the prebuilt firmware binary using `esptool.py` (ensure `esptool` is installed):
   ```bash
   # Auto-detect serial port and flash the bootloader, partition table, and app binary
   esptool.py --chip esp32 --port /dev/ttyUSB0 --baud 921600 write_flash 0x10000 build/doorboard-firmware.bin
   ```
4. Verify the console output ends with `Hash of data verified.` and `Leaving... Hard resetting via RTS pin...`

### Step 3: Degraded Fallback Mode (If ESP32 hardware is broken)
If the ESP32 board is fried and you do not have a spare:
1. Configure `door-api` to run in mock mode so the kiosk touchscreen displays a virtual "Ring Bell" button:
   - Edit the door-api service configuration at `/etc/doorboard/door-api.env`:
     ```ini
     ESP32_MOCK=true
     ```
2. Restart the door-api service:
   ```bash
   sudo systemctl restart door-api
   ```
3. The kiosk UI will now render a software ring button, allowing visitors to ring the bell without the physical hardware button.

## Verification

1. On reboot, the physical button LED ring displays a solid connection color.
2. Run the test link script:
   ```bash
   curl -s http://localhost:8000/metrics | grep esp32_link_connected
   # Should output: esp32_link_connected 1.0
   ```
3. Press the physical bell button once. The kiosk screen should transition to the visitor flow immediately.
