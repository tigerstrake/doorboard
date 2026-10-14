// hall.html?demo=1 — in-memory fake leaderboard + simulated BoothMessages.
// Never touches localStorage.
import type { LeaderboardEntry, PlayerResult } from '../shared/types';
import { MAX_LEADERBOARD } from '../shared/constants';
import type { HallSource } from './data';
import { projectedRank } from './data';

const NAMES = [
  'RAIL QUEEN', 'SPEEDY SAM', 'JUMPIN JO', 'CAPTAIN SQUAT', 'DJ DODGE', 'LANE CHANGER',
  'TURBO TINA', 'HURDLE HANK', 'MAX AIR', 'COIN GOBLIN', 'NOODLE LEGS', 'ZIGZAG ZOE',
];
const SKIN = ['#f1c27d', '#e0ac69', '#c68642', '#8d5524', '#ffdbac'];
const BG = ['#ff4757', '#2ed573', '#ffd23f', '#a55eea', '#ff7a1a', '#1e90ff'];

let seed = 7;
const rnd = (): number => {
  seed = (seed * 16807) % 2147483647;
  return (seed - 1) / 2147483646;
};
const pick = <T>(a: readonly T[]): T => a[Math.floor(rnd() * a.length)]!;

/** A goofy fake "crash-face" selfie as a JPEG data URL. */
function fakeSelfie(): string {
  const c = document.createElement('canvas');
  c.width = 320;
  c.height = 240;
  const g = c.getContext('2d');
  if (!g) return '';
  g.fillStyle = pick(BG);
  g.fillRect(0, 0, 320, 240);
  g.strokeStyle = 'rgba(255,255,255,0.35)';
  g.lineWidth = 6;
  for (let i = 0; i < 9; i++) {
    g.beginPath();
    g.moveTo(160, 130);
    const a = (i / 9) * Math.PI * 2;
    g.lineTo(160 + Math.cos(a) * 300, 130 + Math.sin(a) * 300);
    g.stroke();
  }
  const cx = 150 + rnd() * 20;
  const tilt = (rnd() - 0.5) * 0.6;
  g.save();
  g.translate(cx, 140);
  g.rotate(tilt);
  // shoulders
  g.fillStyle = pick(['#ff7a1a', '#1aa3ff', '#2f3542', '#2ed573']);
  g.beginPath();
  g.ellipse(0, 150, 120, 80, 0, 0, Math.PI * 2);
  g.fill();
  // head
  g.fillStyle = pick(SKIN);
  g.beginPath();
  g.ellipse(0, 0, 62, 74, 0, 0, Math.PI * 2);
  g.fill();
  // hair
  g.fillStyle = pick(['#2d1b0e', '#000', '#b5651d', '#ffd23f', '#ff4757']);
  g.beginPath();
  g.ellipse(0, -52, 64, 34, 0, Math.PI, Math.PI * 2);
  g.fill();
  // eyes: wide or X
  g.fillStyle = '#fff';
  g.strokeStyle = '#1b1b2f';
  g.lineWidth = 4;
  const xEyes = rnd() < 0.4;
  for (const ex of [-24, 24]) {
    if (xEyes) {
      g.beginPath();
      g.moveTo(ex - 10, -18);
      g.lineTo(ex + 10, 2);
      g.moveTo(ex + 10, -18);
      g.lineTo(ex - 10, 2);
      g.stroke();
    } else {
      g.beginPath();
      g.arc(ex, -8, 14, 0, Math.PI * 2);
      g.fill();
      g.stroke();
      g.fillStyle = '#1b1b2f';
      g.beginPath();
      g.arc(ex + (rnd() - 0.5) * 8, -6, 5, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = '#fff';
    }
  }
  // screaming mouth
  g.fillStyle = '#5a1020';
  g.beginPath();
  g.ellipse(0, 36, 16 + rnd() * 10, 18 + rnd() * 10, 0, 0, Math.PI * 2);
  g.fill();
  g.restore();
  // motion lines
  g.strokeStyle = '#fff';
  g.lineWidth = 5;
  for (let i = 0; i < 3; i++) {
    g.beginPath();
    g.moveTo(20, 60 + i * 30);
    g.lineTo(60, 66 + i * 30);
    g.stroke();
  }
  return c.toDataURL('image/jpeg', 0.8);
}

let idn = 0;
function fakeEntry(score: number, agoMin: number, withPhoto: boolean, mode: 1 | 2 = 1, name = pick(NAMES)): LeaderboardEntry {
  return {
    id: `demo-${++idn}`,
    name,
    score,
    coins: Math.round(score / 40 + rnd() * 60),
    distance: Math.round(score / 9 + rnd() * 80),
    date: Date.now() - agoMin * 60000,
    photo: withPhoto ? fakeSelfie() : null,
    mode,
  };
}

export function createDemoSource(empty: boolean): HallSource {
  return {
    start(onBoard, onMessage) {
      let board: LeaderboardEntry[] = empty
        ? []
        : [
            fakeEntry(14820, 3, true, 1, 'RAIL QUEEN'),
            fakeEntry(12210, 18, true, 2, 'SPEEDY SAM'),
            fakeEntry(11050, 42, true, 1, 'JUMPIN JO'),
            fakeEntry(9480, 75, false, 1, 'CAPTAIN SQUAT'),
            fakeEntry(8730, 130, true, 2, 'DJ DODGE'),
            fakeEntry(7400, 200, true, 1, 'TURBO TINA'),
            fakeEntry(6120, 380, true, 1, 'HURDLE HANK'),
            fakeEntry(5010, 1500, false, 1, 'NOODLE LEGS'),
            fakeEntry(3990, 3000, true, 2, 'ZIGZAG ZOE'),
          ];
      onBoard(board);
      const freshName = (): string => {
        const free = NAMES.filter((n) => !board.some((e) => e.name === n));
        return pick(free.length ? free : NAMES);
      };

      const commit = (results: Omit<PlayerResult, 'rank' | 'entryId'>[]): void => {
        const mode = results.length === 2 ? 2 : 1;
        const out: PlayerResult[] = [];
        for (const r of results) {
          const rank = projectedRank(r.score, board);
          let entryId: string | null = null;
          if (rank !== null) {
            const e = fakeEntry(r.score, 0, r.photo !== null, mode, r.name);
            e.coins = r.coins;
            e.distance = r.distance;
            e.photo = r.photo;
            entryId = e.id;
            board = [...board, e].sort((a, b) => b.score - a.score).slice(0, MAX_LEADERBOARD);
          }
          out.push({ ...r, rank: entryId ? board.findIndex((e) => e.id === entryId) + 1 : null, entryId });
        }
        onBoard(board); // the game saves before the gameover screen shows
        onMessage({ type: 'result', results: out });
      };

      // Scripted timeline (ms) followed by randomized cycles.
      type Step = { at: number; run: () => void };
      const timers: number[] = [];
      const run1P = (t0: number, from: number, to: number, ms: number, crashName: string): Step[] => {
        const steps: Step[] = [];
        for (let t = 0; t <= ms; t += 250) {
          const s = from + ((to - from) * t) / ms;
          steps.push({
            at: t0 + t,
            run: () =>
              onMessage({
                type: 'state',
                screen: 'playing',
                playerCount: 1,
                players: [{ score: s, coins: Math.round(s / 40), distance: Math.round(s / 9), alive: true }],
              }),
          });
        }
        steps.push({
          at: t0 + ms + 200,
          run: () =>
            commit([{ playerId: 0, name: crashName, score: Math.round(to), coins: Math.round(to / 40), distance: Math.round(to / 9), time: 60, photo: fakeSelfie() }]),
        });
        return steps;
      };
      const run2P = (t0: number, a: number, b: number, ms: number): Step[] => {
        const steps: Step[] = [];
        steps.push({ at: t0, run: () => onMessage({ type: 'state', screen: 'countdown', playerCount: 2, players: [] }) });
        const start = t0 + 2000;
        for (let t = 0; t <= ms; t += 250) {
          const f = t / ms;
          const sa = a * Math.min(1, f * 1.0);
          const bOut = f > 0.7;
          const sb = b * Math.min(1, f / 0.7);
          steps.push({
            at: start + t,
            run: () =>
              onMessage({
                type: 'state',
                screen: 'playing',
                playerCount: 2,
                players: [
                  { score: sa, coins: Math.round(sa / 40), distance: Math.round(sa / 9), alive: true },
                  { score: sb, coins: Math.round(sb / 40), distance: Math.round(sb / 9), alive: !bOut },
                ],
              }),
          });
        }
        steps.push({
          at: start + ms + 200,
          run: () =>
            commit([
              { playerId: 0, name: freshName(), score: a, coins: Math.round(a / 40), distance: Math.round(a / 9), time: 50, photo: fakeSelfie() },
              { playerId: 1, name: freshName(), score: b, coins: Math.round(b / 40), distance: Math.round(b / 9), time: 35, photo: rnd() < 0.5 ? fakeSelfie() : null },
            ]),
        });
        return steps;
      };

      const schedule = (steps: Step[]): void => {
        for (const s of steps) timers.push(window.setTimeout(s.run, s.at));
      };
      const cycle = (t0: number): number => {
        const steps: Step[] = [];
        steps.push({ at: t0, run: () => onMessage({ type: 'idle' }) });
        const top = board[0]?.score ?? 5000;
        const s1 = Math.round(top * (0.5 + rnd() * 0.7));
        steps.push(...run1P(t0 + 9000, 0, s1, 9000, freshName()));
        steps.push({ at: t0 + 27000, run: () => onMessage({ type: 'idle' }) });
        steps.push(...run2P(t0 + 33000, Math.round(top * (0.6 + rnd() * 0.5)), Math.round(top * (0.3 + rnd() * 0.4)), 9000));
        schedule(steps);
        return t0 + 56000;
      };

      // Opening: mid-run 1P that beats #1 at ~4.2 s (for the screenshot timeline).
      schedule(run1P(0, empty ? 1200 : 11800, empty ? 4200 : 15230, 2000, 'MAX AIR'));
      schedule([{ at: 13000, run: () => onMessage({ type: 'idle' }) }]);
      let next = 13000;
      const loop = (): void => {
        next = cycle(0);
        window.setTimeout(loop, next);
      };
      window.setTimeout(loop, next);
    },
  };
}
