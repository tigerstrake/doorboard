# integrations/printer — 3D printer status

Task: T-604 (Gemini). Feature flag: `FEATURE_PRINTER`.

- **Read-only** adapter for printer state (idle/printing/paused/error/offline, job name, progress, ETA) → `ambient.printer_status`. Concrete backend (OctoPrint/Moonraker/vendor API) chosen in the task brief once the printer is known; the interface is backend-agnostic.
- Optional low-latency camera preview: proxied/relabeled stream endpoint published to door-media metadata — never a raw printer-camera port on public screens.
- **Public screens never get printer control.** No pause/cancel/temperature endpoints exist in this adapter at all; control stays in the printer's own UI.
- Interface: `PrinterProvider` with `mock` implementation first.
