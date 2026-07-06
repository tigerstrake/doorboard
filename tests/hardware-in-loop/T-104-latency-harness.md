# Hardware-in-Loop Procedure: T-104 Latency Harness

This document describes the manual bench testing procedure for measuring real-world latencies using the performance harness. This procedure validates the end-to-end paths defined in `ARCHITECTURE.md` §4 on actual hardware, replacing the simulator mocks.

## Prerequisites

- **Hardware Bench Setup**: You need a Raspberry Pi, an ESP32 board connected via UART/USB (T-101 ESP32 link), and the Hailo/Camera peripherals.
- **Network**: WebRTC measurements require a functioning MediaMTX deployment.
- **Dependencies**: Ensure the python environment is set up (`uv sync`).

## Procedure

1. **Connect and Power On**: Ensure all devices on the bench are connected and powered on. The ESP32 must be running the current firmware build.
2. **Launch Harness**: Run the latency harness in hardware mode from the root of the workspace:
   ```bash
   uv run python -m tests.performance.harness --mode hardware
   ```
3. **Follow Prompts**: Unlike the simulator mode which runs instantly, the hardware mode is interactive. It will prompt you to:
   - Press the physical bell button multiple times (to collect `button_to_generic_feedback` and `button_to_personalized_feedback` samples).
   - Step into the camera's view (to collect `face_to_stable_identity` samples).
   - Use a WebRTC client to view the stream (for `webrtc_glass_to_glass` estimations).
4. **Clock Offset Synchronization**: The harness will automatically run a ping-ack sequence to the ESP32 to establish the cross-device clock offset (Pi's `CLOCK_MONOTONIC` vs ESP32's `esp_timer_get_time`).
5. **Review Results**: Once the required number of samples (typically 50) is collected for each path, the harness will output a report table in the terminal and optionally to a JSON file.

## Interpretation & Constraints

- **Clock Synchronization Error**: Pay attention to the reported `max_error_ms` calculated from the median half-RTT during the clock offset synchronization phase. If this value exceeds 5ms, the bench environment may have severe UART congestion or scheduling issues, and latency measurements should be considered suspect.
- **Budgets**: The p95 latencies must fall strictly within the budgets defined in `ARCHITECTURE.md` §4. Hardware runs include physical serialization and ISR overheads missing from the simulator.
- **Simulator N/A Paths**: Paths like `webrtc_glass_to_glass` which are skipped by the simulator will be actively measured and validated in this hardware mode.
