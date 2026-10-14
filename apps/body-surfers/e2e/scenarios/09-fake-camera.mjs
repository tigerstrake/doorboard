// Chrome's test-pattern camera contains no person. The countdown first waits for a body
// ("Step into view"), then after APP.noViewFallbackS (~8 s) starts anyway so first-timers
// are never stuck; the keyboard controls that run.
import { assert, SHOTS_DIR, world } from '../lib.mjs';

export default {
  name: 'empty camera waits, then falls back to a playable run',
  camera: true,
  timeout: 60000,
  async run(ctx) {
    await ctx.goto('/?autostart=1&mute=1&seed=9');
    await ctx.waitState((s) => s.visionStatus === 'running', { timeout: 25000, desc: "visionStatus 'running'" });
    await ctx.sleep(4000);
    const waiting = await ctx.state();
    assert(waiting.screen === 'countdown' && world(waiting).distance === 0, `empty feed holds at countdown first, got ${waiting.screen}`);
    await ctx.page.screenshot({ path: `${SHOTS_DIR}/body-calibration-waiting.png` });
    await ctx.waitState((s) => s.screen === 'playing', { timeout: 15000, desc: 'no-view fallback starts the run' });
    assert(world(await ctx.state()).status === 'running', 'fallback run is running');
    await ctx.press('ArrowLeft');
    await ctx.waitState((s) => world(s).lane === -1, { timeout: 1500, desc: 'keyboard controls the fallback run' });
  },
};
