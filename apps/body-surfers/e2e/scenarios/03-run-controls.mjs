// ?input=keyboard&autostart=1: countdown → playing, distance grows, lane + jump keys work.
import { assert, startKeyboardRun, world } from '../lib.mjs';

export default {
  name: 'autostart run + lane/jump keys',
  async run(ctx) {
    await ctx.goto('/?input=keyboard&autostart=1&mute=1&seed=7');
    const c = await ctx.waitState((s) => s.screen === 'countdown' || s.screen === 'playing', { desc: 'countdown' });
    assert(c.screen === 'countdown', `autostart goes through countdown first, got ${c.screen}`);
    assert(world(c)?.status === 'ready', `world ready during countdown, got ${world(c)?.status}`);
    const p0 = await ctx.waitState((s) => s.screen === 'playing', { timeout: 8000, desc: 'playing' });
    assert(world(p0).status === 'running', 'world running');
    await ctx.sleep(800);
    const p1 = await ctx.state();
    assert(world(p1).distance > world(p0).distance + 2, `distance increases (${world(p0).distance} → ${world(p1).distance})`);
    assert(world(p1).lane === 0, `starts in centre lane, got ${world(p1).lane}`);

    await ctx.press('ArrowLeft');
    await ctx.waitState((s) => world(s).lane === -1, { timeout: 1000, desc: 'lane -1 after ArrowLeft' });
    await ctx.press('KeyD');
    await ctx.waitState((s) => world(s).lane === 0, { timeout: 1000, desc: 'lane 0 after D' });
    await ctx.press('ArrowRight');
    await ctx.waitState((s) => world(s).lane === 1, { timeout: 1000, desc: 'lane 1 after ArrowRight' });
    await ctx.press('ArrowRight'); // clamp at the edge
    await ctx.sleep(200);
    assert(world(await ctx.state()).lane === 1, 'lane clamps at +1');
    await ctx.press('ArrowLeft');
    await ctx.waitState((s) => world(s).lane === 0, { timeout: 1000, desc: 'back to lane 0' });

    await ctx.waitState((s) => world(s).y < 0.01, { timeout: 2000, desc: 'grounded' });
    await ctx.press('Space');
    await ctx.waitState((s) => world(s).y > 0.3, { timeout: 600, interval: 30, desc: 'airborne after Space' });
    await ctx.waitState((s) => world(s).y < 0.01, { timeout: 2000, interval: 30, desc: 'landed' });
    await ctx.press('ArrowUp');
    await ctx.waitState((s) => world(s).y > 0.3, { timeout: 600, interval: 30, desc: 'airborne after ArrowUp' });
  },
};
