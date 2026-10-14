// Pure (DOM-free) vision pipeline: raw MediaPipe poses (camera space) → slot assignment →
// One Euro smoothing (two paths) → gesture detectors → VisionFrame + ActionEvents.
// VisionSystem wraps this with camera + MediaPipe; tests drive it with synthetic bodies.
import type {
  ActionEvent, GestureState, Landmark, PlayerCount, PlayerId, TrackedPerson, VisionFrame, VisionTuning,
} from '../shared/types';
import { DEFAULT_VISION_TUNING } from '../shared/constants';
import { LANDMARK_COUNT } from '../shared/pose';
import { Emitter } from '../shared/emitter';
import { VISION_CONFIG as C, minCutoffForSmoothing } from './config';
import { OneEuroFilter } from './OneEuroFilter';
import { GestureDetector } from './GestureDetector';
import { PlayerAssigner } from './PlayerAssigner';
import { type Candidate, type RawLandmark, isValidPose, poseHasVisibility, rawCandidate, visibilityOf } from './features';
import { computeHints } from './hints';

class SlotFilters {
  readonly gesture: OneEuroFilter[] = [];
  readonly overlay: OneEuroFilter[] = [];
  readonly gestureLm: Landmark[] = [];
  /** Preallocated snapshot of gestureLm exposed via VisionCore.latest (no per-frame allocation). */
  readonly latestLm: Landmark[] = [];
  constructor(smoothing: number) {
    const mc = minCutoffForSmoothing(smoothing);
    for (let i = 0; i < LANDMARK_COUNT * 2; i++) {
      this.gesture.push(new OneEuroFilter(C.gestureMinCutoff, C.gestureBeta, C.gestureDCutoff));
      this.overlay.push(new OneEuroFilter(mc, C.beta, C.dCutoff));
    }
    for (let i = 0; i < LANDMARK_COUNT; i++) {
      this.gestureLm.push({ x: 0, y: 0, z: 0, visibility: 0 });
      this.latestLm.push({ x: 0, y: 0, z: 0, visibility: 0 });
    }
  }
  reset(): void {
    for (const f of this.gesture) f.reset();
    for (const f of this.overlay) f.reset();
  }
  setSmoothing(smoothing: number): void {
    const mc = minCutoffForSmoothing(smoothing);
    for (const f of this.overlay) f.minCutoff = mc;
  }
}

export class VisionCore {
  private tuning: VisionTuning = { ...DEFAULT_VISION_TUNING };
  readonly assigner = new PlayerAssigner();
  private detectors: [GestureDetector, GestureDetector] = [new GestureDetector(0), new GestureDetector(1)];
  private filters: [SlotFilters, SlotFilters];
  private cands: Candidate[] = [];
  private poseIdx: number[] = [];
  readonly actions = new Emitter<ActionEvent>();
  /** Latest smoothed display-space landmarks per slot (gesture path — low lag), for selfies. */
  readonly latest: [Landmark[] | null, Landmark[] | null] = [null, null];
  /** Set by the camera layer when delivered fps is too low (adds 'low-visibility'). */
  lowFps = false;
  private emitSlot: PlayerId = 0;
  private readonly emitFn = (action: ActionEvent['action'], t: number): void => {
    if (this.emitSlot >= this.assigner.playerCount) return;
    this.actions.emit({ playerId: this.emitSlot, action, source: 'pose', t });
  };

  constructor() {
    this.filters = [new SlotFilters(this.tuning.smoothing), new SlotFilters(this.tuning.smoothing)];
  }

  get playerCount(): PlayerCount {
    return this.assigner.playerCount;
  }

  getTuning(): VisionTuning {
    return { ...this.tuning };
  }

  setTuning(t: Partial<VisionTuning>): void {
    const next = { ...this.tuning };
    for (const k of Object.keys(t) as (keyof VisionTuning)[]) {
      const v = t[k];
      if (v === undefined) continue;
      if (k === 'mirror') next.mirror = Boolean(v);
      else if (typeof v === 'number' && Number.isFinite(v)) next[k] = v;
    }
    const mirrorChanged = next.mirror !== this.tuning.mirror;
    this.tuning = next;
    for (const f of this.filters) f.setSmoothing(next.smoothing);
    if (mirrorChanged) {
      // Display space flipped: positions and calibrated centers are no longer valid.
      for (const f of this.filters) f.reset();
      for (const d of this.detectors) {
        d.resetTracking();
        if (d.calibrated) d.laneCenterX = 1 - d.laneCenterX;
      }
      this.assigner.reset();
    }
  }

  setPlayerCount(n: PlayerCount): void {
    this.assigner.setPlayerCount(n);
    for (let s = n; s < 2; s++) this.freshSlot(s as PlayerId);
  }

  private freshSlot(s: PlayerId): void {
    this.detectors[s] = new GestureDetector(s);
    this.filters[s] = new SlotFilters(this.tuning.smoothing);
    this.latest[s] = null;
  }

  lockSlots(locked: boolean, order?: PlayerId[]): void {
    const remap = this.assigner.lock(locked, order);
    if (!locked || !order || order.length === 0) return;
    const oldD = this.detectors;
    const oldF = this.filters;
    const oldL = this.latest.slice() as [Landmark[] | null, Landmark[] | null];
    const nd: GestureDetector[] = [];
    const nf: SlotFilters[] = [];
    for (let s = 0; s < 2; s++) {
      const from = remap[s];
      if (from === null) {
        nd.push(new GestureDetector(s as PlayerId));
        nf.push(new SlotFilters(this.tuning.smoothing));
        this.latest[s] = null;
      } else {
        const d = oldD[from];
        d.slot = s as PlayerId;
        nd.push(d);
        nf.push(oldF[from]);
        this.latest[s] = oldL[from];
      }
    }
    this.detectors = [nd[0], nd[1]];
    this.filters = [nf[0], nf[1]];
  }

  startCalibration(slot: PlayerId, t: number): void {
    this.detectors[slot].startCalibration(t);
  }

  resetCalibration(slot?: PlayerId): void {
    if (slot === undefined) for (const d of this.detectors) d.resetCalibration();
    else this.detectors[slot].resetCalibration();
  }

  /** Detector access for debugging / tests. */
  detector(slot: PlayerId): GestureDetector {
    return this.detectors[slot];
  }

  /**
   * Process one inference result. `poses`: raw MediaPipe landmarks (camera space, unmirrored).
   * `yScale` = videoHeight / videoWidth.
   */
  process(
    poses: ReadonlyArray<ReadonlyArray<RawLandmark>>,
    t: number,
    yScale: number,
    inferenceMs = 0,
    fps = 0,
  ): VisionFrame {
    const mirror = this.tuning.mirror;
    const cands = this.cands;
    const poseIdx = this.poseIdx;
    let nc = 0;
    poseIdx.length = 0;
    for (let p = 0; p < poses.length; p++) {
      const lm = poses[p];
      if (!isValidPose(lm)) continue;
      if (nc >= cands.length) cands.push({ x: 0, y: 0, scale: 0, confidence: 0 });
      rawCandidate(lm, mirror, yScale, cands[nc]);
      poseIdx.push(p);
      nc++;
    }
    const view = nc === cands.length ? cands : cands.slice(0, nc);
    const res = this.assigner.assign(view, t);

    const people: TrackedPerson[] = [];
    const n = this.assigner.playerCount;
    for (let s = 0; s < n; s++) {
      const ci = res.slots[s];
      if (ci < 0) continue;
      const slot = s as PlayerId;
      const raw = poses[poseIdx[ci]];
      const f = this.filters[s];
      const det = this.detectors[s];
      if (res.identityChanged[s]) {
        f.reset();
        det.resetTracking();
      }
      const hasVis = poseHasVisibility(raw);
      const prevScale = det.scale > 0.01 ? det.scale : cands[ci].scale;
      const invSx = 1 / Math.max(0.01, prevScale);
      const invSy = yScale * invSx;
      const overlay: Landmark[] = new Array<Landmark>(LANDMARK_COUNT);
      const g = f.gestureLm;
      for (let i = 0; i < LANDMARK_COUNT; i++) {
        const r = raw[i];
        const x = mirror ? 1 - r.x : r.x;
        const y = r.y;
        const vis = visibilityOf(r, hasVis);
        const gl = g[i];
        gl.x = f.gesture[2 * i].filter(x, t);
        gl.y = f.gesture[2 * i + 1].filter(y, t);
        gl.z = r.z ?? 0;
        gl.visibility = vis;
        overlay[i] = {
          x: f.overlay[2 * i].filter(x, t, invSx),
          y: f.overlay[2 * i + 1].filter(y, t, invSy),
          z: r.z ?? 0,
          visibility: vis,
        };
      }
      this.emitSlot = slot;
      det.update(g, t, yScale, this.tuning, this.emitFn);
      const snap = f.latestLm;
      for (let i = 0; i < LANDMARK_COUNT; i++) {
        const src = g[i];
        const dst = snap[i];
        dst.x = src.x;
        dst.y = src.y;
        dst.z = src.z;
        dst.visibility = src.visibility;
      }
      this.latest[s] = snap;
      people.push({
        slot,
        landmarks: overlay,
        center: { x: det.centerX, y: det.centerY },
        scale: det.scale,
        handsUp: det.handsUp,
        handsUpProgress: det.handsUpProgress,
        hints: computeHints(g, det.centerX, det.scale, det.confidence, this.tuning.laneThreshold, this.lowFps),
        confidence: det.confidence,
      });
    }
    const gestures: [GestureState | null, GestureState | null] = [null, null];
    for (let s = 0; s < n; s++) {
      const d = this.detectors[s];
      if (d.scale > 0 || d.phase !== 'uncalibrated') gestures[s] = d.getState(this.tuning);
    }
    return { t, people, extraPeople: res.extra, gestures, inferenceMs, fps };
  }

  dispose(): void {
    this.actions.clear();
  }
}
