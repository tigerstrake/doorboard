// Latency + false-positive guards for the early (velocity-predictive) triggers.
// Motion profiles mimic tools/fakecam (shoulder-width units). Times are ms from motion onset.
// Before the early triggers (laneThreshold 0.9, 50 ms debounce, full-threshold jump, 80 ms duck
// hold, gesture filter 2.5/15/1): lane 383/375 ms, jump 83/58 ms, duck 317/292 ms (30/60 fps).
import { describe, expect, it } from 'vitest';
import type { ActionEvent } from '../shared/types';
import { DEFAULT_VISION_TUNING } from '../shared/constants';
import { VisionCore } from './VisionCore';
import { type BodyParams, SYN_YSCALE, jumpLift, keyframes, makeBody, makeRng } from './synthetic';

const smooth = (u: number): number => (u <= 0 ? 0 : u >= 1 ? 1 : u * u * (3 - 2 * u));
const S = 0.15; // DEFAULT_BODY.scale

interface Ev { t: number; ev: string }

/** Calibrate a still person, then play `motion(msSinceOnset)` for `durMs`. */
function play(fps: number, motion: (d: number) => Partial<BodyParams>, durMs = 1500, seed = 1): Ev[] {
  const core = new VisionCore();
  core.setPlayerCount(1);
  const rng = makeRng(seed);
  const dt = 1000 / fps;
  let t = 1000;
  const events: ActionEvent[] = [];
  core.actions.on((e) => events.push(e));
  const step = (b: Partial<BodyParams>) => {
    t += dt;
    core.process([makeBody(b, rng)], t, SYN_YSCALE);
  };
  for (let k = 0; k < 500 / dt; k++) step({});
  core.lockSlots(true);
  core.startCalibration(0, t);
  for (let k = 0; k < 1500 / dt; k++) step({});
  events.length = 0;
  const t0 = t + dt / 2; // onset falls between frames
  while (t < t0 + durMs) step(motion(t + dt - t0));
  return events.map((e) => ({ t: Math.round(e.t - t0), ev: e.action.type === 'lane' ? `lane ${e.action.lane}` : e.action.type }));
}

const stepLeft = (d: number) => ({ rawX: 0.5 + 1.3 * S * smooth(d / 450) });
const hop = (d: number) => ({ lift: jumpLift(d, 0, 0.85, 420) });
const squat = (d: number) => ({ lift: -1.1 * smooth(d / 400) });

describe('gesture latency', () => {
  for (const fps of [30, 60]) {
    it(`${fps} fps: lane ≤ 250 ms, jump ≤ 50 ms after take-off, duck ≤ 250 ms`, () => {
      const lane = play(fps, stepLeft);
      const jump = play(fps, hop);
      const duck = play(fps, squat);
      expect(lane.map((e) => e.ev)).toEqual(['lane -1']);
      expect(jump.map((e) => e.ev)).toEqual(['jump']);
      expect(duck.map((e) => e.ev)).toEqual(['duck']);
      expect(lane[0].t).toBeLessThanOrEqual(250);
      expect(jump[0].t).toBeLessThanOrEqual(50);
      expect(duck[0].t).toBeLessThanOrEqual(250);
    });
  }

  it('returning to the center lane fires early too', () => {
    const ev = play(60, (d) => ({ rawX: 0.5 + 1.3 * S * (1 - smooth((d - 1000) / 450)) }), 2500);
    // first event: the step out happened instantly at onset (still a lane −1), then the return
    expect(ev.map((e) => e.ev)).toEqual(['lane -1', 'lane 0']);
    expect(ev[1].t - 1000).toBeLessThanOrEqual(260);
  });

  it('countermovement jump fires as the body rises back through the baseline', () => {
    // 180 ms crouch to −0.4, then take-off (like the fakecam jump)
    const ev = play(60, (d) => ({ lift: -0.4 * Math.sin(Math.min(1, d / 180) * Math.PI / 2) * (d < 180 ? 1 : Math.max(0, 1 - (d - 180) / 100)) + jumpLift(d, 180, 0.85, 420) }));
    expect(ev.map((e) => e.ev)).toEqual(['jump']);
    expect(ev[0].t - 180).toBeLessThanOrEqual(60);
  });
});

describe('early triggers: no false positives', () => {
  for (const fps of [30, 60]) {
    it(`${fps} fps: fast deep countermovement (−0.6 in 120 ms) → 1 jump, no duck`, () => {
      const ev = play(fps, (d) => ({
        lift: keyframes([[0, 0], [120, -0.6], [140, -0.6], [230, 0]], d) + jumpLift(d, 230, 0.8, 450),
      }), 2000);
      expect(ev.map((e) => e.ev)).toEqual(['jump']);
    });

    it(`${fps} fps: quick tiptoe bounce (0.2 in 90 ms) → nothing`, () => {
      const ev = play(fps, (d) => ({ lift: keyframes([[0, 0], [90, 0.2], [600, 0.2], [750, 0]], d) }), 1500);
      expect(ev).toEqual([]);
    });

    it(`${fps} fps: fast landing-dip recovery and fast stand-up from a squat → no extra jump`, () => {
      const ev = play(fps, (d) =>
        ({ lift: jumpLift(d, 0, 0.8, 450) + keyframes([[450, 0], [520, -0.7], [600, 0]], d) +
          keyframes([[1200, 0], [1450, -1.1], [1900, -1.1], [2100, 0.05], [2300, 0]], d) }), 3000);
      expect(ev.map((e) => e.ev)).toEqual(['jump', 'duck']);
    });

    it(`${fps} fps: brisk side-to-side sway inside the center zone → no lanes`, () => {
      const T = DEFAULT_VISION_TUNING.laneThreshold;
      const amp = T - 0.25; // stays out of the early zone (margin 0.2)
      const ev = play(fps, (d) => ({ rawX: 0.5 + amp * S * Math.sin((d / 1000) * 2 * Math.PI * 1.6) }), 5000);
      expect(ev).toEqual([]);
    });
  }

  it('heavy jitter around the default lane boundaries → no flapping', () => {
    const T = DEFAULT_VISION_TUNING.laneThreshold;
    const H = DEFAULT_VISION_TUNING.laneHysteresis;
    for (const [center, spread] of [[T, 0.4], [T - H + 0.12, 0.2], [T - 0.2, 0.16]]) {
      const jr = makeRng(42);
      const ev = play(30, () => ({ rawX: 0.5 - (center + (jr() - 0.5) * spread) * S }), 6000, 9);
      expect(ev.length).toBeLessThanOrEqual(1);
    }
  });
});
