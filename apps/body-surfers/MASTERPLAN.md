# BODY SURFERS — Masterplan

> A Subway-Surfers-style endless runner you play with your whole body. A laptop webcam
> watches you: **step left/right to switch lanes, jump to jump, squat to roll.** Built for
> a hackathon demo booth: zero install, offline-capable, hands-free menus, 2-player races,
> and a selfie leaderboard of everyone's crash faces.

Working title: `GAME_TITLE` in `src/shared/constants.ts` (change it in one place).

## Changes since the plan

This document is the plan written before the build. The sections below are kept as written,
except that the numbers in §4 now match `src/game/config.ts`. What differs in the shipped game:

- **Speed:** 13 → 27 m/s on the eased curve (tau 90 s), plus an endless linear ramp of
  +0.05 m/s per second, capped at 48 m/s. The plan had 11 → 24 m/s with no ramp. Past
  27 m/s a `speedUp` event fires every further 3 m/s.
- **Power-ups:** 8 instead of 4. Added `jetpack` (7 s, fly 6 m up on a coin trail), `slowmo`
  (8 s, world at 60% speed), `star` (8 s, invincible, smashes obstacles, new `smash` event)
  and `coinRain` (10 s, dense coin lines). Jetpack and coin-rain coins are laid by `World`,
  not the generator, so both 2P tracks stay identical.
- **Harder tuning:** tighter row gaps (1.6 → 1.05 s), earlier pattern unlocks, power-ups
  every 200–320 m. See §4.
- **Copy my moves (`Settings.mimic`, default on):** the runner's arms and torso lean mirror
  the player's pose in camera mode (`MimicPose` in `RenderView`).
- **Tutorial once per browser:** after a run passes the tutorial section, a localStorage flag
  switches the tutorial off; turning it on in Settings clears the flag (`src/app/tutorial.ts`).
  Tutorial crashes are non-fatal (stumble + 1.2 s invulnerability).
- **Countdown fallback:** in camera mode, if nobody is in view for 8 s the run starts anyway
  and the keyboard steers (`APP.noViewFallbackS`).
- **Collisions** are swept, so nothing is skipped at high speed.
- **Hall of Fame** (`hall.html`) is built: leaderboard wall, now-playing panel, result
  celebrations, `?demo=1` and `?tv=1` modes.
- **Deployed** at https://body-surfers-steel.vercel.app.
- **Tests:** 238 Vitest unit tests and 12 Playwright end-to-end scenarios, plus a synthetic
  webcam person (`tools/fakecam`) for the vision path.

---

## 0. Decisions (locked with the user)

| Topic | Decision |
|---|---|
| Stack | Web + TypeScript: Vite 8, Three.js r186, MediaPipe Tasks Vision 1.1 (PoseLandmarker), Vitest 5, TS 7 |
| Lane control | **Body position = lane** (absolute zones, scaled by shoulder width, hysteresis) |
| Extras | Duck/roll via squat, power-ups (magnet, 2×, super sneakers, +hoverboard shield), selfie leaderboard, **2-player split-screen race** |
| Look | Classic Subway Surfers: bright daytime train yard, graffiti walls, colorful trains, cartoony runner — all procedural low-poly, zero downloaded art |
| Always-on | Coins/score, procedural audio + music, raise-both-hands to start/join/restart, live webcam overlay with skeleton + lane zones, keyboard fallback |

## 1. Demo story (what judges experience)

1. Laptop on a table, game fullscreen. Attract mode: an AI runner surfs the tracks behind
   the title; the webcam overlay shows passers-by as skeletons.
2. Someone steps in: "RAISE BOTH HANDS TO PLAY". A ring fills; if a second person is in
   frame they get 4 s to raise hands and **race** in split-screen on the identical course.
3. 3-2-1 countdown silently calibrates each player's neutral stance.
4. A 25-second scripted tutorial teaches JUMP → SQUAT → STEP with big pictogram hints.
5. Physical, sweaty, hilarious play. On crash the game snaps a **crash-face selfie**, shows
   score + rank, and puts the photo on the booth leaderboard.
6. Raise hands to go again. Nobody touches the keyboard.

## 2. Architecture

```
               ┌────────────────────────────── App (src/app) ───────────────────────────────┐
 camera ─► VisionSystem ──ActionEvent──►│ state machine · fixed-step loop · routing · UIModel builder │
 keyboard ─► KeyboardInput ─Action/Menu─►│ sessions: World[0..1] (+ attract World + AutoPilot)          │
                                        │ settings + leaderboard stores · selfie capture            │
               └──┬──────────────┬─────────────────┬──────────────────────┬────────────────────┘
                  │ step/actions │ RenderView[]    │ UIModel              │ GameEvent / UISound
                  ▼              ▼                 ▼                      ▼
             World (game)   Renderer (three)   UIManager (DOM)      AudioManager (WebAudio)
             pure logic     N viewports        overlay + webcam PiP  procedural sfx + music
```

**Principles**

- **Contracts first.** `src/shared/types.ts` + `src/shared/constants.ts` are frozen. Every
  module codes against interfaces; stubs exist so the whole project always type-checks.
- **No global event bus.** Explicit calls: App pulls from Vision (`update`) and Worlds
  (`snapshot`, `drainEvents`) and pushes to Renderer/UI/Audio once per frame.
- **Simulation is pure** (`src/game` has no DOM/Three.js) → unit-testable, deterministic
  (seeded RNG), headless-soakable with the AutoPilot.
- **Rendering is a view of state.** The renderer syncs meshes from `WorldSnapshot`s each
  frame (pooled, keyed by id). It never mutates game state.
- **UI is a view of `UIModel`.** The App rebuilds the model every frame; the UI diffs and
  touches the DOM only on change. UI → App only via `UICommand`s.
- **2P is not a bolt-on.** One `World` per player, same seed, one viewport each.
- **Offline by default.** Models, WASM, fonts are self-hosted (`public/`, `@fontsource`).

### 2.1 Frame loop (App, `requestAnimationFrame`)

```
now = performance.now(); dt = min(0.1, (now - last)/1000)
visionFrame = vision.update(now)          // may run inference; fires onAction callbacks synchronously
keyboard callbacks already queued actions
route queued ActionEvents → worlds[playerId].handleAction()   (only while 'playing' and player alive)
autopilot: for AI-driven worlds, a = ap.decide(world.snapshot()); if (a) world.handleAction(a)
acc += dt; while (acc >= SIM_DT && steps++ < 8) { worlds.forEach(w => w.step(SIM_DT)); acc -= SIM_DT }
events[i] = worlds[i].drainEvents() → audio.playGameEvent(...) + app logic (crash → selfie, winner, …)
renderer.render(views = worlds.map((w,i) => ({snapshot: w.snapshot(), events: events[i]})), dt)
ui.render(buildUIModel(), dt)
```

### 2.2 Coordinate system & units

- Meters and seconds everywhere. Render space: x right, y up, **−z forward**.
- The runner stays at z = 0. An object at track position `s` renders at
  `z = -(s - snapshot.distance)`; its far end (`s + length`) is further negative.
- Lane centers: `laneX(lane) = lane * LANE_WIDTH` (2.6 m). Lanes are −1, 0, 1 (left→right).
- Shared dimensions (collision == visuals): see `constants.ts` (`TRAIN_*`, `BARRIER_*`,
  `RAMP_*`, `PLAYER_*`, `obstacleTop()`, `obstacleWidth()`).

## 3. Directory layout & ownership

| Path | Owner | Notes |
|---|---|---|
| `MASTERPLAN.md`, `src/shared/**`, `package.json`, configs, `scripts/**`, `public/**` | Orchestrator | frozen for agents |
| `src/vision/**`, `sandbox/vision.*` | **Vision agent** | camera, pose, assignment, gestures, calibration, selfies |
| `src/game/**` | **Gameplay agent** | World sim, physics, generator, power-ups, AutoPilot |
| `src/render/**`, `sandbox/render.*` | **Render agent** | Three.js scene, art, animation, split-screen |
| `src/ui/**`, `sandbox/ui.*` | **UI agent** | screens, HUD, webcam overlay, settings, leaderboard view |
| `src/audio/**`, `src/input/**`, `sandbox/audio.*` | **Audio+Input agent** | procedural SFX/music, keyboard mapping |
| `src/app/**`, `src/main.ts`, `index.html` (head/body content only; keep `#game-canvas` + `#ui-root`) | **App agent** | state machine, loop, stores, wiring, booth broadcast |
| `hall.html`, `src/hall/**` | **Booth Display agent** | second-screen Hall of Fame (leaderboard cards + live feed via `BoothMessage`) |
| `e2e/**` | **E2E agent** | Playwright scenario runner + perf harness |
| `tools/fakecam/**`, `test-media/**` | **Fake-camera agent** | synthetic person as a webcam for headless pipeline tests |
| `README.md`, `docs/**` | **Docs agent** | README, Devpost pitch, demo script, booth checklist |
| — (read-only) | **Reviewers** | integration reviewer, gesture researcher, game-feel analyst → findings to orchestrator |

Each module exposes its main class via `src/<module>/index.ts` with the **exact class name
and constructor signature of its stub**:
`new VisionSystem()`, `new World(playerId)`, `new AutoPilot()`, `new Renderer()`,
`new UIManager()`, `new AudioManager()`, `new KeyboardInput()`, `new App(params)`.
Modules may only import from `src/shared/**`, their own folder, and npm packages — with one
exception: **the App imports every module's index**. (Sandboxes may import their own module.)

Module-specific tunables go in `src/<module>/config.ts`.

## 4. Game design spec (owner: Gameplay agent)

All numbers are starting values — tune with tests, the AutoPilot soak and playtests.

### 4.1 Speed & difficulty
- `speed(t) = min(48, 13 + (27 - 13) * (1 - exp(-t / 90)) + 0.05 * t)` m/s
  (22.8 @ 60 s, 29.3 @ 120 s, 34.1 @ 180 s, 41.5 @ 300 s, cap 48 at ~423 s). `t` is
  difficulty time, which starts after the tutorial.
  (Original plan: `11 + (24 - 11) * (1 - exp(-t / 110))`, no ramp, aiming at 1–3 minute runs.)
- `speedLevel = clamp((speed - 13) / (27 - 13), 0, 1)`. Emit `speedUp` at levels 0.25 / 0.5 / 0.75
  (level 1/2/3), then once per further 3 m/s above 27 until the cap.
- During the tutorial the speed stays at 13.
- Obstacle row spacing (time between decision points): `gap = lerp(1.6, 1.05, speedLevel)` s,
  spacing = `max(14, speed * gap)` m, measured from the previous row's **far end** (trains are long).

### 4.2 Runner physics (fixed step `SIM_DT = 1/120`)
- Jump: apex 1.45 m, airtime 0.70 s → `g = 23.7 m/s²`, `v0 = 8.29 m/s`.
- Super sneakers jump: apex 4.0 m (clears `TRAIN_HEIGHT` 3.2 → can land on roofs), `v0 = sqrt(2·g·4.0)`.
- Roll: 0.65 s; collision height `PLAYER_ROLL_HEIGHT`. Duck while airborne → `vy = -22` (fast fall) and roll on landing.
- Lane change: 0.17 s per lane (eased lateral motion; `x` is continuous, collisions use real `x`).
- Input buffering: jump pressed ≤ 0.15 s before landing executes on landing; coyote time 0.1 s after running off a roof.
- Forgiveness: shrink lateral overlap by 0.15 m and obstacle near faces by 0.1 m for collision.

### 4.3 Obstacles & collision
| Kind | Pass by | Crash when (z & lateral overlap) |
|---|---|---|
| `barrier_low` | jump | feet `y < BARRIER_LOW_HEIGHT - 0.1` |
| `barrier_high` | roll | top of runner (`y + (rolling ? ROLL : PLAYER_HEIGHT)`) `> BARRIER_HIGH_CLEARANCE` and `y < BARRIER_HIGH_TOP` |
| `train` (static or moving) | change lane, or be on its roof | reached its front with `y < TRAIN_HEIGHT - 0.45`; within 0.45 m below the roof → snap onto the roof (forgiving) |
| `ramp` | run up it → roof of the train behind it | never fatal; ground height = `TRAIN_HEIGHT * (p - s) / RAMP_LENGTH` |

- **Rooftops:** ground height function over (lane x, track pos). On a roof the runner stays at
  `TRAIN_HEIGHT`; past the far end or into a lane without a train at that z → falls.
  Moving laterally roof→adjacent roof is allowed.
- **Side bumps (position-control friendly):** the world tracks `desiredLane` (from input) and
  `lane`. If moving toward `desiredLane` would enter a train/ramp side above the runner's
  feet + 0.45, emit **one** `blocked` event, keep the lane, set `player.blockedDir`, and **wait**:
  as soon as the path clears, perform the pending move automatically. Never fatal, never
  repeated stumbles from one held position. Keyboard `move` sets `desiredLane = clamp(lane+dir)`.
- **Moving trains:** `s` decreases by `speed·dt`; unlock at `speedLevel ≥ 0.15`; 5–8 m/s;
  always leave another lane open; emit `trainHorn` once when time-to-reach-runner < 3.5 s.
- Obstacle ids are unique per World instance **across resets** (never reset the counter).

### 4.4 Track generator (seeded, deterministic)
Pattern library (weighted; some unlock with difficulty):
1. `single_low` / `double_low` — low barriers in 1–2 lanes.
2. `full_low` — low barriers in all lanes (must jump).
3. `single_high` / `full_high` — overhead bars (must roll).
4. `train_single` — 1–3-car train in one lane; `train_double` — two lanes, free lane gets coins.
5. `ramp_train` — ramp + 2–3-car train; coins on the roof (unlock `speedLevel ≥ 0.06`).
6. `train_wall_ramp` — trains in all 3 lanes, one has a ramp (must climb) (unlock 0.12).
7. `mixed` — train + barrier + free lane combos.
8. `moving_train` — see above (unlock 0.15).
9. `zigzag` — staggered trains forcing 2 consecutive lane changes (unlock 0.3).

(`full_high` unlocks at 0.03. At level 1, hard patterns weigh 2.2× and singles 0.65×.)

**Solvability invariants** (unit-tested over 10k generated rows across seeds):
- Every row has at least one lane passable by staying in it, or all its blocked lanes are uniformly jumpable/rollable.
- A runner beside a long train always has a passable option in its lane until the train ends + reaction distance.
- Never a low barrier and a high barrier in the same lane closer than (airtime + roll time) × speed + 4 m.
- Rows that require lane changes leave ≥ 1.3 s at current speed to react (+0.4 s per extra lane), accounting for 0.17 s/lane.

**Coins:** lines of 5–12 at 2.4 m spacing in free lanes; arcs over low barriers following the
jump parabola; on roofs at `TRAIN_HEIGHT + COIN_HEIGHT`; along ramps.
**Power-ups:** first one 120–240 m after the tutorial (or start), then one every 200–320 m,
in a free lane. Weights: magnet .18, multiplier .16, jetpack .14, sneakers .12, hoverboard .10,
slowmo .10, star .10, coinRain .10.

### 4.5 Power-ups
| Kind | Duration | Effect |
|---|---|---|
| `magnet` | 12 s | coins within 14 m ahead (any lane) fly to the runner (~30 m/s) |
| `multiplier` | 15 s | score multiplier ×2 |
| `sneakers` | 12 s | super jump (land on trains) |
| `hoverboard` | 25 s or one crash | absorbs one crash → `hoverboardBreak`, 1.5 s invulnerability (`player.invulnerable`) |
| `jetpack` | 7 s | fly at 6 m feet height above the trains along a sky coin trail; invulnerable during descent + 0.8 s after landing (added after the plan) |
| `slowmo` | 8 s | world scrolls at 60% speed, 0.3 s ease in/out (added after the plan) |
| `star` | 8 s | invincible; obstacles hit are smashed and removed (`smash` event); 0.8 s grace after (added after the plan) |
| `coinRain` | 10 s | dense coin lines laid 110 m ahead at 2.4 m spacing (added after the plan) |

Emit `powerUpWarning` once 2 s before expiry and `powerUpEnd` at expiry. Re-pickup refreshes duration.

### 4.6 Scoring
- `score += speed·dt·multiplier` (≈ 1 pt/m) + `10 × multiplier` per coin. `coins` counts coins.
- `coin` events carry `combo` (consecutive coins; resets after a 0.6 s gap) — audio pitches up.
- `milestone` every 500 m.

### 4.7 Tutorial (WorldOptions.tutorial)
Scripted rows (track positions from start): 55 m `full_low` → hint `jump`; 105 m `full_high`
→ `duck`; 160 m train center + coin lines left/right → `switch`; 225 m trains left+center →
`right`; 290 m trains right+center → `left`; random generation from 350 m.
`snapshot.hint` = the action the runner still needs for the nearest unpassed tutorial row
within 2.6 s, computed against the current lane (`null` if already safe).

### 4.8 Run lifecycle
`reset(opts)` → status `ready` (runner idles at start; renderer shows an idle bounce) →
`start()` → `running` → crash → `dead` (scrolling stops; `step` keeps ticking `animTime`
so the death anim plays). Attract worlds (`attract: true`): no tutorial, no moving trains.

### 4.9 AutoPilot
Deterministic look-ahead (~2.5 s): per lane find the next blocking obstacle and the action
that passes it; choose the safest lane (prefer staying, then coins, then ramps when walled);
issue `move` one step at a time (≥ 0.12 s apart); jump when a low barrier's near face is
~`speed·0.28` m ahead, duck at ~`speed·0.30` m for high bars. Acceptance: survives the
tutorial 100% and ≥ 90 s in ≥ 9/10 seeds (also our generator soak test).

## 5. Vision & gesture spec (owner: Vision agent)

### 5.1 Pipeline
- `FilesetResolver.forVisionTasks(assetUrl(ASSET_PATHS.mediapipeWasm))`,
  `PoseLandmarker.createFromOptions` with `baseOptions.modelAssetPath = assetUrl(ASSET_PATHS.poseModel(model))`,
  `delegate: 'GPU'` (fall back to `'CPU'` on failure), `runningMode: 'VIDEO'`,
  `numPoses = playerCount + 1` (max 3; the extra lets us ignore spectators),
  confidences 0.5. Use the synchronous `detectForVideo(video, timestampMs)`; timestamps strictly increasing.
- Camera: `getUserMedia({ video: { deviceId?, width: {ideal:1280}, height: {ideal:720}, frameRate: {ideal:60}, facingMode: 'user' }, audio: false })`.
  `<video playsinline muted autoplay>` appended to `document.body`, visually hidden but **not**
  `display:none` (opacity 0, 1×1 px, pointer-events none).
- Run inference only when a new video frame is available (`requestVideoFrameCallback` flag or
  `video.currentTime` change). Track `inferenceMs` and inference `fps` (EMA).
- Smoothing: One Euro filter per landmark x/y. `tuning.smoothing` 0..1 → minCutoff 3.0..0.4 Hz,
  beta ≈ 0.6, dCutoff 1.0. Reset a slot's filters when its identity changes.
- Display space: `x = mirror ? 1 - rawX : rawX` (default mirror on).

### 5.2 Body features (per person)
- `shoulderMid`, `shoulderWidth`; hips used only if both hip visibilities > 0.5.
- `scale = smoothedEMA(max(shoulderWidth, 0.55 · torsoLen_if_hips))` (τ ≈ 0.8 s) — robust to body rotation.
- Anchor: `x = hips ? 0.5·shoulderMid.x + 0.5·hipMid.x : shoulderMid.x`; `y = shoulderMid.y`.
- Works upper-body-only (laptop cameras often cut off at the hips).

### 5.3 Calibration (`startCalibration(slot)`, during the countdown)
- Accumulate frames with confidence > 0.6, hands down, anchor speed < 0.5 scale/s.
  `calibrationProgress = accumulated / 1.0 s`.
- On completion: `centerX = median(anchor.x)`, `baselineY = median(anchor.y)`; clamp `centerX`
  so both side-lane boundaries stay inside [0.03, 0.97]; phase → `ready`; **emit the current
  lane** immediately.
- If not complete 3 s after `startCalibration`, relax the stillness requirement (accept any
  visible hands-down frames) so it finishes within ~1 s more.

### 5.4 Lane (absolute zones with hysteresis)
`dx = (anchor.x - centerX) / scale`, T = `laneThreshold` (0.9), H = `laneHysteresis` (0.3):
- From 0: `dx > T` → +1; `dx < -T` → −1. From +1: `dx < T - H` → 0 (or `< -T` → −1). Mirror for −1.
- Condition must hold ≥ 50 ms (debounce). Emit `{type:'lane'}` on change.
- `GestureState.laneHalfWidth = T · scale`.

### 5.5 Jump / duck
`dy = (baselineY - anchor.y) / scale` (positive = higher), `vUp` = upward anchor speed in scale/s.
- **Jump:** phase `ready`, `dy > jumpThreshold` (0.3) **and** `vUp > 1.5` (velocity gate kills slow
  drift), not within 250 ms of the last jump/duck → emit `jump`, phase `airborne` until
  `dy < 0.4·J` for 80 ms (or 1.2 s timeout). Lanes keep updating while airborne.
- **Duck:** phase `ready`, `dy < -duckThreshold` (0.45) sustained 60 ms → emit `duck`, phase
  `ducking` until `dy > -0.5·D`. One duck per squat.
- **Landing dip guard:** suppress ducks for 300 ms after `airborne` ends.
- Rising out of a squat never triggers a jump (needs `dy > +J`, i.e. above baseline).
- **Baseline drift:** in `ready` with `|dy| < 0.15` and `|vUp| < 0.5` for 500 ms,
  `baselineY = damp(baselineY, anchor.y, 0.5/s)`. `centerX` never drifts (fixed lanes).
- `jumpLineY = baselineY - J·scale`, `duckLineY = baselineY + D·scale` for the overlay.

### 5.6 Hands-up (menus)
Both wrists above the nose by 0.1·scale (visibility > 0.4), **or** both elbows above the
shoulders by 0.2·scale (wrists often leave the frame). `handsUpProgress += dt/HANDS_UP_HOLD_MS`
while up, decays 3× faster when down.

### 5.7 Multi-person slot assignment (pure, unit-tested)
- Candidates: confidence > 0.5 and scale > 0.04.
- **Unlocked:** take the `playerCount` largest-scale candidates (players stand closest), assign
  slots by x (left → slot 0). Everyone else = spectator (`extraPeople`).
- **Locked:** match each slot to the nearest candidate center (distance < 2.5·scale). A slot
  that has never been filled acquires the largest unassigned candidate. A slot that lost its
  person re-acquires only a candidate within 3·scale of its last known center.
- `lockSlots(true, order)` remaps slots per §types; `lockSlots(false)` unlocks.

### 5.8 Positioning hints
`too-close`: scale > 0.30 or a shoulder within 0.03 of an edge. `too-far`: scale < 0.06.
`head-cut-off`: nose.y < 0.10 (no headroom to jump — tilt the screen back / step back).
`move-left`/`move-right`: not enough room for the far side lane
(`center.x ± 1.6·T·scale` outside [0.03, 0.97]). `low-visibility`: confidence < 0.55.

### 5.9 Selfies
`captureSelfie(slot, size = 192)`: square crop around the nose sized
`max(3.2 × ear distance, 1.4 × shoulderWidth)` px, shifted up 15%, clamped to the frame,
mirrored to match display, JPEG q≈0.82 data URL (~10–20 KB).

### 5.10 Code structure (suggested)
`CameraManager`, `PoseTracker` (MediaPipe wrapper), `OneEuroFilter`, `features.ts`,
`GestureDetector` (**pure**: landmarks + t → state + events), `PlayerAssigner` (**pure**),
`hints.ts`, `selfie.ts`, `VisionSystem` (facade), `synthetic.ts` (test body generator).

## 6. Rendering & art spec (owner: Render agent)

- `WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' })`,
  pixel ratio `min(devicePixelRatio, quality === 'high' ? 2 : 1)`, `outputColorSpace = SRGB`,
  ACES or neutral tone mapping, soft shadows on high quality.
- **Split-screen:** one Scene + camera per view (P1 left, P2 right) sharing geometries,
  materials and textures; render each with `setViewport`/`setScissor`. For narrow aspects,
  derive vertical FOV from a target horizontal FOV so all three lanes stay visible.
- **Camera rig:** behind/above the runner (~`(x·0.85, 3.4 + y·0.6, 7.2)` looking at
  `(x·0.85, 1.4 + y·0.6, -10)`), damped follow; FOV 62°→72° with `speedLevel`; shake on
  `blocked`/`crash`/`hoverboardBreak`.
- **Look (classic SS):** sky gradient `#5cc8ff → #dff4ff` + drifting low-poly clouds; fog to
  the horizon color (near ~70 m, far ~`VIEW_DISTANCE`); hemisphere + warm sun (shadows follow
  the runner); toon-ish materials (`MeshToonMaterial` + 3-step gradient map, or Lambert on low).
- **Environment** (scrolling 40 m segments, decoration seeded by segment index so recycling
  never pops): gravel bed, 3 tracks (steel rails + wooden sleepers via `InstancedMesh`),
  concrete side walls with **procedural graffiti** (CanvasTexture: bubble letters, tags,
  spray blobs), pastel apartment blocks with window textures beyond the walls, catenary
  poles + wires, signal lights, fences, bushes.
- **Models** (procedural, merged geometry where possible): trains (5 liveries via
  `variant`: red/yellow, blue/white, green/white, yellow/blue, purple/yellow; windows, doors,
  headlights, couplings between 13 m cars; moving trains get bright headlights), low barrier
  (red/white striped hurdle), high barrier (yellow/black overhead bar on posts), ramp (yellow
  with black chevrons), coin (spinning gold disc, emissive), power-ups (magnet U, "2×"
  badge, sneaker, hoverboard; float + spin + glow ring).
- **Runner** (cartoon, ~1.75 m, big head, backwards cap, hoodie in `PLAYER_COLORS[playerId]`,
  jeans, white sneakers): procedural animations — run cycle (frequency ∝ speed), idle
  bounce (status `ready`), jump tuck, roll (ball spin), lean into lane changes, push-against
  when `blockedDir ≠ 0`, stumble wobble, death fall; hoverboard under feet when active;
  flashing while `invulnerable > 0`.
- **Effects:** coin sparkle burst, landing dust, crash stars/impact flash, magnet field
  shimmer, speed lines at high `speedLevel` (high quality only).
- **Sync:** `Map<id, Object3D>` per view with pools per kind; detect resets (seed change or
  distance decreasing) and clear maps.
- **Budget:** 60+ fps at 1440p on M-series with 2 views; `< 300` draw calls per view;
  zero per-frame allocations in the hot path.
- **Sandbox:** `sandbox/render.html` + `sandbox/render.ts` with its **own fake snapshot
  generator** (independent of `src/game`) showing every obstacle/collectible kind, cycling
  runner animations, keys to toggle 1P/2P + quality.

## 7. UI/UX spec (owner: UI agent)

**Visual language:** classic SS — chunky white **Lilita One** display type with thick navy
outlines (`#1b1b2f`), **Nunito** body text, sunny yellow `#ffd23f`, sky blue `#1e90ff`,
red `#ff4757`, green `#2ed573`; sticker-like panels with slight tilt, 3D pill buttons.
`#ui-root` is `pointer-events: none`; interactive elements opt in.

| Screen | Content |
|---|---|
| `boot` | Logo, loading bar (`boot.progress`, `message`), giant "TAP TO START" when `needsClick`; on `error`: friendly text + "PLAY WITH KEYBOARD" (`keyboard-only`) + tips (allow camera, close other apps using it). Safety line: "Clear some space around you!" |
| `lobby` | Logo; animated "RAISE BOTH HANDS TO PLAY" pictogram; per-person chips (P1/P2 color, ring = `handsUpProgress`, check when `joined`, hint text); `joinCountdown` banner "PLAYER 2 — RAISE YOUR HANDS TO RACE! 3…"; leaderboard panel with photos; keyboard legend. Webcam overlay **large** here. |
| `countdown` | Huge 3 / 2 / 1 / GO! pop; "Stand still — calibrating" + per-player progress. |
| `playing` | Per-player HUD: score (big), coins, ×2 badge, power-up timers with progress, tutorial hint banner with pictogram (⬆ JUMP!, ⬇ SQUAT!, ⬅ STEP LEFT!, ➡ STEP RIGHT!, ↔ SWITCH LANES!), toast. 2P: halves with a divider, colored P1/P2 labels, "OUT" over crashed halves, crown on `winner`. |
| `paused` | Dim; `user` → "PAUSED — raise both hands or press P"; `tracking-lost` → "WE LOST YOU! Step back into view" + missing players; `resumeIn` countdown. |
| `gameover` | "CRASHED!"; polaroid crash selfie; score/coins/distance/time; rank badge ("#3!" / "NEW RECORD!"); editable name (prefilled fun name, `rename` on Enter/blur); leaderboard with the new entry highlighted; restart ring (`handsUpProgress`, greyed during `restartLockout`). 2P: side-by-side results, winner crown. |

- **Webcam overlay** (canvas): mirrored video when `settings.vision.mirror` (landmarks are
  already in display space — draw them as-is), skeleton per person in
  `PLAYER_COLORS[slot]` + head circle, lane boundaries (dashed verticals at
  `centerX ± laneHalfWidth`, active zone tinted), jump line (green) and duck line (orange),
  "JUMP!/SQUAT!/⬅/➡" flash for 400 ms after `lastGesture`, tracking dot, spectators
  count. Bottom-right ~22 vw while playing (hidden if `!settings.showWebcam`), large in
  lobby/countdown. Placeholder when `video` is null.
- **Settings panel** (`settingsOpen`): camera select, model lite/full, quality, mute,
  volumes, tutorial, show webcam, vision tuning sliders with live values
  (laneThreshold 0.5–1.5, laneHysteresis 0.1–0.6, jumpThreshold 0.15–0.6,
  duckThreshold 0.25–0.8, smoothing 0–1, mirror), reset defaults, clear leaderboard with an
  **inline** confirm (never `window.confirm`).
- **Debug overlay** when `debug` is non-null; **toast** visible for 1.8 s after `toast.t`.
- **Sandbox:** `sandbox/ui.html` + `ui.ts` with fake `UIModel`s for every screen (1P and
  2P), a canvas-generated fake video and fake landmarks, and a screen switcher.

## 8. Audio spec (owner: Audio+Input agent)

Procedural Web Audio only; master → music bus + sfx bus (+ compressor). In 2P,
`StereoPannerNode` pans P1 left / P2 right.
- SFX: coin (bright blip, pitch rises with `combo`), jump (rising sweep; super = bigger),
  land (thump), roll (filtered noise whoosh), laneChange (airy whoosh), blocked (thud +
  boing), crash (noise burst + falling tone), powerUp (arpeggio), powerUpWarning (blips),
  powerUpEnd (down-chirp), hoverboardBreak (glass-ish crack), trainHorn (two-tone
  sawtooth horn), speedUp (whoosh-up), milestone (chime). UI: click, tick, go, handsUp,
  join, pause, resume, gameOver, newRecord (fanfare), win. `setChargeLevel` = rising tone.
- Music: upbeat 4-on-the-floor loop ~124 BPM via a lookahead scheduler (kick, snare/clap,
  hats, bass, chord stabs, simple lead); modes lobby (chill), game (full), gameover (sparse),
  off; `setIntensity` raises tempo up to ~+12% / opens a filter.
- Must never throw if `unlock()` wasn't called (silently no-op).

## 9. Keyboard spec (owner: Audio+Input agent)

Use `KeyboardEvent.code`; ignore `repeat`; ignore events whose target is
input/textarea/select/contentEditable; `preventDefault` on arrows/space.
- 1P (player 0): ←/A move −1, →/D move +1, ↑/W/Space jump, ↓/S/Shift duck.
- 2P: A/D/W/S + Space(jump) + ShiftLeft(duck) → player 0; arrows + ShiftRight(duck) → player 1.
- Menu keys (always emitted alongside actions): Enter/Space confirm, 1 one-player,
  2 two-player, P/Escape pause, Backspace/Q back, M mute, F fullscreen, ` settings, I debug.

## 10. App spec (owner: App agent)

### 10.1 State machine
- **boot:** parse params; load settings (localStorage, merged over `DEFAULT_SETTINGS`,
  tolerate corrupt JSON); init renderer (attract world renders behind the boot screen),
  UI, keyboard. Camera mode: `vision.loadModel()` immediately (progress → boot). Show
  "TAP TO START" (`needsClick`) — required for audio + camera. On `boot-click` (or any
  confirm key): `audio.unlock()`, `vision.startCamera(settings.cameraId)` → **lobby**; on
  failure show `boot.error` with keyboard fallback. `?input=keyboard`: no vision at all.
- **lobby:** attract World (`attract: true`) driven by AutoPilot, 1 view, silent; reset it 2 s
  after it dies. `vision.setPlayerCount(params.players ?? 2)`, `lockSlots(false)`. When a
  person's `handsUpProgress ≥ 1` → mark joined (`join` sound) and `lockSlots(true)` (freeze
  identities). If another person is visible, open a 4 s join window (`joinCountdown`); if
  they join or the window ends → countdown with the joined slots. Only one person visible →
  countdown immediately. Keyboard: confirm/1 → 1P, 2 → 2P.
- **countdown:** `lockSlots(true, joinedSlotsInOrder)`; `renderer/keyboard.setPlayerCount(n)`;
  `seed = params.seed ?? randomSeed()`; `worlds[i].reset({seed, tutorial: settings.tutorial})`
  (same seed for all); `vision.startCalibration(i)`; 3 s with `tick`s; if calibration isn't
  done at 0, hold at "Stand still…" (max +5 s, then go anyway). At GO: `go` sound,
  `worlds.start()`, apply `{type:'lane', lane: gestures[i].lane}` → **playing**.
- **playing:** route actions; step; handle events. On `crash` of player i: capture
  `vision.captureSelfie(i)` ~180 ms later; mark `out`; in 2P the first crash makes the other
  player `winner` (toast "P2 WINS — KEEP GOING!", `win` sound). All players dead → 1.5 s →
  **gameover**. Camera mode: an alive player missing from `frame.people` for > 700 ms →
  **paused** (`tracking-lost`). `pause` key or tab hidden → paused (`user`).
  `audio.setIntensity(max speedLevel)`; toast "SPEED UP!" on `speedUp`.
- **paused:** worlds frozen (render last snapshots). `tracking-lost`: when all alive players are
  visible again for 1 s → `resumeIn` 3 s → re-apply lanes → playing. `user`: pause key or
  hands-up → `resumeIn` 3 s. Back key → lobby.
- **gameover:** build `PlayerResult`s; insert qualifying scores into the leaderboard (top
  `MAX_LEADERBOARD`, fun random name e.g. "Turbo Pickle"); `newRecord` if rank 1, else
  `gameOver`. `restartLockout` 2.5 s, then hands-up (a player slot) or confirm → countdown
  with the same players (fewer if someone left). All players missing for 3 s, back key, or
  25 s idle → lobby.

### 10.2 Other responsibilities
- Fixed-step loop (§2.1), max 8 sub-steps/frame. Keyboard and camera inputs are both always
  live. `?autopilot=1` → AutoPilot drives every player (ignore their human inputs).
- `?autostart=1` → skip boot click and lobby: straight to countdown with
  `params.players ?? 1` players (camera mode starts the camera without a click; audio stays
  locked; calibration waits are skipped when there is no camera or autopilot is on).
- Settings changes: `vision.setTuning`, `audio.setMuted/setVolumes`, `renderer.setQuality`,
  `vision.loadModel` on model change, `vision.startCamera(id)` on camera change; persist.
- `UIModel.cameras` from `vision.listCameras()` after the camera starts.
- Leaderboard store (`localStorage`, `STORAGE_KEYS.leaderboard`), rename, clear.
- Fullscreen on `fullscreen`; mute toggle; settings/debug toggles; window resize → `renderer.resize`.
- Debug stats (fps EMA, frame ms, vision fps/inference ms, draw calls).
- **Test handle:** `window.__bodySurfers = { app, state() }` where `state()` returns
  `{ screen, playerCount, worlds: [{status, distance, score, coins, lane, y}], visionStatus }`.

## 11. Quality gates & testing

| Gate | Command / method |
|---|---|
| Types | `npm run typecheck` — zero errors project-wide |
| Unit | `npm test` — vision gestures on synthetic landmark sequences (still → 0 events; jump → exactly 1; squat → 1; step → lane; jitter at a boundary → no flapping; walking closer → no jump/duck; landing dip → no duck; hands-up; 2-person assignment incl. a spectator walking behind); game (physics apex/airtime, collisions per kind, rooftops/ramps, blocked-wait logic, power-ups, generator invariants over 10k rows, determinism, AutoPilot soak) |
| Build | `npm run build` |
| Visual | `node scripts/shot.mjs --url http://localhost:5173/sandbox/{render,ui}.html …` — agents read their own PNGs |
| Smoke | `/?input=keyboard&autostart=1&autopilot=1` (1P and `&players=2`) 20 s, zero page errors, distance increasing via `__bodySurfers.state()`; camera path with `--fake-camera` |
| Human | real camera playtest on the user's MacBook, tuning via the settings panel |

## 12. Orchestration plan

- **Phase 0 — Scaffold (orchestrator, done):** deps, offline assets, configs, frozen
  contracts, stubs, screenshot tool, shared dev server at `http://localhost:5173`.
- **Phase 1 — Parallel build (6 agents):** Vision · Gameplay · Render · UI · Audio+Input · App.
- **Phase 1b — Expansion (7 agents):** Booth Display · E2E/perf · Fake camera · Docs/pitch, plus
  read-only Integration reviewer · Gesture researcher · Game-feel analyst whose findings the
  orchestrator routes to the owning build agents.
- **Phase 2 — Integration (orchestrator):** typecheck/tests/build, smoke tests, screenshots,
  fix-ups routed back to owning agents.
- **Phase 3 — QA & polish (parallel agents):** adversarial code review (gesture logic,
  collisions, state machine), gameplay balance via AutoPilot soak, visual polish from
  screenshots, performance pass with 2 views + inference.
- **Phase 4 — Human playtest & tuning, README + demo-day checklist.**

### Rules of engagement (all agents)
1. Edit only the paths you own (§3). Never edit `src/shared/**`, configs, `package.json`.
   Need a contract change? Work around it and propose it in your report.
2. Don't install packages. Available: `three` (+ `three/examples/jsm/*`),
   `@mediapipe/tasks-vision`, `@fontsource/lilita-one`, `@fontsource/nunito`, `vitest`,
   `playwright-core` (via `scripts/shot.mjs`).
3. A shared dev server runs at `http://localhost:5173` with HMR. Don't start another on
   5173; if it's down, use your assigned port with `--strictPort` and kill it after.
4. Visual checks via `node scripts/shot.mjs` (headless system Chrome, hardware WebGL); read
   the PNGs yourself. **Do not use the claude-in-chrome browser tools** (user's browser).
5. Type-check with `npx tsc --noEmit -p .`; errors in other owners' files aren't yours, but
   your files must be clean. Test with `npx vitest run src/<module>`.
6. Never use `alert/confirm/prompt`. No per-frame console logging. Avoid per-frame
   allocations in hot paths. Named exports only. Strict TS, no `any` unless justified.
7. Final report (< 300 words): files, how to verify, deviations, contract proposals, known issues.

## 13. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Camera latency / false triggers | velocity-gated jumps, hysteresis lanes, debounce, landing-dip guard, generous game timing (0.7 s airtime, forgiveness margins, input buffering), live tuning sliders |
| Venue lighting / framing | positioning hints, overlay with zones, `full` model option, recommend facing the light and tilting the screen back; Continuity Camera (iPhone) selectable |
| Spectators walking behind | extra pose slot + size-ranked, identity-locked assignment |
| Wifi | everything self-hosted; no CDN at runtime |
| Integration drift between 6 parallel agents | frozen contracts + stubs + project-wide typecheck + sandboxes + integration phase |
| 2P complexity | designed in from day one; 1P path independent; 2P isolated in the join flow |
| Physical safety | "clear some space" prompt; side-lane zones scale with distance so players needn't lunge |
| Fatigue / throughput | difficulty ramps so typical runs last 1–3 min |

## 14. Demo-day checklist
- `npm run build && npx vite preview` (or deploy `dist/` to any static host — HTTPS needed off-localhost).
- Fullscreen (F), face a light source, laptop at waist height or raised, screen tilted back.
- Mark a standing spot ~2 m from the laptop with tape; 2P spots ~1.5 m apart.
- Settings (`) → pick camera, check tuning; clear the leaderboard before judging starts.

## 15. Stretch backlog (after everything above works)
~~Jetpack power-up~~ (shipped, see §4.5), character skins, daily seed, QR code to download your crash selfie,
achievements, spectator-cam replay of the crash, haptic-free "boost" by clapping.
