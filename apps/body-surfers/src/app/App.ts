// The integration shell (MASTERPLAN §10): state machine, fixed-step loop, input routing,
// UIModel builder, audio/renderer wiring, settings + leaderboard stores, booth feed.
import { AudioManager } from '../audio';
import { AutoPilot, World } from '../game';
import { KeyboardInput } from '../input';
import { Renderer } from '../render';
import { UIManager } from '../ui';
import { VisionSystem } from '../vision';
import { DoorBridge } from '../door/bridge';
import { DOOR, DOOR_NOTICE } from '../door/config';
import { doorExitReason } from '../door/watchdog';
import { whepCameraSource } from '../door/whepSource';
import { PLAYER_LABELS, STORAGE_KEYS } from '../shared/constants';
import { randomSeed } from '../shared/rng';
import type {
  ActionEvent, AppParams, DebugStats, IKeyboardInput, IRenderer, IUI, IVisionSystem, LeaderboardEntry, MenuKey,
  MimicPose, PlayerCount, PlayerId, PlayerResult, RenderView, Screen, Settings, UICommand, UIModel, UISound, VisionFrame,
  WorldSnapshot,
} from '../shared/types';
import { Attract } from './Attract';
import { BoothBroadcaster } from './booth';
import { APP } from './config';
import { ErrorReporter, SafeAudio, SafeAutoPilot, SafeVision, SafeWorld } from './guards';
import { LeaderboardStore, newEntryId } from './leaderboard';
import { MimicFilter } from './mimic';
import { FrameP90, FrameStats } from './loop';
import { funName } from './names';
import {
  Countdown, JoinFlow, ResumeGate, bootProgress, calibrationOf, cameraErrorMessage, decideWinner, gameoverShouldExit,
  invisiblePlayers, isCalibrated, maxHandsUp, missingPlayers, modelErrorMessage, presentPlayers, restartLockoutLeft,
} from './rules';
import { Session } from './Session';
import { SettingsStore, applyParamOverrides, mergeSettings, sameTuning } from './settings';
import { browserStorage } from './storage';
import { applyTutorialDone, isTutorialDone, setTutorialDone, tutorialCleared, tutorialReenabled } from './tutorial';

const r3 = (v: number): number => Math.round(v * 1000) / 1000;

/** Vision rejected a model/camera switch but kept the previous one running (KeptPreviousError). */
const keptPrevious = (e: unknown): boolean =>
  typeof e === 'object' && e !== null && (e as { keptPrevious?: unknown }).keptPrevious === true;

export interface AppDebugState {
  screen: Screen;
  playerCount: PlayerCount;
  worlds: { status: string; distance: number; score: number; coins: number; lane: number; y: number }[];
  visionStatus: string;
  /** Latest mimic pose per player slot (null = off / not tracked). */
  mimic: (MimicPose | null)[];
}

/** Bounded timeline of app milestones + pose actions (tests / debugging). */
export interface AppLogEntry {
  /** performance.now() ms */
  t: number;
  ev: string;
}

declare global {
  interface Window {
    __bodySurfers?: { app: App; state(): AppDebugState; log: AppLogEntry[] };
  }
}

const NO_EVENTS: readonly never[] = [];
export const KEYBOARD_HINT = 'Playing with keyboard: arrows to move, space to jump';
export const CAMERA_LOST_HINT = 'Camera disconnected. Press Space to keep playing with the keyboard';
export const LOW_QUALITY_TOAST = 'Lowered graphics for smoother play';
export const NO_WEBGL2_ERROR = "Your browser can't run 3D graphics (WebGL2). Try Chrome with hardware acceleration on.";
export const CONTEXT_LOST_ERROR = 'Graphics reset, click to reload';
const BOOT_MESSAGES: Record<string, string> = {
  idle: 'Getting ready…',
  'loading-model': 'Loading body tracking…',
  'model-ready': 'Ready!',
  'starting-camera': 'Starting camera…',
  running: 'Camera on!',
  error: 'Something went wrong',
};

export class App {
  private readonly errors = new ErrorReporter();
  private readonly report = this.errors.report;
  private readonly storage = browserStorage();
  private readonly settingsStore = new SettingsStore(this.storage);
  private readonly leaderboard = new LeaderboardStore(this.storage);
  private readonly booth = new BoothBroadcaster();

  /** Persisted settings (no URL overrides). */
  private stored: Settings;
  /** Effective settings (URL overrides applied). */
  private settings: Settings;

  private readonly audio = new SafeAudio(new AudioManager(), this.report);
  private readonly renderer: IRenderer = new Renderer();
  private readonly ui: IUI = new UIManager();
  private readonly keyboard: IKeyboardInput = new KeyboardInput();
  private vision: IVisionSystem | null = null;
  private readonly session: Session;
  private readonly attract: Attract;

  private inputMode: 'camera' | 'keyboard';
  private screen: Screen = 'boot';
  private screenAt = 0;
  private readonly queue: ActionEvent[] = [];
  private frame: VisionFrame | null = null;
  private lastFrameT = -1;
  private lastFrameAt = 0;

  // boot
  private clicked = false;
  private bootError: string | null = null;
  private bootPending = false;
  private loadingSince = 0;
  /** Current model load; the camera and calibration wait for it (GPU init can block ~7 s). */
  private modelLoad: Promise<void> | null = null;
  private modelReady = false;
  /** Model the vision system is actually running (null until the first load succeeds). */
  private activeModel: Settings['model'] | null = null;
  /** Calibration deferred until the model + camera are running. */
  private calibPending = false;
  // lobby
  private readonly join = new JoinFlow();
  // countdown
  private readonly countdown = new Countdown();
  private countdownValue = 3;
  private countdownHolding = false;
  private goAt: number | null = null;
  /** Countdown: since when no session player has been in view (null = someone visible). */
  private noViewSince: number | null = null;
  /** The current run was started with the tutorial on (and has not cleared it yet). */
  private runTutorial = false;
  // paused
  private pauseReason: 'user' | 'tracking-lost' = 'user';
  private readonly resume = new ResumeGate();
  // gameover
  private results: PlayerResult[] = [];
  private gameoverAt = 0;
  private lastActivity = 0;
  private restartArmed = false;

  private settingsOpen = false;
  private debugOn: boolean;
  private debug: DebugStats | null = null;
  private debugAt = 0;
  private toast: { text: string; t: number } | null = null;
  /** Re-raised every frame it would expire, until cleared (keyboard hint, camera lost). */
  private stickyToast: string | null = null;
  /** The player chose keyboard over a tracking-lost pause: no more tracking pauses this run. */
  private trackingPauseOff = false;
  /** Fatal graphics state: no WebGL2 / renderer init failed ('broken'), or the context was lost. */
  private graphics: 'ok' | 'broken' | 'lost' = 'ok';
  // adaptive quality (runtime override, never saved)
  private readonly frameP90 = new FrameP90();
  private slowSince: number | null = null;
  private qualityLowered = false;
  private rafId = 0;
  private canvas: HTMLCanvasElement | null = null;
  private cameras: { deviceId: string; label: string }[] = [];
  private readonly stats = new FrameStats();
  /** Per-slot arm/torso mimic filters + their latest output. */
  private readonly mimicFilters = [new MimicFilter(), new MimicFilter()];
  private mimicPoses: (MimicPose | null)[] = [null, null];
  private last = 0;
  private running = false;
  readonly log: AppLogEntry[] = [];
  // door mode (ADR-0049): hosted by the doorboard wallboard
  private door: DoorBridge | null = null;
  /** Last time anyone was in view (door watchdog). */
  private doorSeenAt = 0;
  /** When the current boot / tracking error first showed (door watchdog), or null. */
  private doorErrorAt: number | null = null;
  /** The exit message went out; the wallboard is unmounting us. */
  private doorExited = false;

  constructor(readonly params: AppParams) {
    const loaded = this.settingsStore.load();
    // Played the tutorial once before → later runs start without it (toggle can re-enable).
    this.stored = applyTutorialDone(loaded, isTutorialDone(this.storage));
    if (this.stored !== loaded) this.settingsStore.save(this.stored);
    this.settings = applyParamOverrides(this.stored, params);
    this.inputMode = params.input;
    this.debugOn = params.debug;
    const pilot = () => new SafeAutoPilot(new AutoPilot(), this.report);
    this.session = new Session((id) => new SafeWorld(new World(id), this.report), pilot);
    this.attract = new Attract(new SafeWorld(new World(0), this.report), pilot, randomSeed);
  }

  async start(): Promise<void> {
    if (this.running) return;
    this.running = true;
    const now = performance.now() / 1000;
    this.last = performance.now();
    const canvas = document.getElementById('game-canvas') as HTMLCanvasElement | null;
    const root = document.getElementById('ui-root');

    this.canvas = canvas;
    if (!hasWebGL2()) {
      this.failGraphics('no webgl2', null);
    } else {
      try {
        if (canvas) this.renderer.init(canvas, { quality: this.settings.quality });
        this.renderer.setPlayerCount(1);
        this.renderer.resize(window.innerWidth, window.innerHeight);
      } catch (e) {
        this.failGraphics('renderer.init', e);
      }
    }
    canvas?.addEventListener('webglcontextlost', this.onContextLost);
    this.guard('ui.init', () => {
      if (root) this.ui.init(root);
      this.ui.onCommand((c) => this.guard('command', () => this.onCommand(c)));
    });
    this.guard('keyboard.attach', () => {
      this.keyboard.attach(window);
      this.keyboard.setPlayerCount(1);
      this.keyboard.onAction((e) => this.onAction(e));
      this.keyboard.onMenuKey((k) => this.guard('menuKey', () => this.onMenuKey(k)));
    });
    this.audio.setMuted(this.settings.muted);
    this.audio.setVolumes(this.settings.musicVolume, this.settings.sfxVolume);

    window.addEventListener('resize', this.onResize);
    window.addEventListener('storage', this.onStorage);
    document.addEventListener('visibilitychange', this.onVisibility);
    // Any user gesture unlocks audio (autostart / keyboard players never click the boot button).
    window.addEventListener('pointerdown', this.onUnlock, { capture: true });
    window.addEventListener('keydown', this.onUnlock, { capture: true });

    if (this.inputMode === 'camera' && this.graphics === 'ok') this.initVision();
    this.attract.reset();

    // Test / console handle: dev server or ?debug=1 only.
    if (import.meta.env.DEV || this.params.debug) {
      window.__bodySurfers = { app: this, state: () => this.debugState(), log: this.log };
    }

    this.setScreen('boot', now);
    if (this.params.door) {
      this.startDoor(this.params.door.parentOrigin, now);
    } else if (this.params.autostart && this.graphics === 'ok') {
      this.clicked = true;
      if (this.vision) void this.startCamera(false);
      this.beginCountdown(this.params.players === 2 ? [0, 1] : [0], now);
    }
    this.rafId = requestAnimationFrame(this.tick);
  }

  /** Stop the loop, remove every listener and release renderer, vision, booth, audio, UI and keyboard. */
  dispose(): void {
    this.running = false;
    cancelAnimationFrame(this.rafId);
    window.removeEventListener('resize', this.onResize);
    window.removeEventListener('storage', this.onStorage);
    document.removeEventListener('visibilitychange', this.onVisibility);
    window.removeEventListener('pointerdown', this.onUnlock, { capture: true });
    window.removeEventListener('keydown', this.onUnlock, { capture: true });
    this.canvas?.removeEventListener('webglcontextlost', this.onContextLost);
    this.guard('keyboard.dispose', () => this.keyboard.dispose());
    this.guard('ui.dispose', () => this.ui.dispose());
    this.guard('renderer.dispose', () => this.renderer.dispose());
    if (this.vision) {
      this.vision.dispose();
      this.vision = null;
    }
    this.guard('booth.dispose', () => this.booth.dispose());
    this.door = null;
    this.audio.dispose();
    if (window.__bodySurfers?.app === this) delete window.__bodySurfers;
  }

  private readonly onResize = (): void => {
    if (this.graphics !== 'ok') return;
    this.guard('renderer.resize', () => this.renderer.resize(window.innerWidth, window.innerHeight));
  };

  /** Another tab wrote the leaderboard: show its entries here too. */
  private readonly onStorage = (e: StorageEvent): void => {
    if (e.key === null || e.key === STORAGE_KEYS.leaderboard) this.leaderboard.reload();
  };

  private readonly onVisibility = (): void => {
    if (document.hidden && (this.screen === 'playing' || (this.screen === 'countdown' && this.goAt !== null))) {
      this.enterPaused('user');
    }
  };

  private readonly onUnlock = (): void => {
    void this.audio.unlock().then(() => this.syncMusic());
  };

  /** WebGL context lost (GPU reset / driver crash): stop the run and offer a reload. */
  private readonly onContextLost = (e: Event): void => {
    e.preventDefault();
    if (this.graphics !== 'ok') return;
    this.note('webgl-context-lost');
    if (this.screen === 'playing' || this.screen === 'countdown') this.enterPaused('user');
    this.graphics = 'lost';
    this.bootError = CONTEXT_LOST_ERROR;
    this.stickyToast = null;
    this.toast = null;
    this.goAt = null;
    this.session.end();
    this.setScreen('boot', performance.now() / 1000);
  };

  private failGraphics(where: string, e: unknown): void {
    if (e !== null) this.report(where, e);
    else console.warn('[app] WebGL2 is not available');
    this.graphics = 'broken';
    this.bootError = NO_WEBGL2_ERROR;
  }

  // ---------------------------------------------------------------------------
  // Setup helpers
  // ---------------------------------------------------------------------------

  private note(ev: string): void {
    if (this.log.length >= 300) this.log.splice(0, 100);
    this.log.push({ t: Math.round(performance.now()), ev });
  }

  private guard(where: string, fn: () => void): void {
    try {
      fn();
    } catch (e) {
      this.report(where, e);
    }
  }

  private initVision(): void {
    try {
      const door = this.params.door;
      // ?webcam=1 (dev only): door mode on a laptop, with its own camera instead of door-media.
      const system = door && !door.webcam
        ? new VisionSystem({
            source: whepCameraSource({ metadataUrl: door.mediaUrl, streamName: door.stream }),
            minInferenceIntervalMs: DOOR.minInferenceIntervalMs,
            lowFpsThreshold: DOOR.lowFpsThreshold,
          })
        : new VisionSystem();
      const v = new SafeVision(system, this.report);
      this.vision = v;
      v.setTuning(this.settings.vision);
      v.setPlayerCount(this.params.players ?? 2);
      v.onAction((e) => this.onAction(e));
      this.loadModel();
    } catch (e) {
      this.report('vision.create', e);
      this.vision = null;
      this.bootError = modelErrorMessage(e);
    }
  }

  private loadModel(): void {
    const v = this.vision;
    if (!v) return;
    this.loadingSince = performance.now() / 1000;
    this.modelReady = false;
    const prevModel = this.activeModel;
    const model = this.settings.model;
    const load = v.loadModel(model);
    this.modelLoad = load;
    load.then(
      () => {
        if (this.modelLoad === load) {
          this.modelReady = true;
          this.activeModel = model;
        }
        this.note('model-ready');
      },
      () => {},
    );
    load.catch((e: unknown) => {
      if (keptPrevious(e) && this.vision === v && this.modelLoad === load) {
        // The old model still tracks: stay ready and put the setting back.
        this.modelReady = true;
        if (prevModel) this.revertSettings({ model: prevModel });
        this.raiseToast(`Couldn't load the ${model} model, still using ${prevModel ?? 'the previous one'}.`);
        console.warn('[app] model switch failed, kept the previous model:', e);
        return;
      }
      this.report('vision.loadModel', e);
      if (this.vision === v && (this.screen === 'boot' || this.screen === 'lobby' || this.screen === 'countdown')) {
        this.showCameraError(modelErrorMessage(e, v.error));
      }
    });
  }

  /** Start the camera; `fromBoot` → lobby on success / boot error on failure. */
  private async startCamera(fromBoot: boolean): Promise<void> {
    const v = this.vision;
    if (!v || this.bootPending) return;
    this.bootPending = true;
    this.bootError = null;
    try {
      // Never start the camera before the model has fully initialised.
      try {
        if (this.modelLoad) await this.modelLoad;
      } catch (e) {
        if (this.vision === v) this.showCameraError(modelErrorMessage(e, v.error));
        return;
      }
      if (this.vision !== v) return;
      this.note('camera-start');
      await v.startCamera(this.settings.cameraId);
      this.note('camera-running');
      void this.refreshCameras();
      if (fromBoot && this.screen === 'boot') this.enterLobby(performance.now() / 1000);
    } catch (e) {
      // Expected (permission denied / no camera): the boot card explains it, so not an app error.
      console.warn('[app] camera unavailable:', e);
      if (this.vision === v) this.showCameraError(cameraErrorMessage(e, v.error));
    } finally {
      this.bootPending = false;
    }
  }

  private showCameraError(message: string): void {
    this.bootError = message;
    this.calibPending = false;
    this.goAt = null;
    this.session.end();
    this.setScreen('boot', performance.now() / 1000);
  }

  private async refreshCameras(): Promise<void> {
    if (!this.vision) return;
    try {
      const list = await this.vision.listCameras();
      this.cameras = list
        .filter((d) => d.kind === 'videoinput')
        .map((d, i) => ({ deviceId: d.deviceId, label: d.label || `Camera ${i + 1}` }));
    } catch (e) {
      this.report('vision.listCameras', e);
    }
  }

  private useKeyboardOnly(): void {
    this.inputMode = 'keyboard';
    this.bootError = null;
    this.frame = null;
    this.calibPending = false;
    if (this.vision) {
      this.vision.dispose();
      this.vision = null;
    }
  }

  // ---------------------------------------------------------------------------
  // Door mode
  // ---------------------------------------------------------------------------

  /** Hosted by the wallboard: nothing here can be clicked, so start the camera now. */
  private startDoor(parentOrigin: string | null, now: number): void {
    this.clicked = true;
    this.doorSeenAt = now;
    document.documentElement.classList.add('bs-door');
    this.leaderboard.retainSince(Date.now() - DOOR.leaderboardMaxAgeDays * 86_400_000);
    const bridge = new DoorBridge(window, parentOrigin);
    this.door = bridge;
    if (this.vision) void this.startCamera(true);
    bridge.post({ type: 'ready' });
  }

  /** Hand the wallboard back once the run is over, nobody is there, or the camera failed. */
  private updateDoor(now: number): void {
    if (!this.door || this.doorExited) return;
    if (this.frame && this.frame.people.length > 0) this.doorSeenAt = now;
    const error = this.bootError !== null || this.vision?.status === 'error';
    if (!error) this.doorErrorAt = null;
    else if (this.doorErrorAt === null) this.doorErrorAt = now;
    const reason = doorExitReason({
      screen: this.screen,
      screenFor: now - this.screenAt,
      nobodyFor: now - this.doorSeenAt,
      errorFor: this.doorErrorAt === null ? null : now - this.doorErrorAt,
    });
    if (!reason) return;
    this.doorExited = true;
    this.note(`door-exit:${reason}`);
    this.door.post({ type: 'exit', reason });
  }

  // ---------------------------------------------------------------------------
  // Inputs
  // ---------------------------------------------------------------------------

  private onAction(e: ActionEvent): void {
    if (e.source === 'pose') {
      const a = e.action;
      this.note(`pose:p${e.playerId}:${a.type === 'lane' ? `lane${a.lane}` : a.type === 'move' ? `move${a.dir}` : a.type}`);
    }
    if (e.source === 'keyboard') this.lastActivity = performance.now() / 1000;
    if (this.stickyToast === KEYBOARD_HINT && (this.screen === 'playing' || this.goAt !== null)) this.stickyToast = null;
    if (this.queue.length < 64) this.queue.push(e);
  }

  private onMenuKey(k: MenuKey): void {
    const now = performance.now() / 1000;
    this.lastActivity = now;
    if (this.graphics !== 'ok') {
      if (this.graphics === 'lost' && k === 'confirm') location.reload();
      return;
    }
    switch (k) {
      case 'mute':
        this.changeSettings({ muted: !this.settings.muted });
        return;
      case 'fullscreen':
        this.toggleFullscreen();
        return;
      case 'settings':
        this.settingsOpen = !this.settingsOpen;
        if (this.settingsOpen) void this.refreshCameras();
        return;
      case 'debug':
        this.debugOn = !this.debugOn;
        return;
    }
    if (this.settingsOpen) {
      if (k === 'back') this.settingsOpen = false;
      return;
    }
    switch (this.screen) {
      case 'boot':
        if (k === 'confirm' || k === 'one-player' || k === 'two-player') {
          if (this.bootError) this.onCommand({ type: 'keyboard-only' });
          else if (!this.vision || this.modelReady) this.onCommand({ type: 'boot-click' });
        }
        break;
      case 'lobby':
        if (k === 'confirm' || k === 'one-player') this.startFromLobby(1, now);
        else if (k === 'two-player') this.startFromLobby(2, now);
        break;
      case 'countdown':
        if (k === 'back') this.enterLobby(now);
        else if (k === 'pause' && this.goAt !== null) this.enterPaused('user');
        break;
      case 'playing':
        if (k === 'pause') this.enterPaused('user');
        else if (k === 'back') this.enterLobby(now);
        break;
      case 'paused':
        if (k === 'back') this.enterLobby(now);
        else if (k === 'pause' || k === 'confirm') this.resumeByUser();
        break;
      case 'gameover':
        if (k === 'back') this.enterLobby(now);
        else if (this.restartUnlocked(now)) {
          if (k === 'confirm') this.restart(now);
          else if (k === 'one-player') this.beginCountdown([0], now);
          else if (k === 'two-player') this.beginCountdown([0, 1], now);
        }
        break;
    }
  }

  private onCommand(c: UICommand): void {
    const now = performance.now() / 1000;
    this.lastActivity = now;
    if (this.graphics !== 'ok') {
      // Graphics are gone: the boot card's buttons (TRY AGAIN / keyboard) reload the page.
      if (this.graphics === 'lost' && (c.type === 'boot-click' || c.type === 'keyboard-only')) location.reload();
      if (c.type !== 'fullscreen' && c.type !== 'settings-open' && c.type !== 'settings-change') return;
    }
    switch (c.type) {
      case 'boot-click':
        this.audio.playUi('click');
        this.clicked = true;
        void this.audio.unlock().then(() => this.syncMusic());
        if (this.screen !== 'boot') return;
        if (!this.vision && this.inputMode === 'camera') this.initVision();
        else if (this.vision && !this.modelReady && this.vision.status === 'error') this.loadModel();
        if (this.vision) void this.startCamera(true);
        else if (this.inputMode === 'keyboard') this.enterLobby(now);
        break;
      case 'keyboard-only':
        this.clicked = true;
        void this.audio.unlock().then(() => this.syncMusic());
        this.useKeyboardOnly();
        if (this.screen === 'boot') this.enterLobby(now);
        else if (this.screen === 'countdown') this.beginCountdown(this.session.count === 2 ? [0, 1] : [0], now);
        break;
      case 'start':
        // The ▶ PLAY button always starts a run (even before the boot click / camera is up).
        if (this.screen === 'lobby' || this.screen === 'boot') {
          const fromBoot = this.screen === 'boot';
          if (!this.clicked) {
            this.clicked = true;
            void this.audio.unlock().then(() => this.syncMusic());
          }
          if (fromBoot && !this.vision && this.inputMode === 'camera' && !this.bootError) this.initVision();
          this.startFromLobby(c.players, now);
          if (fromBoot && this.vision && this.vision.status !== 'running') void this.startCamera(false);
        }
        else if (this.screen === 'gameover' && this.restartUnlocked(now)) this.beginCountdown(c.players === 2 ? [0, 1] : [0], now);
        break;
      case 'pause-toggle':
        if (this.screen === 'playing') this.enterPaused('user');
        else if (this.screen === 'paused') this.resumeByUser();
        break;
      case 'restart':
        if (this.screen === 'paused' || (this.screen === 'gameover' && this.restartUnlocked(now))) this.restart(now);
        break;
      case 'to-lobby':
        this.enterLobby(now);
        break;
      case 'rename':
        if (this.leaderboard.rename(c.entryId, c.name)) {
          const e = this.leaderboard.entries.find((x) => x.id === c.entryId);
          if (e) this.results = this.results.map((r) => (r.entryId === c.entryId ? { ...r, name: e.name } : r));
        }
        break;
      case 'settings-open':
        this.settingsOpen = c.open;
        if (c.open) void this.refreshCameras();
        break;
      case 'settings-change':
        this.changeSettings(c.settings);
        break;
      case 'clear-leaderboard':
        this.leaderboard.clear();
        break;
      case 'fullscreen':
        this.toggleFullscreen();
        break;
    }
  }

  private changeSettings(patch: Partial<Settings>): void {
    const prev = this.settings;
    const prevStored = this.stored;
    this.stored = mergeSettings(this.stored, patch);
    if (tutorialReenabled(prevStored, this.stored)) setTutorialDone(this.storage, false);
    this.settings = mergeSettings(prev, patch);
    this.settingsStore.save(this.stored);
    const s = this.settings;
    if (!sameTuning(prev.vision, s.vision)) this.vision?.setTuning(s.vision);
    if (prev.muted !== s.muted) this.audio.setMuted(s.muted);
    if (prev.musicVolume !== s.musicVolume || prev.sfxVolume !== s.sfxVolume) this.audio.setVolumes(s.musicVolume, s.sfxVolume);
    if (prev.quality !== s.quality) this.guard('renderer.setQuality', () => this.renderer.setQuality(s.quality));
    if (prev.model !== s.model) this.loadModel();
    const v = this.vision;
    if (prev.cameraId !== s.cameraId && v && v.status !== 'idle') {
      v.startCamera(s.cameraId).then(
        () => {
          void this.refreshCameras();
          if (this.vision === v) this.recalibrateAfterCameraSwitch();
        },
        (e: unknown) => {
          if (keptPrevious(e) && this.vision === v) {
            this.revertSettings({ cameraId: prev.cameraId });
            this.raiseToast("Couldn't switch cameras (is another app using it?). Still using the previous camera.");
            console.warn('[app] camera switch failed, kept the previous camera:', e);
            return;
          }
          this.report('vision.startCamera', e);
        },
      );
    }
  }

  /** Put settings back after a switch that could not be applied, without re-triggering it. */
  private revertSettings(patch: Partial<Settings>): void {
    this.stored = mergeSettings(this.stored, patch);
    this.settings = mergeSettings(this.settings, patch);
    this.settingsStore.save(this.stored);
  }

  /** New camera = new framing: the lane zones and baselines from the old one no longer apply. */
  private recalibrateAfterCameraSwitch(): void {
    const v = this.vision;
    if (!v) return;
    if (this.screen === 'countdown') {
      v.resetCalibration();
      this.calibPending = true;
      this.frame = null;
      this.startCalibrationIfReady();
    } else if (this.screen === 'playing' || this.screen === 'paused') {
      // Pause so the players stand still for the new baseline, then resume as usual.
      if (this.screen === 'playing') this.enterPaused('user');
      v.resetCalibration();
      for (let i = 0; i < this.session.count; i++) v.startCalibration(i as PlayerId);
      this.raiseToast('New camera: stand still for a moment while it recalibrates.');
    }
  }

  /** Gameover restarts (keys, buttons, hands) are ignored during the lockout. */
  private restartUnlocked(now: number): boolean {
    return restartLockoutLeft(now - this.gameoverAt) === null;
  }

  /** Keyboard / button resume. A tracking-lost pause becomes a keyboard run (no more tracking pauses). */
  private resumeByUser(): void {
    if (this.pauseReason === 'tracking-lost') {
      this.pauseReason = 'user';
      this.trackingPauseOff = true;
      this.note('resume-keyboard');
    }
    this.resume.trigger();
  }

  /** The camera died mid-run (track ended / unplugged): pause and carry on with the keyboard. */
  private onCameraLost(): void {
    const msg = this.vision?.error ?? 'Camera disconnected';
    this.note(`camera-lost:${msg}`);
    this.useKeyboardOnly();
    if (this.screen !== 'paused') this.enterPaused('user');
    else this.pauseReason = 'user';
    this.stickyToast = CAMERA_LOST_HINT;
    this.toast = null;
  }

  private toggleFullscreen(): void {
    try {
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
      else void document.documentElement.requestFullscreen().catch(() => {});
    } catch (e) {
      this.report('fullscreen', e);
    }
  }

  // ---------------------------------------------------------------------------
  // Transitions
  // ---------------------------------------------------------------------------

  private setScreen(s: Screen, now: number): void {
    this.screen = s;
    this.note(`screen:${s}`);
    this.screenAt = now;
    if (s === 'boot' || s === 'lobby') this.booth.idle(now, true);
    if (s !== 'playing' && s !== 'paused' && s !== 'countdown') this.stickyToast = null;
    if (s === 'playing') {
      this.frameP90.reset();
      this.slowSince = null;
    }
    this.syncMusic();
  }

  private syncMusic(): void {
    switch (this.screen) {
      case 'boot':
      case 'lobby':
        this.audio.setMusic('lobby');
        break;
      case 'gameover':
        this.audio.setMusic('gameover');
        break;
      default:
        this.audio.setMusic('game');
    }
  }

  private enterLobby(now: number): void {
    this.session.end();
    this.goAt = null;
    this.results = [];
    this.join.reset();
    this.audio.setChargeLevel(0);
    this.audio.setIntensity(0);
    const v = this.vision;
    if (v) {
      v.setPlayerCount(this.params.players ?? 2);
      v.lockSlots(false);
      v.resetCalibration();
    }
    this.guard('renderer.setPlayerCount', () => this.renderer.setPlayerCount(1));
    this.guard('keyboard.setPlayerCount', () => this.keyboard.setPlayerCount(1));
    if (this.attract.snapshot.status !== 'running') this.attract.reset();
    this.setScreen('lobby', now);
  }

  private startFromLobby(players: PlayerCount, now: number): void {
    const n = this.params.players ?? players;
    this.beginCountdown(n === 2 ? [0, 1] : [0], now);
  }

  private beginCountdown(slots: PlayerId[], now: number): void {
    const n = (slots.length >= 2 ? 2 : 1) as PlayerCount;
    this.runTutorial = this.settings.tutorial;
    this.noViewSince = null;
    this.trackingPauseOff = false;
    this.stickyToast = null;
    this.session.begin(n, this.params.seed ?? randomSeed(), this.runTutorial, this.params.autopilot);
    const v = this.vision;
    if (v) {
      v.lockSlots(true, slots.slice(0, n));
      v.setPlayerCount(n);
      v.resetCalibration();
      this.calibPending = true;
      // Do not reuse the old slots' calibration/presence while waiting for a new frame.
      this.frame = null;
      this.startCalibrationIfReady();
    } else {
      this.calibPending = false;
    }
    this.guard('renderer.setPlayerCount', () => this.renderer.setPlayerCount(n));
    this.guard('keyboard.setPlayerCount', () => this.keyboard.setPlayerCount(n));
    // A new run: drop any jetpack rumble / slowmo feel left over from the last one.
    this.audio.clearPowerUps();
    this.audio.setPaused(false);
    this.countdown.reset();
    this.countdownValue = 3;
    this.countdownHolding = false;
    this.goAt = null;
    this.results = [];
    this.queue.length = 0;
    this.toast = null;
    this.audio.setChargeLevel(0);
    this.audio.setIntensity(0);
    this.setScreen('countdown', now);
  }

  /** Start calibrating once the model is loaded and the camera is running. */
  private startCalibrationIfReady(): boolean {
    const v = this.vision;
    if (!this.calibPending) return true;
    if (!v) {
      this.calibPending = false;
      return true;
    }
    if (!this.modelReady || v.status !== 'running') return false;
    for (let i = 0; i < this.session.count; i++) v.startCalibration(i as PlayerId);
    this.calibPending = false;
    this.note('calibrate');
    return true;
  }

  private restart(now: number): void {
    const slots = this.cameraTracked()
      ? presentPlayers(this.session.players, this.session.count, now, APP.presentWindowS)
      : Array.from({ length: this.session.count }, (_, i) => i as PlayerId);
    this.beginCountdown(slots, now);
  }

  private enterPaused(reason: 'user' | 'tracking-lost'): void {
    if (this.screen === 'paused') return;
    this.pauseReason = reason;
    this.resume.reset();
    this.audio.playUi('pause');
    this.audio.setChargeLevel(0);
    this.audio.setPaused(true);
    this.setScreen('paused', performance.now() / 1000);
  }

  private resumePlay(now: number): void {
    this.session.resetClock();
    this.applyLanes();
    this.queue.length = 0;
    this.audio.playUi('resume');
    this.audio.setPaused(false);
    if (this.stickyToast === CAMERA_LOST_HINT) this.stickyToast = KEYBOARD_HINT;
    this.setScreen('playing', now);
  }

  private enterGameover(now: number): void {
    const s = this.session;
    const taken = new Set(this.leaderboard.entries.map((e) => e.name));
    const entries: LeaderboardEntry[] = [];
    const results: PlayerResult[] = [];
    for (let i = 0; i < s.count; i++) {
      const snap = s.snaps[i];
      const p = s.players[i];
      const name = funName(Math.random, taken);
      taken.add(name);
      const entry: LeaderboardEntry = {
        id: newEntryId(),
        name,
        score: Math.floor(snap.score),
        coins: snap.coins,
        distance: Math.floor(snap.distance),
        date: Date.now() + i,
        photo: p.photo,
        mode: s.count,
      };
      entries.push(entry);
      results.push({
        playerId: i as PlayerId,
        name,
        score: entry.score,
        coins: entry.coins,
        distance: entry.distance,
        time: snap.time,
        photo: p.photo,
        rank: null,
        entryId: null,
      });
    }
    const ranks = this.leaderboard.add(entries);
    let best = Infinity;
    for (let i = 0; i < results.length; i++) {
      const rank = ranks.get(entries[i].id);
      if (rank !== undefined) {
        results[i].rank = rank;
        results[i].entryId = entries[i].id;
        best = Math.min(best, rank);
      }
    }
    this.results = results;
    this.booth.result(results);
    this.gameoverAt = now;
    this.lastActivity = now;
    this.restartArmed = false;
    this.audio.setChargeLevel(0);
    this.audio.playUi(best === 1 ? 'newRecord' : 'gameOver');
    this.setScreen('gameover', now);
  }

  private applyLanes(): void {
    const f = this.frame;
    if (!f || !this.vision) return;
    for (let i = 0; i < this.session.count; i++) {
      const g = f.gestures[i];
      if (g && isCalibrated(g)) this.session.applyLane(i as PlayerId, g.lane);
    }
  }

  // ---------------------------------------------------------------------------
  // Frame loop (§2.1)
  // ---------------------------------------------------------------------------

  private readonly tick = (tMs: number): void => {
    if (!this.running) return;
    this.rafId = requestAnimationFrame(this.tick);
    const t0 = performance.now();
    const dtRaw = Math.max(0, (t0 - this.last) / 1000);
    this.last = t0;
    const dt = Math.min(APP.maxFrameDt, dtRaw);
    this.stats.tick(dtRaw);
    void tMs;
    this.guard('quality', () => this.adaptQuality(t0 / 1000, dtRaw * 1000));
    try {
      this.frameUpdate(t0 / 1000, dt);
    } catch (e) {
      this.report('frame', e);
    }
    this.stats.work(performance.now() - t0);
  };

  /**
   * Adaptive quality: while playing, a rolling p90 of rAF deltas over APP.slowFrameMs for
   * APP.slowForS (at once in 2P) drops the renderer to 'low'. Runtime only (the saved setting
   * is untouched) and never stepped back up automatically.
   */
  private adaptQuality(now: number, deltaMs: number): void {
    if (this.qualityLowered || this.graphics !== 'ok' || this.settings.quality === 'low') return;
    if (this.screen !== 'playing') {
      this.slowSince = null;
      return;
    }
    if (deltaMs <= 0 || deltaMs > APP.frameOutlierMs) return;
    this.frameP90.push(deltaMs);
    if (!this.frameP90.full || this.frameP90.value() <= APP.slowFrameMs) {
      this.slowSince = null;
      return;
    }
    this.slowSince ??= now;
    if (this.session.count === 2 || now - this.slowSince >= APP.slowForS) {
      this.qualityLowered = true;
      this.note(`quality-low:p90=${Math.round(this.frameP90.value())}`);
      this.renderer.setQuality('low');
      this.raiseToast(LOW_QUALITY_TOAST);
    }
  }

  private frameUpdate(now: number, dt: number): void {
    // 1. Vision (fires onAction synchronously).
    if (this.vision) {
      const f = this.vision.update(now * 1000);
      if (f) {
        if (f.t !== this.lastFrameT) {
          this.lastFrameT = f.t;
          this.lastFrameAt = now;
        }
        this.frame = now - this.lastFrameAt > APP.staleFrameS ? null : f;
      } else if (now - this.lastFrameAt > APP.staleFrameS) {
        this.frame = null;
      }
    }
    if (this.cameraTracked()) this.updatePresence(now);
    this.updateDoor(now);
    if (
      this.inputMode === 'camera' && this.vision?.status === 'error' &&
      (this.screen === 'playing' || this.screen === 'paused' || (this.screen === 'countdown' && this.goAt !== null))
    ) {
      this.onCameraLost();
    }

    // 2. Screen logic.
    const sessionEvents = this.screen !== 'boot' && this.screen !== 'lobby';
    switch (this.screen) {
      case 'boot':
      case 'lobby':
        this.attract.update(dt);
        if (this.screen === 'lobby') this.updateLobby(now);
        this.booth.idle(now);
        break;
      case 'countdown':
        this.updateCountdown(now, dt);
        break;
      case 'playing':
        this.updatePlaying(now, dt);
        break;
      case 'paused':
        this.updatePaused(now, dt);
        break;
      case 'gameover':
        this.updateGameover(now, dt);
        break;
    }
    this.queue.length = 0;
    if (this.session.active && (this.screen === 'countdown' || this.screen === 'playing' || this.screen === 'paused')) {
      this.booth.state(now, this.screen, this.session.count, this.session.snaps);
    }

    // 3. Render.
    this.updateMimic(now);
    const views: RenderView[] = [];
    if (this.screen === 'boot' || this.screen === 'lobby' || !this.session.active) {
      // Attract preview mimics whoever is in slot 0.
      views.push({ snapshot: this.attract.snapshot, events: this.attract.events, mimic: this.mimicPoses[0] });
    } else {
      const paused = this.screen === 'paused' || !sessionEvents;
      for (let i = 0; i < this.session.count; i++) {
        views.push({
          snapshot: this.session.snaps[i],
          events: paused ? NO_EVENTS : this.session.events[i],
          mimic: this.mimicPoses[i] ?? null,
        });
      }
    }
    if (this.graphics === 'ok') this.guard('renderer.render', () => this.renderer.render(views, dt));
    this.refreshStickyToast();
    if (this.debugOn && now - this.debugAt >= APP.debugIntervalS) {
      this.debugAt = now;
      let drawCalls = 0;
      try {
        drawCalls = this.renderer.getStats().drawCalls;
      } catch {
        // ignore
      }
      this.debug = {
        fps: Math.round(this.stats.fps),
        frameMs: Math.round(this.stats.frameMs * 10) / 10,
        visionFps: this.frame ? Math.round(this.frame.fps) : 0,
        inferenceMs: this.frame ? Math.round(this.frame.inferenceMs * 10) / 10 : 0,
        drawCalls,
      };
    }
    this.guard('ui.render', () => this.ui.render(this.buildModel(now), dt));
  }

  /** Per-slot mimic poses from the latest vision frame (null when mimic is off or not camera play). */
  private updateMimic(now: number): void {
    const on = this.settings.mimic && this.inputMode === 'camera';
    for (let i = 0; i < this.mimicFilters.length; i++) {
      const f = this.mimicFilters[i];
      if (!on) {
        f.reset();
        this.mimicPoses[i] = null;
        continue;
      }
      const person = this.frame?.people.find((p) => p.slot === i);
      let pose: MimicPose | null = null;
      try {
        pose = f.update(person?.landmarks, now);
      } catch (e) {
        this.report('mimic', e);
        f.reset();
      }
      this.mimicPoses[i] = pose;
    }
  }

  /** Camera mode with a live vision system (presence / hands-up matter). */
  private cameraTracked(): boolean {
    return this.inputMode === 'camera' && this.vision !== null;
  }

  private updatePresence(now: number): void {
    const people = this.frame?.people;
    for (let i = 0; i < 2; i++) {
      const p = this.session.players[i];
      let visible = false;
      if (people) for (const q of people) if (q.slot === i) visible = true;
      p.visible = visible;
      if (visible) {
        p.seen = true;
        p.lastSeen = now;
      }
    }
  }

  private updateLobby(now: number): void {
    const people = this.frame?.people ?? [];
    if (!this.cameraTracked()) {
      this.audio.setChargeLevel(0);
      return;
    }
    const step = this.join.update(people, now, this.params.players ?? 2);
    if (step.joined.length > 0) {
      this.audio.playUi('join');
      this.lastActivity = now;
      this.vision?.lockSlots(true);
    }
    if (step.reset) this.vision?.lockSlots(false);
    this.audio.setChargeLevel(maxHandsUp(people, 2, (s) => this.join.has(s)));
    if (step.start) {
      this.audio.playUi('handsUp');
      this.beginCountdown(step.start, now);
    }
  }

  private calibrationNeeded(): boolean {
    return this.inputMode === 'camera' && !this.params.autopilot;
  }

  private updateCountdown(now: number, dt: number): void {
    const s = this.session;
    if (this.goAt !== null) {
      // "GO!" flash: the run is already live.
      this.countdownValue = 0;
      this.updatePlaying(now, dt);
      if (this.screen === 'countdown' && now - this.goAt >= APP.goFlashS) this.setScreen('playing', now);
      return;
    }
    if (this.calibPending && !this.startCalibrationIfReady()) {
      // Body controls cannot work until the model and camera are ready.
      if (!this.params.autopilot) {
        this.countdownValue = 3;
        this.countdownHolding = true;
        s.advance(dt);
        s.collect();
        return;
      }
    }
    let ready = true;
    let fallback = false;
    if (this.calibrationNeeded()) {
      ready = this.modelReady && this.vision?.status === 'running';
      for (let i = 0; i < s.count; i++) {
        if (!isCalibrated(this.frame?.gestures[i]) || !this.frame?.people.some((p) => p.slot === i)) ready = false;
      }
      // Nobody in view for a while → start anyway (keyboard arrows still steer).
      const anyone = this.frame?.people.some((p) => p.slot < s.count) ?? false;
      if (ready || anyone) this.noViewSince = null;
      else this.noViewSince ??= now;
      fallback = !ready && this.noViewSince !== null && now - this.noViewSince >= APP.noViewFallbackS;
    }
    const step = this.countdown.update(dt, ready || fallback);
    this.countdownValue = step.value;
    this.countdownHolding = step.holding;
    if (step.tick) this.audio.playUi('tick');
    // Worlds idle at the start line (animations tick).
    s.advance(dt);
    s.collect();
    if (step.go) {
      this.audio.playUi('go');
      this.note(fallback ? 'go-fallback' : 'go');
      if (fallback) {
        this.stickyToast = KEYBOARD_HINT;
        this.raiseToast(KEYBOARD_HINT);
      }
      s.start();
      this.applyLanes();
      this.queue.length = 0;
      this.goAt = now;
    }
  }

  private updatePlaying(now: number, dt: number): void {
    const s = this.session;
    s.route(this.queue);
    s.drivePilots();
    s.advance(dt);
    s.collect();
    if (this.runTutorial && tutorialCleared(true, s.snaps.map((x) => x.distance))) this.markTutorialDone();

    const camera = this.cameraTracked();
    const selfie = camera ? { delay: APP.selfieDelayS, retry: APP.selfieRetryS } : null;
    let crashed: PlayerId[] | null = null;
    let speedLevel = 0;
    for (let i = 0; i < s.count; i++) {
      const id = i as PlayerId;
      const events = s.events[i];
      for (const e of events) {
        this.audio.playGameEvent(e, id, s.count);
        if (e.type === 'jump' || e.type === 'roll' || e.type === 'crash') this.note(`game:p${id}:${e.type}`);
        else if (e.type === 'laneChange') this.note(`game:p${id}:lane${e.to}`);
        if (e.type === 'crash') {
          if (s.markCrashed(id, now, selfie)) (crashed ??= []).push(id);
        } else if (e.type === 'speedUp') {
          this.raiseToast('SPEED UP!');
        }
      }
      const snap = s.snaps[i];
      if (snap.status === 'dead' && s.players[i].alive && s.markCrashed(id, now, selfie)) (crashed ??= []).push(id);
      if (snap.speedLevel > speedLevel) speedLevel = snap.speedLevel;
    }
    this.audio.setIntensity(speedLevel);
    if (crashed) {
      const alive = s.players.map((p) => p.alive);
      const scores = s.snaps.map((x) => x.score);
      const w = decideWinner(s.count, s.winner, crashed, alive, scores);
      if (w !== null && s.winner === null) {
        s.winner = w;
        if (s.anyAlive()) {
          this.raiseToast(`${PLAYER_LABELS[w]} WINS — KEEP GOING!`);
          this.audio.playUi('win');
        }
      }
    }
    this.updateSelfies(now);

    if (s.allDeadAt !== null) {
      if (now - s.allDeadAt >= APP.gameoverDelayS) this.enterGameover(now);
      return;
    }
    if (camera && !this.params.autopilot && !this.trackingPauseOff) {
      if (missingPlayers(s.players, s.count, now, APP.trackingLostS).length > 0) this.enterPaused('tracking-lost');
    }
  }

  private updateSelfies(now: number): void {
    const v = this.vision;
    const s = this.session;
    for (let i = 0; i < s.count; i++) {
      const p = s.players[i];
      if (p.selfieAt === null || now < p.selfieAt) continue;
      const photo = v ? v.captureSelfie(i as PlayerId) : null;
      if (photo) {
        p.photo = photo;
        p.selfieAt = null;
        if (this.screen === 'gameover') this.patchPhoto(i as PlayerId, photo);
      } else if (now >= p.selfieUntil) {
        p.selfieAt = null;
      } else {
        p.selfieAt = now + APP.selfieRetryEveryS;
      }
    }
  }

  /** A selfie that arrived after the gameover screen opened → result + stored entry. */
  private patchPhoto(id: PlayerId, photo: string): void {
    const r = this.results.find((x) => x.playerId === id);
    if (!r || r.photo) return;
    if (r.entryId) this.leaderboard.setPhoto(r.entryId, photo);
    this.results = this.results.map((x) => (x === r ? { ...x, photo } : x));
  }

  private updatePaused(now: number, dt: number): void {
    const s = this.session;
    let step;
    if (this.pauseReason === 'tracking-lost') {
      step = this.resume.update(dt, invisiblePlayers(s.players, s.count).length === 0);
    } else {
      if (this.cameraTracked() && this.resume.resumeIn === null && maxHandsUp(this.frame?.people, s.count) >= 1) {
        this.resume.trigger();
      }
      step = this.resume.update(dt, null);
    }
    if (step.tick) this.audio.playUi('tick');
    if (step.resume) this.resumePlay(now);
  }

  private updateGameover(now: number, dt: number): void {
    const s = this.session;
    // Dead runners keep animating.
    s.advance(dt);
    s.collect();
    this.updateSelfies(now);
    const since = now - this.gameoverAt;
    const locked = restartLockoutLeft(since) !== null;
    const camera = this.cameraTracked();
    const hands = camera ? maxHandsUp(this.frame?.people, s.count) : 0;
    if (hands > 0) this.lastActivity = now;
    this.audio.setChargeLevel(locked ? 0 : hands);
    if (!locked) {
      if (hands >= 1 && this.restartArmed) {
        this.restart(now);
        return;
      }
      // Require hands to come down once after the lockout (no instant restart from a held pose).
      if (hands < 1) this.restartArmed = true;
      if (this.params.autopilot && since >= APP.restartLockoutS + APP.autopilotRestartS) {
        this.restart(now);
        return;
      }
    }
    let absentFor: number | null = null;
    if (camera) {
      let lastSeen = Number.NEGATIVE_INFINITY;
      let seen = false;
      for (let i = 0; i < s.count; i++) {
        if (s.players[i].seen) seen = true;
        if (s.players[i].lastSeen > lastSeen) lastSeen = s.players[i].lastSeen;
      }
      if (seen) absentFor = now - Math.max(lastSeen, this.gameoverAt);
    }
    if (!this.params.autopilot && gameoverShouldExit({ since, idleFor: now - this.lastActivity, absentFor })) {
      this.enterLobby(now);
    }
  }

  /** The run passed the end of the tutorial: remember it so later runs skip it. */
  private markTutorialDone(): void {
    this.runTutorial = false;
    setTutorialDone(this.storage, true);
    this.note('tutorial-done');
    if (this.stored.tutorial) {
      this.stored = { ...this.stored, tutorial: false };
      this.settingsStore.save(this.stored);
    }
    if (this.settings.tutorial) this.settings = { ...this.settings, tutorial: false };
  }

  /** Keep a sticky toast on screen: re-raise it when it is gone or about to fade. */
  private refreshStickyToast(): void {
    const text = this.stickyToast;
    if (!text) return;
    const t = performance.now();
    if (!this.toast || t - this.toast.t >= APP.stickyToastS * 1000) this.toast = { text, t };
  }

  private raiseToast(text: string): void {
    const t = performance.now();
    if (this.toast && this.toast.text === text && t - this.toast.t < APP.toastRepeatS * 1000) return;
    this.toast = { text, t };
  }

  // ---------------------------------------------------------------------------
  // UIModel
  // ---------------------------------------------------------------------------

  private buildModel(now: number): UIModel {
    const s = this.session;
    const f = this.frame;
    const screen = this.screen;
    const inRun = s.active && screen !== 'boot' && screen !== 'lobby';
    const visionStatus = this.vision ? this.vision.status : 'idle';

    let progress = 1;
    let message = 'Press Enter or tap to start';
    if (this.vision) {
      progress = bootProgress(visionStatus, now - this.loadingSince);
      message = this.bootPending && this.modelReady ? 'Starting camera…' : (BOOT_MESSAGES[visionStatus] ?? '');
    }

    const people: UIModel['lobby']['people'] = [];
    if (screen === 'lobby' && f) {
      for (const p of f.people) {
        people.push({ slot: p.slot, handsUpProgress: p.handsUpProgress, joined: this.join.has(p.slot), hints: p.hints });
      }
    }

    const calibration: number[] = [];
    if (screen === 'countdown') {
      const need = this.calibrationNeeded();
      for (let i = 0; i < s.count; i++) calibration.push(need ? calibrationOf(f?.gestures[i]) : 1);
    }

    let paused: UIModel['paused'] = null;
    if (screen === 'paused') {
      paused = {
        reason: this.pauseReason,
        resumeIn: this.resume.resumeIn,
        missing: this.pauseReason === 'tracking-lost' ? invisiblePlayers(s.players, s.count) : [],
      };
    }

    let gameOver: UIModel['gameOver'] = null;
    if (screen === 'gameover') {
      const lockout = restartLockoutLeft(now - this.gameoverAt);
      gameOver = {
        results: this.results,
        restartLockout: lockout,
        handsUpProgress: lockout === null && this.cameraTracked() ? maxHandsUp(f?.people, s.count) : 0,
      };
    }

    return {
      screen,
      playerCount: inRun ? s.count : 1,
      inputMode: this.cameraTracked() ? 'camera' : 'keyboard',
      boot: {
        progress,
        message,
        error: this.bootError ?? (this.vision?.status === 'error' && !this.bootPending ? this.vision.error : null),
        // Camera mode: TAP TO START only once the model is ready (progress bar meanwhile).
        needsClick: !this.clicked && !this.bootError && (this.vision === null || this.modelReady),
      },
      lobby: {
        people,
        joinCountdown: screen === 'lobby' ? this.join.secondsLeft(now) : null,
        spectators: f?.extraPeople ?? 0,
      },
      countdown: {
        value: this.countdownValue,
        calibration,
        message:
          screen !== 'countdown' || !this.countdownHolding
            ? null
            : this.vision?.status === 'error'
              ? this.vision.error ?? 'Body tracking stopped. Try another camera in Settings.'
              : this.calibPending
                ? 'Warming up the camera…'
                : !f || Array.from({ length: s.count }, (_, i) => i).some((i) => !f.people.some((p) => p.slot === i))
                  ? 'Step into view — keep your head and shoulders in frame'
                  : 'Stand still with your hands down — calibrating…',
      },
      paused,
      hud: inRun ? s.snaps : [],
      out: inRun ? (s.out as PlayerId[]) : [],
      winner: inRun ? s.winner : null,
      gameOver,
      leaderboard: this.leaderboard.entries,
      vision: { status: visionStatus, frame: f, video: this.vision ? this.vision.getVideo() : null },
      settings: this.settings,
      settingsOpen: this.settingsOpen,
      cameras: this.cameras,
      debug: this.debugOn ? this.debug : null,
      toast: this.toast,
      door: this.params.door ? { notice: DOOR_NOTICE } : null,
    };
  }

  private debugState(): AppDebugState {
    const s = this.session;
    const inRun = s.active && this.screen !== 'boot' && this.screen !== 'lobby';
    const snaps: readonly WorldSnapshot[] = inRun ? s.snaps : [];
    return {
      screen: this.screen,
      playerCount: inRun ? s.count : 1,
      worlds: snaps.map((w) => ({
        status: w.status,
        distance: w.distance,
        score: w.score,
        coins: w.coins,
        lane: w.player.lane,
        y: w.player.y,
      })),
      visionStatus: this.vision ? this.vision.status : 'idle',
      mimic: this.mimicPoses.map((m) =>
        m
          ? {
              leftUpper: r3(m.leftUpper),
              leftLower: r3(m.leftLower),
              rightUpper: r3(m.rightUpper),
              rightLower: r3(m.rightLower),
              lean: r3(m.lean),
              weight: r3(m.weight),
            }
          : null,
      ),
    };
  }

  /** For tests / console. */
  get currentScreen(): Screen {
    return this.screen;
  }

  get screenTime(): number {
    return performance.now() / 1000 - this.screenAt;
  }

  playUi(s: UISound): void {
    this.audio.playUi(s);
  }
}

function hasWebGL2(): boolean {
  try {
    return !!document.createElement('canvas').getContext('webgl2');
  } catch {
    return false;
  }
}
