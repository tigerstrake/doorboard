// Booth display data layer: tolerant parsing of the leaderboard + BoothMessages,
// and the live source (localStorage + BroadcastChannel).
import type { BoothMessage, LeaderboardEntry, PlayerCount, PlayerId, PlayerResult, Screen } from '../shared/types';
import { BROADCAST_CHANNEL, MAX_LEADERBOARD, STORAGE_KEYS } from '../shared/constants';

const SCREENS: readonly Screen[] = ['boot', 'lobby', 'countdown', 'playing', 'paused', 'gameover'];

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const num = (v: unknown, def = 0): number => (typeof v === 'number' && Number.isFinite(v) ? v : def);
const nonNeg = (v: unknown): number => Math.max(0, Math.round(num(v)));
const str = (v: unknown, def: string): string => (typeof v === 'string' && v.trim() ? v.trim() : def);
const photo = (v: unknown): string | null =>
  typeof v === 'string' && v.startsWith('data:image/') ? v : null;

export function sanitizeEntry(raw: unknown, i: number): LeaderboardEntry | null {
  if (!isObj(raw)) return null;
  const score = num(raw.score, NaN);
  if (!Number.isFinite(score)) return null;
  return {
    id: str(raw.id, `entry-${i}`),
    name: str(raw.name, 'MYSTERY SURFER').slice(0, 24),
    score: Math.max(0, Math.round(score)),
    coins: nonNeg(raw.coins),
    distance: nonNeg(raw.distance),
    date: num(raw.date, 0),
    photo: photo(raw.photo),
    mode: raw.mode === 2 ? 2 : 1,
  };
}

/** Any junk in -> clean, sorted, de-duplicated top list out. */
export function sanitizeBoard(raw: unknown): LeaderboardEntry[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: LeaderboardEntry[] = [];
  raw.forEach((r, i) => {
    const e = sanitizeEntry(r, i);
    if (!e || seen.has(e.id)) return;
    seen.add(e.id);
    out.push(e);
  });
  out.sort((a, b) => b.score - a.score);
  return out.slice(0, MAX_LEADERBOARD);
}

export function parseBoard(json: string | null): LeaderboardEntry[] {
  if (!json) return [];
  try {
    return sanitizeBoard(JSON.parse(json));
  } catch {
    return [];
  }
}

function sanitizeResult(raw: unknown, i: number): PlayerResult | null {
  if (!isObj(raw)) return null;
  const rank = num(raw.rank, NaN);
  return {
    playerId: (raw.playerId === 1 ? 1 : i === 1 ? 1 : 0) as PlayerId,
    name: str(raw.name, `PLAYER ${i + 1}`).slice(0, 24),
    score: nonNeg(raw.score),
    coins: nonNeg(raw.coins),
    distance: nonNeg(raw.distance),
    time: Math.max(0, num(raw.time)),
    photo: photo(raw.photo),
    rank: Number.isFinite(rank) && rank >= 1 ? Math.round(rank) : null,
    entryId: typeof raw.entryId === 'string' ? raw.entryId : null,
  };
}

export function sanitizeMessage(raw: unknown): BoothMessage | null {
  if (!isObj(raw)) return null;
  switch (raw.type) {
    case 'idle':
      return { type: 'idle' };
    case 'state': {
      const screen = SCREENS.includes(raw.screen as Screen) ? (raw.screen as Screen) : 'playing';
      const players = Array.isArray(raw.players)
        ? raw.players.slice(0, 2).filter(isObj).map((p) => ({
            score: nonNeg(p.score),
            coins: nonNeg(p.coins),
            distance: nonNeg(p.distance),
            alive: p.alive !== false,
          }))
        : [];
      const playerCount: PlayerCount = raw.playerCount === 2 || players.length === 2 ? 2 : 1;
      return { type: 'state', screen, playerCount, players };
    }
    case 'result': {
      const results = Array.isArray(raw.results)
        ? raw.results.slice(0, 2).map(sanitizeResult).filter((r): r is PlayerResult => r !== null)
        : [];
      return { type: 'result', results };
    }
    default:
      return null;
  }
}

export function formatNum(n: number): string {
  return Math.round(n).toLocaleString('en-US');
}

export function relativeDate(date: number, now: number): string {
  if (!date) return 'A WHILE AGO';
  const s = Math.max(0, (now - date) / 1000);
  if (s < 45) return 'JUST NOW';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} MIN AGO`;
  const h = Math.round(m / 60);
  if (h < 24) return h === 1 ? '1 HOUR AGO' : `${h} HOURS AGO`;
  const d = Math.round(h / 24);
  if (d === 1) return 'YESTERDAY';
  if (d < 14) return `${d} DAYS AGO`;
  return new Date(date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }).toUpperCase();
}

/** 1-based rank a score would get on the board, or null if it misses the top list. */
export function projectedRank(score: number, board: readonly LeaderboardEntry[]): number | null {
  let r = 1;
  for (const e of board) if (e.score >= score) r++;
  return r <= MAX_LEADERBOARD ? r : null;
}

export interface HallSource {
  start(onBoard: (board: LeaderboardEntry[]) => void, onMessage: (msg: BoothMessage) => void): void;
}

/** Real booth feed: localStorage (storage events + light polling) and BroadcastChannel. */
export function createLiveSource(): HallSource {
  return {
    start(onBoard, onMessage) {
      let last: string | null | undefined;
      const read = (): void => {
        let raw: string | null = null;
        try {
          raw = localStorage.getItem(STORAGE_KEYS.leaderboard);
        } catch {
          raw = null;
        }
        if (raw === last) return;
        last = raw;
        onBoard(parseBoard(raw));
      };
      read();
      window.addEventListener('storage', (e) => {
        if (e.key === null || e.key === STORAGE_KEYS.leaderboard) read();
      });
      window.setInterval(read, 2000);
      if (typeof BroadcastChannel !== 'undefined') {
        const ch = new BroadcastChannel(BROADCAST_CHANNEL);
        ch.onmessage = (e: MessageEvent<unknown>) => {
          const m = sanitizeMessage(e.data);
          if (m) onMessage(m);
        };
      }
    },
  };
}
