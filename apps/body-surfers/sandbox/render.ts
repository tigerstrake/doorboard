// Render sandbox: drives the Renderer with its own fake world (no src/game dependency).
// URL: ?players=2  ?quality=low  ?t0=<seconds into the anim cycle>
//      ?pu=jetpack|slowmo|star|coinRain|fast  (force a power-up state for the whole cycle)
// Default cycle: star + smashes (0-3 s), slow-mo + coin rain (3-7.3 s), magnet, hoverboard.
//      ?mimic=1  fake player pose: 1.5 s each of both arms up / left only up / arms out / leaning
// Keys: 1 / 2 = player count, Q = toggle quality, P = pause, R = reset run.
import { Renderer } from '../src/render';
import {
  BARRIER_HIGH_DEPTH,
  BARRIER_LOW_DEPTH,
  BEHIND_DISTANCE,
  COIN_HEIGHT,
  RAMP_LENGTH,
  TRAIN_CAR_LENGTH,
  TRAIN_HEIGHT,
  VIEW_DISTANCE,
  laneX,
} from '../src/shared/constants';
import type {
  ActivePowerUp,
  CollectibleState,
  GameEvent,
  Lane,
  MimicPose,
  ObstacleKind,
  ObstacleState,
  PlayerAnim,
  PlayerCount,
  PlayerId,
  PlayerState,
  PowerUpKind,
  Quality,
  RenderView,
  RunStatus,
  WorldSnapshot,
} from '../src/shared/types';

const params = new URLSearchParams(location.search);
let players: PlayerCount = params.get('players') === '2' ? 2 : 1;
let quality: Quality = params.get('quality') === 'low' ? 'low' : 'high';
const t0 = Number(params.get('t0') ?? 0) || 0;
const forced = params.get('pu');
/** ?gallery=1: all power-up pickups float in a row just ahead of the runner. */
const gallery = params.get('gallery') === '1';

const canvas = document.getElementById('c') as HTMLCanvasElement;
const hud = document.getElementById('hud') as HTMLDivElement;
const renderer = new Renderer();
renderer.init(canvas, { quality });
renderer.setPlayerCount(players);
const onResize = (): void => renderer.resize(window.innerWidth, window.innerHeight);
window.addEventListener('resize', onResize);
onResize();

// ---------------------------------------------------------------------------
// Fake world
// ---------------------------------------------------------------------------
const CYCLE = 15;
const ROW = 34;
const POWER_KINDS: readonly PowerUpKind[] = ['jetpack', 'slowmo', 'star', 'coinRain', 'magnet', 'multiplier', 'sneakers', 'hoverboard'];
const JET_Y = 6;

class FakeWorld {
  seed = 1;
  time = 0;
  distance = 0;
  speed = 11;
  status: RunStatus = 'running';
  coins = 0;
  private cycleT: number;
  private prevPhase = '';
  private lane: Lane = 0;
  private x = 0;
  private y = 0;
  private vy = 0;
  private anim: PlayerAnim = 'run';
  private animStart = 0;
  private collected = new Set<number>();
  private events: GameEvent[] = [];
  private obstacles: ObstacleState[] = [];
  private items: CollectibleState[] = [];
  private nextCoinEvt = 0;
  private hover = false;

  constructor(readonly playerId: PlayerId, offset: number) {
    this.cycleT = offset;
  }

  step(dt: number): void {
    const prev = this.cycleT;
    this.cycleT += dt;
    if (this.cycleT >= CYCLE) {
      this.cycleT -= CYCLE;
      this.seed++;
      this.distance = 0;
      this.time = 0;
      this.collected.clear();
      this.coins = 0;
    }
    const c = this.cycleT;
    void prev;
    let phase: string;
    if (c < 3) phase = 'run';
    else if (c < 3.7) phase = 'jump';
    else if (c < 4.5) phase = 'run2';
    else if (c < 5.15) phase = 'roll';
    else if (c < 6) phase = 'blocked';
    else if (c < 7.3) phase = 'superjump';
    else if (c < 8) phase = 'stumble';
    else if (c < 11) phase = 'hover';
    else if (c < 12.8) phase = 'dead';
    else phase = 'ready';
    const entered = phase !== this.prevPhase;
    this.prevPhase = phase;

    this.status = phase === 'dead' ? 'dead' : phase === 'ready' ? 'ready' : 'running';
    if (this.status === 'running') {
      this.time += dt;
      this.speed = forced === 'fast' ? 42 : 11 + 9 * (0.5 + 0.5 * Math.sin(this.time * 0.25));
      if (this.slowmo()) this.speed *= 0.6;
      this.distance += this.speed * dt;
      // star smashes through something every ~0.9 s
      if (this.star() && Math.floor(this.time / 0.9) !== Math.floor((this.time - dt) / 0.9)) {
        const kinds: ObstacleKind[] = ['barrier_low', 'train', 'barrier_high'];
        this.events.push({ type: 'smash', kind: kinds[Math.floor(this.time / 0.9) % 3], lane: this.lane });
      }
    }

    const g = 23.7;
    if (entered) {
      this.animStart = c;
      switch (phase) {
        case 'jump':
          this.vy = 8.29;
          this.anim = 'jump';
          this.events.push({ type: 'jump', super: false });
          break;
        case 'superjump':
          this.vy = Math.sqrt(2 * g * 4);
          this.anim = 'jump';
          this.events.push({ type: 'jump', super: true });
          break;
        case 'roll':
          this.anim = 'roll';
          this.events.push({ type: 'roll' });
          break;
        case 'blocked':
          this.anim = 'run';
          this.events.push({ type: 'blocked', dir: 1 });
          break;
        case 'stumble':
          this.anim = 'stumble';
          break;
        case 'hover':
          this.anim = 'run';
          this.hover = true;
          this.events.push({ type: 'powerUp', kind: 'hoverboard' });
          break;
        case 'dead':
          this.anim = 'dead';
          this.hover = false;
          this.events.push({ type: 'crash', cause: 'train', moving: false });
          break;
        case 'ready':
          this.anim = 'run';
          this.y = 0;
          this.vy = 0;
          break;
        default:
          this.anim = 'run';
      }
    }
    if (phase === 'hover' && c > 10.2 && c - dt <= 10.2) this.events.push({ type: 'hoverboardBreak' });

    // vertical motion
    if (forced === 'jetpack' && this.status === 'running') {
      this.y += (JET_Y - this.y) * Math.min(1, dt * 2.5);
      this.vy = 0;
      if (this.anim !== 'run') this.setAnim('run', c);
    } else if (this.y > 0 || this.vy > 0) {
      this.vy -= g * dt;
      this.y += this.vy * dt;
      if (this.vy < 0 && this.anim === 'jump' && this.y > 2) this.setAnim('fall', c);
      if (this.y <= 0) {
        this.y = 0;
        this.vy = 0;
        if (this.anim === 'jump' || this.anim === 'fall') this.setAnim('run', c);
        this.events.push({ type: 'land', onRoof: false });
      }
    }

    // lane weaving
    const laneSeq: Lane[] = [0, 1, 0, -1, 0, 0, 1, 1, 0, -1, -1, 0, 0, 0, 0];
    const want = phase === 'dead' || phase === 'ready' ? 0 : laneSeq[Math.floor(c) % laneSeq.length];
    if (want !== this.lane) {
      this.events.push({ type: 'laneChange', from: this.lane, to: want });
      this.lane = want;
    }
    const tx = laneX(this.lane);
    const before = this.x;
    if (phase !== 'dead') this.x += Math.sign(tx - this.x) * Math.min(Math.abs(tx - this.x), (2.6 / 0.17) * dt);
    this.lean = dt > 0 ? Math.max(-1, Math.min(1, (this.x - before) / dt / 15)) : 0;

    // coin pickup events
    if (this.status === 'running' && this.time > this.nextCoinEvt) {
      this.nextCoinEvt = this.time + 0.35;
      this.coins++;
      this.events.push({ type: 'coin', x: 0, y: 1, combo: this.coins, total: this.coins });
    }
    this.buildObjects();
    this.galleryItems();
  }

  private lean = 0;

  private slowmo(): boolean {
    return forced === 'slowmo' || (forced === null && this.cycleT >= 3 && this.cycleT < 7.3);
  }
  private star(): boolean {
    return forced === 'star' || (forced === null && this.cycleT < 3);
  }
  private coinRain(): boolean {
    return forced === 'coinRain' || (forced === null && this.cycleT >= 3 && this.cycleT < 7.3);
  }

  private setAnim(a: PlayerAnim, c: number): void {
    this.anim = a;
    this.animStart = c;
  }

  private buildObjects(): void {
    const obs = this.obstacles;
    const items = this.items;
    obs.length = 0;
    items.length = 0;
    const d = this.distance;
    const lo = d - BEHIND_DISTANCE;
    const hi = d + VIEW_DISTANCE;
    const k0 = Math.max(0, Math.floor((lo - 40) / ROW));
    const k1 = Math.floor(hi / ROW);
    const push = (id: number, kind: ObstacleKind, lane: Lane, s: number, length: number, variant: number, moving = false, speed = 0): void => {
      if (s + length < lo || s > hi) return;
      obs.push({ id, kind, lane, s, length, moving, speed, variant });
    };
    const coinLine = (id: number, lane: Lane, s0: number, n: number, y = COIN_HEIGHT, dy = 0): void => {
      for (let i = 0; i < n; i++) {
        const cid = id * 100 + i;
        const s = s0 + i * 2.4;
        if (s < lo || s > hi || s < d - 0.5) continue;
        items.push({ id: cid, kind: 'coin', lane, s, x: laneX(lane), y: y + dy * i });
      }
    };
    for (let k = k0; k <= k1; k++) {
      const s = 30 + k * ROW;
      const id = k * 10;
      switch (k % 6) {
        case 0:
          push(id, 'barrier_low', 0, s, BARRIER_LOW_DEPTH, 0);
          push(id + 1, 'train', -1, s - 6, 2 * TRAIN_CAR_LENGTH, k);
          coinLine(id, 1, s - 4, 8);
          {
            const kind = POWER_KINDS[(k / 6 + 4) % 8];
            if (s + 14 >= d - 0.5 && s + 14 <= hi) items.push({ id: id * 100 + 70, kind, lane: 0, s: s + 14, x: 0, y: 1.1 });
          }
          if (forced === 'jetpack') coinLine(id + 5, 0, s - 10, 14, JET_Y + 1);
          if (this.coinRain()) for (const ln of [-1, 0] as Lane[]) coinLine(id + 6 + ln, ln, s + 6, 10);
          break;
        case 1:
          push(id, 'barrier_high', 0, s, BARRIER_HIGH_DEPTH, 0);
          push(id + 1, 'ramp', 1, s, RAMP_LENGTH, 0);
          push(id + 2, 'train', 1, s + RAMP_LENGTH, 3 * TRAIN_CAR_LENGTH, k + 1);
          coinLine(id, 1, s + RAMP_LENGTH + 2, 10, TRAIN_HEIGHT + COIN_HEIGHT);
          break;
        case 2:
          push(id, 'train', -1, s, TRAIN_CAR_LENGTH, k + 2);
          push(id + 1, 'train', 1, s + 4, TRAIN_CAR_LENGTH, k + 3);
          coinLine(id, 0, s - 2, 9);
          {
            const kind = POWER_KINDS[(k - 2) / 6 % 8 | 0];
            if (s + 16 >= d - 0.5 && s + 16 <= hi) items.push({ id: id * 100 + 70, kind, lane: 0, s: s + 16, x: 0, y: 1.1 });
          }
          if (forced === 'jetpack') coinLine(id + 5, 0, s - 6, 14, JET_Y + 1);
          if (this.coinRain()) for (const ln of [-1, 1] as Lane[]) coinLine(id + 6 + ln, ln, s + 8, 10);
          break;
        case 3: {
          const ms = s + 60 - this.time * 8;
          push(id, 'train', 1, ms, 2 * TRAIN_CAR_LENGTH, k + 4, true, 8);
          const kind = POWER_KINDS[(k >> 1) % 8];
          if (s >= d - 0.5 && s <= hi) items.push({ id: id * 100 + 50, kind, lane: -1, s, x: laneX(-1), y: 1.1 });
          push(id + 1, 'barrier_low', -1, s + 10, BARRIER_LOW_DEPTH, 0);
          break;
        }
        case 4:
          push(id, 'barrier_low', -1, s, BARRIER_LOW_DEPTH, 0);
          push(id + 1, 'barrier_low', 0, s, BARRIER_LOW_DEPTH, 0);
          push(id + 2, 'barrier_low', 1, s, BARRIER_LOW_DEPTH, 0);
          for (let i = 0; i < 7; i++) {
            const cs = s - 7 + i * 2.4;
            const u = (i - 3) / 3;
            if (cs >= d - 0.5 && cs <= hi) items.push({ id: id * 100 + i, kind: 'coin', lane: 0, s: cs, x: 0, y: COIN_HEIGHT + 1.5 * (1 - u * u) });
          }
          {
            const kind = POWER_KINDS[((k >> 1) + 2) % 8];
            if (s + 12 >= d - 0.5 && s + 12 <= hi) items.push({ id: id * 100 + 60, kind, lane: 1, s: s + 12, x: laneX(1), y: 1.1 });
          }
          break;
        case 5:
          push(id, 'train', 0, s + 8, 3 * TRAIN_CAR_LENGTH, k + 5);
          push(id + 1, 'barrier_high', 1, s, BARRIER_HIGH_DEPTH, 0);
          coinLine(id, -1, s, 6);
          break;
      }
    }
  }

  private galleryItems(): void {
    if (!gallery) return;
    POWER_KINDS.forEach((kind, i) => {
      const lane = ((i % 3) - 1) as Lane;
      this.items.push({ id: 900000 + i, kind, lane, s: this.distance + 2.5 + Math.floor(i / 3) * 2.2, x: laneX(lane), y: 1.3 });
    });
  }

  snapshot(): WorldSnapshot {
    const c = this.cycleT;
    const ph = this.prevPhase;
    const player: PlayerState = {
      lane: this.lane,
      x: this.x,
      y: this.y,
      vy: this.vy,
      grounded: this.y <= 0 && forced !== 'jetpack',
      onRoof: false,
      anim: this.anim,
      animTime: c - this.animStart,
      rolling: this.anim === 'roll',
      lean: this.lean,
      blockedDir: ph === 'blocked' ? 1 : 0,
      hoverboard: this.hover,
      invulnerable: (ph === 'hover' && c > 10.2) || (this.star() && (forced !== null || c > 2)) ? 1 : 0,
      alive: ph !== 'dead',
    };
    const pu: ActivePowerUp[] = forced === null && c > 7.3 && c < 9 ? [{ kind: 'magnet', remaining: 5, duration: 12 }] : [];
    if (this.status === 'running') {
      if (forced === 'jetpack') pu.push({ kind: 'jetpack', remaining: 5, duration: 10 });
      if (this.star()) pu.push({ kind: 'star', remaining: 5, duration: 10 });
      if (this.slowmo()) pu.push({ kind: 'slowmo', remaining: 5, duration: 10 });
      if (this.coinRain()) pu.push({ kind: 'coinRain', remaining: 5, duration: 10 });
    }
    return {
      playerId: this.playerId,
      status: this.status,
      seed: this.seed,
      time: this.time,
      distance: this.distance,
      speed: this.status === 'running' ? this.speed : 0,
      speedLevel: Math.min(1, (this.speed - 11) / 13),
      score: Math.floor(this.distance),
      coins: this.coins,
      multiplier: 1,
      player,
      obstacles: this.obstacles,
      collectibles: this.items,
      powerUps: pu,
      hint: null,
    };
  }

  drain(): GameEvent[] {
    const e = this.events;
    this.events = [];
    return e;
  }
}

const worlds = [new FakeWorld(0, t0), new FakeWorld(1, t0 + 5)];
let paused = false;

window.addEventListener('keydown', (e) => {
  if (e.key === '1' || e.key === '2') {
    players = e.key === '1' ? 1 : 2;
    renderer.setPlayerCount(players);
  } else if (e.key === 'q' || e.key === 'Q') {
    quality = quality === 'high' ? 'low' : 'high';
    renderer.setQuality(quality);
  } else if (e.key === 'p' || e.key === 'P') {
    paused = !paused;
  } else if (e.key === 'r' || e.key === 'R') {
    worlds.forEach((w, i) => {
      w.seed += 10;
      w.distance = 0;
      void i;
    });
  }
});

// Fake mimic pose. Phase k = floor((t + 0.75) / 1.5) % 4 so 1.5/3/4.5/6 s land mid-phase.
const mimicOn = params.get('mimic') === '1';
const MIMIC_LABELS = ['both arms up', 'screen-LEFT arm up only', 'arms out', 'lean right + elbows'];
const mimic: MimicPose = { leftUpper: 0, leftLower: 0, rightUpper: 0, rightLower: 0, lean: 0, weight: 1 };
let mimicT = 0;
let mimicPhase = 0;
function stepMimic(dt: number): void {
  mimicT += dt;
  mimicPhase = Math.floor((mimicT + 0.75) / 1.5) % 4;
  const wave = 0.15 * Math.sin(mimicT * 6);
  const m = mimic;
  m.leftLower = 0;
  m.rightLower = 0;
  m.lean = 0;
  if (mimicPhase === 0) {
    m.leftUpper = Math.PI - 0.15 + wave;
    m.rightUpper = Math.PI - 0.15 - wave;
  } else if (mimicPhase === 1) {
    m.leftUpper = Math.PI - 0.1 + wave;
    m.rightUpper = 0.05;
  } else if (mimicPhase === 2) {
    m.leftUpper = Math.PI / 2 + wave;
    m.rightUpper = Math.PI / 2 - wave;
  } else {
    m.leftUpper = Math.PI / 2;
    m.rightUpper = Math.PI / 2;
    m.leftLower = 1.2;
    m.rightLower = 1.2;
    m.lean = 0.35;
  }
}

let last = performance.now();
let fpsAcc = 0;
let fpsFrames = 0;
let fps = 0;
let hudT = 0;
const views: RenderView[] = [];
function frame(now: number): void {
  const dt = Math.max(0, Math.min(0.1, (now - last) / 1000));
  last = now;
  views.length = 0;
  if (mimicOn && !paused) stepMimic(dt);
  for (let i = 0; i < players; i++) {
    const w = worlds[i];
    if (!paused) w.step(dt);
    views.push({ snapshot: w.snapshot(), events: w.drain(), mimic: mimicOn ? mimic : null });
  }
  renderer.render(views, paused ? 0 : dt);
  fpsAcc += dt;
  fpsFrames++;
  if (fpsAcc >= 0.5) {
    fps = fpsFrames / fpsAcc;
    fpsAcc = 0;
    fpsFrames = 0;
  }
  hudT += dt;
  if (hudT > 0.25) {
    hudT = 0;
    const st = renderer.getStats();
    const s = views[0]?.snapshot;
    hud.textContent =
      `fps ${fps.toFixed(0)}  draw calls ${st.drawCalls}  tris ${(st.triangles / 1000).toFixed(0)}k\n` +
      `players ${players}  quality ${quality}  ${s ? `${s.status} ${s.player.anim} d=${s.distance.toFixed(0)}` : ''}\n` +
      (mimicOn ? `mimic: ${MIMIC_LABELS[mimicPhase]}\n` : '') +
      `keys: 1/2 players  Q quality  P pause  R reset`;
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
(window as unknown as { __renderStats: () => unknown }).__renderStats = () => renderer.getStats();
