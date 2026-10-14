// BODY SURFERS — Hall of Fame booth display (second screen).
// Reads the leaderboard from localStorage and the live feed from BroadcastChannel.
// ?demo=1 runs on in-memory fake data (never writes storage); ?demo=empty for the empty state.
import '@fontsource/lilita-one';
import '@fontsource/nunito/700.css';
import '@fontsource/nunito/800.css';
import '@fontsource/nunito/900.css';
import './hall.css';
import type { BoothMessage, LeaderboardEntry } from '../shared/types';
import { GAME_TITLE } from '../shared/constants';
import { createLiveSource, formatNum } from './data';
import type { HallSource } from './data';
import { createDemoSource } from './demo';
import { Wall } from './wall';
import { NowPlaying, escapeHtml } from './nowplaying';
import { Celebrations } from './celebrate';

const STAGE_W = 1920;
const STAGE_H = 1080;

const root = document.getElementById('hall-root');
if (!root) throw new Error('hall: #hall-root missing');

const params = new URLSearchParams(location.search);
const demoParam = params.get('demo');
const demo = demoParam !== null && demoParam !== '0';

// TV mode (?tv=1) hides the cursor and the play link for a booth monitor. Otherwise this
// page is often opened by mistake instead of the game, so offer a clear way into it.
if (params.get('tv') === '1') document.body.classList.add('is-tv');
const playLink = document.createElement('a');
playLink.className = 'hall-play';
playLink.href = './';
playLink.textContent = '▶ Play the game';
document.body.appendChild(playLink);

root.innerHTML = `
  <div class="bg"><div class="sunburst"></div><div class="rails"></div></div>
  <div class="stage">
    <header class="hdr">
      <div class="logo" data-text="${GAME_TITLE}">${GAME_TITLE}</div>
      <div class="hof-ribbon">HALL OF FAME</div>
      <div class="hdr-right">
        <div class="legend-count"></div>
        ${demo ? '<div class="demo-badge">DEMO</div>' : ''}
      </div>
    </header>
    <main class="main">
      <section class="wall-wrap">
        <div class="wall"></div>
        <div class="empty-msg"><div class="empty-big">THE WALL IS EMPTY!</div><div class="empty-sub">Crash first, get famous first. Raise both hands to play!</div></div>
      </section>
      <aside class="np"></aside>
    </main>
    <footer class="ticker"><div class="ticker-track"></div></footer>
    <canvas class="fx" width="${STAGE_W}" height="${STAGE_H}"></canvas>
  </div>`;

const stage = root.querySelector<HTMLElement>('.stage')!;
const wallEl = root.querySelector<HTMLElement>('.wall')!;
const wallWrap = root.querySelector<HTMLElement>('.wall-wrap')!;
const npEl = root.querySelector<HTMLElement>('.np')!;
const tickerTrack = root.querySelector<HTMLElement>('.ticker-track')!;
const legendCount = root.querySelector<HTMLElement>('.legend-count')!;
const fx = root.querySelector<HTMLCanvasElement>('.fx')!;

function fit(): void {
  const s = Math.min(window.innerWidth / STAGE_W, window.innerHeight / STAGE_H);
  stage.style.transform = `translate(${(window.innerWidth - STAGE_W * s) / 2}px, ${(window.innerHeight - STAGE_H * s) / 2}px) scale(${s})`;
}
window.addEventListener('resize', fit);
fit();

const wall = new Wall(wallEl);
const np = new NowPlaying(npEl);
const cele = new Celebrations(stage, fx, (e, r) => wall.cardFor(e, r));

let known: Set<string> | null = null;
const celebrated = new Set<string>();

function updateTicker(board: LeaderboardEntry[]): void {
  const top = board[0];
  const bits = [
    'RAISE BOTH HANDS TO PLAY',
    'JUMP',
    'SQUAT',
    'STEP LEFT & RIGHT',
    'GET YOUR CRASH-FACE ON THE WALL',
    top ? `SCORE TO BEAT: ${formatNum(top.score)} BY ${escapeHtml(top.name.toUpperCase())}` : 'NO RECORD YET — BE THE FIRST!',
    'BRING A FRIEND FOR A 2P RACE',
  ];
  const html = bits.map((b) => `<span>${b}</span><i>&#9733;</i>`).join('');
  tickerTrack.innerHTML = html + html;
}

function onBoard(board: LeaderboardEntry[]): void {
  const fresh = new Set<string>();
  if (known) for (const e of board) if (!known.has(e.id)) fresh.add(e.id);
  known = new Set(board.map((e) => e.id));
  wall.update(board, fresh);
  wallWrap.classList.toggle('is-empty', board.length === 0);
  np.setBoard(board);
  updateTicker(board);
  legendCount.innerHTML = `<b>${board.length}</b> LEGEND${board.length === 1 ? '' : 'S'}`;
  board.forEach((e, i) => {
    if (fresh.has(e.id) && !celebrated.has(e.id)) {
      celebrated.add(e.id);
      cele.enqueue(e, i + 1);
    }
  });
}

function onMessage(m: BoothMessage): void {
  np.onMessage(m, performance.now());
  if (m.type !== 'result') return;
  // Fallback when the storage update hasn't arrived (yet): celebrate from the result itself.
  window.setTimeout(() => {
    for (const r of m.results) {
      if (r.rank === null || !r.entryId || celebrated.has(r.entryId)) continue;
      celebrated.add(r.entryId);
      cele.enqueue(
        { id: r.entryId, name: r.name, score: r.score, coins: r.coins, distance: r.distance, date: Date.now(), photo: r.photo, mode: m.results.length === 2 ? 2 : 1 },
        r.rank,
      );
    }
  }, 1200);
}

const source: HallSource = demo ? createDemoSource(demoParam === 'empty') : createLiveSource();
source.start(onBoard, onMessage);

// attract-loop spotlight + relative date refresh
window.setInterval(() => wall.spotlightNext(np.isIdle && !cele.busy), 2600);
window.setInterval(() => wall.refreshDates(), 60000);

let last = performance.now();
function frame(now: number): void {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  np.tick(now, dt);
  cele.tick(now, dt);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
