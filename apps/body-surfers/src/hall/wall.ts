// The top-10 wall of trading cards (FLIP-animated reorders, pop-in for new entries).
import type { LeaderboardEntry } from '../shared/types';
import { MAX_LEADERBOARD } from '../shared/constants';
import { renderSelfieCard } from './card';
import { relativeDate } from './data';
import { reducedMotion } from '../shared/motion';

interface Slot {
  el: HTMLDivElement;
  img: HTMLImageElement;
  key: string;
}

const MAX_CACHE = 60;

export class Wall {
  private readonly slots = new Map<string, Slot>();
  private readonly cache = new Map<string, Promise<string>>();
  private readonly empties: HTMLDivElement[] = [];
  private board: LeaderboardEntry[] = [];
  private spotIndex = -1;

  constructor(private readonly root: HTMLElement) {
    for (let i = 0; i < MAX_LEADERBOARD; i++) {
      const d = document.createElement('div');
      d.className = 'slot-empty';
      d.innerHTML = `<span class="slot-rank">#${i + 1}</span><span class="slot-text">YOUR<br>FACE<br>HERE</span>`;
      this.empties.push(d);
    }
  }

  get entries(): readonly LeaderboardEntry[] {
    return this.board;
  }

  /** Card bitmap (cached) for an entry at a rank. */
  cardFor(entry: LeaderboardEntry, rank: number): Promise<string> {
    const now = Date.now();
    const key = `${entry.id}|${rank}|${entry.name}|${entry.score}|${entry.coins}|${entry.distance}|${relativeDate(entry.date, now)}|${entry.photo?.length ?? 0}`;
    let p = this.cache.get(key);
    if (!p) {
      p = renderSelfieCard(entry, rank, now).catch(() => '');
      this.cache.set(key, p);
      if (this.cache.size > MAX_CACHE) {
        const first = this.cache.keys().next().value;
        if (first !== undefined) this.cache.delete(first);
      }
    }
    return p;
  }

  update(board: LeaderboardEntry[], newIds: ReadonlySet<string>): void {
    this.board = board;
    const before = new Map<string, DOMRect>();
    for (const [id, s] of this.slots) before.set(id, s.el.getBoundingClientRect());

    const keep = new Set(board.map((e) => e.id));
    for (const [id, s] of this.slots) {
      if (!keep.has(id)) {
        s.el.remove();
        this.slots.delete(id);
      }
    }
    board.forEach((entry, i) => {
      let s = this.slots.get(entry.id);
      if (!s) {
        const el = document.createElement('div');
        el.className = 'card';
        const img = document.createElement('img');
        img.alt = '';
        img.decoding = 'async';
        el.appendChild(img);
        s = { el, img, key: '' };
        this.slots.set(entry.id, s);
      }
      const slot = s;
      slot.el.style.setProperty('--i', String(i));
      slot.el.classList.toggle('rank-1', i === 0);
      this.root.appendChild(slot.el);
      const rank = i + 1;
      const key = `${rank}`;
      const p = this.cardFor(entry, rank);
      slot.key = key + entry.id;
      const want = slot.key;
      void p.then((url) => {
        if (url && slot.key === want && slot.img.src !== url) slot.img.src = url;
      });
    });
    for (let i = board.length; i < MAX_LEADERBOARD; i++) this.root.appendChild(this.empties[i]!);
    for (let i = 0; i < board.length; i++) this.empties[i]!.remove();
    this.root.classList.toggle('is-empty', board.length === 0);

    // FLIP (skipped for prefers-reduced-motion: cards just appear in place)
    const calm = reducedMotion();
    for (const [id, s] of this.slots) {
      if (calm) {
        if (!before.has(id) && newIds.has(id)) {
          s.el.classList.add('is-new');
          window.setTimeout(() => s.el.classList.remove('is-new'), 12000);
        }
        continue;
      }
      const old = before.get(id);
      if (old) {
        const now = s.el.getBoundingClientRect();
        const dx = old.left - now.left;
        const dy = old.top - now.top;
        if (Math.abs(dx) > 1 || Math.abs(dy) > 1) {
          // rects are in screen space; the stage is scaled, so convert back
          const k = now.width / (s.el.offsetWidth || now.width);
          s.el.animate(
            [{ transform: `translate(${dx / k}px, ${dy / k}px)` }, { transform: 'translate(0,0)' }],
            { duration: 700, easing: 'cubic-bezier(.2,1.3,.4,1)' },
          );
        }
      } else if (newIds.has(id)) {
        s.el.classList.add('is-new');
        window.setTimeout(() => s.el.classList.remove('is-new'), 12000);
        s.el.animate(
          [
            { transform: 'scale(0.2) rotate(-25deg)', opacity: 0 },
            { transform: 'scale(1.25) rotate(4deg)', opacity: 1, offset: 0.7 },
            { transform: 'scale(1) rotate(0deg)', opacity: 1 },
          ],
          { duration: 900, delay: 300, easing: 'ease-out', fill: 'backwards' },
        );
      } else {
        s.el.animate(
          [
            { transform: 'translateY(40px) scale(0.85)', opacity: 0 },
            { transform: 'none', opacity: 1 },
          ],
          { duration: 600, delay: Number(s.el.style.getPropertyValue('--i')) * 70, easing: 'cubic-bezier(.2,1.4,.4,1)', fill: 'backwards' },
        );
      }
    }
  }

  /** Re-render cards whose relative date text changed. */
  refreshDates(): void {
    this.update(this.board, new Set());
  }

  /** Attract-loop spotlight: highlight the next card (or clear with null). */
  spotlightNext(on: boolean): void {
    for (const s of this.slots.values()) s.el.classList.remove('spot');
    if (!on || this.board.length === 0) {
      this.spotIndex = -1;
      return;
    }
    this.spotIndex = (this.spotIndex + 1) % this.board.length;
    const e = this.board[this.spotIndex];
    if (e) this.slots.get(e.id)?.el.classList.add('spot');
  }
}
