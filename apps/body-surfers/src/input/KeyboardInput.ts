// Keyboard fallback input (MASTERPLAN §9). Translates keydown events into ActionEvents
// (source 'keyboard') and MenuKeys using the pure mapping in ./keymap.
import type { ActionEvent, IKeyboardInput, MenuKey, PlayerCount } from '../shared/types';
import { Emitter } from '../shared/emitter';
import { isTypingTarget, mapKey } from './keymap';

/** The subset of KeyboardEvent the handler reads (lets tests feed plain objects). */
export interface KeyEventLike {
  readonly code: string;
  readonly repeat: boolean;
  readonly target: EventTarget | null;
  readonly metaKey?: boolean;
  readonly ctrlKey?: boolean;
  readonly altKey?: boolean;
  readonly isComposing?: boolean;
  preventDefault(): void;
}

export class KeyboardInput implements IKeyboardInput {
  private readonly actions = new Emitter<ActionEvent>();
  private readonly menu = new Emitter<MenuKey>();
  private playerCount: PlayerCount = 1;
  private target: Window | null = null;
  private readonly listener = (e: KeyboardEvent): void => this.handleKeyDown(e);

  attach(target: Window): void {
    if (this.target === target) return;
    this.detach();
    target.addEventListener('keydown', this.listener);
    this.target = target;
  }

  setPlayerCount(n: PlayerCount): void {
    this.playerCount = n === 2 ? 2 : 1;
  }

  getPlayerCount(): PlayerCount {
    return this.playerCount;
  }

  onAction(cb: (e: ActionEvent) => void): () => void {
    return this.actions.on(cb);
  }

  onMenuKey(cb: (k: MenuKey) => void): () => void {
    return this.menu.on(cb);
  }

  dispose(): void {
    this.detach();
    this.actions.clear();
    this.menu.clear();
  }

  /** Process one keydown. Public so tests and the sandbox can drive it without a DOM. */
  handleKeyDown(e: KeyEventLike): void {
    // Held keys auto-repeat: one press = one action.
    if (e.repeat || e.isComposing) return;
    // Browser / OS shortcuts (Cmd+W, Ctrl+R, Cmd+Q…) are never game keys.
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    // Typing a leaderboard name or using a settings field.
    if (isTypingTarget(e.target)) return;

    const m = mapKey(e.code, this.playerCount);
    if (m.actions.length === 0 && m.menu === null) return;
    if (m.preventDefault) e.preventDefault();

    const t = performance.now();
    for (const ka of m.actions) {
      this.actions.emit({ playerId: ka.playerId, action: { ...ka.action }, source: 'keyboard', t });
    }
    if (m.menu !== null) this.menu.emit(m.menu);
  }

  private detach(): void {
    if (this.target) this.target.removeEventListener('keydown', this.listener);
    this.target = null;
  }
}
