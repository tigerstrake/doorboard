# T-302: door-visiond Hailo pipeline

**Agent:** codex · **Milestone:** M3 · **Depends on:** T-301, T-201
**Why this agent:** accelerator integration + real-time pipeline with strict privacy constraints.

## Context

Spec: [apps/door-visiond/README.md](../../apps/door-visiond/README.md), ADR-0004 (Hailo ownership), T-301's design (binding). Start from the official Pi/Hailo face-recognition material as *reference only* — wrap or reimplement behind `VisionPipeline`; never couple to the demo's structure (handoff §10, §19-must-not-8).

## Deliverables

- `VisionPipeline` implementations: `hardware` (Hailo detect→align→embed→match), `single-camera`/`dual-camera` configuration, honoring existing `disabled`/`mock` modes.
- Stability filter per ARCHITECTURE.md §5: min face size, 2-of-3 frames, `identity_stable`/`identity_expired` emission with TTL, 30 s per-person cooldown.
- Enrollment storage per T-301's design; `POST /enroll`/`POST /unenroll` (admin-auth) with immediate-deletion semantics; `GET /current-visitor`.
- Privacy enforcement per T-301: unknown embeddings discarded in-memory (the specified tests prove it), privacy mode (`POST /privacy-mode`) drops the pipeline to detection-off while service stays healthy.
- Startup compatibility check (pinned Hailo runtime + model versions) → degrade to `disabled` + health signal on mismatch; Hailo crash/hang → supervised recovery without touching door interaction.
- Metrics: `inference_ms`, `face_to_identity_ms`, `fps`, `frame_drops`, `cache_hit_rate`.

## Out of scope

ESP32 profile push (T-303), enrollment UX (T-304), any second Hailo consumer, recognition-camera hardware bring-up beyond what the pipeline needs.

## Acceptance criteria

- T-301's full test specification implemented and green (non-persistence proven via disk+log inspection in tests).
- Bench: face-visible → identity_stable p95 < 600 ms in reasonable light (T-104 harness); pipeline runs 30 min without memory growth or thermal throttling alongside door-media streaming.
- Hailo unplugged/failing → generic-greeting mode within seconds, button flow unaffected (simulator + bench drill).
- Version pins documented; startup check demonstrated against a wrong-version fixture.
