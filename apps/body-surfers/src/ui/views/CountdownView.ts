// Countdown: huge 3 / 2 / 1 / GO! pop + one short line ("Stand still…") whose pill fills
// with the calibration progress. Per-player bars only show in 2P.
import type { UIModel } from '../../shared/types';
import { PLAYER_COLORS, PLAYER_LABELS } from '../../shared/constants';
import { Attr, button, CssNum, Flag, h, icon, pop, Show, Text } from '../dom';
import { ICONS } from '../icons';
import type { Emit, ScreenView } from './common';

interface CalRow {
  show: Show;
  fill: CssNum;
  done: Flag;
}

const VALUE_TEXT = ['GO!', '1', '2', '3'];
const VALUE_ATTR = ['go', '1', '2', '3'];

export class CountdownView implements ScreenView {
  readonly root: HTMLElement;
  private readonly num: HTMLElement;
  private readonly numText: Text;
  private readonly numAttr: Attr;
  private readonly label: Text;
  private readonly labelShow: Show;
  private readonly labelFill: CssNum;
  private readonly cal: Show;
  private readonly help: Show;
  private readonly rows: CalRow[] = [];
  private lastValue = -1;

  constructor(emit: Emit) {
    this.num = h('div', 'bs-count__num bs-display');
    const label = h('div', 'bs-count__label');
    const calEl = h('div', 'bs-count__cal');
    for (let i = 0; i < 2; i++) {
      const fill = h('span', 'bs-calbar__fill');
      const row = h(
        'div',
        'bs-calbar',
        h('span', 'bs-calbar__tag', PLAYER_LABELS[i]),
        h('span', 'bs-calbar__track', fill),
        icon(ICONS.check, 'bs-calbar__check'),
      );
      row.style.setProperty('--pc', PLAYER_COLORS[i]);
      calEl.append(row);
      this.rows.push({ show: new Show(row), fill: new CssNum(row, '--p', 0.01), done: new Flag(row, 'is-done') });
    }
    const settings = button('bs-btn bs-btn--blue', 'CAMERA SETTINGS');
    const keyboard = button('bs-btn bs-btn--yellow', 'PLAY WITH KEYBOARD');
    settings.addEventListener('click', () => emit({ type: 'settings-open', open: true }));
    keyboard.addEventListener('click', () => emit({ type: 'keyboard-only' }));
    const help = h('div', 'bs-count__help', settings, keyboard);
    this.help = new Show(help);
    this.root = h('section', 'bs-screen bs-count', h('div', 'bs-count__burst'), this.num, h('div', 'bs-count__info', label, calEl, help), h('div', 'bs-count__camslot'));
    this.numText = new Text(this.num);
    this.numAttr = new Attr(this.root, 'data-value');
    this.label = new Text(label);
    this.labelShow = new Show(label);
    this.labelFill = new CssNum(label, '--p', 0.01);
    this.cal = new Show(calEl);
  }

  enter(): void {
    this.lastValue = -1;
  }

  update(m: UIModel): void {
    const c = m.countdown;
    const v = Math.max(0, Math.min(3, Math.ceil(c.value)));
    this.numText.set(VALUE_TEXT[v]);
    this.numAttr.set(VALUE_ATTR[v]);
    if (v !== this.lastValue) {
      this.lastValue = v;
      if (v === 0) {
        pop(this.num, [
          { transform: 'scale(0.2) rotate(-25deg)', opacity: 0 },
          { transform: 'scale(1.35) rotate(6deg)', opacity: 1, offset: 0.55 },
          { transform: 'scale(1) rotate(-3deg)', opacity: 1 },
        ], 520);
      } else {
        pop(this.num, [
          { transform: 'scale(2.6) rotate(12deg)', opacity: 0 },
          { transform: 'scale(0.86) rotate(-4deg)', opacity: 1, offset: 0.6 },
          { transform: 'scale(1) rotate(0deg)', opacity: 1 },
        ], 480, 'cubic-bezier(.2,.9,.3,1)');
      }
    }

    const keyboard = m.inputMode === 'keyboard';
    this.help.set(!keyboard && v > 0 && c.message !== null);
    const n = keyboard ? 0 : Math.min(c.calibration.length, m.playerCount, 2);
    let allDone = true;
    let minP = 1;
    for (let i = 0; i < 2; i++) {
      const visible = i < n;
      this.rows[i].show.set(visible);
      if (!visible) continue;
      const p = Math.max(0, Math.min(1, c.calibration[i]));
      if (p < minP) minP = p;
      this.rows[i].fill.set(p);
      this.rows[i].done.set(p >= 1);
      if (p < 1) allDone = false;
    }
    this.cal.set(n > 1 && v > 0);
    let text: string;
    if (c.message) text = c.message;
    else if (v === 0) text = 'GO!';
    else if (keyboard || n === 0) text = 'Get ready…';
    else text = allDone ? 'Got you!' : 'Stand still…';
    this.label.set(text);
    this.labelFill.set(!keyboard && n > 0 && c.message === null ? minP : 0);
    this.labelShow.set(v > 0 || c.message !== null);
  }
}
