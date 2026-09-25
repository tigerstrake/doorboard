# integrations/printer — 3D printer status

Task: T-604 (Gemini). Feature flag: `FEATURE_PRINTER`.

- **Read-only** adapter for printer state (idle/printing/paused/error/offline, job name, progress, ETA) → `ambient.printer_status`. Concrete backend (OctoPrint) chosen in the task brief; the interface is backend-agnostic.
- **Public screens never get printer control.** No pause/cancel/temperature endpoints exist in this adapter at all; control stays in the printer's own UI.
- Interface: `PrinterProvider` with `mock` and `octoprint` implementations.

## No camera stream

There is none. `ambient.printer_status` carries state, job name, progress and
ETA and nothing else, and the wallboard's printer tile renders exactly that. A
`PRINTER_CAMERA_STREAM_URL` setting existed for a while: it was parsed, passed
into `PrinterConfig`, and read by nothing, while this README described a
proxied webcam preview that no code implemented. It has been removed rather
than left as a promise.

If a preview is ever wanted it is a contract change (a new field on the
payload, so an ADR) and a privacy question first: the raw OctoPrint camera
port must never be reachable from a public screen, so any stream would have
to be a NUC-side proxied, relabeled URL -- and a camera pointed at a desk is
not obviously something a corridor-facing display should show at all.
