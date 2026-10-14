// Paused: dim overlay; user pause vs. tracking lost (with missing players); resume countdown.
import type { UIModel } from '../../shared/types';
import { PLAYER_COLORS, PLAYER_LABELS } from '../../shared/constants';
import { Attr, button, h, icon, NumText, pop, Show, Text } from '../dom';
import { ICONS } from '../icons';
import type { Emit, ScreenView } from './common';

export class PausedView implements ScreenView {
  readonly root: HTMLElement;
  private readonly reason: Attr;
  private readonly title: Text;
  private readonly sub: Text;
  private readonly missing: Show[] = [];
  private readonly missingWrap: Show;
  private readonly resume: Show;
  private readonly resumeNum: NumText;
  private readonly actions: Show;
  private readonly resumeBtn: Show;
  private readonly settingsBtn: Show;
  private lastResume = -1;

  constructor(emit: Emit) {
    const title = h('h1', 'bs-paused__title bs-display');
    const sub = h('p', 'bs-paused__sub');
    const missingEl = h('div', 'bs-paused__missing');
    for (let i = 0; i < 2; i++) {
      const chip = h(
        'div',
        'bs-missing',
        h('span', 'bs-missing__tag bs-display', PLAYER_LABELS[i]),
        h('span', 'bs-missing__text', 'Step back into view'),
      );
      chip.style.setProperty('--pc', PLAYER_COLORS[i]);
      missingEl.append(chip);
      this.missing.push(new Show(chip));
    }
    const resumeNum = h('span', 'bs-paused__num bs-display');
    const resumeEl = h('div', 'bs-paused__resume', h('span', 'bs-paused__resumelabel bs-display', 'GET READY'), resumeNum);

    const resumeBtn = button('bs-btn bs-btn--green', icon(ICONS.play, 'bs-btn__icon'), 'RESUME');
    const settingsBtn = button('bs-btn bs-btn--blue', icon(ICONS.gear, 'bs-btn__icon'), 'SETTINGS');
    const menuBtn = button('bs-btn bs-btn--red', 'QUIT TO MENU');
    resumeBtn.addEventListener('click', () => emit({ type: 'pause-toggle' }));
    settingsBtn.addEventListener('click', () => emit({ type: 'settings-open', open: true }));
    menuBtn.addEventListener('click', () => emit({ type: 'to-lobby' }));
    const actions = h('div', 'bs-paused__actions', resumeBtn, settingsBtn, menuBtn);

    this.root = h(
      'section',
      'bs-screen bs-paused',
      h('div', 'bs-paused__dim'),
      h('div', 'bs-paused__panel', title, sub, missingEl, h('div', 'bs-paused__camslot'), resumeEl, actions),
    );
    this.reason = new Attr(this.root, 'data-reason');
    this.title = new Text(title);
    this.sub = new Text(sub);
    this.missingWrap = new Show(missingEl);
    this.resume = new Show(resumeEl);
    this.resumeNum = new NumText(resumeNum);
    this.actions = new Show(actions);
    this.resumeBtn = new Show(resumeBtn);
    this.settingsBtn = new Show(settingsBtn);
  }

  enter(): void {
    this.lastResume = -1;
  }

  update(m: UIModel): void {
    const p = m.paused;
    const reason = p?.reason ?? 'user';
    this.reason.set(reason);
    const keyboard = m.inputMode === 'keyboard';
    const resumeIn = p?.resumeIn ?? null;

    if (reason === 'tracking-lost') {
      this.title.set('WE LOST YOU!');
      this.sub.set(resumeIn !== null ? 'Found you! Hold your spot…' : 'Step back into view of the camera');
    } else {
      this.title.set('PAUSED');
      this.sub.set(keyboard ? 'Press P to resume' : 'Raise both hands or press P to resume');
    }

    let anyMissing = false;
    for (let i = 0; i < 2; i++) {
      const miss = reason === 'tracking-lost' && p !== null && p.missing.includes(i as 0 | 1) && i < m.playerCount;
      this.missing[i].set(miss);
      if (miss) anyMissing = true;
    }
    this.missingWrap.set(anyMissing && resumeIn === null);

    const counting = resumeIn !== null;
    this.resume.set(counting);
    if (counting) {
      const n = Math.max(1, Math.ceil(resumeIn));
      if (this.resumeNum.set(n) && n !== this.lastResume) {
        pop(this.resumeNum.el, [
          { transform: 'scale(2.2) rotate(10deg)', opacity: 0 },
          { transform: 'scale(1) rotate(0deg)', opacity: 1 },
        ], 420);
      }
      this.lastResume = n;
    } else {
      this.lastResume = -1;
    }
    this.actions.set(!counting);
    this.resumeBtn.set(reason === 'user');
    this.settingsBtn.set(reason === 'user');
  }
}

