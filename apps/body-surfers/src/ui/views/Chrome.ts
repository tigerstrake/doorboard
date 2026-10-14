// Always-on chrome: toast banner, debug stats overlay, corner buttons (mute / settings / fullscreen).
import type { Screen, UIModel } from '../../shared/types';
import { button, Flag, h, icon, pop, Show, Text } from '../dom';
import { ICONS } from '../icons';
import { DEBUG_REFRESH_MS, TOAST_MS } from '../config';
import type { Emit } from './common';

export class Toast {
  readonly root: HTMLElement;
  private readonly show: Show;
  private readonly text: Text;
  private lastT = Number.NaN;
  private lastText = '';
  private wasVisible = false;

  constructor() {
    const inner = h('div', 'bs-toast__inner bs-display');
    this.root = h('div', 'bs-toast', inner);
    this.show = new Show(this.root);
    this.text = new Text(inner);
    this.show.set(false);
  }

  update(m: UIModel, now: number): void {
    const t = m.toast;
    const visible = t !== null && now - t.t < TOAST_MS && now >= t.t - 50;
    this.show.set(visible);
    const wasVisible = this.wasVisible;
    this.wasVisible = visible;
    if (!visible || !t) return;
    this.text.set(t.text);
    if (t.t !== this.lastT) {
      this.lastT = t.t;
      // A re-raised sticky toast (same text, still on screen) must not bounce again.
      const sameText = t.text === this.lastText;
      this.lastText = t.text;
      if (sameText && wasVisible) return;
      pop(this.root, [
        { transform: 'translateX(-50%) scale(.3) rotate(-10deg)', opacity: 0 },
        { transform: 'translateX(-50%) scale(1.12) rotate(2deg)', opacity: 1, offset: 0.6 },
        { transform: 'translateX(-50%) scale(1) rotate(-2deg)', opacity: 1, offset: 0.85 },
        { transform: 'translateX(-50%) scale(1) rotate(-2deg)', opacity: 1 },
      ], 520);
    }
    // Fade out over the last 250 ms.
    const left = TOAST_MS - (now - t.t);
    (this.root as HTMLElement).style.opacity = left < 250 ? (left / 250).toFixed(2) : '';
  }
}

export class DebugOverlay {
  readonly root: HTMLElement;
  private readonly show: Show;
  private readonly text: Text;
  private nextAt = 0;

  constructor() {
    const pre = h('pre', 'bs-debug__pre');
    this.root = h('div', 'bs-debug', pre);
    this.show = new Show(this.root);
    this.text = new Text(pre);
    this.show.set(false);
  }

  update(m: UIModel, now: number): void {
    const d = m.debug;
    this.show.set(d !== null);
    if (!d || now < this.nextAt) return;
    this.nextAt = now + DEBUG_REFRESH_MS;
    const f = m.vision.frame;
    this.text.set(
      `FPS     ${d.fps.toFixed(0).padStart(4)}   frame ${d.frameMs.toFixed(1)} ms\n` +
        `VISION  ${d.visionFps.toFixed(0).padStart(4)}   infer ${d.inferenceMs.toFixed(1)} ms\n` +
        `DRAWS   ${String(d.drawCalls).padStart(4)}   ${m.screen} ${m.playerCount}P ${m.inputMode}\n` +
        `CAMERA  ${m.vision.status}  people ${f ? f.people.length : 0}+${f ? f.extraPeople : 0}`,
    );
  }
}

const CORNER_SCREENS: readonly Screen[] = ['boot', 'lobby', 'paused', 'gameover'];

export class CornerButtons {
  readonly root: HTMLElement;
  private readonly show: Show;
  private readonly muted: Flag;
  private isMuted = false;
  private readonly muteBtn: HTMLButtonElement;

  constructor(emit: Emit) {
    const mute = button('bs-corner__btn bs-corner__mute', icon(ICONS.speaker, 'bs-corner__icon'), h('i', 'bs-corner__slash'));
    mute.title = 'Mute (M)';
    mute.setAttribute('aria-label', 'Mute sound');
    mute.setAttribute('aria-pressed', 'false');
    this.muteBtn = mute;
    const gear = button('bs-corner__btn', icon(ICONS.gear, 'bs-corner__icon'));
    gear.title = 'Settings (`)';
    gear.setAttribute('aria-label', 'Settings');
    const full = button('bs-corner__btn', icon(ICONS.fullscreen, 'bs-corner__icon'));
    full.title = 'Fullscreen (F)';
    full.setAttribute('aria-label', 'Fullscreen');
    mute.addEventListener('click', (e) => {
      e.stopPropagation();
      emit({ type: 'settings-change', settings: { muted: !this.isMuted } });
    });
    gear.addEventListener('click', (e) => {
      e.stopPropagation();
      emit({ type: 'settings-open', open: true });
    });
    full.addEventListener('click', (e) => {
      e.stopPropagation();
      emit({ type: 'fullscreen' });
    });
    this.root = h('div', 'bs-corner', mute, gear, full);
    this.show = new Show(this.root);
    this.muted = new Flag(mute, 'is-muted');
  }

  update(m: UIModel): void {
    this.show.set(CORNER_SCREENS.includes(m.screen) && !m.settingsOpen);
    if (m.settings.muted !== this.isMuted) this.muteBtn.setAttribute('aria-pressed', String(m.settings.muted));
    this.isMuted = m.settings.muted;
    this.muted.set(m.settings.muted);
  }
}
