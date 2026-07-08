# integrations/printer — 3D printer status

Task: T-604 (Gemini). Feature flag: `FEATURE_PRINTER`.

- **Read-only** adapter for printer state (idle/printing/paused/error/offline, job name, progress, ETA) → `ambient.printer_status`. Concrete backend (OctoPrint) chosen in the task brief; the interface is backend-agnostic.
- **Public screens never get printer control.** No pause/cancel/temperature endpoints exist in this adapter at all; control stays in the printer's own UI.
- Interface: `PrinterProvider` with `mock` and `octoprint` implementations.

## Camera Preview Stream Configuration

To display a 3D printer camera preview stream securely:
1. **Never expose the raw OctoPrint camera port** (e.g. `8080` or `5000`) directly on public screens or public frontends.
2. Configure a proxied and relabeled stream URL via `door-media` or your NUC-side reverse proxy.
3. Set the environment variable `PRINTER_CAMERA_STREAM_URL` on the worker to point to the secure proxied path.
4. The frontend wallboard client retrieves the stream metadata from the control plane API.
