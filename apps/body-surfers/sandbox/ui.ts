// UI sandbox: renders the real UIManager against fake UIModels for every screen.
// URL params: ?screen=boot|lobby|countdown|playing|paused|gameover &players=1|2
//   &kb=1 (keyboard input mode) &settings=1 &mimic=0 &debug=1 &error=1|<raw message> (boot error) &novideo=1
//   &only=1 (game over: you are the only leaderboard entry, rank 1) &rank=N (1P game-over rank)
//   &reason=tracking-lost &resume=1 (paused) &out=1 (2P playing: P2 crashed) &empty=1 (no scores)
//   &hint=jump|duck|left|right|switch &mirror=0 &bar=0 (hide the switcher)
import { UIManager } from '../src/ui';
import type {
  GestureState,
  Landmark,
  LeaderboardEntry,
  PlayerCount,
  PlayerId,
  PlayerResult,
  Screen,
  Settings,
  TrackedPerson,
  TutorialHint,
  UICommand,
  UIModel,
  VisionFrame,
  WorldSnapshot,
} from '../src/shared/types';
import { DEFAULT_SETTINGS } from '../src/shared/constants';
import { LM } from '../src/shared/pose';

const q = new URLSearchParams(location.search);
const flag = (k: string): boolean => q.has(k) && q.get(k) !== '0' && q.get(k) !== 'false';
const SCREENS: Screen[] = ['boot', 'lobby', 'countdown', 'playing', 'paused', 'gameover'];
let screen: Screen = (SCREENS as string[]).includes(q.get('screen') ?? '') ? (q.get('screen') as Screen) : 'lobby';
let players: PlayerCount = q.get('players') === '2' ? 2 : 1;
const keyboard = flag('kb');
const noVideo = flag('novideo');
const forcedHint = (q.get('hint') as TutorialHint | null) ?? null;

// ---------------------------------------------------------------------------
// Fake background (sunny train yard-ish) so the UI is judged against a bright scene.
// ---------------------------------------------------------------------------
const bg = document.getElementById('bg') as HTMLCanvasElement;
function drawBg(t: number): void {
  if (bg.width !== innerWidth) bg.width = innerWidth;
  if (bg.height !== innerHeight) bg.height = innerHeight;
  const w = bg.width;
  const h = bg.height;
  const c = bg.getContext('2d');
  if (!c) return;
  const views = screen === 'lobby' || screen === 'boot' ? 1 : players;
  for (let v = 0; v < views; v++) {
    const x0 = (w / views) * v;
    const vw = w / views;
    const sky = c.createLinearGradient(0, 0, 0, h * 0.5);
    sky.addColorStop(0, '#5ec8ff');
    sky.addColorStop(1, '#bfe9ff');
    c.fillStyle = sky;
    c.fillRect(x0, 0, vw, h * 0.45);
    c.fillStyle = '#c98b5a';
    c.fillRect(x0, h * 0.45, vw, h * 0.55);
    const cx = x0 + vw / 2;
    c.fillStyle = '#8a5a3a';
    for (let i = -1; i <= 1; i++) {
      c.beginPath();
      c.moveTo(cx + i * vw * 0.04 - 6, h * 0.45);
      c.lineTo(cx + i * vw * 0.04 + 6, h * 0.45);
      c.lineTo(cx + i * vw * 0.33 + 40, h);
      c.lineTo(cx + i * vw * 0.33 - 40, h);
      c.fill();
    }
    c.fillStyle = '#e94b3c';
    c.fillRect(cx - vw * 0.36, h * 0.38, vw * 0.12, h * 0.3);
    c.fillStyle = '#2b7bd6';
    c.fillRect(cx + vw * 0.22, h * 0.36, vw * 0.14, h * 0.34);
    c.fillStyle = '#ff7a1a';
    const bob = Math.abs(Math.sin(t / 180)) * 10;
    c.fillRect(cx - 18, h * 0.78 - bob, 36, 60);
    c.beginPath();
    c.arc(cx, h * 0.76 - bob, 16, 0, Math.PI * 2);
    c.fillStyle = '#ffdcb5';
    c.fill();
  }
}

// ---------------------------------------------------------------------------
// Fake people (display space) + a fake camera feed drawn in raw (unmirrored) space.
// ---------------------------------------------------------------------------
const ASPECT = 4 / 3;
function makeLandmarks(cx: number, cy: number, s: number, handsUp: number, squat: number): Landmark[] {
  const sy = s * ASPECT;
  const lm: Landmark[] = [];
  for (let i = 0; i < 33; i++) lm.push({ x: cx, y: cy, z: 0, visibility: 0.95 });
  const set = (i: number, dx: number, dy: number): void => {
    lm[i].x = cx + dx * s;
    lm[i].y = cy + dy * sy;
  };
  const sq = squat * 0.9;
  set(LM.NOSE, 0, -0.8 + sq);
  for (const i of [1, 2, 3]) set(i, 0.12, -0.9 + sq);
  for (const i of [4, 5, 6]) set(i, -0.12, -0.9 + sq);
  set(LM.LEFT_EAR, 0.25, -0.85 + sq);
  set(LM.RIGHT_EAR, -0.25, -0.85 + sq);
  set(LM.MOUTH_LEFT, 0.08, -0.65 + sq);
  set(LM.MOUTH_RIGHT, -0.08, -0.65 + sq);
  set(LM.LEFT_SHOULDER, 0.5, sq);
  set(LM.RIGHT_SHOULDER, -0.5, sq);
  const ex = 0.7;
  const eUp = -0.5 * handsUp + 0.65 * (1 - handsUp);
  const wUp = -1.45 * handsUp + 1.25 * (1 - handsUp);
  set(LM.LEFT_ELBOW, ex, eUp + sq);
  set(LM.RIGHT_ELBOW, -ex, eUp + sq);
  set(LM.LEFT_WRIST, 0.62 + 0.15 * (1 - handsUp), wUp + sq);
  set(LM.RIGHT_WRIST, -0.62 - 0.15 * (1 - handsUp), wUp + sq);
  for (const [i, j] of [[17, 15], [19, 15], [21, 15], [18, 16], [20, 16], [22, 16]] as const) {
    lm[i].x = lm[j].x;
    lm[i].y = lm[j].y - 0.02;
  }
  set(LM.LEFT_HIP, 0.3, 1.5 + sq * 0.6);
  set(LM.RIGHT_HIP, -0.3, 1.5 + sq * 0.6);
  set(LM.LEFT_KNEE, 0.38 + squat * 0.3, 2.4);
  set(LM.RIGHT_KNEE, -0.38 - squat * 0.3, 2.4);
  set(LM.LEFT_ANKLE, 0.35, 3.25);
  set(LM.RIGHT_ANKLE, -0.35, 3.25);
  set(LM.LEFT_HEEL, 0.33, 3.32);
  set(LM.RIGHT_HEEL, -0.33, 3.32);
  set(LM.LEFT_FOOT_INDEX, 0.48, 3.35);
  set(LM.RIGHT_FOOT_INDEX, -0.48, 3.35);
  return lm;
}

interface FakePerson {
  slot: PlayerId;
  cx: number;
  cy: number;
  s: number;
  handsUp: number;
  squat: number;
  progress: number;
  hints: TrackedPerson['hints'];
}

function people(t: number): FakePerson[] {
  const out: FakePerson[] = [];
  const n = screen === 'lobby' ? players : players;
  for (let i = 0; i < n; i++) {
    const base = n === 1 ? 0.5 : i === 0 ? 0.3 : 0.7;
    let cx = base;
    let handsUp = 0;
    let squat = 0;
    let progress = 0;
    if (screen === 'lobby' || screen === 'gameover') {
      progress = (((t / 2600 + i * 0.35) % 1.3) / 1.0);
      progress = Math.min(1, progress);
      handsUp = progress > 0.02 ? 1 : 0;
      if (screen === 'lobby' && players === 2 && i === 0) {
        progress = 1;
        handsUp = 0;
      }
    } else if (screen === 'playing') {
      cx = base + Math.sin(t / 900 + i) * (n === 1 ? 0.14 : 0.08);
      squat = Math.max(0, Math.sin(t / 500 + i * 2)) * 0.25;
    } else {
      cx = base + Math.sin(t / 1500) * 0.01;
    }
    const s = n === 1 ? 0.13 : 0.1;
    out.push({ slot: i as PlayerId, cx, cy: 0.38 - squat * 0.0, s, handsUp, squat, progress, hints: i === 1 && progress < 0.05 && screen === 'lobby' ? ['too-far'] : [] });
  }
  return out;
}

const fakeCanvas = document.createElement('canvas');
fakeCanvas.width = 640;
fakeCanvas.height = 480;
const fakeCtx = fakeCanvas.getContext('2d');
let video: HTMLVideoElement | null = null;
if (!noVideo && !keyboard && typeof fakeCanvas.captureStream === 'function') {
  const v = document.createElement('video');
  v.muted = true;
  v.playsInline = true;
  v.srcObject = fakeCanvas.captureStream(30);
  void v.play().catch(() => undefined);
  video = v;
}

function drawFakeVideo(ps: FakePerson[], mirror: boolean): void {
  const c = fakeCtx;
  if (!c) return;
  const W = fakeCanvas.width;
  const H = fakeCanvas.height;
  c.fillStyle = '#c9b9a0';
  c.fillRect(0, 0, W, H);
  c.fillStyle = '#9c8a74';
  c.fillRect(0, H * 0.72, W, H * 0.28);
  c.fillStyle = '#e8dcc8';
  c.fillRect(W * 0.08, H * 0.1, W * 0.22, H * 0.3); // "window" (raw left)
  c.fillStyle = '#6a7a8a';
  c.fillRect(W * 0.75, H * 0.2, W * 0.12, H * 0.5); // "door" (raw right)
  for (const p of ps) {
    const lm = makeLandmarks(p.cx, p.cy, p.s, p.handsUp, p.squat);
    const X = (l: Landmark): number => (mirror ? 1 - l.x : l.x) * W;
    const Y = (l: Landmark): number => l.y * H;
    c.strokeStyle = p.slot === 0 ? '#3a5a8a' : '#7a3a5a';
    c.lineCap = 'round';
    c.lineWidth = p.s * W * 0.45;
    c.beginPath();
    const bone = (a: number, b: number): void => {
      c.moveTo(X(lm[a]), Y(lm[a]));
      c.lineTo(X(lm[b]), Y(lm[b]));
    };
    bone(LM.LEFT_SHOULDER, LM.LEFT_ELBOW);
    bone(LM.LEFT_ELBOW, LM.LEFT_WRIST);
    bone(LM.RIGHT_SHOULDER, LM.RIGHT_ELBOW);
    bone(LM.RIGHT_ELBOW, LM.RIGHT_WRIST);
    bone(LM.LEFT_HIP, LM.LEFT_KNEE);
    bone(LM.LEFT_KNEE, LM.LEFT_ANKLE);
    bone(LM.RIGHT_HIP, LM.RIGHT_KNEE);
    bone(LM.RIGHT_KNEE, LM.RIGHT_ANKLE);
    c.stroke();
    c.fillStyle = c.strokeStyle;
    c.beginPath();
    c.moveTo(X(lm[LM.LEFT_SHOULDER]), Y(lm[LM.LEFT_SHOULDER]));
    c.lineTo(X(lm[LM.RIGHT_SHOULDER]), Y(lm[LM.RIGHT_SHOULDER]));
    c.lineTo(X(lm[LM.RIGHT_HIP]), Y(lm[LM.RIGHT_HIP]));
    c.lineTo(X(lm[LM.LEFT_HIP]), Y(lm[LM.LEFT_HIP]));
    c.fill();
    c.fillStyle = '#e8b98f';
    c.beginPath();
    c.arc(X(lm[LM.NOSE]), Y(lm[LM.NOSE]), p.s * W * 0.38, 0, Math.PI * 2);
    c.fill();
  }
}

function makeGesture(p: FakePerson, t: number): GestureState {
  const center = p.slot === 0 ? (players === 1 ? 0.5 : 0.3) : 0.7;
  const lhw = p.s * 0.9;
  const lane = p.cx < center - lhw ? -1 : p.cx > center + lhw ? 1 : 0;
  const baseline = p.cy + 0.3 * p.s * ASPECT;
  const phase = screen === 'countdown' ? 'calibrating' : screen === 'lobby' ? 'uncalibrated' : 'ready';
  const cycle = Math.floor(t / 1300);
  const kinds = ['jump', 'lane', 'duck'] as const;
  return {
    slot: p.slot,
    phase,
    calibrationProgress: screen === 'countdown' ? Math.min(1, (t % 4000) / 3000) : 1,
    lane,
    centerX: center,
    laneHalfWidth: lhw,
    baselineY: baseline,
    jumpLineY: baseline - 0.3 * p.s * ASPECT,
    duckLineY: baseline + 0.45 * p.s * ASPECT,
    lastGesture: screen === 'playing' ? { kind: kinds[(cycle + p.slot) % 3], t: cycle * 1300 + timeOrigin } : null,
  };
}
const timeOrigin = performance.now();

function visionFrame(t: number, ps: FakePerson[]): VisionFrame {
  const tracked: TrackedPerson[] = ps.map((p) => ({
    slot: p.slot,
    landmarks: makeLandmarks(p.cx, p.cy, p.s, p.handsUp, p.squat),
    center: { x: p.cx, y: p.cy + 0.3 * p.s * ASPECT },
    scale: p.s,
    handsUp: p.handsUp > 0.5,
    handsUpProgress: p.progress,
    hints: p.hints,
    confidence: 0.9,
  }));
  const g: [GestureState | null, GestureState | null] = [null, null];
  if (screen !== 'lobby') for (const p of ps) g[p.slot] = makeGesture(p, t);
  return { t: performance.now(), people: tracked, extraPeople: screen === 'lobby' ? 1 : 0, gestures: g, inferenceMs: 14.2, fps: 29 };
}

// ---------------------------------------------------------------------------
// Fake game data
// ---------------------------------------------------------------------------
function selfie(hue: number, label: string): string {
  const c = document.createElement('canvas');
  c.width = c.height = 160;
  const x = c.getContext('2d');
  if (!x) return '';
  const g = x.createLinearGradient(0, 0, 0, 160);
  g.addColorStop(0, `hsl(${hue} 60% 70%)`);
  g.addColorStop(1, `hsl(${hue} 50% 40%)`);
  x.fillStyle = g;
  x.fillRect(0, 0, 160, 160);
  x.fillStyle = '#f0c49a';
  x.beginPath();
  x.ellipse(80, 78, 42, 52, 0, 0, Math.PI * 2);
  x.fill();
  x.fillStyle = '#3a2a1a';
  x.beginPath();
  x.ellipse(80, 38, 46, 22, 0, Math.PI, 0);
  x.fill();
  x.fillStyle = '#1b1b2f';
  x.beginPath();
  x.arc(64, 74, 6, 0, Math.PI * 2);
  x.arc(96, 74, 6, 0, Math.PI * 2);
  x.fill();
  x.beginPath();
  x.ellipse(80, 108, 12, 15, 0, 0, Math.PI * 2);
  x.fillStyle = '#7a1f2b';
  x.fill();
  x.fillStyle = '#fff';
  x.font = 'bold 14px sans-serif';
  x.fillText(label, 8, 152);
  return c.toDataURL('image/jpeg', 0.8);
}

const NAMES = ['Turbo Pickle', 'Captain Noodle', 'Sir Hops-a-Lot', 'Disco Llama', 'Mega Waffle', 'Lil Zoomer', 'Rocket Bean', 'Funky Tofu', 'Sneaky Taco', 'Nacho Ninja'];
const leaderboard: LeaderboardEntry[] = flag('empty')
  ? []
  : (flag('only') ? NAMES.slice(0, 1) : NAMES).map((name, i) => ({
      id: flag('only') ? 'e2' : `e${i}`,
      name,
      score: Math.round(48000 / (i + 1) + 300),
      coins: 300 - i * 20,
      distance: 3000 - i * 200,
      date: Date.now() - i * 60000,
      photo: i % 3 === 1 ? null : selfie(i * 37, name.split(' ')[0]),
      mode: i % 4 === 2 ? 2 : 1,
    }));

function snapshot(id: PlayerId, t: number): WorldSnapshot {
  const time = t / 1000;
  const hints: TutorialHint[] = ['jump', 'duck', 'left', 'right', 'switch'];
  const hint = forcedHint ?? hints[Math.floor(time / 2.5) % hints.length];
  const playing = screen === 'playing' || screen === 'paused';
  return {
    playerId: id,
    status: screen === 'countdown' ? 'ready' : 'running',
    seed: 1,
    time,
    distance: playing ? 420 + time * 12 : 0,
    speed: 12,
    speedLevel: 0.3,
    score: playing ? 1234 + Math.floor(time * 37) + id * 400 : 0,
    coins: playing ? 57 + Math.floor(time * 2) - id * 9 : 0,
    multiplier: id === 0 && playing ? 2 : 1,
    player: { lane: 0, x: 0, y: 0, vy: 0, grounded: true, onRoof: false, anim: 'run', animTime: 0, rolling: false, lean: 0, blockedDir: 0, hoverboard: false, invulnerable: 0, alive: true },
    obstacles: [],
    collectibles: [],
    powerUps: playing
      ? id === 0
        ? [
            { kind: 'magnet', remaining: 10 - (time % 10), duration: 10 },
            { kind: 'multiplier', remaining: 5 - (time % 5), duration: 5 },
            { kind: 'hoverboard', remaining: 30, duration: 30 },
            // New kinds cycle in/out so the center pop + edge tints are visible.
            ...(time % 12 < 6
              ? [{ kind: 'slowmo' as const, remaining: 6 - (time % 12), duration: 6 }, { kind: 'jetpack' as const, remaining: 6 - (time % 12), duration: 6 }]
              : [{ kind: 'star' as const, remaining: 12 - (time % 12), duration: 6 }]),
          ]
        : [
            { kind: 'sneakers', remaining: 8 - (time % 8), duration: 8 },
            ...(time % 10 < 5 ? [{ kind: 'coinRain' as const, remaining: 5 - (time % 10), duration: 5 }] : []),
          ]
      : [],
    hint: playing ? hint : null,
  };
}

let settings: Settings = { ...DEFAULT_SETTINGS, mimic: q.get('mimic') !== '0', vision: { ...DEFAULT_SETTINGS.vision, mirror: q.get('mirror') !== '0' } };
let settingsOpen = flag('settings');
let names: [string, string] = ['Turbo Pickle', 'Disco Llama'];

function results(): PlayerResult[] {
  const r: PlayerResult[] = [
    { playerId: 0, name: names[0], score: 16300, coins: 212, distance: 1840, time: 94, photo: selfie(200, 'P1'), rank: players === 2 ? 1 : flag('only') ? 1 : q.has('rank') ? Number(q.get('rank')) || null : 3, entryId: 'e2' },
  ];
  if (players === 2) r.push({ playerId: 1, name: names[1], score: 9800, coins: 133, distance: 1102, time: 61, photo: null, rank: 5, entryId: 'e4' });
  return r;
}

function model(t: number): UIModel {
  const ps = keyboard ? [] : people(t);
  if (video) drawFakeVideo(ps, settings.vision.mirror);
  const n = players;
  const hud: WorldSnapshot[] = [];
  if (screen === 'countdown' || screen === 'playing' || screen === 'paused') for (let i = 0; i < n; i++) hud.push(snapshot(i as PlayerId, t));
  const bootP = Math.min(1, (t % 6000) / 3000);
  const lockout = Math.max(0, 2.5 - (t % 8000) / 1000);
  const out: PlayerId[] = n === 2 && flag('out') ? [1] : [];
  const lb = leaderboard.map((e) => (e.id === 'e2' ? { ...e, name: names[0] } : e.id === 'e4' ? { ...e, name: names[1] } : e));
  return {
    screen,
    playerCount: n,
    inputMode: keyboard ? 'keyboard' : 'camera',
    boot: {
      progress: bootP,
      message: bootP < 1 ? 'Loading body tracking…' : 'Ready!',
      error: flag('error') ? (q.get('error') === '1' || q.get('error') === 'true' ? 'NotAllowedError: Permission denied' : (q.get('error') ?? '')) : null,
      needsClick: bootP >= 1,
    },
    lobby: {
      people: ps.map((p) => ({ slot: p.slot, handsUpProgress: p.progress, joined: p.progress >= 1, hints: p.hints })),
      joinCountdown: n === 2 ? 4 - ((t / 1000) % 4) : null,
      spectators: 1,
    },
    countdown: {
      value: 3 - Math.floor((t % 4000) / 1000),
      calibration: [Math.min(1, (t % 4000) / 2500), Math.min(1, (t % 4000) / 3500)].slice(0, n),
      message: null,
    },
    paused:
      screen === 'paused'
        ? {
            reason: q.get('reason') === 'tracking-lost' ? 'tracking-lost' : 'user',
            resumeIn: flag('resume') ? 3 - ((t / 1000) % 3) : null,
            missing: q.get('reason') === 'tracking-lost' ? (n === 2 ? [1] : [0]) : [],
          }
        : null,
    hud,
    out,
    winner: n === 2 && (flag('out') || screen === 'gameover') ? 0 : null,
    gameOver:
      screen === 'gameover'
        ? { results: results(), restartLockout: lockout > 0 ? lockout : null, handsUpProgress: lockout > 0 ? 0 : Math.min(1, ((t % 8000) - 2500) / 2500) }
        : null,
    leaderboard: lb,
    vision: { status: keyboard ? 'idle' : 'running', frame: keyboard ? null : visionFrame(t, ps), video },
    settings,
    settingsOpen,
    cameras: [
      { deviceId: 'cam-a', label: 'FaceTime HD Camera' },
      { deviceId: 'cam-b', label: 'iPhone Continuity Camera' },
    ],
    debug: flag('debug') ? { fps: 60, frameMs: 6.4, visionFps: 29, inferenceMs: 14.2, drawCalls: 180 } : null,
    toast: screen === 'playing' ? { text: n === 2 && flag('out') ? 'P1 WINS — KEEP GOING!' : 'SPEED UP!', t: Math.floor((performance.now() - timeOrigin) / 4000) * 4000 + timeOrigin + 200 } : null,
  };
}

// ---------------------------------------------------------------------------
// Wire up
// ---------------------------------------------------------------------------
const ui = new UIManager();
ui.init(document.getElementById('ui-root') as HTMLElement);
const logEl = document.getElementById('sb-log') as HTMLElement;
const logLines: string[] = [];
ui.onCommand((c: UICommand) => {
  console.log('[ui command]', JSON.stringify(c));
  logLines.push(JSON.stringify(c).slice(0, 120));
  while (logLines.length > 6) logLines.shift();
  logEl.textContent = logLines.join('\n');
  switch (c.type) {
    case 'settings-open':
      settingsOpen = c.open;
      break;
    case 'settings-change':
      settings = { ...settings, ...c.settings, vision: c.settings.vision ? { ...c.settings.vision } : settings.vision };
      break;
    case 'rename':
      if (c.entryId === 'e2') names = [c.name, names[1]];
      if (c.entryId === 'e4') names = [names[0], c.name];
      break;
    case 'start':
      players = c.players;
      setScreen('countdown');
      break;
    case 'restart':
      setScreen('countdown');
      break;
    case 'to-lobby':
      setScreen('lobby');
      break;
    case 'pause-toggle':
      setScreen(screen === 'paused' ? 'playing' : 'paused');
      break;
    case 'boot-click':
    case 'keyboard-only':
      setScreen('lobby');
      break;
    default:
      break;
  }
});
addEventListener('keydown', (e) => {
  if (e.key === '`') settingsOpen = !settingsOpen;
});

const bar = document.getElementById('sb-bar') as HTMLElement;
if (q.get('bar') === '0') bar.style.display = 'none';
const btns = new Map<string, HTMLButtonElement>();
function setScreen(s: Screen): void {
  screen = s;
  for (const [k, b] of btns) b.classList.toggle('on', k === screen || k === `${players}P`);
}
for (const s of SCREENS) {
  const b = document.createElement('button');
  b.textContent = s;
  b.onclick = () => setScreen(s);
  bar.append(b);
  btns.set(s, b);
}
for (const n of [1, 2] as const) {
  const b = document.createElement('button');
  b.textContent = `${n}P`;
  b.onclick = () => {
    players = n;
    setScreen(screen);
  };
  bar.append(b);
  btns.set(`${n}P`, b);
}
setScreen(screen);

let last = performance.now();
function frame(now: number): void {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  const t = now - timeOrigin;
  drawBg(t);
  ui.render(model(t), dt);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
