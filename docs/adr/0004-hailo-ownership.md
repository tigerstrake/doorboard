# ADR-0004: door-visiond exclusively owns the Hailo accelerator

**Status:** Accepted · **Date:** 2026-07-04

## Context

The AI HAT+ (Hailo-8, 26 TOPS) sits on the Pi 5's single PCIe lane. Concurrent unrelated Hailo consumers are not reliably supported, and the handoff mandates that the real-time vision service own the device.

## Decision

`door-visiond` is the only process that opens the Hailo device. All accelerator access is encapsulated behind a replaceable `VisionPipeline` interface inside door-visiond, with modes `disabled | mock | single-camera | dual-camera | hardware`. Other services consume vision *events* (via contracts) or the `current_visitor` cache — never the device. Hailo runtime and model versions are pinned; door-visiond performs a startup compatibility check and falls back to `disabled` mode (generic greetings) on mismatch or device failure.

## Consequences

- Future vision workloads (e.g., extra models) live inside door-visiond or wait for an explicit, tested multi-consumer ADR.
- Migration to a NUC/Jetson vision host later means reimplementing the `VisionPipeline` interface, not the consumers.
- The official Pi/Hailo face-recogniser example is reference material only; its code is wrapped or reimplemented, never imported as the production pipeline.
- Because the Pi 5 PCIe lane is consumed, active storage is a USB 3 SSD (see ADR-0007).
