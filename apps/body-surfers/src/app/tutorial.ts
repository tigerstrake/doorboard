// "Played the tutorial once" memory: after a run clears the tutorial section, later runs
// skip it. One browser profile = one flag (2P included). The settings toggle can turn the
// tutorial back on for a new friend, which clears the flag until they clear it again.
import { CONFIG } from '../game/config';
import type { Settings } from '../shared/types';
import type { StorageLike } from './storage';

export const TUTORIAL_DONE_KEY = 'bodysurfers.tutorialDone.v1';
/** Track distance (m) where the tutorial section ends (World / generator use the same value). */
export const TUTORIAL_END_M: number = CONFIG.tutorialEnd;

export function isTutorialDone(storage: StorageLike, key: string = TUTORIAL_DONE_KEY): boolean {
  try {
    return storage.getItem(key) === '1';
  } catch {
    return false;
  }
}

/** false when the write failed (nothing persists, but the session still skips it). */
export function setTutorialDone(storage: StorageLike, done: boolean, key: string = TUTORIAL_DONE_KEY): boolean {
  try {
    if (done) storage.setItem(key, '1');
    else storage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

/** Saved settings adjusted for the flag: a finished tutorial starts switched off. */
export function applyTutorialDone(s: Settings, done: boolean): Settings {
  return done && s.tutorial ? { ...s, tutorial: false } : s;
}

/** True once any runner in a tutorial run has travelled past the end of the tutorial section. */
export function tutorialCleared(
  tutorialRun: boolean,
  distances: readonly number[],
  end: number = TUTORIAL_END_M,
): boolean {
  if (!tutorialRun) return false;
  for (const d of distances) if (d >= end) return true;
  return false;
}

/**
 * A settings patch turning the tutorial back on (false → true) means "show it again":
 * the done flag must be cleared, or the next page load would switch it off again.
 */
export function tutorialReenabled(prev: Settings, next: Settings): boolean {
  return !prev.tutorial && next.tutorial;
}
