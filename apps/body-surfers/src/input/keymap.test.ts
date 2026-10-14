import { describe, expect, it } from 'vitest';
import type { MenuKey, PlayerAction, PlayerCount, PlayerId } from '../shared/types';
import { isTypingTarget, keyLegend, mapKey } from './keymap';

type Expect = [code: string, playerId: PlayerId, action: PlayerAction];

const left: PlayerAction = { type: 'move', dir: -1 };
const right: PlayerAction = { type: 'move', dir: 1 };
const jump: PlayerAction = { type: 'jump' };
const duck: PlayerAction = { type: 'duck' };

function only(code: string, n: PlayerCount) {
  const m = mapKey(code, n);
  expect(m.actions).toHaveLength(1);
  return m.actions[0];
}

describe('mapKey — 1 player', () => {
  const cases: Expect[] = [
    ['ArrowLeft', 0, left],
    ['KeyA', 0, left],
    ['ArrowRight', 0, right],
    ['KeyD', 0, right],
    ['ArrowUp', 0, jump],
    ['KeyW', 0, jump],
    ['Space', 0, jump],
    ['ArrowDown', 0, duck],
    ['KeyS', 0, duck],
    ['ShiftLeft', 0, duck],
    ['ShiftRight', 0, duck],
  ];
  it.each(cases)('%s → player %i %o', (code, playerId, action) => {
    expect(only(code, 1)).toEqual({ playerId, action });
  });
});

describe('mapKey — 2 players (split keyboard)', () => {
  const cases: Expect[] = [
    ['KeyA', 0, left],
    ['KeyD', 0, right],
    ['KeyW', 0, jump],
    ['Space', 0, jump],
    ['KeyS', 0, duck],
    ['ShiftLeft', 0, duck],
    ['ArrowLeft', 1, left],
    ['ArrowRight', 1, right],
    ['ArrowUp', 1, jump],
    ['ArrowDown', 1, duck],
    ['ShiftRight', 1, duck],
  ];
  it.each(cases)('%s → player %i %o', (code, playerId, action) => {
    expect(only(code, 2)).toEqual({ playerId, action });
  });

  it('never routes a key to both players', () => {
    for (const { mapping } of keyLegend(2)) expect(mapping.actions.length).toBeLessThanOrEqual(1);
  });

  it('player 1 has no actions in 1P', () => {
    for (const { mapping } of keyLegend(1)) for (const a of mapping.actions) expect(a.playerId).toBe(0);
  });
});

describe('mapKey — Space is jump AND confirm', () => {
  it.each([1, 2] as const)('%iP', (n) => {
    const m = mapKey('Space', n);
    expect(m.actions).toEqual([{ playerId: 0, action: jump }]);
    expect(m.menu).toBe('confirm');
    expect(m.preventDefault).toBe(true);
  });
});

describe('mapKey — menu keys (both player counts)', () => {
  const cases: [string, MenuKey][] = [
    ['Enter', 'confirm'],
    ['NumpadEnter', 'confirm'],
    ['Digit1', 'one-player'],
    ['Numpad1', 'one-player'],
    ['Digit2', 'two-player'],
    ['Numpad2', 'two-player'],
    ['KeyP', 'pause'],
    ['Escape', 'pause'],
    ['Backspace', 'back'],
    ['KeyQ', 'back'],
    ['KeyM', 'mute'],
    ['KeyF', 'fullscreen'],
    ['Backquote', 'settings'],
    ['KeyI', 'debug'],
  ];
  for (const n of [1, 2] as const) {
    it.each(cases)(`${n}P: %s → %s (no actions)`, (code, menu) => {
      const m = mapKey(code, n);
      expect(m.menu).toBe(menu);
      expect(m.actions).toHaveLength(0);
      expect(m.preventDefault).toBe(false);
    });
  }

  it('movement keys carry no menu key', () => {
    for (const code of ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'KeyA', 'KeyD', 'KeyW', 'KeyS', 'ShiftLeft', 'ShiftRight']) {
      expect(mapKey(code, 1).menu).toBeNull();
      expect(mapKey(code, 2).menu).toBeNull();
    }
  });
});

describe('mapKey — misc', () => {
  it('unmapped keys do nothing', () => {
    for (const code of ['KeyZ', 'Tab', 'Digit3', 'ControlLeft', 'MetaLeft', '', 'arrowleft']) {
      for (const n of [1, 2] as const) {
        const m = mapKey(code, n);
        expect(m.actions).toHaveLength(0);
        expect(m.menu).toBeNull();
        expect(m.preventDefault).toBe(false);
      }
    }
  });

  it('preventDefault only for arrows and Space', () => {
    const expected = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Space']);
    for (const n of [1, 2] as const) {
      for (const { code, mapping } of keyLegend(n)) expect(mapping.preventDefault).toBe(expected.has(code));
    }
  });

  it('returns shared frozen objects (no allocation per key press)', () => {
    const a = mapKey('ArrowLeft', 1);
    expect(mapKey('ArrowLeft', 1)).toBe(a);
    expect(Object.isFrozen(a)).toBe(true);
    expect(Object.isFrozen(a.actions)).toBe(true);
  });

  it('treats an invalid player count as 1P', () => {
    expect(mapKey('ArrowLeft', 3 as unknown as PlayerCount)).toBe(mapKey('ArrowLeft', 1));
  });
});

describe('isTypingTarget', () => {
  it('detects form fields and contentEditable', () => {
    expect(isTypingTarget({ tagName: 'INPUT' } as unknown as EventTarget)).toBe(true);
    expect(isTypingTarget({ tagName: 'textarea' } as unknown as EventTarget)).toBe(true);
    expect(isTypingTarget({ tagName: 'SELECT' } as unknown as EventTarget)).toBe(true);
    expect(isTypingTarget({ tagName: 'DIV', isContentEditable: true } as unknown as EventTarget)).toBe(true);
  });
  it('ignores everything else', () => {
    expect(isTypingTarget(null)).toBe(false);
    expect(isTypingTarget(undefined)).toBe(false);
    expect(isTypingTarget({ tagName: 'BODY' } as unknown as EventTarget)).toBe(false);
    expect(isTypingTarget({ tagName: 'BUTTON', isContentEditable: false } as unknown as EventTarget)).toBe(false);
    expect(isTypingTarget({} as EventTarget)).toBe(false);
  });
});
