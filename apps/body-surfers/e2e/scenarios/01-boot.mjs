// Boot screen renders (canvas + UI, handle present) in default camera mode, and the
// keyboard-only path: ?input=keyboard → confirm → lobby → "1" → countdown → playing.
import { assert, countVisible } from '../lib.mjs';

export default {
  name: 'boot + keyboard-only path',
  async run(ctx) {
    const { page } = ctx;
    await ctx.goto('/?mute=1');
    const s0 = await ctx.waitState((s) => typeof s.screen === 'string', { desc: 'test handle' });
    assert(s0.screen === 'boot', `camera mode starts on boot, got ${s0.screen}`);
    assert(await page.locator('#game-canvas').count() === 1, '#game-canvas exists');
    const uiText = await page.locator('#ui-root').innerText();
    assert(uiText.trim().length > 0, '#ui-root renders boot content');
    assert((await countVisible(page, '*')) > 0, 'boot UI has visible elements');
    // the attract world renders behind boot: canvas should not be a flat colour
    const png = await page.screenshot();
    assert(png.length > 15000, `screenshot looks blank (${png.length} bytes)`);

    await ctx.goto('/?input=keyboard&mute=1');
    let s = await ctx.waitState((s) => s.screen === 'boot' || s.screen === 'lobby', { desc: 'boot/lobby' });
    if (s.screen === 'boot') {
      await ctx.press('Enter');
      s = await ctx.waitState((s) => s.screen === 'lobby', { timeout: 5000, desc: "lobby after Enter" });
    }
    assert(s.visionStatus === 'idle', `keyboard mode must not start vision, got ${s.visionStatus}`);
    await ctx.press('Digit1');
    await ctx.waitState((s) => s.screen === 'countdown', { timeout: 3000, desc: 'countdown after "1"' });
    const p = await ctx.waitState((s) => s.screen === 'playing', { timeout: 10000, desc: 'playing' });
    assert(p.playerCount === 1 && p.worlds.length === 1, `1P run, got ${JSON.stringify(p)}`);
  },
};
