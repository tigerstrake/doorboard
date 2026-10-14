// Storage abstraction so the settings/leaderboard stores are unit-testable and
// survive browsers where localStorage throws (privacy modes, sandboxed frames).

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export class MemoryStorage implements StorageLike {
  private readonly map = new Map<string, string>();

  getItem(key: string): string | null {
    const v = this.map.get(key);
    return v === undefined ? null : v;
  }

  setItem(key: string, value: string): void {
    this.map.set(key, String(value));
  }

  removeItem(key: string): void {
    this.map.delete(key);
  }
}

/** window.localStorage when it is usable, otherwise an in-memory fallback (nothing persists). */
export function browserStorage(): StorageLike {
  try {
    const s = globalThis.localStorage;
    if (s) {
      const probe = '__bodysurfers_probe__';
      s.setItem(probe, '1');
      s.removeItem(probe);
      return s;
    }
  } catch {
    // fall through
  }
  return new MemoryStorage();
}

/** Parsed JSON at `key`, or undefined when missing / unreadable / corrupt. */
export function readJson(storage: StorageLike, key: string): unknown {
  try {
    const raw = storage.getItem(key);
    if (raw === null) return undefined;
    return JSON.parse(raw) as unknown;
  } catch {
    return undefined;
  }
}

/** false when the write failed (quota exceeded, storage disabled, …). */
export function writeJson(storage: StorageLike, key: string, value: unknown): boolean {
  try {
    storage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

export const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);
