// Boot: big logo, one huge ▶ START button and one line of text; loading bar after the click;
// camera error card with keyboard fallback.
import type { UIModel } from '../../shared/types';
import { button, CssNum, Flag, h, icon, Show, Text } from '../dom';
import { ICONS } from '../icons';
import { friendlyError } from '../copy';
import { ControlsLine, type Emit, type ScreenView, makeLogo } from './common';

const DEBUG_URL = typeof location !== 'undefined' && /[?&]debug=(1|true)\b/.test(location.search);

export class BootView implements ScreenView {
  readonly root: HTMLElement;
  private readonly clickable: Flag;
  private readonly hasErr: Flag;
  private readonly start: Show;
  private readonly load: Show;
  private readonly bar: CssNum;
  private readonly msg: Text;
  private readonly pct: Text;
  private readonly err: Show;
  private readonly errTitle: Text;
  private readonly errBody: Text;
  private readonly errRaw: Text;
  private readonly rawShow: Show;
  private readonly tips: HTMLElement;
  private readonly controls = new ControlsLine('bs-boot__controls');
  private lastError: string | null = null;

  constructor(emit: Emit) {
    const startBtn = button('bs-btn bs-btn--green bs-btn--xl bs-boot__start', '▶ START');
    const startWrap = h('div', 'bs-boot__startwrap', startBtn, this.controls.root, h('p', 'bs-boot__line', 'Uses your camera. Stand back ~2 m.'));

    const barFill = h('span', 'bs-bar__fill');
    const barEl = h('div', 'bs-bar', barFill);
    const msgEl = h('span', 'bs-boot__msg');
    const pctEl = h('span', 'bs-boot__pct');
    const loadEl = h('div', 'bs-boot__load', barEl, h('div', 'bs-boot__msgrow', msgEl, pctEl));

    const errTitle = h('h2', 'bs-err__title bs-display');
    const errBody = h('p', 'bs-err__body');
    const errRaw = h('p', 'bs-err__raw');
    const retry = button('bs-btn bs-btn--blue', 'TRY AGAIN');
    const kb = button('bs-btn bs-btn--yellow', 'PLAY WITH KEYBOARD');
    retry.addEventListener('click', (e) => {
      e.stopPropagation();
      emit({ type: 'boot-click' });
    });
    kb.addEventListener('click', (e) => {
      e.stopPropagation();
      emit({ type: 'keyboard-only' });
    });
    const tips = h('ul', 'bs-err__tips');
    this.tips = tips;
    const errEl = h(
      'div',
      'bs-err bs-sticker',
      icon(ICONS.cameraOff, 'bs-err__icon'),
      errTitle,
      errBody,
      tips,
      h('div', 'bs-err__actions', kb, retry),
      errRaw,
    );

    this.root = h(
      'section',
      'bs-screen bs-boot',
      h('div', 'bs-boot__glow'),
      h('div', 'bs-boot__center', makeLogo('bs-logo--boot'), startWrap, loadEl, errEl),
    );
    // Booth-friendly: while the big button is up, a click anywhere starts.
    this.root.addEventListener('click', () => {
      if (this.clickable.value) emit({ type: 'boot-click' });
    });

    this.clickable = new Flag(this.root, 'is-clickable');
    this.hasErr = new Flag(this.root, 'has-error');
    this.start = new Show(startWrap);
    this.load = new Show(loadEl);
    this.bar = new CssNum(barEl, '--p', 0.005);
    this.msg = new Text(msgEl);
    this.pct = new Text(pctEl);
    this.err = new Show(errEl);
    this.errTitle = new Text(errTitle);
    this.errBody = new Text(errBody);
    this.errRaw = new Text(errRaw);
    this.rawShow = new Show(errRaw);
  }

  update(m: UIModel): void {
    const b = m.boot;
    const hasError = b.error !== null && b.error !== '';
    this.controls.set(m.inputMode === 'keyboard');
    // The raw error is for developers only (?debug=1 or the debug overlay).
    this.rawShow.set(DEBUG_URL || m.debug !== null);
    this.clickable.set(b.needsClick && !hasError);
    this.start.set(b.needsClick && !hasError);
    this.err.set(hasError);
    this.hasErr.set(hasError);
    if (hasError && b.error !== this.lastError) {
      this.lastError = b.error;
      const f = friendlyError(b.error ?? '');
      this.errTitle.set(f.title);
      this.errBody.set(f.body);
      this.errRaw.set(b.error ?? '');
      this.tips.replaceChildren(...f.tips.map((t) => h('li', null, t)));
      this.root.dataset.errorKind = f.kind;
    }
    const p = Math.max(0, Math.min(1, b.progress));
    const loading = p < 1;
    // One thing at a time: the START button, then (after the click) the loading bar.
    this.load.set(!hasError && !b.needsClick);
    this.bar.set(p);
    this.msg.set(b.message || (loading ? 'Loading…' : 'Ready!'));
    this.pct.set(loading ? `${Math.round(p * 100)}%` : '');
  }
}
