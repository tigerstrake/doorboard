// Booth leaderboard: ranking (pure) + a store persisted as a PLAIN JSON ARRAY of
// LeaderboardEntry, best first, ≤ MAX_LEADERBOARD (hall.html reads the key directly
// and listens to 'storage' events, so the whole array is written on every change).
import { MAX_LEADERBOARD, STORAGE_KEYS } from '../shared/constants';
import type { LeaderboardEntry } from '../shared/types';
import { isRecord, readJson, writeJson, type StorageLike } from './storage';

export const MAX_NAME_LENGTH = 18;
export const FALLBACK_NAME = 'Mystery Runner';

/** Trim, drop control characters, collapse whitespace, cap length (code-point safe). */
export function sanitizeName(raw: unknown, fallback: string = FALLBACK_NAME): string {
  if (typeof raw !== 'string') return fallback;
  // eslint-disable-next-line no-control-regex
  const flat = raw.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim();
  const cut = Array.from(flat).slice(0, MAX_NAME_LENGTH).join('').trim();
  return cut.length > 0 ? cut : fallback;
}

const finite = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** A stored value → a valid entry, or null when it is unusable. */
export function sanitizeEntry(raw: unknown): LeaderboardEntry | null {
  if (!isRecord(raw)) return null;
  const score = finite(raw.score);
  if (score === null || score < 0) return null;
  if (typeof raw.id !== 'string' || raw.id === '') return null;
  const coins = finite(raw.coins);
  const distance = finite(raw.distance);
  const date = finite(raw.date);
  return {
    id: raw.id,
    name: sanitizeName(raw.name),
    score: Math.floor(score),
    coins: coins !== null && coins > 0 ? Math.floor(coins) : 0,
    distance: distance !== null && distance > 0 ? distance : 0,
    date: date ?? 0,
    photo: typeof raw.photo === 'string' && raw.photo.startsWith('data:image/') ? raw.photo : null,
    mode: raw.mode === 2 ? 2 : 1,
  };
}

/** Best first: higher score; ties → earlier date keeps the higher rank; then id for stability. */
export function compareEntries(a: LeaderboardEntry, b: LeaderboardEntry): number {
  if (b.score !== a.score) return b.score - a.score;
  if (a.date !== b.date) return a.date - b.date;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

export function sanitizeLeaderboard(raw: unknown, max: number = MAX_LEADERBOARD): LeaderboardEntry[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: LeaderboardEntry[] = [];
  for (const r of raw) {
    const e = sanitizeEntry(r);
    if (e && !seen.has(e.id)) {
      seen.add(e.id);
      out.push(e);
    }
  }
  return out.sort(compareEntries).slice(0, max);
}

/** Would this score make the board? (Ties with the last place lose: the earlier run keeps it.) */
export function qualifies(entries: readonly LeaderboardEntry[], score: number, max: number = MAX_LEADERBOARD): boolean {
  if (!(score > 0)) return false;
  if (entries.length < max) return true;
  return score > entries[entries.length - 1].score;
}

/**
 * Insert several entries at once (2P inserts both racers) and return the new board plus the
 * FINAL 1-based rank of every added entry that survived the cut. Zero scores are never added.
 */
export function insertEntries(
  entries: readonly LeaderboardEntry[],
  added: readonly LeaderboardEntry[],
  max: number = MAX_LEADERBOARD,
): { entries: LeaderboardEntry[]; ranks: Map<string, number> } {
  const merged = [...entries, ...added.filter((e) => e.score > 0)].sort(compareEntries).slice(0, max);
  const ranks = new Map<string, number>();
  for (const a of added) {
    const i = merged.findIndex((e) => e.id === a.id);
    if (i >= 0) ranks.set(a.id, i + 1);
  }
  return { entries: merged, ranks };
}

export function newEntryId(random: () => number = Math.random, now: number = Date.now()): string {
  return `${now.toString(36)}-${Math.floor(random() * 0x7fffffff).toString(36)}`;
}

const withoutPhoto = (e: LeaderboardEntry): LeaderboardEntry => (e.photo ? { ...e, photo: null } : e);

export class LeaderboardStore {
  private list: LeaderboardEntry[];
  /** The last write failed entirely: memory is ahead of storage, so don't re-read over it. */
  private unsaved = false;

  constructor(
    private readonly storage: StorageLike,
    private readonly key: string = STORAGE_KEYS.leaderboard,
    private readonly max: number = MAX_LEADERBOARD,
  ) {
    this.list = sanitizeLeaderboard(readJson(storage, key), max);
  }

  /** Best first. The array is replaced (never mutated) on change, so it is safe to diff by reference. */
  get entries(): LeaderboardEntry[] {
    return this.list;
  }

  /** Insert entries; returns final ranks for the ones that made the board. */
  add(added: readonly LeaderboardEntry[]): Map<string, number> {
    this.sync();
    const r = insertEntries(this.list, added, this.max);
    if (r.ranks.size > 0) {
      this.list = r.entries;
      this.persist();
    }
    return r.ranks;
  }

  rename(id: string, name: string): boolean {
    this.sync();
    const i = this.list.findIndex((e) => e.id === id);
    if (i < 0) return false;
    const clean = sanitizeName(name, this.list[i].name);
    if (clean !== this.list[i].name) {
      this.list = this.list.map((e, k) => (k === i ? { ...e, name: clean } : e));
      this.persist();
    }
    return true;
  }

  /** Attach a late crash selfie to an entry (rewrites the stored array). */
  setPhoto(id: string, photo: string): boolean {
    if (!photo.startsWith('data:image/')) return false;
    this.sync();
    const i = this.list.findIndex((e) => e.id === id);
    if (i < 0) return false;
    this.list = this.list.map((e, k) => (k === i ? { ...e, photo } : e));
    this.persist();
    return true;
  }

  clear(): void {
    this.list = [];
    this.persist();
  }

  /** Drop entries (and their photos) dated before `cutoffMs` (epoch ms). Door retention, ADR-0049. */
  retainSince(cutoffMs: number): void {
    this.sync();
    const kept = this.list.filter((e) => e.date >= cutoffMs);
    if (kept.length === this.list.length) return;
    this.list = kept;
    this.persist();
  }

  /** Re-read from storage (e.g. another tab changed it). Returns true when the list changed. */
  reload(): boolean {
    const before = this.list;
    this.sync();
    return this.list !== before;
  }

  /**
   * Read-modify-write: pick up other tabs' changes before writing, so a stale tab never
   * overwrites them. Selfies that only fit in memory (quota) are kept for matching entries.
   */
  private sync(): void {
    if (this.unsaved) return;
    const stored = sanitizeLeaderboard(readJson(this.storage, this.key), this.max);
    const photos = new Map<string, string>();
    for (const e of this.list) if (e.photo) photos.set(e.id, e.photo);
    const next = stored.map((e) => (!e.photo && photos.has(e.id) ? { ...e, photo: photos.get(e.id)! } : e));
    if (!sameEntries(next, this.list)) this.list = next;
  }

  private persist(): void {
    this.unsaved = false;
    if (writeJson(this.storage, this.key, this.list)) return;
    // Quota exceeded — the selfies are the bulk. Keep them in memory for this session but
    // store fewer: first drop the bottom half's photos, then all of them.
    const keep = Math.ceil(this.list.length / 2);
    if (writeJson(this.storage, this.key, this.list.map((e, i) => (i < keep ? e : withoutPhoto(e))))) return;
    if (!writeJson(this.storage, this.key, this.list.map(withoutPhoto))) {
      this.unsaved = true;
      console.warn('[leaderboard] could not save (storage full or disabled); kept in memory for this session');
    }
  }
}

function sameEntries(a: readonly LeaderboardEntry[], b: readonly LeaderboardEntry[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (x.id !== y.id || x.name !== y.name || x.score !== y.score || x.photo !== y.photo) return false;
  }
  return true;
}
