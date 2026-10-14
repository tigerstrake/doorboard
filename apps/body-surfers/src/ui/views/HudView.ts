// In-game HUD: per player score, coins, multiplier badge, power-up timers, tutorial hint
// banner with pictogram; 2P split chrome with divider, colored P1/P2 tags, OUT and WINNER.
import type { PlayerCount, PlayerId, PowerUpKind, TutorialHint, UIModel, WorldSnapshot } from '../../shared/types';
import { PLAYER_COLORS, PLAYER_LABELS } from '../../shared/constants';
import { Attr, button, CssNum, Flag, h, icon, NumText, pop, Show, Text } from '../dom';
import { ICONS } from '../icons';
import { fmtGrouped, fmtScore, POWERUP_LABEL, POWERUP_ORDER, POWERUP_POP, POWERUP_TOAST, TUTORIAL_COPY } from '../copy';
import { POWERUP_WARN_S } from '../config';
import { type Emit, kbd, picto } from './common';

interface PowerUpRow {
  show: Show;
  fill: CssNum;
  warn: Flag;
  secs: NumText;
}

const POP_MS = 1100;
/** Hint pill exit animation length (keeps it on screen briefly after the hint clears). */
const HINT_OUT_MS = 160;

const HINTS: readonly TutorialHint[] = ['jump', 'duck', 'left', 'right', 'switch'];

class PlayerHud {
  readonly root: HTMLElement;
  private readonly score: NumText;
  private readonly coins: NumText;
  private readonly coinsEl: HTMLElement;
  private readonly mult: NumText;
  private readonly multShow: Show;
  private readonly pus = new Map<PowerUpKind, PowerUpRow>();
  private readonly hintEls = new Map<TutorialHint, { show: Show; keys: HTMLElement }>();
  private readonly hintWrap: Show;
  private readonly hintLeaving: Flag;
  private hintGoneAt = 0;
  private readonly out: Show;
  private readonly outScore: NumText;
  private readonly winner: Flag;
  private readonly crown: Show;
  private readonly dead: Flag;
  private readonly popEl: HTMLElement;
  private readonly popIcon: HTMLElement;
  private readonly popText: Text;
  private readonly popShow: Show;
  private popUntil = 0;
  private readonly tintSlow: Flag;
  private readonly tintStar: Flag;
  private readonly activeKinds = new Set<PowerUpKind>();
  private lastCoins = -1;
  private lastHint: TutorialHint | null = null;
  private keysHint: TutorialHint | null = null;
  private keysKeyboard = false;
  private keysCount = 0;

  constructor(readonly id: PlayerId) {
    const tagText = h('span', 'bs-phud__tagtext', PLAYER_LABELS[id]);
    const crownEl = icon(ICONS.crown, 'bs-phud__crown');
    const tag = h('div', 'bs-phud__tag', crownEl, tagText, h('span', 'bs-phud__wins', 'WINNER'));

    const scoreEl = h('span', 'bs-phud__score bs-display', '0');
    const multEl = h('span', 'bs-phud__mult bs-display', '×2');
    const scoreBox = h('div', 'bs-phud__scorebox', multEl, scoreEl);
    const coinsNum = h('span', 'bs-phud__coinnum bs-display', '0');
    this.coinsEl = h('div', 'bs-phud__coins', icon(ICONS.coin, 'bs-phud__coinicon'), coinsNum);
    const stats = h('div', 'bs-phud__stats', scoreBox, this.coinsEl);

    const pusEl = h('div', 'bs-phud__pus');
    for (const kind of POWERUP_ORDER) {
      const fill = h('i', 'bs-pu__fill');
      const secs = h('span', 'bs-pu__secs');
      const row = h(
        'div',
        `bs-pu bs-pu--${kind}`,
        icon(ICONS[kind], 'bs-pu__icon'),
        h('div', 'bs-pu__body', h('span', 'bs-pu__label', POWERUP_LABEL[kind]), h('span', 'bs-pu__bar', fill)),
        secs,
      );
      pusEl.append(row);
      this.pus.set(kind, {
        show: new Show(row),
        fill: new CssNum(row, '--p', 0.004),
        warn: new Flag(row, 'is-warn'),
        secs: new NumText(secs, (n) => `${n}s`),
      });
    }

    // Tutorial hint: a slim pill in the top callout stack (never over the track centre).
    const hintEl = h('div', 'bs-hint');
    for (const k of HINTS) {
      const keys = h('div', 'bs-hint__keys');
      const el = h(
        'div',
        `bs-hint__card bs-hint__card--${k}`,
        picto(k, 'bs-hint__picto'),
        h('div', 'bs-hint__label bs-display', TUTORIAL_COPY[k].label),
        keys,
      );
      hintEl.append(el);
      this.hintEls.set(k, { show: new Show(el), keys });
    }

    const outScore = h('span', 'bs-phud__outscore');
    const outEl = h('div', 'bs-phud__out', h('div', 'bs-stamp bs-display', 'OUT!'), h('div', 'bs-phud__outline', 'SCORE ', outScore));

    const tint = h('div', 'bs-putint');
    this.popIcon = h('div', 'bs-pupop__icon');
    const popTextEl = h('div', 'bs-pupop__text bs-display');
    this.popEl = h('div', 'bs-pupop', this.popIcon, popTextEl);
    this.popText = new Text(popTextEl);
    this.popShow = new Show(this.popEl);
    this.popShow.set(false);
    this.tintSlow = new Flag(tint, 'is-slowmo');
    this.tintStar = new Flag(tint, 'is-star');

    const callouts = h('div', 'bs-callouts', hintEl, this.popEl);
    this.root = h('div', `bs-phud bs-phud--p${id}`, tint, h('div', 'bs-phud__top', tag, stats), pusEl, callouts, outEl);
    this.root.style.setProperty('--pc', PLAYER_COLORS[id]);

    this.score = new NumText(scoreEl, fmtScore);
    this.coins = new NumText(coinsNum, fmtScore);
    this.mult = new NumText(multEl, (n) => `×${n}`);
    this.multShow = new Show(multEl);
    this.hintWrap = new Show(hintEl);
    this.hintLeaving = new Flag(hintEl, 'is-leaving');
    this.out = new Show(outEl);
    this.outScore = new NumText(outScore, fmtGrouped);
    this.winner = new Flag(this.root, 'is-winner');
    this.crown = new Show(crownEl);
    this.dead = new Flag(this.root, 'is-out');
    for (const r of this.hintEls.values()) r.show.set(false);
    this.hintWrap.set(false);
  }

  update(
    s: WorldSnapshot | undefined,
    playerCount: PlayerCount,
    isOut: boolean,
    isWinner: boolean,
    keyboard: boolean,
    showHint: boolean,
  ): void {
    const twoP = playerCount === 2;
    this.winner.set(twoP && isWinner);
    this.crown.set(twoP && isWinner);
    this.dead.set(twoP && isOut);
    this.out.set(twoP && isOut);
    if (!s) {
      if (this.lastHint) this.hintEls.get(this.lastHint)?.show.set(false);
      this.lastHint = null;
      this.hintLeaving.set(false);
      this.hintWrap.set(false);
      this.tintSlow.set(false);
      this.tintStar.set(false);
      this.popShow.set(false);
      this.activeKinds.clear();
      return;
    }
    const score = Math.floor(s.score);
    this.score.set(score);
    if (twoP && isOut) this.outScore.set(score);

    const coins = s.coins;
    if (this.coins.set(coins) && coins > this.lastCoins && this.lastCoins >= 0) {
      pop(this.coinsEl, [{ transform: 'scale(1.3) rotate(-4deg)' }, { transform: 'scale(1) rotate(0deg)' }], 260, 'ease-out');
    }
    this.lastCoins = coins;

    const mult = Math.round(s.multiplier);
    const multOn = mult > 1;
    if (this.multShow.set(multOn) && multOn) {
      pop(this.mult.el, [{ transform: 'scale(2) rotate(-20deg)' }, { transform: 'scale(1) rotate(-8deg)' }], 420);
    }
    if (multOn) this.mult.set(mult);

    // Power-up timers (fixed order, at most one row per kind).
    for (const kind of POWERUP_ORDER) {
      const row = this.pus.get(kind);
      if (!row) continue;
      let found = -1;
      for (let i = 0; i < s.powerUps.length; i++) if (s.powerUps[i].kind === kind) found = i;
      const active = found >= 0 && s.status !== 'dead';
      row.show.set(active);
      if (!active) continue;
      const pu = s.powerUps[found];
      const frac = pu.duration > 0 ? Math.max(0, Math.min(1, pu.remaining / pu.duration)) : 0;
      row.fill.set(frac);
      row.warn.set(pu.remaining <= POWERUP_WARN_S);
      row.secs.set(Math.ceil(pu.remaining));
    }

    // Big center pop when a new-style power-up first appears; edge tints while active.
    const alive = s.status !== 'dead';
    let slow = false;
    let star = false;
    let fresh: PowerUpKind | null = null;
    const now = performance.now();
    for (const pu of s.powerUps) {
      if (pu.kind === 'slowmo') slow = true;
      if (pu.kind === 'star') star = true;
      if (!this.activeKinds.has(pu.kind) && POWERUP_POP.has(pu.kind)) fresh = pu.kind;
    }
    this.activeKinds.clear();
    for (const pu of s.powerUps) this.activeKinds.add(pu.kind);
    this.tintSlow.set(alive && slow);
    this.tintStar.set(alive && star);
    if (fresh && alive) {
      this.popIcon.innerHTML = ICONS[fresh];
      this.popText.set(POWERUP_TOAST[fresh]);
      this.popShow.set(true);
      this.popUntil = now + POP_MS;
      pop(this.popEl, [
        { transform: 'scale(.4)', opacity: 0 },
        { transform: 'scale(1.1)', opacity: 1, offset: 0.15 },
        { transform: 'scale(1)', opacity: 1, offset: 0.25 },
        { transform: 'scale(1)', opacity: 1, offset: 0.85 },
        { transform: 'translateY(-30%) scale(.9)', opacity: 0 },
      ], POP_MS, 'ease-out');
    } else if (now > this.popUntil) {
      this.popShow.set(false);
    }

    // Tutorial hint pill: pops in on a new hint; on clear it plays a short pop-out first.
    const hint = showHint && s.status === 'running' && !isOut ? s.hint : null;
    if (hint !== null && hint !== this.lastHint) {
      if (this.lastHint) this.hintEls.get(this.lastHint)?.show.set(false);
      this.hintEls.get(hint)?.show.set(true);
      this.lastHint = hint;
      this.hintLeaving.set(false);
      this.hintWrap.set(true);
      pop(this.hintEls.get(hint)?.show.el ?? this.hintWrap.el, [
        { transform: 'scale(.5)', opacity: 0 },
        { transform: 'scale(1.08)', opacity: 1, offset: 0.65 },
        { transform: 'scale(1)', opacity: 1 },
      ], 220, 'ease-out');
    } else if (hint !== null) {
      this.hintLeaving.set(false);
    } else if (this.lastHint !== null) {
      if (!this.hintLeaving.value) {
        this.hintLeaving.set(true);
        this.hintGoneAt = now + HINT_OUT_MS;
      } else if (now >= this.hintGoneAt) {
        this.hintEls.get(this.lastHint)?.show.set(false);
        this.hintLeaving.set(false);
        this.hintWrap.set(false);
        this.lastHint = null;
      }
    }
    if (hint) {
      if (hint !== this.keysHint || keyboard !== this.keysKeyboard || playerCount !== this.keysCount) {
        this.keysHint = hint;
        this.keysKeyboard = keyboard;
        this.keysCount = playerCount;
        const el = this.hintEls.get(hint)?.keys;
        if (el) {
          el.replaceChildren();
          if (keyboard) {
            const c = TUTORIAL_COPY[hint];
            const keys = playerCount === 1 ? c.keys1p : this.id === 0 ? c.keysP1 : c.keysP2;
            for (const k of keys) el.append(kbd(k));
          }
        }
      }
    }
  }
}

export type HudPhase = 'countdown' | 'playing' | 'paused';

export class HudView {
  readonly root: HTMLElement;
  private readonly players: PlayerHud[];
  private readonly playersAttr: Attr;
  private readonly phaseAttr: Attr;
  private readonly p2: Show;

  constructor(emit: Emit) {
    this.players = [new PlayerHud(0), new PlayerHud(1)];
    const pauseBtn = button('bs-hud__pause', icon(ICONS.pause, 'bs-hud__pause-icon'));
    pauseBtn.title = 'Pause (P)';
    pauseBtn.setAttribute('aria-label', 'Pause');
    pauseBtn.addEventListener('click', () => emit({ type: 'pause-toggle' }));
    this.root = h('div', 'bs-hud', this.players[0].root, this.players[1].root, h('div', 'bs-hud__divider'), pauseBtn);
    this.playersAttr = new Attr(this.root, 'data-players');
    this.phaseAttr = new Attr(this.root, 'data-phase');
    this.p2 = new Show(this.players[1].root);
  }

  update(m: UIModel, phase: HudPhase): void {
    const n = m.playerCount;
    this.playersAttr.set(n === 2 ? '2' : '1');
    this.phaseAttr.set(phase);
    this.p2.set(n === 2);
    const keyboard = m.inputMode === 'keyboard';
    const showHint = phase === 'playing';
    for (let i = 0; i < n; i++) {
      const id = i as PlayerId;
      this.players[i].update(m.hud[i], n, m.out.includes(id), m.winner === id, keyboard, showHint);
    }
  }
}
