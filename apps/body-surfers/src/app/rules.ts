// Pure decision logic for the app state machine (MASTERPLAN §10.1). No DOM, no
// module instances: plain inputs in, decisions out. Unit-tested in rules.test.ts.
import type { GestureState, PlayerCount, PlayerId, TrackedPerson, VisionStatus } from '../shared/types';
import { APP } from './config';

// ---------------------------------------------------------------------------
// Lobby: hands-up join flow
// ---------------------------------------------------------------------------

export interface JoinPerson {
  readonly slot: PlayerId;
  readonly handsUpProgress: number;
}

export interface JoinStep {
  /** Slots that joined during this update (play `join`; the first join freezes identities). */
  readonly joined: PlayerId[];
  /** Non-null ⇒ start the countdown with these slots (ascending = left → right). */
  start: PlayerId[] | null;
  /** Everyone who had joined walked away ⇒ unlock slots, back to the open lobby. */
  reset: boolean;
}

/**
 * Raise both hands to join. If someone else is visible when the first person joins,
 * they get a join window to race; otherwise the solo run starts immediately.
 */
export class JoinFlow {
  /** Joined slots, ascending. Replaced (never mutated) on change. */
  joined: readonly PlayerId[] = [];
  /** Flow-clock time when the 2P join window closes, or null. */
  windowEndsAt: number | null = null;
  private readonly missingSince: [number, number] = [-1, -1];
  private readonly step: JoinStep = { joined: [], start: null, reset: false };

  constructor(
    readonly windowS: number = APP.joinWindowS,
    readonly dropS: number = APP.joinDropS,
  ) {}

  reset(): void {
    this.joined = [];
    this.windowEndsAt = null;
    this.missingSince[0] = this.missingSince[1] = -1;
  }

  has(slot: PlayerId): boolean {
    return this.joined.includes(slot);
  }

  /** Seconds left in the join window, or null when no window is open. */
  secondsLeft(now: number): number | null {
    return this.windowEndsAt === null ? null : Math.max(0, this.windowEndsAt - now);
  }

  /** `people` in slot order. The returned object is reused: read it before the next call. */
  update(people: readonly JoinPerson[], now: number, maxPlayers: PlayerCount): JoinStep {
    const step = this.step;
    step.joined.length = 0;
    step.start = null;
    step.reset = false;

    // 1. Forget joined people who left the frame for a while.
    if (this.joined.length > 0) {
      let kept: readonly PlayerId[] = this.joined;
      for (const slot of this.joined) {
        if (hasSlot(people, slot)) this.missingSince[slot] = -1;
        else if (this.missingSince[slot] < 0) this.missingSince[slot] = now;
        else if (now - this.missingSince[slot] >= this.dropS) kept = kept.filter((s) => s !== slot);
      }
      if (kept !== this.joined) {
        if (kept.length === 0) {
          this.reset();
          step.reset = true;
          return step;
        }
        this.joined = kept;
      }
    }

    // 2. New joins.
    for (const p of people) {
      if (this.joined.length >= maxPlayers) break;
      if (p.handsUpProgress >= 1 && !this.joined.includes(p.slot)) {
        this.joined = [...this.joined, p.slot].sort((a, b) => a - b);
        this.missingSince[p.slot] = -1;
        step.joined.push(p.slot);
      }
    }
    if (this.joined.length === 0) return step;

    // 3. Start?
    if (this.joined.length >= maxPlayers) {
      step.start = [...this.joined];
    } else if (this.windowEndsAt === null) {
      let othersVisible = false;
      for (const p of people) if (!this.joined.includes(p.slot)) othersVisible = true;
      if (othersVisible) this.windowEndsAt = now + this.windowS;
      else step.start = [...this.joined];
    } else if (now >= this.windowEndsAt) {
      step.start = [...this.joined];
    }
    return step;
  }
}

export function hasSlot(people: readonly { readonly slot: PlayerId }[], slot: PlayerId): boolean {
  for (const p of people) if (p.slot === slot) return true;
  return false;
}

/** Highest hands-up progress among people in slots < count (optionally skipping joined ones). */
export function maxHandsUp(
  people: readonly TrackedPerson[] | null | undefined,
  count: number,
  skip?: (slot: PlayerId) => boolean,
): number {
  let best = 0;
  if (!people) return 0;
  for (const p of people) {
    if (p.slot >= count || (skip && skip(p.slot))) continue;
    if (p.handsUpProgress > best) best = p.handsUpProgress;
  }
  return best > 1 ? 1 : best;
}

// ---------------------------------------------------------------------------
// Countdown: 3-2-1, calibration-gated GO
// ---------------------------------------------------------------------------

export interface CountdownStep {
  /** 3, 2, 1 (also while holding), 0 at GO. */
  value: number;
  /** Value changed this update ⇒ play `tick`. */
  tick: boolean;
  /** Start the run now (fires once). */
  go: boolean;
  /** Countdown reached zero but calibration isn't done: "Stand still…". */
  holding: boolean;
}

export class Countdown {
  elapsed = 0;
  private last = 0;
  private done = false;
  private readonly out: CountdownStep = { value: 3, tick: false, go: false, holding: false };

  constructor(readonly seconds: number = APP.countdownS) {}

  reset(): void {
    this.elapsed = 0;
    this.last = 0;
    this.done = false;
  }

  /** `ready` = every player is calibrated (or calibration is skipped). Result reused per call. */
  update(dt: number, ready: boolean): CountdownStep {
    const o = this.out;
    o.tick = o.go = o.holding = false;
    if (this.done) {
      o.value = 0;
      return o;
    }
    this.elapsed += dt > 0 ? dt : 0;
    if (this.elapsed < this.seconds) {
      o.value = Math.max(1, Math.ceil(this.seconds - this.elapsed));
    } else if (ready) {
      o.value = 0;
      o.go = true;
      this.done = true;
    } else {
      o.value = 1;
      o.holding = true;
    }
    o.tick = !o.go && o.value !== this.last;
    this.last = o.value;
    return o;
  }
}

/** 0..1 calibration of a slot's gesture detector (1 once it is past calibration). */
export function calibrationOf(g: GestureState | null | undefined): number {
  if (!g) return 0;
  if (isCalibrated(g)) return 1;
  const p = g.calibrationProgress;
  return Number.isFinite(p) ? Math.min(1, Math.max(0, p)) : 0;
}

export function isCalibrated(g: GestureState | null | undefined): boolean {
  return !!g && (g.phase === 'ready' || g.phase === 'airborne' || g.phase === 'ducking');
}

// ---------------------------------------------------------------------------
// Paused → resume
// ---------------------------------------------------------------------------

export interface ResumeStep {
  /** Seconds until play resumes, or null while waiting. */
  resumeIn: number | null;
  tick: boolean;
  resume: boolean;
}

/**
 * tracking-lost: everyone back in view for `backS` → `countdownS` countdown → resume;
 * anyone disappearing cancels it. user: trigger() (pause key / hands-up) → countdown → resume.
 */
export class ResumeGate {
  resumeIn: number | null = null;
  private visibleFor = 0;
  private last = 0;
  private readonly out: ResumeStep = { resumeIn: null, tick: false, resume: false };

  constructor(
    readonly countdownS: number = APP.resumeCountdownS,
    readonly backS: number = APP.trackingBackS,
  ) {}

  reset(): void {
    this.resumeIn = null;
    this.visibleFor = 0;
    this.last = 0;
  }

  trigger(): void {
    if (this.resumeIn === null) {
      this.resumeIn = this.countdownS;
      this.last = 0;
    }
  }

  /** `allVisible`: tracking-lost mode; null: user mode (no visibility requirement). Result reused. */
  update(dt: number, allVisible: boolean | null): ResumeStep {
    const o = this.out;
    o.tick = o.resume = false;
    if (allVisible !== null) {
      if (!allVisible) {
        this.reset();
        o.resumeIn = null;
        return o;
      }
      this.visibleFor += dt;
      if (this.resumeIn === null && this.visibleFor >= this.backS) this.trigger();
    }
    if (this.resumeIn === null) {
      o.resumeIn = null;
      return o;
    }
    this.resumeIn -= dt;
    if (this.resumeIn <= 0) {
      this.reset();
      o.resumeIn = 0;
      o.resume = true;
      return o;
    }
    const whole = Math.ceil(this.resumeIn);
    o.tick = whole !== this.last;
    this.last = whole;
    o.resumeIn = this.resumeIn;
    return o;
  }
}

// ---------------------------------------------------------------------------
// 2P winner, presence, gameover
// ---------------------------------------------------------------------------

/**
 * Last runner standing. `crashed` = players who crashed this frame, `alive` = flags after it.
 * Once decided the winner never changes. Simultaneous crashes: higher score wins (tie → none).
 */
export function decideWinner(
  count: PlayerCount,
  current: PlayerId | null,
  crashed: readonly PlayerId[],
  alive: readonly boolean[],
  scores: readonly number[],
): PlayerId | null {
  if (count < 2 || current !== null || crashed.length === 0) return current;
  const a0 = !!alive[0];
  const a1 = !!alive[1];
  if (a0 && !a1) return 0;
  if (a1 && !a0) return 1;
  if (!a0 && !a1) {
    const s0 = scores[0] ?? 0;
    const s1 = scores[1] ?? 0;
    return s0 > s1 ? 0 : s1 > s0 ? 1 : null;
  }
  return null;
}

export interface Presence {
  alive: boolean;
  /** Seen by the camera at least once this session (keyboard players in camera mode never are). */
  seen: boolean;
  visible: boolean;
  /** Flow-clock time last seen. */
  lastSeen: number;
}

/** Alive, camera-tracked players missing for longer than `thresholdS` (→ tracking-lost pause). */
export function missingPlayers(players: readonly Presence[], count: number, now: number, thresholdS: number): PlayerId[] {
  const out: PlayerId[] = [];
  for (let i = 0; i < count; i++) {
    const p = players[i];
    if (p.alive && p.seen && now - p.lastSeen > thresholdS) out.push(i as PlayerId);
  }
  return out;
}

/** Alive, camera-tracked players not visible right now (paused screen list / resume gate). */
export function invisiblePlayers(players: readonly Presence[], count: number): PlayerId[] {
  const out: PlayerId[] = [];
  for (let i = 0; i < count; i++) {
    const p = players[i];
    if (p.alive && p.seen && !p.visible) out.push(i as PlayerId);
  }
  return out;
}

/** Players seen within `windowS` (who restarts after gameover); everyone if nobody was. */
export function presentPlayers(players: readonly Presence[], count: number, now: number, windowS: number): PlayerId[] {
  const out: PlayerId[] = [];
  for (let i = 0; i < count; i++) if (players[i].seen && now - players[i].lastSeen <= windowS) out.push(i as PlayerId);
  if (out.length > 0) return out;
  for (let i = 0; i < count; i++) out.push(i as PlayerId);
  return out;
}

export function restartLockoutLeft(sinceGameover: number, lockoutS: number = APP.restartLockoutS): number | null {
  const left = lockoutS - sinceGameover;
  return left > 0 ? left : null;
}

export interface GameoverIdleInput {
  /** Seconds since the gameover screen appeared. */
  since: number;
  /** Seconds since the last keyboard/UI/hands-up activity. */
  idleFor: number;
  /** Camera-tracked session: seconds since ANY player was last visible (counted from gameover), else null. */
  absentFor: number | null;
}

/** Gameover → lobby when everybody left (camera) or nobody did anything for a long time. */
export function gameoverShouldExit(i: GameoverIdleInput): boolean {
  if (i.idleFor >= APP.gameoverIdleS) return true;
  return i.absentFor !== null && i.since >= APP.restartLockoutS && i.absentFor >= APP.gameoverAbsentS;
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

/** Loading-bar value from the vision status (the model loader exposes no byte progress). */
export function bootProgress(status: VisionStatus, loadingFor: number): number {
  switch (status) {
    case 'idle':
      return 0.05;
    case 'loading-model':
      return 0.1 + 0.8 * (1 - Math.exp(-Math.max(0, loadingFor) / 1.5));
    default:
      return 1;
  }
}

function errorName(err: unknown): string {
  return typeof err === 'object' && err !== null && 'name' in err ? String((err as { name: unknown }).name) : '';
}

function errorText(err: unknown): string {
  if (typeof err === 'string') return err;
  if (typeof err === 'object' && err !== null && 'message' in err) return String((err as { message: unknown }).message);
  return '';
}

/** Friendly text for a failed getUserMedia / startCamera. */
export function cameraErrorMessage(err: unknown, visionError: string | null = null): string {
  const name = errorName(err);
  const text = `${errorText(err)} ${visionError ?? ''}`;
  if (/NotAllowed|Permission|denied|Security/i.test(name) || /permission|denied|not allowed/i.test(text)) {
    return 'Camera access is blocked. Click the camera icon in the address bar, allow it, then try again — or play with the keyboard.';
  }
  if (/NotFound|DevicesNotFound|Overconstrained/i.test(name) || /not found|no camera|no device/i.test(text)) {
    return 'No camera found. Plug one in (or pick another in settings) and try again — or play with the keyboard.';
  }
  if (/NotReadable|TrackStart|Abort/i.test(name) || /in use|busy|could not start|not readable/i.test(text)) {
    return 'The camera is busy. Close other apps using it (Zoom, FaceTime, Meet…) and try again.';
  }
  const detail = (visionError ?? errorText(err)).trim();
  return detail ? `Couldn't start the camera (${detail}).` : "Couldn't start the camera.";
}

export function modelErrorMessage(err: unknown, visionError: string | null = null): string {
  const detail = (visionError ?? errorText(err)).trim();
  return `Couldn't load the body-tracking model${detail ? ` (${detail})` : ''}. Reload to retry — or play with the keyboard.`;
}
