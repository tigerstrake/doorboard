// Player upper-body pose → MimicPose for the runner (Settings.mimic).
// Pure extraction (poseFromLandmarks) + a small per-slot temporal filter (MimicFilter).
import { LM } from '../shared/pose';
import type { Landmark, MimicPose } from '../shared/types';

/** Landmarks below this visibility are treated as missing. */
export const MIMIC_MIN_VIS = 0.5;
/** Exponential smoothing time constant (s). */
export const MIMIC_TAU_S = 0.06;
/** Weight fade-out time when the person is lost (s). */
export const MIMIC_FADE_S = 0.3;
/** Default videoHeight / videoWidth (1280×720 camera). */
export const DEFAULT_Y_SCALE = 720 / 1280;

const PI = Math.PI;
const TAU = 2 * PI;

/** Wrap to (-PI, PI]. */
function wrapPi(a: number): number {
  let r = a % TAU;
  if (r <= -PI) r += TAU;
  else if (r > PI) r -= TAU;
  return r;
}

const ok = (p: Landmark | undefined): p is Landmark =>
  p !== undefined && Number.isFinite(p.x) && Number.isFinite(p.y) && (p.visibility ?? 0) >= MIMIC_MIN_VIS;

/**
 * Angle of segment a→b in the MimicPose convention for an arm whose outward direction is
 * `side` (-1 = screen-left arm, +1 = screen-right). 0 = down, PI/2 = outward, PI = up.
 * Range (-PI/2, 3PI/2] so "up and slightly inward" stays near PI instead of jumping to -PI.
 */
function armAngle(a: Landmark, b: Landmark, side: -1 | 1, yScale: number): number {
  const out = (b.x - a.x) * side;
  const down = (b.y - a.y) * yScale;
  let ang = Math.atan2(out, down);
  if (ang <= -PI / 2) ang += TAU;
  return ang;
}

interface ArmResult {
  upper: number;
  lower: number;
  /** Joints (elbow, wrist) that were missing. */
  missing: number;
}

function arm(sh: Landmark, el: Landmark | undefined, wr: Landmark | undefined, side: -1 | 1, yScale: number): ArmResult {
  if (!ok(el)) return { upper: 0, lower: 0, missing: 2 };
  const upper = armAngle(sh, el, side, yScale);
  if (!ok(wr)) return { upper, lower: 0, missing: 1 };
  const fore = armAngle(el, wr, side, yScale);
  return { upper, lower: wrapPi(fore - upper), missing: 0 };
}

/**
 * Extract the player's arm/torso pose from 33 display-space (mirrored) landmarks.
 * Returns null when the shoulders aren't visible. Missing elbows/wrists fall back to a
 * neutral (hanging, straight) value and lower the returned weight.
 */
export function poseFromLandmarks(landmarks: readonly Landmark[], yScale = DEFAULT_Y_SCALE): MimicPose | null {
  if (landmarks.length <= LM.RIGHT_HIP) return null;
  const s11 = landmarks[LM.LEFT_SHOULDER];
  const s12 = landmarks[LM.RIGHT_SHOULDER];
  if (!ok(s11) || !ok(s12)) return null;
  // Screen-left arm = the shoulder with the smaller x (independent of MediaPipe's naming).
  const firstIsLeft = s11.x <= s12.x;
  const [lSh, lEl, lWr] = firstIsLeft
    ? [s11, landmarks[LM.LEFT_ELBOW], landmarks[LM.LEFT_WRIST]]
    : [s12, landmarks[LM.RIGHT_ELBOW], landmarks[LM.RIGHT_WRIST]];
  const [rSh, rEl, rWr] = firstIsLeft
    ? [s12, landmarks[LM.RIGHT_ELBOW], landmarks[LM.RIGHT_WRIST]]
    : [s11, landmarks[LM.LEFT_ELBOW], landmarks[LM.LEFT_WRIST]];

  const L = arm(lSh, lEl, lWr, -1, yScale);
  const R = arm(rSh, rEl, rWr, 1, yScale);

  // Lean: torso line (hip-mid → shoulder-mid) when hips are visible, else shoulder-line tilt.
  const h23 = landmarks[LM.LEFT_HIP];
  const h24 = landmarks[LM.RIGHT_HIP];
  let lean: number;
  if (ok(h23) && ok(h24)) {
    const ux = (lSh.x + rSh.x) / 2 - (h23.x + h24.x) / 2;
    const uy = ((lSh.y + rSh.y) / 2 - (h23.y + h24.y) / 2) * yScale; // negative when upright
    lean = Math.atan2(ux, -uy);
  } else {
    // Leaning right drops the screen-right shoulder (larger y).
    lean = Math.atan2((rSh.y - lSh.y) * yScale, rSh.x - lSh.x);
  }

  const missing = L.missing + R.missing;
  return {
    leftUpper: L.upper,
    leftLower: L.lower,
    rightUpper: R.upper,
    rightLower: R.lower,
    lean,
    weight: Math.max(0, 1 - 0.15 * missing),
  };
}

/** Light exponential smoothing + fade-out on loss, one instance per player slot. */
export class MimicFilter {
  private pose: MimicPose | null = null;
  private lastT: number | null = null;

  constructor(
    private readonly tau = MIMIC_TAU_S,
    private readonly fade = MIMIC_FADE_S,
  ) {}

  /** `landmarks` null/undefined = person not visible. `t` in seconds. */
  update(landmarks: readonly Landmark[] | null | undefined, t: number, yScale = DEFAULT_Y_SCALE): MimicPose | null {
    const dt = this.lastT === null ? 0 : Math.max(0, Math.min(0.25, t - this.lastT));
    this.lastT = t;
    const target = landmarks ? poseFromLandmarks(landmarks, yScale) : null;
    const prev = this.pose;
    if (!target) {
      if (!prev) return null;
      const weight = this.fade > 0 ? prev.weight - dt / this.fade : 0;
      this.pose = weight > 0 ? { ...prev, weight } : null;
      return this.pose;
    }
    if (!prev) {
      // Snap the angles, ramp the weight in.
      this.pose = { ...target, weight: dt > 0 ? target.weight * this.alpha(dt) : 0 };
      return this.pose;
    }
    const a = this.alpha(dt);
    const lerpA = (p: number, q: number): number => p + wrapPi(q - p) * a;
    this.pose = {
      leftUpper: lerpA(prev.leftUpper, target.leftUpper),
      leftLower: lerpA(prev.leftLower, target.leftLower),
      rightUpper: lerpA(prev.rightUpper, target.rightUpper),
      rightLower: lerpA(prev.rightLower, target.rightLower),
      lean: lerpA(prev.lean, target.lean),
      weight: prev.weight + (target.weight - prev.weight) * a,
    };
    return this.pose;
  }

  reset(): void {
    this.pose = null;
    this.lastT = null;
  }

  get current(): MimicPose | null {
    return this.pose;
  }

  private alpha(dt: number): number {
    return this.tau > 0 ? 1 - Math.exp(-dt / this.tau) : 1;
  }
}
