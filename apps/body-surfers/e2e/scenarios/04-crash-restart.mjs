// Tutorial off (the tutorial section forgives crashes): doing nothing hits an obstacle → dead → gameover
// (≈1.5 s) → Enter ignored during the 2.5 s restart lockout → Enter restarts (countdown).
import { assert, seedSettings, startKeyboardRun, world } from '../lib.mjs';

export default {
  name: 'crash -> gameover -> Enter restarts',
  timeout: 70000,
  async run(ctx) {
    await seedSettings(ctx, { tutorial: false });
    await startKeyboardRun(ctx, '&seed=3');
    const dead = await ctx.waitState((s) => world(s)?.status === 'dead', { timeout: 30000, desc: 'crash (world dead)' });
    assert(world(dead).distance > 10, `crashed after running some distance (${world(dead).distance})`);
    const go = await ctx.waitState((s) => s.screen === 'gameover', { timeout: 4000, desc: 'gameover' });
    await ctx.sleep(400);
    await ctx.press('Enter');
    await ctx.sleep(500);
    assert((await ctx.state()).screen === 'gameover', 'Enter during restart lockout is ignored');
    await ctx.sleep(2200);
    await ctx.press('Enter');
    const r = await ctx.waitState((s) => s.screen === 'countdown' || s.screen === 'playing', { timeout: 3000, desc: 'restart → countdown' });
    assert(world(r).distance < 1, `fresh world after restart (distance ${world(r).distance})`);
    assert(r.playerCount === go.playerCount, 'same player count on restart');
    await ctx.waitState((s) => s.screen === 'playing', { timeout: 8000, desc: 'playing again' });
  },
};
