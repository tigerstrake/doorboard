# T-404: Wallboard visitor-mode takeover

**Agent:** gemini · **Milestone:** M4 · **Depends on:** T-401, T-004
**Why this agent:** visual/component work against a precise spec and existing event plumbing.

## Context

Spec: [docs/ui/wallboard.md](../ui/wallboard.md) visitor mode. T-004 built the mode-flip mechanics with placeholders; this task makes visitor mode real and delightful.

## Deliverables

- Visitor-mode screens keyed to session state: greeting (generic vs personalized — profile accent color/name for cached enrolled identities only), broad availability display, ring status (`RINGING`/`ANSWERED`/`UNANSWERED_TIMEOUT` visuals), video-message-in-progress state (privacy-respecting: show "recording in progress" indicator, not the camera feed), QR code component (live token from door-api), poll prompt, session-end thank-you.
- Visual effects: entrance/greeting animation, per-profile accent theming, subtle celebratory effect for enrolled greetings — CSS/ui-kit only, no heavy libraries (kiosk CPU budget).
- Late-recognition handling: identity arriving mid-session upgrades the greeting *in place* without resetting flow (per ARCHITECTURE.md §5.late).
- Ambient→visitor→ambient transitions smooth (no white flashes, no layout jank) and interrupt-safe (session end mid-animation).
- Playwright coverage of every session state's wallboard rendering, plus the privacy check: no unenrolled name, no camera feed, no diagnostics in any visitor-mode state.

## Out of scope

Session logic, DoorPad, new ambient tiles (T-605), sound (ESP32 owns audio), any UGC rendering changes (T-403 owns those components).

## Acceptance criteria

- Bell → visitor mode visible p95 < 250 ms (T-104 conventions) on kiosk hardware.
- Screenshot suite (Playwright) for every state at 1080p landscape+portrait committed as visual baselines.
- 1-hour idle+takeover soak: no memory growth, no animation degradation, kiosk CPU under agreed ceiling (document measurement).
