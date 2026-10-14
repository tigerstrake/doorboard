// Synthetic body generator for tests: produces 33 MediaPipe-style landmarks in RAW CAMERA
// space (unmirrored), as the PoseLandmarker would report for a person facing the camera.
// The person's LEFT side appears on the image's RIGHT (higher raw x).
//
// Geometry is in "shoulder-width" units, converted to normalized image coordinates with the
// camera aspect (yScale = h / w). A pinhole perspective model places the shoulders relative to
// the camera's optical axis: shoulderY = 0.5 + horizonOffset · scale (in width units), so walking
// toward the camera moves the shoulders away from the image center proportionally.
import type { RawLandmark } from './features';
import { LM, LANDMARK_COUNT } from '../shared/pose';

export const SYN_YSCALE = 720 / 1280;

export interface BodyParams {
  /** Raw camera-space x of the torso center (0..1). */
  rawX: number;
  /** Shoulder width in normalized image-width units. */
  scale: number;
  /** Vertical body offset in shoulder widths, positive = UP (jump > 0, squat < 0). */
  lift: number;
  /** Shoulders sit this many shoulder widths above (negative) / below the optical axis. */
  horizonOffset: number;
  /** Optical-axis y in normalized image height (0.5 = centered). */
  horizonY: number;
  handsUp: boolean;
  hipsVisible: boolean;
  visibility: number;
  /** Per-landmark positional jitter, normalized units (deterministic). */
  noise: number;
}

export const DEFAULT_BODY: BodyParams = {
  rawX: 0.5,
  scale: 0.15,
  lift: 0,
  horizonOffset: -1.0,
  horizonY: 0.5,
  handsUp: false,
  hipsVisible: true,
  visibility: 0.95,
  noise: 0.0015,
};

/** Template in shoulder widths: [dx (person's left = +), dy (down = +)] relative to shoulder mid. */
function template(handsUp: boolean): Array<[number, number]> {
  const p: Array<[number, number]> = new Array(LANDMARK_COUNT).fill(null).map(() => [0, 0] as [number, number]);
  const set = (i: number, x: number, y: number) => (p[i] = [x, y]);
  set(LM.NOSE, 0, -0.75);
  set(LM.LEFT_EYE_INNER, 0.05, -0.85);
  set(LM.LEFT_EYE, 0.09, -0.86);
  set(LM.LEFT_EYE_OUTER, 0.13, -0.86);
  set(LM.RIGHT_EYE_INNER, -0.05, -0.85);
  set(LM.RIGHT_EYE, -0.09, -0.86);
  set(LM.RIGHT_EYE_OUTER, -0.13, -0.86);
  set(LM.LEFT_EAR, 0.2, -0.8);
  set(LM.RIGHT_EAR, -0.2, -0.8);
  set(LM.MOUTH_LEFT, 0.06, -0.65);
  set(LM.MOUTH_RIGHT, -0.06, -0.65);
  set(LM.LEFT_SHOULDER, 0.5, 0);
  set(LM.RIGHT_SHOULDER, -0.5, 0);
  if (handsUp) {
    set(LM.LEFT_ELBOW, 0.65, -0.6);
    set(LM.RIGHT_ELBOW, -0.65, -0.6);
    set(LM.LEFT_WRIST, 0.55, -1.4);
    set(LM.RIGHT_WRIST, -0.55, -1.4);
  } else {
    set(LM.LEFT_ELBOW, 0.6, 0.7);
    set(LM.RIGHT_ELBOW, -0.6, 0.7);
    set(LM.LEFT_WRIST, 0.62, 1.35);
    set(LM.RIGHT_WRIST, -0.62, 1.35);
  }
  for (const [w, s, hand] of [
    [LM.LEFT_WRIST, 1, [LM.LEFT_PINKY, LM.LEFT_INDEX, LM.LEFT_THUMB]],
    [LM.RIGHT_WRIST, -1, [LM.RIGHT_PINKY, LM.RIGHT_INDEX, LM.RIGHT_THUMB]],
  ] as const) {
    const [wx, wy] = p[w];
    const dir = handsUp ? -1 : 1;
    set(hand[0], wx + 0.05 * s, wy + 0.12 * dir);
    set(hand[1], wx, wy + 0.15 * dir);
    set(hand[2], wx - 0.05 * s, wy + 0.1 * dir);
  }
  set(LM.LEFT_HIP, 0.3, 1.3);
  set(LM.RIGHT_HIP, -0.3, 1.3);
  set(LM.LEFT_KNEE, 0.3, 2.3);
  set(LM.RIGHT_KNEE, -0.3, 2.3);
  set(LM.LEFT_ANKLE, 0.3, 3.3);
  set(LM.RIGHT_ANKLE, -0.3, 3.3);
  set(LM.LEFT_HEEL, 0.3, 3.4);
  set(LM.RIGHT_HEEL, -0.3, 3.4);
  set(LM.LEFT_FOOT_INDEX, 0.35, 3.5);
  set(LM.RIGHT_FOOT_INDEX, -0.35, 3.5);
  return p;
}

const TEMPLATE_DOWN = template(false);
const TEMPLATE_UP = template(true);

/** Deterministic PRNG (mulberry32). */
export function makeRng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function makeBody(params: Partial<BodyParams>, rng: () => number = Math.random, yScale = SYN_YSCALE): RawLandmark[] {
  const b = { ...DEFAULT_BODY, ...params };
  const tpl = b.handsUp ? TEMPLATE_UP : TEMPLATE_DOWN;
  const s = b.scale;
  // Shoulder-mid y in width units (pinhole model), then the lift.
  const shoulderYw = b.horizonY * yScale + b.horizonOffset * s - b.lift * s;
  const out: RawLandmark[] = [];
  for (let i = 0; i < LANDMARK_COUNT; i++) {
    const [tx, ty] = tpl[i];
    // Legs stay planted for squats/jumps: knees/ankles don't follow the lift fully.
    const isLeg = i >= LM.LEFT_KNEE;
    const ly = isLeg && b.lift < 0 ? -b.lift * s * (i >= LM.LEFT_ANKLE ? 1 : 0.5) : 0;
    // Person's left (+tx) appears at higher raw camera x.
    const x = b.rawX + tx * s + (rng() - 0.5) * 2 * b.noise;
    const yw = shoulderYw + ty * s + ly;
    const y = yw / yScale + (rng() - 0.5) * 2 * b.noise;
    let vis = b.visibility;
    if (!b.hipsVisible && i >= LM.LEFT_HIP) vis = 0.1;
    out.push({ x, y, z: 0, visibility: vis });
  }
  return out;
}

/** Piecewise-linear keyframe interpolation: keys = [[tMs, value], ...]. */
export function keyframes(keys: Array<[number, number]>, t: number): number {
  if (t <= keys[0][0]) return keys[0][1];
  for (let i = 1; i < keys.length; i++) {
    if (t <= keys[i][0]) {
      const [t0, v0] = keys[i - 1];
      const [t1, v1] = keys[i];
      return v0 + ((v1 - v0) * (t - t0)) / (t1 - t0);
    }
  }
  return keys[keys.length - 1][1];
}

/** Ballistic jump profile: lift (shoulder widths) at time `t` for a jump starting at t0. */
export function jumpLift(t: number, t0: number, height = 0.7, durMs = 500): number {
  const u = (t - t0) / durMs;
  if (u <= 0 || u >= 1) return 0;
  return 4 * height * u * (1 - u);
}
