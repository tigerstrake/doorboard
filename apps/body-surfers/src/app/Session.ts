// One race: the player worlds (same seed), their autopilots, fixed-step
// simulation, and per-player run bookkeeping (crash, selfie, camera presence).
import { SIM_DT } from '../shared/constants';
import type {
  ActionEvent, GameEvent, IAutoPilot, IWorld, Lane, PlayerCount, PlayerId, WorldSnapshot,
} from '../shared/types';
import { FixedStepper } from './loop';
import type { Presence } from './rules';

export interface PlayerRun extends Presence {
  /** Flow-clock time of the crash, or null while running. */
  crashedAt: number | null;
  /** Crash selfie (JPEG data URL). */
  photo: string | null;
  /** Next selfie capture attempt (flow clock), or null when done / not needed. */
  selfieAt: number | null;
  /** Stop retrying a null selfie after this time. */
  selfieUntil: number;
}

const freshRun = (): PlayerRun => ({
  alive: true,
  seen: false,
  visible: false,
  lastSeen: Number.NEGATIVE_INFINITY,
  crashedAt: null,
  photo: null,
  selfieAt: null,
  selfieUntil: 0,
});

const NO_EVENTS: GameEvent[] = [];

export class Session {
  /** Both player worlds exist for the app's lifetime; the first `count` are in use. */
  readonly worlds: readonly [IWorld, IWorld];
  readonly players: [PlayerRun, PlayerRun] = [freshRun(), freshRun()];
  count: PlayerCount = 1;
  seed = 0;
  autopilot = false;
  /** begin() has been called since the last end(). */
  active = false;
  /** Latest snapshot per active player (refreshed by collect()). Don't hold across frames. */
  readonly snaps: WorldSnapshot[] = [];
  /** Events drained by the latest collect(), per player. */
  readonly events: GameEvent[][] = [NO_EVENTS, NO_EVENTS];
  /** 2P: last runner standing, once decided. */
  winner: PlayerId | null = null;
  /** Players who crashed, ascending. Replaced on change. */
  out: readonly PlayerId[] = [];
  /** Flow-clock time when the last runner died, or null. */
  allDeadAt: number | null = null;

  private readonly pilots: [IAutoPilot | null, IAutoPilot | null] = [null, null];
  private readonly stepper = new FixedStepper();

  constructor(
    createWorld: (id: PlayerId) => IWorld,
    private readonly createPilot: () => IAutoPilot,
  ) {
    this.worlds = [createWorld(0), createWorld(1)];
  }

  /** Fresh run(s) in 'ready' status: same seed for every racer. */
  begin(count: PlayerCount, seed: number, tutorial: boolean, autopilot: boolean): void {
    this.count = count;
    this.seed = seed;
    this.autopilot = autopilot;
    this.active = true;
    for (let i = 0; i < 2; i++) {
      this.players[i] = freshRun();
      this.pilots[i] = null;
      this.events[i] = NO_EVENTS;
    }
    for (let i = 0; i < count; i++) {
      this.worlds[i].reset({ seed, tutorial });
      // Fresh autopilot per run: it may keep timers keyed to snapshot.time.
      if (autopilot) this.pilots[i] = this.createPilot();
    }
    this.winner = null;
    this.out = [];
    this.allDeadAt = null;
    this.stepper.reset();
    this.collect();
  }

  end(): void {
    this.active = false;
  }

  /** GO: 'ready' → 'running'. */
  start(): void {
    for (let i = 0; i < this.count; i++) this.worlds[i].start();
    this.stepper.reset();
  }

  /** Drop accumulated sim time (after a pause) so resuming doesn't burst. */
  resetClock(): void {
    this.stepper.reset();
  }

  isActive(i: number): i is PlayerId {
    return i >= 0 && i < this.count;
  }

  anyAlive(): boolean {
    for (let i = 0; i < this.count; i++) if (this.players[i].alive) return true;
    return false;
  }

  /** Human input → worlds. Ignored while the AI drives, and for crashed players. */
  route(actions: readonly ActionEvent[]): void {
    if (this.autopilot) return;
    for (const e of actions) {
      const i = e.playerId;
      if (this.isActive(i) && this.players[i].alive) this.worlds[i].handleAction(e.action);
    }
  }

  /** Absolute lane from the body position (GO / resume). */
  applyLane(i: PlayerId, lane: Lane): void {
    if (this.isActive(i) && this.players[i].alive && !this.autopilot) this.worlds[i].handleAction({ type: 'lane', lane });
  }

  drivePilots(): void {
    for (let i = 0; i < this.count; i++) {
      const pilot = this.pilots[i];
      if (!pilot || !this.players[i].alive) continue;
      const w = this.worlds[i];
      const snap = w.snapshot();
      if (snap.status !== 'running') continue;
      const a = pilot.decide(snap);
      if (a) w.handleAction(a);
    }
  }

  /** Fixed-step simulation of every active world (dead ones keep animating). */
  advance(dt: number): void {
    const n = this.stepper.advance(dt);
    for (let k = 0; k < n; k++) for (let i = 0; i < this.count; i++) this.worlds[i].step(SIM_DT);
  }

  /** Refresh snapshots and drain events (call once per frame, after advance). */
  collect(): void {
    this.snaps.length = this.count;
    for (let i = 0; i < this.count; i++) {
      const w = this.worlds[i];
      this.snaps[i] = w.snapshot();
      this.events[i] = w.drainEvents();
    }
  }

  /** Mark a crash; true when it is new. */
  markCrashed(i: PlayerId, now: number, selfie: { delay: number; retry: number } | null): boolean {
    const p = this.players[i];
    if (!p.alive) return false;
    p.alive = false;
    p.crashedAt = now;
    if (selfie) {
      p.selfieAt = now + selfie.delay;
      p.selfieUntil = p.selfieAt + selfie.retry;
    }
    const out: PlayerId[] = [];
    for (let k = 0; k < this.count; k++) if (!this.players[k].alive) out.push(k as PlayerId);
    this.out = out;
    if (!this.anyAlive() && this.allDeadAt === null) this.allDeadAt = now;
    return true;
  }
}
