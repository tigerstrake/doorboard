import { describe, expect, it } from 'vitest';
import { parseBoard, projectedRank, relativeDate, sanitizeMessage } from './data';

describe('hall data', () => {
  it('tolerates junk', () => {
    expect(parseBoard(null)).toEqual([]);
    expect(parseBoard('not json')).toEqual([]);
    expect(parseBoard('{"a":1}')).toEqual([]);
    const b = parseBoard(JSON.stringify([null, 5, { score: 'x' }, { id: 'a', score: 10, photo: 'javascript:x' }, { id: 'b', score: 20, name: '' }, { id: 'a', score: 99 }]));
    expect(b.map((e) => e.id)).toEqual(['b', 'a']);
    expect(b[1]!.photo).toBeNull();
    expect(b[0]!.name).toBe('MYSTERY SURFER');
  });
  it('caps at 10 sorted', () => {
    const raw = Array.from({ length: 15 }, (_, i) => ({ id: `e${i}`, score: i }));
    const b = parseBoard(JSON.stringify(raw));
    expect(b).toHaveLength(10);
    expect(b[0]!.score).toBe(14);
  });
  it('messages', () => {
    expect(sanitizeMessage({ type: 'nope' })).toBeNull();
    expect(sanitizeMessage('idle')).toBeNull();
    const m = sanitizeMessage({ type: 'state', screen: 'playing', playerCount: 2, players: [{ score: 5 }, { score: 'q', alive: false }] });
    expect(m).toEqual({ type: 'state', screen: 'playing', playerCount: 2, players: [{ score: 5, coins: 0, distance: 0, alive: true }, { score: 0, coins: 0, distance: 0, alive: false }] });
  });
  it('ranks and dates', () => {
    const b = parseBoard(JSON.stringify([{ id: 'a', score: 100 }, { id: 'b', score: 50 }]));
    expect(projectedRank(200, b)).toBe(1);
    expect(projectedRank(60, b)).toBe(2);
    expect(relativeDate(1000, 1000 + 5 * 60000)).toBe('5 MIN AGO');
  });
});
