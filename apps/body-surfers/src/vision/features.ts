// Pure body-feature extraction (no DOM). Coordinates: x in normalized image-width units,
// y in normalized image-height units. Wherever a vertical distance is compared with the body
// scale it is first multiplied by `yScale` (= videoHeight / videoWidth) so both axes share
// physical units.
import type { Landmark } from '../shared/types';
import { LM, LANDMARK_COUNT } from '../shared/pose';
import { VISION_CONFIG as C } from './config';

/** MediaPipe NormalizedLandmark-compatible input. */
export interface RawLandmark {
  x: number;
  y: number;
  z?: number;
  visibility?: number;
}

/** True if the pose carries real visibility scores (some runtimes report all zeros). */
export function poseHasVisibility(lm: ReadonlyArray<RawLandmark>): boolean {
  for (let i = 0; i < lm.length; i++) {
    const v = lm[i].visibility;
    if (v !== undefined && v > 0.01) return true;
  }
  return false;
}

/** Visibility with a fallback of 1 for runtimes that don't report it. */
export function visibilityOf(l: RawLandmark, hasVisibility: boolean): number {
  if (!hasVisibility) return 1;
  const v = l.visibility;
  return v === undefined || !Number.isFinite(v) ? 0 : v;
}

export function isValidPose(lm: ReadonlyArray<RawLandmark> | undefined | null): lm is ReadonlyArray<RawLandmark> {
  if (!lm || lm.length < LANDMARK_COUNT) return false;
  const a = lm[LM.LEFT_SHOULDER];
  const b = lm[LM.RIGHT_SHOULDER];
  return Number.isFinite(a.x) && Number.isFinite(a.y) && Number.isFinite(b.x) && Number.isFinite(b.y);
}

/** Per-frame, unsmoothed person summary used for slot assignment. */
export interface Candidate {
  /** Torso anchor x, display space. */
  x: number;
  /** Torso anchor y, aspect-corrected (normalized y × yScale). */
  y: number;
  /** Raw body scale (width units). */
  scale: number;
  confidence: number;
}

/** Compute the assignment candidate for one raw (camera-space) pose. */
export function rawCandidate(
  lm: ReadonlyArray<RawLandmark>,
  mirror: boolean,
  yScale: number,
  out: Candidate,
): Candidate {
  const hasVis = poseHasVisibility(lm);
  const vis = (i: number): number => visibilityOf(lm[i], hasVis);
  const ls = lm[LM.LEFT_SHOULDER];
  const rs = lm[LM.RIGHT_SHOULDER];
  const lh = lm[LM.LEFT_HIP];
  const rh = lm[LM.RIGHT_HIP];
  const smx = (ls.x + rs.x) / 2;
  const smy = (ls.y + rs.y) / 2;
  const sw = Math.hypot(ls.x - rs.x, (ls.y - rs.y) * yScale);
  const hips = vis(LM.LEFT_HIP) > 0.5 && vis(LM.RIGHT_HIP) > 0.5;
  let scale = sw;
  let ax = smx;
  if (hips) {
    const hmx = (lh.x + rh.x) / 2;
    const hmy = (lh.y + rh.y) / 2;
    const torso = Math.hypot(smx - hmx, (smy - hmy) * yScale);
    scale = Math.max(sw, C.torsoScaleFactor * torso);
    ax = (1 - C.hipAnchorWeight) * smx + C.hipAnchorWeight * hmx;
  }
  out.x = mirror ? 1 - ax : ax;
  out.y = smy * yScale;
  out.scale = scale;
  out.confidence = (vis(LM.NOSE) + vis(LM.LEFT_SHOULDER) + vis(LM.RIGHT_SHOULDER)) / 3;
  return out;
}

/** Geometric features of a (smoothed, display-space) landmark set. */
export interface BodyFeatures {
  shoulderMidX: number;
  shoulderMidY: number;
  /** Width units. */
  shoulderWidth: number;
  hipMidX: number;
  hipMidY: number;
  /** Raw hip visibility test (both > hipVisOn / either < hipVisOff handled by the caller). */
  hipVisMin: number;
  /** Width units; 0 when hips are not used. */
  torsoLen: number;
  /** max(shoulderWidth, 0.55·torsoLen) (unsmoothed). */
  rawScale: number;
  /** 0..1 mean visibility of nose + shoulders. */
  confidence: number;
}

export function createBodyFeatures(): BodyFeatures {
  return {
    shoulderMidX: 0.5,
    shoulderMidY: 0.5,
    shoulderWidth: 0,
    hipMidX: 0.5,
    hipMidY: 0.5,
    hipVisMin: 0,
    torsoLen: 0,
    rawScale: 0,
    confidence: 0,
  };
}

export function measureBody(lm: ReadonlyArray<Landmark>, yScale: number, useHips: boolean, out: BodyFeatures): BodyFeatures {
  const ls = lm[LM.LEFT_SHOULDER];
  const rs = lm[LM.RIGHT_SHOULDER];
  const lh = lm[LM.LEFT_HIP];
  const rh = lm[LM.RIGHT_HIP];
  out.shoulderMidX = (ls.x + rs.x) / 2;
  out.shoulderMidY = (ls.y + rs.y) / 2;
  out.shoulderWidth = Math.hypot(ls.x - rs.x, (ls.y - rs.y) * yScale);
  out.hipMidX = (lh.x + rh.x) / 2;
  out.hipMidY = (lh.y + rh.y) / 2;
  out.hipVisMin = Math.min(lh.visibility, rh.visibility);
  out.torsoLen = useHips ? Math.hypot(out.shoulderMidX - out.hipMidX, (out.shoulderMidY - out.hipMidY) * yScale) : 0;
  out.rawScale = Math.max(out.shoulderWidth, C.torsoScaleFactor * out.torsoLen);
  out.confidence = (lm[LM.NOSE].visibility + ls.visibility + rs.visibility) / 3;
  return out;
}

/**
 * §5.6: both wrists above the nose by 0.1·scale (visibility > 0.4), or both elbows above
 * their shoulders by 0.2·scale (wrists often leave the frame).
 */
export function isHandsUp(lm: ReadonlyArray<Landmark>, scale: number, yScale: number): boolean {
  const minVis = C.handsMinVisibility;
  const toY = scale / yScale; // scale expressed in normalized-y units
  const nose = lm[LM.NOSE];
  const lw = lm[LM.LEFT_WRIST];
  const rw = lm[LM.RIGHT_WRIST];
  if (lw.visibility > minVis && rw.visibility > minVis) {
    const line = nose.y - C.handsWristAboveNose * toY;
    if (lw.y < line && rw.y < line) return true;
  }
  const le = lm[LM.LEFT_ELBOW];
  const re = lm[LM.RIGHT_ELBOW];
  if (le.visibility > minVis && re.visibility > minVis) {
    const d = C.handsElbowAboveShoulder * toY;
    if (le.y < lm[LM.LEFT_SHOULDER].y - d && re.y < lm[LM.RIGHT_SHOULDER].y - d) return true;
  }
  return false;
}

/** Hands down enough for calibration: no visible wrist above the shoulder line, no raised elbow. */
export function isHandsDown(lm: ReadonlyArray<Landmark>, scale: number, yScale: number): boolean {
  const minVis = C.handsMinVisibility;
  const toY = scale / yScale;
  const shoulderY = (lm[LM.LEFT_SHOULDER].y + lm[LM.RIGHT_SHOULDER].y) / 2;
  const lw = lm[LM.LEFT_WRIST];
  const rw = lm[LM.RIGHT_WRIST];
  if (lw.visibility > minVis && lw.y < shoulderY) return false;
  if (rw.visibility > minVis && rw.y < shoulderY) return false;
  const le = lm[LM.LEFT_ELBOW];
  const re = lm[LM.RIGHT_ELBOW];
  const d = 0.1 * toY;
  if (le.visibility > minVis && le.y < lm[LM.LEFT_SHOULDER].y - d) return false;
  if (re.visibility > minVis && re.y < lm[LM.RIGHT_SHOULDER].y - d) return false;
  return true;
}
