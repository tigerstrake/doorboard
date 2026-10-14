// Celebration overlay + confetti/fireworks for new wall entries (bigger for a new #1).
import type { LeaderboardEntry } from '../shared/types';
import { formatNum } from './data';
import { escapeHtml } from './nowplaying';
import { reducedMotion } from '../shared/motion';

const COLORS = ['#ffd23f', '#1e90ff', '#ff4757', '#2ed573', '#ffffff', '#ff7a1a'];
const MAX_P = 700;

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  rot: number;
  vr: number;
  w: number;
  h: number;
  color: string;
  life: number;
  spark: boolean;
}

interface Pending {
  entry: LeaderboardEntry;
  rank: number;
}

export class Celebrations {
  private readonly ctx: CanvasRenderingContext2D | null;
  private readonly ps: Particle[] = [];
  private readonly queue: Pending[] = [];
  private active: { rank: number; until: number; nextBurst: number } | null = null;
  /** Pending timeout for the pause between two queued celebrations. */
  private gapTimer: number | null = null;
  private readonly overlay: HTMLElement;
  private readonly headline: HTMLElement;
  private readonly sub: HTMLElement;
  private readonly img: HTMLImageElement;

  constructor(
    stage: HTMLElement,
    private readonly canvas: HTMLCanvasElement,
    private readonly cardFor: (entry: LeaderboardEntry, rank: number) => Promise<string>,
  ) {
    this.ctx = canvas.getContext('2d');
    this.overlay = document.createElement('div');
    this.overlay.className = 'celebrate';
    this.overlay.innerHTML = `<div class="cel-rays"></div><div class="cel-flash"></div>
      <div class="cel-inner" role="status" aria-live="polite" aria-atomic="true"><div class="cel-headline"></div><img class="cel-card" alt="" /><div class="cel-sub"></div></div>`;
    stage.appendChild(this.overlay);
    this.headline = this.overlay.querySelector<HTMLElement>('.cel-headline')!;
    this.sub = this.overlay.querySelector<HTMLElement>('.cel-sub')!;
    this.img = this.overlay.querySelector<HTMLImageElement>('.cel-card')!;
  }

  get busy(): boolean {
    return this.active !== null || this.gapTimer !== null || this.queue.length > 0;
  }

  enqueue(entry: LeaderboardEntry, rank: number): void {
    this.queue.push({ entry, rank });
    this.queue.sort((a, b) => a.rank - b.rank);
    // During the short gap between celebrations the pending timer will pick this up.
    if (!this.active && this.gapTimer === null) void this.next(performance.now());
  }

  private async next(now: number): Promise<void> {
    const item = this.queue.shift();
    if (!item) return;
    const champ = item.rank === 1;
    this.active = { rank: item.rank, until: now + (champ ? 8000 : 6000), nextBurst: now };
    const url = await this.cardFor(item.entry, item.rank);
    this.img.src = url;
    this.headline.textContent = champ ? 'NEW CHAMPION!' : item.rank <= 3 ? `PODIUM! #${item.rank}` : `ON THE WALL! #${item.rank}`;
    this.sub.innerHTML = champ
      ? `<b>${escapeHtml(item.entry.name)}</b> takes the crown with <b>${formatNum(item.entry.score)}</b>!`
      : `<b>${escapeHtml(item.entry.name)}</b> scored <b>${formatNum(item.entry.score)}</b>`;
    this.overlay.classList.toggle('champion', champ);
    this.overlay.classList.remove('show');
    void this.overlay.offsetWidth; // restart CSS animations
    this.overlay.classList.add('show');
    if (!reducedMotion()) this.confetti(champ ? 320 : 160);
  }

  private confetti(n: number): void {
    const W = this.canvas.width;
    for (let i = 0; i < n; i++) {
      const left = i % 2 === 0;
      this.spawn({
        x: left ? -20 : W + 20,
        y: 700 + Math.random() * 300,
        vx: (left ? 1 : -1) * (500 + Math.random() * 900),
        vy: -900 - Math.random() * 900,
        spark: false,
      });
    }
  }

  private burst(): void {
    const cx = 200 + Math.random() * (this.canvas.width - 400);
    const cy = 120 + Math.random() * 420;
    const color = COLORS[Math.floor(Math.random() * COLORS.length)]!;
    for (let i = 0; i < 70; i++) {
      const a = (i / 70) * Math.PI * 2;
      const sp = 250 + Math.random() * 350;
      this.spawn({ x: cx, y: cy, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp, spark: true, color });
    }
  }

  private spawn(o: { x: number; y: number; vx: number; vy: number; spark: boolean; color?: string }): void {
    if (this.ps.length >= MAX_P) return;
    this.ps.push({
      ...o,
      rot: Math.random() * 6,
      vr: (Math.random() - 0.5) * 14,
      w: o.spark ? 6 : 12 + Math.random() * 10,
      h: o.spark ? 6 : 7 + Math.random() * 6,
      color: o.color ?? COLORS[Math.floor(Math.random() * COLORS.length)]!,
      life: o.spark ? 1.3 : 4.5,
    });
  }

  tick(now: number, dt: number): void {
    const a = this.active;
    if (a) {
      if (a.rank === 1 && now >= a.nextBurst && !reducedMotion()) {
        this.burst();
        a.nextBurst = now + 450;
      }
      if (now >= a.until) {
        this.active = null;
        this.overlay.classList.remove('show');
        if (this.queue.length && this.gapTimer === null) {
          this.gapTimer = window.setTimeout(() => {
            this.gapTimer = null;
            void this.next(performance.now());
          }, 500);
        }
      }
    }
    const ctx = this.ctx;
    if (!ctx) return;
    if (this.ps.length === 0) return;
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    for (let i = this.ps.length - 1; i >= 0; i--) {
      const p = this.ps[i]!;
      p.life -= dt;
      if (p.life <= 0 || p.y > this.canvas.height + 50) {
        this.ps[i] = this.ps[this.ps.length - 1]!;
        this.ps.pop();
        continue;
      }
      if (p.spark) {
        p.vx *= 1 - dt * 1.8;
        p.vy = p.vy * (1 - dt * 1.8) + 220 * dt;
      } else {
        p.vx *= 1 - dt * 1.2;
        p.vy = Math.min(p.vy + 1400 * dt, 260);
      }
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.rot += p.vr * dt;
      ctx.globalAlpha = Math.min(1, p.life * 1.5);
      ctx.fillStyle = p.color;
      if (p.spark) {
        ctx.fillRect(p.x - 3, p.y - 3, p.w, p.h);
      } else {
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        ctx.scale(1, Math.cos(p.rot * 1.7));
        ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
        ctx.restore();
      }
    }
    ctx.globalAlpha = 1;
    if (this.ps.length === 0) ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }
}
