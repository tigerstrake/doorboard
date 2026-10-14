import { describe, expect, it, vi } from 'vitest';
import type { ActionEvent, MenuKey } from '../shared/types';
import { KeyboardInput } from './KeyboardInput';

type Listener = (e: unknown) => void;

/** Minimal stand-in for `window` (node has no DOM). */
class FakeWindow {
  listeners = new Map<string, Set<Listener>>();
  addEventListener(type: string, cb: Listener): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(cb);
  }
  removeEventListener(type: string, cb: Listener): void {
    this.listeners.get(type)?.delete(cb);
  }
  count(type: string): number {
    return this.listeners.get(type)?.size ?? 0;
  }
  key(code: string, init: Partial<FakeKeyEvent> = {}): FakeKeyEvent {
    const e = new FakeKeyEvent(code, init);
    for (const cb of this.listeners.get('keydown') ?? []) cb(e);
    return e;
  }
  asWindow(): Window {
    return this as unknown as Window;
  }
}

class FakeKeyEvent {
  repeat = false;
  metaKey = false;
  ctrlKey = false;
  altKey = false;
  shiftKey = false;
  isComposing = false;
  target: unknown = { tagName: 'BODY' };
  defaultPrevented = false;
  constructor(
    readonly code: string,
    init: Partial<FakeKeyEvent>,
  ) {
    Object.assign(this, init);
  }
  preventDefault(): void {
    this.defaultPrevented = true;
  }
}

function setup() {
  const win = new FakeWindow();
  const kb = new KeyboardInput();
  const actions: ActionEvent[] = [];
  const menus: MenuKey[] = [];
  kb.onAction((e) => actions.push(e));
  kb.onMenuKey((k) => menus.push(k));
  kb.attach(win.asWindow());
  return { win, kb, actions, menus };
}

describe('KeyboardInput', () => {
  it('registers exactly one keydown listener and removes it on dispose', () => {
    const { win, kb } = setup();
    expect(win.count('keydown')).toBe(1);
    kb.attach(win.asWindow()); // idempotent
    expect(win.count('keydown')).toBe(1);
    kb.dispose();
    expect(win.count('keydown')).toBe(0);
  });

  it('emits keyboard ActionEvents stamped with performance.now()', () => {
    const { win, actions } = setup();
    const before = performance.now();
    win.key('ArrowLeft');
    const after = performance.now();
    expect(actions).toHaveLength(1);
    const e = actions[0];
    expect(e).toMatchObject({ playerId: 0, action: { type: 'move', dir: -1 }, source: 'keyboard' });
    expect(e.t).toBeGreaterThanOrEqual(before);
    expect(e.t).toBeLessThanOrEqual(after);
  });

  it('Space emits jump and confirm', () => {
    const { win, actions, menus } = setup();
    win.key('Space');
    expect(actions.map((a) => a.action)).toEqual([{ type: 'jump' }]);
    expect(menus).toEqual(['confirm']);
  });

  it('emits menu keys without actions', () => {
    const { win, actions, menus } = setup();
    for (const code of ['Enter', 'Digit1', 'Digit2', 'KeyP', 'Escape', 'Backspace', 'KeyQ', 'KeyM', 'KeyF', 'Backquote', 'KeyI']) win.key(code);
    expect(actions).toHaveLength(0);
    expect(menus).toEqual(['confirm', 'one-player', 'two-player', 'pause', 'pause', 'back', 'back', 'mute', 'fullscreen', 'settings', 'debug']);
  });

  it('ignores auto-repeat', () => {
    const { win, actions, menus } = setup();
    win.key('ArrowUp', { repeat: true });
    win.key('Space', { repeat: true });
    expect(actions).toHaveLength(0);
    expect(menus).toHaveLength(0);
  });

  it('ignores typing in form fields and contentEditable (no preventDefault either)', () => {
    const { win, actions, menus } = setup();
    for (const target of [{ tagName: 'INPUT' }, { tagName: 'TEXTAREA' }, { tagName: 'SELECT' }, { tagName: 'DIV', isContentEditable: true }]) {
      const e = win.key('Space', { target });
      expect(e.defaultPrevented).toBe(false);
      win.key('Enter', { target });
      win.key('Backspace', { target });
    }
    expect(actions).toHaveLength(0);
    expect(menus).toHaveLength(0);
  });

  it('ignores browser shortcuts (meta / ctrl / alt)', () => {
    const { win, actions, menus } = setup();
    win.key('KeyW', { metaKey: true });
    win.key('KeyQ', { metaKey: true });
    win.key('KeyF', { ctrlKey: true });
    win.key('KeyI', { altKey: true });
    win.key('ArrowLeft', { altKey: true });
    expect(actions).toHaveLength(0);
    expect(menus).toHaveLength(0);
  });

  it('still accepts Shift-modified keys (Shift is a game key)', () => {
    const { win, actions } = setup();
    win.key('KeyA', { shiftKey: true });
    win.key('ShiftLeft', { shiftKey: true });
    expect(actions.map((a) => a.action.type)).toEqual(['move', 'duck']);
  });

  it('prevents the default only for arrows and Space', () => {
    const { win } = setup();
    for (const code of ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Space']) expect(win.key(code).defaultPrevented).toBe(true);
    for (const code of ['KeyA', 'KeyW', 'Enter', 'Escape', 'KeyZ', 'ShiftLeft']) expect(win.key(code).defaultPrevented).toBe(false);
  });

  it('splits the keyboard in 2P and switches back', () => {
    const { win, kb, actions } = setup();
    kb.setPlayerCount(2);
    win.key('KeyA');
    win.key('ArrowLeft');
    win.key('ShiftLeft');
    win.key('ShiftRight');
    win.key('Space');
    win.key('ArrowUp');
    expect(actions.map((a) => [a.playerId, a.action.type])).toEqual([
      [0, 'move'],
      [1, 'move'],
      [0, 'duck'],
      [1, 'duck'],
      [0, 'jump'],
      [1, 'jump'],
    ]);
    actions.length = 0;
    kb.setPlayerCount(1);
    win.key('ArrowLeft');
    win.key('ShiftRight');
    expect(actions.map((a) => a.playerId)).toEqual([0, 0]);
  });

  it('ignores unmapped keys', () => {
    const { win, actions, menus } = setup();
    const e = win.key('KeyZ');
    expect(actions).toHaveLength(0);
    expect(menus).toHaveLength(0);
    expect(e.defaultPrevented).toBe(false);
  });

  it('emits a fresh action object per event', () => {
    const { win, actions } = setup();
    win.key('ArrowLeft');
    win.key('ArrowLeft');
    expect(actions[0].action).toEqual(actions[1].action);
    expect(actions[0].action).not.toBe(actions[1].action);
  });

  it('unsubscribe stops delivery; dispose stops everything', () => {
    const { win, kb, actions } = setup();
    const spy = vi.fn();
    const off = kb.onAction(spy);
    win.key('ArrowUp');
    off();
    win.key('ArrowUp');
    expect(spy).toHaveBeenCalledTimes(1);
    expect(actions).toHaveLength(2);
    kb.dispose();
    win.key('ArrowUp');
    expect(actions).toHaveLength(2);
  });

  it('re-attaching moves the listener to the new target', () => {
    const { win, kb, actions } = setup();
    const other = new FakeWindow();
    kb.attach(other.asWindow());
    expect(win.count('keydown')).toBe(0);
    expect(other.count('keydown')).toBe(1);
    win.key('ArrowUp');
    other.key('ArrowUp');
    expect(actions).toHaveLength(1);
  });

  it('handleKeyDown works without attaching (sandbox / tests)', () => {
    const kb = new KeyboardInput();
    const got: ActionEvent[] = [];
    kb.onAction((e) => got.push(e));
    kb.handleKeyDown(new FakeKeyEvent('KeyD', {}) as unknown as KeyboardEvent);
    expect(got[0]).toMatchObject({ playerId: 0, action: { type: 'move', dir: 1 }, source: 'keyboard' });
  });
});
