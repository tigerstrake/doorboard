// Audio + keyboard sandbox. ?autoplay=1 unlocks and plays every sound in sequence.
import { AudioManager } from '../src/audio';
import { KeyboardInput } from '../src/input';
import type { GameEvent, MusicMode, PlayerCount, PlayerId, UISound } from '../src/shared/types';

const audio = new AudioManager();
const kb = new KeyboardInput();
kb.attach(window);

const $ = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;
const status = $('status');
const log = $('log');
let players: PlayerCount = 1;
let pid: PlayerId = 0;

function println(s: string): void {
  log.textContent = `${s}\n${log.textContent ?? ''}`.slice(0, 4000);
}

async function unlock(): Promise<void> {
  await audio.unlock();
  status.textContent = audio.unlocked ? `unlocked — context ${audio.context?.state ?? '?'}` : 'Web Audio unavailable';
}

const EVENTS: [string, GameEvent][] = [
  ['jump', { type: 'jump', super: false }],
  ['super jump', { type: 'jump', super: true }],
  ['land', { type: 'land', onRoof: false }],
  ['land roof', { type: 'land', onRoof: true }],
  ['roll', { type: 'roll' }],
  ['lane ←', { type: 'laneChange', from: 0, to: -1 }],
  ['lane →', { type: 'laneChange', from: 0, to: 1 }],
  ['blocked', { type: 'blocked', dir: 1 }],
  ['coin', { type: 'coin', x: 0, y: 1, combo: 1, total: 1 }],
  ['powerUp magnet', { type: 'powerUp', kind: 'magnet' }],
  ['powerUp sneakers', { type: 'powerUp', kind: 'sneakers' }],
  ['powerUp jetpack', { type: 'powerUp', kind: 'jetpack' }],
  ['powerUpEnd jetpack', { type: 'powerUpEnd', kind: 'jetpack' }],
  ['powerUp slowmo', { type: 'powerUp', kind: 'slowmo' }],
  ['powerUpWarning slowmo', { type: 'powerUpWarning', kind: 'slowmo' }],
  ['powerUpEnd slowmo', { type: 'powerUpEnd', kind: 'slowmo' }],
  ['powerUp star', { type: 'powerUp', kind: 'star' }],
  ['smash', { type: 'smash', kind: 'barrier_low', lane: 0 }],
  ['powerUpEnd star', { type: 'powerUpEnd', kind: 'star' }],
  ['powerUp coinRain', { type: 'powerUp', kind: 'coinRain' }],
  ['powerUpWarning', { type: 'powerUpWarning', kind: 'magnet' }],
  ['powerUpEnd', { type: 'powerUpEnd', kind: 'magnet' }],
  ['hoverboardBreak', { type: 'hoverboardBreak' }],
  ['crash', { type: 'crash', cause: 'train', moving: true }],
  ['trainHorn', { type: 'trainHorn', lane: 0 }],
  ['speedUp', { type: 'speedUp', level: 2 }],
  ['milestone', { type: 'milestone', distance: 500 }],
];
button('events', 'smash ×4 (same frame)', () => {
  for (let i = 0; i < 4; i++) ev({ type: 'smash', kind: 'barrier_low', lane: ((i % 3) - 1) as -1 | 0 | 1 });
});
const UIS: UISound[] = ['click', 'tick', 'go', 'handsUp', 'join', 'pause', 'resume', 'gameOver', 'newRecord', 'win', 'speedUp'];
const MODES: MusicMode[] = ['off', 'lobby', 'game', 'gameover'];

function button(parent: string, label: string, fn: () => void): HTMLButtonElement {
  const b = document.createElement('button');
  b.textContent = label;
  b.onclick = () => {
    void unlock().then(fn);
  };
  $(parent).appendChild(b);
  return b;
}

const ev = (e: GameEvent): void => audio.playGameEvent(e, pid, players);
for (const [label, e] of EVENTS) button('events', label, () => ev(e));
for (const s of UIS) button('ui', s, () => audio.playUi(s));
const coinRun = (n: number): void => {
  for (let i = 1; i <= n; i++) setTimeout(() => ev({ type: 'coin', x: 0, y: 1, combo: i, total: i }), i * 110);
};
button('coins', 'combo ×5', () => coinRun(5));
button('coins', 'combo ×15', () => coinRun(15));
button('coins', 'burst (8 same frame)', () => {
  for (let i = 1; i <= 8; i++) ev({ type: 'coin', x: i % 3 - 1, y: 1, combo: i, total: i });
});
const modeButtons = new Map<MusicMode, HTMLButtonElement>();
function setMode(m: MusicMode): void {
  audio.setMusic(m);
  for (const [k, b] of modeButtons) b.classList.toggle('on', k === m);
}
for (const m of MODES) modeButtons.set(m, button('music', m, () => setMode(m)));

$('unlock').onclick = () => void unlock();
$<HTMLInputElement>('mute').onchange = (e) => audio.setMuted((e.target as HTMLInputElement).checked);
$<HTMLSelectElement>('players').onchange = (e) => {
  players = (e.target as HTMLSelectElement).value === '2' ? 2 : 1;
  kb.setPlayerCount(players);
};
$<HTMLSelectElement>('pid').onchange = (e) => {
  pid = (e.target as HTMLSelectElement).value === '1' ? 1 : 0;
};
const vols = (): void => audio.setVolumes(+$<HTMLInputElement>('mvol').value, +$<HTMLInputElement>('svol').value);
$('mvol').oninput = vols;
$('svol').oninput = vols;
$('intensity').oninput = (e) => audio.setIntensity(+(e.target as HTMLInputElement).value);
$('charge').oninput = (e) => audio.setChargeLevel(+(e.target as HTMLInputElement).value);

// Keyboard tester: log + sounds.
kb.onAction((a) => {
  void unlock();
  println(`action P${a.playerId + 1} ${JSON.stringify(a.action)}`);
  const act = a.action as { type: string; dir?: number };
  if (act.type === 'jump') audio.playGameEvent({ type: 'jump', super: false }, a.playerId, players);
  else if (act.type === 'duck' || act.type === 'roll') audio.playGameEvent({ type: 'roll' }, a.playerId, players);
  else audio.playGameEvent({ type: 'laneChange', from: 0, to: (act.dir ?? 1) < 0 ? -1 : 1 }, a.playerId, players);
});
kb.onMenuKey((k) => {
  void unlock();
  println(`menu ${k}`);
  if (k === 'confirm') audio.playUi('click');
  else if (k === 'mute') {
    const m = $<HTMLInputElement>('mute');
    m.checked = !m.checked;
    audio.setMuted(m.checked);
  }
});

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

async function playEverything(): Promise<void> {
  await unlock();
  setMode('game');
  for (const [label, e] of EVENTS) {
    status.textContent = `playing ${label}`;
    ev(e);
    await sleep(220);
  }
  coinRun(12);
  await sleep(1500);
  for (const s of UIS) {
    status.textContent = `ui ${s}`;
    audio.playUi(s);
    await sleep(200);
  }
  for (let i = 0; i <= 20; i++) {
    audio.setChargeLevel(i / 20);
    audio.setIntensity(i / 20);
    await sleep(40);
  }
  audio.setChargeLevel(0);
  setMode('lobby');
  await sleep(600);
  setMode('gameover');
  await sleep(600);
  setMode('game');
  status.textContent = `done — context ${audio.context?.state ?? 'none'}`;
  console.log('[audio sandbox] autoplay sequence complete', audio.context?.state);
}
$('all').onclick = () => void playEverything();

if (new URLSearchParams(location.search).get('autoplay') === '1') void playEverything();
