import { describe, expect, it } from 'vitest';
import { FixedStepper, FrameP90 } from './loop';
import { BoothBroadcaster, boothDue, stateMessage, type ChannelLike } from './booth';
import type { BoothMessage, WorldSnapshot } from '../shared/types';
import { emptySnapshot } from './guards';

describe('FixedStepper', () => {
  it('60 Hz frames → 2 steps at 120 Hz', () => {
    const s = new FixedStepper(1 / 120, 8);
    let n = 0;
    for (let i = 0; i < 60; i++) n += s.advance(1 / 60);
    expect(n).toBe(120);
  });
  it('caps sub-steps', () => {
    const s = new FixedStepper(1 / 120, 8);
    expect(s.advance(1)).toBe(8);
    expect(s.pending).toBeLessThanOrEqual(1 / 120);
  });
});

describe('booth', () => {
  const sink = () => {
    const msgs: BoothMessage[] = [];
    const ch: ChannelLike = { postMessage: (m) => msgs.push(m as BoothMessage), close: () => {} };
    return { msgs, b: new BoothBroadcaster(ch) };
  };
  it('rate limits state to ~4/s and idle to every 2 s', () => {
    const { msgs, b } = sink();
    const snaps: WorldSnapshot[] = [emptySnapshot(0)];
    for (let t = 0; t < 1; t += 1 / 60) b.state(t, 'playing', 1, snaps);
    expect(msgs.filter((m) => m.type === 'state').length).toBe(4);
    msgs.length = 0;
    b.idle(10, true);
    for (let t = 10; t < 14.01; t += 1 / 60) b.idle(t);
    expect(msgs.length).toBe(3);
  });
  it('state message shape', () => {
    const s = { ...emptySnapshot(0), score: 12.7, distance: 3.3, coins: 2, status: 'dead' as const };
    expect(stateMessage('playing', 1, [s])).toEqual({
      type: 'state', screen: 'playing', playerCount: 1, players: [{ score: 12, coins: 2, distance: 3, alive: false }],
    });
    expect(boothDue(1, 0, 0.25)).toBe(true);
  });
});

describe('FrameP90', () => {
  it('reports the 90th percentile once full', () => {
    const p = new FrameP90(10);
    for (let i = 1; i <= 9; i++) p.push(i);
    expect(p.full).toBe(false);
    p.push(10);
    expect(p.full).toBe(true);
    expect(p.value()).toBe(10);
    for (let i = 0; i < 10; i++) p.push(16);
    expect(p.value()).toBe(16);
    p.reset();
    expect(p.value()).toBe(0);
  });
  it('a few slow frames among fast ones stay under the p90', () => {
    const p = new FrameP90(60);
    for (let i = 0; i < 60; i++) p.push(i % 20 === 0 ? 50 : 16.7);
    expect(p.value()).toBeCloseTo(16.7);
  });
});
