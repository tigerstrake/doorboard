import { describe, expect, it } from 'vitest';
import { LeaderboardStore, insertEntries, qualifies, sanitizeLeaderboard, sanitizeName } from './leaderboard';
import { SettingsStore, applyParamOverrides, mergeSettings, sanitizeSettings } from './settings';
import { MemoryStorage } from './storage';
import { DEFAULT_SETTINGS, DEFAULT_VISION_TUNING, MAX_LEADERBOARD, STORAGE_KEYS } from '../shared/constants';
import type { LeaderboardEntry } from '../shared/types';

const entry = (id: string, score: number, date = 0): LeaderboardEntry => ({
  id, name: id, score, coins: 1, distance: 10, date, photo: null, mode: 1,
});

describe('leaderboard', () => {
  it('stores a plain JSON array, best first, capped', () => {
    const st = new MemoryStorage();
    const lb = new LeaderboardStore(st);
    for (let i = 0; i < 12; i++) lb.add([entry(`e${i}`, i * 10 + 5, i)]);
    const raw = JSON.parse(st.getItem(STORAGE_KEYS.leaderboard) ?? 'null') as LeaderboardEntry[];
    expect(Array.isArray(raw)).toBe(true);
    expect(raw.length).toBe(MAX_LEADERBOARD);
    expect(raw[0].score).toBe(115);
    expect(new LeaderboardStore(st).entries.map((e) => e.id)).toEqual(raw.map((e) => e.id));
  });
  it('ranks and zero scores', () => {
    const r = insertEntries([entry('a', 100), entry('b', 50)], [entry('c', 75), entry('z', 0)]);
    expect(r.ranks.get('c')).toBe(2);
    expect(r.ranks.has('z')).toBe(false);
    expect(qualifies([], 0)).toBe(false);
  });
  it('tolerates corrupt storage, renames, clears', () => {
    const st = new MemoryStorage();
    st.setItem(STORAGE_KEYS.leaderboard, '{nope');
    const lb = new LeaderboardStore(st);
    expect(lb.entries).toEqual([]);
    lb.add([entry('a', 10)]);
    expect(lb.rename('a', '  Big\nCheese  ')).toBe(true);
    expect(lb.entries[0].name).toBe('Big Cheese');
    lb.clear();
    expect(JSON.parse(st.getItem(STORAGE_KEYS.leaderboard) ?? '')).toEqual([]);
    expect(sanitizeLeaderboard([{ id: 'x', score: 'bad' }, null])).toEqual([]);
    expect(sanitizeName('')).toBe('Mystery Runner');
  });
});

describe('settings', () => {
  it('defaults on corrupt JSON and clamps ranges', () => {
    const st = new MemoryStorage();
    st.setItem(STORAGE_KEYS.settings, 'garbage');
    expect(new SettingsStore(st).load()).toEqual(DEFAULT_SETTINGS);
    const s = sanitizeSettings({ musicVolume: 5, vision: { laneThreshold: 9 }, model: 'huge' });
    expect(s.musicVolume).toBe(1);
    expect(s.vision.laneThreshold).toBe(1.5);
    expect(s.model).toBe('lite');
  });
  it('merges patches and URL overrides', () => {
    const m = mergeSettings(DEFAULT_SETTINGS, { muted: true });
    expect(m.muted).toBe(true);
    expect(m.vision).toEqual(DEFAULT_SETTINGS.vision);
    const o = applyParamOverrides(DEFAULT_SETTINGS, { model: 'full', quality: null, mute: true });
    expect(o.model).toBe('full');
    expect(o.muted).toBe(true);
  });
  it('persists', () => {
    const st = new MemoryStorage();
    const store = new SettingsStore(st);
    store.save({ ...DEFAULT_SETTINGS, quality: 'low' });
    expect(store.load().quality).toBe('low');
  });
  it('loads old settings without the mimic key (defaults to true) and keeps an explicit false', () => {
    const st = new MemoryStorage();
    const old: Record<string, unknown> = { ...DEFAULT_SETTINGS };
    delete old.mimic;
    st.setItem(STORAGE_KEYS.settings, JSON.stringify({ ...old, muted: true }));
    const loaded = new SettingsStore(st).load();
    expect(loaded.mimic).toBe(true);
    expect(loaded.muted).toBe(true);
    expect(sanitizeSettings({ mimic: false }).mimic).toBe(false);
    expect(mergeSettings(DEFAULT_SETTINGS, { mimic: false }).mimic).toBe(false);
  });
  it('migrates the old untouched laneThreshold default (0.9) on load only', () => {
    const st = new MemoryStorage();
    const store = new SettingsStore(st);
    st.setItem(STORAGE_KEYS.settings, JSON.stringify({ ...DEFAULT_SETTINGS, vision: { ...DEFAULT_VISION_TUNING, laneThreshold: 0.9 } }));
    expect(store.load().vision.laneThreshold).toBe(DEFAULT_VISION_TUNING.laneThreshold);
    st.setItem(STORAGE_KEYS.settings, JSON.stringify({ ...DEFAULT_SETTINGS, vision: { ...DEFAULT_VISION_TUNING, laneThreshold: 1.1 } }));
    expect(store.load().vision.laneThreshold).toBe(1.1);
    // A value picked in-session is not rewritten.
    const picked = mergeSettings(DEFAULT_SETTINGS, { vision: { ...DEFAULT_VISION_TUNING, laneThreshold: 0.9 } });
    expect(picked.vision.laneThreshold).toBe(0.9);
  });
});

describe('leaderboard ties & late photos', () => {
  it('equal scores: earlier date ranks first (existing entry keeps its place)', () => {
    const r = insertEntries([entry('old', 50, 100)], [entry('new', 50, 200)]);
    expect(r.entries.map((e) => e.id)).toEqual(['old', 'new']);
    expect(r.ranks.get('new')).toBe(2);
    const r2 = insertEntries([], [entry('zzz', 50, 1), entry('aaa', 50, 2)]);
    expect(r2.entries.map((e) => e.id)).toEqual(['zzz', 'aaa']);
  });
  it('setPhoto rewrites the stored array', () => {
    const st = new MemoryStorage();
    const lb = new LeaderboardStore(st);
    lb.add([entry('a', 10)]);
    expect(lb.setPhoto('a', 'data:image/jpeg;base64,xx')).toBe(true);
    const raw = JSON.parse(st.getItem(STORAGE_KEYS.leaderboard) ?? '[]') as LeaderboardEntry[];
    expect(raw[0].photo).toBe('data:image/jpeg;base64,xx');
    expect(lb.setPhoto('missing', 'data:image/jpeg;base64,xx')).toBe(false);
  });
});

describe('leaderboard across tabs', () => {
  it('a stale tab merges with entries another tab wrote instead of overwriting them', () => {
    const st = new MemoryStorage();
    const tabA = new LeaderboardStore(st);
    const tabB = new LeaderboardStore(st);
    tabA.add([entry('a', 100)]);
    tabB.add([entry('b', 50)]); // B never saw A's entry
    const raw = JSON.parse(st.getItem(STORAGE_KEYS.leaderboard) ?? '[]') as LeaderboardEntry[];
    expect(raw.map((e) => e.id)).toEqual(['a', 'b']);
    expect(tabA.reload()).toBe(true);
    expect(tabA.entries.map((e) => e.id)).toEqual(['a', 'b']);
    expect(tabA.reload()).toBe(false);
  });

  it('keeps entries in memory when storage cannot be written at all', () => {
    const st = new MemoryStorage();
    const lb = new LeaderboardStore(st);
    lb.add([entry('a', 10)]);
    st.setItem = () => {
      throw new Error('QuotaExceededError');
    };
    lb.add([entry('b', 20)]);
    lb.add([entry('c', 30)]); // must not re-read storage (which lacks b) over memory
    expect(lb.entries.map((e) => e.id)).toEqual(['c', 'b', 'a']);
  });

  it('keeps a selfie that only fits in memory when syncing', () => {
    const st = new MemoryStorage();
    const lb = new LeaderboardStore(st);
    lb.add([entry('a', 10)]);
    const real = st.setItem.bind(st);
    // Photos never fit; plain entries do.
    st.setItem = (k, v) => {
      if (v.includes('data:image')) throw new Error('QuotaExceededError');
      real(k, v);
    };
    expect(lb.setPhoto('a', 'data:image/jpeg;base64,xx')).toBe(true);
    lb.add([entry('b', 5)]);
    expect(lb.entries.find((e) => e.id === 'a')?.photo).toBe('data:image/jpeg;base64,xx');
  });
});
