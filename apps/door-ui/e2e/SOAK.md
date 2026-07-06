# Wallboard idle+takeover soak (T-404 acceptance)

Acceptance criteria: a 1-hour idle+takeover soak shows no memory growth, no
animation degradation, and kiosk CPU under an agreed ceiling.

## Automated smoke proxy (CI/dev, seconds not hours)

`e2e/soak.spec.ts` cycles `IDLE -> VISITOR_MODE -> RINGING -> SESSION_END -> IDLE`
repeatedly against the same mounted `App` instance and asserts the JS heap
(`performance.memory.usedJSHeapSize`, Chromium-only) doesn't grow unbounded
relative to baseline. It runs with real timers (no fixed clock) so intervals and
CSS animations behave as they would on a kiosk. This catches the class of bug a
1-hour soak is meant to catch (leaked listeners/timers, unbounded arrays) in
seconds, but it is not a substitute for the real bench run below — it runs
headless, on dev hardware, for a few dozen cycles, not 1 hour on kiosk hardware.

Run it locally:

```sh
pnpm --filter @doorboard/door-ui test:e2e:soak
# longer local run:
SOAK_CYCLES=500 pnpm --filter @doorboard/door-ui test:e2e:soak
```

## Real 1-hour bench procedure (kiosk hardware, before M4 sign-off)

No hardware was available in this task's dev environment, so the real bench run
must be performed on actual kiosk hardware (or the closest available Pi + monitor)
before this acceptance criterion is signed off. Procedure:

1. Launch the built `door-ui` (`pnpm --filter @doorboard/door-ui build && pnpm --filter @doorboard/door-ui preview`) in the Chromium kiosk profile used on-device, pointed at `/wallboard`.
2. Drive `IDLE <-> VISITOR_MODE <-> RINGING <-> ANSWERED/UNANSWERED_TIMEOUT <-> VIDEO_MESSAGE_* <-> SESSION_END <-> IDLE` transitions continuously for 1 hour, mixing generic and enrolled (celebratory) greetings, via the simulator or the sim control panel. Include idle stretches (ambient mode sitting untouched) as well as rapid takeover cycling.
3. Sample every 5 minutes:
   - **Memory**: Chrome DevTools `chrome://inspect` → the kiosk tab's memory panel, or `performance.memory.usedJSHeapSize` via the remote debugging protocol. Record heap size; flag if the trend across the hour is monotonically increasing rather than flat/sawtooth (sawtooth from GC is expected and fine).
   - **CPU**: `top`/`htop` (or the Pi's `vcgencmd measure_temp` alongside CPU%) for the Chromium renderer process. Ceiling to agree with the team before M4 sign-off — until then, treat sustained single-core saturation as a fail.
   - **Animation health**: DevTools Performance panel or `chrome://tracing` for a 10s window each sample — confirm the entrance/celebratory/ring animations are still hitting frame budget (no frame times climbing over the run, which would indicate accumulating work per animation tick).
4. Record start/end heap, min/max/avg CPU%, and any dropped-frame observations in the PR description or a linked doc. A flat-to-sawtooth heap graph and stable CPU/frame timing across the hour is a pass.

If this repo gains real kiosk hardware access before this PR merges, run the above
and paste the results into the PR. If not, say so explicitly in the PR description
per CLAUDE.md's review gate — do not claim the 1-hour bench passed without having
run it.
