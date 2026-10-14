// Pure, DOM-free per-slot gesture detector (MASTERPLAN §5.2–5.6).
// Input: smoothed DISPLAY-space landmarks + timestamp. Output: GestureState + emitted actions.
//
// Units: x in normalized image-width units; vertical quantities are aspect-corrected
// (normalized y × yScale, yScale = videoHeight/videoWidth) so they share physical units with x.
// All thresholds are multiples of the body scale.
//
// Addition to the spec: the neutral baseline follows a pinhole perspective model —
// when the person walks toward/away from the camera the shoulders move away from/toward the
// image's vertical center in proportion to the scale. The expected baseline at the current
// scale is `yc + (baselineY - yc) · scale / baseScale`, so walking closer is not a jump/duck.
import type { GesturePhase, GestureState, Landmark, Lane, PlayerAction, PlayerId, VisionTuning } from '../shared/types';
import { LM } from '../shared/pose';
import { HANDS_UP_HOLD_MS } from '../shared/constants';
import { VISION_CONFIG as C } from './config';
import { createBodyFeatures, isHandsDown, isHandsUp, measureBody } from './features';

const HIST = 48;

export type EmitFn = (action: PlayerAction, t: number) => void;

function median(a: number[], n: number): number {
  if (n === 0) return 0;
  const s = a.slice(0, n).sort((p, q) => p - q);
  const m = n >> 1;
  return n % 2 ? s[m] : 0.5 * (s[m - 1] + s[m]);
}

export class GestureDetector {
  slot: PlayerId;

  // ---- public person features (display space) ----
  centerX = 0.5;
  /** Display-space (normalized) anchor y. */
  centerY = 0.5;
  scale = 0;
  /** Faster-tracking scale used only by the perspective baseline model. */
  private perspScale = 0;
  confidence = 0;
  handsUp = false;
  handsUpProgress = 0;

  // ---- gesture state ----
  phase: GesturePhase = 'uncalibrated';
  calibrationProgress = 0;
  lane: Lane = 0;
  laneCenterX = 0.5;
  /** Aspect-corrected baseline at `baseScale`. */
  private baselineY = 0;
  private baseScale = 0;
  private yScale = 0.5625;
  private lastGesture: GestureState['lastGesture'] = null;

  // internals
  private body = createBodyFeatures();
  private useHips = false;
  private hipWeight = 0;
  private anchorY = 0; // aspect-corrected
  private lastT = -1;
  private hasData = false;
  private histT = new Float64Array(HIST);
  private histX = new Float64Array(HIST);
  private histY = new Float64Array(HIST);
  private histV = new Float64Array(HIST);
  private histN = 0;
  private histHead = 0;
  vUp = 0;
  /** Short-window (~instVelWindowMs) vertical speed: sees deceleration that the 70 ms vUp still hides. */
  private vUpInst = 0;
  /** Lateral (display x) speed, scale/s, + = right. */
  vX = 0;
  /** Net display-x travel over laneTravelWindowMs, in scales. */
  private travelX = 0;
  /** Rise of the anchor above its lowest point in the last jumpEarlyDipWindowMs, in scales. */
  private riseFromLow = 0;
  private vPeak = 0;
  private vDownPeak = 0;
  private calmSpeed = 0;
  dy = 0;
  dx = 0;

  private calibStartT = -1;
  private calibAccumMs = 0;
  private calibX: number[] = [];
  private calibY: number[] = [];
  private calibS: number[] = [];
  private calibN = 0;
  /** Accepted calibration frames seen so far (for stride decimation). */
  private calibSeen = 0;
  /** Keep every `calibStride`-th accepted frame; doubles whenever the buffer fills. */
  private calibStride = 1;

  private pendingLane: Lane = 0;
  private pendingSince = -1;
  private lastJumpDuckT = -1e9;
  private airborneSince = 0;
  private landBelowSince = -1;
  private landedT = -1e9;
  private duckEndT = -1e9;
  private duckBelowSince = -1;
  private calmSince = -1;
  private settleUntil = -1;

  constructor(slot: PlayerId) {
    this.slot = slot;
  }

  get calibrated(): boolean {
    return this.phase === 'ready' || this.phase === 'airborne' || this.phase === 'ducking';
  }

  startCalibration(t: number): void {
    this.phase = 'calibrating';
    this.calibrationProgress = 0;
    this.calibStartT = t;
    this.calibAccumMs = 0;
    this.calibN = 0;
    this.calibSeen = 0;
    this.calibStride = 1;
  }

  resetCalibration(): void {
    this.phase = 'uncalibrated';
    this.calibrationProgress = 0;
    this.calibStartT = -1;
    this.calibN = 0;
    this.calibSeen = 0;
    this.calibStride = 1;
    this.laneCenterX = 0.5;
    this.lastGesture = null;
  }

  /**
   * Deterministic, time-uniform calibration sampling: keep every `calibStride`-th accepted
   * frame. When the buffer is full, drop every other kept sample and double the stride, so the
   * kept set always spans the whole calibration evenly (no bias toward recent frames).
   */
  private pushCalibSample(): void {
    const seen = this.calibSeen++;
    if (seen % this.calibStride !== 0) return;
    if (this.calibN >= C.calibMaxSamples) {
      let w = 0;
      for (let r = 0; r < this.calibN; r += 2, w++) {
        this.calibX[w] = this.calibX[r];
        this.calibY[w] = this.calibY[r];
        this.calibS[w] = this.calibS[r];
      }
      this.calibN = w;
      this.calibStride *= 2;
      if (seen % this.calibStride !== 0) return;
    }
    const i = this.calibN++;
    this.calibX[i] = this.centerX;
    this.calibY[i] = this.anchorY;
    this.calibS[i] = this.perspScale;
  }

  /** New identity in this slot (or long gap): drop kinematic history, keep calibration. */
  resetTracking(): void {
    this.hasData = false;
    this.histN = 0;
    this.handsUpProgress = 0;
    this.pendingSince = -1;
  }

  private pushHist(t: number, x: number, y: number): void {
    this.histHead = (this.histHead + 1) % HIST;
    this.histT[this.histHead] = t;
    this.histX[this.histHead] = x;
    this.histY[this.histHead] = y;
    if (this.histN < HIST) this.histN++;
  }

  /** Index of the newest sample at least `ms` older than the newest (or the oldest available). */
  private histBack(ms: number): number {
    const now = this.histT[this.histHead];
    let idx = this.histHead;
    for (let k = 1; k < this.histN; k++) {
      const i = (this.histHead - k + HIST) % HIST;
      idx = i;
      if (now - this.histT[i] >= ms) break;
    }
    return idx;
  }

  private kinematics(): void {
    const s = this.scale > 1e-4 ? this.scale : 1e-4;
    const h = this.histHead;
    if (this.histN < 2) {
      this.vUp = 0;
      this.vX = 0;
      this.vUpInst = 0;
      this.travelX = 0;
      this.riseFromLow = 0;
      this.histV[h] = 0;
      this.vPeak = 0;
      this.vDownPeak = 0;
      this.calmSpeed = 0;
      return;
    }
    const i = this.histBack(C.velWindowMs);
    const dt = (this.histT[h] - this.histT[i]) / 1000;
    this.vUp = dt > 0 ? (this.histY[i] - this.histY[h]) / dt / s : 0;
    this.vX = dt > 0 ? (this.histX[h] - this.histX[i]) / dt / s : 0;
    this.histV[h] = this.vUp;
    const q = this.histBack(C.instVelWindowMs);
    const dtq = (this.histT[h] - this.histT[q]) / 1000;
    this.vUpInst = dtq > 0 ? (this.histY[q] - this.histY[h]) / dtq / s : 0;
    this.travelX = (this.histX[h] - this.histX[this.histBack(C.laneTravelWindowMs)]) / s;
    // peaks over recent windows
    let peak = -Infinity;
    let downPeak = -Infinity;
    let lowY = this.histY[h];
    const now = this.histT[h];
    for (let k = 0; k < this.histN; k++) {
      const j = (h - k + HIST) % HIST;
      const age = now - this.histT[j];
      if (age > C.duckVelWindowMs) break;
      const v = this.histV[j];
      if (age <= C.vPeakWindowMs && v > peak) peak = v;
      if (-v > downPeak) downPeak = -v;
      if (age <= C.jumpEarlyDipWindowMs && this.histY[j] > lowY) lowY = this.histY[j];
    }
    this.riseFromLow = (lowY - this.histY[h]) / s;
    this.vPeak = peak === -Infinity ? 0 : peak;
    this.vDownPeak = downPeak === -Infinity ? 0 : downPeak;
    const c = this.histBack(C.calmWindowMs);
    const dtc = (this.histT[h] - this.histT[c]) / 1000;
    this.calmSpeed =
      dtc > 0 ? Math.hypot(this.histX[h] - this.histX[c], this.histY[h] - this.histY[c]) / dtc / s : 0;
  }

  /** Expected neutral anchor y (aspect-corrected) at the current scale. */
  private expectedBaseline(): number {
    if (this.baseScale <= 0) return this.baselineY;
    const yc = 0.5 * this.yScale;
    return yc + (this.baselineY - yc) * (this.perspScale / this.baseScale);
  }

  private laneFor(dx: number, from: Lane, T: number, H: number): Lane {
    if (from === 0) return dx > T ? 1 : dx < -T ? -1 : 0;
    if (from === 1) return dx < -T ? -1 : dx < T - H ? 0 : 1;
    return dx > T ? 1 : dx > -(T - H) ? 0 : -1;
  }

  /**
   * Early lane trigger: a sustained fast step toward the next lane fires within `margin` of the
   * zone boundary. margin < H, so the new lane's own hysteresis keeps it (no snap-back).
   */
  private earlyLane(T: number, H: number): Lane {
    const dir = this.vX >= C.laneEarlyMinV ? 1 : this.vX <= -C.laneEarlyMinV ? -1 : 0;
    if (dir === 0 || dir * this.travelX < C.laneEarlyMinTravel) return this.lane;
    const next = this.lane + dir;
    if (next < -1 || next > 1) return this.lane;
    const margin = Math.min(C.laneEarlyMargin, C.laneEarlyHysteresisShare * H);
    // Boundary between `lane` and `next`, in dx units: outward ±T, inward ±(T − H).
    const boundary = this.lane === 0 ? dir * T : this.lane * (T - H);
    return dir * (this.dx - boundary) >= -margin ? (next as Lane) : this.lane;
  }

  private setLane(lane: Lane, t: number, emit: EmitFn): void {
    this.lane = lane;
    this.pendingSince = -1;
    if (this.calibrated) this.emit(emit, { type: 'lane', lane }, t);
  }

  private emit(emit: EmitFn, action: PlayerAction, t: number): void {
    this.lastGesture = { kind: action.type === 'lane' ? 'lane' : action.type === 'jump' ? 'jump' : 'duck', t };
    emit(action, t);
  }

  /**
   * Process one frame. `lm`: 33 smoothed display-space landmarks. `yScale`: videoHeight/videoWidth.
   */
  update(lm: ReadonlyArray<Landmark>, t: number, yScale: number, tuning: VisionTuning, emit: EmitFn): void {
    this.yScale = yScale;
    const gap = this.lastT < 0 ? Infinity : t - this.lastT;
    if (gap <= 0) return;
    const dtMs = Math.min(gap, C.maxFrameDtMs);
    if (gap > C.gapResetMs) this.histN = 0;
    if (this.hasData && gap > C.settleAfterGapMs) {
      this.settleUntil = t + C.settleMs;
      this.handsUpProgress = 0;
    }
    this.lastT = t;

    // ---- body features ----
    const b = this.body;
    const hipVis = Math.min(lm[LM.LEFT_HIP].visibility, lm[LM.RIGHT_HIP].visibility);
    this.useHips = this.useHips ? hipVis > C.hipVisOff : hipVis > C.hipVisOn;
    measureBody(lm, yScale, this.useHips, b);
    if (!this.hasData) {
      this.scale = b.rawScale;
      this.perspScale = b.rawScale;
      this.hipWeight = this.useHips ? C.hipAnchorWeight : 0;
    } else {
      this.scale += (b.rawScale - this.scale) * (1 - Math.exp(-dtMs / C.scaleTauMs));
      this.perspScale += (b.rawScale - this.perspScale) * (1 - Math.exp(-dtMs / C.perspScaleTauMs));
      const target = this.useHips ? C.hipAnchorWeight : 0;
      this.hipWeight += (target - this.hipWeight) * (1 - Math.exp((-C.hipBlendRate * dtMs) / 1000));
    }
    if (!(this.scale > 1e-4)) this.scale = 1e-4;
    this.centerX = (1 - this.hipWeight) * b.shoulderMidX + this.hipWeight * b.hipMidX;
    this.centerY = b.shoulderMidY;
    this.anchorY = b.shoulderMidY * yScale;
    this.confidence = b.confidence;
    this.hasData = true;
    this.pushHist(t, this.centerX, this.anchorY);
    this.kinematics();

    // ---- hands up ----
    this.handsUp = isHandsUp(lm, this.scale, yScale);
    const step = dtMs / HANDS_UP_HOLD_MS;
    this.handsUpProgress = this.handsUp
      ? Math.min(1, this.handsUpProgress + step)
      : Math.max(0, this.handsUpProgress - C.handsDecayFactor * step);

    const T = tuning.laneThreshold;
    const H = tuning.laneHysteresis;
    const J = tuning.jumpThreshold;
    const D = tuning.duckThreshold;

    // ---- calibration ----
    if (this.phase === 'calibrating') {
      if (this.calibStartT < 0) this.calibStartT = t;
      const elapsed = t - this.calibStartT;
      const handsDown = isHandsDown(lm, this.scale, yScale);
      let ok: boolean;
      if (elapsed >= C.calibAnyAfterMs) ok = true;
      else if (elapsed >= C.calibRelaxAfterMs) ok = handsDown && this.confidence > C.calibRelaxMinConfidence;
      else ok = handsDown && this.confidence > C.calibMinConfidence && this.calmSpeed < C.calibMaxSpeed;
      if (ok) {
        this.calibAccumMs += dtMs;
        this.pushCalibSample();
      }
      this.calibrationProgress = Math.min(1, this.calibAccumMs / C.calibDurationMs);
      if (this.calibAccumMs >= C.calibDurationMs) {
        const cx = median(this.calibX, this.calibN);
        this.baselineY = median(this.calibY, this.calibN);
        this.baseScale = median(this.calibS, this.calibN);
        const half = T * this.scale;
        const lo = C.edgeMargin + half;
        const hi = 1 - C.edgeMargin - half;
        this.laneCenterX = lo <= hi ? Math.min(hi, Math.max(lo, cx)) : 0.5;
        this.phase = 'ready';
        this.calibrationProgress = 1;
        this.settleUntil = -1;
        this.calmSince = -1;
        this.duckBelowSince = -1;
        this.landedT = -1e9;
        this.duckEndT = -1e9;
        this.lastJumpDuckT = -1e9;
        const dx0 = (this.centerX - this.laneCenterX) / this.scale;
        this.lane = dx0 > T ? 1 : dx0 < -T ? -1 : 0;
        this.pendingSince = -1;
        this.dx = dx0;
        this.dy = 0;
        this.emit(emit, { type: 'lane', lane: this.lane }, t);
        return;
      }
    }

    // ---- lanes ----
    this.dx = (this.centerX - this.laneCenterX) / this.scale;
    const target = this.laneFor(this.dx, this.lane, T, H);
    if (target !== this.lane) {
      if (this.pendingSince < 0 || this.pendingLane !== target) {
        this.pendingLane = target;
        this.pendingSince = t;
      }
      // Moving fast toward the new zone ⇒ no debounce (jitter at a boundary has no sustained speed).
      const fast = (target - this.lane) * this.vX >= C.laneFastV;
      if (fast || t - this.pendingSince >= C.laneDebounceMs) this.setLane(target, t, emit);
    } else {
      this.pendingSince = -1;
      const early = this.earlyLane(T, H);
      if (early !== this.lane) this.setLane(early, t, emit);
    }

    if (!this.calibrated) return;

    // ---- jump / duck ----
    const expected = this.expectedBaseline();
    this.dy = (expected - this.anchorY) / this.scale;
    const dy = this.dy;
    const settling = t < this.settleUntil;

    if (settling) {
      // Re-acquired after a gap: re-settle the baseline, no jump/duck.
      const k = 1 - Math.exp((-C.settleRate * dtMs) / 1000);
      this.rebaseToward(k);
      if (this.phase !== 'ready') this.phase = 'ready';
      return;
    }

    if (this.phase === 'ready') {
      const sinceGesture = t - this.lastJumpDuckT;
      // Velocity-predictive take-off: a fast rise fires before the full jump line.
      const earlyJump =
        this.vUpInst >= C.jumpEarlyVUp &&
        t - this.landedT >= C.jumpEarlyGuardMs &&
        t - this.duckEndT >= C.jumpEarlyGuardMs &&
        (dy > C.jumpEarlyFraction * J || (dy > 0 && this.riseFromLow > J + dy));
      if ((dy > J || earlyJump) && this.vPeak > C.jumpMinVUp && sinceGesture >= C.jumpRefractoryMs) {
        this.phase = 'airborne';
        this.airborneSince = t;
        this.landBelowSince = -1;
        this.lastJumpDuckT = t;
        this.duckBelowSince = -1;
        this.emit(emit, { type: 'jump' }, t);
      } else if (dy < -D) {
        if (this.duckBelowSince < 0) this.duckBelowSince = t;
        const hold = -this.vUpInst >= C.duckFastVDown ? C.duckFastHoldMs : C.duckHoldMs;
        if (
          t - this.duckBelowSince >= hold &&
          t - this.landedT >= C.landingDuckGuardMs &&
          sinceGesture >= C.jumpRefractoryMs &&
          this.vDownPeak > C.duckMinVDown
        ) {
          this.phase = 'ducking';
          this.lastJumpDuckT = t;
          this.duckBelowSince = -1;
          this.emit(emit, { type: 'duck' }, t);
        }
      } else {
        this.duckBelowSince = -1;
      }
      // baseline drift
      if (this.phase === 'ready' && Math.abs(dy) < C.driftMaxDy && Math.abs(this.vUp) < C.driftMaxV) {
        if (this.calmSince < 0) this.calmSince = t;
        if (t - this.calmSince >= C.driftCalmMs) this.rebaseToward(1 - Math.exp((-C.driftRate * dtMs) / 1000));
      } else {
        this.calmSince = -1;
      }
    } else if (this.phase === 'airborne') {
      if (dy < C.landFraction * J) {
        if (this.landBelowSince < 0) this.landBelowSince = t;
      } else {
        this.landBelowSince = -1;
      }
      if (
        (this.landBelowSince >= 0 && t - this.landBelowSince >= C.landHoldMs) ||
        t - this.airborneSince >= C.airborneTimeoutMs
      ) {
        this.phase = 'ready';
        this.landedT = t;
        this.calmSince = -1;
      }
    } else if (this.phase === 'ducking') {
      if (dy > -C.duckReleaseFraction * D) {
        this.phase = 'ready';
        this.duckEndT = t;
        this.calmSince = -1;
        this.duckBelowSince = -1;
      }
    }
  }

  /** Move the baseline (expressed at baseScale) toward the current anchor by fraction k. */
  private rebaseToward(k: number): void {
    if (this.baseScale <= 0) {
      this.baselineY += (this.anchorY - this.baselineY) * k;
      return;
    }
    const yc = 0.5 * this.yScale;
    const target = yc + (this.anchorY - yc) * (this.baseScale / this.perspScale);
    this.baselineY += (target - this.baselineY) * k;
  }

  getState(tuning: VisionTuning): GestureState {
    const ys = this.yScale > 0 ? this.yScale : 1;
    const calibrated = this.calibrated;
    const base = calibrated ? this.expectedBaseline() : this.anchorY;
    return {
      slot: this.slot,
      phase: this.phase,
      calibrationProgress: this.calibrationProgress,
      lane: this.lane,
      centerX: this.laneCenterX,
      laneHalfWidth: tuning.laneThreshold * this.scale,
      baselineY: base / ys,
      jumpLineY: (base - tuning.jumpThreshold * this.scale) / ys,
      duckLineY: (base + tuning.duckThreshold * this.scale) / ys,
      lastGesture: this.lastGesture ? { ...this.lastGesture } : null,
    };
  }
}
