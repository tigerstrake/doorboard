// When the game should hand the wallboard back. Pure, so the rules are testable
// without a camera. Nothing on the wallboard can be clicked, so every way out of a
// run has to be one of these; the wallboard also ends the game itself on a bell
// press or privacy mode, without asking.
import type { Screen } from '../shared/types';
import type { DoorExitReason } from './bridge';
import { DOOR } from './config';

export interface DoorWatch {
  screen: Screen;
  /** Seconds on the current screen. */
  screenFor: number;
  /** Seconds since anyone (player or spectator) was last in view. */
  nobodyFor: number;
  /** Seconds since a boot or tracking error appeared, or null while there is none. */
  errorFor: number | null;
}

export function doorExitReason(w: DoorWatch): DoorExitReason | null {
  if (w.errorFor !== null) return w.errorFor >= DOOR.errorDwellS ? 'error' : null;
  if (w.screen === 'boot') return w.screenFor >= DOOR.bootMaxS ? 'error' : null;
  if (w.screen === 'lobby') return w.screenFor >= DOOR.lobbyMaxS || w.nobodyFor >= DOOR.nobodyS ? 'idle' : null;
  return w.nobodyFor >= DOOR.nobodyS ? 'walked_away' : null;
}
