> **Doorboard copy.** This is Body Surfers as vendored into the doorboard (`@doorboard/body-surfers`), from the
> upstream repo `tigerstrake/body-surfers`. With `?door=1` it runs inside the wallboard. In that mode it reads the
> visitor camera through door-media's WHEP stream, exits by itself, and keeps crash photos for 30 days. That code
> lives in `src/door/`; see [ADR-0049](../../docs/adr/0049-body-surfers-runs-on-the-wallboard.md). Without
> `?door=1` it is the game below, unchanged. The Vercel deploy and git notes in `HANDOVER.md` describe the
> upstream repo, not this copy.

# BODY SURFERS

**A Subway-Surfers-style endless runner you play with your whole body.** Your laptop webcam
watches you: step left or right to switch lanes, jump to jump, squat to roll. Two people can
race split-screen on the same course. When you crash, the game takes a selfie of your crash
face and puts it on the leaderboard.

Runs in the browser. No install, no controllers. The pose models and WASM runtime are
self-hosted, so the game does not need a CDN at runtime.

**Play it live:** https://body-surfers-steel.vercel.app

You need a laptop with a webcam. Chrome is recommended (the game is developed and tested in
Chrome). On a Mac, turn off Center Stage first (see [Tracking tips](#tracking-tips)).

---

## Quick start

```bash
npm install
npm run dev          # http://localhost:5173
```

Open `http://localhost:5173`, click **TAP TO START** (browsers need one click for audio and
camera), allow camera access, step back, and raise both hands.

No camera? Open `http://localhost:5173/?input=keyboard`.

Requirements: Node 22.12+ (Vite 8 alone runs on 20.19+, but Vitest 5 needs 22.12+), a laptop
webcam, and a browser with WebGL2. Chrome is recommended; it is the browser the game and its
Playwright tests are run in. Developed on M-series MacBooks.

The MediaPipe WASM runtime and pose models are already in `public/`. If `public/mediapipe/wasm`
is missing, run `npm run copy-mediapipe`.

## How to play

### With your body (default)

| Move | In game |
|---|---|
| Step left / right | Change lane. Your body position *is* the lane: stand left of center for the left lane, center for the middle, right for the right lane. |
| Jump | Jump over low barriers |
| Squat | Roll under overhead bars. Squatting in the air fast-falls. |
| Raise both hands (hold ~1 s) | Start, join, restart, resume |

**Starting a game.** In the lobby, one person raises both hands. A ring fills up, and the
player joins. If a second person is in frame, they get 4 seconds to raise their hands and
join a race. If nobody else is visible, a 1-player game starts right away.

**Countdown = calibration.** During 3-2-1, stand still with your hands down. The game
records your neutral position so lanes and jump/squat lines fit your body and distance.
The runner waits until every player is visible and calibrated. If it stays at 1, follow
the camera hint or open **CAMERA SETTINGS**; **PLAY WITH KEYBOARD** explicitly skips body controls.
Camera or model errors are shown on the start screen, including when using `?autostart=1`.

**Tutorial.** The first 350 m (about 27 seconds at the fixed tutorial speed of 13 m/s) teach
jump, squat and step with big hints, and crashes there are not fatal. The tutorial shows
once per browser: after a run gets past it, later runs skip it. Turning **Tutorial** back
on in Settings shows it again.

**Countdown with nobody in view.** If the camera sees no one for 8 seconds during the
countdown, the run starts anyway and the arrow keys steer.

**Copy my moves.** In camera mode the runner's arms and upper body mirror yours while the
legs keep running. It is on by default; toggle it in Settings under **Copy my moves**.

**Speed.** The run starts at 13 m/s, eases toward 27 m/s, and keeps ramping by a further
0.05 m/s every second, up to a cap of 48 m/s (reached after about 7 minutes of running).

**Power-ups (8):**

| Power-up | Duration | Effect |
|---|---|---|
| Magnet | 12 s | Pulls in coins within 14 m |
| Multiplier | 15 s | 2x score |
| Super sneakers | 12 s | Super jump (4 m apex), lands on train roofs |
| Hoverboard | 25 s | Absorbs one crash, then 1.5 s of invulnerability |
| Jetpack | 7 s | Flies 6 m up over the trains along a coin trail |
| Slow-mo | 8 s | The world runs at 60% speed |
| Star | 8 s | Invincible; smash through obstacles |
| Coin rain | 10 s | Dense coin lines in every lane |

Ramps take you up onto train roofs.

**Crash.** You get a crash selfie, your score and rank, and an editable fun name. Raise both
hands to go again.

In 2-player mode, the first player to crash makes the other the winner, and the survivor
keeps running for a high score.

### Keyboard

Keyboard and camera are both always active, so the keyboard works as a fallback at any time.

| 1 player | Action |
|---|---|
| Left / A | Move left |
| Right / D | Move right |
| Up / W / Space | Jump |
| Down / S / Shift | Roll |

| 2 players | P1 (left) | P2 (right) |
|---|---|---|
| Move | A / D | Left / Right |
| Jump | W / Space | Up |
| Roll | S / Left Shift | Down / Right Shift |

| Menu key | Does |
|---|---|
| Enter / Space | Confirm / start |
| 1 / 2 | Start 1-player / 2-player |
| P / Esc | Pause |
| Backspace / Q | Back to lobby |
| M | Mute |
| F | Fullscreen |
| `` ` `` (backtick) | Settings panel |
| I | Debug overlay |

## Tracking tips

- **Stand about 2 m from the laptop.** Your head and shoulders must stay in frame when you
  jump. The game shows hints such as "too close", "too far" and "head cut off".
- **Light in front of you**, not behind. A window behind the player makes tracking unreliable.
- **Tilt the screen back** so the camera sees from your head to your hips, with headroom
  above you. Raising the laptop to waist height helps.
- **Leave room on both sides.** Lane zones scale with your distance, so a normal step is
  enough, but you need clear floor.
- **Better camera:** on a Mac, an iPhone via Continuity Camera usually tracks better than the
  built-in webcam. Pick it in Settings (`` ` ``) under camera.
- People walking behind the players are ignored as spectators. Players are identified by
  size (closest to the camera) and locked once they join.
- For two players, stand about 1.5 m apart.
- **Turn off Center Stage on Macs.** On M-series MacBooks, Center Stage (and Studio Light)
  pans and crops the image to follow you, which breaks the absolute lane zones. Turn them
  off in Control Center → Video Effects while the camera is in use.
- **Dim light** drops many webcams to around 15 fps, which makes jumps laggy. Face a light.

## Settings and tuning

Press `` ` `` (backtick) to open the settings panel. Everything is saved to `localStorage`.

| Setting | Default | Notes |
|---|---|---|
| Camera | system default | Lists all video inputs, including Continuity Camera |
| Pose model | `lite` | `full` is more accurate and slower |
| Quality | `high` | `low` disables shadows and caps pixel ratio at 1 |
| Mute, music volume, SFX volume | off, 0.6, 0.8 | |
| Tutorial | on | Switches itself off once a run gets past the tutorial (once per browser). Turn it on again to replay it. |
| Show webcam | on | Webcam overlay with skeleton, lane zones, jump line (green) and duck line (orange) |
| Lane threshold | 0.9 (0.5 to 1.5) | How far from center (in shoulder widths) to enter a side lane. Lower = smaller steps. |
| Lane hysteresis | 0.3 (0.1 to 0.6) | How far back toward center before leaving a side lane. Raise if lanes flicker. |
| Jump threshold | 0.3 (0.15 to 0.6) | Torso rise needed to jump. Lower if jumps are missed, raise for false jumps. |
| Duck threshold | 0.45 (0.25 to 0.8) | Torso drop needed to roll |
| Smoothing | 0.5 (0 to 1) | Higher = steadier but laggier |
| Copy my moves | on | The runner's arms and upper body mirror yours (camera mode only) |
| Mirror | on | Selfie-style view |

The panel also has **Reset defaults** and **Clear leaderboard** (with an inline confirm).

## URL parameters

| Param | Effect |
|---|---|
| `?input=keyboard` | Skip the camera entirely |
| `?players=1` / `?players=2` | Force the player count |
| `?seed=123` | Fixed course (same seed gives the same track) |
| `?autostart=1` | Skip the start click and lobby, go straight to the countdown |
| `?autopilot=1` | The AI plays every runner (demos, soak tests) |
| `?debug=1` | Stats overlay (fps, frame time, vision fps, inference ms, draw calls) |
| `?model=lite\|full` | Override the saved pose model |
| `?quality=low\|high` | Override the saved render quality |
| `?mute=1` | Start muted |

Example hands-off demo: `/?input=keyboard&autostart=1&autopilot=1&players=2`

## Hall of Fame (second screen)

`hall.html` is a booth display for a second monitor or projector on the **same machine and
browser**. It reads the leaderboard from `localStorage` and receives live scores and results
from the game over a `BroadcastChannel`. Open `http://localhost:5173/hall.html` in a second
window and drag it to the other screen.

The page shows the photo leaderboard, a "now playing" panel with live scores, and a result
celebration when a run ends. `hall.html?demo=1` runs it on fake in-memory data (it never
writes storage); add `tv=1` on a booth monitor to hide the cursor and the play button.

## Scripts

| Command | Does |
|---|---|
| `npm run dev` | Vite dev server on port 5173 |
| `npm run build` | Typecheck, then production build to `dist/` (game + hall) |
| `npm run preview` | Serve the built `dist/` |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` / `npm run test:watch` | Vitest unit tests (238 tests in 15 files) |
| `node e2e/run.mjs` | 12 Playwright end-to-end scenarios in headless system Chrome |
| `node e2e/perf.mjs` | Frame-time benchmark (headless; numbers are a lower bound of a real GPU) |
| `npm run shot -- --url <url> --out shots/x.png` | Headless Chrome screenshot, key presses, error check (see the header of `scripts/shot.mjs`) |
| `npm run copy-mediapipe` | Copy the MediaPipe WASM runtime into `public/` |
| `bash scripts/check.sh [--quick]` | Full gate: typecheck, tests, build, then headless smoke runs (1P and 2P autopilot, boot screen, fake-camera path) against the running dev server |

`scripts/shot.mjs` uses your installed Chrome through `playwright-core`; `--fake-camera`
feeds Chrome's synthetic camera.

## Project structure

```
index.html            game entry (#game-canvas + #ui-root)
hall.html             second-screen Hall of Fame
src/
  main.ts             boots the App
  shared/             frozen contracts: types.ts, constants.ts, params.ts, pose.ts, rng, math
  app/                state machine, fixed-step loop, settings + leaderboard stores, booth broadcast
  vision/             camera, MediaPipe PoseLandmarker, One Euro smoothing, gestures, calibration, selfies
  game/               pure simulation: World, track generator, physics, power-ups, AutoPilot
  render/             Three.js scene, procedural art, runner animation, split-screen
  ui/                 DOM screens, HUD, webcam overlay, settings panel, leaderboard
  audio/              procedural Web Audio SFX and music
  input/              keyboard mapping
  hall/               Hall of Fame display
public/
  models/             pose_landmarker_lite.task, pose_landmarker_full.task
  mediapipe/wasm/     MediaPipe runtime (self-hosted, no CDN)
scripts/              check.sh, shot.mjs, copy-mediapipe.mjs
docs/                 pitch, demo script, booth checklist
```

## Architecture

See [MASTERPLAN.md](MASTERPLAN.md) for the full design: module contracts, frame loop, game
and gesture specs, and the multi-agent build plan. In short, the App pulls actions from
vision and keyboard, steps one pure `World` per player at 120 Hz, and pushes snapshots to
the renderer, UI and audio once per frame. The simulation has no DOM or Three.js, so it is
deterministic and unit-tested.

## Troubleshooting

| Problem | Try |
|---|---|
| "Camera not available" / black overlay | Allow camera in the browser's site settings. Close other apps using the camera (Zoom, Meet, Photo Booth). Or click **PLAY WITH KEYBOARD**. |
| Camera works on localhost but not on another device | Browsers only allow the camera on HTTPS or `localhost`. See Deployment. |
| No sound | Click once on the page (browsers block audio until a click). Check M (mute) and settings volumes. |
| Lanes flicker | Raise lane hysteresis or smoothing. Check that only the player is close to the camera. |
| Jumps not detected | Make sure your head has room above it in the overlay. Lower the jump threshold. Try the `full` model. |
| Random jumps or rolls | Raise jump / duck thresholds. Stand still during the countdown so calibration is clean. |
| "We lost you!" pause | You left the frame. Step back into view and it resumes after 3 s. |
| Countdown stays at 1 | Keep your head and shoulders in view, lower your hands, and stand still. For two players, both people must be visible and calibrated. Use **CAMERA SETTINGS** to change cameras if needed. |
| Low frame rate | Settings → quality `low`, pose model `lite`. Close other tabs. Check `?debug=1`. |
| GPU delegate fails | Vision falls back to CPU automatically; it is slower. |

## Deployment

The live build is at https://body-surfers-steel.vercel.app (Vercel). Because the folder name
contains a space, deploy with `npx vercel --prod --yes --name body-surfers`.

```bash
npm run build        # outputs dist/ (index.html + hall.html + assets)
npx vite preview     # local check
```

`dist/` is static and can go on any static host (GitHub Pages, Netlify, Vercel, S3).
`vite.config.ts` sets `base: './'` and assets resolve against the base URL, so it works
under a sub-path. **Serve over HTTPS**: `getUserMedia` refuses the camera on plain HTTP
anywhere other than `localhost`. The pose models are about 6 MB (lite) and 9 MB (full).

## Privacy

- Pose tracking runs entirely in the browser. Camera video never leaves the device.
- Crash selfies are small JPEGs stored only in this browser's `localStorage`, together with
  names and scores. Clear them from Settings → Clear leaderboard, or by clearing site data.
- The game itself has no backend and sends no data. However, MediaPipe's library sends
  anonymous usage pings to a Google logging endpoint (`odml.pa.googleapis.com`).

## Credits

- [MediaPipe Tasks Vision](https://github.com/google-ai-edge/mediapipe) and the BlazePose
  pose landmarker models, Apache-2.0, Google.
- [Three.js](https://threejs.org), MIT.
- Fonts [Lilita One](https://fonts.google.com/specimen/Lilita+One) and
  [Nunito](https://fonts.google.com/specimen/Nunito), SIL Open Font License 1.1, via Fontsource.
- All art, music and sound effects are procedural and generated in code.
- Inspired by Subway Surfers (SYBO / Kiloo). This is an unaffiliated fan project.

Built at a casual hackathon with friends. This repository does not include a license file.
