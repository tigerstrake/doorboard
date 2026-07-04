# UI spec — DoorPad (`/doorpad`)

Public 7" touchscreen (~1024×600), the visitor's control surface. The hardware bell button remains the primary ring path — the DoorPad complements it.

## Home screen actions (large buttons, ≥48 px targets)

1. **Ring** — same session flow as the physical button.
2. **Leave a video message** — flow below.
3. **Guestbook** — short note; minimal keyboard (canned phrases + short free text, length-capped).
4. **Poll** — current question, tap to vote.
5. **Check in** — voluntary "I was here" (optionally as an enrolled person; feeds the playful most-frequent-visitor stat).
6. **Photo booth** (feature-flagged).
7. **Privacy** — camera notice, deletion request flow (`social.deletion_requested`).

## Video message flow

Offer → countdown → recording (visible red indicator + elapsed) → review (play back, re-record, discard) → save confirmation. Visitor can skip/abort at any step. Recording is explicitly visitor-initiated (privacy requirement); max duration capped.

## Behavior requirements

- Tap → visible response p95 < 100 ms (optimistic local rendering).
- Auto-reset to home after inactivity (~30 s outside recording flow); session state survives kiosk reload.
- Every action gives immediate visual + (via ESP32) audio feedback.
- No admin functions, no settings, no diagnostics, no exit from kiosk via touch.
- All submitted text is sanitized and rate-limited; deletion path visible.
