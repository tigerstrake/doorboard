/**
 * Minimal in-memory stand-in for the Upstash client.
 *
 * Only the operations `lib/store.ts` actually uses, and it records **every byte
 * written** so P-13 can byte-scan the store for sentinels and prove the relay
 * never held plaintext.
 */
export interface FakeRedisEntry {
  value: string;
  expiresAtMs: number | null;
}

export class FakeRedis {
  readonly strings = new Map<string, FakeRedisEntry>();
  readonly zsets = new Map<string, Map<string, number>>();
  /** Everything ever written, for the sentinel scan. */
  readonly writeLog: string[] = [];

  private live(key: string): FakeRedisEntry | null {
    const entry = this.strings.get(key);
    if (!entry) return null;
    if (entry.expiresAtMs !== null && entry.expiresAtMs <= Date.now()) {
      this.strings.delete(key);
      return null;
    }
    return entry;
  }

  async set(key: string, value: string, options?: { ex?: number }): Promise<void> {
    this.writeLog.push(`${key}=${value}`);
    this.strings.set(key, {
      value,
      expiresAtMs: options?.ex ? Date.now() + options.ex * 1000 : null,
    });
  }

  async get(key: string): Promise<string | null> {
    return this.live(key)?.value ?? null;
  }

  async del(key: string): Promise<void> {
    this.strings.delete(key);
  }

  async incr(key: string): Promise<number> {
    const next = Number(this.live(key)?.value ?? "0") + 1;
    this.strings.set(key, { value: String(next), expiresAtMs: this.strings.get(key)?.expiresAtMs ?? null });
    return next;
  }

  async expire(key: string, seconds: number): Promise<void> {
    const entry = this.strings.get(key);
    if (entry) entry.expiresAtMs = Date.now() + seconds * 1000;
  }

  async zadd(key: string, member: { score: number; member: string }): Promise<void> {
    this.writeLog.push(`${key}+${member.member}`);
    const set = this.zsets.get(key) ?? new Map<string, number>();
    set.set(member.member, member.score);
    this.zsets.set(key, set);
  }

  async zrange<T>(
    key: string,
    min: number,
    max: number,
    options?: { byScore?: boolean; offset?: number; count?: number },
  ): Promise<T> {
    const set = this.zsets.get(key) ?? new Map<string, number>();
    let members = [...set.entries()].sort((a, b) => a[1] - b[1]);
    if (options?.byScore) members = members.filter(([, score]) => score >= min && score <= max);
    const offset = options?.offset ?? 0;
    const count = options?.count ?? members.length;
    return members.slice(offset, offset + count).map(([member]) => member) as T;
  }

  async zrem(key: string, member: string): Promise<void> {
    this.zsets.get(key)?.delete(member);
  }

  async zcard(key: string): Promise<number> {
    return this.zsets.get(key)?.size ?? 0;
  }

  /** Every string value plus every sorted-set member, for byte scanning. */
  dumpAll(): string {
    const parts = [...this.writeLog];
    for (const [key, entry] of this.strings) parts.push(`${key}=${entry.value}`);
    for (const [key, set] of this.zsets) parts.push(`${key}=${[...set.keys()].join(",")}`);
    return parts.join("\n");
  }
}
