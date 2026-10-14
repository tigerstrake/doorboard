import { describe, expect, it, vi } from 'vitest';
import type { ActionEvent, MenuKey, PlayerCount, PlayerId, Screen, Settings, UICommand, VisionFrame, VisionStatus } from '../shared/types';
import { DEFAULT_SETTINGS } from '../shared/constants';
import { parseParams } from '../shared/params';
import { AutoPilot, World } from '../game';
import { VisionCore } from '../vision/VisionCore';
import { makeBody, SYN_YSCALE } from '../vision/synthetic';
import { App } from './App';
import { Session } from './Session';
import { Countdown, ResumeGate } from './rules';
import { FrameP90 } from './loop';

// Exercise the real app transitions, countdown, vision core and worlds without creating
// browser renderers, opening a webcam or starting a requestAnimationFrame loop.
vi.mock('../render', () => ({ Renderer: class {} }));
vi.mock('../ui', () => ({ UIManager: class {} }));

function harness() {
  const vision = {
    status: 'running' as VisionStatus,
    error: null as string | null,
    lockSlots: vi.fn(),
    setPlayerCount: vi.fn(),
    resetCalibration: vi.fn(),
    startCalibration: vi.fn(),
    startCamera: vi.fn(async () => {}),
    loadModel: vi.fn(async () => {}),
    listCameras: vi.fn(async () => []),
    dispose: vi.fn(),
  };
  const state = {
    params: parseParams('?autostart=1&seed=123'),
    settings: { ...DEFAULT_SETTINGS },
    inputMode: 'camera' as 'camera' | 'keyboard',
    screen: 'boot' as Screen,
    screenAt: 0,
    session: new Session((id) => new World(id), () => new AutoPilot()),
    vision: vision as typeof vision | null,
    modelReady: true,
    modelLoad: null as Promise<void> | null,
    activeModel: 'lite' as Settings['model'] | null,
    stored: {} as Partial<Settings>,
    settingsStore: { save: vi.fn() },
    loadingSince: 0,
    frame: null as VisionFrame | null,
    bootError: null as string | null,
    bootPending: false,
    calibPending: false,
    countdown: new Countdown(),
    countdownValue: 3,
    countdownHolding: false,
    goAt: null as number | null,
    queue: [] as ActionEvent[],
    log: [],
    report: vi.fn(),
    toast: null as { text: string; t: number } | null,
    stickyToast: null as string | null,
    trackingPauseOff: false,
    graphics: 'ok' as 'ok' | 'broken' | 'lost',
    pauseReason: 'user' as 'user' | 'tracking-lost',
    resume: new ResumeGate(),
    gameoverAt: 0,
    frameP90: new FrameP90(),
    slowSince: null as number | null,
    qualityLowered: false,
    booth: { idle: vi.fn() },
    renderer: { setPlayerCount: vi.fn(), setQuality: vi.fn() },
    keyboard: { setPlayerCount: vi.fn() },
    audio: {
      unlock: vi.fn(async () => {}),
      setMusic: vi.fn(),
      setChargeLevel: vi.fn(),
      setIntensity: vi.fn(),
      clearPowerUps: vi.fn(),
      setPaused: vi.fn(),
      playUi: vi.fn(),
    },
  };
  const app = Object.assign(Object.create(App.prototype), state) as typeof state & {
    beginCountdown(slots: PlayerId[], now: number): void;
    updateCountdown(now: number, dt: number): void;
    updatePresence(now: number): void;
    startCamera(fromBoot: boolean): Promise<void>;
    onCommand(command: UICommand): void;
    onMenuKey(k: MenuKey): void;
    onAction(e: ActionEvent): void;
    onCameraLost(): void;
    adaptQuality(now: number, deltaMs: number): void;
    refreshStickyToast(): void;
    loadModel(): void;
    enterPaused(reason: 'user' | 'tracking-lost'): void;
  };
  return { app, vision };
}

function trackedFrame(count: PlayerCount, calibrated: boolean): VisionFrame {
  const core = new VisionCore();
  core.setPlayerCount(count);
  if (calibrated) for (let slot = 0; slot < count; slot++) core.startCalibration(slot as PlayerId, 0);
  let frame!: VisionFrame;
  for (let t = 33; t < 2000; t += 33) {
    const bodies = Array.from({ length: count }, (_, slot) => makeBody({
      rawX: count === 1 ? 0.5 : slot === 0 ? 0.7 : 0.3,
      noise: 0,
    }));
    frame = core.process(bodies, t, SYN_YSCALE);
  }
  return frame;
}

function countdownFor(app: ReturnType<typeof harness>['app'], seconds: number, from = 0): void {
  for (let i = 1; i <= seconds * 10 && app.goAt === null && app.screen === 'countdown'; i++) {
    const now = from + i / 10;
    app.updatePresence(now);
    app.updateCountdown(now, 0.1);
  }
}

function statuses(app: ReturnType<typeof harness>['app']): string[] {
  return app.session.worlds.slice(0, app.session.count).map((world) => world.snapshot().status);
}

describe('App camera controls', () => {
  it('calibrates both players when starting a two-player session from a solo session', () => {
    const { app, vision } = harness();
    app.beginCountdown([0, 1], 0);
    expect(app.session.count).toBe(2);
    expect(vision.startCalibration.mock.calls).toEqual([[0], [1]]);
    expect(statuses(app)).toEqual(['ready', 'ready']);
  });

  it('does not start running after a long camera/model warm-up timeout', () => {
    const { app, vision } = harness();
    vision.status = 'loading-model';
    app.modelReady = false;
    app.beginCountdown([0], 0);
    countdownFor(app, 60);
    expect(app.goAt).toBeNull();
    expect(statuses(app)).toEqual(['ready']);
    expect(app.calibPending).toBe(true);

    vision.status = 'running';
    app.modelReady = true;
    app.frame = trackedFrame(1, true);
    countdownFor(app, 4, 60);
    expect(vision.startCalibration).toHaveBeenCalledWith(0);
    expect(app.goAt).not.toBeNull();
    expect(statuses(app)).toEqual(['running']);
  });

  it('holds for uncalibrated body tracking and starts once the visible player is calibrated', () => {
    const { app } = harness();
    app.beginCountdown([0], 0);
    app.frame = trackedFrame(1, false);
    countdownFor(app, 20);
    expect(app.goAt).toBeNull();
    expect(statuses(app)).toEqual(['ready']);

    app.frame = trackedFrame(1, true);
    countdownFor(app, 4, 20);
    expect(app.goAt).not.toBeNull();
    expect(statuses(app)).toEqual(['running']);
  });

  it('does not start with a remembered calibration after the player leaves the frame', () => {
    const { app } = harness();
    app.beginCountdown([0], 0);
    app.frame = { ...trackedFrame(1, true), people: [] };
    countdownFor(app, 7);
    expect(app.goAt).toBeNull();
    expect(statuses(app)).toEqual(['ready']);
  });

  it('starts anyway with a keyboard note after ~8 s with nobody in view', () => {
    const { app } = harness();
    app.beginCountdown([0], 0);
    app.frame = { ...trackedFrame(1, false), people: [] };
    countdownFor(app, 7.5);
    expect(app.goAt).toBeNull();
    expect(app.countdownHolding).toBe(true);
    countdownFor(app, 2, 7.5);
    expect(app.goAt).not.toBeNull();
    expect(statuses(app)).toEqual(['running']);
    expect((app as unknown as { toast: { text: string } | null }).toast?.text).toMatch(/arrows to move/);
  });

  it('keeps waiting (no fallback) while a visible player is still calibrating', () => {
    const { app } = harness();
    app.beginCountdown([0], 0);
    app.frame = trackedFrame(1, false);
    countdownFor(app, 30);
    expect(app.goAt).toBeNull();
  });

  it('the lobby PLAY button always starts a countdown', () => {
    const { app } = harness();
    app.screen = 'lobby';
    app.onCommand({ type: 'start', players: 1 });
    expect(app.screen).toBe('countdown');
    expect(app.session.count).toBe(1);
  });

  it('keeps camera mode and shows the camera error when autostart camera access fails', async () => {
    const { app, vision } = harness();
    app.beginCountdown([0], 0);
    vision.startCamera.mockRejectedValueOnce(new Error('Camera permission denied'));
    await app.startCamera(false);
    expect(app.inputMode).toBe('camera');
    expect(app.screen).toBe('boot');
    expect(app.bootError).toBeTruthy();
    expect(vision.dispose).not.toHaveBeenCalled();
    expect(statuses(app)).toEqual(['ready']);
  });

  it('keeps camera mode and shows the error when autostart model loading fails', async () => {
    const { app, vision } = harness();
    app.beginCountdown([0], 0);
    app.modelLoad = Promise.reject(new Error('Pose model unavailable'));
    await app.startCamera(false);
    expect(app.inputMode).toBe('camera');
    expect(app.screen).toBe('boot');
    expect(app.bootError).toBeTruthy();
    expect(vision.startCamera).not.toHaveBeenCalled();
    expect(vision.dispose).not.toHaveBeenCalled();
    expect(statuses(app)).toEqual(['ready']);
  });

  it('allows an explicit keyboard fallback to restart the countdown with the same player count', async () => {
    const { app, vision } = harness();
    app.beginCountdown([0, 1], 0);
    countdownFor(app, 6);
    expect(app.goAt).toBeNull();
    app.onCommand({ type: 'keyboard-only' });
    expect(app.inputMode).toBe('keyboard');
    expect(app.session.count).toBe(2);
    expect(app.countdownValue).toBe(3);
    expect(vision.dispose).toHaveBeenCalledOnce();
    countdownFor(app, 4, 6);
    expect(app.goAt).not.toBeNull();
    expect(statuses(app)).toEqual(['running', 'running']);
    await Promise.resolve();
  });
});

describe('App review fixes', () => {
  const now = () => performance.now() / 1000;

  it('gameover PLAY / restart buttons respect the restart lockout', () => {
    const { app } = harness();
    app.beginCountdown([0], 0);
    app.screen = 'gameover';
    app.gameoverAt = now();
    app.onCommand({ type: 'start', players: 1 });
    app.onCommand({ type: 'restart' });
    expect(app.screen).toBe('gameover');
    app.gameoverAt = now() - 10;
    app.onCommand({ type: 'restart' });
    expect(app.screen).toBe('countdown');
  });

  it('a tracking-lost pause resumes on a key press and stops tracking pauses for the run', () => {
    const { app } = harness();
    app.beginCountdown([0], 0);
    app.screen = 'paused';
    app.pauseReason = 'tracking-lost';
    app.onMenuKey('confirm');
    expect(app.pauseReason).toBe('user');
    expect(app.trackingPauseOff).toBe(true);
    expect(app.resume.resumeIn).not.toBeNull();
  });

  it('camera lost mid-run pauses and switches to keyboard with a sticky message', () => {
    const { app, vision } = harness();
    app.beginCountdown([0], 0);
    app.screen = 'playing';
    vision.status = 'error';
    vision.error = 'Camera disconnected';
    app.onCameraLost();
    expect(app.screen).toBe('paused');
    expect(app.inputMode).toBe('keyboard');
    expect(app.vision).toBeNull();
    app.refreshStickyToast();
    expect(app.toast?.text).toMatch(/keyboard/i);
  });

  it('the no-view keyboard hint stays until the first input', () => {
    const { app } = harness();
    app.screen = 'playing';
    app.stickyToast = 'Playing with keyboard: arrows to move, space to jump';
    app.toast = { text: 'SPEED UP!', t: performance.now() - 5000 };
    app.refreshStickyToast();
    expect(app.toast?.text).toMatch(/arrows to move/);
    app.onAction({ source: 'keyboard', playerId: 0, action: { type: 'jump' }, t: 0 } as ActionEvent);
    expect(app.stickyToast).toBeNull();
  });

  it('lowers graphics after 2 s of slow frames (1P), at once in 2P, and never raises them', () => {
    const { app } = harness();
    app.beginCountdown([0], 0);
    app.screen = 'playing';
    let t = 0;
    for (let i = 0; i < 60; i++) app.adaptQuality((t += 0.03), 30);
    expect(app.renderer.setQuality).not.toHaveBeenCalled();
    for (let i = 0; i < 70; i++) app.adaptQuality((t += 0.03), 30);
    expect(app.renderer.setQuality).toHaveBeenCalledWith('low');
    expect(app.toast?.text).toMatch(/Lowered graphics/);
    expect(app.settings.quality).toBe(DEFAULT_SETTINGS.quality);
    for (let i = 0; i < 200; i++) app.adaptQuality((t += 0.016), 16);
    expect(app.renderer.setQuality).toHaveBeenCalledTimes(1);

    const two = harness().app;
    two.beginCountdown([0, 1], 0);
    two.screen = 'playing';
    for (let i = 0; i < 60; i++) two.adaptQuality(i * 0.03, 30);
    expect(two.renderer.setQuality).toHaveBeenCalledWith('low');
  });
});

describe('App run resets', () => {
  it('a new countdown drops power-up audio left from the last run; pause holds it', () => {
    const { app } = harness();
    app.beginCountdown([0], 0);
    expect(app.audio.clearPowerUps).toHaveBeenCalledTimes(1);
    expect(app.audio.setPaused).toHaveBeenLastCalledWith(false);
    app.screen = 'playing';
    app.enterPaused('user');
    expect(app.audio.setPaused).toHaveBeenLastCalledWith(true);
  });

  it('a failed model switch keeps the old model ready and reverts the setting', async () => {
    const { app, vision } = harness();
    app.settings = { ...app.settings, model: 'full' };
    const kept = Object.assign(new Error('download failed'), { keptPrevious: true });
    vision.loadModel.mockRejectedValueOnce(kept);
    app.screen = 'lobby';
    app.loadModel();
    expect(app.modelReady).toBe(false);
    await app.modelLoad?.catch(() => undefined);
    await Promise.resolve();
    expect(app.modelReady).toBe(true);
    expect(app.settings.model).toBe('lite');
    expect(app.screen).toBe('lobby');
    expect(app.toast?.text).toMatch(/still using lite/);
    expect(app.report).not.toHaveBeenCalled();
  });
});
