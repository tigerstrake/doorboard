# T-613: Body Surfers on the wallboard

**Agent:** claude · **Milestone:** M7 · **Depends on:** T-405 (wallboard control), T-202 (door-media live stream)

Adds the owner's body-controlled endless runner, Body Surfers, to the door. You start it from the DoorPad and play
it on the wallboard, with the visitor camera tracking your body. Design and the privacy exception: ADR-0049.

## Context

The owner asked to add their webcam game (repo `tigerstrake/body-surfers`) to the doorboard. They chose body
tracking on the visitor camera and crash selfies on the leaderboard, as on the website (2026-10-14). Both
cameras are already owned, the Pi software-encodes video, and the bell path has a 250 ms budget. So the game
reads door-media's existing stream, runs in its own renderer process, and gives way to the bell immediately.

## Deliverables

- `apps/body-surfers`: the game, vendored into the pnpm workspace. A door mode (`?door=1`, `src/door/`) adds:
  - a WHEP camera source and a 20 Hz inference cap
  - exit rules and a one-way postMessage bridge
  - the photo notice, with click-only controls hidden
  - 30-day leaderboard retention and `clear.html`
- door-ui:
  - the build copies the game to `/games/body-surfers/`
  - the wallboard shows it in a cross-site sandboxed iframe
  - a DoorPad tile and a "now playing" screen
  - the admin "Clear leaderboard" control
  - flags `VITE_FEATURE_BODY_SURFERS`, `VITE_BODY_SURFERS_MEDIA_URL`, `VITE_BODY_SURFERS_URL`
- door-api:
  - `POST /wallboard/game` (start/stop), which refuses a start in privacy mode or during a visit
  - `POST /admin/wallboard/game/clear-leaderboard`
  - a stop broadcast when privacy mode switches on
  - the `FEATURE_BODY_SURFERS` flag
- `scripts/serve_door_ui.py`: `/games/*` may be framed only by the two loopback origins, is never served the SPA
  fallback, and gets the `application/wasm` MIME type.
- `infra/systemd/chromium-kiosk@.service`: `--site-per-process`.

## Out of scope

- Any `packages/contracts` change. The control message is the same ephemeral `/ws` UI-control pattern as
  `wallboard.focus_changed` (ADR-0011 debt, recorded in ADR-0049 §5).
- Game audio on the door, the Hall of Fame second screen, and per-entry leaderboard moderation.
- Moving the kiosk profile off the microSD (ADR-0049 §6 records the deviation).

## Acceptance criteria

- A DoorPad tap shows the game on the wallboard, and it is framed from the other loopback name.
- A bell press, any takeover state, or privacy mode removes the game in the same render, and it does not come
  back after the visit.
- A start during a visit, or in privacy mode, is refused with a reason the DoorPad shows as a sentence. This
  includes privacy mode that door-api missed across a restart.
- The game exits by itself: 60 s in the lobby without a run, 20 s with nobody in view, after 8 s on an error.
  The DoorPad returns home.
- The owner can clear the leaderboard with the admin token (401 without it), and entries older than 30 days
  are dropped.
- The game's unit tests, door-ui's unit tests, the door-api tests and the serve tests pass, as do lint,
  typecheck and the door-ui build.
- **On-device (owed):** bell → visitor mode p95 < 250 ms with a game running, with the stream encode and
  `face_to_identity_ms_p95` unaffected. If not, ship with `FEATURE_BODY_SURFERS=false`.
