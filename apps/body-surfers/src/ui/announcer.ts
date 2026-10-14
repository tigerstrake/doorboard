// Screen-reader announcements: a visually hidden aria-live region fed from the UIModel.
// Only speaks on changes (screen, countdown number, toast, final result), never per frame.
import type { UIModel } from '../shared/types';
import { h } from './dom';
import { fmtGrouped } from './copy';

/** What should be announced for this model, or null for nothing new (pure: unit-tested). */
export function announcement(m: UIModel): string | null {
  switch (m.screen) {
    case 'boot':
      return m.boot.error ?? null;
    case 'lobby':
      return 'Lobby. Raise both hands to start, or press Enter.';
    case 'countdown':
      return m.countdown.value > 0 ? String(m.countdown.value) : 'Go!';
    case 'paused':
      return m.paused?.reason === 'tracking-lost' ? 'Paused: we lost sight of you. Step back into view.' : 'Paused.';
    case 'gameover': {
      const rs = m.gameOver?.results ?? [];
      if (rs.length === 0) return 'Game over.';
      const parts = rs.map((r) => {
        const who = rs.length > 1 ? `${r.name}: ` : '';
        const rank = r.rank !== null ? `, rank ${r.rank} on the leaderboard` : '';
        return `${who}${fmtGrouped(r.score)} points${rank}`;
      });
      return `Game over. ${parts.join('. ')}.`;
    }
    default:
      return null;
  }
}

export class Announcer {
  readonly root: HTMLElement;
  private last: string | null = null;
  private lastToast = -1;

  constructor() {
    this.root = h('div', 'bs-sr-only');
    this.root.setAttribute('aria-live', 'polite');
    this.root.setAttribute('aria-atomic', 'true');
    this.root.setAttribute('role', 'status');
  }

  update(m: UIModel): void {
    if (m.toast && m.toast.t !== this.lastToast) {
      this.lastToast = m.toast.t;
      this.say(m.toast.text);
      return;
    }
    const text = announcement(m);
    if (text !== null && text !== this.last) this.say(text);
    else if (text === null) this.last = null;
  }

  private say(text: string): void {
    this.last = text;
    this.root.textContent = text;
  }
}
