import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import type { WhepPlayerOptions, WhepPlayerSnapshot } from '@doorboard/media-client';
import { parseParams } from '../shared/params';
import { BRIDGE_CHANNEL, DoorBridge } from './bridge';
import { DOOR } from './config';
import { doorExitReason } from './watchdog';
import { whepCameraSource, type PlayerLike } from './whepSource';

describe('door params', () => {
  it('is off unless ?door=1', () => {
    expect(parseParams('').door).toBeNull();
    expect(parseParams('?door=0').door).toBeNull();
  });

  it('defaults to the loopback door-media, the visitor stream, lite + low', () => {
    const p = parseParams('?door=1');
    expect(p.door).toEqual({ mediaUrl: 'http://127.0.0.1:8082/streams', stream: 'visitor', parentOrigin: null, webcam: false });
    expect(p.model).toBe('lite');
    expect(p.quality).toBe('low');
  });

  it('still honours an explicit model / quality', () => {
    const p = parseParams('?door=1&model=full&quality=high');
    expect([p.model, p.quality]).toEqual(['full', 'high']);
  });

  it('accepts only a bare http(s) origin as the parent', () => {
    expect(parseParams('?door=1&parent=http://127.0.0.1:5173').door?.parentOrigin).toBe('http://127.0.0.1:5173');
    expect(parseParams('?door=1&parent=http://127.0.0.1:5173/wallboard').door?.parentOrigin).toBeNull();
    expect(parseParams('?door=1&parent=javascript:alert(1)').door?.parentOrigin).toBeNull();
    expect(parseParams('?door=1&parent=*').door?.parentOrigin).toBeNull();
  });

  it('rejects a non-http media URL and a malformed stream name', () => {
    const d = parseParams('?door=1&media=file:///etc/passwd&stream=../x').door;
    expect(d?.mediaUrl).toBe('http://127.0.0.1:8082/streams');
    expect(d?.stream).toBe('visitor');
  });
});

describe('door watchdog', () => {
  const at = (over: Partial<Parameters<typeof doorExitReason>[0]>) =>
    doorExitReason({ screen: 'lobby', screenFor: 0, nobodyFor: 0, errorFor: null, ...over });

  it('keeps a live game', () => {
    expect(at({ screen: 'playing', screenFor: 600, nobodyFor: 1 })).toBeNull();
    expect(at({ screen: 'boot', screenFor: 10 })).toBeNull();
  });

  it('gives up on an empty or unused lobby', () => {
    expect(at({ screenFor: DOOR.lobbyMaxS })).toBe('idle');
    expect(at({ screenFor: 5, nobodyFor: DOOR.nobodyS })).toBe('idle');
  });

  it('ends a run everyone walked away from, on any screen', () => {
    for (const screen of ['countdown', 'playing', 'paused', 'gameover'] as const) {
      expect(at({ screen, nobodyFor: DOOR.nobodyS })).toBe('walked_away');
    }
  });

  it('lets an error be read before leaving', () => {
    expect(at({ screen: 'boot', errorFor: DOOR.errorDwellS - 1 })).toBeNull();
    expect(at({ screen: 'boot', errorFor: DOOR.errorDwellS })).toBe('error');
    expect(at({ screen: 'paused', errorFor: DOOR.errorDwellS, nobodyFor: 0 })).toBe('error');
  });

  it('treats a boot that never finishes as an error', () => {
    expect(at({ screen: 'boot', screenFor: DOOR.bootMaxS })).toBe('error');
  });
});

class FakeWindow extends EventTarget {
  parent: { postMessage: ReturnType<typeof vi.fn> } | FakeWindow = { postMessage: vi.fn() };
}

describe('door bridge', () => {
  const ORIGIN = 'http://127.0.0.1:5173';

  it('posts to the parent at its exact origin', () => {
    const win = new FakeWindow();
    new DoorBridge(win as unknown as Window, ORIGIN).post({ type: 'exit', reason: 'idle' });
    expect((win.parent as { postMessage: ReturnType<typeof vi.fn> }).postMessage).toHaveBeenCalledWith(
      { channel: BRIDGE_CHANNEL, type: 'exit', reason: 'idle' },
      ORIGIN,
    );
  });

  it('is a no-op without a parent origin or when not framed', () => {
    const win = new FakeWindow();
    new DoorBridge(win as unknown as Window, null).post({ type: 'ready' });
    expect((win.parent as { postMessage: ReturnType<typeof vi.fn> }).postMessage).not.toHaveBeenCalled();
    const top = new FakeWindow();
    top.parent = top;
    expect(() => new DoorBridge(top as unknown as Window, ORIGIN).post({ type: 'ready' })).not.toThrow();
  });
});

describe('door WHEP camera source', () => {
  let onChange: ((s: WhepPlayerSnapshot) => void) | undefined;
  let player: { start: Mock<() => void>; stop: Mock<() => void> };
  const createPlayer = (o: WhepPlayerOptions): PlayerLike => {
    onChange = o.onChange;
    return player;
  };
  const snap = (over: Partial<WhepPlayerSnapshot>): WhepPlayerSnapshot => ({
    status: 'connecting', stream: null, streamName: 'visitor', lastError: null, connectedAtMonotonicMs: null, ...over,
  });
  const stream = (): MediaStream => ({}) as MediaStream;

  beforeEach(() => {
    vi.useFakeTimers();
    player = { start: vi.fn<() => void>(), stop: vi.fn<() => void>() };
  });
  afterEach(() => vi.useRealTimers());

  const source = () => whepCameraSource({ metadataUrl: 'http://127.0.0.1:8082/streams', streamName: 'visitor', createPlayer });

  it('resolves the first connected stream and stops the player on release', async () => {
    const opening = source().open(vi.fn());
    expect(player.start).toHaveBeenCalled();
    const s = stream();
    onChange?.(snap({ status: 'connected', stream: s }));
    const opened = await opening;
    expect(opened.stream).toBe(s);
    opened.release();
    opened.release();
    expect(player.stop).toHaveBeenCalledTimes(1);
  });

  it('times out with the player\'s last error', async () => {
    const opening = source().open(vi.fn());
    onChange?.(snap({ status: 'unavailable', lastError: 'stream visitor is unavailable' }));
    vi.advanceTimersByTime(DOOR.cameraTimeoutS * 1000);
    await expect(opening).rejects.toThrow('stream visitor is unavailable');
    expect(player.stop).toHaveBeenCalledTimes(1);
  });

  it('reports the stream lost when the player drops or swaps it', async () => {
    const lost = vi.fn();
    const opening = source().open(lost);
    onChange?.(snap({ status: 'connected', stream: stream() }));
    await opening;
    onChange?.(snap({ status: 'unavailable', stream: null }));
    expect(lost).toHaveBeenCalledTimes(1);
    expect(player.stop).toHaveBeenCalledTimes(1);
    onChange?.(snap({ status: 'connected', stream: stream() }));
    expect(lost).toHaveBeenCalledTimes(1);
  });
});
