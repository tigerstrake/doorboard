// Seeded, deterministic track generator. Produces abstract rows (per-lane
// content + items); the World materializes them into obstacles/collectibles.
//
// A row's obstacles all start at row.s (ramp lanes: ramp at s, train at s + RAMP_LENGTH),
// so a runner beside a long train never meets a new obstacle until the row's far
// end + spacing. Spacing is always measured from the previous row's far end.
import { Rng } from '../shared/rng';
import { lerp } from '../shared/math';
import {
  BARRIER_HIGH_DEPTH, BARRIER_LOW_DEPTH, COIN_HEIGHT, RAMP_LENGTH, TRAIN_CAR_LENGTH, TRAIN_HEIGHT,
} from '../shared/constants';
import type { CollectibleKind, Lane, PowerUpKind, TutorialHint } from '../shared/types';
import { CONFIG as C, GRAVITY, JUMP_V0 } from './config';
import { Course } from './course';

export type LaneContent = 'empty' | 'low' | 'high' | 'train' | 'ramp' | 'moving';

export type PatternName =
  | 'tutorial'
  | 'single_low' | 'double_low' | 'full_low'
  | 'single_high' | 'full_high'
  | 'train_single' | 'train_double'
  | 'ramp_train' | 'train_wall_ramp'
  | 'mixed' | 'moving_train' | 'zigzag';

export interface LaneSpec {
  content: LaneContent;
  /** Train cars (train / ramp / moving). */
  cars: number;
  /** Moving trains: m/s toward the runner. */
  mspeed: number;
  variant: number;
}

export interface ItemSpec {
  kind: CollectibleKind;
  lane: Lane;
  s: number;
  y: number;
}

export interface RowSpec {
  index: number;
  pattern: PatternName;
  /** Near edge (for moving trains: where the runner meets the train's front). */
  s: number;
  /** Far end in runner terms (moving trains: where the runner passes the tail). */
  far: number;
  lanes: [LaneSpec, LaneSpec, LaneSpec];
  /** Bit i (lane i-1): passable by staying in the lane. */
  passMask: number;
  /** Lanes the runner can be in when leaving the row. */
  exitMask: number;
  /** Worst-case lane changes needed from the previous row's exits. */
  requiredChanges: number;
  speed: number;
  level: number;
  /** Distance from the previous row's far end. */
  spacing: number;
  /** Minimum spacing the rules demanded (spacing >= minSpacing). */
  requiredSpacing: number;
  items: ItemSpec[];
  hint: TutorialHint | null;
  /** Moving rows: lane index reserved (kept empty) until this track position. */
  reserveUntil: number;
  /** Row contains a low/high barrier. */
  vertical: boolean;
}

export interface GeneratorOptions {
  seed: number;
  tutorial: boolean;
  attract: boolean;
}

const LANE_OF = [-1, 0, 1] as const;
const ALL = 7;

const PATTERNS: readonly { name: PatternName; w: number; unlock: number }[] = [
  { name: 'single_low', w: 1.0, unlock: 0 },
  { name: 'double_low', w: 0.7, unlock: 0 },
  { name: 'full_low', w: 0.45, unlock: 0 },
  { name: 'single_high', w: 0.8, unlock: 0 },
  { name: 'full_high', w: 0.4, unlock: C.fullHighUnlock },
  { name: 'train_single', w: 1.1, unlock: 0 },
  { name: 'train_double', w: 0.9, unlock: 0 },
  { name: 'ramp_train', w: 0.6, unlock: C.rampUnlock },
  { name: 'train_wall_ramp', w: 0.35, unlock: C.wallRampUnlock },
  { name: 'mixed', w: 1.0, unlock: 0 },
  { name: 'moving_train', w: 0.55, unlock: C.movingUnlock },
  { name: 'zigzag', w: 0.4, unlock: C.zigzagUnlock },
];
const VERTICAL_PATTERNS = new Set<PatternName>(['single_low', 'double_low', 'full_low', 'single_high', 'full_high', 'mixed']);

const HARD_PATTERNS = new Set<PatternName>(['double_low', 'train_double', 'mixed']);
const SINGLE_PATTERNS = new Set<PatternName>(['single_low', 'single_high', 'train_single']);

export const POWERUPS: readonly PowerUpKind[] = ['magnet', 'multiplier', 'sneakers', 'hoverboard', 'jetpack', 'slowmo', 'star', 'coinRain'];
const POWERUP_W: readonly number[] = POWERUPS.map((k) => C.powerUpWeights[k]);

export const isPassContent = (c: LaneContent): boolean => c === 'empty' || c === 'low' || c === 'high' || c === 'ramp';

export function gapSeconds(level: number): number {
  return lerp(C.gapStart, C.gapEnd, level);
}

/** Lane changes needed in the worst case to get from any exit lane to a passable lane. */
export function requiredChanges(prevExit: number, pass: number): number {
  let worst = 0;
  for (let e = 0; e < 3; e++) {
    if (!(prevExit & (1 << e))) continue;
    let best = 9;
    for (let q = 0; q < 3; q++) if (pass & (1 << q)) best = Math.min(best, Math.abs(e - q));
    worst = Math.max(worst, best);
  }
  return worst;
}

const emptyLane = (): LaneSpec => ({ content: 'empty', cars: 0, mspeed: 0, variant: 0 });

export class TrackGenerator {
  readonly course: Course;
  private rng: Rng;
  private index = 0;
  private prev: RowSpec | null = null;
  private prevFar = 0;
  private tutorialQueue: RowSpec[] = [];
  private queued: { pattern: PatternName; open: number } | null = null;
  private reservedUntil = [0, 0, 0];
  private nextPowerUp: number;
  private consecutiveVertical = 0;
  private verticalTimes: number[] = [];
  private firstMin = 0;

  constructor(readonly opts: GeneratorOptions) {
    this.rng = new Rng(opts.seed ^ 0x5eed1234);
    const tut = opts.tutorial && !opts.attract;
    const start = tut ? C.tutorialEnd : 0;
    this.course = new Course(start);
    this.nextPowerUp = start + this.rng.range(C.firstPowerUpMin, C.firstPowerUpMax);
    this.firstMin = tut ? C.tutorialEnd : C.firstRowNoTutorial;
    if (tut) this.buildTutorial();
    else this.prevFar = 0;
  }

  /** Tutorial rows (for hint computation). */
  tutorialRows(): readonly RowSpec[] {
    return this.tutorialRowsAll;
  }
  private tutorialRowsAll: RowSpec[] = [];

  next(): RowSpec {
    const t = this.tutorialQueue.shift();
    if (t) {
      this.prev = t;
      this.prevFar = t.far;
      return t;
    }
    return this.generate();
  }

  // -------------------------------------------------------------------------
  private buildTutorial(): void {
    const mk = (s: number, lanes: LaneContent[], cars: number, hint: TutorialHint): RowSpec => {
      const ls = lanes.map((c) => ({ ...emptyLane(), content: c, cars: c === 'train' ? cars : 0, variant: this.rng.int(0, 7) })) as RowSpec['lanes'];
      const row = this.finishRow('tutorial', s, ls, 0, 0, 0);
      row.hint = hint;
      return row;
    };
    const r1 = mk(55, ['low', 'low', 'low'], 0, 'jump');
    this.arcCoins(r1, 0, C.speedMin);
    const r2 = mk(105, ['high', 'high', 'high'], 0, 'duck');
    this.lineCoins(r2.items, 0, 70, 9, COIN_HEIGHT);
    const r3 = mk(160, ['empty', 'train', 'empty'], 2, 'switch');
    this.lineCoins(r3.items, -1, 161, 10, COIN_HEIGHT);
    this.lineCoins(r3.items, 1, 161, 10, COIN_HEIGHT);
    const r4 = mk(225, ['train', 'train', 'empty'], 2, 'right');
    this.lineCoins(r4.items, 1, 226, 10, COIN_HEIGHT);
    const r5 = mk(290, ['empty', 'train', 'train'], 2, 'left');
    this.lineCoins(r5.items, -1, 291, 10, COIN_HEIGHT);
    this.tutorialQueue = [r1, r2, r3, r4, r5];
    this.tutorialRowsAll = [r1, r2, r3, r4, r5];
    this.prevFar = 0;
  }

  private finishRow(pattern: PatternName, s: number, lanes: RowSpec['lanes'], v: number, spacing: number, required: number): RowSpec {
    let far = s;
    let pass = 0;
    let exit = 0;
    let hasRamp = false;
    let vertical = false;
    const speed = v || C.speedMin;
    for (let i = 0; i < 3; i++) {
      const l = lanes[i];
      let f = s;
      switch (l.content) {
        case 'low': f = s + BARRIER_LOW_DEPTH; vertical = true; break;
        case 'high': f = s + BARRIER_HIGH_DEPTH; vertical = true; break;
        case 'train': f = s + l.cars * TRAIN_CAR_LENGTH; break;
        case 'ramp': f = s + RAMP_LENGTH + l.cars * TRAIN_CAR_LENGTH; hasRamp = true; break;
        case 'moving': f = s + (l.cars * TRAIN_CAR_LENGTH * speed) / (speed + l.mspeed); break;
        case 'empty': break;
      }
      far = Math.max(far, f);
      if (isPassContent(l.content)) pass |= 1 << i;
    }
    exit = pass;
    if (hasRamp) for (let i = 0; i < 3; i++) if (lanes[i].content === 'train') exit |= 1 << i;
    return {
      index: this.index++, pattern, s, far, lanes, passMask: pass, exitMask: exit,
      requiredChanges: 0, speed, level: this.course.levelAt(s), spacing, requiredSpacing: required,
      items: [], hint: null, reserveUntil: 0, vertical,
    };
  }

  private pickPattern(level: number, s: number): PatternName {
    const names: PatternName[] = [];
    const ws: number[] = [];
    const reserved = this.reservedUntil.some((r) => r > s);
    const tNow = this.course.timeAt(s);
    let recentVertical = 0;
    for (const t of this.verticalTimes) if (tNow - t < 60) recentVertical++;
    const verticalOk = this.consecutiveVertical < C.maxConsecutiveVertical && recentVertical < C.maxVerticalPerMinute;
    for (const p of PATTERNS) {
      if (level < p.unlock) continue;
      if (p.name === 'moving_train' && (this.opts.attract || reserved)) continue;
      if (!verticalOk && VERTICAL_PATTERNS.has(p.name)) continue;
      names.push(p.name);
      // As the level rises, doubles/mixed crowd out singles.
      let w = p.w;
      if (HARD_PATTERNS.has(p.name)) w *= 1 + C.hardBoost * level;
      else if (SINGLE_PATTERNS.has(p.name)) w *= 1 - C.singleFade * level;
      ws.push(w);
    }
    return this.rng.weighted(names, ws);
  }

  private maxCars(level: number): number {
    return level < 0.12 ? 2 : 3;
  }

  private laneIdx(): number {
    return this.rng.int(0, 2);
  }

  private buildLanes(pattern: PatternName, level: number, open: number): RowSpec['lanes'] {
    const L: RowSpec['lanes'] = [emptyLane(), emptyLane(), emptyLane()];
    for (const l of L) l.variant = this.rng.int(0, 7);
    const mc = this.maxCars(level);
    const cars = (lo = 1, hi = mc): number => this.rng.int(lo, Math.max(lo, hi));
    const others = (i: number): [number, number] => [(i + 1) % 3, (i + 2) % 3];
    switch (pattern) {
      case 'single_low': L[this.laneIdx()].content = 'low'; break;
      case 'double_low': { const [a, b] = others(this.laneIdx()); L[a].content = 'low'; L[b].content = 'low'; break; }
      case 'full_low': for (const l of L) l.content = 'low'; break;
      case 'single_high': {
        const i = this.laneIdx();
        L[i].content = 'high';
        if (this.rng.chance(0.3)) L[(i + 1) % 3].content = 'high';
        break;
      }
      case 'full_high': for (const l of L) l.content = 'high'; break;
      case 'train_single': { const i = this.laneIdx(); L[i].content = 'train'; L[i].cars = cars(); break; }
      case 'train_double':
      case 'zigzag': {
        const o = open >= 0 ? open : this.laneIdx();
        for (const i of others(o)) { L[i].content = 'train'; L[i].cars = pattern === 'zigzag' ? cars(1, 2) : cars(); }
        break;
      }
      case 'ramp_train': {
        const i = this.laneIdx();
        L[i].content = 'ramp'; L[i].cars = cars(2, 3);
        if (this.rng.chance(0.4)) { const j = others(i)[this.rng.int(0, 1)]; L[j].content = 'train'; L[j].cars = cars(); }
        break;
      }
      case 'train_wall_ramp': {
        const i = this.laneIdx();
        for (let j = 0; j < 3; j++) { L[j].content = 'train'; L[j].cars = cars(); }
        L[i].content = 'ramp'; L[i].cars = cars(2, 3);
        break;
      }
      case 'mixed': {
        const i = this.laneIdx();
        const [a, b] = others(i);
        L[i].content = 'train'; L[i].cars = cars();
        const bi = this.rng.chance(0.5) ? a : b;
        L[bi].content = this.rng.chance(0.6) ? 'low' : 'high';
        break;
      }
      case 'moving_train': {
        const i = this.laneIdx();
        L[i].content = 'moving'; L[i].cars = cars(1, 3);
        L[i].mspeed = this.rng.range(C.movingSpeedMin, C.movingSpeedMax);
        if (this.rng.chance(0.5)) { const j = others(i)[this.rng.int(0, 1)]; L[j].content = 'train'; L[j].cars = cars(); }
        break;
      }
      case 'tutorial': break;
    }
    return L;
  }

  private generate(): RowSpec {
    const prevFar = this.prevFar;
    const sEst = prevFar + C.minSpacing;
    const level0 = this.course.levelAt(sEst);
    let pattern: PatternName;
    let open = -1;
    let zig = false;
    if (this.queued) {
      pattern = this.queued.pattern; open = this.queued.open; this.queued = null; zig = true;
    } else {
      pattern = this.pickPattern(level0, sEst);
      if (pattern === 'zigzag') {
        open = this.rng.chance(0.5) ? 0 : 2;
        const nextOpen = this.rng.chance(0.5) ? 1 : 2 - open;
        this.queued = { pattern: 'zigzag', open: nextOpen };
      }
    }
    const lanes = this.buildLanes(pattern, level0, open);
    // Moving-train reservations: keep reserved lanes empty.
    for (let i = 0; i < 3; i++) if (this.reservedUntil[i] > sEst && lanes[i].content !== 'empty') lanes[i] = { ...emptyLane(), variant: lanes[i].variant };

    let pass = 0;
    let movingSpeed = 0;
    for (let i = 0; i < 3; i++) {
      if (isPassContent(lanes[i].content)) pass |= 1 << i;
      if (lanes[i].content === 'moving') movingSpeed = lanes[i].mspeed;
    }
    const k = this.prev ? requiredChanges(this.prev.exitMask, pass) : 0;

    // Same-lane low↔high conflicts with the previous row.
    let lowHigh = false;
    if (this.prev) {
      for (let i = 0; i < 3; i++) {
        const a = this.prev.lanes[i].content;
        const b = lanes[i].content;
        if ((a === 'low' && b === 'high') || (a === 'high' && b === 'low')) lowHigh = true;
      }
    }

    // Spacing: iterate twice so the speed used is the speed at the row itself.
    let s = sEst;
    let v = this.course.speedAt(s);
    let required = C.minSpacing;
    for (let it = 0; it < 3; it++) {
      v = this.course.speedAt(s);
      const level = this.course.levelAt(s);
      const closing = v + movingSpeed;
      let req = Math.max(C.minSpacing, closing * gapSeconds(level));
      if (k > 0) req = Math.max(req, closing * (C.reactTime + C.reactPerExtraLane * (k - 1)) + 2);
      if (lowHigh) req = Math.max(req, v * (C.jumpAirtime + C.rollTime) + C.lowHighExtra);
      if (zig) req = Math.max(req, v * C.zigzagGap);
      required = req;
      s = Math.max(prevFar + req, this.firstMin);
    }

    const row = this.finishRow(pattern, s, lanes, v, s - prevFar, required);
    row.requiredChanges = k;

    // Moving: reserve its lane for the stretch it drives through before meeting the runner.
    for (let i = 0; i < 3; i++) {
      const l = lanes[i];
      if (l.content !== 'moving') continue;
      const lead = this.course.timeAt(s) - this.course.timeAt(Math.max(0, s - C.genLeadMax));
      row.reserveUntil = s + l.mspeed * lead + l.cars * TRAIN_CAR_LENGTH + 10;
      this.reservedUntil[i] = Math.max(this.reservedUntil[i], row.reserveUntil);
    }

    // Vertical density bookkeeping.
    if (row.vertical) {
      this.consecutiveVertical++;
      this.verticalTimes.push(this.course.timeAt(s));
      if (this.verticalTimes.length > 40) this.verticalTimes.shift();
    } else this.consecutiveVertical = 0;

    this.placeItems(row);
    this.prev = row;
    this.prevFar = row.far;
    return row;
  }

  // -------------------------------------------------------------------------
  private lineCoins(items: ItemSpec[], lane: Lane, s0: number, n: number, y: number): void {
    for (let i = 0; i < n; i++) items.push({ kind: 'coin', lane, s: s0 + i * C.coinSpacing, y });
  }

  private arcCoins(row: RowSpec, i: number, v: number): void {
    // Jump starting so the apex is over the barrier.
    const half = C.jumpAirtime / 2;
    const lane = LANE_OF[i];
    for (let k = 1; k <= 6; k++) {
      const t = (k / 7) * C.jumpAirtime;
      const y = JUMP_V0 * t - 0.5 * GRAVITY * t * t;
      row.items.push({ kind: 'coin', lane, s: row.s + 0.2 + (t - half) * v, y: y + COIN_HEIGHT * 0.9 });
    }
  }

  private placeItems(row: RowSpec): void {
    const s = row.s;
    const free: number[] = [];
    const lows: number[] = [];
    let ramp = -1;
    for (let i = 0; i < 3; i++) {
      const c = row.lanes[i].content;
      if (c === 'empty') {
        if (this.reservedUntil[i] <= s - row.spacing) free.push(i);
      } else if (c === 'low') lows.push(i);
      else if (c === 'ramp') ramp = i;
    }
    // Power-up.
    let puLane = -1;
    // Overdue: also accept the run-up to a ramp or a hurdle (collected on the way up / mid-jump).
    const puLanes = free.length > 0 || s < this.nextPowerUp + C.powerUpOverdue ? free : ramp >= 0 ? [ramp] : lows;
    if (s >= this.nextPowerUp && puLanes.length > 0) {
      puLane = puLanes[this.rng.int(0, puLanes.length - 1)];
      const kind = this.rng.weighted(POWERUPS, POWERUP_W);
      row.items.push({ kind, lane: LANE_OF[puLane] as Lane, s: s - 3, y: COIN_HEIGHT });
      this.nextPowerUp = s + this.rng.range(C.powerUpMin, C.powerUpMax);
    }
    if (!this.rng.chance(C.coinChance)) return;
    const len = row.far - s;
    if (ramp >= 0 && this.rng.chance(0.8)) {
      const lane = LANE_OF[ramp] as Lane;
      for (let z = 1.2; z < RAMP_LENGTH; z += C.coinSpacing) {
        row.items.push({ kind: 'coin', lane, s: s + z, y: (TRAIN_HEIGHT * z) / RAMP_LENGTH + COIN_HEIGHT });
      }
      const roofLen = row.lanes[ramp].cars * TRAIN_CAR_LENGTH;
      const n = Math.min(16, Math.floor((roofLen - 2) / C.coinSpacing));
      this.lineCoins(row.items, lane, s + RAMP_LENGTH + 1.2, n, TRAIN_HEIGHT + COIN_HEIGHT);
      return;
    }
    const freeNoPu = free.filter((i) => i !== puLane);
    const useArc = lows.length > 0 && (freeNoPu.length === 0 || this.rng.chance(0.4));
    if (useArc) {
      this.arcCoins(row, lows[this.rng.int(0, lows.length - 1)], row.speed);
      return;
    }
    if (freeNoPu.length === 0) return;
    const i = freeNoPu[this.rng.int(0, freeNoPu.length - 1)];
    const lane = LANE_OF[i] as Lane;
    if (len > 10) {
      const n = Math.min(12, Math.floor(len / C.coinSpacing));
      this.lineCoins(row.items, lane, s + 1, n, COIN_HEIGHT);
    } else {
      const n = Math.min(12, Math.floor((row.spacing - 4) / C.coinSpacing) + 1);
      if (n >= 3) this.lineCoins(row.items, lane, s - 2 - (n - 1) * C.coinSpacing, n, COIN_HEIGHT);
    }
  }
}

export { ALL as ALL_LANES_MASK };
