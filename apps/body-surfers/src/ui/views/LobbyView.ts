// Lobby, built for first-timers: one obvious next step at a time.
//   centre: the camera preview with a "stand here" silhouette guide and live feedback pill
//           ("Step into the box" → "Step back a bit" → "Perfect!" → "You're in!")
//   below:  ONE instruction with the hands-up progress ring(s), then the big green ▶ PLAY
//           (2P race is a smaller secondary button)
//   tucked away: a small top-3 leaderboard and a small, dim keyboard legend.
import type { PositioningHint, UIModel } from '../../shared/types';
import { PLAYER_COLORS, PLAYER_LABELS } from '../../shared/constants';
import { Attr, button, CssNum, CssStr, Flag, h, icon, NumText, pop, Show, Text } from '../dom';
import { ICONS } from '../icons';
import { HINT_COPY } from '../copy';
import { LEADERBOARD_ROWS_LOBBY } from '../config';
import { LeaderboardPanel } from './LeaderboardPanel';
import { ControlsLine, type Emit, type ScreenView, kbd, makeLogo, picto } from './common';

type Person = UIModel['lobby']['people'][number];
type GuideState = 'off' | 'empty' | 'adjust' | 'good' | 'joined';

/** Dashed "stand here" outline drawn over the camera preview (viewBox 0 0 100 100, centred). */
const GUIDE_SVG =
  '<svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet" aria-hidden="true">' +
  '<path class="bs-guide__frame" d="M28 10 h-8 v10 M72 10 h8 v10 M28 96 h-8 v-10 M72 96 h8 v-10"/>' +
  '<g class="bs-guide__body">' +
  '<circle cx="50" cy="22" r="7.5"/>' +
  '<path d="M41 33 Q50 29 59 33 L64 61 L60 62 L57 44 L57 66 L56 93 L51.5 93 L50 69 L48.5 93 L44 93 L43 66 L43 44 L40 62 L36 61 Z"/>' +
  '</g></svg>';

/** Hands-up progress ring for one person (P1 / P2 tag, check mark when joined). */
class Ring {
  readonly root: HTMLElement;
  readonly show: Show;
  private readonly p: CssNum;
  private readonly color: CssStr;
  private readonly tag: Text;
  private readonly joined: Flag;
  private readonly charging: Flag;
  private wasJoined = false;

  constructor() {
    const tagEl = h('span', 'bs-ring__tag');
    this.root = h('div', 'bs-ring', tagEl, icon(ICONS.check, 'bs-ring__check'));
    this.show = new Show(this.root);
    this.p = new CssNum(this.root, '--p', 0.01);
    this.color = new CssStr(this.root, '--pc');
    this.tag = new Text(tagEl);
    this.joined = new Flag(this.root, 'is-joined');
    this.charging = new Flag(this.root, 'is-charging');
  }

  update(p: Person): void {
    this.color.set(PLAYER_COLORS[p.slot]);
    this.tag.set(PLAYER_LABELS[p.slot]);
    const progress = p.joined ? 1 : clamp01(p.handsUpProgress);
    this.p.set(progress);
    this.joined.set(p.joined);
    this.charging.set(!p.joined && progress > 0.04);
    if (p.joined && !this.wasJoined) pop(this.root, [{ transform: 'scale(1.3)' }, { transform: 'scale(1)' }], 420);
    this.wasJoined = p.joined;
  }
}

const clamp01 = (n: number): number => Math.max(0, Math.min(1, n));

/** Hints are in display space; without mirroring, "scoot left" means the other way for the player. */
function mirrorHint(hint: PositioningHint, mirror: boolean): PositioningHint {
  if (mirror) return hint;
  if (hint === 'move-left') return 'move-right';
  if (hint === 'move-right') return 'move-left';
  return hint;
}

export class LobbyView implements ScreenView {
  readonly root: HTMLElement;
  private readonly kbMode: Flag;
  private readonly multi: Flag;
  private readonly guideState: Attr;
  private readonly guideText: Text;
  private readonly title: Text;
  private readonly sub: Text;
  private readonly rings: Ring[] = [];
  private readonly ghost: Show;
  private readonly join: Show;
  private readonly joinNum: NumText;
  private readonly board: LeaderboardPanel;
  private readonly doorNotice: Text;
  private readonly doorShow: Show;
  private readonly controls = new ControlsLine('bs-lobby__controls');
  private lastJoinNum = -1;
  private lastGuide: GuideState | null = null;

  constructor(emit: Emit) {
    // --- Camera preview + position guide -------------------------------------------------
    const guideSvg = h('div', 'bs-guide__svg');
    guideSvg.innerHTML = GUIDE_SVG;
    const guideText = h('span', 'bs-guide__text');
    const guideIcon = h('span', 'bs-guide__dot');
    const guide = h('div', 'bs-guide', guideSvg, h('div', 'bs-guide__pill', guideIcon, guideText));

    const joinNum = h('span', 'bs-join__num bs-display');
    const joinEl = h(
      'div',
      'bs-join',
      h('div', 'bs-join__text', h('span', 'bs-join__who', 'PLAYER 2'), h('span', 'bs-join__title bs-display', 'RAISE YOUR HANDS TO RACE!')),
      joinNum,
    );

    // Keyboard mode (no camera): a simple controls card where the preview would be.
    const kbCard = h(
      'div',
      'bs-lobby__kb bs-sticker',
      picto('switch', 'bs-lobby__kbpicto'),
      h(
        'div',
        'bs-lobby__kbkeys',
        h('div', null, kbd('←'), kbd('→'), h('span', null, 'change lanes')),
        h('div', null, kbd('↑'), h('span', null, 'jump')),
        h('div', null, kbd('↓'), h('span', null, 'squat')),
      ),
    );

    const camWrap = h('div', 'bs-lobby__camwrap', h('div', 'bs-lobby__camslot'), guide, joinEl, kbCard);

    // --- The one instruction ---------------------------------------------------------------
    const ringsEl = h('div', 'bs-lobby__rings');
    for (let i = 0; i < 2; i++) {
      const r = new Ring();
      this.rings.push(r);
      ringsEl.append(r.root);
    }
    const ghostEl = h('div', 'bs-ring bs-ring--ghost', icon(ICONS.camera, 'bs-ring__cam'));
    ringsEl.append(ghostEl);
    const titleEl = h('div', 'bs-lobby__title bs-display');
    const subEl = h('div', 'bs-lobby__sub');
    const doRow = h('div', 'bs-lobby__do', ringsEl, h('div', 'bs-lobby__dotext', titleEl, subEl));

    // --- Actions: one dominant PLAY, smaller 2P race --------------------------------------
    const playSolo = button('bs-play bs-play--solo', '▶ PLAY');
    const playRace = button('bs-play bs-play--race', '2P RACE');
    playSolo.addEventListener('click', () => emit({ type: 'start', players: 1 }));
    playRace.addEventListener('click', () => emit({ type: 'start', players: 2 }));
    const actions = h('div', 'bs-play-row', playSolo, playRace);

    // --- Tucked away: keyboard legend (small, dim) + top-3 board ---------------------------
    const solo = button('bs-legend__btn', kbd('1'), h('span', null, 'Solo'));
    const race = button('bs-legend__btn', kbd('2'), h('span', null, 'Race'));
    const settings = button('bs-legend__btn', kbd('`'), h('span', null, 'Settings'));
    const full = button('bs-legend__btn', kbd('F'), h('span', null, 'Fullscreen'));
    solo.addEventListener('click', () => emit({ type: 'start', players: 1 }));
    race.addEventListener('click', () => emit({ type: 'start', players: 2 }));
    settings.addEventListener('click', () => emit({ type: 'settings-open', open: true }));
    full.addEventListener('click', () => emit({ type: 'fullscreen' }));
    const legend = h('div', 'bs-legend', solo, race, settings, full);
    // Door mode: the wallboard can't be clicked, so the legend gives way to the photo notice.
    const doorNoticeEl = h('p', 'bs-door-notice is-hidden');

    this.board = new LeaderboardPanel(LEADERBOARD_ROWS_LOBBY, 'TOP SURFERS', 'No scores yet. Be the first!', 'bs-lobby__board');

    this.root = h(
      'section',
      'bs-screen bs-lobby',
      makeLogo('bs-logo--lobby'),
      h('div', 'bs-lobby__main', camWrap, doRow, this.controls.root, actions),
      this.board.root,
      legend,
      doorNoticeEl,
    );

    this.kbMode = new Flag(this.root, 'is-keyboard');
    this.multi = new Flag(this.root, 'is-multi');
    this.guideState = new Attr(this.root, 'data-guide');
    this.guideText = new Text(guideText);
    this.title = new Text(titleEl);
    this.sub = new Text(subEl);
    this.ghost = new Show(ghostEl);
    this.join = new Show(joinEl);
    this.joinNum = new NumText(joinNum);
    this.doorNotice = new Text(doorNoticeEl);
    this.doorShow = new Show(doorNoticeEl);
  }

  update(m: UIModel): void {
    const keyboard = m.inputMode === 'keyboard';
    this.kbMode.set(keyboard);
    this.doorShow.set(m.door !== null);
    if (m.door) this.doorNotice.set(m.door.notice);
    this.controls.set(keyboard);
    const people = m.lobby.people;
    const mirror = m.settings.vision.mirror;
    this.multi.set(people.length > 1);

    let anyJoined = false;
    let anyCharging = false;
    let hinted: Person | null = null;
    for (let i = 0; i < people.length; i++) {
      const p = people[i];
      if (p.joined) anyJoined = true;
      else if (p.handsUpProgress > 0.04) anyCharging = true;
      if (!hinted && !p.joined && p.hints.length > 0) hinted = p;
    }

    // Rings: one per detected person; a ghost camera ring while nobody is in view.
    for (let i = 0; i < this.rings.length; i++) {
      const p = i < people.length ? people[i] : undefined;
      const visible = !keyboard && p !== undefined;
      this.rings[i].show.set(visible);
      if (visible && p) this.rings[i].update(p);
    }
    this.ghost.set(!keyboard && people.length === 0);

    // Position guide over the camera.
    let state: GuideState;
    let guide = '';
    if (keyboard) state = 'off';
    else if (people.length === 0) {
      state = 'empty';
      guide = 'Step into the box';
    } else if (anyJoined && !hinted) {
      state = 'joined';
      guide = "You're in!";
    } else if (hinted) {
      state = 'adjust';
      const copy = HINT_COPY[mirrorHint(hinted.hints[0], mirror)].title;
      guide = people.length > 1 ? `${PLAYER_LABELS[hinted.slot]}: ${copy}` : copy;
    } else {
      state = 'good';
      guide = 'Perfect!';
    }
    this.guideState.set(state);
    this.guideText.set(guide);
    if (state !== this.lastGuide && (state === 'good' || state === 'joined')) {
      pop(this.guideText.el.parentElement ?? this.guideText.el, [{ transform: 'translateX(-50%) scale(1.25)' }, { transform: 'translateX(-50%) scale(1)' }], 360);
    }
    this.lastGuide = state;

    // The one instruction.
    if (keyboard) {
      this.title.set('PRESS PLAY');
      this.sub.set('or press 1 on your keyboard');
    } else if (people.length === 0) {
      // The camera pill already says "Step into the box"; don't repeat it here.
      this.title.set('GET IN THE FRAME');
      this.sub.set('Stand about 2 m from the screen');
    } else if (anyJoined) {
      this.title.set("YOU'RE IN!");
      this.sub.set(m.lobby.joinCountdown !== null ? 'Waiting for player 2…' : 'Get ready…');
    } else if (anyCharging) {
      this.title.set('KEEP THEM UP!');
      this.sub.set('Hold until the ring is full');
    } else {
      this.title.set('RAISE BOTH HANDS TO PLAY');
      this.sub.set('…or just click PLAY');
    }

    // P2 join countdown banner (over the bottom of the preview).
    const jc = m.lobby.joinCountdown;
    const showJoin = !keyboard && jc !== null && anyJoined;
    this.join.set(showJoin);
    if (showJoin && jc !== null) {
      const n = Math.max(0, Math.ceil(jc));
      if (this.joinNum.set(n) && n !== this.lastJoinNum) {
        pop(this.joinNum.el, [{ transform: 'scale(1.7) rotate(-8deg)' }, { transform: 'scale(1) rotate(0deg)' }], 380);
      }
      this.lastJoinNum = n;
    } else {
      this.lastJoinNum = -1;
    }

    this.board.update(m.leaderboard);
  }
}
