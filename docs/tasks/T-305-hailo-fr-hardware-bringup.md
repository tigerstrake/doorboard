# T-305: Hailo facial-recognition hardware bring-up

**Agent:** claude · **Milestone:** M7 (hardware) · **Depends on:** T-302, T-303, T-304

Turns on real facial recognition once the Raspberry Pi AI HAT (Hailo NPU) +
camera are installed. Completes the hardware path deliberately deferred by
[T-302](T-302-visiond-hailo-pipeline.md) and tracked as issue #84. **Must be
implemented and verified on the device** — the inference/camera code is not
meaningfully testable off-hardware.

## Context

The privacy/matching pipeline, consent-first enrollment workflow (admin UI +
CLI), and deletion/privacy machinery are all built and pass in mock/scripted
mode ([ADR-0009](../adr/0009-enrollment-and-biometric-data.md)). What is *not*
built is the actual Hailo inference: `HailoEmbedder.embed()` and
`HardwareBackend.next_capture()` both raise `RuntimeError` placeholders, the
compat check can never verify a model so hardware modes fail closed to
`disabled`, no model artifact exists in the repo, and door-media's `/snapshot`
returns a 1×1 black pixel. So installing the HAT alone changes nothing. This
task implements and verifies the hardware layer end-to-end. The fail-closed
interlocks (`compat.py`, `storage_security.py`) are safety features — this task
makes them *pass legitimately*, it does not route around them.

## Deliverables

Ordered bring-up checklist (each step verified on the Pi before the next):

1. **Runtime + model.** Install `hailo_platform` (pinned `4.19.0`,
   `door_visiond/settings.py`). Source/compile the pinned recognition model
   (`arcface_mobilefacenet_v1`, 512-dim) to a `.hef`; decide where it lives
   (not committed — it's a build artifact) and how it's provisioned to the Pi.
2. **`probe_hailo` / compat.** Make `compat.probe_hailo()` actually read the
   loaded model's id + embedding dim so `check_compatibility` can return
   `ok=True` when (and only when) the pinned runtime + model are present.
3. **`HailoEmbedder.embed()`** (`embedder.py`): load the `.hef`, run inference,
   return a 512-dim `Embedding`. Must match `MockEmbedder`'s output contract so
   the existing matcher/enrollment logic is unchanged.
4. **`HardwareBackend.next_capture()`** (`pipeline.py`): real camera capture
   (libcamera/picamera2) + face detect + align feeding the embedder.
5. **Real enrollment snapshot.** Replace door-media `/snapshot`'s placeholder
   (`door_media/app.py`) with a real camera frame, so the enrollment wizard/CLI
   capture actual faces.
6. **Encrypted storage + key release.** Stand up the LUKS enrollment volume and
   the NUC key-release endpoint so `storage_security` unlocks and visiond leaves
   `enrollment_locked` (required by the pi-door default
   `VISIOND_REQUIRE_ENCRYPTED_STORAGE=true`).
7. **On-device verification.** Enroll a consenting person end-to-end, confirm a
   live match emits the personalization event, and confirm an unknown face is
   never persisted (re-run the ADR-0009 P-1…P-10 privacy checks on hardware).

## Out of scope

- The privacy/matching pipeline and enrollment workflow themselves (shipped in
  T-302/T-304) — unchanged except the adapters behind the seam.
- Self-serve/kiosk enrollment (currently admin-operated) — separate task.
- Any change to the door critical path (button → ESP32 → local UI) or to the
  fail-closed behavior when hardware is absent.
- Committing the model artifact to the repo.

## Acceptance criteria

- With the HAT + camera + model present, visiond runs in a hardware mode (not
  degraded to `disabled`); `hailo_ok` is only ever true when real inference is
  active.
- A consenting person can enroll (real captured frames) and be recognized live;
  raw images are wiped post-enroll.
- Unknown faces are never persisted; deletion/purge and privacy-mode kill switch
  still hold on hardware.
- With the HAT absent, behavior is unchanged (fails closed to `disabled`, door
  flow unaffected).
- `scripts/lint`, `scripts/typecheck`, and `scripts/test` pass (mock-mode tests
  remain green; hardware paths verified manually on-device and documented).
