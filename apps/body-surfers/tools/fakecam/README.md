# fakecam: a synthetic webcam person for headless tests

This lets you test the real `getUserMedia` → `<video>` → MediaPipe PoseLandmarker → gesture pipeline in headless Chrome. It works like this:

- A Playwright init script overrides `navigator.mediaDevices.getUserMedia` (for video requests) and `enumerateDevices`.
- The overridden call returns `canvas.captureStream()` from a three.js scene. In that scene a jointed, shaded figure with a face stands in a plain room about 3 m from a 66° laptop camera and follows a scripted timeline in real time.
- `enumerateDevices` reports one camera, `FakeCam (<routine>)`, with deviceId `fakecam`.

The modules are served by the repo's Vite dev server (`/tools/fakecam/*.js`), so the page must be on that server, or you pass `--assets <vite origin>`.

## Commands

```bash
# drive any page with the fake camera
node tools/fakecam/run.mjs --routine solo --url "http://localhost:5173/?autostart=1" \
     [--seconds 22] [--shots 2000,9600,12300,16000] [--out shots/fakecam.png] \
     [--fps 30] [--size 1280x720] [--loop] [--start-delay 0] [--eval "<js>"] [--logs] [--hmr]

# smoke test: the bundled probe page runs PoseLandmarker on the stream and records per-phase hits
node tools/fakecam/run.mjs --routine solo --url http://localhost:5173/tools/fakecam/probe.html --eval "window.__probe"

# offline check: render the routine frame by frame and run PoseLandmarker on every frame
node tools/fakecam/validate.mjs --routine solo|duo|spectator [--model lite|full] [--fps 15] [--frames 1,5,12.3]
#   prints the detection rate per phase; writes test-media/<routine>-<model>-validate.json and sample PNGs
```

- The routine clock starts at the page's first video `getUserMedia` call.
- `--shots` times are milliseconds after page load.
- The runner prints every phase change, e.g. `[fakecam] +9.5s routine t=9s phase=jump-1`, so you can line it up with the game's own logs. In the page, `window.__fakecam` holds `{ t, phase, phases, fps }`.
- By default the Vite HMR client is replaced with a stub that still injects CSS. Without the stub, any `.html` edit on the shared dev server full-reloads the page and the routine starts over. Pass `--hmr` to keep the real client.

## Routines (scene.js `ROUTINES`)

| routine | content |
|---|---|
| `solo` | idle 3 s → step to own left 1.3 shoulder widths → back → step right → back (each 1.5 s) → jump ×2 (1.3 s each, 0.3 m apex) → squat ×2 (1.8 s each) → hands up 1.5 s → idle. About 18.8 s total. |
| `duo` | two people 1.5 m apart (red and green), each running `solo`; the right one starts 1 s later |
| `spectator` | `solo` player plus a person walking back and forth 1.3 m behind them |
| `idle` | standing still forever |

"Left" means the person's own left, which is +x and the right side of the raw (unmirrored) frame.

## Detection rates (PoseLandmarker, VIDEO mode, CPU, numPoses 3, 15 fps sampling)

- solo / lite: 100% in every phase (283 frames). solo / full: 98.2%.
- duo / lite: 100% for both players (596 actor-frames).
- spectator / lite: player 100%. The walker is detected in 29% of frames, mostly when it isn't behind the player, so it shows up as a realistic part-time distractor.
- Live stream through the probe page: 966/966 frames had a detection. The scene renders at about 46 fps in headless SwiftShader.

On this figure, the landmark shoulder width is about 0.092 of the frame width (0.36 m). The motion magnitudes below are in units of that shoulder width. Vertical values are aspect-corrected.

| motion | magnitude |
|---|---|
| step (hip x shift) | about 1.3 |
| jump (shoulder rise) | about 0.85 |
| squat (shoulder drop) | about 1.1 |
| hands up (wrists) | well above the nose |

## Limitations

- The figure is a stylised mannequin (capsule limbs, simple face), not a photoreal human. BlazePose tracks it reliably, but how confident it is and how much the landmarks jitter will differ from real people. There is no camera noise, motion blur or lighting change.
- Movements are idealised: steps slide with a small leg lift, and jumps are perfect parabolas.
- It needs the Vite dev server to serve `tools/fakecam/`. It won't work against a `dist/` build unless you pass `--assets`.
- `getSettings()` on the track is patched, but `getCapabilities()` and `applyConstraints()` are the canvas track's own.
