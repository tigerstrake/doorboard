// §11 smoke: ?autopilot=1 1P and 2P for ~10 s each, distance keeps increasing, zero page errors.
import { assert, startKeyboardRun, world } from '../lib.mjs';

export default {
  name: 'autopilot smoke 1P+2P',
  timeout: 60000,
  async run(ctx) {
    for (const players of [1, 2]) {
      await startKeyboardRun(ctx, `&autopilot=1&players=${players}&seed=42`);
      let prev = await ctx.state();
      for (let i = 0; i < 5; i++) {
        await ctx.sleep(2000);
        const s = await ctx.state();
        assert(s.screen === 'playing', `${players}P autopilot still playing after ${(i + 1) * 2} s (screen ${s.screen}, worlds ${JSON.stringify(s.worlds)})`);
        for (let w = 0; w < players; w++) assert(world(s, w).distance > world(prev, w).distance + 5, `${players}P world ${w} distance increasing`);
        prev = s;
      }
    }
  },
};
