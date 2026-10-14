// Pure multi-person slot assignment (MASTERPLAN §5.7). No DOM.
//
// Unlocked: the `playerCount` largest-scale candidates are players (incumbents get a small
// bonus so two similar-size people don't flap), assigned to slots left → right.
// Locked: each slot keeps following the nearest candidate (cost = distance/scale + scale
// mismatch), within a gate that widens with the time since the slot was last seen (capped at
// matchMaxDist·scale). A never-filled slot takes the largest unassigned candidate; a slot that
// lost its person (unseen > trackTimeoutMs) only re-acquires someone within
// reacquireMaxDist·scale of its last known center and of similar size.
import type { PlayerCount, PlayerId } from '../shared/types';
import { VISION_CONFIG as C } from './config';
import type { Candidate } from './features';

interface SlotTrack {
  everFilled: boolean;
  x: number;
  y: number;
  scale: number;
  lastSeen: number;
}

export interface AssignResult {
  /** Candidate index per slot, or -1. */
  slots: [number, number];
  /** True when the slot's person is (probably) a different individual than last frame. */
  identityChanged: [boolean, boolean];
  /** Detected people not assigned to a slot. */
  extra: number;
}

const freshTrack = (): SlotTrack => ({ everFilled: false, x: 0.5, y: 0.5, scale: 0, lastSeen: -1e9 });

export class PlayerAssigner {
  private tracks: [SlotTrack, SlotTrack] = [freshTrack(), freshTrack()];
  private _playerCount: PlayerCount = 1;
  private _locked = false;
  private lastT = -1e9;
  private readonly result: AssignResult = { slots: [-1, -1], identityChanged: [false, false], extra: 0 };
  private readonly valid: number[] = [];

  get playerCount(): PlayerCount {
    return this._playerCount;
  }
  get locked(): boolean {
    return this._locked;
  }

  setPlayerCount(n: PlayerCount): void {
    this._playerCount = n;
    if (n === 1) this.tracks[1] = freshTrack();
  }

  /**
   * Lock/unlock. With `order`, new slot i takes the person from old slot order[i]; player
   * count becomes order.length. Returns, per new slot, the old slot it came from (or null).
   */
  lock(locked: boolean, order?: PlayerId[]): [PlayerId | null, PlayerId | null] {
    this._locked = locked;
    if (!locked || !order || order.length === 0) return [0, 1];
    const ord = order.slice(0, 2);
    const old = this.tracks;
    const next: [SlotTrack, SlotTrack] = [freshTrack(), freshTrack()];
    const remap: [PlayerId | null, PlayerId | null] = [null, null];
    ord.forEach((from, i) => {
      next[i] = { ...old[from] };
      remap[i] = from;
    });
    this.tracks = next;
    this._playerCount = ord.length === 2 ? 2 : 1;
    return remap;
  }

  /** Forget all identities (e.g. new camera). */
  reset(): void {
    this.tracks = [freshTrack(), freshTrack()];
  }

  private isValid(c: Candidate): boolean {
    return c.confidence > C.candMinConfidence && c.scale > C.candMinScale && Number.isFinite(c.x);
  }

  private dist(tr: SlotTrack, c: Candidate): number {
    return Math.hypot(c.x - tr.x, c.y - tr.y);
  }

  assign(cands: ReadonlyArray<Candidate>, t: number): AssignResult {
    const r = this.result;
    r.slots[0] = r.slots[1] = -1;
    r.identityChanged[0] = r.identityChanged[1] = false;
    const valid = this.valid;
    valid.length = 0;
    for (let i = 0; i < cands.length; i++) if (this.isValid(cands[i])) valid.push(i);
    const n = this._playerCount;

    if (!this._locked) this.assignUnlocked(cands, t);
    else this.assignLocked(cands, t);

    let assigned = 0;
    for (let s = 0; s < n; s++) {
      const ci = r.slots[s];
      if (ci < 0) continue;
      assigned++;
      const tr = this.tracks[s];
      const c = cands[ci];
      if (!tr.everFilled || t - tr.lastSeen > C.trackTimeoutMs) r.identityChanged[s] = true;
      else if (!this._locked) {
        const gate = this.gate(tr, t);
        if (this.dist(tr, c) > gate * tr.scale || Math.abs(Math.log(c.scale / tr.scale)) > this.scaleGate(tr, t))
          r.identityChanged[s] = true;
      }
      const k = tr.everFilled && !r.identityChanged[s] ? 1 - Math.exp(-(t - tr.lastSeen) / C.trackScaleTauMs) : 1;
      tr.scale += (c.scale - tr.scale) * Math.min(1, k);
      tr.x = c.x;
      tr.y = c.y;
      tr.lastSeen = t;
      tr.everFilled = true;
    }
    // Only plausible people count as extras; low-confidence detections are noise.
    r.extra = Math.max(0, valid.length - assigned);
    this.lastT = t;
    return r;
  }

  private gate(tr: SlotTrack, t: number): number {
    const gapS = Math.max(0, (t - tr.lastSeen) / 1000);
    return Math.min(C.matchMaxDist, C.matchBaseDist + C.matchMaxSpeed * gapS);
  }

  private scaleGate(tr: SlotTrack, t: number): number {
    const gapS = Math.max(0, (t - tr.lastSeen) / 1000);
    return Math.min(C.matchScaleMax, C.matchScaleBase + C.matchScaleRate * gapS);
  }

  private assignUnlocked(cands: ReadonlyArray<Candidate>, t: number): void {
    const n = this._playerCount;
    // Incumbent bonus: candidates that continue a recently seen slot rank higher.
    const ranked = this.valid.map((ci) => {
      const c = cands[ci];
      let eff = c.scale;
      for (let s = 0; s < n; s++) {
        const tr = this.tracks[s];
        if (!tr.everFilled || t - tr.lastSeen > C.unlockedHoldMs) continue;
        if (this.dist(tr, c) < this.gate(tr, t) * tr.scale) {
          eff *= C.unlockedIncumbentBonus;
          break;
        }
      }
      return { ci, eff };
    });
    ranked.sort((a, b) => b.eff - a.eff);
    const chosen = ranked.slice(0, n).map((e) => e.ci);
    chosen.sort((a, b) => cands[a].x - cands[b].x);
    for (let s = 0; s < chosen.length; s++) this.result.slots[s] = chosen[s];
  }

  private assignLocked(cands: ReadonlyArray<Candidate>, t: number): void {
    const n = this._playerCount;
    const r = this.result;
    const used = new Set<number>();
    const pairs: { s: number; ci: number; cost: number }[] = [];
    for (let s = 0; s < n; s++) {
      const tr = this.tracks[s];
      if (!tr.everFilled) continue;
      const lost = t - tr.lastSeen > C.trackTimeoutMs;
      const distGate = (lost ? C.reacquireMaxDist : this.gate(tr, t)) * tr.scale;
      const scaleGate = lost ? C.reacquireMaxScaleLog : this.scaleGate(tr, t);
      for (const ci of this.valid) {
        const c = cands[ci];
        const d = this.dist(tr, c);
        const ls = Math.abs(Math.log(c.scale / tr.scale));
        if (d > distGate || ls > scaleGate) continue;
        pairs.push({ s, ci, cost: d / tr.scale + C.matchScaleWeight * ls });
      }
    }
    pairs.sort((a, b) => a.cost - b.cost);
    for (const p of pairs) {
      if (r.slots[p.s] >= 0 || used.has(p.ci)) continue;
      r.slots[p.s] = p.ci;
      used.add(p.ci);
    }
    // Never-filled slots acquire the largest unassigned candidates (left → right among them).
    const empty: number[] = [];
    for (let s = 0; s < n; s++) if (!this.tracks[s].everFilled && r.slots[s] < 0) empty.push(s);
    if (empty.length === 0) return;
    const free = this.valid.filter((ci) => !used.has(ci));
    free.sort((p, q) => cands[q].scale - cands[p].scale);
    const take = free.slice(0, empty.length).sort((p, q) => cands[p].x - cands[q].x);
    for (let k = 0; k < take.length; k++) {
      r.slots[empty[k]] = take[k];
      used.add(take[k]);
    }
  }
}
