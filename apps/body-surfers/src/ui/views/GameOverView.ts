// Game over: CRASHED! / RACE OVER!, polaroid crash selfies, rank badges, editable names,
// stats, leaderboard with the new entries highlighted, hands-up restart ring + buttons.
import type { PlayerId, PlayerResult, UIModel } from '../../shared/types';
import { PLAYER_COLORS, PLAYER_LABELS } from '../../shared/constants';
import { Attr, button, CssNum, CssStr, Flag, h, icon, ImgSrc, NumText, pop, Show, Text } from '../dom';
import { ICONS } from '../icons';
import { placeholderAvatar } from '../avatar';
import { fmtDistance, fmtGrouped, fmtTime } from '../copy';
import { LEADERBOARD_ROWS_GAMEOVER, NAME_MAX_LENGTH } from '../config';
import { LeaderboardPanel } from './LeaderboardPanel';
import { type Emit, type ScreenView, picto } from './common';

class ResultCard {
  readonly root: HTMLElement;
  readonly show: Show;
  private readonly img: ImgSrc;
  private readonly color: CssStr;
  private readonly tag: Text;
  private readonly rankShow: Show;
  private readonly rankText: Text;
  private readonly rankKind: Attr;
  private readonly winner: Flag;
  private readonly input: HTMLInputElement;
  private readonly nameShow: Show;
  private readonly staticName: Text;
  private readonly staticShow: Show;
  private readonly score: NumText;
  private readonly coins: NumText;
  private readonly dist: Text;
  private readonly time: Text;
  private entryId: string | null = null;
  /** Name as last confirmed (model value or our own rename). */
  private committed = '';
  private resultKey = '';

  constructor(private readonly emit: Emit) {
    const img = h('img', 'bs-polaroid__img');
    img.alt = 'Crash selfie';
    img.draggable = false;
    const tagEl = h('span', 'bs-polaroid__tag bs-display');
    const crown = icon(ICONS.crown, 'bs-result__crown');
    const polaroid = h('div', 'bs-polaroid', h('span', 'bs-polaroid__tape'), h('div', 'bs-polaroid__photo', img), tagEl);
    const rankEl = h('div', 'bs-rank bs-display');
    const input = h('input', 'bs-name__input');
    input.type = 'text';
    input.maxLength = NAME_MAX_LENGTH;
    input.spellcheck = false;
    input.autocomplete = 'off';
    input.setAttribute('aria-label', 'Your name');
    const pencil = h('span', 'bs-name__pen', '✎');
    const nameWrap = h('label', 'bs-name', input, pencil);
    const staticName = h('div', 'bs-name__static');
    const scoreEl = h('div', 'bs-result__score bs-display');
    const coinsEl = h('span', null);
    const distEl = h('span', null);
    const timeEl = h('span', null);
    const stats = h(
      'div',
      'bs-result__stats',
      h('div', 'bs-stat', icon(ICONS.coin, 'bs-stat__icon'), coinsEl),
      h('div', 'bs-stat', h('b', null, 'DIST'), distEl),
      h('div', 'bs-stat', h('b', null, 'TIME'), timeEl),
    );
    this.root = h(
      'div',
      'bs-result',
      h('div', 'bs-result__pic', polaroid, crown),
      // Rank badge sits in the info column so it can never cover the subtitle or the selfie.
      h('div', 'bs-result__info', rankEl, h('div', 'bs-result__label', 'SCORE'), scoreEl, stats, nameWrap, staticName),
    );
    this.show = new Show(this.root);
    this.img = new ImgSrc(img);
    this.color = new CssStr(this.root, '--pc');
    this.tag = new Text(tagEl);
    this.rankShow = new Show(rankEl);
    this.rankText = new Text(rankEl);
    this.rankKind = new Attr(rankEl, 'data-kind');
    this.winner = new Flag(this.root, 'is-winner');
    this.input = input;
    this.nameShow = new Show(nameWrap);
    this.staticName = new Text(staticName);
    this.staticShow = new Show(staticName);
    this.score = new NumText(scoreEl, fmtGrouped);
    this.coins = new NumText(coinsEl, fmtGrouped);
    this.dist = new Text(distEl);
    this.time = new Text(timeEl);

    input.addEventListener('keydown', (e) => {
      e.stopPropagation(); // typing must not drive the game / menus
      if (e.key === 'Enter') {
        e.preventDefault();
        input.blur();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        input.value = this.committed;
        input.blur();
      }
    });
    input.addEventListener('keyup', (e) => e.stopPropagation());
    input.addEventListener('keypress', (e) => e.stopPropagation());
    input.addEventListener('blur', () => this.commit());
    input.addEventListener('focus', () => input.select());
  }

  private commit(): void {
    const v = this.input.value.replace(/\s+/g, ' ').trim().slice(0, NAME_MAX_LENGTH);
    if (!v) {
      this.input.value = this.committed;
      return;
    }
    this.input.value = v;
    if (v !== this.committed && this.entryId) {
      this.committed = v;
      this.emit({ type: 'rename', entryId: this.entryId, name: v });
    }
  }

  blur(): void {
    if (document.activeElement === this.input) this.input.blur();
  }

  update(r: PlayerResult, twoP: boolean, isWinner: boolean): void {
    const id = r.playerId as PlayerId;
    this.color.set(PLAYER_COLORS[id]);
    this.tag.set(twoP ? PLAYER_LABELS[id] : 'OUCH!');
    this.img.set(r.photo ?? placeholderAvatar(r.entryId ?? `${r.name}-${r.playerId}`));
    this.winner.set(twoP && isWinner);
    this.score.set(Math.floor(r.score));
    this.coins.set(r.coins);
    this.dist.set(fmtDistance(r.distance));
    this.time.set(fmtTime(r.time));

    if (r.rank === 1) {
      this.rankText.set('NEW RECORD!');
      this.rankKind.set('record');
    } else if (r.rank !== null) {
      this.rankText.set(`#${r.rank}!`);
      this.rankKind.set('rank');
    }
    this.rankShow.set(r.rank !== null);

    const key = `${r.entryId ?? ''}|${r.playerId}`;
    const editable = r.entryId !== null;
    this.nameShow.set(editable);
    this.staticShow.set(!editable);
    if (key !== this.resultKey) {
      // New run: reset the input to the model name.
      this.resultKey = key;
      this.entryId = r.entryId;
      this.committed = r.name;
      this.input.value = r.name;
      pop(this.root, [{ transform: 'translateY(30px) scale(.9)', opacity: 0 }, { transform: 'none', opacity: 1 }], 520);
    } else if (document.activeElement !== this.input && r.name !== this.committed) {
      this.committed = r.name;
      this.input.value = r.name;
    }
    if (!editable) this.staticName.set(r.name);
  }
}

export class GameOverView implements ScreenView {
  readonly root: HTMLElement;
  private readonly cards: ResultCard[];
  private readonly title: Text;
  private readonly sub: Text;
  private readonly subShow: Show;
  private readonly boardShow: Show;
  private readonly playersAttr: Attr;
  private readonly board: LeaderboardPanel;
  private readonly ring: CssNum;
  private readonly locked: Flag;
  private readonly restartTitle: Text;
  private readonly restartSub: Text;
  private readonly highlight: (string | null)[] = [null, null];

  constructor(emit: Emit) {
    this.cards = [new ResultCard(emit), new ResultCard(emit)];
    const title = h('h1', 'bs-go__title bs-display');
    const sub = h('div', 'bs-go__sub');
    this.board = new LeaderboardPanel(LEADERBOARD_ROWS_GAMEOVER, 'TOP SURFERS', 'No scores yet', 'bs-go__board');

    const rTitle = h('div', 'bs-restart__title bs-display');
    const rSub = h('div', 'bs-restart__sub');
    const ringEl = h('div', 'bs-restart__ring', h('span', 'bs-restart__fill'), picto('handsUp', 'bs-restart__picto'));
    const again = button('bs-btn bs-btn--green', 'PLAY AGAIN');
    const menu = button('bs-btn bs-btn--blue bs-btn--sm', 'MENU');
    again.addEventListener('click', () => emit({ type: 'restart' }));
    menu.addEventListener('click', () => emit({ type: 'to-lobby' }));
    const restart = h(
      'div',
      'bs-restart bs-sticker',
      ringEl,
      h('div', 'bs-restart__text', rTitle, rSub),
      h('div', 'bs-restart__btns', again, menu),
    );

    this.root = h(
      'section',
      'bs-screen bs-go',
      h('div', 'bs-go__dim'),
      h('header', 'bs-go__head', title, sub),
      h('div', 'bs-go__results', this.cards[0].root, this.cards[1].root),
      this.board.root,
      restart,
      h('div', 'bs-go__camslot'),
    );
    this.title = new Text(title);
    this.sub = new Text(sub);
    this.subShow = new Show(sub);
    this.boardShow = new Show(this.board.root);
    this.playersAttr = new Attr(this.root, 'data-players');
    this.ring = new CssNum(restart, '--p', 0.01);
    this.locked = new Flag(restart, 'is-locked');
    this.restartTitle = new Text(rTitle);
    this.restartSub = new Text(rSub);
  }

  enter(): void {
    pop(this.root.querySelector('.bs-go__title') ?? this.root, [
      { transform: 'scale(2.4) rotate(-12deg)', opacity: 0 },
      { transform: 'scale(.9) rotate(-4deg)', opacity: 1, offset: 0.6 },
      { transform: 'rotate(-3deg)', opacity: 1 },
    ], 600);
  }

  leave(): void {
    for (const c of this.cards) c.blur();
  }

  update(m: UIModel): void {
    const go = m.gameOver;
    const results = go ? go.results : [];
    const twoP = results.length > 1;
    this.playersAttr.set(twoP ? '2' : '1');
    if (twoP) {
      const w = m.winner;
      this.title.set('RACE OVER!');
      this.sub.set(w !== null ? `${PLAYER_LABELS[w]} WINS!` : "IT'S A TIE!");
    } else {
      this.title.set('CRASHED!');
      const r = results[0];
      // #1: the NEW RECORD! badge says it all; no extra "you're the best" subtitle.
      this.sub.set(r && r.rank !== null && r.rank > 1 ? 'You made the board!' : r && r.rank === null ? 'So close! Try again?' : '');
    }
    this.subShow.set(this.sub.el.textContent !== '');
    // A board holding only this run's entry just repeats the result card.
    let others = 0;
    for (const e of m.leaderboard) if (!results.some((r) => r.entryId === e.id)) others++;
    this.boardShow.set(others > 0);
    for (let i = 0; i < 2; i++) {
      const r = results[i];
      this.cards[i].show.set(r !== undefined);
      if (r) this.cards[i].update(r, twoP, m.winner === r.playerId);
      this.highlight[i] = r ? r.entryId : null;
    }
    this.board.update(m.leaderboard, this.highlight);

    const keyboard = m.inputMode === 'keyboard';
    const lock = go ? go.restartLockout : null;
    const isLocked = lock !== null && lock > 0;
    this.locked.set(isLocked);
    this.ring.set(isLocked ? 0 : Math.max(0, Math.min(1, go ? go.handsUpProgress : 0)));
    if (isLocked) {
      this.restartTitle.set('CATCH YOUR BREATH…');
      this.restartSub.set(`Play again in ${Math.ceil(lock)}`);
    } else if (keyboard) {
      this.restartTitle.set('PLAY AGAIN?');
      this.restartSub.set('Press ENTER');
    } else {
      this.restartTitle.set('HANDS UP TO GO AGAIN!');
      this.restartSub.set('or press ENTER');
    }
  }
}

