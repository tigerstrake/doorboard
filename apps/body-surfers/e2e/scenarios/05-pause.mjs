// P pauses (world frozen), P again resumes after the 3 s resumeIn countdown. Escape also pauses.
import { assert, startKeyboardRun, world } from '../lib.mjs';

export default {
  name: 'P pause / resume',
  async run(ctx) {
    await startKeyboardRun(ctx, '&seed=11');
    await ctx.sleep(300);
    await ctx.press('KeyP');
    const p = await ctx.waitState((s) => s.screen === 'paused', { timeout: 1500, desc: 'paused after P' });
    await ctx.sleep(1000);
    const p2 = await ctx.state();
    assert(p2.screen === 'paused', 'stays paused');
    assert(Math.abs(world(p2).distance - world(p).distance) < 0.01, `world frozen while paused (${world(p).distance} → ${world(p2).distance})`);
    await ctx.press('KeyP');
    await ctx.sleep(1500);
    assert((await ctx.state()).screen === 'paused', 'resume waits for the 3 s resumeIn countdown');
    await ctx.waitState((s) => s.screen === 'playing', { timeout: 4000, desc: 'playing after resumeIn' });
    const r0 = await ctx.state();
    await ctx.sleep(500);
    assert(world(await ctx.state()).distance > world(r0).distance + 1, 'distance increases after resume');
    await ctx.press('Escape');
    await ctx.waitState((s) => s.screen === 'paused', { timeout: 1500, desc: 'paused after Escape' });
  },
};
