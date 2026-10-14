// Fault isolation between modules: a throwing call in one module (audio, vision,
// a world) is reported (rate-limited) and replaced by a harmless default, so the
// state machine never half-transitions and the frame loop keeps running.
import type {
  ActionEvent, GameEvent, IAudio, IAutoPilot, IVisionSystem, IWorld, MusicMode, PlayerAction, PlayerCount,
  PlayerId, PoseModel, UISound, VisionFrame, VisionStatus, VisionTuning, WorldOptions, WorldSnapshot,
} from '../shared/types';
import { DEFAULT_VISION_TUNING } from '../shared/constants';

export type Report = (where: string, err: unknown) => void;

/** console.error the first few failures per call site, then every 300th (no per-frame spam). */
export class ErrorReporter {
  private readonly counts = new Map<string, number>();

  readonly report: Report = (where, err) => {
    const n = (this.counts.get(where) ?? 0) + 1;
    this.counts.set(where, n);
    if (n <= 3 || n % 300 === 0) console.error(`[app] ${where} failed${n > 3 ? ` (×${n})` : ''}:`, err);
  };

  count(where: string): number {
    return this.counts.get(where) ?? 0;
  }
}

const resolved = Promise.resolve();

export class SafeAudio implements IAudio {
  constructor(
    private readonly inner: IAudio,
    private readonly report: Report,
  ) {}

  get unlocked(): boolean {
    try {
      return this.inner.unlocked;
    } catch {
      return false;
    }
  }

  unlock(): Promise<void> {
    try {
      return this.inner.unlock().catch((e: unknown) => this.report('audio.unlock', e));
    } catch (e) {
      this.report('audio.unlock', e);
      return resolved;
    }
  }

  setMuted(m: boolean): void {
    try {
      this.inner.setMuted(m);
    } catch (e) {
      this.report('audio.setMuted', e);
    }
  }

  setVolumes(music: number, sfx: number): void {
    try {
      this.inner.setVolumes(music, sfx);
    } catch (e) {
      this.report('audio.setVolumes', e);
    }
  }

  playGameEvent(e: GameEvent, playerId: PlayerId, playerCount: PlayerCount): void {
    try {
      this.inner.playGameEvent(e, playerId, playerCount);
    } catch (err) {
      this.report('audio.playGameEvent', err);
    }
  }

  playUi(s: UISound): void {
    try {
      this.inner.playUi(s);
    } catch (e) {
      this.report('audio.playUi', e);
    }
  }

  setMusic(mode: MusicMode): void {
    try {
      this.inner.setMusic(mode);
    } catch (e) {
      this.report('audio.setMusic', e);
    }
  }

  setIntensity(level: number): void {
    try {
      this.inner.setIntensity(level);
    } catch (e) {
      this.report('audio.setIntensity', e);
    }
  }

  setChargeLevel(level: number): void {
    try {
      this.inner.setChargeLevel(level);
    } catch (e) {
      this.report('audio.setChargeLevel', e);
    }
  }

  clearPowerUps(): void {
    try {
      this.inner.clearPowerUps();
    } catch (e) {
      this.report('audio.clearPowerUps', e);
    }
  }

  setPaused(paused: boolean): void {
    try {
      this.inner.setPaused(paused);
    } catch (e) {
      this.report('audio.setPaused', e);
    }
  }

  dispose(): void {
    try {
      this.inner.dispose();
    } catch (e) {
      this.report('audio.dispose', e);
    }
  }
}

export class SafeVision implements IVisionSystem {
  constructor(
    private readonly inner: IVisionSystem,
    private readonly report: Report,
  ) {}

  get status(): VisionStatus {
    try {
      return this.inner.status;
    } catch {
      return 'error';
    }
  }

  get error(): string | null {
    try {
      return this.inner.error;
    } catch {
      return null;
    }
  }

  private wrapAsync<T>(where: string, fn: () => Promise<T>): Promise<T> {
    try {
      return fn();
    } catch (e) {
      this.report(where, e);
      return Promise.reject(e instanceof Error ? e : new Error(String(e)));
    }
  }

  private call(where: string, fn: () => void): void {
    try {
      fn();
    } catch (e) {
      this.report(where, e);
    }
  }

  loadModel(model: PoseModel): Promise<void> {
    return this.wrapAsync('vision.loadModel', () => this.inner.loadModel(model));
  }

  startCamera(deviceId?: string | null): Promise<void> {
    return this.wrapAsync('vision.startCamera', () => this.inner.startCamera(deviceId));
  }

  listCameras(): Promise<MediaDeviceInfo[]> {
    return this.wrapAsync('vision.listCameras', () => this.inner.listCameras());
  }

  getVideo(): HTMLVideoElement | null {
    try {
      return this.inner.getVideo();
    } catch (e) {
      this.report('vision.getVideo', e);
      return null;
    }
  }

  setPlayerCount(n: PlayerCount): void {
    this.call('vision.setPlayerCount', () => this.inner.setPlayerCount(n));
  }

  lockSlots(locked: boolean, order?: PlayerId[]): void {
    this.call('vision.lockSlots', () => this.inner.lockSlots(locked, order));
  }

  startCalibration(slot: PlayerId): void {
    this.call('vision.startCalibration', () => this.inner.startCalibration(slot));
  }

  resetCalibration(slot?: PlayerId): void {
    this.call('vision.resetCalibration', () => this.inner.resetCalibration(slot));
  }

  update(now: number): VisionFrame | null {
    try {
      return this.inner.update(now);
    } catch (e) {
      this.report('vision.update', e);
      return null;
    }
  }

  onAction(cb: (e: ActionEvent) => void): () => void {
    try {
      return this.inner.onAction(cb);
    } catch (e) {
      this.report('vision.onAction', e);
      return () => {};
    }
  }

  getTuning(): VisionTuning {
    try {
      return this.inner.getTuning();
    } catch (e) {
      this.report('vision.getTuning', e);
      return { ...DEFAULT_VISION_TUNING };
    }
  }

  setTuning(t: Partial<VisionTuning>): void {
    this.call('vision.setTuning', () => this.inner.setTuning(t));
  }

  captureSelfie(slot: PlayerId, size?: number): string | null {
    try {
      return this.inner.captureSelfie(slot, size);
    } catch (e) {
      this.report('vision.captureSelfie', e);
      return null;
    }
  }

  dispose(): void {
    this.call('vision.dispose', () => this.inner.dispose());
  }
}

/** Minimal valid snapshot used only until a world produces a real one. */
export function emptySnapshot(playerId: PlayerId): WorldSnapshot {
  return {
    playerId,
    status: 'ready',
    seed: 0,
    time: 0,
    distance: 0,
    speed: 0,
    speedLevel: 0,
    score: 0,
    coins: 0,
    multiplier: 1,
    player: {
      lane: 0, x: 0, y: 0, vy: 0, grounded: true, onRoof: false, anim: 'run', animTime: 0, rolling: false,
      lean: 0, blockedDir: 0, hoverboard: false, invulnerable: 0, alive: true,
    },
    obstacles: [],
    collectibles: [],
    powerUps: [],
    hint: null,
  };
}

const NO_EVENTS: GameEvent[] = [];

export class SafeWorld implements IWorld {
  private last: WorldSnapshot | null = null;

  constructor(
    private readonly inner: IWorld,
    private readonly report: Report,
  ) {}

  get playerId(): PlayerId {
    return this.inner.playerId;
  }

  reset(opts: WorldOptions): void {
    try {
      this.inner.reset(opts);
    } catch (e) {
      this.report('world.reset', e);
    }
  }

  start(): void {
    try {
      this.inner.start();
    } catch (e) {
      this.report('world.start', e);
    }
  }

  handleAction(a: PlayerAction): void {
    try {
      this.inner.handleAction(a);
    } catch (e) {
      this.report('world.handleAction', e);
    }
  }

  step(dt: number): void {
    try {
      this.inner.step(dt);
    } catch (e) {
      this.report('world.step', e);
    }
  }

  snapshot(): WorldSnapshot {
    try {
      const s = this.inner.snapshot();
      this.last = s;
      return s;
    } catch (e) {
      this.report('world.snapshot', e);
      return this.last ?? (this.last = emptySnapshot(this.inner.playerId));
    }
  }

  drainEvents(): GameEvent[] {
    try {
      return this.inner.drainEvents();
    } catch (e) {
      this.report('world.drainEvents', e);
      return NO_EVENTS;
    }
  }
}

export class SafeAutoPilot implements IAutoPilot {
  constructor(
    private readonly inner: IAutoPilot,
    private readonly report: Report,
  ) {}

  decide(s: WorldSnapshot): PlayerAction | null {
    try {
      return this.inner.decide(s);
    } catch (e) {
      this.report('autopilot.decide', e);
      return null;
    }
  }
}
