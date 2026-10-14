import { describe, expect, it } from 'vitest';
import { LANDMARK_COUNT, LM } from '../shared/pose';
import type { Landmark } from '../shared/types';
import { makeBody } from '../vision/synthetic';
import { MimicFilter, poseFromLandmarks } from './mimic';

const Y = 720 / 1280;
const PI = Math.PI;

/** Point in width units (x right, y down) → normalized display landmark. */
const P = (x: number, yw: number, vis = 1): Landmark => ({ x, y: yw / Y, z: 0, visibility: vis });

/**
 * Display-space body (already mirrored). Angles follow the MimicPose convention per arm.
 * The person's RIGHT shoulder (index 12) is on screen-left in a mirrored selfie view.
 */
function body(opts: { lu?: number; ll?: number; ru?: number; rl?: number; lean?: number; hips?: boolean; vis?: Partial<Record<number, number>> } = {}): Landmark[] {
  const { lu = 0, ll = 0, ru = 0, rl = 0, lean = 0, hips = true, vis = {} } = opts;
  const lm: Landmark[] = Array.from({ length: LANDMARK_COUNT }, () => P(0.5, 0.3, 0));
  const up = 0.1;
  // Torso: hip-mid at (0.5, 0.4); shoulder-mid along the lean direction.
  const hm = { x: 0.5, y: 0.4 };
  const sm = { x: hm.x + Math.sin(lean) * 0.2, y: hm.y - Math.cos(lean) * 0.2 };
  const half = 0.05;
  const sL = { x: sm.x - Math.cos(lean) * half, y: sm.y - Math.sin(lean) * half };
  const sR = { x: sm.x + Math.cos(lean) * half, y: sm.y + Math.sin(lean) * half };
  const seg = (from: { x: number; y: number }, ang: number, side: -1 | 1) => ({
    x: from.x + side * Math.sin(ang) * up,
    y: from.y + Math.cos(ang) * up,
  });
  const eL = seg(sL, lu, -1);
  const wL = seg(eL, lu + ll, -1);
  const eR = seg(sR, ru, 1);
  const wR = seg(eR, ru + rl, 1);
  const set = (i: number, p: { x: number; y: number }) => {
    lm[i] = P(p.x, p.y, vis[i] ?? 1);
  };
  set(LM.RIGHT_SHOULDER, sL);
  set(LM.RIGHT_ELBOW, eL);
  set(LM.RIGHT_WRIST, wL);
  set(LM.LEFT_SHOULDER, sR);
  set(LM.LEFT_ELBOW, eR);
  set(LM.LEFT_WRIST, wR);
  if (hips) {
    set(LM.RIGHT_HIP, { x: hm.x - half, y: hm.y });
    set(LM.LEFT_HIP, { x: hm.x + half, y: hm.y });
  }
  return lm;
}

const close = (a: number, b: number, eps = 0.02) => expect(Math.abs(a - b)).toBeLessThan(eps);

describe('poseFromLandmarks', () => {
  it('arms hanging down → ~0', () => {
    const p = poseFromLandmarks(body(), Y)!;
    close(p.leftUpper, 0);
    close(p.rightUpper, 0);
    close(p.leftLower, 0);
    close(p.rightLower, 0);
    close(p.lean, 0);
    expect(p.weight).toBe(1);
  });

  it('arms straight out → ~PI/2 on both sides (sign mirrored per side)', () => {
    const p = poseFromLandmarks(body({ lu: PI / 2, ru: PI / 2 }), Y)!;
    close(p.leftUpper, PI / 2);
    close(p.rightUpper, PI / 2);
    close(p.leftLower, 0);
    close(p.rightLower, 0);
  });

  it('arms straight up → ~PI', () => {
    const p = poseFromLandmarks(body({ lu: PI, ru: PI }), Y)!;
    close(p.leftUpper, PI);
    close(p.rightUpper, PI);
  });

  it('up and slightly inward stays near PI (no wrap to -PI)', () => {
    const p = poseFromLandmarks(body({ lu: PI + 0.2, ru: PI + 0.2 }), Y)!;
    close(p.leftUpper, PI + 0.2);
    close(p.rightUpper, PI + 0.2);
  });

  it('one arm up: only the screen-right arm', () => {
    const p = poseFromLandmarks(body({ ru: PI }), Y)!;
    close(p.leftUpper, 0);
    close(p.rightUpper, PI);
  });

  it('screen-left is the smaller-x shoulder regardless of landmark naming', () => {
    // Swap the person-left/right indices: unmirrored data puts index 11 on screen-left.
    const lm = body({ lu: PI / 2 });
    const sw = (a: number, b: number) => ([lm[a], lm[b]] = [lm[b], lm[a]]);
    sw(LM.LEFT_SHOULDER, LM.RIGHT_SHOULDER);
    sw(LM.LEFT_ELBOW, LM.RIGHT_ELBOW);
    sw(LM.LEFT_WRIST, LM.RIGHT_WRIST);
    const p = poseFromLandmarks(lm, Y)!;
    close(p.leftUpper, PI / 2);
    close(p.rightUpper, 0);
  });

  it('bent elbow: arm out, forearm up → lower ≈ +PI/2', () => {
    const p = poseFromLandmarks(body({ lu: PI / 2, ll: PI / 2, ru: PI / 2, rl: PI / 2 }), Y)!;
    close(p.leftUpper, PI / 2);
    close(p.leftLower, PI / 2);
    close(p.rightLower, PI / 2);
  });

  it('leaning torso: + = top tilts to screen-right (with and without hips)', () => {
    const right = poseFromLandmarks(body({ lean: 0.3 }), Y)!;
    close(right.lean, 0.3);
    const left = poseFromLandmarks(body({ lean: -0.25 }), Y)!;
    close(left.lean, -0.25);
    const noHips = poseFromLandmarks(body({ lean: 0.3, hips: false }), Y)!;
    close(noHips.lean, 0.3);
  });

  it('low-visibility joints use a neutral value with lower weight', () => {
    const p = poseFromLandmarks(body({ lu: PI, ru: PI, vis: { [LM.RIGHT_ELBOW]: 0.2 } }), Y)!;
    close(p.leftUpper, 0);
    close(p.leftLower, 0);
    close(p.rightUpper, PI);
    expect(p.weight).toBeLessThan(1);
    expect(p.weight).toBeGreaterThan(0);
  });

  it('no visible shoulders → null', () => {
    expect(poseFromLandmarks(body({ vis: { [LM.LEFT_SHOULDER]: 0.1 } }), Y)).toBeNull();
    expect(poseFromLandmarks([], Y)).toBeNull();
  });

  it('works on vision/synthetic makeBody output (mirrored into display space)', () => {
    const mirror = (lm: ReturnType<typeof makeBody>): Landmark[] => lm.map((p) => ({ x: 1 - p.x, y: p.y, z: p.z ?? 0, visibility: p.visibility ?? 1 }));
    const down = poseFromLandmarks(mirror(makeBody({ noise: 0 })), Y)!;
    const up = poseFromLandmarks(mirror(makeBody({ noise: 0, handsUp: true })), Y)!;
    expect(Math.abs(down.leftUpper)).toBeLessThan(0.6);
    expect(Math.abs(down.rightUpper)).toBeLessThan(0.6);
    expect(up.leftUpper).toBeGreaterThan(2.2);
    expect(up.rightUpper).toBeGreaterThan(2.2);
  });
});

describe('MimicFilter', () => {
  it('smooths toward the target and fades out when lost', () => {
    const f = new MimicFilter();
    const down = body();
    const up = body({ lu: PI, ru: PI });
    let t = 0;
    for (let i = 0; i < 30; i++) f.update(down, (t += 1 / 60), Y);
    expect(f.current!.weight).toBeGreaterThan(0.99);
    const one = f.update(up, (t += 1 / 60), Y)!;
    expect(one.leftUpper).toBeGreaterThan(0.1);
    expect(one.leftUpper).toBeLessThan(PI / 2); // not snapped
    for (let i = 0; i < 30; i++) f.update(up, (t += 1 / 60), Y);
    close(f.current!.leftUpper, PI, 0.05);
    const lost = f.update(null, (t += 0.15), Y)!;
    expect(lost.weight).toBeGreaterThan(0.3);
    expect(lost.weight).toBeLessThan(0.7);
    close(lost.leftUpper, PI, 0.05); // holds the last pose while fading
    expect(f.update(null, (t += 0.2), Y)).toBeNull();
  });
});
