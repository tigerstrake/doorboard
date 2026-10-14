// "Top surfers" sticker panel: crash-selfie photos, names, scores. Used by lobby + game over.
// Rows are pooled; each field is cached so per-frame updates are no-ops unless data changed.
import type { LeaderboardEntry } from '../../shared/types';
import { Attr, Flag, h, icon, ImgSrc, NumText, Show, Text } from '../dom';
import { ICONS } from '../icons';
import { placeholderAvatar } from '../avatar';
import { fmtGrouped } from '../copy';

interface Row {
  root: HTMLLIElement;
  show: Show;
  rank: NumText;
  img: ImgSrc;
  name: Text;
  score: NumText;
  mode: Show;
  hl: Flag;
  medal: Attr;
  gap: Flag;
}

export class LeaderboardPanel {
  readonly root: HTMLElement;
  private readonly list: HTMLOListElement;
  private readonly empty: Show;
  private readonly rows: Row[] = [];
  /** Index into entries for each visible row (reused, no per-frame allocation). */
  private readonly order: number[] = [];

  constructor(
    private readonly maxRows: number,
    title: string,
    emptyText: string,
    extraClass = '',
  ) {
    this.list = h('ol', 'bs-lb__list');
    const emptyEl = h('div', 'bs-lb__empty', icon(ICONS.trophy, 'bs-lb__empty-icon'), h('span', null, emptyText));
    this.empty = new Show(emptyEl);
    this.root = h(
      'aside',
      `bs-lb bs-sticker ${extraClass}`.trim(),
      h('header', 'bs-lb__head', icon(ICONS.trophy, 'bs-lb__trophy'), h('span', 'bs-lb__title bs-display', title)),
      this.list,
      emptyEl,
    );
    for (let i = 0; i < maxRows; i++) this.rows.push(this.makeRow());
  }

  private makeRow(): Row {
    const rankEl = h('span', 'bs-lb__rank');
    const img = h('img', 'bs-lb__img');
    img.alt = '';
    img.decoding = 'async';
    img.draggable = false;
    const nameEl = h('span', 'bs-lb__name');
    const modeEl = h('span', 'bs-lb__mode', '2P');
    const scoreEl = h('span', 'bs-lb__score');
    const you = h('span', 'bs-lb__you', 'YOU');
    const root = h('li', 'bs-lb__row', rankEl, h('span', 'bs-lb__photo', img), h('span', 'bs-lb__who', nameEl, modeEl), scoreEl, you);
    this.list.append(root);
    return {
      root,
      show: new Show(root),
      rank: new NumText(rankEl),
      img: new ImgSrc(img),
      name: new Text(nameEl),
      score: new NumText(scoreEl, fmtGrouped),
      mode: new Show(modeEl),
      hl: new Flag(root, 'is-highlight'),
      medal: new Attr(root, 'data-medal'),
      gap: new Flag(root, 'is-gap'),
    };
  }

  /**
   * @param highlight entry ids to highlight (the current game's results); entries ranked
   * below the visible rows are swapped into the last row(s) so players always see themselves.
   */
  update(entries: readonly LeaderboardEntry[], highlight: readonly (string | null)[] = []): void {
    const n = Math.min(entries.length, this.maxRows);
    // Which entries to show: top N, but make room for highlighted entries ranked lower.
    let extra = 0;
    for (let i = this.maxRows; i < entries.length; i++) if (isIn(entries[i].id, highlight)) extra++;
    extra = Math.min(extra, n);
    this.order.length = 0;
    for (let i = 0; i < n - extra; i++) this.order.push(i);
    if (extra > 0) for (let i = this.maxRows; i < entries.length; i++) if (isIn(entries[i].id, highlight)) this.order.push(i);

    for (let r = 0; r < this.rows.length; r++) {
      const row = this.rows[r];
      if (r >= this.order.length) {
        row.show.set(false);
        continue;
      }
      const idx = this.order[r];
      const e = entries[idx];
      row.show.set(true);
      row.rank.set(idx + 1);
      row.medal.set(MEDALS[Math.min(idx, 3)]);
      row.gap.set(r > 0 && idx !== this.order[r - 1] + 1);
      row.img.set(e.photo ?? placeholderAvatar(e.id || e.name));
      row.name.set(e.name);
      row.score.set(e.score);
      row.mode.set(e.mode === 2);
      row.hl.set(isIn(e.id, highlight));
    }
    this.empty.set(n === 0);
  }
}

const MEDALS = ['gold', 'silver', 'bronze', 'none'] as const;

function isIn(id: string, list: readonly (string | null)[]): boolean {
  for (let i = 0; i < list.length; i++) if (list[i] === id) return true;
  return false;
}
