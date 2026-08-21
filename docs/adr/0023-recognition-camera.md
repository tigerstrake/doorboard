# ADR-0023: `dual-camera` means a second camera

**Status:** Accepted · **Date:** 2026-08-17

Implements the two-camera layout ARCHITECTURE.md §1 has always described
(`CSI×2: recognition cam (Std) + visitor cam (Wide NoIR)`). Extends
[T-314](../tasks/) (visitor encode settings) and [ADR-0007](0007-storage-tiers.md) only in
that no new data is stored.

## Context

`VISION_MODE` has accepted `dual-camera` since T-302, and it did nothing.
`_HARDWARE_MODES` treated `single-camera`, `dual-camera` and `hardware` identically:
`HardwareBackend` took one `snapshot_url`, door-media published one stream, and no code
anywhere referred to a second sensor. A door configured for two cameras used one, and
nothing said so — not `/health`, not the logs, not the metrics.

That became load-bearing when a second camera was physically connected and the roles were
chosen: **wide sensor frames the doorway, narrow sensor does faces.** Wide coverage is
right for video and wrong for recognition, which wants pixels on a face; the narrow sensor
is the opposite. Getting that backwards costs match scores, and with the mode inert there
was no way to express it at all.

## Decision

**`dual-camera` reads a different camera for faces.** door-media gains
`GET /snapshot/recognition`, and `Settings.face_snapshot_url` resolves per mode — one
property, so the backend, the startup path and the health surface cannot disagree about
which sensor is in use. Every other mode reads the visitor stream exactly as before.

**The recognition camera is read directly, in MJPEG.** `rpicam-vid --codec mjpeg` off the
sensor: no RTSP hop, no MediaMTX path, no H.264 encode. Nothing consumes a recognition
*stream* — the only reader is a JPEG decoder feeding SCRFD — so an encode would spend ~90%
of a core (measured, T-314) producing something nothing wants. The visitor camera keeps its
RTSP reader, because it is *already* encoding H.264 for the live view and recordings and
re-opening that sensor directly would contend for it.

**A missing recognition camera 404s rather than substituting.** `RECOGNITION_CAM_INDEX=-1`
(the default, and the single-camera door) makes the route return 404, and door-visiond gets
no frame. It deliberately does **not** fall back to the visitor stream: that substitution
is precisely the silent wrong-camera behaviour this ADR exists to end, and it would be
invisible from outside — a wide-angle view of the doorway, reported as success.

For the same reason the route returns no placeholder JPEG, unlike `/snapshot`. A
placeholder is a valid-looking image of nothing, and the face path would read it as a frame
containing no face — turning "there is no camera" into "nobody is at the door".

**Orientation and tuning are per camera.** Two sensors mounted separately can be inverted
separately (T-321 found the first one upside down), and they are different NoIR variants
whose tuning files are not interchangeable.

**`/health` states the frame source.** A URL, not a boolean: "configured for two cameras,
using one" was the failure mode, so the answer to "which camera am I actually reading" is
now one field.

## Consequences

- The MJPEG framing logic was extracted to `door_media.frame_reader` so both cameras share
  one implementation. Duplicating it would have duplicated a specific hazard: a JPEG's SOI
  and EOI routinely land in different pipe reads, and mishandling that presents as
  recognition working *intermittently*.
  **Extracting it surfaced a real bug in the original.** When no SOI was found the buffer
  was cleared outright, so a chunk boundary falling *inside* the two-byte start marker
  discarded it and lost the frame that followed. It only triggers on that exact alignment,
  which is why it survived T-310's split-stream test. Now the trailing byte is kept.
- A second sensor is opened while the first is streaming. They are independent CSI devices,
  so this is not the Hailo situation — but the recognition reader idle-stops like the
  visitor one, so an empty doorway and privacy mode still cost nothing.
- One more camera's frames exist in memory, briefly, and are never written to disk. No
  retention rule changes, and ADR-0009's biometric rules are untouched: these are frames,
  matched and dropped, exactly as the single-camera path already does.
- The mode is still opt-in. An existing door upgrading gets `-1` and behaves as it does
  today.
