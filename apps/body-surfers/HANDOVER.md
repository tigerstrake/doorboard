# HANDOVER: Body Surfers

Read this first if you're picking up this project (human or a fresh Claude session).
Last updated: 2026-10-08.

## What this is
A Subway-Surfers-style endless runner played with your body through a laptop webcam:
step left/right to change lanes, jump to jump, squat to roll. 1P or 2P split-screen race,
crash-selfie leaderboard, and a second-screen Hall of Fame (`hall.html`). Built at a
casual hackathon with friends. The demo has already happened.

Stack: TypeScript, Vite 8, Three.js r186, MediaPipe Tasks Vision 1.1 (PoseLandmarker,
self-hosted in `public/`), Vitest 5, TypeScript 7, procedural Web Audio + procedural art.

## Status (what works)
- Full game loop, verified: boot → lobby (raise both hands) → countdown/calibration → play ⇄ pause → game over → restart.
- **Real webcam body tracking works** (user-tested). Fake-camera tests pass too.
- 2P split-screen race, leaderboard with crash selfies, Hall of Fame booth page, sounds/music.
- Endless speed ramp: it keeps getting faster up to a 45–48 m/s cap.
- Gates: typecheck clean, all unit tests green, `node e2e/run.mjs` 12/12. Latest round added swept collisions, ~120 ms faster lane detection, a compact tutorial pill, a simpler first-time flow, a once-only tutorial, and an arm-mimic runner (Settings → "Copy my moves").

## In progress / open items
1. **New power-ups** (`jetpack`, `slowmo`, `star`, `coinRain`) and the harder tuning are done across game, render, UI and audio. All unit tests pass. Jetpack and coinRain coins are laid by World, not the generator, so 2P tracks stay identical.
2. **Deployed on Vercel:** https://body-surfers-steel.vercel.app (project `body-surfers`, team tiger-strakes-projects). Redeploy with `npx vercel --prod --yes --name body-surfers`. The `--name` flag is needed because the folder name has a space.
3. **Git:** private repo https://github.com/tigerstrake/body-surfers (branch `main`). Commit and push after each working change. A Vercel deploy always needs `npx vercel --prod --yes --name body-surfers`; check that the output says `READY` and that the live site shows the change.
4. Nice-to-haves: seated/arms-only accessibility mode; runner skins; "save my card" button (reuse `renderSelfieCard` in `src/hall/card.ts`); more crash quips and names.

## How to run
```bash
npm install
npm run dev                      # http://localhost:5173  (camera needs localhost or HTTPS)
bash scripts/check.sh            # typecheck + tests + build + headless smoke runs (needs dev server)
node e2e/run.mjs                 # 12 Playwright scenarios (system Chrome, headless)
node e2e/perf.mjs                # frame-time benchmark
node scripts/shot.mjs --url "<url>" --out shots/x.png [--fake-camera] [--eval "js"]   # screenshot + console check
node tools/fakecam/run.mjs --routine solo|duo|spectator --url "<url>"                   # synthetic webcam person
```
Useful URLs:
- `/?input=keyboard&autostart=1&autopilot=1[&players=2][&seed=7]` for an AI demo or a smoke run.
- `/sandbox/vision.html` is the live camera debug page with skeleton, zones, event log and tuning sliders.
- `/sandbox/render.html`, `/sandbox/ui.html`, `/sandbox/audio.html` are module sandboxes.
- `/hall.html?demo=1` is the booth display with fake data. Add `?tv=1` on a booth monitor to hide the cursor and the play button.

Test handle in the browser: `window.__bodySurfers.state()` and `window.__bodySurfers.log`.

## Architecture (details in MASTERPLAN.md)
- `src/shared/types.ts` + `constants.ts` hold the **contracts** every module codes against. Change them deliberately, then fix all the consumers that tsc flags.
- `src/vision`: camera → MediaPipe → slot assignment → GestureDetector → ActionEvents.
- `src/game`: pure, seeded simulation (World, generator, AutoPilot). Deterministic: the same seed gives the same course.
- `src/render`: Three.js, one scene and camera per player, built entirely from code.
- `src/ui`: DOM overlay rendered from a `UIModel` each frame.
- `src/audio`: procedural sfx and music.
- `src/input`: keyboard.
- `src/app`: state machine, fixed-step loop, input routing, stores, booth broadcast.
- `src/hall`: second-screen page.

## Robustness notes (2026-10-13 pass)
- A failed **model or camera switch** in Settings keeps the previous one running (`KeptPreviousError` in `src/vision/VisionSystem.ts`); the app reverts the setting and shows a toast instead of erroring.
- Switching cameras mid-run pauses and recalibrates (new framing ⇒ new lane zones).
- `IAudio.clearPowerUps()` / `setPaused()`: a new countdown drops leftover jetpack/slowmo audio; pause silences the rumble. A crash clears that player's power-up audio (2P).
- Leaderboard writes are read-modify-write and App listens for `storage` events, so two game tabs no longer overwrite each other.
- `prefers-reduced-motion` (`src/shared/motion.ts`) turns off camera shake, FOV kicks, JS pops and hall confetti, and softens the crash flash. `src/ui/announcer.ts` feeds an `aria-live` region.
- `public/mediapipe/wasm` no longer ships the unused `*_module_internal` build; three.js and MediaPipe are separate cached chunks (`vite.config.ts`).

## Hard-won lessons
- **Webcam freeze bug (fixed):** Chrome's `requestVideoFrameCallback` can report a constant `mediaTime` for live webcam streams. Key new frames on `presentedFrames` (`src/vision/VisionSystem.ts` frame watch). The fake camera hid this bug, because canvas streams do advance `mediaTime`.
- **Center Stage / Studio Light** on M-series Macs pans the image and breaks the absolute lane zones. Turn them off in Control Center → Video Effects.
- Dim light drops the webcam to ~15 fps, which makes jumps laggy. Face a light.
- Load the pose model **before** starting the camera; GPU init can block for seconds.
- One Euro filter: use beta ≈ 15 on normalized coordinates for the gesture path (the original 0.6 added ~90 ms of lag).
- Camera play needs ~1 s from obstacle to completed lane change. Keep row gaps ≥ ~1.05 s at max level.
- Agents editing files trigger Vite HMR reloads, which restart smoke runs midway. Pause agents before live demos.

## Tuning knobs
- `src/game/config.ts`: `speedMin`/`speedMax`/`speedTau`/`speedRamp`/`speedCap`, `gapStart`/`gapEnd`, `reactTime`, forgiveness margins, power-up durations and weights.
- `src/shared/constants.ts` → `DEFAULT_VISION_TUNING` sets lane, jump and duck thresholds. These are also live in the in-game settings panel (`` ` `` key).

## Docs
`README.md`, `docs/PITCH.md`, `docs/DEMO_SCRIPT.md`, `docs/BOOTH_CHECKLIST.md`, `MASTERPLAN.md`, `tools/fakecam/README.md`.
