// ` opens the settings panel (vision tuning sliders), changes persist to localStorage and
// survive a reload; M toggles mute and persists; corrupt settings JSON is tolerated.
import { STORAGE_KEYS, assert, countVisible, readStorage, waitFor } from '../lib.mjs';

export default {
  name: 'settings panel + persistence',
  async run(ctx) {
    const { page } = ctx;
    await ctx.goto('/?input=keyboard');
    await ctx.waitState((s) => s.screen === 'boot' || s.screen === 'lobby', { desc: 'boot/lobby' });
    const before = await countVisible(page, 'input[type=range]');
    await ctx.press('Backquote');
    const sliders = await waitFor(async () => (await countVisible(page, 'input[type=range]')) || 0, { timeout: 2000, desc: 'settings sliders visible after `' });
    assert(sliders >= 5 && sliders > before, `settings panel shows tuning sliders (got ${sliders})`);

    // move the first range input that maps to a tuning value
    const changed = await page.evaluate(() => {
      const r = [...document.querySelectorAll('#ui-root input[type=range]')].find((el) => el.getBoundingClientRect().width > 0);
      const min = Number(r.min || 0), max = Number(r.max || 1);
      const v = min + (max - min) * 0.73;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(r, String(v));
      r.dispatchEvent(new Event('input', { bubbles: true }));
      r.dispatchEvent(new Event('change', { bubbles: true }));
      return { value: Number(r.value), name: r.name || r.id || r.dataset.key || null };
    });
    const stored = await waitFor(async () => {
      const s = await readStorage(page, STORAGE_KEYS.settings);
      return s && JSON.stringify(s).includes(String(changed.value).slice(0, 4)) ? s : false;
    }, { timeout: 2000, desc: `slider value ${changed.value} persisted to ${STORAGE_KEYS.settings}` });

    await ctx.press('Backquote'); // close
    await waitFor(async () => (await countVisible(page, 'input[type=range]')) === before, { timeout: 2000, desc: 'settings panel closes on `' });

    const m0 = (await readStorage(page, STORAGE_KEYS.settings))?.muted;
    await ctx.press('KeyM');
    const m1 = await waitFor(async () => {
      const s = await readStorage(page, STORAGE_KEYS.settings);
      return s && s.muted === !m0 ? s : false;
    }, { timeout: 2000, desc: 'M toggles settings.muted in storage' });

    await ctx.reload();
    await ctx.waitState((s) => !!s.screen, { desc: 'handle after reload' });
    await ctx.sleep(500);
    const after = await readStorage(page, STORAGE_KEYS.settings);
    assert(after && after.muted === m1.muted, `muted persisted across reload (${after?.muted})`);
    assert(JSON.stringify(after.vision) === JSON.stringify(stored.vision), `vision tuning persisted across reload: ${JSON.stringify(after.vision)} vs ${JSON.stringify(stored.vision)}`);
    await ctx.press('Backquote');
    const reopened = await page.evaluate(() =>
      [...document.querySelectorAll('#ui-root input[type=range]')].filter((el) => el.getBoundingClientRect().width > 0).map((el) => Number(el.value)),
    );
    assert(reopened.some((v) => Math.abs(v - changed.value) < 1e-6), `reopened panel shows persisted slider value ${changed.value} (got ${reopened})`);

    // corrupt JSON must not break boot
    await page.evaluate((k) => localStorage.setItem(k, '{not json'), STORAGE_KEYS.settings);
    await ctx.reload();
    await ctx.waitState((s) => s.screen === 'boot' || s.screen === 'lobby', { desc: 'boots with corrupt settings' });
  },
};
