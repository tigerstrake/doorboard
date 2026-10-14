import { describe, expect, it } from 'vitest';
import { Color } from 'three';
import { Particles } from './Particles';

type Internals = {
  lo: number;
  hi: number;
  posAttr: { updateRanges: { start: number; count: number }[] };
};

describe('particles', () => {
  it('updates and uploads only the live span, then goes idle', () => {
    const p = new Particles(900);
    const view = p as unknown as Internals;
    p.burst(10, 0, 0, 0, 0.1, 1, 1, new Color('#fff'), 0.3, 0.5, 2, 0, () => 0.5);
    expect([view.lo, view.hi]).toEqual([0, 9]);
    p.update(1 / 60, 10);
    expect(view.posAttr.updateRanges).toEqual([{ start: 0, count: 30 }]);
    for (let i = 0; i < 120; i++) p.update(1 / 60, 10);
    expect(view.hi).toBeLessThan(view.lo); // all dead → nothing to update
    p.dispose();
  });

  it('a wrapped ring buffer covers both ends', () => {
    const p = new Particles(20);
    const view = p as unknown as Internals;
    const c = new Color('#fff');
    p.burst(15, 0, 0, 0, 0.1, 1, 1, c, 0.3, 5, 2, 0, () => 0.5);
    p.burst(10, 0, 0, 0, 0.1, 1, 1, c, 0.3, 5, 2, 0, () => 0.5); // indices 15..19, 0..4
    expect([view.lo, view.hi]).toEqual([0, 19]);
    p.dispose();
  });
});
