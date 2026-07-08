# T-790: Final acceptance

**Reviewer:** Claude tier (release sign-off, ADR-0008) · **Date:** 2026-07-08 · **Scope:** whole system at `main`, end of M7.

**Verdict:** ✅ **Software release accepted — sim-complete.** All eight milestones (M0–M7) are closed, the four invariant families hold, and the full test suite + simulator soak are green. The system is **not yet accepted on hardware**: physical bring-up and on-device acceptance are deferred debt, consolidated and tracked in **#84**. This is a GO to proceed to hardware bring-up, not a GO to a live hallway.

## Success criterion (handoff §21)

> A person can approach the door, press a physical button, see and hear immediate feedback, interact with two displays, leave a video message, and trigger a personalized opt-in greeting, even if NUC, NAS, or internet is temporarily unavailable.

| Clause | Delivered by | Status |
|---|---|---|
| approach the door | vision approach detection (M3/M4) | ✅ met in simulation |
| press a physical button | ESP32 button + firmware (M1) | ✅ sim + firmware written · ⏸ physical flash/press → #84 |
| see & hear immediate feedback | ESP32 LED/audio, &lt;30 ms budget (M1) | ✅ logic in sim · ⏸ physical p95 → #84 |
| interact with two displays | door-ui wallboard + doorpad (M4) | ✅ met in simulation |
| leave a video message | media pipeline + session machine (M2/M4) | ✅ sim · ⏸ real camera capture → #84 |
| personalized opt-in greeting | identity cache + profile push (M3) | ✅ sim · ⏸ Hailo bring-up → #84 |
| even if NUC / NAS / internet down | plane split + failure behavior (M5, T-702) | ✅ proven in sim (NAS-outage soak, offline critical path) |

The criterion is **structurally satisfied end-to-end in simulation**; every ⏸ is a hardware-execution gap, not a design or logic gap.

## Milestone completion

| Milestone | Scope | State |
|---|---|---|
| M0 | Bootstrap: tooling, CI, contracts, simulator, mock UIs | ✅ closed |
| M1 | Real-time I/O: ESP32, Pi↔ESP32, latency harness | ✅ closed |
| M2 | Camera + media: MediaMTX, WebRTC, SSD recording | ✅ closed |
| M3 | Face recognition: Hailo pipeline, enrollment, identity cache, privacy | ✅ closed |
| M4 | Visitor session: state machine, DoorPad, video messages, guestbook/polls | ✅ closed |
| M5 | Control plane: NUC API, Postgres, HA, statuses, NAS sync | ✅ closed |
| M6 | Ambient features (6 adapters) + review (T-690) | ✅ closed (7/7) |
| M7 | Perf acceptance (T-702), runbooks/monitoring (T-703), security review (T-701), final acceptance (T-790) | ✅ closed |

## What's proven now (in simulation)

- Full `pytest` suite green; `pnpm` typecheck + TS tests green; pyright at 0; contracts generated & current.
- T-702 30-min simulator soak passes: p95 budgets met (sim proxies), NAS outage injected + backlog drains, no missed presses, no throttling.
- T-701 security review: no blocking code/config findings; trust boundaries hold.
- Latency budgets encoded and observable; a regression is treated as a bug.

## What remains before a live hallway — tracked in #84

Hardware bring-up (Hailo, ESP32 flash, real cameras), on-device acceptance (physical p95, 30-min hardware soak per §17), live external-service wiring + per-device config, and the deployment/physical security walk from `docs/runbooks/security-checklist.md`. None of these are software defects; they require hardware that does not yet exist.

## Sign-off

Software scope of the doorboard project is **complete and accepted** against the handoff. Release to hardware is authorized to begin, gated on the #84 bring-up checklist. Final acceptance *on hardware* will re-run this matrix with each ⏸ converted to ✅ (or to a filed defect) once the device exists.
