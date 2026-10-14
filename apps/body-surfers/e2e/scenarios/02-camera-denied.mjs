// Camera mode without a camera (headless, no fake device): boot-click fails → boot.error
// with a "PLAY WITH KEYBOARD" fallback that leads to the lobby.
import { assert } from '../lib.mjs';

export default {
  name: 'camera denied -> keyboard fallback',
  async run(ctx) {
    const { page } = ctx;
    // no camera permission granted in this context → getUserMedia rejects
    await ctx.goto('/?mute=1');
    await ctx.waitState((s) => s.screen === 'boot', { desc: 'boot' });
    await ctx.sleep(500);
    await ctx.press('Enter'); // confirm key acts as boot-click
    const btn = page.locator('#ui-root').getByRole('button', { name: /keyboard/i }).first();
    await btn.waitFor({ state: 'visible', timeout: 15000 });
    const s = await ctx.state();
    assert(s.screen === 'boot', `stays on boot with error, got ${s.screen}`);
    await btn.click();
    await ctx.waitState((s) => s.screen === 'lobby', { timeout: 5000, desc: 'lobby after keyboard fallback' });
    // the expected getUserMedia failure may be logged; don't count it as an app error
    ctx.errors.splice(0, ctx.errors.length, ...ctx.errors.filter((e) => !/NotAllowed|NotFound|Permission|camera|getUserMedia|Requested device/i.test(e)));
  },
};
