// Deterministic look-ahead AI runner (attract mode + soak tests). MASTERPLAN §4.9.
import { LANE_WIDTH, PLAYER_DEPTH, TRAIN_HEIGHT } from '../shared/constants';
import type { IAutoPilot, ObstacleState, PlayerAction, WorldSnapshot } from '../shared/types';
import { CONFIG as C } from './config';

const HORIZON = 2.8;
const MOVE_INTERVAL = 0.12;
const HALF_D = PLAYER_DEPTH / 2;

export class AutoPilot implements IAutoPilot {
  /**
   * @param lag Seconds between decide() and the action reaching the world (default 0).
   *   Soak tests use it to model camera players (input + body latency): the pilot
   *   then acts that much earlier and never repeats a jump/duck inside the window.
   */
  constructor(private readonly lag = 0) {}
  private lastMove = -99;
  private lastVertical = -99;
  private planned = 0;
  private plannedUntil = -99;
  private lastTime = -1;
  private blockT = [0, 0, 0];
  private entry = [false, false, false];
  private coinsAhead = [0, 0, 0];
  private barrierT = [0, 0, 0];
  /** Seconds of guaranteed protection (star / invulnerability / jetpack flight + landing). */
  private safeT = 0;

  private protection(s: WorldSnapshot): number {
    let t = s.player.invulnerable;
    for (const a of s.powerUps) if (a.kind === 'jetpack') t = Math.max(t, a.remaining + C.jetpackLandInvuln);
    return t - 0.15;
  }

  /** Obstacle is fully behind us before protection runs out ⇒ ignore it. */
  private covered(o: ObstacleState, p: number, v: number): boolean {
    if (this.safeT <= 0) return false;
    return (o.s + o.length + HALF_D - p) / (v + o.speed) < this.safeT;
  }

  decide(s: WorldSnapshot): PlayerAction | null {
    if (s.status !== 'running' || !s.player.alive) return null;
    if (s.time < this.lastTime) { this.lastMove = -99; this.lastVertical = -99; this.plannedUntil = -99; } // new run
    this.lastTime = s.time;
    this.safeT = this.protection(s);
    const v = this.vertical(s);
    if (v) return v;
    return this.lateral(s);
  }

  private vertical(s: WorldSnapshot): PlayerAction | null {
    const pl = s.player;
    if (pl.y > 1.0) return null; // on a roof/ramp or high in the air: barriers can't touch us
    if (s.powerUps.some((a) => a.kind === 'jetpack')) return null; // flying: jump/duck do nothing
    const p = s.distance;
    const v = s.speed;
    for (const o of s.obstacles) {
      if (o.kind !== 'barrier_low' && o.kind !== 'barrier_high') continue;
      if (this.covered(o, p, v)) continue;
      // Any lane we are in, heading to, or overlapping laterally.
      if (o.lane !== pl.lane && Math.abs(pl.x - o.lane * LANE_WIDTH) > LANE_WIDTH * 0.75) continue;
      const d = o.s - (p + HALF_D);
      if (d < -(o.length + 0.5)) continue;
      if (o.kind === 'barrier_low') {
        if (d <= v * (0.28 + this.lag) && (pl.grounded || this.lag > 0) && this.verticalReady(s)) return { type: 'jump' };
      } else if (d <= v * (0.3 + this.lag) && !pl.rolling && this.verticalReady(s)) return { type: 'duck' };
    }
    return null;
  }

  private verticalReady(s: WorldSnapshot): boolean {
    if (this.lag > 0 && s.time - this.lastVertical < this.lag + 0.3) return false;
    this.lastVertical = s.time;
    return true;
  }

  private lateral(s: WorldSnapshot): PlayerAction | null {
    const pl = s.player;
    const p = s.distance;
    const v = s.speed;
    const high = pl.y >= TRAIN_HEIGHT - C.roofSnap;
    const blockT = this.blockT;
    const entry = this.entry;
    const coins = this.coinsAhead;
    const barrier = this.barrierT;
    for (let i = 0; i < 3; i++) { blockT[i] = Infinity; entry[i] = false; coins[i] = 0; barrier[i] = Infinity; }
    const entryLook = v * (C.laneTime * 0.7 + this.lag) + 0.6;
    const ci = this.lag > 0 && s.time < this.plannedUntil ? this.planned : pl.lane + 1;

    for (const o of s.obstacles) {
      if (this.covered(o, p, v)) continue;
      if (o.kind === 'barrier_low' || o.kind === 'barrier_high') {
        const d = o.s - (p + HALF_D);
        if (d > -1 && d < v * (0.75 + this.lag)) barrier[o.lane + 1] = Math.min(barrier[o.lane + 1], d / v - this.lag);
        continue;
      }
      if (o.kind !== 'train') continue;
      const far = o.s + o.length;
      if (far < p - HALF_D - 0.2) continue;
      const i = o.lane + 1;
      if (o.s <= p + HALF_D + entryLook + (o.moving ? o.speed * 0.3 : 0)) {
        if (!high) entry[i] = true;
        continue;
      }
      // Ramp directly in front of this train?
      let ramp = -1;
      if (!o.moving) {
        for (const r of s.obstacles) {
          if (r.kind === 'ramp' && r.lane === o.lane && Math.abs(r.s + r.length - o.s) < 0.5) { ramp = r.s; break; }
        }
      }
      if (ramp >= 0) {
        // Passable if we can still enter the lane before the ramp rises too high.
        if (i !== ci && !high && ramp + 0.9 < p + HALF_D + entryLook) entry[i] = true;
        continue;
      }
      const t = (o.s - (p + HALF_D)) / (v + o.speed) - this.lag;
      if (t < blockT[i]) blockT[i] = t;
    }
    const coinWin = p + v * 1.5;
    for (const c of s.collectibles) {
      if (c.s > p && c.s < coinWin) coins[c.lane + 1] += c.kind === 'coin' ? 1 : 4;
    }

    const val = (i: number): number => Math.min(blockT[i], HORIZON);
    let best = ci;
    let bestVal = val(ci);
    if (bestVal < HORIZON) {
      for (let t = 0; t < 3; t++) {
        if (t === ci || !this.reachable(ci, t)) continue;
        const tv = val(t) - 0.05 * Math.abs(t - ci);
        if (tv > bestVal + 0.15) { best = t; bestVal = tv; }
      }
    } else if (coins[ci] === 0) {
      // Safe: drift toward coins if an adjacent lane is equally safe.
      for (const t of [ci - 1, ci + 1]) {
        if (t < 0 || t > 2 || coins[t] === 0 || val(t) < HORIZON || !this.reachable(ci, t)) continue;
        if (best === ci || coins[t] > coins[best]) best = t;
      }
    }

    if (best === ci) {
      if (pl.blockedDir !== 0 && s.time >= this.plannedUntil) return { type: 'lane', lane: pl.lane };
      return null;
    }
    const dir = (best > ci ? 1 : -1) as -1 | 1;
    if (pl.blockedDir === dir) return null; // world is waiting to perform it
    if (s.time - this.lastMove < MOVE_INTERVAL) return null;
    this.lastMove = s.time;
    this.planned = ci + dir;
    this.plannedUntil = s.time + this.lag + C.laneTime + 0.05;
    return { type: 'move', dir };
  }

  private reachable(from: number, to: number): boolean {
    const dir = to > from ? 1 : -1;
    for (let j = from + dir, k = 1; ; j += dir, k++) {
      if (this.entry[j]) return false;
      // Don't arrive in a lane right on top of a barrier we couldn't react to.
      if (this.barrierT[j] < k * C.laneTime + 0.45) return false;
      if (this.blockT[j] <= k * C.laneTime + 0.25) return false;
      if (j === to) return true;
    }
  }
}

