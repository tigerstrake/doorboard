import { describe, expect, it } from 'vitest';
import { AudioManager } from './AudioManager';
import { coinFrequency, coinSemitones, unit } from './dsp';
import { COIN } from './config';
import type { GameEvent, UISound } from '../shared/types';

describe('coin pitch', () => {
  it('climbs strictly with combo up to the climb limit', () => {
    for (let c = 1; c <= COIN.climbSteps; c++) expect(coinFrequency(c + 1)).toBeGreaterThan(coinFrequency(c));
  });
  it('starts at the base note and stays bounded for huge combos', () => {
    expect(coinSemitones(1)).toBe(0);
    const top = coinSemitones(COIN.climbSteps + 1);
    for (let c = 1; c < 500; c++) expect(coinSemitones(c)).toBeLessThanOrEqual(top);
    expect(coinSemitones(NaN)).toBe(0);
  });
  it('unit sanitizes input', () => {
    expect(unit(NaN, 0.5)).toBe(0.5);
    expect(unit(2, 0)).toBe(1);
    expect(unit(-1, 0)).toBe(0);
  });
});

describe('AudioManager before unlock', () => {
  it('is a silent no-op and never throws', () => {
    const a = new AudioManager();
    expect(a.unlocked).toBe(false);
    const events: GameEvent[] = [
      { type: 'jump', super: true },
      { type: 'coin', x: 0, y: 0, combo: 3, total: 3 },
      { type: 'crash', cause: 'train', moving: true },
      { type: 'powerUp', kind: 'jetpack' },
      { type: 'powerUp', kind: 'slowmo' },
      { type: 'powerUp', kind: 'star' },
      { type: 'powerUp', kind: 'coinRain' },
      { type: 'smash', kind: 'barrier_low', lane: 0 },
      { type: 'powerUpWarning', kind: 'star' },
      { type: 'powerUpEnd', kind: 'slowmo' },
      { type: 'powerUpEnd', kind: 'jetpack' },
    ];
    const ui: UISound[] = ['click', 'go', 'newRecord'];
    expect(() => {
      a.setMuted(true);
      a.setVolumes(0.5, NaN);
      for (const e of events) a.playGameEvent(e, 1, 2);
      for (const s of ui) a.playUi(s);
      a.setMusic('game');
      a.setIntensity(0.7);
      a.setChargeLevel(0.5);
      a.dispose();
    }).not.toThrow();
  });
  it('unlock resolves without Web Audio', async () => {
    const a = new AudioManager();
    await a.unlock();
    expect(a.unlocked).toBe(false);
  });
});
