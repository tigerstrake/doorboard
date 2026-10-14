import { describe, expect, it } from 'vitest';
import { CONFIG } from '../game/config';
import { DEFAULT_SETTINGS } from '../shared/constants';
import { MemoryStorage, type StorageLike } from './storage';
import {
  TUTORIAL_DONE_KEY, TUTORIAL_END_M, applyTutorialDone, isTutorialDone, setTutorialDone, tutorialCleared,
  tutorialReenabled,
} from './tutorial';

const broken: StorageLike = {
  getItem: () => {
    throw new Error('denied');
  },
  setItem: () => {
    throw new Error('quota');
  },
  removeItem: () => {
    throw new Error('denied');
  },
};

describe('tutorial done flag', () => {
  it('uses its own versioned key and matches the game tutorial end', () => {
    expect(TUTORIAL_DONE_KEY).toBe('bodysurfers.tutorialDone.v1');
    expect(TUTORIAL_END_M).toBe(CONFIG.tutorialEnd);
  });

  it('is unset on a fresh profile, then persists once set, and can be cleared', () => {
    const st = new MemoryStorage();
    expect(isTutorialDone(st)).toBe(false);
    expect(setTutorialDone(st, true)).toBe(true);
    expect(st.getItem(TUTORIAL_DONE_KEY)).toBe('1');
    expect(isTutorialDone(st)).toBe(true);
    setTutorialDone(st, false);
    expect(st.getItem(TUTORIAL_DONE_KEY)).toBeNull();
    expect(isTutorialDone(st)).toBe(false);
  });

  it('treats garbage as not done and survives throwing storage', () => {
    const st = new MemoryStorage();
    st.setItem(TUTORIAL_DONE_KEY, 'yes');
    expect(isTutorialDone(st)).toBe(false);
    expect(isTutorialDone(broken)).toBe(false);
    expect(setTutorialDone(broken, true)).toBe(false);
  });

  it('switches the tutorial setting off only when done', () => {
    const on = { ...DEFAULT_SETTINGS, tutorial: true };
    expect(applyTutorialDone(on, false)).toBe(on);
    expect(applyTutorialDone(on, true)).toEqual({ ...on, tutorial: false });
    const off = { ...DEFAULT_SETTINGS, tutorial: false };
    expect(applyTutorialDone(off, true)).toBe(off);
  });

  it('is cleared when any runner of a tutorial run passes the tutorial end', () => {
    expect(tutorialCleared(true, [0])).toBe(false);
    expect(tutorialCleared(true, [TUTORIAL_END_M - 0.1])).toBe(false);
    expect(tutorialCleared(true, [TUTORIAL_END_M])).toBe(true);
    expect(tutorialCleared(true, [10, TUTORIAL_END_M + 5])).toBe(true); // 2P: either player
    expect(tutorialCleared(false, [TUTORIAL_END_M * 3])).toBe(false); // run without tutorial
    expect(tutorialCleared(true, [])).toBe(false);
    expect(tutorialCleared(true, [50], 40)).toBe(true);
  });

  it('detects the settings toggle turning the tutorial back on', () => {
    const off = { ...DEFAULT_SETTINGS, tutorial: false };
    const on = { ...DEFAULT_SETTINGS, tutorial: true };
    expect(tutorialReenabled(off, on)).toBe(true);
    expect(tutorialReenabled(on, off)).toBe(false);
    expect(tutorialReenabled(on, on)).toBe(false);
  });
});
