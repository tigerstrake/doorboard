// VisionSystem — camera + MediaPipe PoseLandmarker facade around the pure VisionCore.
// See MASTERPLAN §5. update() never throws; errors surface via `status`/`error`.
import { FilesetResolver, PoseLandmarker, type PoseLandmarkerResult } from '@mediapipe/tasks-vision';
import type {
  ActionEvent, IVisionSystem, PlayerCount, PlayerId, PoseModel, VisionFrame, VisionStatus, VisionTuning,
} from '../shared/types';
import { ASSET_PATHS } from '../shared/constants';
import { assetUrl } from '../shared/params';
import { VISION_CONFIG as C } from './config';
import { VisionCore } from './VisionCore';
import { captureSelfieFromVideo } from './selfie';

type WasmFileset = Awaited<ReturnType<typeof FilesetResolver.forVisionTasks>>;

export const CAMERA_DISCONNECTED_MESSAGE =
  'Camera disconnected. Plug it back in and press Retry, or play with the keyboard.';
export const MODEL_DOWNLOAD_FAILED_MESSAGE =
  "Couldn't download the body-tracking model. Check your connection and reload.";
/** Consecutive detectForVideo failures before switching to CPU / reporting an error. */
const MAX_CONSECUTIVE_FAILURES = 30;
/** A crash selfie needs a tracked frame at most this old. */
const SELFIE_MAX_AGE_MS = 300;

function errName(e: unknown): string {
  return e && typeof e === 'object' && 'name' in e ? String((e as { name: unknown }).name) : '';
}
function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function cameraErrorMessage(e: unknown): string {
  switch (errName(e)) {
    case 'NotAllowedError':
    case 'SecurityError':
      return 'Camera access was blocked. Click the camera icon in the address bar, allow access, then reload.';
    case 'NotFoundError':
    case 'OverconstrainedError':
      return 'No camera found. Plug in a webcam (or pick another camera in Settings) and try again.';
    case 'NotReadableError':
    case 'AbortError':
      return 'The camera is busy — close other apps or tabs using it (Zoom, FaceTime, Meet…) and try again.';
    default:
      return `Could not start the camera: ${errMsg(e)}`;
  }
}

/**
 * A model or camera switch failed but the previous one is still running. The status stays
 * healthy; callers should revert the setting rather than show a fatal error.
 */
export class KeptPreviousError extends Error {
  readonly keptPrevious = true;
}

export class VisionSystem implements IVisionSystem {
  private _status: VisionStatus = 'idle';
  private _error: string | null = null;
  private readonly core = new VisionCore();
  private fileset: WasmFileset | null = null;
  private landmarker: PoseLandmarker | null = null;
  private model: PoseModel | null = null;
  /** numPoses the current landmarker was built with (setPlayerCount may change it mid-load). */
  private readonly posesOf = new WeakMap<PoseLandmarker, number>();
  private loadToken = 0;
  private loading = false;
  private video: HTMLVideoElement | null = null;
  private stream: MediaStream | null = null;
  private cameraToken = 0;
  private latestStart: { token: number; promise: Promise<void> } | null = null;
  private cameraReady = false;
  private disposed = false;

  // frame scheduling
  private rvfcId = 0;
  private rvfcSupported = false;
  private framePending = false;
  private lastMediaTime = -1;
  private lastMediaTimeRaw = -1;
  private lastRvfcAt = 0;
  private lastPolledTime = -1;
  private lastTs = 0;
  private lastInferenceAt = 0;
  private consecutiveFailures = 0;
  /** A runtime GPU→CPU landmarker swap was already attempted for the current model. */
  private cpuFallbackTried = false;
  /** A CPU landmarker is being created after repeated runtime failures; skip inference. */
  private recovering = false;
  /** Detaches the 'ended' listeners of the active stream's tracks. */
  private detachTrackListeners: (() => void) | null = null;
  private deviceChangeHooked = false;
  private readonly onDeviceChange = (): void => void this.checkActiveDevice();

  // stats
  private inferenceMs = 0;
  private fps = 0;
  private deliveredWindowStart = 0;
  private deliveredFramesAtStart = 0;
  private deliveredFps = 0;
  private lowFpsSince = -1;
  private lastFrame: VisionFrame | null = null;

  get status(): VisionStatus {
    return this._status;
  }
  get error(): string | null {
    return this._error;
  }
  /** Camera fps actually delivered (rVFC presentedFrames over ~1 s). */
  get cameraFps(): number {
    return this.deliveredFps;
  }
  /** Delegate actually in use. */
  delegate: 'GPU' | 'CPU' | null = null;

  private fail(msg: string): Error {
    this._status = 'error';
    this._error = msg;
    return new Error(msg);
  }

  private numPoses(): number {
    const n = this.core.playerCount + (this.core.assigner.locked && !C.extraPoseWhenLocked ? 0 : 1);
    return Math.min(C.maxPoses, n);
  }

  async loadModel(model: PoseModel): Promise<void> {
    const token = ++this.loadToken;
    this._status = 'loading-model';
    this._error = null;
    this.loading = true;
    try {
      if (!this.fileset) this.fileset = await FilesetResolver.forVisionTasks(assetUrl(ASSET_PATHS.mediapipeWasm));
      const make = (delegate: 'GPU' | 'CPU') => this.createLandmarker(model, delegate);
      let lm: PoseLandmarker;
      let delegate: 'GPU' | 'CPU' = 'GPU';
      try {
        lm = await make('GPU');
      } catch (gpuErr) {
        console.warn('[vision] GPU delegate failed, falling back to CPU:', errMsg(gpuErr));
        delegate = 'CPU';
        lm = await make('CPU');
      }
      if (token === this.loadToken) this.loading = false;
      if (token !== this.loadToken || this.disposed) {
        lm.close();
        return;
      }
      const old = this.landmarker;
      this.landmarker = lm;
      this.model = model;
      this.delegate = delegate;
      // setPlayerCount / lockSlots during the load went to the old (or no) landmarker.
      if (this.posesOf.get(lm) !== this.numPoses()) this.applyNumPoses();
      if (old) {
        try {
          old.close();
        } catch {
          /* ignore */
        }
      }
      this.consecutiveFailures = 0;
      this.cpuFallbackTried = false;
      this.recovering = false;
      this._status = this.cameraReady ? 'running' : 'model-ready';
    } catch (e) {
      if (token !== this.loadToken) return;
      this.loading = false;
      this.recovering = false;
      // Developer detail goes to the console; the player sees a plain-language message.
      console.error(
        `[vision] could not load pose model '${model}' (${ASSET_PATHS.poseModel(model)}, WASM ${ASSET_PATHS.mediapipeWasm}):`,
        errMsg(e),
      );
      if (this.landmarker && !this.disposed) {
        // Switching models failed but the previous one still works: keep tracking with it.
        this._status = this.cameraReady ? 'running' : 'model-ready';
        this._error = null;
        throw new KeptPreviousError(MODEL_DOWNLOAD_FAILED_MESSAGE);
      }
      throw this.fail(MODEL_DOWNLOAD_FAILED_MESSAGE);
    }
  }

  private async createLandmarker(model: PoseModel, delegate: 'GPU' | 'CPU'): Promise<PoseLandmarker> {
    const numPoses = this.numPoses();
    const lm = await PoseLandmarker.createFromOptions(this.fileset as WasmFileset, {
      baseOptions: { modelAssetPath: assetUrl(ASSET_PATHS.poseModel(model)), delegate },
      runningMode: 'VIDEO',
      numPoses,
      minPoseDetectionConfidence: C.minPoseDetectionConfidence,
      minPosePresenceConfidence: C.minPosePresenceConfidence,
      minTrackingConfidence: C.minTrackingConfidence,
      outputSegmentationMasks: false,
    });
    this.posesOf.set(lm, numPoses);
    return lm;
  }

  /** Runtime failures on the GPU delegate: rebuild the landmarker on CPU (once per model load). */
  private async fallbackToCpu(lastErr: unknown): Promise<void> {
    const model = this.model;
    const token = this.loadToken;
    this.cpuFallbackTried = true;
    if (!model || !this.fileset) {
      this.trackingFailed(lastErr);
      return;
    }
    console.warn('[vision] pose tracking keeps failing on the GPU, switching to CPU:', errMsg(lastErr));
    this.recovering = true;
    let lm: PoseLandmarker;
    try {
      lm = await this.createLandmarker(model, 'CPU');
    } catch (e) {
      if (token !== this.loadToken || this.disposed) return;
      this.recovering = false;
      this.trackingFailed(e);
      return;
    }
    if (token !== this.loadToken || this.disposed) {
      // A newer loadModel (or dispose) superseded this swap.
      lm.close();
      return;
    }
    const old = this.landmarker;
    this.landmarker = lm;
    this.delegate = 'CPU';
    if (this.posesOf.get(lm) !== this.numPoses()) this.applyNumPoses();
    this.consecutiveFailures = 0;
    this.recovering = false;
    if (old && old !== lm) {
      try {
        old.close();
      } catch {
        /* ignore */
      }
    }
  }

  private trackingFailed(e: unknown): void {
    this._status = 'error';
    this._error = `Pose tracking stopped working (${errMsg(e)}). Reload the page or switch the model in Settings.`;
  }

  /** The active camera went away (unplugged, taken by another app, permission revoked). */
  private cameraDisconnected(stream: MediaStream): void {
    if (this.stream !== stream || this.disposed) return;
    console.warn('[vision] camera disconnected');
    this.cameraToken++; // cancel any in-flight start that still references this stream
    if (this.video && this.rvfcId && this.rvfcSupported) this.video.cancelVideoFrameCallback(this.rvfcId);
    this.rvfcId = 0;
    this.stopStream();
    if (this.video) this.video.srcObject = null;
    this.fail(CAMERA_DISCONNECTED_MESSAGE);
  }

  private watchStream(stream: MediaStream): void {
    const tracks = stream.getVideoTracks();
    const onEnded = (): void => this.cameraDisconnected(stream);
    for (const t of tracks) t.addEventListener?.('ended', onEnded);
    this.detachTrackListeners = () => {
      for (const t of tracks) t.removeEventListener?.('ended', onEnded);
    };
    const md = navigator.mediaDevices;
    if (!this.deviceChangeHooked && typeof md?.addEventListener === 'function') {
      md.addEventListener('devicechange', this.onDeviceChange);
      this.deviceChangeHooked = true;
    }
  }

  /** On 'devicechange': if the active camera is no longer listed (or its track died), disconnect. */
  private async checkActiveDevice(): Promise<void> {
    const stream = this.stream;
    if (!stream || this.disposed) return;
    const track = stream.getVideoTracks()[0];
    if (!track || track.readyState === 'ended') {
      this.cameraDisconnected(stream);
      return;
    }
    const id = typeof track.getSettings === 'function' ? track.getSettings().deviceId : undefined;
    if (!id) return;
    let devices: MediaDeviceInfo[];
    try {
      devices = await navigator.mediaDevices.enumerateDevices();
    } catch {
      return;
    }
    if (this.stream !== stream) return;
    // Some browsers hide device ids until permission is granted; don't treat that as removal.
    const cams = devices.filter((d) => d.kind === 'videoinput');
    if (cams.length > 0 && cams.every((d) => !d.deviceId)) return;
    if (!cams.some((d) => d.deviceId === id)) this.cameraDisconnected(stream);
  }

  startCamera(deviceId?: string | null): Promise<void> {
    const token = this.cameraToken + 1;
    const promise = this.startCameraInner(deviceId);
    this.latestStart = { token, promise };
    return promise;
  }

  /** A newer startCamera replaced this one: settle with that call's outcome instead. */
  private superseded(token: number): Promise<void> | undefined {
    const latest = this.latestStart;
    return latest && latest.token > token ? latest.promise : undefined;
  }

  private async startCameraInner(deviceId?: string | null): Promise<void> {
    const token = ++this.cameraToken;
    // Switching cameras: the old stream keeps running (and stays healthy) until the new one works.
    const switching = this.cameraReady && this.stream !== null && this._status === 'running';
    if (!this.loading && !switching) this._status = 'starting-camera';
    if (!switching) this._error = null;
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      throw this.fail('Camera unavailable: this page must be served over HTTPS or from localhost.');
    }
    const constraints = (id?: string | null): MediaStreamConstraints => ({
      video: {
        ...(id ? { deviceId: { exact: id } } : {}),
        width: { ideal: C.cameraWidth },
        height: { ideal: C.cameraHeight },
        frameRate: { ideal: C.cameraFps },
        facingMode: 'user',
      },
      audio: false,
    });
    let stream: MediaStream;
    try {
      try {
        stream = await navigator.mediaDevices.getUserMedia(constraints(deviceId));
      } catch (e) {
        const n = errName(e);
        if (deviceId && (n === 'OverconstrainedError' || n === 'NotFoundError')) {
          console.warn('[vision] selected camera unavailable, using the default camera');
          stream = await navigator.mediaDevices.getUserMedia(constraints(null));
        } else throw e;
      }
    } catch (e) {
      if (token !== this.cameraToken) return this.superseded(token);
      if (switching && this.cameraReady && !this.disposed) throw new KeptPreviousError(cameraErrorMessage(e));
      throw this.fail(cameraErrorMessage(e));
    }
    if (token !== this.cameraToken || this.disposed) {
      stream.getTracks().forEach((t) => t.stop());
      return this.disposed ? undefined : this.superseded(token);
    }
    this.stopStream();
    this.stream = stream;
    this.watchStream(stream);
    const video = this.ensureVideo();
    video.srcObject = stream;
    try {
      await video.play().catch(() => undefined);
      await this.waitForFrames(video);
    } catch (e) {
      if (token !== this.cameraToken) return this.superseded(token);
      // No frames: release the camera (light off) rather than holding it until a retry.
      if (this.stream === stream) {
        this.stopStream();
        video.srcObject = null;
      }
      throw this.fail(errMsg(e));
    }
    if (token !== this.cameraToken) return this.superseded(token);
    this.cameraReady = true;
    // While slots are locked (mid-run) keep identities; the app recalibrates for the new framing.
    if (!this.core.assigner.locked) this.core.assigner.reset();
    this.startFrameWatch(video);
    const label = stream.getVideoTracks()[0]?.label ?? '';
    if (/facetime|macbook|built-in/i.test(label)) {
      console.info('[vision] Tip: turn OFF Center Stage (Control Center → Video Effects) — its panning shifts the lane zones.');
    }
    // 'running' needs both camera and model; while the model is still loading keep that status.
    if (this.landmarker) this._status = 'running';
    else if (this.loading) this._status = 'loading-model';
  }

  private ensureVideo(): HTMLVideoElement {
    if (this.video) return this.video;
    const v = document.createElement('video');
    v.setAttribute('playsinline', '');
    v.setAttribute('autoplay', '');
    v.muted = true;
    v.playsInline = true;
    v.autoplay = true;
    v.dataset.vision = 'camera';
    Object.assign(v.style, {
      position: 'fixed', left: '0', top: '0', width: '1px', height: '1px', opacity: '0', pointerEvents: 'none', zIndex: '-1',
    });
    document.body.appendChild(v);
    this.video = v;
    return v;
  }

  private waitForFrames(video: HTMLVideoElement): Promise<void> {
    if (video.readyState >= 2 && video.videoWidth > 0) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        cleanup();
        reject(new Error('The camera started but sent no video. Try another camera or reload the page.'));
      }, C.cameraStartTimeoutMs);
      const ok = () => {
        if (video.videoWidth > 0) {
          cleanup();
          resolve();
        }
      };
      const cleanup = () => {
        clearTimeout(timer);
        video.removeEventListener('loadeddata', ok);
        video.removeEventListener('playing', ok);
        video.removeEventListener('resize', ok);
      };
      video.addEventListener('loadeddata', ok);
      video.addEventListener('playing', ok);
      video.addEventListener('resize', ok);
    });
  }

  private startFrameWatch(video: HTMLVideoElement): void {
    this.rvfcSupported = typeof video.requestVideoFrameCallback === 'function';
    this.framePending = true; // waitForFrames already confirmed a decoded frame.
    this.lastMediaTime = -1;
    this.lastMediaTimeRaw = -1;
    this.lastPolledTime = -1;
    this.lastRvfcAt = 0;
    this.lastInferenceAt = 0;
    this.deliveredWindowStart = 0;
    this.deliveredFps = 0;
    this.lowFpsSince = -1;
    this.core.lowFps = false;
    if (!this.rvfcSupported) return;
    if (this.rvfcId) video.cancelVideoFrameCallback(this.rvfcId);
    const cb = (now: number, meta: VideoFrameCallbackMetadata): void => {
      if (this.video !== video || this.disposed) return;
      this.lastRvfcAt = now;
      // Each rVFC callback is a newly presented frame. Don't key on mediaTime alone:
      // live webcam streams in Chrome can report a constant mediaTime, which would
      // freeze tracking after the first frame.
      if (meta.presentedFrames !== this.lastMediaTime || meta.mediaTime !== this.lastMediaTimeRaw) {
        this.lastMediaTime = meta.presentedFrames;
        this.lastMediaTimeRaw = meta.mediaTime;
        this.framePending = true;
      }
      // delivered fps over ~1 s windows
      if (this.deliveredWindowStart === 0) {
        this.deliveredWindowStart = now;
        this.deliveredFramesAtStart = meta.presentedFrames;
      } else if (now - this.deliveredWindowStart >= 1000) {
        this.deliveredFps = ((meta.presentedFrames - this.deliveredFramesAtStart) * 1000) / (now - this.deliveredWindowStart);
        this.deliveredWindowStart = now;
        this.deliveredFramesAtStart = meta.presentedFrames;
      }
      this.rvfcId = video.requestVideoFrameCallback(cb);
    };
    this.rvfcId = video.requestVideoFrameCallback(cb);
  }

  async listCameras(): Promise<MediaDeviceInfo[]> {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      return all.filter((d) => d.kind === 'videoinput');
    } catch {
      return [];
    }
  }

  getVideo(): HTMLVideoElement | null {
    return this.video;
  }

  private applyNumPoses(): void {
    const lm = this.landmarker;
    if (!lm) return;
    lm.setOptions({ numPoses: this.numPoses() }).catch((e: unknown) =>
      console.warn('[vision] could not change numPoses:', errMsg(e)),
    );
  }

  setPlayerCount(n: PlayerCount): void {
    if (n !== this.core.playerCount) {
      this.core.setPlayerCount(n);
      this.applyNumPoses();
    }
  }

  lockSlots(locked: boolean, order?: PlayerId[]): void {
    const before = this.numPoses();
    this.core.lockSlots(locked, order);
    if (this.numPoses() !== before) this.applyNumPoses();
  }

  startCalibration(slot: PlayerId): void {
    this.core.startCalibration(slot, performance.now());
  }

  resetCalibration(slot?: PlayerId): void {
    this.core.resetCalibration(slot);
  }

  private hasNewFrame(video: HTMLVideoElement, now: number): boolean {
    if (this.rvfcSupported && now - this.lastRvfcAt < C.rvfcStaleMs) return this.framePending;
    // Fallback (no rVFC, or rVFC silent e.g. background tab): poll currentTime.
    // Only consume it once inference actually runs; the throttle may defer this frame.
    return this.framePending || video.currentTime !== this.lastPolledTime;
  }

  update(now: number): VisionFrame | null {
    try {
      const video = this.video;
      const lm = this.landmarker;
      if (!video || !lm || !this.cameraReady || this.disposed || this.recovering) return this.lastFrame;
      if (video.readyState < 2 || video.videoWidth === 0) return this.lastFrame;
      if (!this.hasNewFrame(video, now)) return this.lastFrame;
      if (this.inferenceMs > C.slowInferenceMs && now - this.lastInferenceAt < C.slowInferenceMinIntervalMs) {
        return this.lastFrame;
      }
      this.framePending = false;
      this.lastPolledTime = video.currentTime;
      // Calibration, polling, and gestures share the application's performance clock.
      // Alternating captureTime with now can send gesture time backwards when rVFC
      // resumes after a stall, corrupting the motion history used for jumps and ducks.
      const ts = Math.max(this.lastTs + 1, now);
      this.lastTs = ts;
      const t0 = performance.now();
      let result: PoseLandmarkerResult;
      try {
        result = lm.detectForVideo(video, ts);
      } catch (e) {
        if (++this.consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
          if (this.delegate === 'GPU' && !this.cpuFallbackTried) void this.fallbackToCpu(e);
          else this.trackingFailed(e);
        }
        return this.lastFrame;
      }
      this.consecutiveFailures = 0;
      if (this._status === 'error' && this.landmarker) {
        this._status = 'running';
        this._error = null;
      }
      const ms = performance.now() - t0;
      const a = C.statsAlpha;
      this.inferenceMs = this.inferenceMs === 0 ? ms : this.inferenceMs + a * (ms - this.inferenceMs);
      if (this.lastInferenceAt > 0) {
        const dt = now - this.lastInferenceAt;
        if (dt > 0) {
          const inst = 1000 / dt;
          this.fps = this.fps === 0 ? inst : this.fps + a * (inst - this.fps);
        }
      }
      this.lastInferenceAt = now;
      // low-light detection via delivered camera fps
      if (this.deliveredFps > 0 && this.deliveredFps < C.lowFpsThreshold) {
        if (this.lowFpsSince < 0) this.lowFpsSince = now;
        this.core.lowFps = now - this.lowFpsSince >= C.lowFpsHoldMs;
      } else {
        this.lowFpsSince = -1;
        this.core.lowFps = false;
      }
      const yScale = video.videoHeight / video.videoWidth;
      this.lastFrame = this.core.process(result.landmarks ?? [], ts, yScale, this.inferenceMs, this.fps);
      return this.lastFrame;
    } catch (e) {
      // Never throw into the game loop.
      if (this._error === null) console.warn('[vision] update failed:', errMsg(e));
      return this.lastFrame;
    }
  }

  onAction(cb: (e: ActionEvent) => void): () => void {
    return this.core.actions.on(cb);
  }

  getTuning(): VisionTuning {
    return this.core.getTuning();
  }

  setTuning(t: Partial<VisionTuning>): void {
    this.core.setTuning(t);
  }

  captureSelfie(slot: PlayerId, size: number = C.selfieSize): string | null {
    try {
      const lm = this.core.latest[slot];
      if (!this.video || !lm) return null;
      // Only crop where the player is right now: stale landmarks (they stepped out) would
      // frame the background or someone else. The caller retries until they're back.
      const f = this.lastFrame;
      if (!f || performance.now() - f.t > SELFIE_MAX_AGE_MS || !f.people.some((p) => p.slot === slot)) return null;
      return captureSelfieFromVideo(this.video, lm, this.core.getTuning().mirror, size);
    } catch (e) {
      console.warn('[vision] selfie failed:', errMsg(e));
      return null;
    }
  }

  /** Currently loaded model (null before loadModel resolves). */
  get currentModel(): PoseModel | null {
    return this.model;
  }

  private stopStream(): void {
    this.detachTrackListeners?.();
    this.detachTrackListeners = null;
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.cameraReady = false;
  }

  dispose(): void {
    this.disposed = true;
    this.loadToken++;
    this.cameraToken++;
    if (this.video && this.rvfcId && this.rvfcSupported) this.video.cancelVideoFrameCallback(this.rvfcId);
    this.stopStream();
    if (this.deviceChangeHooked) {
      navigator.mediaDevices?.removeEventListener?.('devicechange', this.onDeviceChange);
      this.deviceChangeHooked = false;
    }
    if (this.video) {
      this.video.srcObject = null;
      this.video.remove();
      this.video = null;
    }
    if (this.landmarker) {
      try {
        this.landmarker.close();
      } catch {
        /* ignore */
      }
      this.landmarker = null;
    }
    this.core.dispose();
    this._status = 'idle';
  }
}
