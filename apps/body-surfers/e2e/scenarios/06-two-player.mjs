// 2P keyboard: WASD → world 0, arrows → world 1.
import { assert, startKeyboardRun, world } from '../lib.mjs';

export default {
  name: '2P keyboard split',
  async run(ctx) {
    const s = await startKeyboardRun(ctx, '&players=2&seed=5');
    assert(s.playerCount === 2 && s.worlds.length === 2, `2 worlds, got ${JSON.stringify(s)}`);
    await ctx.sleep(300);
    await ctx.press('KeyA');
    await ctx.waitState((s) => world(s, 0).lane === -1, { timeout: 1000, desc: 'P1 lane -1 after A' });
    assert(world(await ctx.state(), 1).lane === 0, 'A does not move P2');
    await ctx.press('ArrowRight');
    await ctx.waitState((s) => world(s, 1).lane === 1, { timeout: 1000, desc: 'P2 lane +1 after ArrowRight' });
    assert(world(await ctx.state(), 0).lane === -1, 'ArrowRight does not move P1');
    await ctx.waitState((s) => world(s, 0).y < 0.01 && world(s, 1).y < 0.01, { timeout: 2000, desc: 'both grounded' });
    await ctx.press('KeyW');
    const j = await ctx.waitState((s) => world(s, 0).y > 0.3, { timeout: 600, interval: 30, desc: 'P1 airborne after W' });
    assert(world(j, 1).y < 0.01, 'W does not make P2 jump');
    await ctx.waitState((s) => world(s, 0).y < 0.01, { timeout: 2000, interval: 30, desc: 'P1 landed' });
    await ctx.press('ArrowUp');
    const k = await ctx.waitState((s) => world(s, 1).y > 0.3, { timeout: 600, interval: 30, desc: 'P2 airborne after ArrowUp' });
    assert(world(k, 0).y < 0.01, 'ArrowUp does not make P1 jump');
    assert(world(k, 0).distance > 5 && world(k, 1).distance > 5, 'both worlds advancing');
  },
};
