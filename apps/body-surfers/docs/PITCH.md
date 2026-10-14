# BODY SURFERS: Devpost write-up

**Tagline:** Subway Surfers, but you are the controller. Step, jump and squat in front of
your laptop webcam to dodge trains, and race a friend in split-screen.

## Inspiration

Everyone has played Subway Surfers with a thumb. We wanted to know what it feels like with
your whole body: actually jumping over the barrier, actually ducking under the bar. Motion
games usually need a Kinect, a console or a VR headset. Almost every laptop has a webcam,
and pose estimation now runs in real time in a browser tab. We wanted something you could
walk up to at a booth and play in five seconds, with no install and nothing to hold.

## What it does

- A 3D endless runner in the style of Subway Surfers: three lanes, trains, barriers, ramps
  onto train roofs, coins, and eight power-ups: magnet, 2x score, super sneakers, hoverboard,
  jetpack (fly over the trains on a coin trail), slow-mo, star (invincible, smash through
  obstacles) and coin rain.
- An endless speed ramp: 13 m/s at the start, easing toward 27 m/s, then +0.05 m/s every
  second up to a 48 m/s cap.
- **Your body is the controller.** Where you stand picks the lane. Jump to jump, squat to
  roll. Lane zones and jump/squat lines are scaled to your body size, so it works whether
  you stand 1.5 m or 3 m away.
- **Hands-free menus.** Raise both hands to start, join, restart or resume. Nobody touches
  the laptop.
- **2-player races.** A second person in frame gets 4 seconds to raise their hands and join.
  Both players run the identical course side by side.
- **Crash selfies.** When you crash, the game snaps your face mid-crash and puts it on a
  photo leaderboard. A second-screen "Hall of Fame" page shows the board and live scores.
- A live webcam overlay shows your skeleton, lane zones and jump/duck lines, so players see
  what the game sees.
- "Copy my moves": the runner's arms and upper body mirror the player's (toggle in Settings).
- 3-second calibration hidden in the countdown, and a roughly 27-second tutorial that teaches
  each move. It shows once per browser.
- Keyboard fallback and an AI autopilot for when the camera or the lighting does not cooperate.

## How we built it

- **TypeScript + Vite** web app, fully static, no backend.
- **MediaPipe Tasks Vision** PoseLandmarker (BlazePose, lite or full model) running on the
  GPU delegate in the browser. Models and WASM are self-hosted, so it runs without wifi.
- **Gesture layer:** One Euro filters on landmarks; a torso anchor (shoulders blended with
  hips); absolute lane zones with hysteresis and debounce; jumps that need both height and
  upward velocity, which removes false triggers from slow drift or walking toward the
  camera; a guard against the landing dip being read as a squat. Multi-person slot
  assignment ranks people by size and locks identities so spectators behind the players
  are ignored.
- **Pure simulation:** the game world has no DOM or Three.js dependencies. It runs at a fixed
  120 Hz step with a seeded RNG, so both players in a race get exactly the same track. A
  deterministic AutoPilot plays the game for attract mode and soak tests, and the track
  generator is unit-tested for solvability over thousands of rows.
- **Three.js r186** rendering with all art made procedurally in code: low-poly trains with
  five liveries, canvas-generated graffiti, a cartoon runner with procedural animation, and
  split-screen viewports.
- **Procedural Web Audio** for every sound and the music loop. No audio files.
- **Contract-first multi-agent build with Claude Code.** We first wrote a masterplan and a
  frozen set of shared TypeScript contracts (`src/shared/types.ts`, `constants.ts`) plus
  stubs, so the project type-checked from minute one. Then we ran parallel Claude Code
  agents, each owning one folder: vision, gameplay, rendering, UI, audio and input, app
  wiring, booth display, testing and docs. Agents could only edit their own paths and had to
  propose contract changes instead of making them. Each agent checked its own work with
  sandbox pages and a headless screenshot script, and an integration gate
  (`scripts/check.sh`) ran typecheck, unit tests, the build and autopilot smoke runs.

## Challenges we ran into

- **Turning noisy landmarks into crisp game input.** A jump is a 0.3-second event, and the
  difference between "jumping" and "leaning in to look at the screen" is small. Velocity
  gates, hysteresis and per-body scaling took most of the tuning time.
- **Laptop camera framing.** Built-in webcams point at your face. Players need to stand back
  and tilt the screen so there is headroom to jump, and hips are often cut off, so tracking
  had to work from the upper body alone.
- **Latency.** Camera, inference and smoothing add delay, so the game has to be forgiving:
  a 0.7 s jump airtime, input buffering, coyote time and slightly smaller hitboxes.
- **Two people and a crowd.** Keeping each player's identity stable while spectators walk
  through the background.
- **Coordinating many parallel agents** without them overwriting each other or drifting
  apart on interfaces.

## Accomplishments that we're proud of

- It is playable with your body through a normal laptop webcam; we tested it ourselves with
  real cameras, not only the synthetic fake-camera harness.
- Zero-touch flow: walk up, raise hands, play, crash, laugh at your selfie, raise hands again.
- Real 2-player split-screen racing on one laptop camera.
- Everything, including art, music and sound, is generated in code, and the models and
  runtime are self-hosted.
- 238 unit tests and 12 end-to-end Playwright scenarios, including a synthetic webcam person
  (`tools/fakecam`) that drives the real vision pipeline.

## What we learned

- Gesture design is mostly about rejecting what is not a gesture.
- Positioning feedback (too close, head cut off) matters as much as the detection itself.
- Freezing contracts up front is what made parallel AI agents workable. Shared types, clear
  file ownership and automated checks mattered more than any single prompt.
- A deterministic simulation plus an autopilot gives you free regression tests and a free
  attract mode.

## What's next for BODY SURFERS

- Character skins.
- A seated / arms-only mode for accessibility.
- QR code to download your crash selfie.
- Daily seed with a shared online leaderboard (opt-in).
- Crash replays from a spectator camera.
- Clap to boost.
- Phone-as-camera without Continuity Camera.

## Built with

TypeScript, Vite, Three.js, MediaPipe Tasks Vision, Web Audio API, Vitest, Playwright,
Claude Code.
