// NOW PLAYING panel: live 1P score / 2P race bars, result recap, idle attract slides.
import type { BoothMessage, LeaderboardEntry, PlayerResult } from '../shared/types';
import { MAX_LEADERBOARD, PLAYER_COLORS, PLAYER_LABELS } from '../shared/constants';
import { formatNum, projectedRank } from './data';

type Mode = 'idle' | 'countdown' | 'live1' | 'live2' | 'result';
type LivePlayer = { score: number; coins: number; distance: number; alive: boolean };

const STALE_MS = 4000;
const RESULT_MS = 9000;
const SLIDE_MS = 5000;

const q = <T extends HTMLElement>(root: HTMLElement, sel: string): T => {
  const el = root.querySelector<T>(sel);
  if (!el) throw new Error(`hall: missing ${sel}`);
  return el;
};

export class NowPlaying {
  private mode: Mode = 'idle';
  private board: LeaderboardEntry[] = [];
  private players: LivePlayer[] = [];
  private shown = [0, 0];
  private lastState = 0;
  private resultAt = 0;
  private paused = false;
  private slide = 0;
  private slideAt = 0;
  private readonly el: {
    status: HTMLElement;
    score1: HTMLElement;
    coins1: HTMLElement;
    dist1: HTMLElement;
    pace1: HTMLElement;
    race: HTMLElement;
    rows: { row: HTMLElement; fill: HTMLElement; score: HTMLElement; meta: HTMLElement }[];
    raceNote: HTMLElement;
    results: HTMLElement;
    slides: HTMLElement[];
    beat: HTMLElement;
  };

  constructor(private readonly root: HTMLElement) {
    root.innerHTML = `
      <div class="np-head"><span class="np-dot"></span><span class="np-title">NOW PLAYING</span></div>
      <div class="np-body">
        <section class="np-mode np-idle">
          <div class="slide active">
            <div class="pict" aria-hidden="true">
              <svg viewBox="0 0 120 150" class="pict-svg">
                <g class="arms"><path d="M38 70 L18 30" /><path d="M82 70 L102 30" /></g>
                <circle cx="60" cy="40" r="17" />
                <path d="M60 58 L60 102 M60 102 L44 140 M60 102 L76 140 M38 70 L82 70" />
              </svg>
            </div>
            <div class="np-big">RAISE<br>BOTH HANDS<br>TO PLAY!</div>
            <div class="np-sub">Step up to the screen &mdash; bring a friend to race!</div>
          </div>
          <div class="slide">
            <div class="np-kicker">SCORE TO BEAT</div>
            <div class="np-beat"></div>
          </div>
          <div class="slide">
            <div class="np-kicker">HOW TO PLAY</div>
            <ul class="howto">
              <li><span class="key k-y">&#9650;</span><b>JUMP</b><i>hop over hurdles</i></li>
              <li><span class="key k-r">&#9660;</span><b>SQUAT</b><i>duck under bars</i></li>
              <li><span class="key k-g">&#9664;&#9654;</span><b>STEP</b><i>switch lanes</i></li>
            </ul>
            <div class="np-sub">Crash and your face goes on the wall!</div>
          </div>
        </section>
        <section class="np-mode np-countdown">
          <div class="np-ready">GET<br>READY!</div>
          <div class="np-sub">Stand still &mdash; calibrating&hellip;</div>
        </section>
        <section class="np-mode np-live1">
          <div class="np-chip" style="--c:${PLAYER_COLORS[0]}">${PLAYER_LABELS[0]}</div>
          <div class="np-kicker">SCORE</div>
          <div class="np-score">0</div>
          <div class="np-stats">
            <div class="stat"><span class="coin"></span><b class="np-coins">0</b></div>
            <div class="stat"><span class="flag"></span><b class="np-dist">0 m</b></div>
          </div>
          <div class="np-pace"></div>
        </section>
        <section class="np-mode np-live2">
          <div class="np-kicker">2P RACE</div>
          <div class="race">
            ${[0, 1]
              .map(
                (i) => `<div class="race-row" style="--c:${PLAYER_COLORS[i]}">
                <div class="race-top"><span class="np-chip small">${PLAYER_LABELS[i]}</span><span class="race-score">0</span></div>
                <div class="race-track"><div class="race-fill"><span class="race-runner"></span></div></div>
                <div class="race-meta"></div>
                <div class="race-out">OUT!</div>
              </div>`,
              )
              .join('')}
          </div>
          <div class="race-note"></div>
        </section>
        <section class="np-mode np-result">
          <div class="np-crash">CRASHED!</div>
          <div class="np-results"></div>
        </section>
        <div class="np-paused">PAUSED</div>
      </div>`;
    const rows = Array.from(root.querySelectorAll<HTMLElement>('.race-row')).map((row) => ({
      row,
      fill: q(row, '.race-fill'),
      score: q(row, '.race-score'),
      meta: q(row, '.race-meta'),
    }));
    this.el = {
      status: q(root, '.np-title'),
      score1: q(root, '.np-score'),
      coins1: q(root, '.np-coins'),
      dist1: q(root, '.np-dist'),
      pace1: q(root, '.np-pace'),
      race: q(root, '.race'),
      rows,
      raceNote: q(root, '.race-note'),
      results: q(root, '.np-results'),
      slides: Array.from(root.querySelectorAll<HTMLElement>('.slide')),
      beat: q(root, '.np-beat'),
    };
    this.setMode('idle');
  }

  get isIdle(): boolean {
    return this.mode === 'idle';
  }

  setBoard(board: LeaderboardEntry[]): void {
    this.board = board;
    const top = board[0];
    this.el.beat.innerHTML = top
      ? `<div class="beat-score">${formatNum(top.score)}</div><div class="beat-name">by ${escapeHtml(top.name)}</div><div class="np-sub">Think you can take the crown?</div>`
      : `<div class="beat-score">???</div><div class="beat-name">NO RECORD YET</div><div class="np-sub">Be the first legend on the wall!</div>`;
  }

  onMessage(m: BoothMessage, now: number): void {
    if (m.type === 'idle') {
      if (this.mode !== 'result') this.setMode('idle');
      return;
    }
    if (m.type === 'result') {
      this.showResults(m.results);
      this.resultAt = now;
      this.setMode('result');
      return;
    }
    this.lastState = now;
    this.paused = m.screen === 'paused';
    this.root.classList.toggle('is-paused', this.paused);
    if (m.screen === 'countdown') {
      this.setMode('countdown');
      return;
    }
    if (m.screen !== 'playing' && m.screen !== 'paused') return;
    const fresh = this.mode !== 'live1' && this.mode !== 'live2';
    this.players = m.players;
    if (fresh) this.shown = [0, 0];
    this.setMode(m.playerCount === 2 ? 'live2' : 'live1');
  }

  tick(now: number, dt: number): void {
    if ((this.mode === 'live1' || this.mode === 'live2' || this.mode === 'countdown') && now - this.lastState > STALE_MS) {
      this.paused = false;
      this.root.classList.remove('is-paused');
      this.setMode('idle');
    }
    if (this.mode === 'result' && now - this.resultAt > RESULT_MS) this.setMode('idle');

    if (this.mode === 'idle') {
      if (now - this.slideAt > SLIDE_MS) {
        this.slideAt = now;
        this.slide = (this.slide + 1) % this.el.slides.length;
        this.el.slides.forEach((s, i) => s.classList.toggle('active', i === this.slide));
      }
      return;
    }
    const k = Math.min(1, dt * 8);
    for (let i = 0; i < this.players.length; i++) {
      const target = this.players[i]!.score;
      const cur = this.shown[i] ?? 0;
      this.shown[i] = Math.abs(target - cur) < 1 ? target : cur + (target - cur) * k;
    }
    if (this.mode === 'live1') this.renderLive1();
    else if (this.mode === 'live2') this.renderLive2();
  }

  private setMode(mode: Mode): void {
    if (mode === this.mode && this.root.dataset.mode === mode) return;
    this.mode = mode;
    this.root.dataset.mode = mode;
    this.el.status.textContent =
      mode === 'idle' ? 'NEXT UP: YOU!' : mode === 'result' ? 'RUN OVER' : mode === 'countdown' ? 'STARTING…' : 'NOW PLAYING';
    if (mode === 'idle') {
      this.slide = 0;
      this.slideAt = performance.now();
      this.el.slides.forEach((s, i) => s.classList.toggle('active', i === 0));
    }
  }

  private renderLive1(): void {
    const p = this.players[0];
    if (!p) return;
    const s = this.shown[0] ?? 0;
    const txt = formatNum(s);
    this.el.score1.textContent = txt;
    this.el.score1.style.fontSize = `${txt.length <= 5 ? 136 : txt.length === 6 ? 118 : 100}px`;
    this.el.coins1.textContent = formatNum(p.coins);
    this.el.dist1.textContent = `${formatNum(p.distance)} m`;
    this.el.pace1.innerHTML = this.paceText(p.score);
  }

  private paceText(score: number): string {
    const top = this.board[0];
    if (!top || score > top.score) return `<span class="hot">NEW RECORD PACE!</span>`;
    const r = projectedRank(score, this.board);
    if (r !== null) return `ON PACE FOR <span class="hot">#${r}</span>`;
    const last = this.board[MAX_LEADERBOARD - 1];
    return last ? `${formatNum(last.score - score + 1)} MORE TO MAKE THE WALL` : '';
  }

  private renderLive2(): void {
    const a = this.shown[0] ?? 0;
    const b = this.shown[1] ?? 0;
    const max = Math.max(a, b, 1) * 1.08;
    const leader = a === b ? -1 : a > b ? 0 : 1;
    this.el.rows.forEach((r, i) => {
      const p = this.players[i];
      const s = i === 0 ? a : b;
      r.fill.style.width = `${Math.max(4, (s / max) * 100)}%`;
      r.score.textContent = formatNum(s);
      r.meta.textContent = p ? `${formatNum(p.coins)} coins · ${formatNum(p.distance)} m` : '';
      r.row.classList.toggle('lead', i === leader);
      r.row.classList.toggle('out', p ? !p.alive : false);
    });
    const gap = Math.abs((this.players[0]?.score ?? 0) - (this.players[1]?.score ?? 0));
    this.el.raceNote.innerHTML =
      leader === -1 ? 'NECK AND NECK!' : `<span style="color:${PLAYER_COLORS[leader]}">${PLAYER_LABELS[leader]}</span> LEADS BY ${formatNum(gap)}`;
  }

  private showResults(results: PlayerResult[]): void {
    const best = results.reduce((m, r) => Math.max(m, r.score), 0);
    this.el.results.innerHTML = results
      .map((r) => {
        const color = PLAYER_COLORS[r.playerId];
        const verdict =
          r.rank === 1
            ? `<span class="verdict gold">NEW #1!</span>`
            : r.rank !== null
              ? `<span class="verdict">#${r.rank} ON THE WALL!</span>`
              : `<span class="verdict miss">SO CLOSE &mdash; TRY AGAIN!</span>`;
        const crown = results.length > 1 && r.score === best ? '<span class="crown">WINNER</span>' : '';
        return `<div class="res" style="--c:${color}">
          <div class="res-name"><span class="np-chip small">${PLAYER_LABELS[r.playerId]}</span>${escapeHtml(r.name)}${crown}</div>
          <div class="res-score">${formatNum(r.score)}</div>
          ${verdict}
        </div>`;
      })
      .join('');
  }
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}
