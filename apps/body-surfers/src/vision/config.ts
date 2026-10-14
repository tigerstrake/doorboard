// Vision-module tunables. Player-facing thresholds (lane / jump / duck / smoothing / mirror)
// live in VisionTuning (settings panel); everything else is here. All "scale" units are
// multiples of the person's body scale (≈ shoulder width) so behaviour is distance-independent.
// Vertical distances are aspect-corrected (normalized y × videoHeight/videoWidth) before being
// divided by the scale, so "0.3 scale" means the same physical rise on any camera aspect.
import type { PoseModel } from '../shared/types';

export const VISION_CONFIG = {
  // ---- MediaPipe -------------------------------------------------------------------------
  maxPoses: 3,
  /** Extra pose slot beyond playerCount (lets us ignore spectators). Applied while locked too. */
  extraPoseWhenLocked: true,
  minPoseDetectionConfidence: 0.5,
  minPosePresenceConfidence: 0.5,
  minTrackingConfidence: 0.5,
  defaultModel: 'lite' as PoseModel,

  // ---- Camera ----------------------------------------------------------------------------
  cameraWidth: 1280,
  cameraHeight: 720,
  cameraFps: 60,
  /** Give up waiting for the first decoded frame after this long. */
  cameraStartTimeoutMs: 8000,
  /** requestVideoFrameCallback silent this long ⇒ fall back to polling video.currentTime. */
  rvfcStaleMs: 700,
  /**
   * Inference slower than this (EMA) ⇒ cap inference at ~40 Hz to protect the render loop.
   * Below it every camera frame is inferred (M-series GPU: ~10–14 ms ⇒ unthrottled, even at 60 fps).
   */
  slowInferenceMs: 16,
  slowInferenceMinIntervalMs: 24,

  // ---- One Euro filters (per landmark x/y) ---------------------------------------------
  // Two paths: a light GESTURE path that feeds GestureDetector (low lag on jumps), and the
  // heavier OVERLAY path (tuning.smoothing) used only for the drawn skeleton / TrackedPerson.
  gestureMinCutoff: 3.0,
  /** Per normalized-unit/s (not per scale): beta ≈ 15 keeps fast motion lag-free. */
  gestureBeta: 25,
  gestureDCutoff: 2.0,
  /** OVERLAY path: tuning.smoothing 0 → 3.0 Hz, 1 → 0.4 Hz. */
  minCutoffAtSmoothing0: 3.0,
  minCutoffAtSmoothing1: 0.4,
  /** OVERLAY path speed term, per (body scale / s) — speeds are normalized by the person's scale. */
  beta: 0.6,
  dCutoff: 1.0,

  // ---- Body features -----------------------------------------------------------------------
  scaleTauMs: 800,
  /** Faster scale estimate for the perspective baseline model (walking toward the camera). */
  perspScaleTauMs: 150,
  torsoScaleFactor: 0.55,
  /** Hips count as visible above `hipVisOn`, stop counting below `hipVisOff` (hysteresis). */
  hipVisOn: 0.55,
  hipVisOff: 0.45,
  /** Weight of hipMid.x in the anchor when hips are visible (spec: 0.5), blended in at this rate (1/s). */
  hipAnchorWeight: 0.5,
  hipBlendRate: 8,

  // ---- Kinematics ------------------------------------------------------------------------
  /** Finite-difference window for the vertical velocity (vUp). */
  velWindowMs: 70,
  /** Short finite-difference window (one frame at 30 fps, two at 60 fps) for the early jump / fast duck checks. */
  instVelWindowMs: 30,
  /** The jump gate uses the peak vUp over this recent window (small jumps peak before the apex). */
  vPeakWindowMs: 160,
  /** Window for the (slower) "stillness" speed used by calibration. */
  calmWindowMs: 160,
  /** Frame gap that clears the velocity history. */
  gapResetMs: 250,
  /** dt used for accumulators is capped at this value (ms). */
  maxFrameDtMs: 100,

  // ---- Calibration -------------------------------------------------------------------------
  calibMinConfidence: 0.6,
  calibMaxSpeed: 0.5,
  calibDurationMs: 1000,
  /** After this long, accept any visible hands-down frame (stillness no longer required). */
  calibRelaxAfterMs: 3000,
  calibRelaxMinConfidence: 0.35,
  /** Last resort: after this long accept any visible frame, even with hands up. */
  calibAnyAfterMs: 6000,
  calibMaxSamples: 512,
  /** Lane boundaries (centerX ± T·scale) are kept inside [edge, 1 - edge]. */
  edgeMargin: 0.03,

  // ---- Lanes -------------------------------------------------------------------------------
  /** A zone change must persist this long (slow drifts; fast steps use the early path below). */
  laneDebounceMs: 30,
  /** No debounce when the lateral speed toward the new zone exceeds this (scale/s). */
  laneFastV: 1.5,
  /**
   * Early lane trigger: a fast step fires `laneEarlyMargin` before the zone boundary (capped at
   * laneEarlyHysteresisShare·hysteresis, so the new lane is already "sticky" when it fires).
   * Needs lateral speed ≥ laneEarlyMinV and a net travel ≥ laneEarlyMinTravel toward the zone over
   * laneTravelWindowMs — a sustained step, not jitter.
   */
  laneEarlyMargin: 0.25,
  laneEarlyHysteresisShare: 0.67,
  laneEarlyMinV: 2.0,
  laneEarlyMinTravel: 0.3,
  laneTravelWindowMs: 150,

  // ---- Jump / duck -------------------------------------------------------------------------
  jumpMinVUp: 1.5,
  /**
   * Velocity-predictive jump: fire at jumpEarlyFraction·J (instead of J) when the short-window upward
   * speed is ≥ jumpEarlyVUp (scale/s; real take-offs reach 6–8, heel raises stay well below), or
   * as soon as the anchor rises fast back through the baseline after a dip ≥ J deep
   * (countermovement → take-off). Off for jumpEarlyGuardMs after landing / un-ducking, so
   * recovering from a landing dip or standing up from a squat is never a jump.
   */
  jumpEarlyFraction: 0.7,
  jumpEarlyVUp: 4.0,
  jumpEarlyDipWindowMs: 400,
  jumpEarlyGuardMs: 400,
  jumpRefractoryMs: 250,
  /** Airborne ends when dy < landFraction·J for landHoldMs (or after airborneTimeoutMs). */
  landFraction: 0.4,
  landHoldMs: 80,
  airborneTimeoutMs: 1200,
  /** Sustain before a duck fires (80 ms: ignores the countermovement dip before a hop). */
  duckHoldMs: 80,
  /**
   * Fast duck: still dropping fast (short-window speed ≥ duckFastVDown scale/s) duckFastHoldMs after
   * crossing the duck line ⇒ fire. A jump's countermovement dip has bottomed out by then (speed ≈ 0).
   */
  duckFastVDown: 2.5,
  duckFastHoldMs: 30,
  /** Duck also needs a downward anchor speed peak > this (scale/s) within duckVelWindowMs — slow sinking (walking away, slouching) never ducks. */
  duckMinVDown: 0.7,
  duckVelWindowMs: 600,
  /** Ducking ends when dy > -duckReleaseFraction·D. */
  duckReleaseFraction: 0.5,
  /** Landing-dip guard: no ducks this long after airborne ends. */
  landingDuckGuardMs: 200,

  // ---- Baseline drift ------------------------------------------------------------------------
  driftMaxDy: 0.15,
  driftMaxV: 0.5,
  driftCalmMs: 500,
  driftRate: 0.5,
  // Distance changes are handled by the perspective baseline model in GestureDetector.

  // ---- Re-acquisition (addition): after a tracking gap the baseline re-settles --------------
  settleAfterGapMs: 500,
  settleMs: 400,
  settleRate: 6,

  // ---- Hands up --------------------------------------------------------------------------
  handsWristAboveNose: 0.1,
  handsElbowAboveShoulder: 0.2,
  handsMinVisibility: 0.4,
  handsDecayFactor: 3,

  // ---- Player assignment -----------------------------------------------------------------
  candMinConfidence: 0.5,
  candMinScale: 0.04,
  /** Frame-to-frame match gate: min(matchMaxDist, matchBaseDist + matchMaxSpeed·gap) × scale. */
  matchMaxDist: 2.5,
  matchBaseDist: 0.6,
  matchMaxSpeed: 6,
  /** Allowed |ln(scale ratio)| for a match: matchScaleBase + matchScaleRate·gap(s), capped. */
  matchScaleBase: 0.35,
  matchScaleRate: 2.0,
  matchScaleMax: 0.92,
  matchScaleWeight: 1.5,
  trackScaleTauMs: 500,
  /** A track unseen this long is dropped; a locked slot bound to it becomes "lost". */
  trackTimeoutMs: 1000,
  /** Unlocked: tracks unseen for less than this still hold their slot (no flicker on dropouts). */
  unlockedHoldMs: 300,
  /** Unlocked: incumbents get this scale bonus when ranking (prevents flapping between similar sizes). */
  unlockedIncumbentBonus: 1.15,
  /** A lost slot re-acquires only a person within this × scale of its last known center. */
  reacquireMaxDist: 3.0,
  reacquireMaxScaleLog: 0.7,

  // ---- Positioning hints ---------------------------------------------------------------------
  hintTooCloseScale: 0.3,
  hintTooFarScale: 0.06,
  hintEdge: 0.03,
  hintHeadCutY: 0.1,
  hintSideLaneFactor: 1.6,
  hintLowConfidence: 0.55,
  /** Delivered camera fps below this for lowFpsHoldMs ⇒ 'low-visibility' hint (dim light ⇒ long exposure). */
  lowFpsThreshold: 24,
  lowFpsHoldMs: 2000,

  // ---- Selfie ------------------------------------------------------------------------------
  selfieSize: 192,
  selfieEarFactor: 3.2,
  selfieShoulderFactor: 1.4,
  selfieMaxShoulderFactor: 3.5,
  selfieShiftUp: 0.15,
  selfieQuality: 0.82,

  // ---- Stats -------------------------------------------------------------------------------
  statsAlpha: 0.1,
} as const;

/** smoothing 0..1 → One Euro minCutoff (Hz). */
export function minCutoffForSmoothing(smoothing: number): number {
  const s = smoothing < 0 ? 0 : smoothing > 1 ? 1 : smoothing;
  return VISION_CONFIG.minCutoffAtSmoothing0 + (VISION_CONFIG.minCutoffAtSmoothing1 - VISION_CONFIG.minCutoffAtSmoothing0) * s;
}
