import { describe, expect, it } from 'vitest';
import type { ActionEvent, VisionFrame } from '../shared/types';
import { VisionCore } from './VisionCore';
import { PlayerAssigner } from './PlayerAssigner';
import type { Candidate } from './features';
import { type BodyParams, SYN_YSCALE, jumpLift, keyframes, makeBody, makeRng } from './synthetic';
import { OneEuroFilter } from './OneEuroFilter';
import { GestureDetector } from './GestureDetector';
import { VISION_CONFIG as C } from './config';

const FPS = 30;
const DT = 1000 / FPS;

interface Sim {
  core: VisionCore;
  t: number;
  events: ActionEvent[];
  rng: () => number;
  last: VisionFrame | null;
}

function makeSim(seed = 1, players: 1 | 2 = 1): Sim {
  const core = new VisionCore();
  core.setPlayerCount(players);
  const sim: Sim = { core, t: 1000, events: [], rng: makeRng(seed), last: null };
  core.actions.on((e) => sim.events.push(e));
  return sim;
}

type BodiesFn = (t: number) => Array<Partial<BodyParams>>;

function run(sim: Sim, durMs: number, bodies: BodiesFn): void {
  const end = sim.t + durMs;
  while (sim.t < end) {
    sim.t += DT;
    const poses = bodies(sim.t).map((b) => makeBody(b, sim.rng));
    sim.last = sim.core.process(poses, sim.t, SYN_YSCALE);
  }
}

/** Lock + calibrate a still person; returns with events cleared. */
function calibrated(seed = 1, base: Partial<BodyParams> = {}): Sim {
  const sim = makeSim(seed);
  run(sim, 500, () => [base]);
  sim.core.lockSlots(true);
  sim.core.startCalibration(0, sim.t);
  run(sim, 1500, () => [base]);
  expect(sim.core.detector(0).phase).toBe('ready');
  expect(sim.events.map((e) => e.action)).toEqual([{ type: 'lane', lane: 0 }]);
  sim.events.length = 0;
  return sim;
}

const kinds = (sim: Sim, type: string) => sim.events.filter((e) => e.action.type === type);

describe('GestureDetector via VisionCore', () => {
  it('still body → 0 events', () => {
    const sim = calibrated();
    run(sim, 10000, () => [{}]);
    expect(sim.events).toEqual([]);
  });

  it('jump → exactly 1 jump, no duck (incl. countermovement + landing dip)', () => {
    const sim = calibrated();
    const t0 = sim.t + 300;
    run(sim, 2500, (t) => [
      {
        lift:
          // countermovement dip (−0.35) before take-off, ballistic flight, landing dip (−0.6)
          keyframes([[t0 - 250, 0], [t0 - 100, -0.35], [t0, 0]], t) +
          jumpLift(t, t0, 0.7, 450) +
          keyframes([[t0 + 450, 0], [t0 + 550, -0.6], [t0 + 700, -0.6], [t0 + 850, 0]], t),
      },
    ]);
    expect(kinds(sim, 'jump')).toHaveLength(1);
    expect(kinds(sim, 'duck')).toHaveLength(0);
    expect(kinds(sim, 'lane')).toHaveLength(0);
  });

  it('two jumps in a row → 2 jumps', () => {
    const sim = calibrated();
    const t0 = sim.t + 200;
    run(sim, 3000, (t) => [{ lift: jumpLift(t, t0, 0.7, 450) + jumpLift(t, t0 + 1200, 0.7, 450) }]);
    expect(kinds(sim, 'jump')).toHaveLength(2);
  });

  it('squat → exactly 1 duck, no jump on the way up', () => {
    const sim = calibrated();
    const t0 = sim.t + 200;
    run(sim, 3000, (t) => [{ lift: keyframes([[t0, 0], [t0 + 300, -0.9], [t0 + 900, -0.9], [t0 + 1300, 0]], t) }]);
    expect(kinds(sim, 'duck')).toHaveLength(1);
    expect(kinds(sim, 'jump')).toHaveLength(0);
  });

  it('tiptoe (small fast rise) → no jump', () => {
    const sim = calibrated();
    const t0 = sim.t + 200;
    run(sim, 2500, (t) => [{ lift: keyframes([[t0, 0], [t0 + 150, 0.18], [t0 + 900, 0.18], [t0 + 1100, 0]], t) }]);
    expect(sim.events).toEqual([]);
  });

  it('slow rise above the jump line (stretching) → no jump', () => {
    const sim = calibrated();
    const t0 = sim.t + 200;
    run(sim, 4000, (t) => [{ lift: keyframes([[t0, 0], [t0 + 2500, 0.45]], t) }]);
    expect(kinds(sim, 'jump')).toHaveLength(0);
  });

  it("step to the player's left → lane −1 (mirrored display), right → +1", () => {
    const sim = calibrated();
    const s = 0.15;
    const t0 = sim.t + 200;
    // Person's left = higher raw camera x.
    run(sim, 1500, (t) => [{ rawX: keyframes([[t0, 0.5], [t0 + 300, 0.5 + 1.4 * s]], t) }]);
    expect(sim.events.map((e) => e.action)).toEqual([{ type: 'lane', lane: -1 }]);
    sim.events.length = 0;
    const t1 = sim.t + 100;
    run(sim, 2000, (t) => [{ rawX: keyframes([[t1, 0.5 + 1.4 * s], [t1 + 600, 0.5 - 1.4 * s]], t) }]);
    expect(sim.events.map((e) => e.action)).toEqual([
      { type: 'lane', lane: 0 },
      { type: 'lane', lane: 1 },
    ]);
    expect(sim.last?.gestures[0]?.lane).toBe(1);
  });

  it('unmirrored tuning flips the lane direction', () => {
    const sim = makeSim();
    sim.core.setTuning({ mirror: false });
    run(sim, 300, () => [{}]);
    sim.core.lockSlots(true);
    sim.core.startCalibration(0, sim.t);
    run(sim, 1500, () => [{}]);
    sim.events.length = 0;
    const t0 = sim.t + 100;
    run(sim, 1500, (t) => [{ rawX: keyframes([[t0, 0.5], [t0 + 300, 0.5 + 1.4 * 0.15]], t) }]);
    expect(sim.events.map((e) => e.action)).toEqual([{ type: 'lane', lane: 1 }]);
  });

  it('jitter at a lane boundary → no flapping', () => {
    const sim = calibrated(7);
    const s = 0.15;
    const jr = makeRng(99);
    // Display dx oscillates in [0.7, 1.1] around T=0.9 (raw x decreases ⇒ display right).
    run(sim, 6000, () => [{ rawX: 0.5 - (0.9 + (jr() - 0.5) * 0.4) * s }]);
    expect(kinds(sim, 'lane').length).toBeLessThanOrEqual(1);
    // Also within the hysteresis band around the inner boundary.
    sim.events.length = 0;
    run(sim, 6000, () => [{ rawX: 0.5 - (0.75 + (jr() - 0.5) * 0.25) * s }]);
    expect(kinds(sim, 'lane').length).toBeLessThanOrEqual(1);
  });

  it('walking closer / farther (perspective) → no jump/duck', () => {
    const sim = calibrated(3, { scale: 0.12, horizonY: 0.45 });
    const t0 = sim.t;
    const scale = (t: number) => keyframes([[t0, 0.12], [t0 + 3000, 0.2], [t0 + 4000, 0.2], [t0 + 8000, 0.09]], t);
    // Walking bob + a horizon that doesn't match the model's assumed image center.
    run(sim, 9000, (t) => [{ scale: scale(t), lift: 0.06 * Math.sin((t - t0) / 1000 * 2 * Math.PI * 1.8), horizonY: 0.45 }]);
    expect(kinds(sim, 'jump')).toHaveLength(0);
    expect(kinds(sim, 'duck')).toHaveLength(0);
  });

  it('upper-body only (hips not visible) still jumps', () => {
    const sim = calibrated(4, { hipsVisible: false });
    const t0 = sim.t + 200;
    run(sim, 1500, (t) => [{ hipsVisible: false, lift: jumpLift(t, t0, 0.6, 450) }]);
    expect(kinds(sim, 'jump')).toHaveLength(1);
  });

  it('calibration relaxes after 3 s for a fidgety player', () => {
    const sim = makeSim();
    run(sim, 300, () => [{}]);
    sim.core.lockSlots(true);
    const start = sim.t;
    sim.core.startCalibration(0, sim.t);
    // Constant sway at ~1.4 scale/s — never "still".
    run(sim, 5000, (t) => [{ rawX: 0.5 + 0.03 * Math.sin(((t - start) / 1000) * 2 * Math.PI * 1.5) }]);
    const d = sim.core.detector(0);
    expect(d.phase).not.toBe('calibrating');
    expect(kinds(sim, 'lane')).toHaveLength(1);
  });

  it('hands-up progress fills in ~HANDS_UP_HOLD_MS and decays fast', () => {
    const sim = makeSim();
    run(sim, 500, () => [{}]);
    expect(sim.last?.people[0].handsUpProgress).toBe(0);
    run(sim, 600, () => [{ handsUp: true }]);
    const p = sim.last!.people[0];
    expect(p.handsUp).toBe(true);
    expect(p.handsUpProgress).toBeGreaterThan(0.5);
    expect(p.handsUpProgress).toBeLessThan(1);
    run(sim, 600, () => [{ handsUp: true }]);
    expect(sim.last!.people[0].handsUpProgress).toBe(1);
    run(sim, 400, () => [{}]);
    expect(sim.last!.people[0].handsUpProgress).toBe(0);
  });

  it('hints: too-far, move-right, head-cut-off', () => {
    const sim = makeSim();
    run(sim, 300, () => [{ scale: 0.05 }]);
    expect(sim.last!.people[0].hints).toContain('too-far');
    run(sim, 300, () => [{ rawX: 0.85 }]); // display x 0.15 ⇒ no room on the left
    expect(sim.last!.people[0].hints).toContain('move-right');
    run(sim, 300, () => [{ horizonOffset: -3.2 }]);
    expect(sim.last!.people[0].hints).toContain('head-cut-off');
  });

  it('display-space landmarks are mirrored', () => {
    const sim = makeSim();
    run(sim, 300, () => [{ rawX: 0.7 }]);
    expect(sim.last!.people[0].center.x).toBeCloseTo(0.3, 2);
  });

  it('lockSlots(true, [1]) makes the right-hand person the solo player', () => {
    const sim = makeSim(5, 2);
    // Display left person = higher raw x.
    const bodies = () => [{ rawX: 0.72 }, { rawX: 0.28 }];
    run(sim, 500, bodies);
    expect(sim.last!.people.map((p) => Math.round(p.center.x * 100))).toEqual([28, 72]);
    sim.core.lockSlots(true, [1]);
    expect(sim.core.playerCount).toBe(1);
    run(sim, 300, bodies);
    expect(sim.last!.people).toHaveLength(1);
    expect(sim.last!.people[0].slot).toBe(0);
    expect(sim.last!.people[0].center.x).toBeCloseTo(0.72, 2);
    expect(sim.last!.extraPeople).toBe(1);
    expect(sim.last!.gestures[1]).toBeNull();
  });
});

describe('PlayerAssigner', () => {
  const cand = (x: number, scale: number, y = 0.15): Candidate => ({ x, y, scale, confidence: 0.9 });

  function spectatorWalk(locked: boolean): void {
    const a = new PlayerAssigner();
    a.setPlayerCount(2);
    const rng = makeRng(11);
    let t = 0;
    const j = () => (rng() - 0.5) * 0.01;
    for (let k = 0; k < 10; k++) {
      t += DT;
      a.assign([cand(0.3 + j(), 0.15), cand(0.7 + j(), 0.15)], t);
    }
    if (locked) a.lock(true);
    for (let k = 0; k < 120; k++) {
      t += DT;
      const sx = 0.05 + (0.9 * k) / 120;
      // Order of MediaPipe results is unstable: shuffle.
      const list = [cand(0.3 + j(), 0.15), cand(0.7 + j(), 0.15), cand(sx, 0.08, 0.1)];
      const order = rng() < 0.5 ? [2, 0, 1] : [1, 2, 0];
      const shuffled = order.map((i) => list[i]);
      const r = a.assign(shuffled, t);
      expect(shuffled[r.slots[0]].x).toBeCloseTo(0.3, 1);
      expect(shuffled[r.slots[1]].x).toBeCloseTo(0.7, 1);
      expect(r.extra).toBe(1);
      expect(r.identityChanged).toEqual([false, false]);
    }
  }

  it('unlocked: spectator walking between two players → no swaps', () => spectatorWalk(false));
  it('locked: spectator walking between two players → no swaps', () => spectatorWalk(true));

  it('unlocked: largest candidates are players, assigned left → right', () => {
    const a = new PlayerAssigner();
    a.setPlayerCount(2);
    const r = a.assign([cand(0.8, 0.14), cand(0.5, 0.05), cand(0.2, 0.16)], 0);
    expect(r.slots).toEqual([2, 0]);
    expect(r.extra).toBe(1);
  });

  it('locked: players crossing sides keep their slots', () => {
    const a = new PlayerAssigner();
    a.setPlayerCount(2);
    let t = 0;
    a.assign([cand(0.3, 0.15), cand(0.7, 0.13)], t);
    a.lock(true);
    for (let k = 0; k <= 60; k++) {
      t += DT;
      const u = k / 60;
      const x0 = 0.3 + 0.4 * u;
      const x1 = 0.7 - 0.4 * u;
      const r = a.assign([cand(x1, 0.13, 0.25), cand(x0, 0.15)], t);
      expect(r.slots).toEqual([1, 0]);
    }
  });

  it('locked: a lost slot does not grab a far-away spectator, but re-acquires nearby', () => {
    const a = new PlayerAssigner();
    a.setPlayerCount(1);
    let t = 0;
    a.assign([cand(0.5, 0.15)], t);
    a.lock(true);
    t += 2000; // player left the frame for 2 s
    expect(a.assign([cand(0.05, 0.14)], t).slots[0]).toBe(-1);
    t += DT;
    expect(a.assign([cand(0.55, 0.07)], t).slots[0]).toBe(-1); // too small: spectator behind
    t += DT;
    const r = a.assign([cand(0.05, 0.14), cand(0.6, 0.15)], t);
    expect(r.slots[0]).toBe(1);
  });

  it('extra counts only valid (confident, large enough) candidates', () => {
    const a = new PlayerAssigner();
    const r = a.assign(
      [cand(0.5, 0.15), { x: 0.2, y: 0.15, scale: 0.12, confidence: 0.1 }, cand(0.8, 0.01), cand(0.3, 0.1)],
      0,
    );
    expect(r.slots[0]).toBe(0);
    expect(r.extra).toBe(1);
  });

  it('lockSlots(true, [1]) remaps slot 1 → slot 0', () => {
    const a = new PlayerAssigner();
    a.setPlayerCount(2);
    let t = 0;
    a.assign([cand(0.3, 0.15), cand(0.7, 0.15)], t);
    const remap = a.lock(true, [1]);
    expect(remap).toEqual([1, null]);
    expect(a.playerCount).toBe(1);
    t += DT;
    const r = a.assign([cand(0.3, 0.15), cand(0.7, 0.15)], t);
    expect(r.slots[0]).toBe(1);
    expect(r.slots[1]).toBe(-1);
    expect(r.extra).toBe(1);
    a.lock(false);
    expect(a.locked).toBe(false);
  });
});

describe('OneEuroFilter', () => {
  it('passes a constant, smooths noise', () => {
    const f = new OneEuroFilter(1, 0, 1);
    let v = 0;
    for (let i = 0; i < 100; i++) v = f.filter(i % 2 ? 0.51 : 0.49, i * 33);
    expect(Math.abs(v - 0.5)).toBeLessThan(0.01);
  });
});


describe('GestureDetector calibration sampling', () => {
  type CalibInternals = {
    calibX: number[];
    calibN: number;
    centerX: number;
    anchorY: number;
    perspScale: number;
    pushCalibSample(): void;
  };

  it('is deterministic and spans the whole calibration evenly when the buffer overflows', () => {
    const fill = (): CalibInternals => {
      const d = new GestureDetector(0);
      d.startCalibration(0);
      const g = d as unknown as CalibInternals;
      const total = C.calibMaxSamples * 3 + 7;
      for (let k = 0; k < total; k++) {
        g.centerX = k / total; // sample "time" in [0, 1)
        g.pushCalibSample();
      }
      return g;
    };
    const a = fill();
    const b = fill();
    expect(a.calibN).toBeLessThanOrEqual(C.calibMaxSamples);
    expect(a.calibN).toBeGreaterThan(C.calibMaxSamples / 2);
    expect(a.calibX.slice(0, a.calibN)).toEqual(b.calibX.slice(0, b.calibN));
    const kept = a.calibX.slice(0, a.calibN);
    // Evenly spaced and covering the whole run: no skew toward recent samples.
    expect(kept[0]).toBe(0);
    expect(kept[kept.length - 1]).toBeGreaterThan(0.9);
    const mean = kept.reduce((p, q) => p + q, 0) / kept.length;
    expect(mean).toBeGreaterThan(0.45);
    expect(mean).toBeLessThan(0.55);
    for (let i = 2; i < kept.length; i++) {
      expect(kept[i] - kept[i - 1]).toBeCloseTo(kept[1] - kept[0], 9);
    }
  });
});

describe('VisionCore latest landmarks', () => {
  it('reuses the same preallocated buffer every frame', () => {
    const core = new VisionCore();
    core.process([makeBody({ noise: 0 })], 0, SYN_YSCALE);
    const first = core.latest[0];
    expect(first).not.toBeNull();
    core.process([makeBody({ noise: 0, rawX: 0.6 })], 33, SYN_YSCALE);
    expect(core.latest[0]).toBe(first);
    expect(first![0].x).not.toBe(0);
  });
});
