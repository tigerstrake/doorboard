// Pure keyboard mapping (MASTERPLAN §9). KeyboardEvent.code → player actions + menu key.
// No DOM access here, so it is unit-testable in node.
import type { MenuKey, PlayerAction, PlayerCount, PlayerId } from '../shared/types';

export interface KeyAction {
  readonly playerId: PlayerId;
  readonly action: PlayerAction;
}

export interface KeyMapping {
  /** Player actions triggered by the key (empty when it is not a game key). */
  readonly actions: readonly KeyAction[];
  /** Menu key emitted alongside the actions, or null. */
  readonly menu: MenuKey | null;
  /** Suppress the browser default (page scrolling for arrows / Space). */
  readonly preventDefault: boolean;
}

const LEFT: PlayerAction = { type: 'move', dir: -1 };
const RIGHT: PlayerAction = { type: 'move', dir: 1 };
const JUMP: PlayerAction = { type: 'jump' };
const DUCK: PlayerAction = { type: 'duck' };

/** 1P: arrows + WASD + Space/Shift all drive player 0. */
const ACTIONS_1P: Readonly<Record<string, PlayerAction>> = {
  ArrowLeft: LEFT,
  KeyA: LEFT,
  ArrowRight: RIGHT,
  KeyD: RIGHT,
  ArrowUp: JUMP,
  KeyW: JUMP,
  Space: JUMP,
  ArrowDown: DUCK,
  KeyS: DUCK,
  ShiftLeft: DUCK,
  ShiftRight: DUCK,
};

/** 2P: WASD + Space + ShiftLeft → player 0; arrows + ShiftRight → player 1. */
const ACTIONS_2P: Readonly<Record<string, KeyAction>> = {
  KeyA: { playerId: 0, action: LEFT },
  KeyD: { playerId: 0, action: RIGHT },
  KeyW: { playerId: 0, action: JUMP },
  Space: { playerId: 0, action: JUMP },
  KeyS: { playerId: 0, action: DUCK },
  ShiftLeft: { playerId: 0, action: DUCK },
  ArrowLeft: { playerId: 1, action: LEFT },
  ArrowRight: { playerId: 1, action: RIGHT },
  ArrowUp: { playerId: 1, action: JUMP },
  ArrowDown: { playerId: 1, action: DUCK },
  ShiftRight: { playerId: 1, action: DUCK },
};

/** Menu keys are emitted in every mode, alongside any action. */
const MENU: Readonly<Record<string, MenuKey>> = {
  Enter: 'confirm',
  NumpadEnter: 'confirm',
  Space: 'confirm',
  Digit1: 'one-player',
  Numpad1: 'one-player',
  Digit2: 'two-player',
  Numpad2: 'two-player',
  KeyP: 'pause',
  Escape: 'pause',
  Backspace: 'back',
  KeyQ: 'back',
  KeyM: 'mute',
  KeyF: 'fullscreen',
  Backquote: 'settings',
  KeyI: 'debug',
};

const PREVENT_DEFAULT: ReadonlySet<string> = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Space']);

const NONE: KeyMapping = Object.freeze({ actions: Object.freeze([]), menu: null, preventDefault: false });

function build(count: PlayerCount): ReadonlyMap<string, KeyMapping> {
  const codes = new Set<string>([...Object.keys(ACTIONS_1P), ...Object.keys(ACTIONS_2P), ...Object.keys(MENU)]);
  const table = new Map<string, KeyMapping>();
  for (const code of codes) {
    const actions: KeyAction[] = [];
    if (count === 1) {
      const a = ACTIONS_1P[code];
      if (a) actions.push({ playerId: 0, action: a });
    } else {
      const ka = ACTIONS_2P[code];
      if (ka) actions.push(ka);
    }
    const menu = MENU[code] ?? null;
    if (actions.length === 0 && menu === null) continue;
    table.set(code, Object.freeze({ actions: Object.freeze(actions), menu, preventDefault: PREVENT_DEFAULT.has(code) }));
  }
  return table;
}

// Precomputed so a key press allocates nothing.
const TABLES: Readonly<Record<PlayerCount, ReadonlyMap<string, KeyMapping>>> = { 1: build(1), 2: build(2) };

/**
 * Map a `KeyboardEvent.code` to the actions / menu key it triggers for the given player count.
 * Returns a shared frozen object — do not mutate.
 */
export function mapKey(code: string, playerCount: PlayerCount): KeyMapping {
  return TABLES[playerCount === 2 ? 2 : 1].get(code) ?? NONE;
}

/** Human-readable legend of the layout (sandbox / UI help). */
export function keyLegend(playerCount: PlayerCount): { code: string; mapping: KeyMapping }[] {
  return [...TABLES[playerCount === 2 ? 2 : 1].entries()].map(([code, mapping]) => ({ code, mapping }));
}

/** Minimal shape of a key event target we need to inspect (works without a DOM). */
interface TargetLike {
  tagName?: unknown;
  isContentEditable?: unknown;
}

/** True when the event comes from a text field / select / contentEditable (the user is typing). */
export function isTypingTarget(target: EventTarget | null | undefined): boolean {
  if (!target || typeof target !== 'object') return false;
  const el = target as TargetLike;
  if (el.isContentEditable === true) return true;
  const tag = typeof el.tagName === 'string' ? el.tagName.toUpperCase() : '';
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
}
