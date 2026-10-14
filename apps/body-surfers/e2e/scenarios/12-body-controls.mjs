import { assert, waitFor, world } from '../lib.mjs';
import { installBodyCamera, setBodyCamera } from '../body-camera.mjs';

export default {
  name: 'body camera calibration, gestures, and tracking recovery',
  camera: true,
  timeout: 90000,
  async run(ctx) {
    await installBodyCamera(ctx);
    await ctx.goto('/?autostart=1&players=1&mute=1&seed=9');
    await ctx.waitState((s) => s.visionStatus === 'running', { timeout: 25000, desc: 'real MediaPipe camera running' });

    // Still inside the no-view fallback window (~8 s): must be waiting at the start line.
    await ctx.sleep(4000);
    const waiting = await ctx.state();
    assert(waiting.screen === 'countdown' && world(waiting).distance === 0, 'no person means no running');

    await setBodyCamera(ctx);
    await ctx.waitState((s) => s.screen === 'playing', { timeout: 18000, desc: 'late-arriving body calibrates and starts the run' });
    const start = await ctx.page.evaluate(() => window.__bodySurfers.log.length);
    // Start at the first movement only after GO, independent of model startup time.
    await setBodyCamera(ctx, { time: 3, rate: 1 });
    await ctx.waitState((s) => world(s).lane === -1, { timeout: 3500, desc: 'body step left changes actual runner lane' });
    await ctx.waitState((s) => world(s).lane === 1, { timeout: 5000, desc: 'body step right changes actual runner lane' });
    await ctx.waitState((s) => world(s).y > 0.2, { timeout: 5000, interval: 30, desc: 'body jump makes actual runner airborne' });
    const required = ['pose:p0:lane-1', 'game:p0:lane-1', 'pose:p0:lane1', 'game:p0:lane1', 'pose:p0:jump', 'game:p0:jump', 'pose:p0:duck', 'game:p0:roll'];
    await waitFor(async () => {
      const events = await ctx.page.evaluate((offset) => window.__bodySurfers.log.slice(offset).map((e) => e.ev), start);
      return required.every((ev) => events.includes(ev));
    }, { timeout: 7000, desc: 'left/right, jump, and duck produce real game actions' });

    await setBodyCamera(ctx, { visible: false });
    const paused = await ctx.waitState((s) => s.screen === 'paused', { timeout: 5000, desc: 'losing body tracking pauses gameplay' });
    const distance = world(paused).distance;
    await ctx.sleep(800);
    assert(world(await ctx.state()).distance === distance, 'runner stops while body tracking is lost');
    await setBodyCamera(ctx);
    await ctx.waitState((s) => s.screen === 'playing', { timeout: 12000, desc: 'body returns and gameplay resumes' });
    await ctx.waitState((s) => world(s).distance > distance + 1, { timeout: 1500, desc: 'runner advances after tracking recovery' });
  },
};
