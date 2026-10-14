// After a crash the run lands in localStorage[STORAGE_KEYS.leaderboard] as a plain JSON
// array (best first) and the gameover name input renames it.
import { STORAGE_KEYS, assert, readStorage, seedSettings, startKeyboardRun, waitFor, world } from '../lib.mjs';

export default {
  name: 'leaderboard entry + rename',
  timeout: 60000,
  async run(ctx) {
    const { page } = ctx;
    await ctx.goto('/?input=keyboard&mute=1');
    await page.evaluate((k) => localStorage.removeItem(k), STORAGE_KEYS.leaderboard);
    await seedSettings(ctx, { tutorial: false });
    await startKeyboardRun(ctx, '&seed=21');
    await ctx.waitState((s) => s.screen === 'gameover', { timeout: 35000, desc: 'gameover after crash' });
    const s = await ctx.state();
    const lb = await waitFor(async () => {
      const v = await readStorage(page, STORAGE_KEYS.leaderboard);
      return Array.isArray(v) && v.length ? v : false;
    }, { timeout: 3000, desc: 'leaderboard array in storage' });
    const e = lb[0];
    for (const k of ['id', 'name', 'score', 'coins', 'distance', 'date', 'mode']) assert(k in e, `entry has ${k}: ${JSON.stringify(e).slice(0, 200)}`);
    assert(typeof e.name === 'string' && e.name.length > 0, 'entry has a fun name');
    assert(e.mode === 1, `mode 1, got ${e.mode}`);
    assert(Math.abs(e.score - world(s).score) <= 1, `stored score ${e.score} ≈ run score ${world(s).score}`);
    assert(Math.abs(e.distance - world(s).distance) <= 1, `stored distance ${e.distance} ≈ ${world(s).distance}`);

    const input = page.locator('#ui-root input[type=text], #ui-root input:not([type])').first();
    await input.waitFor({ state: 'visible', timeout: 3000 });
    await input.fill('E2E Tester');
    await input.press('Enter');
    await waitFor(async () => {
      const v = await readStorage(page, STORAGE_KEYS.leaderboard);
      return Array.isArray(v) && v.find((x) => x.id === e.id)?.name === 'E2E Tester';
    }, { timeout: 2000, desc: 'renamed entry in storage' });
    assert((await ctx.state()).screen === 'gameover', 'typing a name (Enter) does not restart the game');
    await ctx.reload();
    await ctx.waitState((s) => !!s.screen, { desc: 'reload' });
    const lb2 = await readStorage(page, STORAGE_KEYS.leaderboard);
    assert(lb2?.find((x) => x.id === e.id)?.name === 'E2E Tester', 'rename persists across reload');
  },
};
