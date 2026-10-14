import { describe, expect, it } from 'vitest';
import { announcement } from './announcer';
import type { PlayerResult, UIModel } from '../shared/types';

const model = (patch: Partial<UIModel>): UIModel => ({
  screen: 'playing',
  boot: { progress: 1, message: '', error: null, needsClick: false },
  countdown: { value: 3, calibration: [], message: null },
  paused: null,
  gameOver: null,
  ...patch,
}) as UIModel;

const result = (name: string, score: number, rank: number | null): PlayerResult => ({
  playerId: 0, name, score, coins: 0, distance: 0, time: 0, photo: null, rank, entryId: null,
});

describe('screen-reader announcements', () => {
  it('counts down, then says go', () => {
    expect(announcement(model({ screen: 'countdown', countdown: { value: 2, calibration: [], message: null } }))).toBe('2');
    expect(announcement(model({ screen: 'countdown', countdown: { value: 0, calibration: [], message: null } }))).toBe('Go!');
  });
  it('is silent during play and explains a tracking pause', () => {
    expect(announcement(model({}))).toBeNull();
    const paused = model({ screen: 'paused', paused: { reason: 'tracking-lost', resumeIn: null, missing: [0] } });
    expect(announcement(paused)).toMatch(/lost sight of you/);
  });
  it('reads the result and leaderboard rank', () => {
    const solo = model({ screen: 'gameover', gameOver: { results: [result('Ann', 12345, 2)], restartLockout: null, handsUpProgress: 0 } });
    expect(announcement(solo)).toBe('Game over. 12,345 points, rank 2 on the leaderboard.');
    const duo = model({
      screen: 'gameover',
      gameOver: { results: [result('Ann', 10, null), result('Bo', 20, 1)], restartLockout: null, handsUpProgress: 0 },
    });
    expect(announcement(duo)).toBe('Game over. Ann: 10 points. Bo: 20 points, rank 1 on the leaderboard.');
  });
});
