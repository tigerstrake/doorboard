// Pure, deterministic world simulation for one runner (MASTERPLAN §4).
import {
  BARRIER_HIGH_CLEARANCE, BARRIER_HIGH_TOP, BARRIER_LOW_DEPTH, BARRIER_HIGH_DEPTH, BARRIER_LOW_HEIGHT,
  BEHIND_DISTANCE, COIN_RADIUS, LANE_WIDTH, PLAYER_DEPTH, PLAYER_HEIGHT, PLAYER_ROLL_HEIGHT, PLAYER_WIDTH,
  COIN_HEIGHT, RAMP_LENGTH, TRAIN_CAR_LENGTH, TRAIN_HEIGHT, VIEW_DISTANCE, laneX, obstacleWidth,
} from '../shared/constants';
import { approach, clamp, damp } from '../shared/math';
import type {
  ActivePowerUp, CollectibleState, GameEvent, IWorld, Lane, ObstacleKind, ObstacleState, PlayerAction,
  PlayerAnim, PlayerId, PlayerState, PowerUpKind, TutorialHint, WorldOptions, WorldSnapshot,
} from '../shared/types';
import { CONFIG as C, GRAVITY, JUMP_V0, SUPER_V0 } from './config';
import { levelOfSpeed, speedAtDiffTime } from './course';
import { TrackGenerator, type RowSpec } from './generator';

interface Obs extends ObstacleState {
  horned: boolean;
  /** Track position at the start of the current step (moving trains). */
  ps: number;
}
interface Item extends CollectibleState {
  attracted: boolean;
}
interface ActiveP extends ActivePowerUp {
  warned: boolean;
}

const HALF_D = PLAYER_DEPTH / 2;
const LANE_SPEED = LANE_WIDTH / C.laneTime;
const POWERUP_DURATION: Record<PowerUpKind, number> = {
  magnet: C.magnetDuration,
  multiplier: C.multiplierDuration,
  sneakers: C.sneakersDuration,
  hoverboard: C.hoverboardDuration,
  jetpack: C.jetpackDuration,
  slowmo: C.slowmoDuration,
  star: C.starDuration,
  coinRain: C.coinRainDuration,
};

/** Jetpack flight phases. */
/** Shared result for steps without events (frozen: consumers must not mutate drained arrays). */
const NO_EVENTS = Object.freeze([]) as unknown as GameEvent[];
const JET_NONE = 0;
const JET_FLY = 1;
const JET_DESCEND = 2;

/** Small deterministic integer hash (sky-trail lanes; never touches the course RNG). */
function hash32(a: number, b: number): number {
  let h = (Math.imul(a | 0, 0x9e3779b1) ^ Math.imul(b | 0, 0x85ebca77)) >>> 0;
  h = Math.imul(h ^ (h >>> 15), 0x2c1b3c6d) >>> 0;
  return (h ^ (h >>> 13)) >>> 0;
}

const clampLane = (l: number): Lane => (l < -1 ? -1 : l > 1 ? 1 : (l as Lane));
const lateralHalf = (kind: ObstacleKind): number => (PLAYER_WIDTH + obstacleWidth(kind)) / 2 - C.lateralForgiveness;

/**
 * Swept 1-D overlap: a(f) = a0 + (a1 - a0) f for f in [0, 1]. Writes the sub-interval
 * of f where lo < a(f) < hi into out and returns true if it is non-empty.
 */
function sweep(a0: number, a1: number, lo: number, hi: number, out: [number, number]): boolean {
  const d = a1 - a0;
  if (Math.abs(d) < 1e-12) {
    out[0] = 0; out[1] = 1;
    return a0 > lo && a0 < hi;
  }
  let f0 = (lo - a0) / d;
  let f1 = (hi - a0) / d;
  if (f0 > f1) { const t = f0; f0 = f1; f1 = t; }
  out[0] = f0 > 0 ? f0 : 0;
  out[1] = f1 < 1 ? f1 : 1;
  return out[0] < out[1];
}
const LAT: [number, number] = [0, 1];
const LON: [number, number] = [0, 1];

export class World implements IWorld {
  private nextId = 1;
  private opts: WorldOptions = { seed: 1, tutorial: false };
  private gen!: TrackGenerator;
  private status: WorldSnapshot['status'] = 'ready';
  private time = 0;
  private diffTime = 0;
  private distance = 0;
  /** Effective scroll speed (base × timeScale). */
  private speed = C.speedMin;
  private baseSpeed = C.speedMin;
  /** Slowmo world-time factor (eases toward slowmoFactor / 1). */
  private timeScale = 1;
  private jet = JET_NONE;
  private skyCursor = 0;
  private rainCursor = 0;
  private smashed: Obs[] = [];
  private score = 0;
  private coins = 0;
  private combo = 0;
  private lastCoinTime = -99;
  private nextMilestone = C.milestoneEvery;
  private speedUpIdx = 0;
  private lastFar = 0;
  private tutorialEnd = 0;
  private tutorialRows: readonly RowSpec[] = [];

  private obstacles: Obs[] = [];
  private items: Item[] = [];
  private powerUps: ActiveP[] = [];
  private viewObs: ObstacleState[] = [];
  private viewItems: CollectibleState[] = [];
  private events: GameEvent[] = [];

  private player: PlayerState = World.freshPlayer();
  private desiredLane: Lane = 0;
  private blockedEmitted = false;
  private jumpBuf = 0;
  private coyote = 0;
  private rollTimer = 0;
  private pendingRoll = false;
  private stumble = 0;
  private lastDuckTime = -99;
  /** Runner state at the end of the previous step: collisions are swept from here to now. */
  private prevX = 0;
  private prevY = 0;
  private prevDistance = 0;

  private snap: WorldSnapshot;
  private genEnabled = true;

  constructor(readonly playerId: PlayerId) {
    this.snap = {
      playerId, status: 'ready', seed: 1, time: 0, distance: 0, speed: C.speedMin, speedLevel: 0, score: 0,
      coins: 0, multiplier: 1, player: this.player, obstacles: this.viewObs, collectibles: this.viewItems,
      powerUps: this.powerUps, hint: null,
    };
    this.reset({ seed: 1, tutorial: false });
  }

  private static freshPlayer(): PlayerState {
    return {
      lane: 0, x: 0, y: 0, vy: 0, grounded: true, onRoof: false, anim: 'run', animTime: 0, rolling: false,
      lean: 0, blockedDir: 0, hoverboard: false, invulnerable: 0, alive: true,
    };
  }

  // ---------------------------------------------------------------------------
  reset(opts: WorldOptions): void {
    this.opts = { ...opts };
    const tutorial = opts.tutorial && !opts.attract;
    this.gen = new TrackGenerator({ seed: opts.seed, tutorial, attract: !!opts.attract });
    this.tutorialEnd = tutorial ? C.tutorialEnd : 0;
    this.tutorialRows = this.gen.tutorialRows();
    this.status = 'ready';
    this.time = 0;
    this.diffTime = 0;
    this.distance = 0;
    this.speed = C.speedMin;
    this.baseSpeed = C.speedMin;
    this.timeScale = 1;
    this.jet = JET_NONE;
    this.skyCursor = 0;
    this.rainCursor = 0;
    this.smashed.length = 0;
    this.score = 0;
    this.coins = 0;
    this.combo = 0;
    this.lastCoinTime = -99;
    this.nextMilestone = C.milestoneEvery;
    this.speedUpIdx = 0;
    this.lastFar = 0;
    this.obstacles.length = 0;
    this.items.length = 0;
    this.powerUps.length = 0;
    this.viewObs.length = 0;
    this.viewItems.length = 0;
    this.events = [];
    Object.assign(this.player, World.freshPlayer());
    this.desiredLane = 0;
    this.blockedEmitted = false;
    this.jumpBuf = 0;
    this.coyote = 0;
    this.rollTimer = 0;
    this.pendingRoll = false;
    this.stumble = 0;
    this.lastDuckTime = -99;
    this.prevX = 0;
    this.prevY = 0;
    this.prevDistance = 0;
    this.genEnabled = true;
    this.generate();
  }

  start(): void {
    if (this.status === 'ready') this.status = 'running';
  }

  handleAction(a: PlayerAction): void {
    if (this.status === 'dead') return;
    const p = this.player;
    if (a.type === 'lane' && this.status === 'ready') {
      this.desiredLane = a.lane;
      p.lane = a.lane;
      p.x = laneX(a.lane);
      this.prevX = p.x;
      return;
    }
    if (this.status !== 'running') return;
    if (this.jet === JET_FLY && (a.type === 'jump' || a.type === 'duck')) return; // flying: lanes only
    switch (a.type) {
      case 'lane': this.setDesired(a.lane); break;
      case 'move': this.setDesired(clampLane(p.lane + a.dir)); break;
      case 'jump':
        if (this.pendingRoll && this.time - this.lastDuckTime <= C.duckJumpCancel) {
          this.pendingRoll = false;
          p.rolling = false;
        }
        if (this.canJump()) this.doJump();
        else this.jumpBuf = C.jumpBuffer;
        break;
      case 'duck':
        this.lastDuckTime = this.time;
        this.jumpBuf = 0;
        if (p.grounded) this.startRoll();
        else {
          p.vy = Math.min(p.vy, C.fastFallVy);
          p.rolling = true;
          this.pendingRoll = true;
          this.coyote = 0;
        }
        break;
    }
  }

  private setDesired(l: Lane): void {
    if (l !== this.desiredLane) {
      this.desiredLane = l;
      this.blockedEmitted = false;
    }
  }

  private canJump(): boolean {
    const p = this.player;
    return p.grounded || (this.coyote > 0 && p.vy <= 0);
  }

  private doJump(): void {
    const p = this.player;
    const sup = this.hasPower('sneakers');
    p.vy = sup ? SUPER_V0 : JUMP_V0;
    p.grounded = false;
    p.onRoof = false;
    p.rolling = false;
    this.rollTimer = 0;
    this.pendingRoll = false;
    this.coyote = 0;
    this.jumpBuf = 0;
    this.events.push({ type: 'jump', super: sup });
  }

  private startRoll(): void {
    const p = this.player;
    p.rolling = true;
    this.rollTimer = C.rollTime;
    this.pendingRoll = false;
    this.events.push({ type: 'roll' });
  }

  // ---------------------------------------------------------------------------
  step(dt: number): void {
    const p = this.player;
    if (this.status !== 'running') {
      p.animTime += dt;
      return;
    }
    const inTutorial = this.distance < this.tutorialEnd;
    // Slowmo scales world time (scroll, difficulty clock, moving trains); timers stay real-time.
    this.timeScale = approach(this.timeScale, this.hasPower('slowmo') ? C.slowmoFactor : 1, ((1 - C.slowmoFactor) / C.slowmoEase) * dt);
    const wdt = dt * this.timeScale;
    if (!inTutorial) this.diffTime += wdt;
    this.baseSpeed = inTutorial ? C.speedMin : speedAtDiffTime(this.diffTime);
    this.speed = this.baseSpeed * this.timeScale;
    const v = this.speed;
    this.time += dt;
    this.prevDistance = this.distance;
    this.distance += v * dt;
    this.score += v * dt * this.multiplier();

    while (this.distance >= this.nextMilestone) {
      this.events.push({ type: 'milestone', distance: this.nextMilestone });
      this.nextMilestone += C.milestoneEvery;
    }
    const level = levelOfSpeed(this.baseSpeed);
    while (this.speedUpIdx < C.speedUpLevels.length && level >= C.speedUpLevels[this.speedUpIdx]) {
      this.speedUpIdx++;
      this.events.push({ type: 'speedUp', level: this.speedUpIdx });
    }
    // Endless ramp: keep announcing every speedUpEvery m/s past speedMax.
    while (
      this.speedUpIdx >= C.speedUpLevels.length &&
      this.baseSpeed < C.speedCap &&
      this.baseSpeed >= C.speedMax + (this.speedUpIdx - C.speedUpLevels.length + 1) * C.speedUpEvery
    ) {
      this.speedUpIdx++;
      this.events.push({ type: 'speedUp', level: this.speedUpIdx });
    }

    this.generate();

    // Moving trains + horn.
    for (let i = 0; i < this.obstacles.length; i++) {
      const o = this.obstacles[i];
      o.ps = o.s;
      if (!o.moving) continue;
      o.s -= o.speed * wdt;
      if (!o.horned && o.s > this.distance) {
        const ttr = (o.s - this.distance) / (v + o.speed * this.timeScale);
        if (ttr < C.hornTime) {
          o.horned = true;
          this.events.push({ type: 'trainHorn', lane: o.lane });
        }
      }
    }

    this.updatePowerUps(dt);
    const star = this.findPower('star');
    if (star >= 0) p.invulnerable = Math.max(p.invulnerable, this.powerUps[star].remaining + C.starGrace);
    if (this.jet === JET_DESCEND) p.invulnerable = Math.max(p.invulnerable, C.jetpackLandInvuln);
    this.fillSkyTrail();
    this.fillCoinRain();
    if (p.invulnerable > 0) p.invulnerable = Math.max(0, p.invulnerable - dt);
    if (this.jumpBuf > 0) this.jumpBuf -= dt;
    if (this.coyote > 0) this.coyote -= dt;
    if (this.stumble > 0) this.stumble -= dt;
    if (p.rolling && p.grounded && !this.pendingRoll) {
      this.rollTimer -= dt;
      if (this.rollTimer <= 0) p.rolling = false;
    }

    this.updateLateral(dt);

    if (this.jet === JET_FLY) {
      const ny = damp(p.y, C.jetpackHeight, C.jetpackRise, dt);
      p.vy = (ny - p.y) / dt;
      p.y = ny;
    } else if (!p.grounded) {
      p.vy -= GRAVITY * dt;
      p.y += p.vy * dt;
    }

    if (this.jet !== JET_FLY) this.resolve(dt);
    if (this.status === 'running') this.updateItems(dt);
    this.cull();
    this.updateAnim(dt);
    this.prevX = p.x;
    this.prevY = p.y;
  }

  private updateLateral(dt: number): void {
    const p = this.player;
    if (this.desiredLane !== p.lane) {
      const dir = (this.desiredLane > p.lane ? 1 : -1) as -1 | 1;
      if (Math.abs(p.x - laneX(p.lane)) <= LANE_WIDTH * 0.5 + 1e-6) {
        const next = clampLane(p.lane + dir);
        if (this.hasPower('star')) {
          for (let o = this.laneBlocker(next); o; o = this.laneBlocker(next)) this.smash(o);
        }
        if (this.laneBlocker(next)) {
          p.blockedDir = dir;
          if (!this.blockedEmitted) {
            this.blockedEmitted = true;
            this.events.push({ type: 'blocked', dir });
            this.stumble = C.stumbleTime;
          }
        } else {
          const from = p.lane;
          p.lane = next;
          p.blockedDir = 0;
          this.blockedEmitted = false;
          this.events.push({ type: 'laneChange', from, to: next });
        }
      }
    } else p.blockedDir = 0;
    const tx = laneX(p.lane);
    p.x = approach(p.x, tx, LANE_SPEED * dt);
    p.lean = damp(p.lean, clamp((tx - p.x) / (LANE_WIDTH * 0.5), -1, 1), 18, dt);
  }

  /** The train/ramp whose side would block a move into `lane` (above the feet), if any. */
  private laneBlocker(lane: Lane): Obs | null {
    const p = this.player;
    if (this.jet === JET_FLY) return null;
    const front = this.distance + HALF_D;
    const back = this.distance - HALF_D;
    for (let i = 0; i < this.obstacles.length; i++) {
      const o = this.obstacles[i];
      if (o.lane !== lane) continue;
      if (o.kind === 'train') {
        const look = this.speed * C.laneTime * C.blockLookaheadFrac + (o.moving ? o.speed * C.laneTime : 0);
        if (o.s + C.nearFaceForgiveness > front + look || o.s + o.length < back) continue;
        if (TRAIN_HEIGHT > p.y + C.sideClearance) return o;
      } else if (o.kind === 'ramp') {
        if (o.s > front || o.s + o.length < back) continue;
        const h = (TRAIN_HEIGHT * clamp(front - o.s, 0, o.length)) / o.length;
        if (h > p.y + C.sideClearance) return o;
      }
    }
    return null;
  }

  /** Star: remove an obstacle the runner plows through. */
  private smash(o: Obs): void {
    const i = this.obstacles.indexOf(o);
    if (i < 0) return;
    this.obstacles.splice(i, 1);
    this.events.push({ type: 'smash', kind: o.kind, lane: o.lane });
  }

  /**
   * Swept collision: every obstacle is tested against the runner's motion over the whole
   * step (s from prevDistance → distance relative to the obstacle's own motion, x from
   * prevX → x, y from prevY → y), so nothing can be skipped at any speed. The axis that
   * was crossed last decides whether a train/ramp contact is a front hit or a side bump.
   */
  private resolve(dt: number): void {
    const p = this.player;
    const nf = C.nearFaceForgiveness;
    const d0 = this.prevDistance;
    const d1 = this.distance;
    const x0 = this.prevX;
    const x1 = p.x;
    const y0 = this.prevY;
    const y1 = p.y;
    const movingX = Math.abs(x1 - x0) > 1e-6;
    let ground = 0;
    let inside = false;
    let crashObs: Obs | null = null;
    const star = this.hasPower('star');
    const smashed = this.smashed;
    smashed.length = 0;
    for (let i = 0; i < this.obstacles.length; i++) {
      const o = this.obstacles[i];
      const lx = laneX(o.lane);
      const half = lateralHalf(o.kind);
      if (!sweep(x0 - lx, x1 - lx, -half, half, LAT)) continue;
      // Runner box [r - HALF_D, r + HALF_D] vs obstacle [s (+ near-face forgiveness), s + length].
      const nearOff = o.kind === 'ramp' ? 0 : nf;
      const r0 = d0 - o.ps;
      const r1 = d1 - o.s;
      if (!sweep(r0, r1, nearOff - HALF_D, o.length + HALF_D, LON)) continue;
      const f0 = Math.max(LAT[0], LON[0]);
      const f1 = Math.min(LAT[1], LON[1]);
      if (f0 >= f1) continue;

      if (o.kind === 'barrier_low' || o.kind === 'barrier_high') {
        // Hit if the runner is at a colliding height at any point of the contact interval.
        const hit = this.barrierHit(o.kind, y0 + (y1 - y0) * f0) || this.barrierHit(o.kind, y0 + (y1 - y0) * f1);
        if (!hit) continue;
        if (star) { smashed.push(o); continue; }
        if (p.invulnerable > 0) { inside = true; continue; }
        if (!crashObs) crashObs = o;
        continue;
      }
      const isTrain = o.kind === 'train';
      const h = isTrain ? TRAIN_HEIGHT : (TRAIN_HEIGHT * clamp(d1 - o.s, 0, o.length)) / o.length;
      if (y1 >= h - C.roofSnap) {
        if (h > ground) ground = h;
        continue;
      }
      // Which face was crossed last? (0 = already overlapping on that axis last step.)
      const latEnter = Math.abs(x0 - lx) < half ? 0 : LAT[0];
      const lonEnter = r0 > nearOff - HALF_D && r0 < o.length + HALF_D ? 0 : LON[0];
      const side = latEnter > lonEnter;
      if (star && (isTrain || side)) { smashed.push(o); continue; }
      if (p.invulnerable > 0) { inside = true; continue; }
      if (side) {
        this.sideBump(lx, half);
        continue;
      }
      if (!isTrain) {
        if (h > ground) ground = h; // ramps are never fatal
        continue;
      }
      // Front contact that only clips the train's corner while changing lanes: side bump
      // (moving in) or graze (moving out). Anything deeper is a crash.
      if (movingX) {
        const pen = half - Math.abs(x0 + (x1 - x0) * lonEnter - lx);
        if (pen <= C.cornerForgiveness) {
          if (Math.abs(x1 - lx) < Math.abs(x0 - lx)) this.sideBump(lx, half);
          else p.x = lx + Math.sign(x1 - lx) * (half + 0.001);
          continue;
        }
      }
      if (!crashObs) crashObs = o;
    }

    for (let i = 0; i < smashed.length; i++) this.smash(smashed[i]);
    smashed.length = 0;
    if (crashObs) {
      this.crash(crashObs);
      if (this.status !== 'running') return;
    }
    if (inside && p.invulnerable < dt * 2) p.invulnerable = dt * 2;

    // Vertical resolution against the ground height.
    if (p.grounded) {
      if (p.y > ground + 0.02) {
        p.grounded = false;
        p.onRoof = false;
        p.vy = 0;
        this.coyote = C.coyoteTime;
      } else {
        p.y = ground;
        p.onRoof = ground >= TRAIN_HEIGHT - 0.01;
      }
    } else if (p.y <= ground) {
      if (p.vy <= 0) this.land(ground);
      else p.y = ground;
    }
  }

  private barrierHit(kind: ObstacleKind, y: number): boolean {
    if (kind === 'barrier_low') return y < BARRIER_LOW_HEIGHT - C.lowBarrierMargin;
    const top = y + (this.player.rolling ? PLAYER_ROLL_HEIGHT : PLAYER_HEIGHT);
    return top > BARRIER_HIGH_CLEARANCE && y < BARRIER_HIGH_TOP;
  }

  private land(ground: number): void {
    const p = this.player;
    if (this.jet === JET_DESCEND) {
      this.jet = JET_NONE;
      p.invulnerable = Math.max(p.invulnerable, C.jetpackLandInvuln);
    }
    p.y = ground;
    p.vy = 0;
    p.grounded = true;
    p.onRoof = ground >= TRAIN_HEIGHT - 0.01;
    this.events.push({ type: 'land', onRoof: p.onRoof });
    if (this.jumpBuf > 0 && !this.pendingRoll) {
      this.doJump();
    } else if (this.pendingRoll) {
      this.startRoll();
    }
  }

  private sideBump(obsX: number, half: number): void {
    const p = this.player;
    const dir = (obsX > this.prevX ? 1 : -1) as -1 | 1;
    p.x = obsX - dir * (half + 0.001);
    p.lane = clampLane(p.lane - dir);
    // desiredLane is kept on purpose: like a pre-check block, the runner waits beside the
    // train and completes the move once it has passed (camera lanes are absolute).
    p.blockedDir = dir;
    if (!this.blockedEmitted) {
      this.blockedEmitted = true;
      this.events.push({ type: 'blocked', dir });
      this.stumble = C.stumbleTime;
    }
  }

  private crash(o: Obs): void {
    const p = this.player;
    if (p.invulnerable > 0) return;
    const hb = this.findPower('hoverboard');
    if (hb >= 0) {
      this.powerUps.splice(hb, 1);
      p.hoverboard = false;
      p.invulnerable = C.invulnerableTime;
      this.events.push({ type: 'hoverboardBreak' });
      return;
    }
    if (this.distance < this.tutorialEnd) {
      p.invulnerable = C.tutorialInvulnerable;
      this.stumble = C.stumbleTime;
      this.events.push({ type: 'blocked', dir: 1 });
      return;
    }
    p.alive = false;
    this.status = 'dead';
    this.events.push({ type: 'crash', cause: o.kind, moving: o.moving });
  }

  // ---------------------------------------------------------------------------
  private multiplier(): number {
    return this.findPower('multiplier') >= 0 ? 2 : 1;
  }

  private findPower(kind: PowerUpKind): number {
    for (let i = 0; i < this.powerUps.length; i++) if (this.powerUps[i].kind === kind) return i;
    return -1;
  }

  private hasPower(kind: PowerUpKind): boolean {
    return this.findPower(kind) >= 0;
  }

  private activate(kind: PowerUpKind): void {
    const i = this.findPower(kind);
    const d = POWERUP_DURATION[kind];
    if (i >= 0) {
      this.powerUps[i].remaining = d;
      this.powerUps[i].warned = false;
    } else this.powerUps.push({ kind, remaining: d, duration: d, warned: false });
    if (kind === 'hoverboard') this.player.hoverboard = true;
    if (kind === 'jetpack') this.takeOff();
    if (kind === 'star') this.player.invulnerable = Math.max(this.player.invulnerable, d + C.starGrace);
    this.events.push({ type: 'powerUp', kind });
  }

  private updatePowerUps(dt: number): void {
    let w = 0;
    for (let i = 0; i < this.powerUps.length; i++) {
      const a = this.powerUps[i];
      a.remaining -= dt;
      if (!a.warned && a.remaining <= C.powerUpWarning) {
        a.warned = true;
        this.events.push({ type: 'powerUpWarning', kind: a.kind });
      }
      if (a.remaining <= 0) {
        this.events.push({ type: 'powerUpEnd', kind: a.kind });
        if (a.kind === 'hoverboard') this.player.hoverboard = false;
        if (a.kind === 'jetpack') this.startDescent();
        continue;
      }
      this.powerUps[w++] = a;
    }
    this.powerUps.length = w;
  }

  private takeOff(): void {
    const p = this.player;
    if (this.jet !== JET_FLY) this.skyCursor = 0;
    this.jet = JET_FLY;
    p.grounded = false;
    p.onRoof = false;
    p.rolling = false;
    this.rollTimer = 0;
    this.pendingRoll = false;
    this.jumpBuf = 0;
    this.coyote = 0;
  }

  private startDescent(): void {
    const p = this.player;
    this.jet = JET_DESCEND;
    p.vy = Math.min(p.vy, 0);
    p.invulnerable = Math.max(p.invulnerable, C.jetpackLandInvuln);
  }

  /** Jetpack: lay a coin trail at flight height that hops lanes every skyTrailSegment meters. */
  private fillSkyTrail(): void {
    if (this.jet !== JET_FLY) return;
    const a = this.powerUps[this.findPower('jetpack')];
    if (!a) return;
    const until = this.distance + Math.min(C.skyTrailLead, this.speed * Math.max(0, a.remaining - 0.4));
    if (this.skyCursor < this.distance + this.speed * 0.5) this.skyCursor = this.distance + this.speed * 0.5;
    const y = C.jetpackHeight + COIN_HEIGHT * 0.9;
    while (this.skyCursor < until) {
      const seg = Math.floor(this.skyCursor / C.skyTrailSegment);
      const lane = ((hash32(seg, this.opts.seed) % 3) - 1) as Lane;
      this.items.push({ id: this.nextId++, kind: 'coin', lane, s: this.skyCursor, x: laneX(lane), y, attracted: false });
      this.skyCursor += C.coinSpacing;
    }
  }

  /** Coin rain: dense coin lines in every lane ahead (on roofs/ramps where trains are). */
  private fillCoinRain(): void {
    const a = this.powerUps[this.findPower('coinRain')];
    if (!a) return;
    const until = this.distance + Math.min(C.coinRainLead, this.speed * a.remaining);
    if (this.rainCursor < this.distance + 6) this.rainCursor = this.distance + 6;
    const flying = this.jet === JET_FLY;
    while (this.rainCursor < until) {
      const s = this.rainCursor;
      this.rainCursor += C.coinRainSpacing;
      for (let lane = -1 as Lane; lane <= 1; lane++) {
        const y = flying ? C.jetpackHeight + COIN_HEIGHT * 0.9 : this.rainHeight(lane as Lane, s);
        if (y < 0 || this.coinNear(lane as Lane, s, y)) continue;
        this.items.push({ id: this.nextId++, kind: 'coin', lane: lane as Lane, s, x: laneX(lane as Lane), y, attracted: false });
      }
    }
  }

  /** Coin height for a rain coin at (lane, s), or -1 if no coin belongs there. */
  private rainHeight(lane: Lane, s: number): number {
    let y = COIN_HEIGHT;
    for (let i = 0; i < this.obstacles.length; i++) {
      const o = this.obstacles[i];
      if (o.lane !== lane) continue;
      if (o.moving) { if (o.s + o.length > this.distance) return -1; continue; } // lane will be driven through
      if (o.kind === 'barrier_low' || o.kind === 'barrier_high') {
        if (s > o.s - 1.5 && s < o.s + o.length + 1.5) return -1;
      } else if (o.kind === 'train') {
        if (s >= o.s - 0.5 && s <= o.s + o.length - 0.5) y = TRAIN_HEIGHT + COIN_HEIGHT;
        else if (s > o.s - 4 && s < o.s - 0.5) return -1; // don't lead the line into a train face
      } else if (s >= o.s && s <= o.s + o.length) {
        y = (TRAIN_HEIGHT * (s - o.s)) / o.length + COIN_HEIGHT;
      }
    }
    return y;
  }

  private coinNear(lane: Lane, s: number, y: number): boolean {
    for (let i = 0; i < this.items.length; i++) {
      const it = this.items[i];
      if (it.lane === lane && Math.abs(it.s - s) < 1.2 && Math.abs(it.y - y) < 1.5) return true;
    }
    return false;
  }

  private updateItems(dt: number): void {
    const p = this.player;
    const magnet = this.hasPower('magnet');
    const height = p.rolling ? PLAYER_ROLL_HEIGHT : PLAYER_HEIGHT;
    const reachZ = HALF_D + COIN_RADIUS + 0.2;
    const reachX = PLAYER_WIDTH / 2 + COIN_RADIUS;
    const x0 = this.prevX;
    const y0 = this.prevY;
    const d0 = this.prevDistance;
    let w = 0;
    for (let i = 0; i < this.items.length; i++) {
      const it = this.items[i];
      let ds = it.s - this.distance;
      if (ds < -BEHIND_DISTANCE) continue;
      if (it.kind === 'coin') {
        if (magnet && !it.attracted && ds < C.magnetRange && ds > -1) it.attracted = true;
        if (it.attracted) {
          const tx = p.x - it.x;
          const ty = p.y + height * 0.5 - it.y;
          const len = Math.sqrt(ds * ds + tx * tx + ty * ty);
          const stepLen = C.magnetSpeed * dt;
          if (len <= stepLen || len < 1e-6) {
            it.s = this.distance; it.x = p.x; it.y = p.y + height * 0.5;
          } else {
            const k = stepLen / len;
            it.s -= ds * k; it.x += tx * k; it.y += ty * k;
          }
          ds = it.s - this.distance;
        }
      }
      // Swept along s: test at the runner's closest approach to the item during this step.
      let px = p.x;
      let py = p.y;
      if (!it.attracted) {
        const r0 = d0 - it.s;
        const r1 = -ds;
        let f = 1;
        if (r0 < 0 && r1 >= 0) f = r0 / (r0 - r1);
        else if (Math.abs(r0) < Math.abs(r1)) f = 0;
        ds = -(r0 + (r1 - r0) * f);
        px = x0 + (p.x - x0) * f;
        py = y0 + (p.y - y0) * f;
      }
      if (Math.abs(ds) < reachZ && Math.abs(it.x - px) < reachX && it.y + COIN_RADIUS > py && it.y - COIN_RADIUS < py + height) {
        this.collect(it);
        continue;
      }
      this.items[w++] = it;
    }
    this.items.length = w;
  }

  private collect(it: Item): void {
    const p = this.player;
    if (it.kind === 'coin') {
      this.coins++;
      this.combo = this.time - this.lastCoinTime <= C.comboGap ? this.combo + 1 : 1;
      this.lastCoinTime = this.time;
      this.score += C.coinScore * this.multiplier();
      this.events.push({ type: 'coin', x: it.x - p.x, y: it.y - p.y, combo: this.combo, total: this.coins });
    } else this.activate(it.kind);
  }

  private cull(): void {
    const min = this.distance - BEHIND_DISTANCE;
    let w = 0;
    for (let i = 0; i < this.obstacles.length; i++) {
      const o = this.obstacles[i];
      if (o.s + o.length < min) continue;
      this.obstacles[w++] = o;
    }
    this.obstacles.length = w;
  }

  private updateAnim(dt: number): void {
    const p = this.player;
    let a: PlayerAnim;
    if (!p.alive) a = 'dead';
    else if (p.rolling) a = 'roll';
    else if (this.jet === JET_FLY) a = 'jump';
    else if (!p.grounded) a = p.vy > 0 ? 'jump' : 'fall';
    else if (this.stumble > 0) a = 'stumble';
    else a = 'run';
    if (a !== p.anim) {
      p.anim = a;
      p.animTime = 0;
    } else p.animTime += dt;
  }

  // ---------------------------------------------------------------------------
  private generate(): void {
    if (!this.genEnabled) return;
    while (this.lastFar < this.distance + C.genAhead) this.materialize(this.gen.next());
  }

  private materialize(r: RowSpec): void {
    for (let i = 0; i < 3; i++) {
      const l = r.lanes[i];
      const lane = (i - 1) as Lane;
      switch (l.content) {
        case 'empty': break;
        case 'low': this.addObs('barrier_low', lane, r.s, BARRIER_LOW_DEPTH, l.variant); break;
        case 'high': this.addObs('barrier_high', lane, r.s, BARRIER_HIGH_DEPTH, l.variant); break;
        case 'train': this.addObs('train', lane, r.s, l.cars * TRAIN_CAR_LENGTH, l.variant); break;
        case 'ramp':
          this.addObs('ramp', lane, r.s, RAMP_LENGTH, l.variant);
          this.addObs('train', lane, r.s + RAMP_LENGTH, l.cars * TRAIN_CAR_LENGTH, l.variant);
          break;
        case 'moving': {
          const c = this.gen.course;
          const lead = Math.max(0, c.timeAt(r.s) - c.timeAt(this.distance));
          const o = this.addObs('train', lane, r.s + l.mspeed * lead, l.cars * TRAIN_CAR_LENGTH, l.variant);
          o.moving = true;
          o.speed = l.mspeed;
          break;
        }
      }
    }
    for (const it of r.items) {
      this.items.push({ id: this.nextId++, kind: it.kind, lane: it.lane, s: it.s, x: laneX(it.lane), y: it.y, attracted: false });
    }
    if (r.far > this.lastFar) this.lastFar = r.far;
  }

  private addObs(kind: ObstacleKind, lane: Lane, s: number, length: number, variant: number): Obs {
    const o: Obs = { id: this.nextId++, kind, lane, s, length, moving: false, speed: 0, variant, horned: false, ps: s };
    this.obstacles.push(o);
    return o;
  }

  // ---------------------------------------------------------------------------
  private computeHint(): TutorialHint | null {
    if (this.status !== 'running' || this.distance >= this.tutorialEnd) return null;
    const p = this.player;
    for (const r of this.tutorialRows) {
      const barrier = r.hint === 'jump' || r.hint === 'duck';
      const passed = barrier ? this.distance - HALF_D > r.s + BARRIER_LOW_DEPTH : this.distance + HALF_D > r.s;
      if (passed) continue;
      if ((r.s - this.distance) / this.speed > C.hintLead) return null;
      switch (r.hint) {
        case 'jump': return p.grounded ? 'jump' : null;
        case 'duck': return p.rolling ? null : 'duck';
        default: {
          const c = r.lanes[p.lane + 1].content;
          return c === 'train' ? r.hint : null;
        }
      }
    }
    return null;
  }

  snapshot(): WorldSnapshot {
    const s = this.snap;
    const lo = this.distance - BEHIND_DISTANCE;
    const hi = this.distance + VIEW_DISTANCE;
    let n = 0;
    for (let i = 0; i < this.obstacles.length; i++) {
      const o = this.obstacles[i];
      if (o.s + o.length >= lo && o.s <= hi) this.viewObs[n++] = o;
    }
    this.viewObs.length = n;
    n = 0;
    for (let i = 0; i < this.items.length; i++) {
      const it = this.items[i];
      if (it.s >= lo && it.s <= hi) this.viewItems[n++] = it;
    }
    this.viewItems.length = n;
    s.status = this.status;
    s.seed = this.opts.seed;
    s.time = this.time;
    s.distance = this.distance;
    s.speed = this.speed;
    s.speedLevel = levelOfSpeed(this.speed);
    s.score = Math.floor(this.score);
    s.coins = this.coins;
    s.multiplier = this.multiplier();
    s.hint = this.computeHint();
    return s;
  }

  // ---- Test hooks (not part of IWorld; used by unit tests / sandboxes) -------
  /** Remove all generated content and stop generating (scripted test tracks). */
  testClearTrack(): void {
    this.obstacles.length = 0;
    this.items.length = 0;
    this.genEnabled = false;
  }

  testAddObstacle(kind: ObstacleKind, lane: Lane, s: number, length: number, movingSpeed = 0): ObstacleState {
    const o = this.addObs(kind, lane, s, length, 0);
    if (movingSpeed > 0) { o.moving = true; o.speed = movingSpeed; }
    return o;
  }

  /** Jump the difficulty clock (e.g. 1e4 ⇒ the speed cap) without simulating the run. */
  testSetDiffTime(t: number): void {
    this.diffTime = t;
  }

  testAddItem(kind: CollectibleState['kind'], lane: Lane, s: number, y: number): void {
    this.items.push({ id: this.nextId++, kind, lane, s, x: laneX(lane), y, attracted: false });
  }

  drainEvents(): GameEvent[] {
    const e = this.events;
    if (e.length === 0) return NO_EVENTS; // most steps: no garbage
    this.events = [];
    return e;
  }
}
