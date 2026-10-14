// hall.html shows the stored leaderboard and reacts to BoothMessages on the BroadcastChannel.
import { BROADCAST_CHANNEL, STORAGE_KEYS, waitFor } from '../lib.mjs';

const entry = (name, score) => ({ id: `e2e-${name}`, name, score, coins: 12, distance: score, date: Date.now(), photo: null, mode: 1 });

export default {
  name: 'hall.html booth display',
  async run(ctx) {
    const { page } = ctx;
    await ctx.goto('/hall.html');
    await page.evaluate(([k, v]) => localStorage.setItem(k, JSON.stringify(v)), [STORAGE_KEYS.leaderboard, [entry('Zappy Walrus', 4321), entry('Turbo Pickle', 1234)]]);
    await ctx.reload();
    const text = () => page.locator('body').innerText();
    await waitFor(async () => /Zappy Walrus/.test(await text()), { timeout: 4000, desc: 'hall lists stored leaderboard' });

    const sender = await ctx.newPage('sender');
    await ctx.goto('/hall.html', sender); // same origin → same BroadcastChannel scope
    const post = (msg) => sender.evaluate(([ch, m]) => { const c = new BroadcastChannel(ch); c.postMessage(m); c.close(); }, [BROADCAST_CHANNEL, msg]);

    const idleText = await text();
    for (let i = 0; i < 4; i++) {
      await post({ type: 'state', screen: 'playing', playerCount: 2, players: [{ score: 98765, coins: 42, distance: 900, alive: true }, { score: 55555, coins: 7, distance: 800, alive: false }] });
      await ctx.sleep(250);
    }
    await waitFor(async () => { const t = await text(); return /98[,.\s]?765/.test(t) && t !== idleText; }, { timeout: 3000, desc: "hall shows live score from a 'state' message" });

    await post({ type: 'result', results: [{ playerId: 0, name: 'Captain Noodle', score: 7777, coins: 3, distance: 700, time: 60, photo: null, rank: 1, entryId: 'e2e-new' }] });
    await waitFor(async () => /Captain Noodle/.test(await text()), { timeout: 4000, desc: "hall shows the 'result' message" });

    await post({ type: 'idle' });
    await waitFor(async () => !/98[,.\s]?765/.test(await text()), { timeout: 15000, desc: "live score cleared after 'idle'" });
  },
};
