# ADR-0049: Body Surfers runs on the wallboard

**Status:** Accepted · **Date:** 2026-10-14 · **Relates to:** ADR-0002 (two planes), ADR-0005 (privacy model), ADR-0007 (storage tiers), ADR-0011 (display control), ADR-0033 (guestbook photos), ADR-0047 (LAN proxy allow-list) · **Amends, for this game only:** ADR-0033's "a photo is private until the owner approves it" and ARCHITECTURE §9's "no private photos on public routes"

## Context

The owner asked for Body Surfers to be added to the doorboard. Body Surfers is their webcam endless runner (a separate repo, `tigerstrake/body-surfers`): you step left and right to change lanes, jump, and squat to roll. When you crash, it takes a selfie for a photo leaderboard. On 2026-10-14 the owner chose two things:

- **Body tracking on the visitor camera.** The alternatives offered were swipe control on the DoorPad, the DoorPad as a controller, and a separate USB camera.
- **Crash selfies "just like on the website".** The alternatives offered were no selfies, owner-approved selfies, and no leaderboard.

Five constraints decide how it can be done:

- **Camera ownership.** Both cameras are already owned. door-visiond has the recognition camera. door-media has the visitor camera, published through MediaMTX as one encode with many consumers. The game cannot open a camera device.
- **CPU.** The Pi 5 has no H.264 block, so the visitor stream is encoded *and* decoded in software. MediaPipe and Three.js add to that load. Bell → visitor mode is budgeted at p95 < 250 ms.
- **Privacy.** ARCHITECTURE §9 and ADR-0033 keep visitor photos private until the owner approves them. A photo leaderboard on the hallway screen is exactly that.
- **Framing.** `serve_door_ui.py` sends `X-Frame-Options: DENY` on everything.
- **Kiosk input.** The wallboard has no pointer and no keyboard.

## Decision

**1. Vendored, served by door-ui, door mode behind `?door=1`.** The game is copied into `apps/body-surfers` (`@doorboard/body-surfers`). The door-ui build compiles it and copies it to `/games/body-surfers/` (`apps/door-ui/scripts/copy-games.mjs`), so `serve_door_ui.py` serves it with the kiosk bundle. Everything door-specific lives in `src/door/` and runs only with `?door=1`:

- the WHEP camera source
- the exit rules
- the postMessage bridge
- the leaderboard clear page
- lite model, low render quality
- no click-only controls

Without `?door=1` the game behaves as it did on the website.

**2. It runs only on the wallboard, and only when someone starts it from the DoorPad.** Nothing tracks poses while no game is on. A game ends when any of these happens:

- **The game hands the wallboard back by itself:**
  - 60 s in the lobby without a run
  - 20 s with nobody in view, on any screen
  - an error, after 8 s on screen so it can be read
  - a boot that has not finished after 45 s
- someone taps **Stop** on the DoorPad
- the session enters any wallboard takeover state (the bell)
- privacy mode switches on
- the 15-minute hard cap
- no `ready` from the frame within 30 s

A game ended by a visit does not come back when the visit ends. A second Play while a game runs does not restart it.

**3. The camera is the existing visitor stream.** The game reads door-media's WHEP stream through `@doorboard/media-client`, the same player the kiosk's live panels use. There is no getUserMedia, no new camera owner and no permission prompt. door-media's CORS already admits both kiosk origins, and MediaMTX's WebRTC endpoint allows any origin.

Pose landmarks exist only in the frame's memory. They are never logged, stored or sent anywhere, and they are not biometric templates: there is no face embedding, matching or naming. Inference is capped at 20 Hz (`DOOR.minInferenceIntervalMs`), the lite model is forced, and rendering uses the low path.

**4. The game runs in its own renderer process, and unmounting it is how it stops.** The wallboard frames the game from the *other* loopback name: `127.0.0.1` ↔ `localhost`, on the same server. Those are different sites to Chromium, so site isolation puts the frame in a separate renderer process. The kiosk unit passes `--site-per-process` so that does not rest on a heuristic.

As a result, the wallboard's main thread, which handles the `/ws` session delta, never waits on pose inference or the game's render loop. The render that switches to visitor mode is the same render that removes the frame, and removing the frame ends its process. A browser smoke test on a dev machine measured 5 ms from the ring POST to the frame being gone. The Pi is still to be measured; see Consequences.

The frame is `sandbox="allow-scripts allow-same-origin"`. It is cross-site to the wallboard, so allowing same-origin only gives the game its own storage.

`serve_door_ui.py` sends `Content-Security-Policy: frame-ancestors http://127.0.0.1:<port> http://localhost:<port>` for `/games/*` only. Every other path keeps `X-Frame-Options: DENY`. A missing game asset is a 404, never the SPA shell.

**5. Control runs over the existing display-control path, not a new contract.** The two kiosks talk as follows:

- **Start/stop.** The DoorPad POSTs `/wallboard/game` (`start`, or `stop` with reason `doorpad`). The wallboard POSTs `stop` with reason `ended` when the game exits.
- **Fan-out.** door-api sends an ephemeral `wallboard.game_changed` message on `/ws`, exactly as `/wallboard/focus` does with `wallboard.focus_changed`. It is not a `packages/contracts` event and is never persisted. The wallboard's rendered state is the source of truth, and a lost message degrades to "no game".
- **Refusals.** door-api refuses a start while privacy mode is on, or while the session is in a takeover state (409 `privacy_mode` / `visitor_session`).
- **Within the kiosk page.** The game and the wallboard use a one-way postMessage bridge: `ready`, `exit`, `leaderboard-cleared`. The wallboard checks the sender's exact origin and window. These messages never leave the kiosk page.

This is the same contract debt ADR-0011 already records: display control was accepted as a typed `display.*` event but never landed in `packages/contracts`. When it does, `wallboard.game_changed` moves there with `wallboard.focus_changed`. Neither route is on the ADR-0047 LAN allow-list, so both are loopback-only.

**6. Crash selfies are allowed on the wallboard leaderboard. This is the owner's decision, scoped as follows:**

- **Who.** Only a person who joined a run by raising both hands. They have been told twice before playing:
  - the DoorPad's "now playing" screen says the camera tracks their body during the game, and that a crash photo of their face goes on the leaderboard for 30 days
  - the lobby shows the same notice
  The photo is the game's existing 192 px crop around the player's face, not the camera frame, so bystanders are not in it.
- **Where it is kept.** Only in the wallboard kiosk's browser storage, for the game's origin. It is never uploaded (no door-sync, NUC, NAS or relay), never linked to recognition, a `person_id` or an enrollment, and never shown on `/visitor`, the relay, ambient tiles or the DoorPad. It appears only on the game's own screens on the wallboard.
- **How long.** The top 10 scores, each dropped 30 days after the run (`DOOR.leaderboardMaxAgeDays`). That is checked every time a game starts.
- **Deletion.** The owner's **Clear leaderboard** in the admin moderation panel calls `POST /admin/wallboard/game/clear-leaderboard` (admin token). door-api relays the request to the wallboard, which loads the game's tiny `clear.html` in a hidden frame to wipe that origin's storage. A game that is running sees the wipe through its `storage` listener.
- **Storage tier, a recorded deviation from ADR-0007.** The kiosk profile lives at `/var/lib/doorboard/kiosk-wallboard` on the microSD. The board is at most 10 × ~15 KB and is rewritten only at game over. Moving the kiosk profile to the SSD would put the wallboard's own startup behind the SSD mount, which is the worse trade for the door path.

**7. Privacy mode ends the game and blocks new ones.** The wallboard drops the frame on the `vision.privacy_mode_changed` delta, and door-api broadcasts a stop as well, which the DoorPad acts on.

On a start, door-api asks door-visiond's `/health` for `privacy_enabled` (0.5 s, loopback), because its own copy only learns from events and would forget privacy mode across a door-api restart. If visiond does not answer, door-api falls back to that copy.

**8. Both sides can switch it off.** door-api has `FEATURE_BODY_SURFERS` (runtime); door-ui has `VITE_FEATURE_BODY_SURFERS` (build). Both default on. With either off, door-api answers 404 `body_surfers_disabled`, or the tile and the frame are simply absent.

## Consequences

- **Pi acceptance is owed before this is trusted on the door.** Neither the CPU headroom nor the latency budget has been measured on the Pi. Two things need checking:
  - bell → visitor mode p95 with a game running, against the 250 ms budget
  - visitor-stream encode health and door-visiond's `face_to_identity_ms_p95` while a game runs
  If either regresses, the fix is a smaller render target or a lower inference rate. If neither is enough, the fallback is `FEATURE_BODY_SURFERS=false`, not a change to the bell path.
- **The game is silent on the door.** Nothing on the wallboard can give the user gesture Chromium needs before audio plays, and a dorm hallway is the wrong place for game music. Adding `--autoplay-policy=no-user-gesture-required` to the wallboard kiosk would turn sound on.
- **The doorboard now carries a second front-end toolchain** (TypeScript 7, Vite 8, Vitest 5, Three.js, MediaPipe). It is isolated to `apps/body-surfers`. The repo's ESLint ignores the game's dev-only harnesses (`e2e/`, `tools/`, `scripts/`) and the MediaPipe runtime, which is copied from `node_modules` at build time rather than committed.
- **Two lists are kept in step by hand, without a lockstep test:** door-api's `_WALLBOARD_TAKEOVER_STATES` and door-ui's `WALLBOARD_TAKEOVER_STATES`.
- **Upstream changes are not merged automatically.** The upstream repo keeps its own history; changes there are copied into `apps/body-surfers` by hand.
