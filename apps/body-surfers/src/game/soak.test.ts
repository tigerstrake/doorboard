import { describe, expect, it } from 'vitest';
import { World } from './World';
import { AutoPilot } from './AutoPilot';
import { SIM_DT } from '../shared/constants';
import type { GameEvent, PlayerAction } from '../shared/types';

function runPilot(seed: number, tutorial: boolean, maxTime: number, attract = false, lag = 0) {
  const w = new World(0);
  const ap = new AutoPilot(lag);
  const q: [number, PlayerAction][] = [];
  w.reset({ seed, tutorial, attract });
  w.start();
  let steps = 0;
  let crash: GameEvent | null = null;
  let tutorialStumbles = 0;
  let coins = 0;
  const powerUps: Record<string, number> = {};
  let smashes = 0;
  while (w.snapshot().status === 'running' && w.snapshot().time < maxTime) {
    if (steps % 2 === 0) {
      const a = ap.decide(w.snapshot());
      if (a) q.push([w.snapshot().time + lag, a]);
    }
    while (q.length && q[0][0] <= w.snapshot().time) w.handleAction(q.shift()![1]);
    w.step(SIM_DT);
    steps++;
    for (const e of w.drainEvents()) {
      if (e.type === 'crash') crash = e;
      if (e.type === 'powerUp') powerUps[e.kind] = (powerUps[e.kind] ?? 0) + 1;
      if (e.type === 'smash') smashes++;
      if (e.type === 'blocked' && w.snapshot().distance < 350 && tutorial) tutorialStumbles++;
    }
  }
  const s = w.snapshot();
  coins = s.coins;
  return { time: s.time, distance: s.distance, score: s.score, coins, crash, tutorialStumbles, status: s.status, powerUps, smashes };
}

describe('AutoPilot soak', () => {
  it('clears the tutorial and survives >= 90 s in >= 9/10 seeds', () => {
    const results = [];
    for (let seed = 1; seed <= 20; seed++) results.push({ seed, ...runPilot(seed * 7919, true, 120) });
    const survived = results.filter((r) => r.time >= 90).length;
    const lines = results.map((r) => `seed ${r.seed}: t=${r.time.toFixed(1)}s d=${r.distance.toFixed(0)}m coins=${r.coins} ${r.crash ? 'crash ' + JSON.stringify(r.crash) : 'alive'}`);
    console.log(`[soak] survived>=90s: ${survived}/${results.length}\n` + lines.join('\n'));
    for (const r of results) {
      expect(r.distance).toBeGreaterThan(350);
      expect(r.tutorialStumbles).toBe(0);
    }
    expect(survived / results.length).toBeGreaterThanOrEqual(0.9);
  }, 120000);

  it('attract mode (no tutorial, no moving trains) survives', () => {
    let ok = 0;
    for (let seed = 1; seed <= 10; seed++) if (runPilot(seed * 31, false, 120, true).time >= 90) ok++;
    console.log(`[soak] attract survived>=90s: ${ok}/10`);
    expect(ok).toBeGreaterThanOrEqual(9);
  }, 120000);

  it('camera-latency model (0.45 s input lag): tutorial 100%, survives >= 60 s in >= 8/10 seeds', () => {
    const res = [];
    for (let seed = 1; seed <= 20; seed++) res.push(runPilot(seed * 104729, true, 240, false, 0.45));
    const ok = res.filter((r) => r.time >= 60).length;
    const times = res.map((r) => r.time).sort((a, b) => a - b);
    const median = (times[9] + times[10]) / 2;
    const pu: Record<string, number> = {};
    for (const r of res) for (const [k, n] of Object.entries(r.powerUps)) pu[k] = (pu[k] ?? 0) + n;
    const smashes = res.reduce((a, r) => a + r.smashes, 0);
    console.log(
      `[soak] lag 0.45 s survived>=60s: ${ok}/20, median ${median.toFixed(1)} s (cap 240), min ${times[0].toFixed(1)} s\n` +
      `[soak] power-ups collected ${JSON.stringify(pu)}, smashes ${smashes}\n` +
      res.map((r, i) => `seed ${i + 1}: t=${r.time.toFixed(1)}s d=${r.distance.toFixed(0)}m ${r.crash ? 'crash ' + JSON.stringify(r.crash) : 'alive'}`).join('\n'),
    );
    for (const r of res) {
      expect(r.distance).toBeGreaterThan(350); // tutorial cleared
      expect(r.tutorialStumbles).toBe(0);
    }
    expect(ok / res.length).toBeGreaterThanOrEqual(0.8);
    for (const k of ['jetpack', 'slowmo', 'star', 'coinRain']) expect(pu[k] ?? 0).toBeGreaterThan(0);
  }, 120000);
});
