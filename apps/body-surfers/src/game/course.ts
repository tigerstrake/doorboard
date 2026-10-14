// Speed curve as a pure function of time AND of track position, so the generator
// can know the speed at any future row without depending on when it runs.
import { clamp } from '../shared/math';
import { CONFIG as C } from './config';

const DV = C.speedMax - C.speedMin;

function rawSpeed(t: number): number {
  return C.speedMin + DV * (1 - Math.exp(-t / C.speedTau)) + C.speedRamp * t;
}

function rawDistance(t: number): number {
  return C.speedMax * t - DV * C.speedTau * (1 - Math.exp(-t / C.speedTau)) + 0.5 * C.speedRamp * t * t;
}

/** Difficulty time at which the endless ramp hits speedCap (rawSpeed is monotonic). */
const T_CAP = (() => {
  let lo = 0;
  let hi = 1;
  while (rawSpeed(hi) < C.speedCap && hi < 1e6) hi *= 2;
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2;
    if (rawSpeed(mid) < C.speedCap) lo = mid;
    else hi = mid;
  }
  return hi;
})();
const D_CAP = rawDistance(T_CAP);

/** Speed after `t` seconds of (post-tutorial) difficulty time: eases toward speedMax, keeps ramping, capped. */
export function speedAtDiffTime(t: number): number {
  return t >= T_CAP ? C.speedCap : rawSpeed(t);
}

export function levelOfSpeed(v: number): number {
  return clamp((v - C.speedMin) / DV, 0, 1);
}

/** Distance covered after `t` seconds of difficulty time. */
export function distanceAtDiffTime(t: number): number {
  return t >= T_CAP ? D_CAP + C.speedCap * (t - T_CAP) : rawDistance(t);
}

/** Inverse of distanceAtDiffTime (Newton). */
export function diffTimeAtDistance(d: number): number {
  if (d <= 0) return 0;
  let t = d / C.speedMin;
  for (let i = 0; i < 30; i++) {
    const f = distanceAtDiffTime(t) - d;
    const v = speedAtDiffTime(t);
    const nt = t - f / v;
    if (Math.abs(nt - t) < 1e-9) return nt;
    t = nt < 0 ? 0 : nt;
  }
  return t;
}

/** Track-position → time/speed model for one run. */
export class Course {
  /** Track position where difficulty time starts (end of tutorial, or 0). */
  constructor(readonly difficultyStart: number) {}

  /** Running time at which the runner reaches track position s. */
  timeAt(s: number): number {
    const s0 = this.difficultyStart;
    if (s <= s0) return s / C.speedMin;
    return s0 / C.speedMin + diffTimeAtDistance(s - s0);
  }

  speedAt(s: number): number {
    if (s <= this.difficultyStart) return C.speedMin;
    return speedAtDiffTime(diffTimeAtDistance(s - this.difficultyStart));
  }

  levelAt(s: number): number {
    return levelOfSpeed(this.speedAt(s));
  }
}
