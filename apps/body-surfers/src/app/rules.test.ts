import { describe, expect, it } from 'vitest';
import {
  Countdown, JoinFlow, ResumeGate, calibrationOf, cameraErrorMessage, decideWinner, gameoverShouldExit,
  invisiblePlayers, missingPlayers, presentPlayers, restartLockoutLeft, type Presence,
} from './rules';
import type { GestureState, PlayerId } from '../shared/types';

const person = (slot: PlayerId, handsUpProgress: number) => ({ slot, handsUpProgress });

describe('JoinFlow', () => {
  it('solo person joining starts immediately', () => {
    const j = new JoinFlow(4, 1.5);
    expect(j.update([person(0, 0.5)], 0, 2).start).toBeNull();
    const s = j.update([person(0, 1)], 0.1, 2);
    expect(s.joined).toEqual([0]);
    expect(s.start).toEqual([0]);
  });

  it('opens a join window when someone else is visible; second join starts 2P', () => {
    const j = new JoinFlow(4, 1.5);
    const s1 = j.update([person(0, 1), person(1, 0)], 0, 2);
    expect(s1.start).toBeNull();
    expect(j.secondsLeft(1)).toBeCloseTo(3);
    const s2 = j.update([person(0, 1), person(1, 1)], 2, 2);
    expect(s2.joined).toEqual([1]);
    expect(s2.start).toEqual([0, 1]);
  });

  it('window expiry starts with the joined slots only', () => {
    const j = new JoinFlow(4, 1.5);
    j.update([person(0, 0), person(1, 1)], 0, 2);
    expect(j.update([person(0, 0), person(1, 1)], 3.9, 2).start).toBeNull();
    expect(j.update([person(0, 0), person(1, 1)], 4.0, 2).start).toEqual([1]);
  });

  it('maxPlayers 1 starts at the first join', () => {
    const j = new JoinFlow(4, 1.5);
    expect(j.update([person(0, 1), person(1, 0)], 0, 1).start).toEqual([0]);
  });

  it('resets when the joined person leaves', () => {
    const j = new JoinFlow(4, 1.5);
    j.update([person(0, 1), person(1, 0)], 0, 2);
    j.update([person(1, 0)], 0.5, 2);
    const s = j.update([person(1, 0)], 2.1, 2);
    expect(s.reset).toBe(true);
    expect(j.joined).toEqual([]);
  });
});

describe('Countdown', () => {
  it('ticks 3-2-1 then GO when ready', () => {
    const c = new Countdown(3);
    const ticks: number[] = [];
    let go = false;
    for (let t = 0; t < 3.2 && !go; t += 0.1) {
      const s = c.update(0.1, true);
      if (s.tick) ticks.push(s.value);
      go = s.go;
    }
    expect(ticks).toEqual([3, 2, 1]);
    expect(go).toBe(true);
  });

  it('never starts an uncalibrated run, even after a long wait', () => {
    const c = new Countdown(3);
    for (let i = 0; i < 31; i++) c.update(0.1, false);
    expect(c.update(0.1, false).holding).toBe(true);
    for (let i = 0; i < 600; i++) {
      const step = c.update(0.1, false);
      expect(step.go).toBe(false);
      expect(step.holding).toBe(true);
    }
    expect(c.update(0.1, true).go).toBe(true);
  });

  it('goes once calibration completes during the hold', () => {
    const c = new Countdown(3);
    for (let i = 0; i < 35; i++) c.update(0.1, false);
    expect(c.update(0.1, true).go).toBe(true);
    expect(c.update(0.1, true).go).toBe(false);
  });
});

describe('calibrationOf', () => {
  const g = (phase: GestureState['phase'], p: number) => ({ phase, calibrationProgress: p }) as GestureState;
  it('maps phases', () => {
    expect(calibrationOf(null)).toBe(0);
    expect(calibrationOf(g('calibrating', 0.4))).toBe(0.4);
    expect(calibrationOf(g('ready', 0))).toBe(1);
  });
});

describe('ResumeGate', () => {
  it('tracking-lost: needs 1 s visible, then 3 s countdown', () => {
    const r = new ResumeGate(3, 1);
    let resumed = false;
    let t = 0;
    for (; t < 10 && !resumed; t += 0.1) resumed = r.update(0.1, true).resume;
    expect(resumed).toBe(true);
    expect(t).toBeGreaterThan(3.9);
    expect(t).toBeLessThan(4.3);
  });
  it('disappearing cancels', () => {
    const r = new ResumeGate(3, 1);
    for (let i = 0; i < 15; i++) r.update(0.1, true);
    expect(r.resumeIn).not.toBeNull();
    r.update(0.1, false);
    expect(r.resumeIn).toBeNull();
  });
  it('user mode resumes only after trigger', () => {
    const r = new ResumeGate(3, 1);
    for (let i = 0; i < 50; i++) expect(r.update(0.1, null).resume).toBe(false);
    r.trigger();
    let resumed = false;
    for (let i = 0; i < 31 && !resumed; i++) resumed = r.update(0.1, null).resume;
    expect(resumed).toBe(true);
  });
});

describe('decideWinner', () => {
  it('first crash in 2P makes the other the winner', () => {
    expect(decideWinner(2, null, [0], [false, true], [10, 5])).toBe(1);
    expect(decideWinner(2, null, [1], [true, false], [10, 5])).toBe(0);
  });
  it('never changes once decided; 1P has none', () => {
    expect(decideWinner(2, 1, [1], [false, false], [10, 5])).toBe(1);
    expect(decideWinner(1, null, [0], [false], [10])).toBeNull();
  });
  it('simultaneous crash: higher score, tie → none', () => {
    expect(decideWinner(2, null, [0, 1], [false, false], [10, 50])).toBe(1);
    expect(decideWinner(2, null, [0, 1], [false, false], [10, 10])).toBeNull();
  });
});

describe('presence', () => {
  const p = (o: Partial<Presence>): Presence => ({ alive: true, seen: true, visible: true, lastSeen: 0, ...o });
  it('missingPlayers respects threshold, alive and seen', () => {
    const ps = [p({ lastSeen: 0 }), p({ lastSeen: 9.5 })];
    expect(missingPlayers(ps, 2, 10, 0.7)).toEqual([0]);
    expect(missingPlayers([p({ alive: false }), p({ seen: false })], 2, 10, 0.7)).toEqual([]);
  });
  it('invisiblePlayers', () => {
    expect(invisiblePlayers([p({ visible: false }), p({})], 2)).toEqual([0]);
  });
  it('presentPlayers falls back to everyone', () => {
    expect(presentPlayers([p({ lastSeen: 0 }), p({ lastSeen: 9.9 })], 2, 10, 1.5)).toEqual([1]);
    expect(presentPlayers([p({ lastSeen: 0 }), p({ lastSeen: 0 })], 2, 10, 1.5)).toEqual([0, 1]);
  });
});

describe('gameover', () => {
  it('restart lockout', () => {
    expect(restartLockoutLeft(1, 2.5)).toBeCloseTo(1.5);
    expect(restartLockoutLeft(2.6, 2.5)).toBeNull();
  });
  it('exits on idle or absence', () => {
    expect(gameoverShouldExit({ since: 26, idleFor: 26, absentFor: null })).toBe(true);
    expect(gameoverShouldExit({ since: 5, idleFor: 1, absentFor: 3.5 })).toBe(true);
    expect(gameoverShouldExit({ since: 2, idleFor: 1, absentFor: 3.5 })).toBe(false);
    expect(gameoverShouldExit({ since: 5, idleFor: 1, absentFor: null })).toBe(false);
  });
});

describe('cameraErrorMessage', () => {
  it('classifies denial', () => {
    expect(cameraErrorMessage({ name: 'NotAllowedError', message: 'x' })).toMatch(/blocked/);
    expect(cameraErrorMessage({ name: 'NotFoundError', message: '' })).toMatch(/No camera/);
  });
});
